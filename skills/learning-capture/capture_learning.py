#!/usr/bin/env python3
"""Per-session learning capture (the WRITE counterpart to episodic_recall.py).

WHY
---
Recall (`agent-preflight-check/episodic_recall.py`) lets an agent reuse prior work
at task START. But nothing captured new learnings at task END, so the memory was
write-never / read-only: sessions ended and lessons evaporated. This tool closes
that half. It is deliberately LOW-FRICTION: the agent does not choose among KB
files, edit an index, or format a page. It appends ONE structured, dated,
source-cited line to a single append-only log:

    <kb-root>/lessons-log.jsonl

`kb-curate` later promotes/dedups these inbox entries into the structured KB pages.
`episodic_recall.py` reads this same log so a captured lesson is actually re-served
next session (loop closed).

DISCIPLINE (consistent with evidence-discipline)
------------------------------------------------
A capture REQUIRES both a lesson and a checkable source (file:line / command /
log line). A learning with no source is a guess, so it is rejected (exit 2).

USAGE
-----
  # capture a durable lesson
  python capture_learning.py --kb-root <dir> \
      --lesson "<one-sentence durable lesson>" \
      --source "<file:line | command | log line>" \
      [--layer backend|frontend|contracts] [--repo <name>] [--ticket <id>] \
      [--tags a,b] [--session <id>]

  # explicitly record that nothing durable came up (no silent skip)
  python capture_learning.py --kb-root <dir> --none

  # list recent captured lessons
  python capture_learning.py --kb-root <dir> --list [--limit N]

OUTPUT / STAMP
--------------
Prints a stamp line for the handoff, e.g.
  learned: 1 (backend) -> lessons-log.jsonl
  learned: none (nothing durable this session)
  learned: 0 (duplicate - already captured)

EXIT CODES
----------
  0 = captured / duplicate-skipped / none / listed
  2 = usage error (no --lesson/--source, or --source cites a non-existent file anchor)

EVIDENCE (100% code-proven only)
--------------------------------
Capture only OBSERVED facts. When a --source names a code file (known extension,
optionally with :line) — bare (Foo.cs:12) or path-qualified (src/Foo.cs:12) — the
file MUST exist or the capture is rejected (exit 2), killing fabricated/stale
citations. Path-qualified/absolute anchors resolve directly; a bare filename is
found via a bounded, pruned search under --source-root then cwd. Sources with no
file anchor (commands, log lines, URLs) are not file-verifiable and are allowed.
Reviewers (which never cd into the reviewed repo) should pass --source-root <repo
root>. Use --no-verify-source only for an intentionally external anchor.

Stdlib only; cross-platform; no network. Appends only (never rewrites history).
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

LOG_NAME = "lessons-log.jsonl"

CODE_EXTS = (
    ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".cs", ".ts", ".tsx", ".js",
    ".jsx", ".py", ".go", ".java", ".rb", ".rs", ".kt", ".scala", ".swift",
    ".yaml", ".yml", ".json", ".md", ".sql", ".xml", ".gradle", ".groovy",
    ".sh", ".ps1", ".proto", ".toml", ".ini", ".cfg", ".config",
)

_TOKEN_RE = re.compile(r"[^\s,;()]+")
_LINE_SUFFIX_RE = re.compile(r"^(.*?)(?::\d+(?::\d+)?)?$")
_PRUNE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "build", "out", "bin", "obj",
    "dist", "target", "packages", ".vs", ".vscode", ".idea", "__pycache__",
}
_WALK_FILE_CAP = 60000


def _file_anchors(source):
    """Tokens that name a code file (known extension, optional :line[:col]),
    whether bare (Foo.cs:12) or path-qualified (src/Foo.cs:12)."""
    out = []
    for tok in _TOKEN_RE.findall(source or ""):
        m = _LINE_SUFFIX_RE.match(tok)
        path = m.group(1) if m else tok
        if path.lower().endswith(CODE_EXTS):
            out.append(path)
    return out


def _find_by_name(root, name, cap=_WALK_FILE_CAP):
    """Bounded, pruned recursive search for a file basename under root. Returns
    True on first match; gives up after visiting ~cap files (so a fabricated
    citation cannot walk a huge repo forever)."""
    try:
        if not Path(root).is_dir():
            return False
    except OSError:
        return False
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        if name in filenames:
            return True
        seen += len(filenames)
        if seen > cap:
            return False
    return False


def verify_source(source, source_root=None):
    """(ok, bad_path). ok=False when the source names any code file (by known
    extension) that does NOT resolve to an existing file — EVERY named code file
    must exist (100% proof: one fabricated anchor riding along a real one is still
    a guess). Sources with no file anchor (commands, log lines, URLs) are not
    file-verifiable => ok. Per anchor: absolute path; join under --source-root /
    cwd; and for a bare filename a bounded, pruned search under --source-root then
    cwd (so bare citations — the common style — are actually verified, not silently
    trusted)."""
    anchors = _file_anchors(source)
    if not anchors:
        return True, None
    roots = [Path.cwd()]
    if source_root:
        roots.insert(0, Path(source_root))
    for path in anchors:
        p = Path(path)
        if p.is_absolute():
            if not p.exists():
                return False, path
            continue
        if any((r / p).exists() for r in roots):
            continue
        is_bare = ("/" not in path) and ("\\" not in path)
        if is_bare and any(_find_by_name(r, p.name) for r in roots):
            continue
        return False, path
    return True, None


def log_path(kb_root):
    return Path(kb_root) / LOG_NAME


def _normalize(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def read_lessons(kb_root):
    """Return the list of captured lesson dicts (best-effort; skips bad lines)."""
    p = log_path(kb_root)
    out = []
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def is_duplicate(entries, lesson):
    norm = _normalize(lesson)
    return any(_normalize(e.get("lesson", "")) == norm for e in entries if not e.get("none"))


def append_entry(kb_root, entry):
    p = log_path(kb_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Per-session learning capture (write counterpart to episodic recall).")
    ap.add_argument("--kb-root", required=True, help="KB root dir where lessons-log.jsonl lives")
    ap.add_argument("--lesson", default="", help="the durable, reusable lesson (one sentence)")
    ap.add_argument("--source", default="", help="checkable source: file:line / command / log line")
    ap.add_argument("--layer", default="", help="layer tag (backend/frontend/contracts/...)")
    ap.add_argument("--repo", default="", help="repo name")
    ap.add_argument("--ticket", default="", help="ticket id")
    ap.add_argument("--tags", default="", help="comma-separated tags")
    ap.add_argument("--session", default="", help="session id")
    ap.add_argument("--source-root", default="", help="base dir to resolve relative file:line sources (default: cwd)")
    ap.add_argument("--no-verify-source", action="store_true", help="skip file-existence check for the source anchor")
    ap.add_argument("--none", action="store_true", help="explicitly record that nothing durable came up")
    ap.add_argument("--list", action="store_true", help="list recent captured lessons and exit")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)

    if args.list:
        entries = [e for e in read_lessons(args.kb_root) if not e.get("none")]
        if not entries:
            print("learned: none captured yet")
            return 0
        for e in entries[-args.limit:]:
            layer = f" | {e['layer']}" if e.get("layer") else ""
            src = f" (src: {e['source']})" if e.get("source") else ""
            print(f"  - {e.get('date','?')}{layer} | {e.get('lesson','').strip()}{src}")
        return 0

    if args.none:
        print("learned: none (nothing durable this session)")
        return 0

    # capture path - enforce lesson + source (evidence-discipline)
    if not args.lesson.strip():
        print("ERROR: --lesson is required to capture (or use --none to record nothing durable)", file=sys.stderr)
        return 2
    if not args.source.strip():
        print("ERROR: --source is required (a lesson with no checkable source is a guess). "
              "Give file:line / command / log line.", file=sys.stderr)
        return 2

    if not args.no_verify_source:
        ok, bad = verify_source(args.source, args.source_root or None)
        if not ok:
            print(f"ERROR: --source cites a file that does not exist: '{bad}'. "
                  "Capture only OBSERVED, code-anchored facts — fix the path, cite a real "
                  "file:line/command/log line, or pass --no-verify-source for an "
                  "intentionally external anchor.", file=sys.stderr)
            return 2

    entries = read_lessons(args.kb_root)
    if is_duplicate(entries, args.lesson):
        print("learned: 0 (duplicate - already captured)")
        return 0

    entry = {
        "date": date.today().isoformat(),
        "lesson": args.lesson.strip(),
        "source": args.source.strip(),
        "layer": args.layer.strip(),
        "repo": args.repo.strip(),
        "ticket": args.ticket.strip(),
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()],
        "session": args.session.strip(),
    }
    append_entry(args.kb_root, entry)
    layer_note = f" ({entry['layer']})" if entry["layer"] else ""
    print(f"learned: 1{layer_note} -> {LOG_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
