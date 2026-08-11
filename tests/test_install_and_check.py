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


if __name__ == "__main__":
    unittest.main()
