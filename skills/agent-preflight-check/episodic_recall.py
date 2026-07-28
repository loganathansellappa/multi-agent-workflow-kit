#!/usr/bin/env python3
"""Cross-session episodic recall (advisory): "have we worked on this before?"

WHY
---
Each Copilot CLI session is independent, so agents re-solve problems they already
solved in a past session — re-reading the same files, rediscovering the same
build quirks. That is wasted tokens and time. The CLI already keeps a local
store of every past session (`~/.copilot/session-store.db`); this script queries
it read-only at task start and prints a short digest of prior sessions that
touched the same repo or the same files, so the agent can reuse instead of
rediscover.

It is ADVISORY: it never blocks. If the store is missing or a query fails, it
prints a single note and exits 0. It reads only session METADATA (summary line,
date, file path, branch) — never source, secrets, or full transcripts.

USAGE
-----
  python episodic_recall.py --repo <repo-name-or-path-substring> \
      [--files <comma-separated file paths/basenames>] \
      [--limit N] [--exclude-session <id>] [--db <path>]

At least one of --repo / --files should be given; with neither, it reports
nothing useful and exits 0.

OUTPUT
------
A compact, human- and model-readable digest, e.g.:

  recall: 3 prior session(s) on 'webplatformservices'
    - 2026-07-23 | Update Agent Permissions | branch=feature/x
    - 2026-07-22 | Password change timestamp | branch=main
  recall-files: 1 prior session touched 'openapi.yaml'
    - 2026-07-20 | Implement Password Change Timestamp

Stdlib only (sqlite3); cross-platform; read-only; no network.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path


def default_db():
    return str(Path.home() / ".copilot" / "session-store.db")


def connect_ro(db_path):
    """Open the store read-only so recall can never mutate session history."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def sessions_by_repo(cur, repo, limit, exclude):
    # Match either the recorded repository name or the working directory path,
    # so it works whether sessions were tagged by repo or only by cwd.
    like = f"%{repo}%"
    rows = cur.execute(
        """
        SELECT id,
               substr(coalesce(summary, ''), 1, 70),
               substr(coalesce(updated_at, created_at), 1, 10),
               coalesce(branch, '')
        FROM sessions
        WHERE (repository LIKE ? OR cwd LIKE ?) AND id != ?
        ORDER BY coalesce(updated_at, created_at) DESC
        LIMIT ?
        """,
        (like, like, exclude, limit),
    ).fetchall()
    return rows


def sessions_by_file(cur, basename, limit, exclude):
    rows = cur.execute(
        """
        SELECT DISTINCT s.id,
               substr(coalesce(s.summary, ''), 1, 60),
               substr(coalesce(s.updated_at, s.created_at), 1, 10)
        FROM session_files sf
        JOIN sessions s ON s.id = sf.session_id
        WHERE sf.file_path LIKE ? AND s.id != ?
        ORDER BY sf.first_seen_at DESC
        LIMIT ?
        """,
        (f"%{basename}", exclude, limit),
    ).fetchall()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Advisory cross-session episodic recall.")
    ap.add_argument("--repo", default="", help="Repo name or cwd-path substring to match")
    ap.add_argument("--files", default="", help="Comma-separated file paths/basenames to match")
    ap.add_argument("--limit", type=int, default=5, help="Max rows per section (default 5)")
    ap.add_argument("--exclude-session", default="", help="Session id to exclude (usually the current one)")
    ap.add_argument("--db", default=os.environ.get("SESSION_STORE_DB", default_db()))
    args = ap.parse_args(argv)

    if not args.repo and not args.files:
        print("recall: skipped (no --repo/--files given)")
        return 0

    db_path = Path(args.db)
    if not db_path.is_file():
        # Advisory, not an error: a fresh install simply has no history yet.
        print(f"recall: unavailable (no session store at {db_path})")
        return 0

    try:
        conn = connect_ro(str(db_path))
        cur = conn.cursor()
    except Exception as exc:
        print(f"recall: unavailable (cannot open session store: {exc})")
        return 0

    try:
        if args.repo:
            rows = sessions_by_repo(cur, args.repo, args.limit, args.exclude_session)
            if rows:
                print(f"recall: {len(rows)} prior session(s) on '{args.repo}'")
                for sid, summary, date, branch in rows:
                    branch_note = f" | branch={branch}" if branch else ""
                    label = summary.strip() or "(no summary)"
                    print(f"  - {date} | {label}{branch_note}")
            else:
                print(f"recall: no prior sessions on '{args.repo}'")

        for f in [x.strip() for x in args.files.split(",") if x.strip()]:
            base = os.path.basename(f) or f
            rows = sessions_by_file(cur, base, args.limit, args.exclude_session)
            if rows:
                print(f"recall-files: {len(rows)} prior session(s) touched '{base}'")
                for sid, summary, date in rows:
                    label = summary.strip() or "(no summary)"
                    print(f"  - {date} | {label}")
    except Exception as exc:
        # Any schema drift / query issue degrades to advisory silence.
        print(f"recall: unavailable (query failed: {exc})")
        return 0
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
