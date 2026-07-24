#!/usr/bin/env python3
"""Apply declarative per-agent model routing to the Copilot CLI store.

Reads the ``models:`` section of agents.config.yaml (tiers + agent->tier map +
exempt list) and writes it into ~/.copilot/settings.json under subagents.agents.<key>,
where <key> is the agent name (== file basename without .agent.md). This is the
platform-native mechanism Copilot CLI uses for per-agent model binding; markdown
frontmatter is NOT used for model selection.

  - Sets model + effortLevel + contextTier for every mapped agent.
  - Leaves exempt agents (personal / non-rollout) untouched.
  - Prunes obviously stale entries (keys containing ' Copy' or '.agent').
  - Reports (but never deletes) other unmanaged keys.
  - Idempotent. Use --dry-run to preview without writing.

Exit codes: 0 = ok, 4 = config error. Stdlib only; cross-platform.
"""
import argparse
import json
import re
import sys
from pathlib import Path


def default(*parts):
    # Build an absolute path under the current user's ~/.copilot directory,
    # so defaults work cross-platform without hardcoding a home path.
    return str(Path.home() / ".copilot" / Path(*parts))


def main(argv=None):
    # CLI surface: every input path is overridable so the script is testable
    # against temp fixtures (see tests/test_apply_model_routing.py).
    ap = argparse.ArgumentParser(description="Apply per-agent model routing to the Copilot store.")
    ap.add_argument("--config", default=default("agents", "agents.config.yaml"),
                    help="Path to agents.config.yaml")
    ap.add_argument("--settings", default=default("settings.json"),
                    help="Path to Copilot settings.json")
    ap.add_argument("--agents-dir", default=default("agents"),
                    help="Directory of live *.agent.md files (used to detect orphaned keys)")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 4
    cfg = cfg_path.read_text(encoding="utf-8")

    # The kit is stdlib-only (no PyYAML dependency), so the small, fixed subset
    # of YAML we need is parsed with targeted regexes rather than a full parser.
    # --- Parse models.tiers (model + effortLevel per tier) ---
    tiers = {}
    for m in re.finditer(
        r"(?m)^\s{4}(premium|standard):\s*\{\s*model:\s*([^,}\s]+)\s*,\s*effortLevel:\s*([^,}\s]+)\s*\}",
        cfg):
        tiers[m.group(1)] = {"model": m.group(2).strip(), "effortLevel": m.group(3).strip()}
    if not tiers:
        print("ERROR: no models.tiers found in config", file=sys.stderr)
        return 4

    # --- Parse models.agents (agent -> tier), scoped to the models: block ---
    mb = re.search(r"(?ms)^models:\s*.*?(?=^\S)", cfg)
    models_block = mb.group(0) if mb else ""
    if not models_block:
        mb = re.search(r"(?ms)^models:\s*.*", cfg)
        models_block = mb.group(0) if mb else ""
    agent_tier = {}
    for m in re.finditer(r"(?m)^\s{4}([a-z0-9][a-z0-9-]+):\s*(premium|standard)\s*$", models_block):
        agent_tier[m.group(1)] = m.group(2)
    if not agent_tier:
        print("ERROR: no models.agents map found in config", file=sys.stderr)
        return 4

    # --- Parse exempt list ---
    exempt = []
    em = re.search(r"(?m)^\s{2}exempt:\s*\[([^\]]*)\]", models_block)
    if em:
        exempt = [x.strip() for x in em.group(1).split(",") if x.strip()]

    # --- Load or seed settings.json ---
    settings_path = Path(args.settings)
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            settings = {}
    else:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    sub = settings.setdefault("subagents", {})
    if not isinstance(sub, dict):
        sub = settings["subagents"] = {}
    agents_obj = sub.setdefault("agents", {})
    if not isinstance(agents_obj, dict):
        agents_obj = sub["agents"] = {}

    applied, pruned, unmanaged = [], [], []

    # Write the resolved model/effort/contextTier for every agent named in the
    # config's tier map. This is the core action of the script.
    # Apply mapped agents
    for agent, tier in agent_tier.items():
        t = tiers.get(tier)
        if not t:
            continue
        agents_obj[agent] = {"model": t["model"], "effortLevel": t["effortLevel"], "contextTier": "default"}
        applied.append(f"{agent} -> {tier} ({t['model']}/{t['effortLevel']})")

    # Housekeeping: remove entries that are clearly stale (editor " Copy"
    # duplicates, ".agent" suffixes) or orphaned (no matching live *.agent.md).
    # Anything else unrecognised is reported but never deleted, to stay safe.
    # Prune stale junk + orphaned keys; report other unmanaged keys
    agents_dir = Path(args.agents_dir)
    live_names = []
    if agents_dir.is_dir():
        live_names = [p.name[:-len(".agent.md")] for p in agents_dir.glob("*.agent.md")]
    for key in list(agents_obj.keys()):
        if key in agent_tier or key in exempt:
            continue
        is_junk = (" Copy" in key) or (".agent" in key)
        is_orphan = bool(live_names) and key not in live_names
        if is_junk or is_orphan:
            del agents_obj[key]
            pruned.append(key)
        else:
            unmanaged.append(key)

    verb = "WOULD APPLY" if args.dry_run else "APPLIED"
    print(f"{verb}: {len(applied)} agent(s)")
    for a in applied:
        print(f"  + {a}")
    if pruned:
        print("PRUNED stale keys:")
        for p in pruned:
            print(f"  - {p}")
    if unmanaged:
        print("UNMANAGED (left as-is; add to config models.agents or models.exempt):")
        for u in unmanaged:
            print(f"  ? {u}")

    if args.dry_run:
        print("\n(dry run - settings.json not written)")
        return 0

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {settings_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
