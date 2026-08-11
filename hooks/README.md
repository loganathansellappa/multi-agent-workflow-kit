# Hooks

Copilot CLI **hooks** are external commands the CLI runs automatically at
lifecycle points (before/after a tool runs, at session start/end, etc.). Unlike
skills — which an agent *chooses* to invoke — a hook is a runtime interceptor
the CLI runs on its own, so it enforces policy the model cannot skip.

> **Hooks are session/user/repo-level, not per-agent.** You cannot put a hook
> "inside" an agent `.md`. Agents declare *skills and tools*; hooks intercept
> *every* tool call in the session regardless of which agent is running. To scope
> behavior by agent, use the `subagentStart`/`subagentStop` events with a
> `matcher`; to scope by tool, use the `matcher` on `preToolUse`/`postToolUse`.

## What ships here

| File | Purpose |
| ---- | ------- |
| `push-guard-hook.py` | `preToolUse` handler that **blocks `git push` to a protected branch** (main/master, plus any configured `baseBranch`) at the tool layer. This is the *enforcement* half of the `git-push-guard` skill. |
| `shell-guard-hook.py` | `preToolUse` + `subagentStart`/`subagentStop` handler that enforces the **shell trust boundary** the model can't skip: (A) denies *any* agent's shell command that references a secret path (`mcp-config.json`, `.secrets/`, `*.token`/`*.pem`/`*.key`); (B) while a **read-only agent** (reviewer / read-only orchestrator) is active, additionally denies file-mutating shell (`Set-Content`/`Out-File`/`rm`/`mv`/`sed -i`/`>` redirect/`git commit`\|`apply`\|`reset`…). |
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
branch. The model cannot bypass it. Run the skill and the hook together
(defense-in-depth) and back both with **server-side branch protection** for a
hard guarantee.

## Install (user-level)

Copy both files into your Copilot CLI hooks directory:

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\hooks" | Out-Null
Copy-Item hooks\push-guard-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\shell-guard-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\hooks.example.json "$env:USERPROFILE\.copilot\hooks\kit-hooks.json"
```

```bash
# macOS / Linux
mkdir -p ~/.copilot/hooks
cp hooks/push-guard-hook.py ~/.copilot/hooks/
cp hooks/shell-guard-hook.py ~/.copilot/hooks/
cp hooks/hooks.example.json ~/.copilot/hooks/kit-hooks.json
```

`scripts/install_to_copilot.py --hooks` does the same copy for you.

**Optional — extend the protected set with your configured base branches.**
Set `PUSH_GUARD_CONFIG` in the `env` block of the installed
`~/.copilot/hooks/kit-hooks.json` to the absolute path of your
`service-path.config.yaml`. The hook then also protects each service's
`baseBranch`, not just `main`/`master`. Leave it empty to protect only
`main`/`master`.

Restart the CLI (or start a new session) so hooks reload.

## Behavior

* Runs before every shell tool call (`matcher: "bash|powershell|shell|execute"`).
* **Allows** everything that is not a confirmed push to a protected branch —
  the JSON body carries the decision; the script always exits 0.
* **Denies** (with an actionable reason) a push whose target branch is
  `main`/`master`/configured `baseBranch`, including `HEAD:main`, `:main`
  (delete), force-push variants, and `git push --all`/`--mirror`.
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

> **Verifying subagent→session correlation live.** The correlation of
> `subagentStart(sessionId)` → `preToolUse(sessionId)` cannot be observed offline
> (hooks reload only on CLI start). After deploying, restart the CLI, run a
> reviewer agent, and confirm a marker appears under
> `~/.copilot/hooks/.readonly-sessions/` while it runs and is removed on stop. Even
> if subagents share the parent `sessionId`, the marker covers the reviewer's
> active window and is deny-only + fail-open, so a mismatch cannot brick a session.

## Uninstall

```powershell
Remove-Item "$env:USERPROFILE\.copilot\hooks\kit-hooks.json", "$env:USERPROFILE\.copilot\hooks\push-guard-hook.py", "$env:USERPROFILE\.copilot\hooks\shell-guard-hook.py"
```

## Other hooks worth considering (not shipped)

We deliberately ship only the push-guard and shell-guard hooks. Keep hooks
minimal, deterministic, and stdlib-only — every hook is a moving part on the
critical path of every tool call. Candidates you *could* add for your own setup:

* `sessionStart` prompt hook to auto-run preflight (kept as a skill here so it
  stays opt-in rather than firing on every session).
* `postToolUse`/`sessionEnd` metrics capture (covered by the
  `delivery-metrics-capture` skill).

Avoid over-hooking: prefer a skill unless you specifically need runtime
enforcement the model cannot skip.
