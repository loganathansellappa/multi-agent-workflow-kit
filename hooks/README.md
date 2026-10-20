# Hooks

Copilot CLI **hooks** are external commands the CLI runs automatically at
lifecycle points (before/after a tool runs, at session start/end, etc.). Unlike
skills — which an agent *chooses* to invoke — a hook is a runtime interceptor
the CLI runs on its own, so it applies policy without the agent's cooperation
(defense-in-depth, deliberately fail-open — see failure behavior below).

> **Global hooks are session/user/repo-level; agents can add their own.** Hooks in
> `hooks.example.json` intercept *every* tool call in the session regardless of which
> agent is running. An agent `.md` can also declare hooks in its frontmatter
> (`hooks:` with `userPromptSubmitted` / `preToolUse` entries using `command:`).
> Those fire only while that agent runs, both top level (`--agent X`) and as a
> subagent (verified on CLI 1.0.98). A subagent runs under its **own** sessionId;
> global hooks see its tool calls and its prompt (`userPromptSubmitted`) under that
> child id. To scope by tool, use the `matcher` on `preToolUse`/`postToolUse`.

## What ships here

| File | Purpose |
| ---- | ------- |
| `push-guard-hook.py` | `preToolUse` handler that **blocks `git push` to a protected branch** (main/master, plus any configured `baseBranch`) at the tool layer. This is the *enforcement* half of the `git-push-guard` skill. |
| `shell-guard-hook.py` | `preToolUse` + `subagentStart`/`subagentStop` handler that enforces the **shell trust boundary** (fail-open defense-in-depth): (A) denies *any* agent's shell command that references a secret path (`mcp-config.json`, `.secrets/`, `*.token`/`*.pem`/`*.key`); (B) while a **read-only agent** (reviewer / read-only orchestrator) is active, additionally denies file-mutating shell (`Set-Content`/`Out-File`/`rm`/`mv`/`sed -i`/`>` redirect/`git commit`\|`apply`\|`reset`…). |
| `slop-guard-hook.py` | `preToolUse` handler that **enforces the PR-comment humanize + grounding gates** at the tool layer. This is the *enforcement* half of the `pr-comment-respond` skill: it denies a PR-comment POST made with a raw HTTP client (`curl`/`gh api`/`glab api`/`Invoke-RestMethod`/`urllib`) that bypasses the gated `pr_comments.py`, denies `--no-lint` / `--no-ground`, and denies `pr_comments.py reply`/`comment` without `--approval`. Covers GitHub, GitLab, and Bitbucket endpoints. Read-only GETs that list comments stay allowed. |
| `pr-post-gate-hook.py` | Global `preToolUse` (all tools) + `postToolUse` (`ask_user`, shell) + `userPromptSubmitted`, plus agent-scoped modes declared in `review-orchestrator`, `code-reviewer` and `security-reviewer`. **Deterministic PR comment and review-work gate**: posting from a review is OFF unless the human typed `--post-comments=true`; every post needs a one-use, HMAC-signed grant bound to session + PR + target + text hash that the hook mints only after the user selected Post (or `true` in a legacy boolean form) showing the exact staged text. Edit text requires restaging and fresh approval. Text must be humanized, have no hyphen/dash or semicolon outside `code`, and pass slophound (missing slophound = deny). Denies `gh`/`glab` comment commands, MCP comment tools, raw HTTP comment writes, `pr_comments` internals and approval-state access. Keeps review sessions read-only and caps full reviews at 64 ordinary tool calls, with same pinned PR range follow ups capped at 8 calls plus 2 contract scans. Tests: `tests/test_pr_post_gate.py`. |
| `hooks.example.json` | The hook registration the CLI reads. Copy it to `~/.copilot/hooks/`. |

## Why the shell-guard hook exists

The reviewer "read-only" guarantee is only half enforced by the runtime: the CLI
strips reviewers of `edit`/`create`, but **every** agent still holds the full,
unrestricted `powershell`/`bash` tool. The `shell(git:*)` line in the agent files
is prose with no runtime effect. So a prompt-injected reviewer analysing a hostile
diff can, in one shell line, read the configured secrets and exfiltrate
them, or mutate the workspace despite lacking `edit`. `shell-guard-hook.py` closes
both paths at the tool layer:

* **Layer A (all agents)** blocks the first step of the mapped exfiltration path —
  no shell command may reference a secret path. Secrets are provided via env vars.
* **Layer B (read-only agents)** enforces "reviewers/orchestrators don't mutate" —
  their read-only gates (`git diff`/`log`/`show`, `npm run lint`, `eslint`)
  stay allowed; writes and commits are denied. It is intentionally **not**
  "git-only": reviewers legitimately run non-git lint tooling.

`preToolUse` carries no agent identity, so `subagentStart` writes a marker for the
active read-only agent's session and `subagentStop` clears it; `preToolUse` reads
the marker to decide whether Layer B applies. Deny-only + fail-open means a
mis-correlation is at worst a recoverable false-deny, never a lock-out.

## Why the push-guard hook exists

The `git-push-guard` **skill** (`skills/git-push-guard/`) is *advisory*: it only
protects if the agent remembers to run it before pushing. The **hook** promotes
that same check to *enforcement* — the CLI runs it before every shell tool call,
inspects any `git push`, and returns `permissionDecision: deny` for a protected
branch. This is strong defense-in-depth, not an absolute barrier: the hook is
deliberately fail-open (a crash, unparseable input, or an uncovered path — e.g. a
push from inside a script — allows the call). Run the skill and the hook together
and back both with **server-side branch protection** for the hard guarantee.

## Why the slop-guard hook exists

The `pr-comment-respond` **skill** posts every reply/comment through
`pr_comments.py`, which sanitizes the text and lints it with slophound before it
reaches the host. That is only enforced if the agent uses the script. An agent can
post a PR comment by calling the host REST API directly — `curl`, `gh api`,
`glab api`, `Invoke-RestMethod`, or inline `urllib` — and never touch the gate, or
pass `--no-lint` to switch it off. `slop-guard-hook.py` promotes the gate to
*enforcement*: it inspects every shell call, denies a comment POST/PUT/PATCH that
bypasses `pr_comments.py` on GitHub, GitLab, or Bitbucket, and denies `--no-lint`
(humanize) and `--no-ground` (the grounding/target validity preflight) on a real
post. Listing comments with a GET stays allowed, so reading feedback is
never blocked. Like the other two, it is deny-only and fail-open: anything it
cannot positively identify as a bypass is allowed. It also denies a sanctioned
`reply`/`comment` without `--approval`; the strict checks live in pr-post-gate.

## Why the pr-post-gate hook exists

New comments require a related PR diff file and positive line. Generic comments
and file-only anchors are blocked by the hook and helper. The helper verifies
the Bitbucket diff line and chooses ADDED or CONTEXT; invalid anchors never fall
back to a general comment. Replies remain in their existing threads.

The helper enforces duplicate checks both before staging and before the write,
against every author's open comments/tasks at the same nonorphaned file/line.
Normalised equal wording, equivalent release reminders, or 80% word-set overlap
with at least four shared words suppress posting. Resolved items do not count.
API failures are errors, never permission to post a duplicate.

The same hook also bounds reviewer work at runtime. It tracks repeated launches of
the same reviewer for the same PR URL and exact base and head commits within one
orchestrator session. The first review gets 64 ordinary tool calls; a repeated
pinned range gets 8 ordinary calls and at most 2 contract scans. A different
commit range starts a full review. The orchestrator and both shipped reviewers
declare scoped hook entries, so the budget is not only an instruction in their
prompts. When a runtime supplies a tool call ID, the hook uses it to avoid
counting the same global and scoped callback twice. If an ID is missing, both
callbacks may consume budget separately, which can stop a review early but
cannot let it exceed the configured maximum.

Instructions like "only post after the user says yes" can be skipped or
self-satisfied. If an approval token is minted at `stage`, before a human sees the
text, an agent can approve itself. The gate moves the decision to the tool layer:

1. `pr_comments.py stage` humanizes and checks the text and prints `APPROVAL_TOKEN=<t>`.
   The token authorises nothing.
2. The agent calls `ask_user` with `PR_POST_APPROVAL <t>`, the exact text, and only a
   string `post_<t>` field with enum `["post", "edit", "skip"]`, enumNames
   `["Post", "Edit text", "Skip"]` and no default. `preToolUse` rejects malformed forms.
3. `postToolUse` reads the real answer. Only `post_<t>=post` mints the grant.
   Edit text discards the stage without granting permission. Collect replacement
   wording in a separate free-text form, then stage and approve it with a new token
   for the same PR/target. Skip, cancellation and unrecognised responses never post.
   Legacy boolean forms remain supported with an exact `true` answer.
4. `reply`/`comment --approval <t>` is checked by the hook and the grant is consumed
   atomically by `pr_comments.py` (replays are refused through a used-token ledger).

Default OFF for reviews: inside `review-orchestrator` (and every reviewer it spawns)
staging, approval and posting are denied unless the human typed
`--post-comments=true` in the prompt. The flag only counts if it appears in the
prompt the human typed (root session) **and** in the reviewer's own prompt, so an
agent that adds the flag to a `task` prompt does not enable posting. Review sessions
also stay read-only even with opt-in: no edits, commits, pushes, merges,
`resolve` or `delete`.

Text rules (checked at stage and again at post): humanized, no hyphen or dash and no
semicolon outside `inline code`, and slophound must pass. A missing slophound denies.

Fail behavior: unlike the other hooks, this one **fails closed** for comment
actions (an internal error on a comment write denies it). A hook timeout fails open
in the CLI, which is still safe because `pr_comments.py` re-verifies the grant and
only the hook holds the signing key (`~/.copilot/hooks/.secrets/pr-post-gate.key`,
auto-created). Non-comment tools are never affected outside review sessions.

## Install (user-level)

Copy the files into your Copilot CLI hooks directory:

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\hooks" | Out-Null
Copy-Item hooks\push-guard-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\shell-guard-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\slop-guard-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\pr-post-gate-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\hooks.example.json "$env:USERPROFILE\.copilot\hooks\kit-hooks.json"
```

```bash
# macOS / Linux
mkdir -p ~/.copilot/hooks
cp hooks/push-guard-hook.py ~/.copilot/hooks/
cp hooks/shell-guard-hook.py ~/.copilot/hooks/
cp hooks/slop-guard-hook.py ~/.copilot/hooks/
cp hooks/pr-post-gate-hook.py ~/.copilot/hooks/
cp hooks/hooks.example.json ~/.copilot/hooks/kit-hooks.json
```

`scripts/install_to_copilot.py --hooks` does the same copy for you. Even without
`--hooks` the installer copies `pr-post-gate-hook.py` (inert until wired), because
`review-orchestrator`'s frontmatter hooks and `pr_comments.py` load it.

**Per-repo base branches — auto-wired by the installer.**
`scripts/install_to_copilot.py --hooks` now sets `PUSH_GUARD_CONFIG` in the
installed `~/.copilot/hooks/kit-hooks.json` to the absolute path of your live
`agents.config.yaml` automatically (when that config exists). The hook then
also protects each service's configured `baseBranch` (e.g. `develop`,
`release/x`), not just `main`/`master`. Config can only ADD protection — a
missing/unreadable config, or a repo not listed in it, still gets the
unconditional `main`/`master` block. To disable, blank the value out manually.

Restart the CLI (or start a new session) so hooks reload.

## Behavior

* Runs before every shell tool call (`matcher: "bash|powershell|shell|execute"`).
* **Allows** everything that is not a confirmed push to a protected branch —
  the JSON body carries the decision; the script always exits 0.
* **Denies** (with an actionable reason) a push whose target branch is
  `main`/`master`/configured `baseBranch`, including `HEAD:main`, `:main`
  (delete), force-push variants, and `git push --all`/`--mirror`.
* **Soft gate-status block:** also denies a push when the repo's recorded
  `workflow-state` gate is `FAIL` (written by
  `skills/workflow-state/workflow_state.py`). If no record exists (ad-hoc push) or
  the gate is `PASS`, the push is allowed — so this never blocks pushes in repos
  that don't use workflow-state.
* If it matches a push but cannot resolve the target branch, it denies that
  push specifically and asks for an explicit refspec.

## Fail behavior (read this before relying on it)

Command `preToolUse` hooks are **fail-closed** in the CLI: a crash or non-zero
exit denies the pending tool. Because this hook matches *all* shell calls, a
broken hook could block every shell command. The design mitigates that:

1. `push-guard-hook.py` wraps everything in `try/except` and **always exits 0** —
   allow/deny is expressed only through the JSON body.
2. It only ever denies a *confirmed* protected push; anything unparseable is
   allowed.
3. The installed command appends `|| exit 0` / `; exit 0` so even "python not
   found" fails **open** rather than bricking your session.

Net effect: the guard blocks confirmed protected pushes, and any failure of the
hook itself degrades to "allow" (with the skill + server-side protection as
backstops) instead of breaking the CLI.

## Test it

```powershell
'{"toolName":"powershell","toolArguments":{"command":"git push origin main"},"cwd":"."}' `
  | python hooks\push-guard-hook.py
# -> {"permissionDecision":"deny",...}

'{"toolName":"powershell","toolArguments":{"command":"git push origin my-task-branch"},"cwd":"."}' `
  | python hooks\push-guard-hook.py
# -> {}
```

Shell-guard:

```powershell
# Secret access is denied for any agent:
'{"sessionId":"s","toolName":"powershell","toolArgs":{"command":"cat .secrets/api-credentials"}}' `
  | python hooks\shell-guard-hook.py
# -> {"permissionDecision":"deny",...}

# Mutation is denied only while a read-only agent is active (marker set by subagentStart):
'{"sessionId":"r","agentName":"code-reviewer"}' | python hooks\shell-guard-hook.py   # marks session r
'{"sessionId":"r","toolName":"powershell","toolArgs":{"command":"git commit -m x"}}' `
  | python hooks\shell-guard-hook.py
# -> {"permissionDecision":"deny",...}
'{"sessionId":"r","toolName":"powershell","toolArgs":{"command":"npm run lint"}}' `
  | python hooks\shell-guard-hook.py
# -> {}   (reviewer lint gate still allowed)
```

The full offline test suite lives in `tests/test_shell_guard.py`.

Slop-guard:

```powershell
# A raw comment POST that bypasses the gated script is denied (any host):
'{"toolName":"powershell","toolArgs":{"command":"gh api repos/o/r/pulls/7/comments -f body=hi"}}' `
  | python hooks\slop-guard-hook.py
# -> {"permissionDecision":"deny",...}

# --no-lint on a real post is denied:
'{"toolName":"powershell","toolArgs":{"command":"python pr_comments.py reply --slug s --pr 1 --comment 2 --text x --no-lint"}}' `
  | python hooks\slop-guard-hook.py
# -> {"permissionDecision":"deny",...}

# --no-ground on a real post is denied too:
'{"toolName":"powershell","toolArgs":{"command":"python pr_comments.py reply --slug s --pr 1 --comment 2 --text x --no-ground"}}' `
  | python hooks\slop-guard-hook.py
# -> {"permissionDecision":"deny",...}

# Listing comments (GET) and the sanctioned script are allowed:
'{"toolName":"powershell","toolArgs":{"command":"gh api repos/o/r/pulls/7/comments"}}' `
  | python hooks\slop-guard-hook.py
# -> {}
```

The full offline test suite lives in `tests/test_slop_guard.py`.

PR-post-gate (each call is a fresh shell call in session `s`):

```powershell
# gh pr comment never posts, opted in or not:
'{"sessionId":"s","toolName":"powershell","toolArgs":{"command":"gh pr comment 7 --body hi"}}' `
  | python hooks\pr-post-gate-hook.py pre
# -> {"permissionDecision":"deny",...}

# A post with a token the user never approved is denied:
'{"sessionId":"s","toolName":"powershell","toolArgs":{"command":"python pr_comments.py reply --slug s --pr 1 --comment 2 --approval 0123456789abcdef01234567"}}' `
  | python hooks\pr-post-gate-hook.py pre
# -> {"permissionDecision":"deny",...}
```

The full offline suite (47 negative-first tests: forged/expired/replayed grants,
wrong target, changed text, agent-injected opt-in, bypasses, review read-only)
lives in `tests/test_pr_post_gate.py`.

> **Verifying subagent→session correlation live.** The correlation of
> `subagentStart(sessionId)` → `preToolUse(sessionId)` cannot be observed offline
> (hooks reload only on CLI start). After deploying, restart the CLI, run a
> reviewer agent, and confirm a marker appears under
> `~/.copilot/hooks/.readonly-sessions/` while it runs and is removed on stop. Even
> if subagents share the parent `sessionId`, the marker covers the reviewer's
> active window and is deny-only + fail-open, so a mismatch cannot brick a session.
>
> **Known gap (observed on CLI 1.0.98):** subagents run under their own sessionId,
> while `subagentStart` reports the parent's. The shell-guard marker therefore lands
> on the parent, not the reviewer. `pr-post-gate-hook.py` enforces review read-only
> mode itself via prompt lineage, so reviewers spawned by `review-orchestrator`
> are covered regardless.

## Uninstall

```powershell
Remove-Item "$env:USERPROFILE\.copilot\hooks\kit-hooks.json", "$env:USERPROFILE\.copilot\hooks\push-guard-hook.py", "$env:USERPROFILE\.copilot\hooks\shell-guard-hook.py", "$env:USERPROFILE\.copilot\hooks\slop-guard-hook.py", "$env:USERPROFILE\.copilot\hooks\pr-post-gate-hook.py"
```

## Other hooks worth considering (not shipped)

We deliberately ship only the push-guard, shell-guard, slop-guard and pr-post-gate hooks. Keep
hooks minimal, deterministic, and stdlib-only — every hook is a moving part on the
critical path of every tool call. Candidates you *could* add for your own setup:

* `sessionStart` prompt hook to auto-run preflight (kept as a skill here so it
  stays opt-in rather than firing on every session).
* `postToolUse`/`sessionEnd` metrics capture (covered by the
  `delivery-metrics-capture` skill).

Avoid over-hooking: prefer a skill unless you specifically need tool-boundary
enforcement that applies without the agent's cooperation.
