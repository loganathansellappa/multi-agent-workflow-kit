---
name: quality-loop-harness
description: "Reusable build/verify/review/fix loop harness for deterministic engineering execution with explicit gates."
---

# quality-loop-harness

Use this skill to run and enforce repeatable engineering loops.

## Loop

1. PLAN (files, scope, risk, validation commands)
2. IMPLEMENT (scoped changes only)
3. BUILD/VERIFY (targeted command set for changed scope)
4. REVIEW (correct reviewer for changed component)
5. ADDRESS findings
6. RE-RUN BUILD/VERIFY + REVIEW
7. Repeat until clean gate is met

## Loop bounds (cap + breaker)

The loop is **bounded**, never open-ended. It must terminate at a clean gate *or* an explicit blocked
hand-off — never spin.

- **Max 3 review/fix cycles** (steps 4–6). A cycle = one REVIEW + one ADDRESS + one RE-RUN. If the clean
  gate is not met after the 3rd cycle, **stop and hand off BLOCKED** with the residual findings, the last
  verification output, and a recommended next step. Do not start a 4th cycle silently.
- **No-progress / oscillation breaker.** From the 2nd cycle onward (once a prior cycle's findings exist),
  compare the open-findings set to the previous cycle. Stop early and hand off BLOCKED if any holds:
  - the count of open Critical/High/Medium findings did not decrease, or
  - the *same* finding recurs after a fix attempt (fix didn't take / regressed), or
  - a fix for finding A re-opens a previously-closed finding B (thrashing).
- **Per-cycle wall-clock guidance.** If a single cycle's verification/build runs unexpectedly long for the
  task's scope, or exceeds an explicit `--timeout`, stop and hand off rather than blocking indefinitely.
- **On cap, breaker, or wall-clock trip:** hand off **BLOCKED** and escalate to the human. Report the loop
  count reached, the unresolved findings (or the partial result on a wall-clock trip), and why the loop
  could not close, so runaway loops / unbounded cost are visible.

## Clean gate

- No unresolved high-severity findings per agent policy.
- **All coverage dimensions checked** (see skill `review-findings-output` → "Coverage dimensions"):
  functional + regression + non-functional (perf/memory/lifetime) + concurrency + security. Mark N/A
  explicitly, never silently.
- **Low-confidence findings dispositioned.** Every "Low confidence / needs verification" item raised in
  REVIEW (see skill `review-findings-output` → "Confidence tiers") is resolved before the gate closes —
  either verify → promote to a Confirmed finding and fix it, or dismiss it with a one-line, code-cited
  reason. None may be carried past the gate unexamined. Reviewers are read-only and only report these; the
  developer/orchestrator owns the disposition.
- **Evidence-discipline self-check passes.** Every material claim in the plan/handoff/report carries an
  OBSERVED (cited) or INFERRED label via an Evidence Ledger (see skill `evidence-discipline`). Run
  `python skills/evidence-discipline/evidence_lint.py <report> --require-ledger`; the gate does not close
  while it reports a FAIL (an uncited claim is a guess — verify it or mark it INFERRED, then re-run).
- Verification commands for changed scope are successful, or blockers are explicit and unrelated.

## Required evidence

- Commands used for verification
- Summary pass/fail outcomes
- Findings disposition: fixed vs rejected with code-cited justification
- Evidence Ledger (claim | OBSERVED/INFERRED | source) — clean under `evidence_lint.py --require-ledger`

## Guardrails

- No one-pass "looks good" completion.
- Any fix restarts verification + review for the updated diff.
- Keep validation scoped; avoid unnecessary full-suite runs unless needed.
- The loop is **bounded** (see "Loop bounds"): at most 3 cycles, and it stops early on no-progress /
  oscillation. Never loop indefinitely — close at a clean gate or hand off BLOCKED.
