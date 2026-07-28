#!/usr/bin/env python3
"""KB health linter (advisory): find bloat/staleness/duplication in the knowledge base.

WHY
---
The knowledge base is living memory that agents WRITE to during the LEARN step.
Left ungoverned it only grows: pages get large, lessons duplicate, and old facts
go stale — so every future KB read costs more tokens. This script is the
deterministic signal that drives the `kb-curate` skill: it reports which pages
need consolidation. It NEVER edits anything — the agent (or a human) does the
actual merge/trim, using judgment.

WHAT IT CHECKS (all advisory)
-----------------------------
  * oversized pages   — files larger than --max-file-kb (default 12 KB)
  * stale pages       — newest ISO date (YYYY-MM-DD) in the file is older than
                        --stale-days (default 180), or the file has no dates
  * duplicate headings— the same Markdown heading text appears 2+ times in a file
                        (a common sign the same lesson was appended twice)
  * total KB size     — sum across all pages, so growth is visible over time

USAGE
-----
  python kb_lint.py --kb-root <path to KB dir> [--max-file-kb 12] [--stale-days 180]

Exit codes:
  0 = ran successfully (findings, if any, are advisory — not failures)
  4 = usage error (KB root missing / not a directory)

Stdlib only; cross-platform; read-only; no network.
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)


def newest_date(text):
    """Return the most recent ISO date found in the file, or None."""
    newest = None
    for m in ISO_DATE.finditer(text):
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            continue
        if newest is None or d > newest:
            newest = d
    return newest


def duplicate_headings(text):
    """Headings that appear more than once (case-insensitive), ignoring the
    top-level title. Signals a lesson likely appended twice."""
    seen = {}
    for h in HEADING.findall(text):
        key = h.strip().lower()
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
    return {h: n for h, n in seen.items() if n > 1}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Advisory KB health linter for the kb-curate skill.")
    ap.add_argument("--kb-root", required=True, help="Path to the knowledge-base directory")
    ap.add_argument("--max-file-kb", type=float, default=12.0, help="Flag pages larger than this (KB)")
    ap.add_argument("--stale-days", type=int, default=180, help="Flag pages whose newest date is older than this")
    args = ap.parse_args(argv)

    root = Path(args.kb_root)
    if not root.is_dir():
        print(f"ERROR: KB root not found: {root}", file=sys.stderr)
        return 4

    now = datetime.now(timezone.utc)
    md_files = sorted(root.rglob("*.md"))
    total_bytes = 0
    oversized, stale, dup = [], [], []

    for f in md_files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        size = len(text.encode("utf-8"))
        total_bytes += size
        rel = f.relative_to(root)

        if size > args.max_file_kb * 1024:
            oversized.append((str(rel), round(size / 1024, 1)))

        nd = newest_date(text)
        if nd is None:
            stale.append((str(rel), "no-date"))
        elif (now - nd).days > args.stale_days:
            stale.append((str(rel), nd.strftime("%Y-%m-%d")))

        dups = duplicate_headings(text)
        if dups:
            dup.append((str(rel), dups))

    # Report — concise, advisory. Empty sections are stated so "clean" is explicit.
    print(f"kb-lint: {len(md_files)} page(s), {round(total_bytes / 1024, 1)} KB total")

    if oversized:
        print(f"kb-lint: {len(oversized)} oversized (> {args.max_file_kb} KB) — consider splitting:")
        for rel, kb in oversized:
            print(f"  - {rel} ({kb} KB)")
    else:
        print("kb-lint: no oversized pages")

    if stale:
        print(f"kb-lint: {len(stale)} stale (> {args.stale_days}d or undated) — verify or archive:")
        for rel, when in stale:
            print(f"  - {rel} (newest: {when})")
    else:
        print("kb-lint: no stale pages")

    if dup:
        print(f"kb-lint: {len(dup)} page(s) with duplicate headings — likely double-appended lessons:")
        for rel, dups in dup:
            joined = "; ".join(f"'{h}' x{n}" for h, n in dups.items())
            print(f"  - {rel}: {joined}")
    else:
        print("kb-lint: no duplicate headings")

    return 0


if __name__ == "__main__":
    sys.exit(main())
