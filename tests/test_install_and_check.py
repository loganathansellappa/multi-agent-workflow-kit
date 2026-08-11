"""Tests for the portable capability check and the installer's rotating backup.

These cover the two behaviors that make the harness "travel with the pack":
  - capability_check works on the FLATTENED live layout (all agents in one dir), not
    just the kit's agents/<role>/ tree, and catches privilege drift + a dropped cap.
  - install_to_copilot keeps only the newest N backups of live agents+skills.

Stdlib only; discovered by run_tests.py.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capability_check as cc          # noqa: E402
import install_to_copilot as inst      # noqa: E402

REV = "name: sample-reviewer\ntools: ['execute', 'read', 'search', 'skill', 'ask_user']\n"
DEV = "name: sample-developer\ntools: ['execute', 'read', 'search', 'edit', 'task', 'skill', 'ask_user']\n"


def _write(d, fname, text):
    (d / fname).write_text(text, encoding="utf-8")


class TestPortableCheckFlattened(unittest.TestCase):
    def _flat(self, tmp):
        # Simulate the flattened ~/.copilot/agents layout (no role subdirs).
        _write(tmp, "sample-reviewer.agent.md", REV)
        _write(tmp, "sample-developer.agent.md", DEV)

    def test_clean_flat_layout_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._flat(tmp)
            violations, n, n_rev = cc.check_agents(tmp)
            self.assertEqual(violations, [])
            self.assertEqual(n, 2)
            self.assertEqual(n_rev, 1, "reviewer must be detected by name in a flat layout")

    def test_reviewer_with_edit_flagged_when_flat(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write(tmp, "sample-reviewer.agent.md",
                   "name: sample-reviewer\ntools: ['execute', 'read', 'edit', 'skill']\n")
            violations, _, _ = cc.check_agents(tmp)
            self.assertTrue(any("mutation" in v for v in violations))

    def test_reviewer_with_task_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write(tmp, "sample-reviewer.agent.md",
                   "name: sample-reviewer\ntools: ['execute', 'read', 'task', 'skill']\n")
            violations, _, _ = cc.check_agents(tmp)
            self.assertTrue(any("delegation" in v for v in violations))

    def test_meta_reviewer_may_delegate(self):
        import tempfile
        if not cc.META_REVIEWERS:
            self.skipTest("this kit defines no meta-reviewer")
        meta = sorted(cc.META_REVIEWERS)[0]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write(tmp, f"{meta}.agent.md",
                   f"name: {meta}\ntools: ['execute', 'read', 'task', 'skill']\n")
            violations, _, _ = cc.check_agents(tmp)
            self.assertEqual(violations, [], "meta-reviewer is allowed to hold task")

    def test_developer_missing_edit_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write(tmp, "sample-developer.agent.md",
                   "name: sample-developer\ntools: ['execute', 'read', 'task', 'skill']\n")
            violations, _, _ = cc.check_agents(tmp)
            self.assertTrue(any("missing required tool 'edit'" in v for v in violations))


class TestLoopCapCheck(unittest.TestCase):
    def test_detects_cap(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            skills = Path(t)
            (skills / "quality-loop-harness").mkdir()
            (skills / "quality-loop-harness" / "SKILL.md").write_text(
                "Loop bounds: a cap of 3 review/fix cycles then hand off BLOCKED.", encoding="utf-8")
            self.assertEqual(cc.check_loop_cap(skills), [])

    def test_missing_cap_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            skills = Path(t)
            (skills / "quality-loop-harness").mkdir()
            (skills / "quality-loop-harness" / "SKILL.md").write_text(
                "Repeat the loop until everything is clean.", encoding="utf-8")
            self.assertTrue(cc.check_loop_cap(skills))


class TestBackupRotation(unittest.TestCase):
    def test_keeps_newest_three(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            copilot = Path(t)
            agents = copilot / "agents"
            skills = copilot / "skills"
            agents.mkdir()
            skills.mkdir()
            _write(agents, "x.agent.md", REV)  # live has content -> backup runs
            backups = copilot / ".install-backups"
            backups.mkdir()
            # Pre-seed 5 older snapshots (lexicographically < the new timestamp).
            for i in range(1, 6):
                (backups / f"20200101-00000{i}").mkdir()
            inst._rotate_backup(copilot, agents, skills)
            remaining = sorted(d.name for d in backups.iterdir() if d.is_dir())
            self.assertEqual(len(remaining), inst.BACKUP_KEEP)
            # The two newest pre-seeded + the freshly created one survive.
            self.assertIn("20200101-000005", remaining)
            self.assertIn("20200101-000004", remaining)
            self.assertNotIn("20200101-000001", remaining)

    def test_no_backup_on_first_install(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            copilot = Path(t)
            agents = copilot / "agents"
            skills = copilot / "skills"
            agents.mkdir()
            skills.mkdir()  # both empty -> nothing to back up
            self.assertIsNone(inst._rotate_backup(copilot, agents, skills))
            self.assertFalse((copilot / ".install-backups").exists())


class TestHookConfigWiring(unittest.TestCase):
    """_write_hook_config must wire PUSH_GUARD_CONFIG to the live per-repo config
    when it exists, and leave it blank (never crash) when it doesn't — so the guard
    protects each repo's configured baseBranch, not just the hard-coded main/master."""

    HOOK_JSON = (
        '{"version":1,"hooks":{"preToolUse":['
        '{"matcher":"bash","type":"command","bash":"x","env":{"PUSH_GUARD_CONFIG":""}},'
        '{"matcher":"bash","type":"command","bash":"y"}'
        ']}}'
    )

    def _src(self, tmp):
        src = tmp / "hooks.example.json"
        src.write_text(self.HOOK_JSON, encoding="utf-8")
        return src

    def test_wires_config_path_when_present(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            src = self._src(tmp)
            cfg = tmp / inst.CONFIG_FILE
            cfg.write_text("services: {}\n", encoding="utf-8")
            dest = tmp / "kit-hooks.json"
            resolved = inst._write_hook_config(src, dest, cfg)
            self.assertEqual(resolved, str(cfg))
            data = json.loads(dest.read_text(encoding="utf-8"))
            env = data["hooks"]["preToolUse"][0]["env"]
            self.assertEqual(env["PUSH_GUARD_CONFIG"], str(cfg))
            # An entry without an env block is left untouched.
            self.assertNotIn("env", data["hooks"]["preToolUse"][1])

    def test_blank_when_config_absent(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            src = self._src(tmp)
            dest = tmp / "kit-hooks.json"
            resolved = inst._write_hook_config(src, dest, tmp / "does-not-exist.yaml")
            self.assertEqual(resolved, "")
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["preToolUse"][0]["env"]["PUSH_GUARD_CONFIG"], "")

    def test_malformed_source_falls_back_to_copy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            src = tmp / "hooks.example.json"
            src.write_text("{not valid json", encoding="utf-8")
            dest = tmp / "kit-hooks.json"
            resolved = inst._write_hook_config(src, dest, tmp / "cfg.yaml")
            self.assertEqual(resolved, "")
            self.assertTrue(dest.is_file())  # verbatim copy, install never blocked


class TestInstructionsInstall(unittest.TestCase):
    """_read_kb_root must read kbRoot; _install_instructions must substitute
    __KB_ROOT__ with the live KB root and copy into ~/.copilot/instructions."""

    def test_read_kb_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t) / "c.yaml"
            cfg.write_text("kbRoot: X:\\kb\\prod\n", encoding="utf-8")
            self.assertEqual(inst._read_kb_root(cfg), "X:\\kb\\prod")

    def test_read_kb_root_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t) / "c.yaml"
            cfg.write_text("other: 1\n", encoding="utf-8")
            self.assertEqual(inst._read_kb_root(cfg), "")

    def test_install_substitutes_kb_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            kit = Path(t) / "kit"
            (kit / "instructions").mkdir(parents=True)
            (kit / "instructions" / "learning-loop.instructions.md").write_text(
                "--kb-root __KB_ROOT__\n", encoding="utf-8")
            copilot = Path(t) / "copilot"
            copilot.mkdir()
            cfg = copilot / inst.CONFIG_FILE
            cfg.write_text("kbRoot: Y:\\kb\n", encoding="utf-8")
            inst._install_instructions(kit, copilot, cfg)
            out = (copilot / "instructions" / "learning-loop.instructions.md").read_text(encoding="utf-8")
            self.assertIn("Y:\\kb", out)
            self.assertNotIn("__KB_ROOT__", out)

    def test_install_leaves_token_when_kb_root_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            kit = Path(t) / "kit"
            (kit / "instructions").mkdir(parents=True)
            (kit / "instructions" / "learning-loop.instructions.md").write_text(
                "--kb-root __KB_ROOT__\n", encoding="utf-8")
            copilot = Path(t) / "copilot"
            copilot.mkdir()
            cfg = copilot / inst.CONFIG_FILE  # not created -> kbRoot unknown
            inst._install_instructions(kit, copilot, cfg)
            out = (copilot / "instructions" / "learning-loop.instructions.md").read_text(encoding="utf-8")
            self.assertIn("__KB_ROOT__", out)

    def test_install_noop_when_instructions_dir_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            kit = Path(t) / "kit"
            kit.mkdir()  # no instructions/ subdir
            copilot = Path(t) / "copilot"
            copilot.mkdir()
            inst._install_instructions(kit, copilot, copilot / inst.CONFIG_FILE)
            self.assertFalse((copilot / "instructions").exists())

    def test_install_is_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            kit = Path(t) / "kit"
            (kit / "instructions").mkdir(parents=True)
            (kit / "instructions" / "learning-loop.instructions.md").write_text(
                "--kb-root __KB_ROOT__\n", encoding="utf-8")
            copilot = Path(t) / "copilot"
            copilot.mkdir()
            cfg = copilot / inst.CONFIG_FILE
            cfg.write_text("kbRoot: Y:\\kb\n", encoding="utf-8")
            dest = copilot / "instructions" / "learning-loop.instructions.md"
            inst._install_instructions(kit, copilot, cfg)
            first = dest.read_text(encoding="utf-8")
            inst._install_instructions(kit, copilot, cfg)
            self.assertEqual(dest.read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
