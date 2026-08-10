#!/usr/bin/env python3
"""Consistency lint for the Multi-Agent Workflow Kit.

Validates every *.agent.md under agents/ for:
  - YAML frontmatter present (name, description, tools)
  - exactly one "Operational Hardening" block
  - no duplicated "LEARN (mandatory after every task)" block
  - no leaked personal paths (C:\\Users\\<name>) and no absolute D:\\ / E:\\ paths in bodies
  - reviewer agents are read-only (tools within an allowlist; no edit/write)
  - every `skill `<name>`` reference resolves to skills/<name>/SKILL.md
  - no legacy clean-gate wording ("0 Major"); canonical gate is 0 Critical / 0 High / 0 Medium
Also checks the kit does not track a real per-user config or secrets, and runs a
repo-wide "keep it generic" scrub scan (see scan_repo_for_leaks) so no company /
product name, private host, personal path, or internal ticket id ever ships in the
public kit.
Exit code 0 = clean, 1 = findings. Stdlib only; cross-platform.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REVIEWER_ALLOWED = ("execute", "read", "search", "skill", "ask_user", "task")

# --- repo-wide scrub scan ("keep it generic") ------------------------------
# The public kit must contain no company/product names, private hosts, personal
# paths, or internal ticket ids. This scan enforces the CONTRIBUTING policy in
# code. It is deliberately company-AGNOSTIC: it names no company itself. Two
# layers:
#   1. Generic leak-CLASS patterns baked in below (high signal, low noise).
#   2. An OPTIONAL, gitignored local denylist file (scripts/company-terms.txt)
#      where a maintainer lists the actual product/host/user names to catch. The
#      committed repo never contains that file, so the names never leak either.
SCAN_EXT = {".md", ".py", ".yml", ".yaml", ".txt", ".json", ".sh", ".ps1",
            ".cfg", ".ini", ".toml"}
SCAN_SKIP_DIRS = {".git", "__pycache__", ".sync-backups", ".secrets", "tests"}
# Files that legitimately contain leak-like literals (this scanner's own
# patterns, and the local denylist) — never scan them for leaks.
SCAN_SKIP_RELPATHS = {"scripts/validate_agents.py", "scripts/company-terms.txt"}
DEFAULT_TERMS_REL = "scripts/company-terms.txt"

# JIRA-style internal ticket ids (e.g. ABC-1234). Case-sensitive prefix so lowercase
# tokens like "utf-8", "opus-4" are ignored.
TICKET_RE = re.compile(r"\b[A-Z]{2,10}-\d{1,6}\b")
# Standards / well-known prefixes that look like tickets but are public.
TICKET_PREFIX_ALLOW = {"ISO", "UTF", "SHA", "RFC", "AES", "RSA", "MD", "EC",
                       "CVE", "HTTP", "IPV", "PBKDF", "TLS", "SSL", "UTC"}
# Obvious placeholder numbers used in docs/examples (ABC-123, TICKET-123).
TICKET_PLACEHOLDER_NUM = re.compile(r"^(?:0+|1|12|123|1234|12345|123456)$")
# Personal home directories (Windows profile + unix home).
HOMEPATH_RE = re.compile(r"C:\\Users\\[A-Za-z0-9._-]+|/(?:home|Users)/[A-Za-z0-9._-]+/")
# Absolute drive paths other than the placeholder-friendly cases.
DRIVEPATH_RE = re.compile(r"(?<![A-Za-z0-9])[D-Z]:\\[A-Za-z0-9_.\\-]+")
# Private VCS hosting / internal-network hints.
PRIVATE_HOST_RE = re.compile(r"\bssh://[\w.-]+|\bgit@[\w.-]+|\b[\w.-]+\.(?:internal|corp|intranet)\b", re.I)


def _is_ticketish(tok):
    """True if tok looks like a REAL internal ticket (not a standard/placeholder)."""
    prefix, _, num = tok.partition("-")
    if prefix in TICKET_PREFIX_ALLOW:
        return False
    if TICKET_PLACEHOLDER_NUM.match(num):
        return False
    return True


def load_terms(terms_path):
    """Load the optional local denylist (one term per line; '#' comments)."""
    p = Path(terms_path)
    terms = []
    if p.is_file():
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                terms.append(ln)
    return terms


def scan_repo_for_leaks(kit_root, terms, terms_path=None):
    """Repo-wide company-agnostic leak scan. Returns a list of finding strings."""
    findings = []
    term_res = [(t, re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.I))
                for t in terms]
    terms_resolved = Path(terms_path).resolve() if terms_path else None
    for f in sorted(kit_root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SCAN_EXT:
            continue
        if any(part in SCAN_SKIP_DIRS for part in f.relative_to(kit_root).parts):
            continue
        rel = f.relative_to(kit_root).as_posix()
        if rel in SCAN_SKIP_RELPATHS:
            continue
        if terms_resolved and f.resolve() == terms_resolved:
            continue  # never scan the denylist file itself
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in HOMEPATH_RE.finditer(line):
                findings.append(f"{rel}:{i} : leaked personal path '{m.group(0)}'")
            for m in DRIVEPATH_RE.finditer(line):
                findings.append(f"{rel}:{i} : absolute drive path '{m.group(0)}' (use a placeholder)")
            for m in PRIVATE_HOST_RE.finditer(line):
                findings.append(f"{rel}:{i} : private host/URL '{m.group(0)}'")
            for tok in TICKET_RE.findall(line):
                if _is_ticketish(tok):
                    findings.append(f"{rel}:{i} : internal ticket id '{tok}' (strip or use a placeholder)")
            for term, rx in term_res:
                if rx.search(line):
                    findings.append(f"{rel}:{i} : company/product term '{term}' (keep the public kit generic)")
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint the Multi-Agent Workflow Kit.")
    ap.add_argument("--kit-root", default=str(Path(__file__).resolve().parent.parent),
                    help="Kit root (defaults to parent of scripts/)")
    ap.add_argument("--terms", default="",
                    help="Optional local denylist file (default: <kit-root>/scripts/company-terms.txt)")
    args = ap.parse_args(argv)

    kit_root = Path(args.kit_root)
    findings = []  # every rule violation is appended here; empty == clean
    agents_dir = kit_root / "agents"
    skills_dir = kit_root / "skills"

    # Check every agent definition in the kit against the kit's conventions.
    agents = sorted(agents_dir.rglob("*.agent.md"))
    for a in agents:
        text = a.read_text(encoding="utf-8", errors="ignore")
        name = a.name

        # Frontmatter must declare name + description + tools. The combined regex
        # is the happy path; if it fails we report each missing field individually.
        if not re.search(r"(?ms)^\s*---\s*.*?\bname:\s*.+?\bdescription:\s*.+?\btools:\s*\[.*?\].*?---", text):
            if not re.search(r"(?m)^name:\s*\S", text):
                findings.append(f"{name} : missing frontmatter 'name'")
            if not re.search(r"(?m)^description:\s*\S", text):
                findings.append(f"{name} : missing frontmatter 'description'")
            if not re.search(r"(?m)^tools:\s*\[", text):
                findings.append(f"{name} : missing frontmatter 'tools'")

        # Each agent must have exactly one Operational Hardening block: zero means
        # the safety/hardening rules are missing; more than one signals a bad merge.
        oh = len(re.findall(r"Operational Hardening|OPERATIONAL HARDENING", text))
        if oh == 0:
            findings.append(f"{name} : missing Operational Hardening block")
        if oh > 1:
            findings.append(f"{name} : duplicated Operational Hardening block ({oh})")

        # A duplicated LEARN block is another copy-paste/merge smell.
        learn = len(re.findall(r"LEARN \(mandatory after every task\)", text))
        if learn > 1:
            findings.append(f"{name} : duplicated LEARN block ({learn})")

        # Never ship someone's personal home path in a distributable agent.
        for m in re.finditer(r"C:\\Users\\[A-Za-z0-9._-]+", text):
            findings.append(f"{name} : leaked personal path '{m.group(0)}'")

        # Body-only checks (strip the leading YAML frontmatter block)
        body = re.sub(r"(?s)^\s*---.*?---", "", text, count=1)

        for m in re.finditer(r"(?<![A-Za-z0-9])[DE]:\\[A-Za-z0-9_.\\-]+", body):
            findings.append(f"{name} : absolute path in body '{m.group(0)}' (use config placeholders instead)")

        # Reviewer agents must be read-only.
        is_reviewer = bool(re.search(r"(?i)review", name)) or bool(re.search(r"(?im)^name:\s*.*review", text))
        if is_reviewer:
            tm = re.search(r"(?m)^tools:\s*\[([^\]]*)\]", text)
            tools_line = tm.group(1) if tm else ""
            tools = re.findall(r"'([^']+)'", tools_line)
            for tool in tools:
                if tool not in REVIEWER_ALLOWED:
                    findings.append(f"{name} : reviewer holds disallowed tool '{tool}' "
                                    f"(read-only allowlist: {', '.join(REVIEWER_ALLOWED)})")

        # Every 'skill `<name>`' reference must resolve to a shipped skill.
        for m in re.finditer(r"skill\s+`([a-z0-9\-]+)`", text):
            sk = m.group(1)
            if not (skills_dir / sk / "SKILL.md").is_file():
                findings.append(f"{name} : references skill '{sk}' but skills/{sk}/SKILL.md is missing")

        # Legacy clean-gate wording.
        if re.search(r"0\s+Major", body):
            findings.append(f"{name} : legacy severity wording '0 Major' (use 0 Critical / 0 High / 0 Medium)")

    # Repo hygiene: real per-user config / secrets must not be tracked. Ask git
    # for the tracked file list; if git isn't available, skip this check quietly.
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=str(kit_root),
                                 capture_output=True, text=True).stdout.splitlines()
    except Exception:
        tracked = []
    if "agents/agents.config.yaml" in tracked:
        findings.append("repo : agents/agents.config.yaml is tracked "
                        "(should be gitignored; ship .example instead)")
    for t in tracked:
        if re.search(r"\.secrets/", t) or re.search(r"\.token$", t):
            findings.append(f"repo : secret file tracked -> {t}")

    # Repo-wide "keep it generic" scrub scan (company-agnostic).
    terms_path = args.terms or str(kit_root / DEFAULT_TERMS_REL)
    findings.extend(scan_repo_for_leaks(kit_root, load_terms(terms_path), terms_path))

    if not findings:
        print(f"OK: agents kit lint passed ({len(agents)} agents checked).")
        return 0
    print(f"FINDINGS ({len(findings)}):")
    for f in findings:
        print(f" - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
