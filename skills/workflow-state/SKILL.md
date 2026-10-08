---
name: workflow-state
description: "Deterministic persist + validate-on-resume for orchestration invariants (planned scope, gate status, review/evidence SHA, terminal state). Backs scope-drift, stale-evidence rejection, and safe resume; read by the push-guard for the gate-status block."
---

# workflow-state

The data layer that makes an orchestrated run **recoverable and self-checking**. Copilot CLI
`/resume` restores the *conversation*, not your *workflow* — it never re-checks whether a gate
still holds, which commit a review passed against, or whether the tree drifted from the plan.
This skill records those invariants after each stage and **re-validates them against live git**
when you resume, so you never trust stale prose.

All state is a single per-repo JSON file at `<repo>/.git/copilot-workflow-state.json` (never
committed). Stdlib only, no network, never mutates the repo.

## When to use (orchestrators: feature-orchestrator; also any developer that pushes)

| Stage | Command |
|---|---|
| After PLAN | `python <skills>/workflow-state/workflow_state.py --repo <R> set-plan --files <f1> <f2> ...` |
| After each stage | `... --repo <R> set-stage --stage IMPLEMENT` |
| After a layer review passes | `... --repo <R> set-review --layer backend --status PASS` |
| After the deterministic gate | `... --repo <R> set-gate --status PASS` — or `--status FAIL --detail "<why>"` when it does **not** pass (never leave a failed gate unrecorded) |
| After gathering evidence | `... --repo <R> set-evidence` |
| At each routing/conclusion decision | `... --repo <R> log-decision --decision delegate --choice <agent> --alternatives <...> --reason <...> --evidence <...> [--confidence 0..1]` |
| Before push / at handoff | `... --repo <R> drift` and `... --repo <R> governance-check` |
| On terminal outcome | `... --repo <R> set-terminal --state SUCCESS` (see states below) |
| **On resume (first thing)** | `... --repo <R> validate` |
| Audit/replay the run's decisions | `... --repo <R> decisions` |

`set-gate`, `set-review`, and `set-evidence` stamp the current `HEAD` SHA automatically, so any
later check can tell whether the record is still fresh.

## Exit codes

- `set-*`, `get`, `gate-status`, `governance-check` → `0`.
- `drift` → `0` clean, `3` unexpected files changed (scope drift), `4` usage.
- `validate` → `0` state matches live git, `3` something is stale/FAIL (not safe to resume as-is), `4` usage.
- `set-*` with a bad enum value → `4`.

## What `validate` checks on resume (review #10 + #4)

- **Gate**: was it PASS, and against the *current* HEAD? A moved HEAD ⇒ re-run the gate.
- **Reviews**: each layer review still points at the current HEAD? Otherwise re-review.
- **Evidence**: gathered against the current HEAD? Otherwise treat as stale (re-cite).
- **Uncommitted edits**: even when a gate/review/evidence SHA still equals HEAD, if the working tree
  has uncommitted changes (staged/unstaged/untracked) the record no longer covers the live tree ⇒
  flagged STALE (review #4/#5). SHA equality alone is not treated as "fresh".
- **Plan/scope**: any files changed that were not planned? ⇒ scope drift.

Any of these ⇒ exit `3` with a specific reason. Fix the flagged item before continuing.

## Scope-drift (`drift`, review #6)

Compares the planned file set against everything actually changed since the plan's base commit
(committed + staged + unstaged + untracked). Unexpected files ⇒ exit `3`; update the plan or get
approval. Governance/policy files among the drift are tagged `[GOVERNANCE]`.

## Governance check (`governance-check`, review #8) — warn-only

Flags changed `*.agent.md`, `service-path.config.yaml`, `*.policy.md` — i.e. an agent editing the
rules that govern it. **This never blocks** (always exit `0`); it prints a WARN so the change is
called out in the handoff for explicit human review.

## Terminal / block states (review #11)

`set-terminal --state <X>` accepts: `SUCCESS`, `FAILED`, `BLOCKED`, `ESCALATED`, `LOOP_LIMIT`,
`BUDGET_EXCEEDED`, `NEEDS_CLARIFICATION`, `NEEDS_AUTHORIZATION`, `NEEDS_HUMAN_REVIEW`,
`TECHNICAL_BLOCK`, `POLICY_BLOCK`, `ENVIRONMENT_FAILURE`. Use these instead of looping forever.

## Decision ledger (`log-decision` / `decisions`) — audit only

An **append-only** record of *why* the orchestrator made each routing/conclusion
decision, so a run can be reconstructed after `/resume` instead of re-reading the
whole transcript. Each entry stamps the current HEAD and stores: the decision kind
(`delegate` / `conclude` / `expand-scope` / …), the `choice`, the `alternatives`
considered, one or more `reason`s, cited `evidence`, and an optional `confidence`.

```
python <skills>/workflow-state/workflow_state.py --repo <R> log-decision \
  --decision delegate --choice backend-developer \
  --alternatives frontend-developer do_not_delegate \
  --reason "files under backend/" "API acceptance criterion" \
  --evidence "git diff" "repo structure" --confidence 0.94
python <skills>/workflow-state/workflow_state.py --repo <R> decisions   # read back
```

It is deliberately **audit-only**: it never enforces, blocks, or gates anything
(the deterministic gate, `validate`, and the push-guard remain the control plane).
`validate` only prints a one-line count. Log a decision at each delegation and at
the final conclude; skip trivial no-choice steps. Entries are immutable — a
correction is a new entry, never an edit.

## Honest limits

- **Stage-level recovery, not a debugger.** The value is *re-validation on resume* — replacing "trust
  the transcript" with "re-check live git". It complements, and does not replace, server-side branch
  protection and CI gates.
- **Writes are atomic** (temp file + `os.replace`), so a crash or a concurrent invocation will not leave
  a torn/partial record; a corrupt file is reported on stderr rather than silently treated as empty. But
  there is no cross-process lock — two simultaneous writers still race to last-writer-wins.
- **The gate-FAIL push block is only as good as the agent's FAIL-recording discipline.** Unlike the
  protected-branch check (which the hook derives independently from the command), the soft gate block
  fires only if something actually ran `set-gate --status FAIL`. If a run is blocked but never records
  the FAIL, the block cannot fire. The orchestrator wiring mandates recording it; ad-hoc callers must remember.
- **Freshness is commit + working-tree, not per-line semantics.** `validate` flags a moved HEAD or any
  uncommitted edit, but it cannot know whether a specific change actually invalidates a given review —
  when in doubt it errs toward "re-check".
- **Free-text fields (`--detail`, `--note`) are stored verbatim** in the local (never-committed) state
  file. Don't paste secrets into them, same hygiene as any local log.
