#!/usr/bin/env python3
"""Human-readable rollup of the agent metrics log (closes the cost feedback loop).

Reads the JSONL metrics log written by the delivery-metrics-capture skill and prints a
weekly-style summary so advisories are actually reviewed rather than logged and forgotten:
  - totals + tokens/task
  - by agent (task count, tokens, avg tokens/task)
  - by tier
  - by day
  - budget check against outputs.budgets in the config
Read-only; never mutates the log or config. Stdlib only; cross-platform.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def parse_ts(value):
    # Best-effort timestamp parser: metrics rows may store ISO-8601 (with a "Z"
    # UTC suffix) or a few looser formats. Returns a datetime, or None if unparseable.
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Fall back to a handful of common explicit formats before giving up.
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value).strip()[:len("2020-01-01T00:00:00")], fmt)
            except ValueError:
                continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rollup of the agent metrics log.")
    ap.add_argument("--config", default=str(Path.home() / ".copilot" / "agents" / "agents.config.yaml"))
    ap.add_argument("--metrics-log", default=None, help="Explicit JSONL log (overrides config)")
    ap.add_argument("--days", type=int, default=7, help="Rolling window in days")
    args = ap.parse_args(argv)

    rolling_budget = 4000000
    metrics_log = args.metrics_log
    cfg_path = Path(args.config)
    if cfg_path.is_file():
        cfg = cfg_path.read_text(encoding="utf-8")
        if not metrics_log:
            m = re.search(r"(?m)^\s*metricsLog:\s*(.+?)\s*$", cfg)
            if m:
                metrics_log = m.group(1).strip()
        rb = re.search(r"(?m)^\s*rollingDailyTokenBudget:\s*(\d+)", cfg)
        if rb:
            rolling_budget = int(rb.group(1))

    if not metrics_log or not Path(metrics_log).is_file():
        print(f"No metrics log found ({metrics_log}). Nothing to report yet.")
        return 0

    since = datetime.now() - timedelta(days=args.days)
    rows = []
    # Read the JSONL log line by line, keeping only well-formed rows inside the
    # rolling window. Malformed lines are skipped rather than aborting the report.
    for line in Path(metrics_log).read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_ts(o.get("ts"))
        if ts is not None:
            ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
            if ts_naive < since:
                continue
        try:
            tok = int(o.get("tokens", 0))
        except (TypeError, ValueError):
            tok = 0
        rows.append({
            "day": ts.strftime("%Y-%m-%d") if ts else "unknown",
            "agent": str(o.get("agent") or "unknown"),
            "tier": str(o.get("tier") or "unknown"),
            "tokens": tok,
        })

    if not rows:
        print(f"No metrics in the last {args.days} day(s).")
        return 0

    total_tasks = len(rows)
    total_tokens = sum(r["tokens"] for r in rows)
    print(f"=== Metrics rollup (last {args.days} days) ===")
    print(f"tasks: {total_tasks}   tokens: {total_tokens:,}   tokens/task: {round(total_tokens/total_tasks):,}")

    def group(key):
        # Aggregate task count and token totals keyed by the given row field
        # (used for the by-agent, by-tier, and by-day breakdowns below).
        g = defaultdict(lambda: {"count": 0, "tokens": 0})
        for r in rows:
            g[r[key]]["count"] += 1
            g[r[key]]["tokens"] += r["tokens"]
        return g

    print("\n-- by agent --")
    by_agent = group("agent")
    for name in sorted(by_agent, key=lambda n: by_agent[n]["tokens"], reverse=True):
        v = by_agent[name]
        avg = round(v["tokens"] / v["count"])
        print(f"{name:<32} tasks={v['count']:<4} tokens={v['tokens']:>12,} avg={avg:>10,}")

    print("\n-- by tier --")
    by_tier = group("tier")
    for name in sorted(by_tier):
        v = by_tier[name]
        print(f"{name:<12} tasks={v['count']:<4} tokens={v['tokens']:>12,}")

    print("\n-- by day --")
    by_day = group("day")
    for name in sorted(by_day):
        v = by_day[name]
        flag = "  <-- over daily budget" if v["tokens"] >= rolling_budget else ""
        print(f"{name}  tasks={v['count']:<4} tokens={v['tokens']:>12,}{flag}")

    print(f"\n(advisory only - rollups inform; nothing is blocked. Daily budget: {rolling_budget:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
