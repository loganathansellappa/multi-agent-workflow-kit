#!/usr/bin/env python3
"""Sync live Copilot agents/skills back into this kit (live -> kit).

Live agents (~/.copilot/agents, flat) are the source of truth. The kit is the
distributable snapshot for other devs. This script copies each live *.agent.md into its
existing kit location (developers/ reviewers/ orchestrators/), mirrors skills, and mirrors
non-skill support files (agents/*.py). It NEVER copies personal/live-only agents that don't
already exist in the kit, and NEVER copies the per-user service-path.config.yaml or secrets.
Run scripts/validate_agents.py afterwards. Stdlib only; cross-platform.
"""
import argparse
import filecmp
import shutil
import sys
from pathlib import Path

SUPPORT_GLOBS = ("*.py",)


def same(a: Path, b: Path) -> bool:
    # True only if b exists and its bytes are identical to a (deep compare),
    # so we copy/report a file only when it has actually changed.
    return b.is_file() and filecmp.cmp(str(a), str(b), shallow=False)


def mirror_dir(src: Path, dst: Path, what_if: bool):
    """Mirror src -> dst (copy new/changed, delete extras in dst). Returns change count."""
    changes = 0
    dst.mkdir(parents=True, exist_ok=True)
    # Compare by relative path so we can detect both changed files and stragglers.
    src_files = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst) for p in dst.rglob("*") if p.is_file()}
    # Copy any source file that is new or whose contents differ from the dest.
    for rel in src_files:
        s, d = src / rel, dst / rel
        if not same(s, d):
            changes += 1
            if not what_if:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
    # Delete dest files that no longer exist in the source (true mirror).
    for rel in dst_files - src_files:
        changes += 1
        if not what_if:
            (dst / rel).unlink()
    return changes


def main(argv=None):
    # --what-if previews the sync (prints what would change) without touching disk.
    ap = argparse.ArgumentParser(description="Sync live Copilot agents/skills into the kit.")
    ap.add_argument("--kit-root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--live-root", default=str(Path.home() / ".copilot"))
    ap.add_argument("--what-if", action="store_true", help="Preview only; do not write")
    args = ap.parse_args(argv)

    kit_root, live_root = Path(args.kit_root), Path(args.live_root)
    kit_agents, live_agents = kit_root / "agents", live_root / "agents"
    kit_skills, live_skills = kit_root / "skills", live_root / "skills"

    copied, skipped = [], []

    # Agents: only update files that already have a home in the kit.
    kit_files = sorted(kit_agents.rglob("*.agent.md"))
    kit_names = {k.name for k in kit_files}
    for k in kit_files:
        src = live_agents / k.name
        if not src.is_file():
            skipped.append(f"{k.name}: not present in live")
            continue
        if not same(src, k):
            if args.what_if:
                copied.append(f"{k.name} -> {k.parent.name}/ (would update)")
            else:
                shutil.copy2(src, k)
                copied.append(f"{k.name} -> {k.parent.name}/")

    # Warn about live-only agents (personal or new) that the kit does not carry.
    if live_agents.is_dir():
        for p in sorted(live_agents.glob("*.agent.md")):
            if p.name not in kit_names:
                skipped.append(f"{p.name}: LIVE-ONLY (not in kit; add manually to a subfolder if it should ship)")

    # Non-skill support files under agents/ (e.g. fetch-review-prs.py) must be mirrored too.
    for glob in SUPPORT_GLOBS:
        if live_agents.is_dir():
            for p in sorted(live_agents.glob(glob)):
                dest = kit_agents / p.name
                if not same(p, dest):
                    if args.what_if:
                        copied.append(f"support:{p.name} (would update)")
                    else:
                        shutil.copy2(p, dest)
                        copied.append(f"support:{p.name}")
    # Drift warning: kit support files with no live counterpart.
    for glob in SUPPORT_GLOBS:
        for p in sorted(kit_agents.glob(glob)):
            if not (live_agents / p.name).is_file():
                skipped.append(f"{p.name}: kit support file has no live counterpart (possible drift)")

    # Skills: mirror live -> kit for skills already in the kit.
    if live_skills.is_dir():
        for d in sorted([x for x in kit_skills.iterdir() if x.is_dir()]):
            src_dir = live_skills / d.name
            if src_dir.is_dir():
                n = mirror_dir(src_dir, d, args.what_if)
                if args.what_if:
                    if n > 0:
                        copied.append(f"skill:{d.name} (would sync {n} file(s))")
                else:
                    copied.append(f"skill:{d.name}")

    verb = "WOULD SYNC" if args.what_if else "SYNCED"
    print(f"{verb}: {len(copied)} item(s)")
    for c in copied:
        print(f"  + {c}")
    if skipped:
        print("NOTES:")
        for s in skipped:
            print(f"  - {s}")
    print("\nNext: run scripts/validate_agents.py, review 'git status', then commit the kit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
