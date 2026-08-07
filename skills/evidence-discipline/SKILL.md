---
name: evidence-discipline
description: "Producer-side anti-guessing rule: label every factual claim OBSERVED vs INFERRED, cite file:line / log-line / command output, and never present inference as fact. The developer/orchestrator counterpart to reviewers' anti-fabrication rule."
---

# evidence-discipline

Keeps developers and orchestrators honest about what they actually know versus what they are guessing.
Reviewers already have an anti-fabrication rule (skill `review-findings-output`); this is the same
discipline for **producers** — the agents that investigate, plan, and change code.

## The rule

Every factual claim you make about code, config, behavior, or system state must be one of:

- **OBSERVED** — you verified it against a concrete source. Cite the source inline: `file:line`, a
  quoted log line, a command + its output, a symbol you actually read, or a specific doc anchor.
- **INFERRED** — a reasonable deduction you have NOT confirmed. Label it explicitly (e.g.
  "INFERRED:", "likely", "appears to") AND state what would confirm it.

Never present INFERRED as OBSERVED. If you cannot cite a source, it is not a fact yet — say so.

## When it applies (every gate, not just the end)

- **Discovery / analysis:** distinguish what the code shows from what you assume it does.
- **Plan:** claims about affected files, call paths, contract impact, and blast radius must be
  OBSERVED (cite) or flagged INFERRED with the check that would confirm them.
- **Fix justification & finding disposition:** verifying or dismissing a reviewer finding requires a
  code-cited reason (`file:line`), not a plausibility argument.
- **Handoff / report:** any value you assert (timeout, version, host, config key, DB column, deployed
  setting) carries its source, or is marked INFERRED.

## What to do when you don't have evidence

1. Go get it — read the file, run the command, check the log — then cite it.
2. If you genuinely cannot (no access, out of scope), state the gap explicitly and mark the claim
   INFERRED / unknown. Do not fill the gap with a confident guess.
3. Prefer "I need to verify X at Y before I can say" over a fabricated certainty.

## Guardrails

- No invented files, line numbers, symbols, config keys, versions, or behavior — ever.
- "Sounds right" / pattern-matching from another repo is INFERRED until re-verified against the actual
  code in front of you.
- Uncertainty is not a licence to guess; it is a signal to either verify or clearly label the doubt.
- Being explicitly uncertain (with the check to run) is always preferred over being confidently wrong.
