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
| `hooks.example.json` | The hook registration the CLI reads. Copy it to `~/.copilot/hooks/`. |

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

```bash
# macOS / Linux
mkdir -p ~/.copilot/hooks
cp hooks/push-guard-hook.py ~/.copilot/hooks/
cp hooks/hooks.example.json ~/.copilot/hooks/push-guard.json
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\hooks" | Out-Null
Copy-Item hooks\push-guard-hook.py "$env:USERPROFILE\.copilot\hooks\"
Copy-Item hooks\hooks.example.json "$env:USERPROFILE\.copilot\hooks\push-guard.json"
```

`scripts/install_to_copilot.py --hooks` does the same copy for you.

**Optional — extend the protected set with your configured base branches.**
Set `PUSH_GUARD_CONFIG` in the `env` block of the installed
`~/.copilot/hooks/push-guard.json` to the absolute path of your
`agents.config.yaml`. The hook then also protects each service's `baseBranch`,
not just `main`/`master`. Leave it empty to protect only `main`/`master`.

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

```bash
echo '{"toolName":"powershell","toolArguments":{"command":"git push origin main"},"cwd":"."}' \
  | python3 hooks/push-guard-hook.py
# -> {"permissionDecision":"deny",...}

echo '{"toolName":"powershell","toolArguments":{"command":"git push origin my-task-branch"},"cwd":"."}' \
  | python3 hooks/push-guard-hook.py
# -> {}
```

## Uninstall

```bash
rm ~/.copilot/hooks/push-guard.json ~/.copilot/hooks/push-guard-hook.py
```

## Other hooks worth considering (not shipped)

We deliberately ship only the push-guard hook. Keep hooks minimal, deterministic,
and stdlib-only — every hook is a moving part on the critical path of every tool
call. Candidates you *could* add for your own setup:

* `sessionStart` prompt hook to auto-run preflight (kept as a skill here so it
  stays opt-in rather than firing on every session).
* `postToolUse`/`sessionEnd` metrics capture (covered by the
  `delivery-metrics-capture` skill).

Avoid over-hooking: prefer a skill unless you specifically need runtime
enforcement the model cannot skip.
