#!/usr/bin/env python3
"""Portable capability-contract check — the drift insurance that travels with the pack.

Unlike tests/test_capability_contract.py (which runs against the *kit* source tree and
freezes an exact per-agent snapshot), this script is self-contained and layout-agnostic:
it classifies each agent's role from its NAME, so it works identically against the kit's
`agents/<role>/` tree AND the flattened `~/.copilot/agents/` install that teammates
actually receive and hand-edit. install_to_copilot.py ships this file into ~/.copilot and
runs it after every install, so a prompt tweak that silently strips a reviewer's read-only
guarantee (or drops the loop cap) is caught on the recipient's machine, not in production.

Universally-true invariants asserted (both the internal and public kits):
  - Reviewers (name ends in '-reviewer', or a known reviewer name) hold NO mutation tool
    (edit/create/write/apply_patch/...).
  - Reviewers other than the meta-reviewer hold NO delegation tool (task/write_agent).
  - Developers (name ends in '-developer') hold BOTH edit and task.
  - The quality-loop-harness skill still declares a numeric review/fix loop cap.

Usage:
  python capability_check.py                         # defaults to ~/.copilot
  python capability_check.py --agents-dir <dir> --skills-dir <dir>
Exit code: 0 if all invariants hold, 1 on any violation, 4 on usage error.
Stdlib only; read-only; cross-platform.
"""
import argparse
import re
import sys
from pathlib import Path

MUTATION_TOOLS = {"edit", "create", "write", "apply_patch", "str_replace", "multi_edit", "insert"}
DELEGATION_TOOLS = {"task", "write_agent"}

# Reviewers that do not end in '-reviewer', and any meta-reviewer allowed to delegate.
EXTRA_REVIEWERS = set()
META_REVIEWERS = set()  # reviewers here may hold 'task' (spawn review sub-agents)


def parse_tools(text):
    m = re.search(r"(?m)^tools:[ \t]*\[([^\]]*)\]", text)
    return set(re.findall(r"'([^']+)'", m.group(1))) if m else set()


def parse_name(text, fallback):
    m = re.search(r"(?m)^name:[ \t]*(\S+)", text)
    return m.group(1).strip() if m else fallback


def is_reviewer(name):
    return name.endswith("-reviewer") or name in EXTRA_REVIEWERS


def is_developer(name):
    return name.endswith("-developer")


def check_agents(agents_dir):
    """Return (violations, agent_count, reviewer_count)."""
    violations = []
    agents = {}
    for p in sorted(Path(agents_dir).rglob("*.agent.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        agents[parse_name(text, p.stem.replace(".agent", ""))] = parse_tools(text)

    reviewer_count = 0
    for name, tools in agents.items():
        if is_reviewer(name):
            reviewer_count += 1
            bad = sorted(tools & MUTATION_TOOLS)
            if bad:
                violations.append(f"reviewer '{name}' holds mutation tool(s) {bad} (must be read-only)")
            if name not in META_REVIEWERS:
                badd = sorted(tools & DELEGATION_TOOLS)
                if badd:
                    violations.append(f"reviewer '{name}' holds delegation tool(s) {badd} (must not sub-delegate)")
        elif is_developer(name):
            for req in ("edit", "task"):
                if req not in tools:
                    violations.append(f"developer '{name}' is missing required tool '{req}'")
    return violations, len(agents), reviewer_count


def check_loop_cap(skills_dir):
    """The quality-loop-harness skill must still declare a numeric review/fix cap."""
    skill = Path(skills_dir) / "quality-loop-harness" / "SKILL.md"
    if not skill.is_file():
        return [f"quality-loop-harness/SKILL.md not found under {skills_dir} (loop cap unverifiable)"]
    text = skill.read_text(encoding="utf-8", errors="ignore")
    # A digit on a line that also mentions a cycle/loop cap.
    for line in text.splitlines():
        low = line.lower()
        if re.search(r"\d", line) and ("cycle" in low or "loop" in low) and \
           any(w in low for w in ("cap", "max", "most", "bound")):
            return []
    return ["quality-loop-harness declares no numeric review/fix loop cap (bound removed?)"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Portable per-agent capability-contract check.")
    default_root = Path.home() / ".copilot"
    ap.add_argument("--agents-dir", default=str(default_root / "agents"))
    ap.add_argument("--skills-dir", default=str(default_root / "skills"))
    args = ap.parse_args(argv)

    agents_dir = Path(args.agents_dir)
    if not agents_dir.is_dir():
        print(f"capability-check: agents dir not found: {agents_dir}", file=sys.stderr)
        return 4

    violations, n_agents, n_reviewers = check_agents(agents_dir)
    violations += check_loop_cap(args.skills_dir)

    if violations:
        print(f"capability-check: FAIL ({len(violations)} violation(s)) "
              f"[{n_agents} agents, {n_reviewers} reviewers]")
        for v in violations:
            print(f"  - {v}")
        return 1
    print(f"capability-check: PASS ({n_agents} agents, {n_reviewers} reviewers, loop cap present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
