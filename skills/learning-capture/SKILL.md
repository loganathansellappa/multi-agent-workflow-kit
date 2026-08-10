---
name: learning-capture
description: "Per-session self-learning: at task end, capture durable, source-cited lessons to an append-only KB inbox so future sessions reuse them instead of rediscovering. The WRITE counterpart to agent-preflight-check's episodic recall."
---

# learning-capture

Closes the self-learning loop. Recall (`agent-preflight-check` → `episodic_recall.py`) reads prior work
at task **start**; this skill captures new learnings at task **end**. Without it the agent's memory is
read-only — sessions end and lessons evaporate (the classic "agents don't self-learn" complaint).

## When to capture (every non-trivial session)

At the LEARN step, before you hand off, decide: did this task surface something a future session on this
codebase would benefit from and could NOT trivially re-derive? Examples worth capturing:

- a new/changed build, lint, test, or env command (and the fix for a failure)
- a versioning / contract / codegen rule (e.g. "a read-only API field must map to an immutable model property")
- a reusable bug shape or cross-layer gotcha, with the anchor (`file:line`)
- a workflow correction (a gate that should have run, an order that matters)

If nothing durable came up, record that explicitly — never skip silently.

## How (low-friction — one append, not a KB edit)

You do **not** pick among KB pages or edit the index. Append one structured, dated, **source-cited** line
to the KB inbox with the tool:

```
python skills/learning-capture/capture_learning.py --kb-root <path to your KB dir> \
  --lesson "<one-sentence durable lesson>" \
  --source "<file:line | command | log line>" \
  [--layer backend|frontend|contracts] [--repo <name>] [--ticket <id>] [--tags a,b]
```

If nothing durable came up:

```
python skills/learning-capture/capture_learning.py --kb-root <path to your KB dir> --none
```

The tool **requires a checkable `--source`** (consistent with `evidence-discipline`: a lesson with no
source is a guess and is rejected), and it **de-duplicates** — re-capturing the same lesson is a no-op.

## The stamp (make learning visible)

The tool prints a `learned:` line for your handoff stamp — include it:

- `learned: 1 (backend) -> lessons-log.jsonl`
- `learned: none (nothing durable this session)`
- `learned: 0 (duplicate - already captured)`

This is the anti-evaporation signal: a handoff with no `learned:` line means LEARN was skipped.
`delivery-metrics-capture` records it so `metrics_rollup.py` can trend how often sessions actually learn.

## The loop is closed

Captures land in `<kb-root>/lessons-log.jsonl`. `episodic_recall.py --kb-root <kb-root>` reads that same
log at the next task start and re-serves matching lessons (`recall-lessons: …`). `kb-curate` periodically
promotes/dedups inbox entries into the structured KB pages and keeps the log from growing unbounded.

## Guardrails

- One lesson = one append. Keep it a single, concrete, reusable sentence — not a session summary.
- Always cite a source; if you can't, it isn't a durable fact yet (mark it INFERRED in the report, don't
  capture it).
- Never capture secrets, tokens, customer data, or repo-specific paths that won't generalize.
- Do not rewrite or delete log history here — consolidation is `kb-curate`'s job.
