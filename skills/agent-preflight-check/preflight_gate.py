#!/usr/bin/env python3
"""Advisory cost-governance gate for Copilot custom agents.

Deterministic (non-LLM) *advisory* check of the two cost levers. It never hard-stops the
developer's work - it only informs so the human/agent can make an informed choice:
  1. Model-routing hint - flags a standard-tier role running on a premium (Opus-class)
     model, which wastes ~5x with no quality gain.
  2. Budget hint        - flags when today's rolling token spend has passed the configured
     daily budget, or this task is projected past the per-task ceiling.

The agent-preflight-check skill runs this FIRST and reads the advisories, but is NOT
blocked by them. Exit codes:
  0 = ran ok (may include advisories) -> proceed
  4 = usage/config error              -> fix invocation/config (setup bug, not governance)

Personal, non-rollout agents (usage-cost, performance-*) are intentionally NOT governed.
--override silences the governance advisories entirely. Stdlib only; cross-platform.
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

LEARN_STALE_DAYS = 7  # a self-learning loop with no capture in this many days looks skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description="Advisory cost-governance gate.")
    ap.add_argument("--config", required=True, help="Path to agents.config.yaml")
    ap.add_argument("--agent", required=True, help="This agent's name")
    ap.add_argument("--model", required=True, help="Active model id")
    ap.add_argument("--tier", choices=["trivial", "standard", "complex"], default="standard")
    ap.add_argument("--estimated-tokens", type=int, default=0)
    ap.add_argument("--override", action="store_true", help="Silence advisories")
    args = ap.parse_args(argv)

    advisories = []

    def advise(msg):
        # Queue an advisory message unless the caller passed --override to mute them.
        if not args.override:
            advisories.append(msg)

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 4
    cfg = cfg_path.read_text(encoding="utf-8")

    # Tiny stdlib-only readers for single scalar/int values out of the YAML config
    # (the kit avoids a PyYAML dependency).
    def read_val(pattern):
        m = re.search(pattern, cfg)
        return m.group(1).strip() if m else None

    def read_int(pattern):
        m = re.search(pattern, cfg)
        try:
            return int(m.group(1)) if m else 0
        except (TypeError, ValueError):
            return 0

    # Pull the budget levers from config, falling back to sane defaults if absent.
    metrics_log = read_val(r"(?m)^\s*metricsLog:\s*(.+?)\s*$")
    per_task_ceiling = read_int(r"(?m)^\s*perTaskTokenCeiling:\s*(\d+)")
    rolling_budget = read_int(r"(?m)^\s*rollingDailyTokenBudget:\s*(\d+)")
    if per_task_ceiling <= 0:
        per_task_ceiling = 150000
    if rolling_budget <= 0:
        rolling_budget = 4000000

    # --- Model tier detection ---
    model_lc = args.model.lower()
    is_premium = "opus" in model_lc
    is_cheap = bool(re.search(r"sonnet|haiku|mini|flash", model_lc))

    # --- Agent role classification (driven by config models.agents map, not name regex) ---
    a = args.agent.lower()
    mb = re.search(r"(?ms)^models:\s*.*?(?=^\S)", cfg)
    models_block = mb.group(0) if mb else ""
    if not models_block:
        mb = re.search(r"(?ms)^models:\s*.*", cfg)
        models_block = mb.group(0) if mb else ""
    mapped_tier = None
    em = re.search(r"(?m)^\s{4}" + re.escape(a) + r":\s*(premium|standard)\s*$", models_block)
    if em:
        mapped_tier = em.group(1)
    exempt_list = []
    exempt_match = re.search(r"(?m)^\s{2}exempt:\s*\[([^\]]*)\]", models_block)
    if exempt_match:
        exempt_list = [x.strip() for x in exempt_match.group(1).split(",")]
    # Personal / non-rollout agents are exempt (config exempt list or usage/performance names).
    is_personal = (a in exempt_list) or bool(re.search(r"usage|performance", a))
    if mapped_tier:
        pure_cheap_role = (not is_personal) and (mapped_tier == "standard")
        high_default_role = (mapped_tier == "premium")
    else:
        # Fallback when config has no map for this agent: heuristic (kept for resilience).
        pure_cheap_role = ((not is_personal)
                           and bool(re.search(r"review|reviewer|orchestrat", a))
                           and not re.search(r"developer", a))
        high_default_role = a in ("backend-developer", "native-client-developer", "e2e")

    # --- 1. Model-routing hint (advisory only) ---
    model_guard = "ok"
    if pure_cheap_role and is_premium:
        model_guard = "mismatch"
        advise(f"model: standard-tier role '{args.agent}' is on premium model '{args.model}' "
               f"(~5x cost, no quality gain). Consider /model or /subagents to a Sonnet/Haiku-class model.")
    elif high_default_role and is_cheap and args.tier == "complex":
        model_guard = "under-tiered"
        advise(f"model: high-value complex task on '{args.agent}' is on cheap model '{args.model}' "
               f"- consider escalating to Opus for quality.")

    # --- 2. Budget hint (advisory only) ---
    budget = "ok"
    today_tokens = 0
    # Sum today's token usage from the metrics log to compare against the daily budget.
    if metrics_log and Path(metrics_log).is_file():
        today = date.today().strftime("%Y-%m-%d")
        for line in Path(metrics_log).read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.search(r'"ts"\s*:\s*"' + re.escape(today), line):
                tm = re.search(r'"tokens"\s*:\s*(\d+)', line)
                if tm:
                    today_tokens += int(tm.group(1))
    est = args.estimated_tokens
    if today_tokens >= rolling_budget:
        budget = "over(rolling)"
        advise(f"budget: today={today_tokens} has passed daily budget={rolling_budget}. "
               f"Enough for today - consider reducing scope or deferring non-urgent work.")
    elif est > 0 and est >= per_task_ceiling:
        budget = "near(task-ceiling)"
        advise(f"budget: estimated task tokens {est} >= per-task ceiling {per_task_ceiling} "
               f"- consider tightening scope.")
    elif (today_tokens + est) >= rolling_budget:
        budget = "near(rolling)"
        advise(f"budget: today+estimate ({today_tokens + est}) approaches daily budget {rolling_budget}.")

    # --- 3. Learning-ledger hint (advisory only): is the self-learning loop alive? ---
    # Flags when learning-capture has never run (no log) or has gone stale, so a
    # skipped LEARN step is impossible to miss at the next task start (same
    # retrospective-visibility model as the budget check above).
    learn = "ok"
    # NOTE: assumes exactly one `kbRoot:` key in the config file (internal: e2e.kbRoot;
    # public: learning.kbRoot). This reader is intentionally not section-scoped to stay
    # consistent with read_val's style; do not add a second kbRoot: under another section.
    kb_root = read_val(r"(?m)^\s*kbRoot:\s*(.+?)\s*$")
    if not kb_root:
        learn = "unconfigured"
    else:
        lessons_log = Path(kb_root) / "lessons-log.jsonl"
        if not lessons_log.is_file():
            learn = "no-log"
            advise("learn: no lessons-log.jsonl under kbRoot - the learning-capture step has "
                   "never run. Run skill learning-capture at every handoff (a real --lesson or "
                   "--none); otherwise lessons evaporate.")
        else:
            latest = None
            for line in lessons_log.read_text(encoding="utf-8", errors="ignore").splitlines():
                dm = re.search(r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"', line)
                if dm and (latest is None or dm.group(1) > latest):
                    latest = dm.group(1)
            if latest is None:
                learn = "empty"
                advise("learn: lessons-log.jsonl exists but has no dated entries - capture is not "
                       "writing lessons. Check skill learning-capture.")
            else:
                try:
                    age = (date.today() - date.fromisoformat(latest)).days
                except ValueError:
                    age = 0
                if age > LEARN_STALE_DAYS:
                    learn = f"stale({age}d)"
                    advise(f"learn: last captured lesson was {age} days ago (>{LEARN_STALE_DAYS}d) - "
                           f"the self-learning loop looks skipped. Run learning-capture at handoff.")

    # --- Result (always exit 0; advisory only) ---
    print(f"gate: advisory | tier: {args.tier} | model: {model_guard} | budget: {budget} "
          f"| rolling: {today_tokens}/{rolling_budget} | learn: {learn}")
    for msg in advisories:
        print(f"  info: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
