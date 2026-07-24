#!/usr/bin/env python3
"""Install kit agents and skills into ~/.copilot (kit -> live). Cross-platform.

Flattens all *.agent.md files into ~/.copilot/agents for reliable discovery, copies
supporting root files, and mirrors skills. Replaces the earlier install-to-copilot.ps1
(Windows) and install-to-copilot.sh (Unix) with one stdlib-only Python entry point.
"""
import argparse
import shutil
import sys
from pathlib import Path

SUPPORT_FILES = ("agents.config.yaml",)


def main(argv=None):
    # Copy the kit's agents + skills into the live ~/.copilot install so the
    # Copilot CLI can discover them. Idempotent: safe to re-run after edits.
    ap = argparse.ArgumentParser(description="Install kit agents and skills into ~/.copilot.")
    ap.add_argument("--kit-root", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args(argv)

    kit_root = Path(args.kit_root)
    src_agents = kit_root / "agents"
    src_skills = kit_root / "skills"

    dest_copilot = Path.home() / ".copilot"
    dest_agents = dest_copilot / "agents"
    dest_skills = dest_copilot / "skills"
    dest_agents.mkdir(parents=True, exist_ok=True)
    dest_skills.mkdir(parents=True, exist_ok=True)

    # Flatten all *.agent.md files into ~/.copilot/agents for reliable discovery.
    for f in src_agents.rglob("*.agent.md"):
        shutil.copy2(f, dest_agents / f.name)

    # Copy supporting root files expected by agents.
    for name in SUPPORT_FILES:
        src = src_agents / name
        if src.is_file():
            shutil.copy2(src, dest_agents / name)

    # Warn if the per-user config has not been created from the template yet.
    if not (src_agents / "agents.config.yaml").is_file():
        print("WARNING: agents.config.yaml not found. Copy agents.config.example.yaml to "
              "agents.config.yaml and fill in your paths before running agents.", file=sys.stderr)

    # Copy skills (recursive).
    for d in src_skills.iterdir():
        if d.is_dir():
            shutil.copytree(d, dest_skills / d.name, dirs_exist_ok=True)

    print(f"Installed agents and skills to {dest_copilot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
