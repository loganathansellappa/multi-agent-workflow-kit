#!/usr/bin/env python3
"""Tests for scripts/apply_model_routing.py."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPLY = ROOT / "scripts" / "apply_model_routing.py"

# Minimal config fixture: two tiers, three mapped agents, one exempt agent.
CONFIG = """\
models:
  tiers:
    premium: { model: claude-opus-4.8, effortLevel: high }
    standard: { model: claude-sonnet-5, effortLevel: medium }
  agents:
    backend-developer: premium
    frontend-developer: standard
    code-reviewer: standard
  exempt: [my-personal-agent]

integrations:
  baseUrl: x
"""


def run(*args):
    # Invoke the script as a subprocess so we exercise the real CLI entry point.
    return subprocess.run([sys.executable, str(APPLY), *args], capture_output=True, text=True)


class ApplyModelRouting(unittest.TestCase):
    def setUp(self):
        # Build a throwaway kit (agents dir + config) in a temp folder per test.
        self.tmp = Path(tempfile.mkdtemp(prefix="apply-"))
        self.agents_dir = self.tmp / "agents"
        self.agents_dir.mkdir()
        for n in ("backend-developer", "frontend-developer", "code-reviewer", "my-personal-agent"):
            (self.agents_dir / f"{n}.agent.md").write_text(f"---\nname: {n}\n---\nbody")
        self.config = self.tmp / "config.yaml"
        self.config.write_text(CONFIG)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_settings(self, obj):
        # Helper: write a fresh settings.json fixture and return its path.
        p = self.tmp / f"settings-{len(list(self.tmp.glob('settings-*')))}.json"
        p.write_text(json.dumps(obj))
        return p

    def test_maps_tiers_and_writes(self):
        # Each mapped agent gets the model + effortLevel of its assigned tier.
        s = self.new_settings({"model": "claude-opus-4.8"})
        r = run("--config", str(self.config), "--settings", str(s), "--agents-dir", str(self.agents_dir))
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(s.read_text())
        self.assertEqual(o["subagents"]["agents"]["backend-developer"]["model"], "claude-opus-4.8")
        self.assertEqual(o["subagents"]["agents"]["backend-developer"]["effortLevel"], "high")
        self.assertEqual(o["subagents"]["agents"]["frontend-developer"]["model"], "claude-sonnet-5")
        self.assertEqual(o["subagents"]["agents"]["code-reviewer"]["model"], "claude-sonnet-5")

    def test_prunes_junk_and_orphans(self):
        # Stale " Copy" duplicates and agents with no live file are removed.
        s = self.new_settings({"subagents": {"agents": {
            "code-reviewer.agent - Copy": {"model": "x"}, "ghost-agent": {"model": "y"}}}})
        run("--config", str(self.config), "--settings", str(s), "--agents-dir", str(self.agents_dir))
        o = json.loads(s.read_text())
        self.assertNotIn("code-reviewer.agent - Copy", o["subagents"]["agents"])
        self.assertNotIn("ghost-agent", o["subagents"]["agents"])

    def test_does_not_touch_exempt(self):
        # An agent on the exempt list keeps its existing model untouched.
        s = self.new_settings({"subagents": {"agents": {"my-personal-agent": {"model": "auto"}}}})
        run("--config", str(self.config), "--settings", str(s), "--agents-dir", str(self.agents_dir))
        o = json.loads(s.read_text())
        self.assertEqual(o["subagents"]["agents"]["my-personal-agent"]["model"], "auto")

    def test_dry_run_does_not_write(self):
        # --dry-run must leave settings.json byte-for-byte unchanged.
        s = self.new_settings({"x": 1})
        before = s.read_text()
        run("--config", str(self.config), "--settings", str(s), "--agents-dir", str(self.agents_dir), "--dry-run")
        self.assertEqual(s.read_text(), before)

    def test_exits_4_on_missing_config(self):
        # A missing config path is a usage error -> exit code 4.
        s = self.new_settings({})
        r = run("--config", str(self.tmp / "nope.yaml"), "--settings", str(s), "--agents-dir", str(self.agents_dir))
        self.assertEqual(r.returncode, 4)


if __name__ == "__main__":
    unittest.main()
