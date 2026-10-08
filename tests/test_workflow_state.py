#!/usr/bin/env python3
"""Offline unit tests for the workflow-state skill and its push-guard integration.

Covers:
  * skills/workflow-state/workflow_state.py  (persist + drift + validate + enums)
  * hooks/push-guard-hook.py                 (soft gate-status block)
No network. Each test builds a throwaway git repo in a tempdir.
"""
import json
import os
import subprocess
import sys
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WFS = ROOT / "skills" / "workflow-state" / "workflow_state.py"
HOOK = ROOT / "hooks" / "push-guard-hook.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("push_guard_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def make_repo(tmp):
    repo = Path(tmp)
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def wfs(repo, *args, env=None):
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    proc = subprocess.run([sys.executable, str(WFS), "--repo", str(repo), *args],
                          capture_output=True, text=True, env=run_env)
    return proc.returncode, proc.stdout


def run_hook(cmd, cwd):
    payload = {"toolName": "powershell", "toolArgs": {"command": cmd}, "cwd": str(cwd)}
    proc = subprocess.run([sys.executable, str(HOOK)],
                          input=json.dumps(payload), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def denied(cmd, cwd):
    return run_hook(cmd, cwd).get("permissionDecision") == "deny"


class WorkflowStateCore(unittest.TestCase):
    def test_plan_gate_status_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            self.assertEqual(wfs(repo, "set-plan", "--files", "a.txt", "b.txt")[0], 0)
            self.assertEqual(wfs(repo, "set-gate", "--status", "PASS")[0], 0)
            rc, out = wfs(repo, "gate-status")
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "PASS")

    def test_drift_ok_and_block(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "set-plan", "--files", "a.txt", "b.txt")
            (repo / "a.txt").write_text("2", encoding="utf-8")  # planned file
            self.assertEqual(wfs(repo, "drift")[0], 0)
            (repo / "c.txt").write_text("x", encoding="utf-8")  # unplanned
            self.assertEqual(wfs(repo, "drift")[0], 3)

    def test_governance_check_is_warn_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            rc, out = wfs(repo, "governance-check", "--files", "foo.agent.md", "a.txt")
            self.assertEqual(rc, 0)               # never blocks
            self.assertIn("foo.agent.md", out)
            self.assertIn("WARN", out)

    def test_validate_flags_stale_gate_after_commit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "set-gate", "--status", "PASS")
            self.assertEqual(wfs(repo, "validate")[0], 0)   # fresh
            (repo / "a.txt").write_text("2", encoding="utf-8")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "move")
            rc, out = wfs(repo, "validate")
            self.assertEqual(rc, 3)                          # HEAD moved -> stale
            self.assertIn("STALE", out)

    def test_terminal_enum_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            self.assertEqual(wfs(repo, "set-terminal", "--state", "SUCCESS")[0], 0)
            self.assertEqual(wfs(repo, "set-terminal", "--state", "NOPE")[0], 4)

    def test_state_file_lives_in_git_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "set-gate", "--status", "PASS")
            self.assertTrue((repo / ".git" / "copilot-workflow-state.json").is_file())

    def test_validate_flags_uncommitted_edit_to_planned_file(self):
        # F5: SHA still equals HEAD, but an uncommitted edit means the gate no
        # longer covers the tree -> must be flagged STALE, not "OK".
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "set-plan", "--files", "a.txt")
            wfs(repo, "set-gate", "--status", "PASS")
            self.assertEqual(wfs(repo, "validate")[0], 0)     # clean tree, fresh
            (repo / "a.txt").write_text("edited-not-committed", encoding="utf-8")
            rc, out = wfs(repo, "validate")
            self.assertEqual(rc, 3)                            # uncommitted edit -> stale
            self.assertIn("STALE", out)

    def test_non_ascii_repo_path_does_not_crash(self):
        # F1: a non-ASCII repo path must not raise UnicodeEncodeError under a
        # narrow console code page, which would break the 0/3/4 exit contract.
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(Path(t) / "wfs_\u76ee\u5f55")  # 'wfs_目录'
            rc, _ = wfs(repo, "validate", env={"PYTHONIOENCODING": "cp1252"})
            self.assertIn(rc, (0, 3))   # ran to a valid contract code, did not crash (1)

    def test_corrupt_state_file_warns_not_crash(self):
        # F4: a corrupt state file must be reported (stderr) and treated as empty,
        # never crash the caller.
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            (repo / ".git" / "copilot-workflow-state.json").write_text(
                "{not json", encoding="utf-8")
            rc, out = wfs(repo, "gate-status")
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "NONE")


class HookUnit(unittest.TestCase):
    def test_repo_from_statement_preserves_backslashes(self):
        # F3: shlex.split(posix=True) eats backslashes; the raw-statement regex
        # extraction must preserve a Windows -C path so the gate/config lookup
        # targets the real repo instead of a mangled path (which fails open).
        mod = _load_hook_module()
        stmt = 'git -C "C:\\Sources\\proj x" push origin main'
        tokens = mod.tokenize(stmt)
        self.assertEqual(mod.repo_from_statement(stmt, tokens, "cwd"),
                         "C:\\Sources\\proj x")

    def test_gate_fail_blocks_with_dash_C_path(self):
        # F3 integration: `git -C <repo> push` must still see the FAIL gate.
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            git(repo, "checkout", "-q", "-b", "my-task")
            wfs(repo, "set-gate", "--status", "FAIL")
            cmd = f'git -C "{repo}" push origin my-task'
            self.assertTrue(denied(cmd, cwd=os.getcwd()))


class PushGuardGateBlock(unittest.TestCase):
    """Soft gate-status block: FAIL blocks even a task branch; PASS/none allow."""

    def test_task_branch_blocked_when_gate_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            git(repo, "checkout", "-q", "-b", "my-task")
            wfs(repo, "set-gate", "--status", "FAIL")
            self.assertTrue(denied("git push origin my-task", cwd=repo))

    def test_task_branch_allowed_when_gate_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            git(repo, "checkout", "-q", "-b", "my-task")
            wfs(repo, "set-gate", "--status", "PASS")
            self.assertFalse(denied("git push origin my-task", cwd=repo))

    def test_task_branch_allowed_when_no_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            git(repo, "checkout", "-q", "-b", "my-task")
            self.assertFalse(denied("git push origin my-task", cwd=repo))

    def test_protected_still_blocked_even_with_gate_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "set-gate", "--status", "PASS")
            self.assertTrue(denied("git push origin main", cwd=repo))


class DecisionLedger(unittest.TestCase):
    """Append-only decision ledger (audit-only; must never gate/block)."""

    def test_log_and_read_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            rc, _ = wfs(repo, "log-decision", "--decision", "delegate",
                        "--choice", "backend-developer",
                        "--alternatives", "frontend-developer", "do_not_delegate",
                        "--reason", "files under backend/",
                        "--evidence", "git diff", "--confidence", "0.9")
            self.assertEqual(rc, 0)
            self.assertEqual(wfs(repo, "log-decision", "--decision", "conclude",
                                 "--choice", "DONE")[0], 0)
            rc, out = wfs(repo, "decisions")
            self.assertEqual(rc, 0)
            self.assertIn("2 recorded", out)
            self.assertIn("backend-developer", out)
            self.assertIn("DONE", out)

    def test_ledger_persisted_and_append_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "log-decision", "--decision", "delegate", "--choice", "a")
            wfs(repo, "log-decision", "--decision", "delegate", "--choice", "b")
            state = json.loads((repo / ".git" / "copilot-workflow-state.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual([d["choice"] for d in state["decisions"]], ["a", "b"])
            self.assertIsNotNone(state["decisions"][0]["sha"])

    def test_decisions_audit_only_does_not_block_validate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            wfs(repo, "log-decision", "--decision", "conclude", "--choice", "DONE")
            rc, out = wfs(repo, "validate")
            self.assertEqual(rc, 0)                 # ledger never makes resume unsafe
            self.assertIn("decisions: 1 logged", out)

    def test_decisions_empty_is_ok(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(t)
            rc, out = wfs(repo, "decisions")
            self.assertEqual(rc, 0)
            self.assertIn("none recorded", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
