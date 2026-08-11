#!/usr/bin/env python3
"""Offline unit tests for the git-push-guard pair:

  * hooks/push-guard-hook.py         (enforcement, run as the CLI runs it: JSON on stdin)
  * skills/git-push-guard/push_guard.py  (advisory, branch-normalization unit)

Regression focus: refspec/force-prefix normalization bypasses — `+main`,
`refs/heads/main`, and `HEAD:refs/heads/main` must all be recognised as `main`.
No network; the hook never runs `git push` itself.
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "push-guard-hook.py"
SKILL = ROOT / "skills" / "git-push-guard" / "push_guard.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pg = _load(SKILL, "push_guard_mod")


def run_hook(cmd, cwd=".", env=None):
    payload = {"toolName": "powershell", "toolArgs": {"command": cmd}, "cwd": cwd}
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=run_env,
    )
    assert proc.returncode == 0, f"hook must exit 0, got {proc.returncode}: {proc.stderr}"
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def denied(cmd, cwd=".", env=None):
    return run_hook(cmd, cwd, env).get("permissionDecision") == "deny"


class HookPerRepoBaseBranch(unittest.TestCase):
    """The configurable base-branch feature: when PUSH_GUARD_CONFIG points at a
    per-repo config, that repo's baseBranch (e.g. develop) is protected too — but
    only for the matching repo, and never at the cost of main/master coverage."""

    def _cfg(self, tmp, repo_path, base):
        cfg = tmp / "svc.yaml"
        cfg.write_text(
            "services:\n"
            "  demo:\n"
            f"    repoPath: {repo_path}\n"
            f"    baseBranch: {base}\n",
            encoding="utf-8",
        )
        return cfg

    def test_configured_develop_denied_for_matching_repo(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp)
            cfg = self._cfg(tmp, repo, "develop")
            env = {"PUSH_GUARD_CONFIG": str(cfg)}
            self.assertTrue(denied("git push origin develop", cwd=repo, env=env))
            # Normalization still applies on top of the configured base.
            self.assertTrue(denied("git push origin +refs/heads/develop", cwd=repo, env=env))

    def test_develop_allowed_without_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = str(Path(t))
            # No PUSH_GUARD_CONFIG -> only main/master protected, develop is a task branch.
            self.assertFalse(denied("git push origin develop", cwd=repo,
                                    env={"PUSH_GUARD_CONFIG": ""}))

    def test_configured_base_does_not_leak_to_other_repo(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = self._cfg(tmp, str(tmp / "repoA"), "develop")
            env = {"PUSH_GUARD_CONFIG": str(cfg)}
            other = str(tmp / "repoB")
            # develop is only the base of repoA; in repoB it's an ordinary branch.
            self.assertFalse(denied("git push origin develop", cwd=other, env=env))

    def test_main_still_protected_even_with_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp)
            cfg = self._cfg(tmp, repo, "develop")
            env = {"PUSH_GUARD_CONFIG": str(cfg)}
            self.assertTrue(denied("git push origin main", cwd=repo, env=env))
            self.assertTrue(denied("git push origin master", cwd=repo, env=env))


class HookProtectedPushes(unittest.TestCase):
    """Every variant below writes to a protected branch and MUST be denied."""

    def test_direct_and_head_refspecs(self):
        for c in ("git push origin main",
                  "git push origin master",
                  "git push origin HEAD:main",
                  "git push origin HEAD:master"):
            self.assertTrue(denied(c), c)

    def test_fully_qualified_ref_bypass(self):
        for c in ("git push origin refs/heads/main",
                  "git push origin HEAD:refs/heads/main",
                  "git push origin refs/heads/master"):
            self.assertTrue(denied(c), c)

    def test_force_prefix_bypass(self):
        for c in ("git push origin +main",
                  "git push origin +master",
                  "git push origin +refs/heads/main",
                  "git push origin +HEAD:main"):
            self.assertTrue(denied(c), c)

    def test_force_flag_and_options(self):
        for c in ("git push --force origin main",
                  "git push -f origin refs/heads/main",
                  "git -C . push origin +main"):
            self.assertTrue(denied(c), c)

    def test_wildcard_pushes_denied(self):
        for c in ("git push --all origin", "git push --mirror origin"):
            self.assertTrue(denied(c), c)

    def test_delete_protected_denied(self):
        # `:main` deletes the remote main branch -> still a write to a protected ref.
        self.assertTrue(denied("git push origin :main"))


class HookAllowedPushes(unittest.TestCase):
    """Task/feature branches must stay allowed (guard must not over-block)."""

    def test_task_branches_allowed(self):
        for c in ("git push origin my-task-branch",
                  "git push origin HEAD:my-feature",
                  "git push origin refs/heads/feature/x",
                  "git push origin +my-task-branch",
                  "git push -u origin some-branch"):
            self.assertFalse(denied(c), c)

    def test_non_push_allowed(self):
        for c in ("git status", "git commit -m x", "git fetch origin",
                  "echo push origin main"):
            self.assertFalse(denied(c), c)

    def test_branch_named_like_prefix_not_confused(self):
        # A branch literally named "mainline" is NOT protected.
        self.assertFalse(denied("git push origin mainline"))
        self.assertFalse(denied("git push origin refs/heads/mainline"))


class HookFailOpen(unittest.TestCase):
    def test_empty_and_malformed_allow(self):
        self.assertEqual(run_hook(""), {})
        proc = subprocess.run([sys.executable, str(HOOK)], input="{not json",
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)


class SkillBranchNormalization(unittest.TestCase):
    def test_normalizes_force_and_refs_prefix(self):
        self.assertEqual(pg._normalize_branch("+main"), "main")
        self.assertEqual(pg._normalize_branch("refs/heads/main"), "main")
        self.assertEqual(pg._normalize_branch("+refs/heads/main"), "main")
        self.assertEqual(pg._normalize_branch("HEAD:refs/heads/main"), "main")
        self.assertEqual(pg._normalize_branch("origin/HEAD:main"), "main")

    def test_leaves_plain_branch_untouched(self):
        self.assertEqual(pg._normalize_branch("my-task-branch"), "my-task-branch")
        self.assertEqual(pg._normalize_branch("feature/x"), "feature/x")
        self.assertEqual(pg._normalize_branch("mainline"), "mainline")

    def test_empty(self):
        self.assertEqual(pg._normalize_branch(""), "")
        self.assertEqual(pg._normalize_branch(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
