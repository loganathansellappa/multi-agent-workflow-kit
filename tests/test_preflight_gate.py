#!/usr/bin/env python3
"""Tests for skills/agent-preflight-check/preflight_gate.py."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "skills" / "agent-preflight-check" / "preflight_gate.py"


class PreflightGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-"))
        self.metrics = self.tmp / "metrics.jsonl"
        self.config = self.tmp / "config.yaml"
        self.config.write_text(f"""\
outputs:
  metricsLog: {self.metrics}
  budgets:
    perTaskTokenCeiling: 150000
    rollingDailyTokenBudget: 4000000

models:
  tiers:
    premium: {{ model: claude-opus-4.8, effortLevel: high }}
    standard: {{ model: claude-sonnet-5, effortLevel: medium }}
  agents:
    backend-developer: premium
    frontend-developer: standard
    code-reviewer: standard
  exempt: [my-personal-agent]

integrations:
  baseUrl: x
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gate(self, agent, model, tier="standard", est=0, config=None):
        # Run the gate as a subprocess; return (exit_code, combined_output).
        cfg = config or str(self.config)
        r = subprocess.run(
            [sys.executable, str(GATE), "--config", cfg, "--agent", agent, "--model", model,
             "--tier", tier, "--estimated-tokens", str(est)],
            capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr)

    def test_exits_4_when_config_missing(self):
        # Missing config is a usage error -> exit 4.
        code, _ = self.gate("x", "y", config=str(self.tmp / "missing.yaml"))
        self.assertEqual(code, 4)

    def test_never_blocks_standard_on_premium(self):
        # A standard-tier agent on a premium model is advised, never blocked (exit 0).
        code, text = self.gate("code-reviewer", "claude-opus-4.8")
        self.assertEqual(code, 0)
        self.assertIn("standard-tier role", text)

    def test_flags_under_tiered(self):
        # A high-value complex task on a cheap model gets an "under-tiered" hint.
        code, text = self.gate("backend-developer", "claude-sonnet-5", tier="complex")
        self.assertEqual(code, 0)
        self.assertIn("under-tiered", text)

    def test_exempts_personal_agents(self):
        # Exempt/personal agents are never governed, even on a premium model.
        code, text = self.gate("my-personal-agent", "claude-opus-4.8")
        self.assertEqual(code, 0)
        self.assertIn("model: ok", text)

    def test_premium_on_premium_is_clean(self):
        # A premium-tier agent on a premium model is correctly routed (no hint).
        code, text = self.gate("backend-developer", "claude-opus-4.8", tier="complex")
        self.assertEqual(code, 0)
        self.assertIn("model: ok", text)

    def test_advises_when_budget_exceeded(self):
        # When today's logged tokens exceed the daily budget, a budget advisory prints.
        today = date.today().strftime("%Y-%m-%d")
        self.metrics.write_text(f'{{"ts":"{today}T00:00:00Z","agent":"x","tokens":5000000}}\n')
        code, text = self.gate("frontend-developer", "claude-sonnet-5")
        self.assertEqual(code, 0)
        self.assertIn("budget: over", text)


if __name__ == "__main__":
    unittest.main()
