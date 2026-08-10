#!/usr/bin/env python3
"""Deterministic (non-LLM) enforcement half of the evidence-discipline pair.

WHAT THIS IS
------------
`skills/evidence-discipline/SKILL.md` is the *advisory* half: it tells a producer
agent to label every claim OBSERVED vs INFERRED and cite a source. Like any skill,
it only helps if the agent follows it. This linter is the *enforcement* half: it
reads an agent's report / handoff text and mechanically checks that the discipline
was actually applied, so "the agent should cite evidence" (instruction) becomes
"an uncited claim is flagged" (check). Mirrors git-push-guard (skill + hook) and
kb-curate (skill + kb_lint.py).

WHAT IT CHECKS
--------------
1. Evidence Ledger present (a markdown table whose header has a `claim` column and
   a `status`/`evidence` column and a `source` column). With --require-ledger a
   missing ledger is a FAIL; otherwise it is a WARNING when the text asserts facts.
2. Every ledger row:
     - status is exactly OBSERVED or INFERRED (case-insensitive)  -> else FAIL
     - source/evidence cell is non-empty                          -> else FAIL
     - OBSERVED rows cite a *real* source (file:line, `path.ext`, a command, a
       quoted log/output) and not a vague hand-wave ("the code", "obvious",
       "as expected")                                             -> else FAIL
     - INFERRED rows state how they'd be confirmed (non-trivial note)-> else WARN
3. Prose scan (advisory WARNINGS): confident factual assertions *outside* the
   ledger — version numbers, timeouts/durations, ports, absolute words
   (always/never/guaranteed), or "it does/returns/throws X" — that have no nearby
   citation. These are the classic guessing patterns.

EXIT CODES
----------
  0 = clean (no FAILs; and, with --strict, no WARNINGs)
  1 = one or more FAILs (or WARNINGs under --strict) -> use to gate a loop
  2 = usage error (no input)
Use --advisory to always exit 0 (print findings only).

Stdlib only; cross-platform; reads a file arg or stdin.
"""
import argparse
import re
import sys
from pathlib import Path

# --- citation detectors (what counts as a real, checkable source) ---
FILE_LINE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}:\d+")          # foo/bar.cpp:142
BACKTICK_PATH = re.compile(r"`[^`]*\.[A-Za-z0-9]{1,6}[^`]*`")         # `Foo.cs`, `openapi.yaml`
INLINE_CODE = re.compile(r"`[^`]+`")                                   # any inline code span
SOURCE_KEYWORD = re.compile(
    r"\b(log|logs|log line|stack ?trace|command|output|stdout|stderr|grep|"
    r"git |ran |line \d+|column|query|response body)\b",
    re.IGNORECASE,
)
VAGUE_SOURCE = re.compile(
    r"^\s*(the code|code|obvious|as (?:expected|usual)|standard|"
    r"common knowledge|well[- ]known|n/?a|-+|see above|self[- ]evident|clearly)\s*$",
    re.IGNORECASE,
)

# --- risk / assertion patterns for the prose scan ---
RISK_PATTERNS = [
    ("version", re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")),
    ("duration", re.compile(r"\b\d+\s?(?:ms|s|sec|secs|seconds|m|min|mins|minutes|h|hr|hrs|hours)\b", re.IGNORECASE)),
    ("port", re.compile(r"\b(?:port\s+\d{2,5}|:\d{2,5}\b)")),
    ("absolute", re.compile(r"\b(?:always|never|guaranteed|cannot|can't|will not|won't|impossible)\b", re.IGNORECASE)),
    ("behavior", re.compile(r"\bit (?:does|calls|returns|throws|sets|uses|sends|reads|writes|triggers|handles)\b", re.IGNORECASE)),
]
STATUS_TOKEN = re.compile(r"\b(OBSERVED|INFERRED)\b")


def has_citation(text):
    """True if the text contains any checkable source token."""
    return bool(
        FILE_LINE.search(text)
        or BACKTICK_PATH.search(text)
        or SOURCE_KEYWORD.search(text)
        or STATUS_TOKEN.search(text)
    )


def _split_lines(text):
    return text.splitlines()


def find_ledger_rows(lines):
    """Return (found, rows) where rows is a list of (lineno, [cells]) for data rows
    of the first markdown table that looks like an evidence ledger."""
    header_idx = None
    for i, ln in enumerate(lines):
        if "|" not in ln:
            continue
        low = ln.lower()
        if "claim" in low and ("source" in low or "evidence" in low):
            header_idx = i
            break
    if header_idx is None:
        return (False, [])
    rows = []
    # skip header + separator row (---|---), collect until table ends
    j = header_idx + 1
    if j < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[j]):
        j += 1
    while j < len(lines) and "|" in lines[j]:
        cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
        rows.append((j + 1, cells))
        j += 1
    return (True, rows)


def _header_columns(lines):
    for ln in lines:
        low = ln.lower()
        if "|" in ln and "claim" in low and ("source" in low or "evidence" in low):
            return [c.strip().lower() for c in ln.strip().strip("|").split("|")]
    return []


def validate_ledger(lines):
    findings = []
    cols = _header_columns(lines)
    # locate column indices (best-effort)
    def col_idx(*names):
        for n in names:
            for i, c in enumerate(cols):
                if n in c:
                    return i
        return None

    i_status = col_idx("status", "observed", "evidence type", "type")
    i_source = col_idx("source", "evidence", "citation", "proof")
    _, rows = find_ledger_rows(lines)
    for lineno, cells in rows:
        status = cells[i_status] if (i_status is not None and i_status < len(cells)) else ""
        source = cells[i_source] if (i_source is not None and i_source < len(cells)) else ""
        status_u = status.upper()
        if not STATUS_TOKEN.search(status_u):
            findings.append(("FAIL", lineno, f"ledger row status must be OBSERVED or INFERRED, got '{status}'"))
            continue
        if not source or VAGUE_SOURCE.match(source):
            label = "OBSERVED" if "OBSERVED" in status_u else "INFERRED"
            findings.append(("FAIL", lineno, f"{label} row has empty/vague source: '{source or '(empty)'}'"))
            continue
        if "OBSERVED" in status_u and not has_citation(source) and not has_citation(cells[0] if cells else ""):
            findings.append(("FAIL", lineno, f"OBSERVED row lacks a checkable source (file:line/`path.ext`/log/command): '{source}'"))
        if "INFERRED" in status_u and len(source) < 8:
            findings.append(("WARN", lineno, f"INFERRED row should state how to confirm it: '{source}'"))
    return findings


def prose_scan(lines, ledger_line_range):
    """Advisory: flag confident assertions outside the ledger with no nearby citation."""
    findings = []
    lo, hi = ledger_line_range
    for i, ln in enumerate(lines):
        if lo <= i <= hi:
            continue  # inside ledger table
        stripped = ln.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(">"):
            continue
        for kind, pat in RISK_PATTERNS:
            if pat.search(ln):
                # backed if this line OR its neighbor carries a citation/status
                context = ln
                if i > 0:
                    context += "\n" + lines[i - 1]
                if i + 1 < len(lines):
                    context += "\n" + lines[i + 1]
                if not has_citation(context):
                    findings.append(("WARN", i + 1, f"uncited {kind} assertion: \"{stripped[:90]}\""))
                break  # one finding per line is enough
    return findings


def lint(text, require_ledger=False):
    lines = _split_lines(text)
    found, rows = find_ledger_rows(lines)
    findings = []

    if not found:
        # any factual assertion at all?
        risky = any(pat.search(text) for _, pat in RISK_PATTERNS)
        sev = "FAIL" if require_ledger else ("WARN" if risky else "INFO")
        findings.append((sev, 0, "no Evidence Ledger table found (header needs a 'claim' and 'source'/'evidence' column)"))
        findings.extend(prose_scan(lines, (-1, -1)))
        return findings

    # ledger present: compute its line span to exclude from prose scan
    ledger_lines = [ln for ln, _ in rows]
    span = (min(ledger_lines) - 3, max(ledger_lines)) if ledger_lines else (-1, -1)
    findings.extend(validate_ledger(lines))
    findings.extend(prose_scan(lines, span))
    if not rows:
        findings.append(("WARN", 0, "Evidence Ledger table is empty (no claim rows)"))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic evidence-discipline linter for agent reports/handoffs.")
    ap.add_argument("path", nargs="?", help="file to lint (default: stdin)")
    ap.add_argument("--require-ledger", action="store_true", help="a missing Evidence Ledger is a FAIL")
    ap.add_argument("--strict", action="store_true", help="WARNINGs also cause a non-zero exit")
    ap.add_argument("--advisory", action="store_true", help="always exit 0 (print findings only)")
    args = ap.parse_args(argv)

    if args.path:
        p = Path(args.path)
        if not p.is_file():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            return 2
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("ERROR: no input (pass a file or pipe text on stdin)", file=sys.stderr)
        return 2

    findings = lint(text, require_ledger=args.require_ledger)
    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]

    for sev, lineno, msg in findings:
        loc = f"line {lineno}" if lineno else "doc"
        print(f"evidence-lint [{sev}] {loc}: {msg}")

    print(f"\nevidence-lint: {len(fails)} FAIL, {len(warns)} WARN")
    if args.advisory:
        return 0
    bad = len(fails) + (len(warns) if args.strict else 0)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
