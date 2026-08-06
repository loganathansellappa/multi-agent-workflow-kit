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
- Verification commands for changed scope are successful, or blockers are explicit and unrelated.

## Required evidence

- Commands used for verification
- Summary pass/fail outcomes
- Findings disposition: fixed vs rejected with code-cited justification

## Guardrails

- No one-pass "looks good" completion.
- Any fix restarts verification + review for the updated diff.
- Keep validation scoped; avoid unnecessary full-suite runs unless needed.
