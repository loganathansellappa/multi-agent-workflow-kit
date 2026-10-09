#!/usr/bin/env python3
"""Offline unit tests for hooks/slop-guard-hook.py.

The slop-guard hook enforces the PR-comment humanize gate at the tool layer:
  1. a raw-HTTP comment POST that bypasses pr_comments.py is denied (any host);
  2. pr_comments.py reply/comment with --no-lint is denied;
  3. listing comments (GET) and routing through pr_comments.py are allowed.

No network; the hook never runs the command. It must always exit 0 and express
allow/deny only through the JSON body (fail-open).
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "slop-guard-hook.py"


def run_hook(cmd):
    payload = {"toolName": "powershell", "toolArgs": {"command": cmd}, "cwd": "."}
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=dict(os.environ),
    )
    assert proc.returncode == 0, f"hook must exit 0, got {proc.returncode}: {proc.stderr}"
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def denied(decision):
    return decision.get("permissionDecision") == "deny"


class RawBypassDenied(unittest.TestCase):
    def test_github_gh_api_post_denied(self):
        cmd = ('gh api repos/o/r/pulls/7/comments -f body="hi" -F in_reply_to=11')
        self.assertTrue(denied(run_hook(cmd)))

    def test_github_curl_post_denied(self):
        cmd = ('curl -X POST https://api.github.com/repos/o/r/pulls/7/comments '
               '-d \'{"body":"hi"}\'')
        self.assertTrue(denied(run_hook(cmd)))

    def test_gitlab_glab_notes_post_denied(self):
        cmd = ('glab api -X POST projects/1/merge_requests/2/notes -f body="hi"')
        self.assertTrue(denied(run_hook(cmd)))

    def test_bitbucket_curl_post_denied(self):
        cmd = ('curl -X POST -d \'{"text":"hi"}\' '
               'https://bb/rest/api/1.0/projects/P/repos/r/pull-requests/9/comments')
        self.assertTrue(denied(run_hook(cmd)))

    def test_powershell_irm_post_denied(self):
        cmd = ('Invoke-RestMethod -Method POST -Body $b '
               'https://api.bitbucket.org/2.0/repositories/w/r/pullrequests/5/comments')
        self.assertTrue(denied(run_hook(cmd)))

    def test_python_urllib_post_denied(self):
        cmd = ('python -c "import urllib.request; urllib.request.urlopen(req)"  '
               '# POST pulls/7/comments')
        self.assertTrue(denied(run_hook(cmd)))


class NoLintDenied(unittest.TestCase):
    def test_reply_no_lint_denied(self):
        cmd = 'python pr_comments.py reply --slug s --pr 1 --comment 2 --text "x" --no-lint'
        self.assertTrue(denied(run_hook(cmd)))

    def test_comment_no_lint_denied(self):
        cmd = 'python pr_comments.py comment --slug s --pr 1 --text "x" --no-lint'
        self.assertTrue(denied(run_hook(cmd)))


class Allowed(unittest.TestCase):
    def test_list_comments_get_allowed(self):
        self.assertEqual(run_hook("gh api repos/o/r/pulls/7/comments"), {})

    def test_gitlab_list_get_allowed(self):
        self.assertEqual(run_hook("glab api projects/1/merge_requests/2/discussions"), {})

    def test_sanctioned_reply_allowed(self):
        cmd = 'python pr_comments.py reply --slug s --pr 1 --comment 2 --text "good catch"'
        self.assertEqual(run_hook(cmd), {})

    def test_unrelated_command_allowed(self):
        self.assertEqual(run_hook("git status"), {})

    def test_diff_allowed(self):
        self.assertEqual(run_hook("gh pr diff 12 --repo o/r"), {})


class FailOpen(unittest.TestCase):
    def test_empty_payload_allows(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK)], input="", capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.strip()
        self.assertEqual(json.loads(out) if out else {}, {})

    def test_non_shell_payload_allows(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"sessionId": "x", "agentName": "reviewer"}),
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.strip()
        self.assertEqual(json.loads(out) if out else {}, {})


if __name__ == "__main__":
    unittest.main()
