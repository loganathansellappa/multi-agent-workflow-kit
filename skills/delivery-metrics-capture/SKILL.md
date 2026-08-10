---
name: delivery-metrics-capture
description: "Capture lightweight delivery metrics per task to support quality/cost trend tracking and workflow tuning."
---

# delivery-metrics-capture

Use this skill at handoff time (ready/blocking).

## Objective

Log small, comparable execution metrics so teams can improve throughput, quality, and cost over time.

## Minimum metrics

1. Task type (implementation/review/orchestration)
2. Scope touched (repos/components)
3. Task tier (trivial/standard/complex) from `agent-preflight-check`
4. Loop count (how many review/fix cycles)
5. Verification status (pass / blocked / partial)
6. Blocker class when blocked (env/config/test/data/dependency)
7. Cost signals: token usage snapshot when available (drives the budget/alert loop)
8. Learning captured: count of durable lessons written via skill `learning-capture` (`0`/`none` if nothing durable) — the self-learning signal

## Output behavior

- Keep summary concise and machine-readable where possible.
- If data is unavailable, emit explicit `unknown` instead of guessing.
- Do not block completion solely because optional metrics are missing.

## Persist to a log (enforcement hook)

Append one JSON line per handoff to a metrics log so the team can review trends weekly, turning
"the agent should loop" from aspiration into a measurable signal.

- Path: `outputs.metricsLog` from `agents.config.yaml` if set, else `<outputs.reportsRoot>/agent-metrics.jsonl`.
- One JSON object per line, for example:

```json
{"ts":"2026-01-01T00:00:00Z","agent":"backend-developer","task_type":"implementation","tier":"standard","scope":["serviceA"],"loop_count":2,"verification":"pass","blocker_class":null,"tokens":"unknown","learned":1}
```

- Never write secrets, tokens, source code, or PII into the log — metadata only.
- If the log path is not writable, report `metrics: unwritten (path not writable)` and continue.

## Budget loop (governance, not just logging)

Metrics exist to be governed. `agent-preflight-check` reads this log at the start of each task and
compares against `outputs.budgets` (`perTaskTokenCeiling`, `rollingDailyTokenBudget`):

- Always include a `tokens` value when the runtime exposes it (use `unknown` only when truly unavailable) —
  the budget/alert check is only as good as this field.
- For a weekly rollup, aggregate `tokens` by `agent`, `tier`, and day with `scripts/metrics_rollup.py`
  to produce a tokens/task and model-mix trend (the board-level cost signal).
