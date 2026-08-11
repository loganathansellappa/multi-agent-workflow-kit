---
name: kb-curate
description: "Consolidate and prune the knowledge base so it stays small, current, and cheap to read — periodic maintenance, not per-task work."
---

# kb-curate

Use this skill **occasionally** to keep the knowledge base (KB) healthy — not on every task. The KB is
living memory that agents write to during the `LEARN` step; left ungoverned it only grows, so every
future KB read costs more tokens and stale facts start misleading agents. This skill is the
consolidation pass that keeps it lean.

## When to run

- Periodically (e.g. weekly, or after a burst of `LEARN` write-backs), or
- When the linter below flags oversized/stale/duplicated pages, or
- When agents report the KB feels noisy or contradictory.

Do **not** run this as part of every implementation/review task — it is maintenance, and running it
per-task just burns tokens.

## Deterministic signal (run FIRST)

Get the facts before editing anything. The bundled linter is read-only and never edits the KB:

```
python <copilot-skills-dir>/kb-curate/kb_lint.py \
  --kb-root <path to your KB dir, e.g. kb/myproject> \
  [--max-file-kb 12] [--stale-days 180]
```

It reports, all advisory (exit code `0`):
- **oversized pages** — larger than `--max-file-kb`; candidates for splitting.
- **stale pages** — newest ISO date older than `--stale-days`, or undated; verify or archive.
- **duplicate headings** — same heading appears 2+ times in a page; a lesson likely appended twice.
- **total KB size** — so growth is visible run over run.

## Validate the learning inbox (`lessons-log.jsonl`) — turn hints into knowledge

New lessons captured during the `LEARN` step are appended to `<kb-root>/lessons-log.jsonl` with
`validated: false`. Until curated, `episodic_recall` surfaces them to future sessions marked
`[unverified]` — advisory hints, not authority. Curating them is what makes them trusted:

1. **Re-verify each unverified entry** against the *current* repo at the cited `source` anchor. A
   resolvable anchor only proves the source exists — confirm the entry's actual conclusion is still true.
2. **If confirmed** — set `validated: true` on that JSONL line (and promote it into the relevant
   structured KB page). It will then recall without the `[unverified]` mark.
3. **If wrong, stale, or unprovable** — delete the line. A wrong lesson recalled as fact is worse than
   no lesson.
4. **Never set `validated: true` without re-verification** — that defeats the safeguard.

## Consolidation procedure (human/agent judgment — the script never edits)

Work only from the linter's findings; make the smallest change that fixes each one:

1. **Deduplicate** — when a page has duplicate headings or two pages state the same lesson, merge them
   into one authoritative entry. Keep the most recent, verified version; delete the copy.
2. **Trim stale** — for flagged stale pages, re-verify the fact against the current repo. If still
   true, re-date it (`YYYY-MM-DD`); if obsolete, remove it or move it to an archive page.
3. **Split oversized** — break a large page along its natural headings into focused pages, and register
   the new pages in `00-index.md` so agents can find them.
4. **Keep entries short, dated, verified** — every retained lesson should be a few lines with an ISO
   date and confirmed against reality. A KB full of stale guesses is worse than no KB.
5. **Preserve the map** — after any split/merge/rename, update `00-index.md` so the index still points
   to every page. Agents read the index first; a broken map silently hides knowledge.

## Safety

- **Never delete a current, verified lesson** just because it is undated — re-date it instead.
- **Prefer merge over delete** when unsure; consolidation should lose no real knowledge.
- The linter is read-only; all edits are deliberate and reviewable. Commit KB changes separately from
  code changes so the curation is auditable.

## Output

Emit a concise summary of what changed, e.g.:
`kb-curate: merged 2 dup lessons, archived 1 stale page, split 1 oversized page; KB 84 KB -> 61 KB`.
