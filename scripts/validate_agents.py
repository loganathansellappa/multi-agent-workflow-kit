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
Also checks the kit does not track a real per-user config or secrets.
Exit code 0 = clean, 1 = findings. Stdlib only; cross-platform.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REVIEWER_ALLOWED = ("execute", "read", "search", "skill", "ask_user", "task")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint the Multi-Agent Workflow Kit.")
    ap.add_argument("--kit-root", default=str(Path(__file__).resolve().parent.parent),
                    help="Kit root (defaults to parent of scripts/)")
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

    if not findings:
        print(f"OK: agents kit lint passed ({len(agents)} agents checked).")
        return 0
    print(f"FINDINGS ({len(findings)}):")
    for f in findings:
        print(f" - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
