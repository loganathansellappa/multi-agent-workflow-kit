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

## Guardrails

- Report only high-confidence issues.
- Tie each finding to observable code in the diff.
- Keep wording concise and implementation-focused.
