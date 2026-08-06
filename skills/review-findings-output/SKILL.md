---
name: review-findings-output
description: "Standard reviewer findings contract: severity buckets, file/line evidence, impact, and mandatory concrete code fixes."
---

# review-findings-output

Use this skill to keep reviewer outputs consistent and actionable.

## Canonical severity model (kit-wide, authoritative)

All agents (developers, reviewers, orchestrators) use ONE severity model:

| Severity | Meaning | Clean-gate blocking? |
|---|---|---|
| **Critical** | Exploitable security flaw, data loss/corruption, crash, or broken core contract. | Yes |
| **High** | Incorrect behavior, regression, race/lifetime/memory bug, or breaking API change. | Yes |
| **Medium** | Correctness/robustness risk in edge cases, missing validation, or contract drift. | Yes |
| **Low** | Minor quality/readability/style; safe to defer. | No |

Legacy label mapping (normalize to the model above): **Major ≡ High**, **Minor ≡ Low**.

**Clean gate = 0 Critical / 0 High / 0 Medium.** Low findings do not block but should be listed.

## Coverage dimensions (mandatory — every review AND every developer self-check)

Actively check ALL of these before declaring a clean gate. A dimension that is genuinely
not-applicable to the changed component must be explicitly marked N/A (do not silently skip):

1. **Functional correctness** — behavior matches intent; edge cases, error/empty/boundary inputs; API/contract conformance.
2. **Regression** — existing behavior preserved; run the existing test suites for the changed scope (not only new tests).
3. **Non-functional** — performance (hot paths, N+1/query shape), memory/allocations, resource/handle/connection lifetime.
4. **Concurrency** — data races, deadlocks, thread-safety, object/callback lifetime across threads, async ordering (critical for any shared mutable state).
5. **Security** — authn/authz and permission checks, input validation, injection (SQL/command/log), secret handling, sensitive-data exposure.

Map each dimension to the severity model above (e.g. a race/lifetime/memory bug is High; an exploitable security flaw is Critical).

## Required output contract

1. Include a concise summary with risk assessment.
2. Categorize findings using the canonical severity model above.
3. Include file-level entries with:
   - file path
   - line reference(s)
   - impact
   - rule/policy violated (when applicable)
4. Every finding must include a concrete fix:
   - why the fix is needed
   - exact change snippet (before/after or corrected code)
   - fix specific to the actual diff (no generic advice)

## Confidence tiers (surface every concern — separate confirmed from speculative)

Report findings in TWO tiers so nothing real is silently dropped, without polluting the clean gate with guesses:

1. **Confirmed findings** (high-confidence, evidence-backed) — the primary report. Each is tied to
   observable code in the diff and verified against the real code, then bucketed by the canonical severity
   model above. **Only Confirmed findings count toward the clean gate.**
2. **Low confidence / needs verification** — plausible concerns you could not fully prove from the diff
   alone (missing context, unclear call site, possible-but-unproven race/edge case). List each with:
   - what and where (file/line)
   - why it is uncertain (what evidence is missing)
   - how to confirm or refute it (the specific check to run)
   These do NOT count as Confirmed findings and never block the gate on their own (a clean gate is 0
   Critical / 0 High / 0 Medium *Confirmed* findings). But they must not be silently carried past "done":
   the consuming developer/orchestrator must **disposition each one** before the clean gate closes — verify
   → promote to Confirmed (then fix), or dismiss with a one-line, code-cited reason (see skill
   `quality-loop-harness` → "Clean gate"). Reviewers are read-only and only report them; never promote one
   to a blocking finding without verifying it first.

## Guardrails

- Surface every real concern — do not silently drop it: verified → Confirmed; plausible but unproven → Low confidence / needs verification.
- No fabrication in either tier: every item must reference code that actually exists in the diff/repo. Never invent files, line numbers, symbols, config keys, or behavior. Uncertainty about impact goes to the Low-confidence tier; it is never a licence to guess.
- Do not flag established or pre-existing patterns on pattern-matching alone; re-verify each concern against the current diff before reporting it (avoid false positives).
- Keep wording concise and implementation-focused.
