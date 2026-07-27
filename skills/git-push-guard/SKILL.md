---
name: git-push-guard
description: "Deterministic, blocking check that refuses `git push` to a repo's protected default branch (main/master or configured baseBranch); allows push to any other (agent-created task) branch."
---

# git-push-guard

Use this skill immediately before running `git push` in any repo, in every developer agent
(`backend-developer`, `frontend-developer`, `api-developer`, and any specialized developer agents you add),
and in `feature-orchestrator` if it ever pushes directly.

## Why this exists

Instruction text alone ("never push to main/master") is not a hard guarantee — it can drift under
prompt pressure. This skill replaces that soft rule with a **deterministic script check** that must
pass before any push executes. It is a **blocking** gate (unlike the advisory `agent-preflight-check`).

> **Defense-in-depth, not a full guarantee.** This gate only fires when the agent runs it, and it checks
> a single branch name — `git push --all`/`--mirror`, tag pushes, and multi-refspec pushes can bypass it.
> Pair it with **server-side branch protection** on your remote for a hard guarantee; this skill is the
> agent-side complement, not a replacement.

## How to use (mandatory before every `git push`)

Run the bundled script, passing the repo you are about to push and (optionally) the branch you intend
to push to:

```
python <copilot-skills-dir>/git-push-guard/push_guard.py \
  --config <path to agents.config.yaml> \
  --repo <absolute path to the repo> \
  [--target-branch <branch-name>]
```

- Omit `--target-branch` for the common case (`git push`, `git push -u origin HEAD`) — the script reads
  the repo's currently checked-out branch via `git rev-parse --abbrev-ref HEAD`.
- Pass `--target-branch <name>` explicitly whenever the push refspec names a different branch than the
  one checked out (e.g. `git push origin <local-branch>:main`, or any push to a ref by name).

## Exit-code contract (blocking — not advisory)

- `0` → **ALLOWED**. Target branch is not protected. Proceed with `git push`.
- `3` → **BLOCKED**. Target branch is `main`, `master`, or the repo's configured `baseBranch` in
  `agents.config.yaml`. Do **not** push. Stop, do not retry with a different branch name to work
  around it, and hand off to the human developer instead — per each developer agent's "never push to
  main/master" rule.
- `4` → usage/config error (bad path, repo not found, git failure). Fix the invocation; this is not a
  policy decision.

## Rules

- Run this check every single time before `git push`, even if you believe you already know the branch
  is safe — the check is cheap (single Python process, no network) and removes any doubt.
- Never bypass a `3` (BLOCKED) result by retargeting the push to a different protected name, force-pushing,
  or pushing indirectly (e.g. opening a merge into `main`/`master` from the agent itself). Protected-branch
  changes go through a pull request, opened by the human developer or via the normal PR flow — not by the
  agent pushing directly.
- A `0` (ALLOWED) result only means the *branch name* is not protected. It does not replace the rest of
  the Definition of Done (build/test green, review loop clean, commit created) — those gates still apply
  before this check is even reached.
- This script never runs `git push` itself and never mutates the repo; it is read-only (one `git
  rev-parse` call plus reading the YAML config).

## Handoff evidence stamp

Include the result in your evidence output, e.g.:
`push-guard: allowed | target-branch: 1234-add-user-permissions` or
`push-guard: blocked | target-branch: main | action: skipped push, handed off to developer`.
