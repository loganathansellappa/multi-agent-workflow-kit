#!/usr/bin/env python3
"""Install kit agents and skills into ~/.copilot (kit -> live). Cross-platform.

Flattens all *.agent.md files into ~/.copilot/agents for reliable discovery, copies
supporting root files, and mirrors skills. Before overwriting, it snapshots the current
live agents+skills into ~/.copilot/.install-backups/<timestamp>/ (keeping the newest 3)
so a bad install or a later hand-edit can be rolled back. After installing it ships the
portable capability check into ~/.copilot/scripts/ and runs it against the freshly
installed agents, so the drift insurance travels with the pack and privilege drift is
caught on the recipient's machine. Replaces the earlier install-to-copilot.ps1 / .sh.
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SUPPORT_FILES = ("agents.config.yaml",)
CONFIG_FILE = "agents.config.yaml"  # per-repo config the push-guard reads baseBranch from
BACKUP_KEEP = 3  # rotating backups of live agents+skills kept before each overwrite


def _write_hook_config(hook_config_src, dest_path, config_path):
    """Install the hook registration, wiring PUSH_GUARD_CONFIG to the live per-repo
    config when it exists so the push-guard protects each repo's configured baseBranch
    (e.g. develop/release/x), not just the hard-coded main/master. When no config is
    present the value is left blank — the unconditional main/master protection still
    applies, so this only ever ADDS coverage. Returns the resolved path (or "")."""
    resolved = str(config_path) if config_path and config_path.is_file() else ""
    try:
        data = json.loads(hook_config_src.read_text(encoding="utf-8"))
        wired = False
        for entry in data.get("hooks", {}).get("preToolUse", []):
            env = entry.get("env")
            if isinstance(env, dict) and "PUSH_GUARD_CONFIG" in env:
                env["PUSH_GUARD_CONFIG"] = resolved
                wired = True
        if wired:
            dest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return resolved
    except Exception:
        pass  # fall back to a verbatim copy so a parse error never blocks install
    shutil.copy2(hook_config_src, dest_path)
    return ""


def _read_kb_root(config_path):
    """Best-effort read of kbRoot from the per-user config; '' if absent.
    Anchored to a line that starts with 'kbRoot:' so it never matches other keys."""
    try:
        import re
        txt = config_path.read_text(encoding="utf-8")
        m = re.search(r"(?m)^\s*kbRoot:\s*(.+?)\s*$", txt)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _install_instructions(kit_root, dest_copilot, config_path):
    """Copy instructions/*.instructions.md into ~/.copilot/instructions, substituting
    __KB_ROOT__ with the live kbRoot so the always-on learning loop points at the real
    KB. If kbRoot is unknown the token is left in place and a warning is printed (the
    instruction still loads; only the concrete path is missing)."""
    src_dir = kit_root / "instructions"
    if not src_dir.is_dir():
        return
    dest_dir = dest_copilot / "instructions"
    dest_dir.mkdir(parents=True, exist_ok=True)
    kb_root = _read_kb_root(config_path)
    count = 0
    for f in sorted(src_dir.glob("*.instructions.md")):
        text = f.read_text(encoding="utf-8")
        if "__KB_ROOT__" in text:
            if kb_root:
                text = text.replace("__KB_ROOT__", kb_root)
            else:
                print(f"WARNING: kbRoot not found in config; {f.name} installed with "
                      "__KB_ROOT__ placeholder.", file=sys.stderr)
        (dest_dir / f.name).write_text(text, encoding="utf-8")
        count += 1
    if count:
        print(f"Installed {count} session instruction file(s) to {dest_dir}")


def _rotate_backup(dest_copilot, dest_agents, dest_skills):
    """Snapshot the current live agents+skills before overwriting, keep newest BACKUP_KEEP."""
    backups_root = dest_copilot / ".install-backups"
    have_agents = dest_agents.is_dir() and any(dest_agents.iterdir())
    have_skills = dest_skills.is_dir() and any(dest_skills.iterdir())
    if not (have_agents or have_skills):
        return None  # nothing live to back up yet (first install)
    backups_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap = backups_root / stamp
    snap.mkdir(parents=True, exist_ok=True)
    if have_agents:
        shutil.copytree(dest_agents, snap / "agents", dirs_exist_ok=True)
    if have_skills:
        shutil.copytree(dest_skills, snap / "skills", dirs_exist_ok=True)
    # Prune to the newest BACKUP_KEEP snapshots.
    snaps = sorted([d for d in backups_root.iterdir() if d.is_dir()])
    for old in snaps[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    print(f"Backed up live agents+skills to {snap} (keeping newest {BACKUP_KEEP}).")
    return snap


def main(argv=None):
    # Copy the kit's agents + skills into the live ~/.copilot install so the
    # Copilot CLI can discover them. Idempotent: safe to re-run after edits.
    ap = argparse.ArgumentParser(description="Install kit agents and skills into ~/.copilot.")
    ap.add_argument("--kit-root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument(
        "--hooks",
        action="store_true",
        help="Also install the guard preToolUse hooks into ~/.copilot/hooks "
             "(push-guard-hook.py + shell-guard-hook.py + kit-hooks.json). "
             "See hooks/README.md.",
    )
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip the rotating backup of the current live agents+skills.")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the post-install capability-contract check against the live install.")
    args = ap.parse_args(argv)

    kit_root = Path(args.kit_root)
    src_agents = kit_root / "agents"
    src_skills = kit_root / "skills"

    dest_copilot = Path.home() / ".copilot"
    dest_agents = dest_copilot / "agents"
    dest_skills = dest_copilot / "skills"

    # Back up the current live agents+skills BEFORE overwriting, so a bad install
    # (or a later hand-edit that fails the check) can be rolled back easily.
    if not args.no_backup:
        _rotate_backup(dest_copilot, dest_agents, dest_skills)

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

    # Install always-on session instructions (the learning loop), substituting the
    # live KB root so a plain CLI session captures lessons without extra wiring.
    _install_instructions(kit_root, dest_copilot, dest_agents / CONFIG_FILE)

    # Optionally install the guard preToolUse hooks (push-guard + shell-guard).
    # Kept opt-in because a hook fires on every shell tool call for the whole
    # user, not just this kit; see hooks/README.md for the fail-open safety design.
    if args.hooks:
        src_hooks = kit_root / "hooks"
        dest_hooks = dest_copilot / "hooks"
        dest_hooks.mkdir(parents=True, exist_ok=True)
        hook_config = src_hooks / "hooks.example.json"
        hook_scripts = ["push-guard-hook.py", "shell-guard-hook.py"]
        present = [s for s in hook_scripts if (src_hooks / s).is_file()]
        if present and hook_config.is_file():
            for s in present:
                shutil.copy2(src_hooks / s, dest_hooks / s)
            # Register under a stable name so re-running is idempotent, wiring
            # PUSH_GUARD_CONFIG to the live per-repo config so each repo's
            # configured baseBranch is protected (not just main/master).
            resolved_cfg = _write_hook_config(
                hook_config, dest_hooks / "kit-hooks.json", dest_agents / CONFIG_FILE
            )
            # Remove the legacy single-purpose file so hooks aren't double-registered.
            legacy = dest_hooks / "push-guard.json"
            if legacy.is_file():
                legacy.unlink()
            print(f"Installed guard hooks ({', '.join(present)}) to {dest_hooks} "
                  "(restart the CLI so hooks reload).")
            if resolved_cfg:
                print(f"  push-guard base-branch config wired: {resolved_cfg}")
            else:
                print("  push-guard base-branch config NOT wired (no per-repo config found); "
                      "main/master still protected.")
        else:
            print("WARNING: hooks/ files not found; skipped hook install.", file=sys.stderr)

    print(f"Installed agents and skills to {dest_copilot}")

    # Ship the portable capability-contract check INTO the live install so the drift
    # insurance travels with the pack, then run it against what we just installed.
    check_src = kit_root / "scripts" / "capability_check.py"
    if check_src.is_file():
        dest_scripts = dest_copilot / "scripts"
        dest_scripts.mkdir(parents=True, exist_ok=True)
        dest_check = dest_scripts / "capability_check.py"
        shutil.copy2(check_src, dest_check)
        print(f"Installed capability check to {dest_check}")
        if not args.no_verify:
            rc = subprocess.run(
                [sys.executable, str(dest_check),
                 "--agents-dir", str(dest_agents), "--skills-dir", str(dest_skills)]
            ).returncode
            if rc != 0:
                print("WARNING: post-install capability check FAILED against the live install. "
                      "Roll back from ~/.copilot/.install-backups/<latest> if needed.",
                      file=sys.stderr)
                return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
