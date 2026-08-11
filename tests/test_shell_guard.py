#!/usr/bin/env python3
"""Offline unit tests for hooks/shell-guard-hook.py.

Runs the hook as a subprocess with crafted JSON payloads on stdin (exactly how the
CLI invokes it) and asserts the JSON decision on stdout, plus marker side-effects.
No network, no real shell execution, no CLI restart needed.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "shell-guard-hook.py"


def run_hook(payload):
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"hook must always exit 0, got {proc.returncode}: {proc.stderr}"
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def pretool(cmd, session_id="s1"):
    return {"sessionId": session_id, "toolName": "powershell", "toolArgs": {"command": cmd}}


class SecretGuard(unittest.TestCase):
    """Layer A: any agent, no marker needed."""

    def test_denies_reading_mcp_config(self):
        d = run_hook(pretool("Get-Content ~/.copilot/mcp-config.json"))
        self.assertEqual(d.get("permissionDecision"), "deny")

    def test_denies_secrets_dir(self):
        d = run_hook(pretool("cat .secrets/service"))
        self.assertEqual(d.get("permissionDecision"), "deny")

    def test_denies_token_pem_key(self):
        for c in ("cat api.token", "cp id_rsa.pem /tmp", "type server.key"):
            self.assertEqual(run_hook(pretool(c)).get("permissionDecision"), "deny", c)

    def test_allows_benign_shell(self):
        for c in ("git diff --stat", "npm run lint", "git log --oneline -5", "eslint src"):
            self.assertEqual(run_hook(pretool(c)), {}, c)

    def test_no_false_positive_on_keyword(self):
        self.assertEqual(run_hook(pretool("echo tokenizer && echo keys")), {})


class MutationGuardReadonlySession(unittest.TestCase):
    """Layer B: only when the session is marked read-only."""

    def setUp(self):
        run_hook({"sessionId": "rev1", "agentName": "code-reviewer"})

    def tearDown(self):
        run_hook({"sessionId": "rev1", "agentId": "a", "agentName": "code-reviewer",
                  "response": "done", "stopReason": "end"})

    def test_denies_file_write(self):
        for c in ("Set-Content foo.txt 'x'", "echo x > out.txt", "rm -rf build",
                  "git commit -m x", "git checkout .", "sed -i 's/a/b/' f"):
            self.assertEqual(run_hook(pretool(c, "rev1")).get("permissionDecision"),
                             "deny", c)

    def test_allows_readonly_shell_for_reviewer(self):
        for c in ("git diff", "npm run lint", "eslint src", "git log --oneline", "git show HEAD"):
            self.assertEqual(run_hook(pretool(c, "rev1")), {}, c)

    def test_marker_cleared_on_stop(self):
        run_hook({"sessionId": "rev1", "agentId": "a", "agentName": "code-reviewer",
                  "response": "done", "stopReason": "end"})
        self.assertEqual(run_hook(pretool("Set-Content f.txt 'x'", "rev1")), {})
        run_hook({"sessionId": "rev1", "agentName": "code-reviewer"})


class MutationAllowedForUnmarkedSession(unittest.TestCase):
    def test_developer_session_may_mutate_via_shell(self):
        self.assertEqual(run_hook(pretool("git commit -m x", "dev-session")), {})

    def test_developer_subagent_does_not_mark(self):
        run_hook({"sessionId": "dev2", "agentName": "backend-developer"})
        self.assertEqual(run_hook(pretool("Set-Content f 'x'", "dev2")), {})

    def test_secret_guard_still_applies_to_developer(self):
        run_hook({"sessionId": "dev3", "agentName": "backend-developer"})
        self.assertEqual(run_hook(pretool("cat .secrets/x", "dev3")).get("permissionDecision"),
                         "deny")


class FailOpen(unittest.TestCase):
    def test_empty_payload_allows(self):
        self.assertEqual(run_hook({}), {})

    def test_malformed_toolargs_allows(self):
        self.assertEqual(run_hook({"sessionId": "x", "toolName": "powershell",
                                   "toolArgs": None}), {})

    def test_non_shell_payload_allows(self):
        self.assertEqual(run_hook({"sessionId": "x", "toolName": "edit",
                                   "toolArgs": {"path": "a"}}), {})


class ReadonlyClassification(unittest.TestCase):
    def test_orchestrators_and_reviewers_marked(self):
        for name in ("code-reviewer", "security-reviewer", "review-orchestrator",
                     "feature-orchestrator"):
            run_hook({"sessionId": f"c-{name}", "agentName": name})
            self.assertEqual(
                run_hook(pretool("git commit -m x", f"c-{name}")).get("permissionDecision"),
                "deny", name)
            run_hook({"sessionId": f"c-{name}", "agentId": "a", "response": "d",
                      "stopReason": "end", "agentName": name})

    def test_developers_not_marked(self):
        for name in ("api-developer", "backend-developer", "frontend-developer"):
            run_hook({"sessionId": f"nd-{name}", "agentName": name})
            self.assertEqual(run_hook(pretool("git commit -m x", f"nd-{name}")), {}, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
