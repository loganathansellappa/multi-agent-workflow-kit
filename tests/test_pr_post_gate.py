"""Negative-first tests for hooks/pr-post-gate-hook.py and the pr_comments.py posting path.

Every test runs against a throwaway COPILOT_HOME with a fake, deterministic
`slophound` on PATH (rejects text containing "delve" or "tapestry"), so no real
state, key, network or slophound install is touched.

Override the files under test with PR_GATE_HOOK / PR_COMMENTS_PY.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
HOOK_SRC = Path(os.environ.get("PR_GATE_HOOK") or KIT / "hooks" / "pr-post-gate-hook.py")
PRC_SRC = Path(os.environ.get("PR_COMMENTS_PY")
               or KIT / "skills" / "pr-comment-respond" / "pr_comments.py")

FAKE_SLOPHOUND = (
    "import sys\n"
    "t = sys.stdin.read().lower()\n"
    "bad = [w for w in ('delve', 'tapestry') if w in t]\n"
    "print('slop: ' + ', '.join(bad) if bad else '')\n"
    "sys.exit(1 if bad else 0)\n"
)
GOOD = "Thanks, I moved the null check before the loop so the early return now works."
SLUG, PR, COMMENT = "PROJ/repo", 7, "42"
PRC = "python C:/kit/skills/pr-comment-respond/pr_comments.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prgate-"))
        self.home = self.tmp / "home"
        (self.home / "hooks").mkdir(parents=True)
        (self.home / "agents").mkdir(parents=True)
        shutil.copy(HOOK_SRC, self.home / "hooks" / "pr-post-gate-hook.py")
        (self.home / "agents" / "agents.config.yaml").write_text(
            "services:\n  repo:\n    repoPath: C:\\repos\\myservice\n", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "fake_slophound.py").write_text(FAKE_SLOPHOUND, encoding="utf-8")
        if os.name == "nt":
            (self.bin / "slophound.cmd").write_text(
                f'@"{sys.executable}" "%~dp0fake_slophound.py" %*\r\n', encoding="utf-8")
        else:
            sh = self.bin / "slophound"
            sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_slophound.py" "$@"\n')
            sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
        self._old_env = {k: os.environ.get(k) for k in ("COPILOT_HOME", "PATH")}
        os.environ["COPILOT_HOME"] = str(self.home)
        os.environ["PATH"] = str(self.bin) + os.pathsep + (self._old_env["PATH"] or "")
        self.gate = _load(self.home / "hooks" / "pr-post-gate-hook.py", "gate_" + secrets.token_hex(4))
        self.approvals = self.home / ".pr-approvals"

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def run_hook(self, mode, payload, raw=None):
        p = subprocess.run([sys.executable, str(self.home / "hooks" / "pr-post-gate-hook.py"), mode],
                           input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, env=os.environ.copy(), timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout or "{}")

    def pre(self, sid, tool, args, mode="pre"):
        return self.run_hook(mode, {"sessionId": sid, "toolName": tool, "toolArgs": args})

    def shell(self, sid, command, mode="pre"):
        return self.pre(sid, "powershell", {"command": command}, mode)

    def assertDenied(self, out, needle=None):
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        if needle:
            self.assertIn(needle.lower(), out.get("permissionDecisionReason", "").lower())

    def assertAllowed(self, out):
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)

    def user_root(self, sid, prompt):
        self.run_hook("prompt", {"sessionId": sid, "prompt": prompt})

    def spawn(self, parent, child, agent, prompt):
        self.assertAllowed(self.pre(parent, "task", {"agent_type": agent, "prompt": prompt}))
        self.run_hook("prompt", {"sessionId": child, "prompt": prompt})

    def stage(self, sid, text=GOOD, kind="reply", slug=SLUG, pr=PR, comment=COMMENT,
              path=None, line=None, bind=True):
        token = secrets.token_hex(12)
        rec = {"slug": slug, "pr": pr, "kind": kind, "text": text,
               "hash": self.gate.text_hash(text), "token": token, "created": int(time.time())}
        if kind == "reply":
            rec["comment"] = comment
        if kind == "inline":
            rec["path"], rec["line"] = path, line
        self.approvals.mkdir(parents=True, exist_ok=True)
        (self.approvals / f"{token}.json").write_text(json.dumps(rec), encoding="utf-8")
        if bind:
            self.run_hook("post", {
                "sessionId": sid, "toolName": "powershell",
                "toolArgs": {"command": f"{PRC} stage --slug {slug} --pr {pr} --text x"},
                "toolResult": {"textResultForLlm": f"APPROVAL_TOKEN={token}\n--- final text ---\n{text}"}})
        return token

    @staticmethod
    def ask_args(token, text=GOOD, field=None, extra=None):
        props = {f"post_{token}": field or {"type": "boolean", "title": "Post this comment?"}}
        if extra:
            props.update(extra)
        return {"message": f"PR_POST_APPROVAL {token}\n\n{text}",
                "requestedSchema": {"properties": props}}

    def approve(self, sid, token, text=GOOD, answer="true"):
        args = self.ask_args(token, text)
        self.assertAllowed(self.pre(sid, "ask_user", args))
        result = (f"User responded: post_{token}={answer}" if answer != "cancel"
                  else "User cancelled the request.")
        return self.run_hook("post", {"sessionId": sid, "toolName": "ask_user", "toolArgs": args,
                                      "toolResult": {"textResultForLlm": result}})

    @staticmethod
    def reply_cmd(token, slug=SLUG, pr=PR, comment=COMMENT):
        return f"{PRC} reply --slug {slug} --pr {pr} --comment {comment} --approval {token}"


class OptInTests(GateTestBase):
    def test_non_review_session_can_stage_without_flag(self):
        self.user_root("u1", "please reply to the review comments on my PR")
        self.assertAllowed(self.shell("u1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"))

    def test_review_without_opt_in_cannot_stage(self):
        self.user_root("u1", "review PR 7")
        self.spawn("u1", "c1", "review-orchestrator", "review PR 7 in PROJ/repo")
        self.assertDenied(self.shell("c1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"), "OFF")

    def test_flag_injected_by_agent_into_task_prompt_is_ignored(self):
        self.user_root("u1", "review PR 7")
        self.spawn("u1", "c1", "review-orchestrator", "review PR 7 --post-comments=true")
        self.assertDenied(self.shell("c1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"))

    def test_flag_dropped_from_task_prompt_stays_off(self):
        self.user_root("u1", "review PR 7 --post-comments=true")
        self.spawn("u1", "c1", "review-orchestrator", "review PR 7")
        self.assertDenied(self.shell("c1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"))

    def test_user_opt_in_enables_staging_only(self):
        self.user_root("u1", "review PR 7 --post-comments=true")
        self.spawn("u1", "c1", "review-orchestrator", "review PR 7 --post-comments=true")
        self.assertAllowed(self.shell("c1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"))
        token = self.stage("c1")
        self.assertDenied(self.shell("c1", self.reply_cmd(token)), "approval grant")

    def test_reviewer_grandchild_inherits_review_mode(self):
        self.user_root("u1", "review PR 7")
        self.spawn("u1", "c1", "review-orchestrator", "review PR 7")
        self.spawn("c1", "g1", "general-purpose", "look at file x for PR 7")
        self.assertDenied(self.shell("g1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"))
        self.assertDenied(self.shell("g1", "git commit -m x"))

    def test_similar_flag_values_do_not_opt_in(self):
        for p in ("--post-comments=false", "--post-comments", "--no-post-comments=true",
                  "--post-comments=truely"):
            self.user_root("r_" + p, f"review PR 7 {p}")
            self.run_hook("review-prompt", {"sessionId": "r_" + p, "prompt": f"review PR 7 {p}"})
            out = self.shell("r_" + p, f"{PRC} stage --slug {SLUG} --pr 7 --text hi", mode="review-pre")
            self.assertDenied(out)

    def test_top_level_orchestrator_scoped_hooks(self):
        self.user_root("t1", "review PR 7")
        self.run_hook("review-prompt", {"sessionId": "t1", "prompt": "review PR 7"})
        self.assertDenied(self.shell("t1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi", "review-pre"))
        self.user_root("t2", "review PR 7 --post-comments=true")
        self.run_hook("review-prompt", {"sessionId": "t2", "prompt": "review PR 7 --post-comments=true"})
        self.assertAllowed(self.shell("t2", f"{PRC} stage --slug {SLUG} --pr 7 --text hi", "review-pre"))

    def test_ask_user_in_review_without_opt_in_denied(self):
        self.user_root("u1", "review PR 7")
        self.spawn("u1", "c1", "review-orchestrator", "review PR 7")
        token = self.stage("c1")
        self.assertDenied(self.pre("c1", "ask_user", self.ask_args(token)), "opt in")


class ApprovalTests(GateTestBase):
    def setUp(self):
        super().setUp()
        self.user_root("s1", "help me answer review comments")

    def test_post_without_approval_flag_denied(self):
        self.assertDenied(self.shell("s1", f"{PRC} reply --slug {SLUG} --pr 7 --comment 42"))

    def test_staged_token_alone_is_not_approval(self):
        token = self.stage("s1")
        self.assertDenied(self.shell("s1", self.reply_cmd(token)), "approval grant")

    def test_approved_flow_allows_exactly_one_post(self):
        token = self.stage("s1")
        out = self.approve("s1", token)
        self.assertIn(f"APPROVED {token}", out.get("additionalContext", ""))
        self.assertAllowed(self.shell("s1", self.reply_cmd(token)))
        ok, why, rec = self.gate.consume_grant(token, SLUG, PR, "reply", COMMENT)
        self.assertTrue(ok, why)
        self.assertEqual(rec["text"], GOOD)
        ok, _, _ = self.gate.consume_grant(token, SLUG, PR, "reply", COMMENT)
        self.assertFalse(ok)
        self.assertDenied(self.shell("s1", self.reply_cmd(token)))

    def test_declined_and_cancelled_mint_nothing(self):
        for answer in ("false", "cancel"):
            token = self.stage("s1")
            out = self.approve("s1", token, answer=answer)
            self.assertIn("NOT APPROVED", out.get("additionalContext", ""))
            self.assertFalse((self.approvals / f"{token}.grant.json").exists())
            self.assertFalse((self.approvals / f"{token}.json").exists())
            self.assertDenied(self.shell("s1", self.reply_cmd(token)))

    def test_ask_user_must_show_exact_text(self):
        token = self.stage("s1")
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(token, text="something else")),
                          "exact staged text")

    def test_ask_user_field_must_be_boolean_without_default(self):
        token = self.stage("s1")
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(
            token, field={"type": "boolean", "default": True})), "default")
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(
            token, field={"type": "string", "enum": ["yes", "no"]})), "boolean")

    def test_ask_user_extra_fields_rejected(self):
        token = self.stage("s1")
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(
            token, extra={"note": {"type": "string"}})), "only contain")

    def test_token_staged_in_other_session_cannot_be_approved(self):
        self.user_root("s2", "other work")
        token = self.stage("s2")
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(token)), "not staged in this session")

    def test_unbound_token_cannot_be_approved(self):
        token = self.stage("s1", bind=False)
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(token)))

    def test_forged_grant_denied(self):
        token = self.stage("s1")
        g = {"token": token, "slug": SLUG, "pr": PR, "kind": "reply", "comment": COMMENT,
             "path": None, "line": None, "hash": self.gate.text_hash(GOOD), "sid": "s1",
             "exp": int(time.time()) + 900, "mac": "0" * 64}
        (self.approvals / f"{token}.grant.json").write_text(json.dumps(g), encoding="utf-8")
        self.assertDenied(self.shell("s1", self.reply_cmd(token)), "signature")

    def test_tampered_grant_target_denied(self):
        token = self.stage("s1")
        self.approve("s1", token)
        gp = self.approvals / f"{token}.grant.json"
        g = json.loads(gp.read_text(encoding="utf-8"))
        g["pr"] = 8
        gp.write_text(json.dumps(g), encoding="utf-8")
        self.assertDenied(self.shell("s1", self.reply_cmd(token, pr=8)), "signature")

    def test_target_mismatch_denied(self):
        token = self.stage("s1")
        self.approve("s1", token)
        self.assertDenied(self.shell("s1", self.reply_cmd(token, comment="99")), "different")
        self.assertDenied(self.shell("s1", self.reply_cmd(token, pr=8)), "different")
        self.assertDenied(self.shell("s1", self.reply_cmd(token, slug="PROJ/other")), "different")
        self.assertDenied(self.shell("s1", f"{PRC} comment --slug {SLUG} --pr 7 --approval {token}"),
                          "different")

    def test_inline_line_mismatch_denied(self):
        token = self.stage("s1", kind="inline", path="src/a.cpp", line=10)
        self.approve("s1", token)
        base = f"{PRC} comment --slug {SLUG} --pr 7 --path src/a.cpp"
        self.assertDenied(self.shell("s1", f"{base} --line 11 --approval {token}"), "different")
        self.assertAllowed(self.shell("s1", f"{base} --line 10 --approval {token}"))

    def test_grant_bound_to_session(self):
        self.user_root("s2", "other work")
        token = self.stage("s1")
        self.approve("s1", token)
        self.assertDenied(self.shell("s2", self.reply_cmd(token)), "different session")

    def test_expired_grant_denied(self):
        token = self.stage("s1")
        rec = json.loads((self.approvals / f"{token}.json").read_text(encoding="utf-8"))
        g = self.gate.mint_grant(token, rec, "s1")
        g["exp"] = int(time.time()) - 1
        g["mac"] = self.gate._mac(g)
        (self.approvals / f"{token}.grant.json").write_text(json.dumps(g), encoding="utf-8")
        self.assertDenied(self.shell("s1", self.reply_cmd(token)), "expired")

    def test_reused_token_in_ledger_denied(self):
        token = self.stage("s1")
        self.approve("s1", token)
        with open(self.approvals / "used-tokens.txt", "a", encoding="utf-8") as f:
            f.write(token + "\n")
        self.assertDenied(self.shell("s1", self.reply_cmd(token)), "already used")

    def test_staged_text_changed_after_approval_denied(self):
        token = self.stage("s1")
        self.approve("s1", token)
        sp = self.approvals / f"{token}.json"
        rec = json.loads(sp.read_text(encoding="utf-8"))
        rec["text"] = "Different text that was never shown."
        rec["hash"] = self.gate.text_hash(rec["text"])
        sp.write_text(json.dumps(rec), encoding="utf-8")
        self.assertDenied(self.shell("s1", self.reply_cmd(token)), "changed after approval")

    def test_two_posts_in_one_shell_call_denied(self):
        t1, t2 = self.stage("s1"), self.stage("s1")
        self.approve("s1", t1)
        self.approve("s1", t2)
        self.assertDenied(self.shell("s1", self.reply_cmd(t1) + " ; " + self.reply_cmd(t2)), "one comment")

    def test_bad_text_cannot_be_approved(self):
        for bad in ("Fixed the off-by-one in the loop.", "Fixed it; thanks.",
                    "We should delve into this later."):
            token = self.stage("s1", text=bad)
            self.assertDenied(self.pre("s1", "ask_user", self.ask_args(token, text=bad)), "rules")

    def test_internal_error_on_comment_action_fails_closed(self):
        token = self.stage("s1")
        sp = self.approvals / f"{token}.json"
        rec = json.loads(sp.read_text(encoding="utf-8"))
        rec["created"] = "not-a-number"
        sp.write_text(json.dumps(rec), encoding="utf-8")
        self.assertDenied(self.pre("s1", "ask_user", self.ask_args(token)), "failing closed")


class TextRuleTests(GateTestBase):
    def test_rules(self):
        g = self.gate
        self.assertEqual(g.check_text(GOOD), [])
        self.assertTrue(g.check_text("Use the well-known path."))
        self.assertTrue(g.check_text("Done; merged."))
        self.assertTrue(g.check_text("Check A \u2014 then B"))
        self.assertTrue(g.check_text("Values go 1\u20132"))
        self.assertTrue(g.check_text("x \u2212 y"))
        self.assertTrue(g.check_text("Let us delve in."))
        self.assertTrue(g.check_text(""))
        self.assertEqual(g.check_text("Renamed `TEAM-123` in `my-file.cpp` as asked."), [])

    def test_humanize_is_idempotent_and_strips_tells(self):
        g = self.gate
        h = g.humanize("Nice catch \u2014 fixed it \U0001F680  now.")
        self.assertNotIn("\u2014", h)
        self.assertEqual(g.humanize(h), h)

    def test_missing_slophound_fails_closed(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        os.environ["PATH"] = str(empty)
        probs = self.gate.check_text(GOOD)
        self.assertTrue(any("slophound" in p for p in probs), probs)


class BypassTests(GateTestBase):
    def setUp(self):
        super().setUp()
        self.user_root("b1", "help me with the PR")

    def test_raw_http_comment_writes_denied_even_when_naming_the_helper(self):
        for cmd in (
            'curl -X POST https://bb/rest/api/1.0/projects/P/repos/r/pull-requests/7/comments -d "{}"',
            'curl -X POST https://api.github.com/repos/o/r/issues/7/comments -d x  # pr_comments.py',
            'gh api repos/o/r/pulls/7/comments -f body=hi',
            'Invoke-RestMethod -Method Post -Uri "$u/merge_requests/7/notes" -Body $b',
            "python -c \"import urllib.request as u; u.urlopen(u.Request(x+'/pulls/7/comments', data=b'1'))\"",
        ):
            self.assertDenied(self.shell("b1", cmd))

    def test_cli_comment_commands_denied(self):
        for cmd in ("gh pr comment 7 --body hi", "gh pr review 7 --approve", "gh issue comment 7 -b x",
                    "glab mr note 7 -m hi"):
            self.assertDenied(self.shell("b1", cmd))

    def test_pr_comments_internals_denied(self):
        for cmd in ('python -c "import pr_comments; pr_comments.cmd_reply(...)"',
                    'python -c "from pr_comments import post_reply"',
                    "python -c \"import runpy; runpy.run_path('x/pr_comments.py')\""):
            self.assertDenied(self.shell("b1", cmd))

    def test_state_and_key_are_untouchable(self):
        grant_dir = ".pr-" + "approvals"
        key = "pr-post-gate" + ".key"
        self.assertDenied(self.pre("b1", "view", {"path": str(self.home / grant_dir / "x.grant.json")}))
        self.assertDenied(self.pre("b1", "edit", {"path": str(self.home / grant_dir / "x.json"),
                                                  "old_str": "a", "new_str": "b"}))
        self.assertDenied(self.pre("b1", "create", {"path": str(self.home / "hooks" / ".secrets" / key),
                                                    "file_text": "k"}))
        self.assertDenied(self.shell("b1", f"Get-Content ~/.copilot/hooks/.secrets/{key}"))
        self.assertDenied(self.shell("b1", f"ls ~/.copilot/{grant_dir}"))

    def test_mcp_comment_writers_denied(self):
        for tool in ("github-mcp-server-add_pull_request_review_comment",
                     "github-mcp-server-create_pull_request_review",
                     "gitlab-create_merge_request_note", "bitbucket-add_pr_comment"):
            self.assertDenied(self.pre("b1", tool, {"body": "hi"}))

    def test_unrelated_tools_allowed(self):
        self.assertAllowed(self.pre("b1", "atlassian-jira_add_comment", {"body": "hi"}))
        self.assertAllowed(self.pre("b1", "github-mcp-server-get_pull_request_comments", {}))
        self.assertAllowed(self.shell("b1", "git status && gh pr view 7"))
        self.assertAllowed(self.shell("b1", 'git commit -m "fix"'))
        self.assertAllowed(self.pre("b1", "edit", {"path": "C:/repos/x/a.cpp", "old_str": "a", "new_str": "b"}))


class ReviewReadOnlyTests(GateTestBase):
    def setUp(self):
        super().setUp()
        self.user_root("u1", "review PR 7 --post-comments=true")
        self.spawn("u1", "r1", "review-orchestrator", "review PR 7 --post-comments=true")
        self.spawn("r1", "w1", "backend-reviewer", "review the backend part of PR 7")

    def test_git_and_forge_mutations_denied(self):
        for sid in ("r1", "w1"):
            for cmd in ("git commit -am x", "git push origin HEAD", "git -C C:/r merge main",
                        "git checkout -b x", "git reset --hard", "gh pr merge 7",
                        "glab mr merge 7", "curl -X PUT https://bb/pull-requests/7/merge"):
                self.assertDenied(self.shell(sid, cmd))

    def test_edit_tools_denied(self):
        self.assertDenied(self.pre("w1", "edit", {"path": "C:/repos/x/a.cpp", "old_str": "a", "new_str": "b"}))
        self.assertDenied(self.pre("r1", "create", {"path": "C:/tmp/x.txt", "file_text": "x"}))

    def test_shell_writes_into_repo_or_kit_denied(self):
        self.assertDenied(self.shell("w1", "Set-Content C:\\repos\\myservice\\a.cpp 'x'"))
        self.assertDenied(self.shell("w1", "echo x > ~/.copilot/agents/a.md"))

    def test_resolve_and_delete_denied(self):
        self.assertDenied(self.shell("r1", f"{PRC} resolve --slug {SLUG} --pr 7 --comment 42"))
        self.assertDenied(self.shell("r1", f"{PRC} delete --slug {SLUG} --pr 7 --comment 42"))

    def test_read_only_work_allowed(self):
        for cmd in ("git fetch origin", "git log -5", "git diff main...HEAD", "gh pr view 7",
                    f"{PRC} list --slug {SLUG} --pr 7", "Get-Content C:\\repos\\myservice\\a.cpp",
                    "git status > $null"):
            self.assertAllowed(self.shell("w1", cmd))


class PrCommentsPostTests(GateTestBase):
    def setUp(self):
        super().setUp()
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")
        skill = self.home / "skills" / "pr-comment-respond"
        skill.mkdir(parents=True)
        shutil.copy(PRC_SRC, skill / "pr_comments.py")
        self.prc = _load(skill / "pr_comments.py", "prc_" + secrets.token_hex(4))
        self.user_root("p1", "answer review comments")

    def ns(self, token, comment=COMMENT, pr=PR):
        return argparse.Namespace(approval=token, slug=SLUG, pr=pr, comment=comment)

    def test_post_requires_grant(self):
        token = self.stage("p1")
        with self.assertRaises(SystemExit):
            self.prc._post_guarded(self.ns(token), "reply", comment=COMMENT)

    def test_post_consumes_grant_once(self):
        token = self.stage("p1")
        self.approve("p1", token)
        rec = self.prc._post_guarded(self.ns(token), "reply", comment=COMMENT)
        self.assertEqual(rec["text"], GOOD)
        with self.assertRaises(SystemExit):
            self.prc._post_guarded(self.ns(token), "reply", comment=COMMENT)

    def test_post_refuses_other_target(self):
        token = self.stage("p1")
        self.approve("p1", token)
        with self.assertRaises(SystemExit):
            self.prc._post_guarded(self.ns(token, comment="99"), "reply", comment="99")

    def test_stage_text_rejects_rule_breaks(self):
        with self.assertRaises(SystemExit):
            self.prc.stage_text(self.gate, "Fixed it; thanks.")
        self.assertEqual(self.prc.stage_text(self.gate, "Nice catch \u2014 fixed it now."),
                         "Nice catch, fixed it now.")


class PrInfoTests(GateTestBase):
    """PR number / URL input: parsing, service mapping, read-only gate, output shape."""

    def setUp(self):
        super().setUp()
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")
        skill = self.home / "skills" / "pr-comment-respond"
        skill.mkdir(parents=True)
        shutil.copy(PRC_SRC, skill / "pr_comments.py")
        self.prc = _load(skill / "pr_comments.py", "prc_" + secrets.token_hex(4))

    def test_pr_reference_forms(self):
        p = self.prc.parse_pr_ref
        for ref in ("4521", "PR#4521", "pr 4521", "#4521", " 4521 "):
            self.assertEqual(p(ref), (None, 4521), ref)
        self.assertEqual(p("https://bb/projects/WEB/repos/web-app/pull-requests/812/overview"),
                         ("web-app", 812))

    def test_non_pr_input_rejected(self):
        for ref in ("TEAM-4521", "feature-4521", "4521a", ""):
            with self.assertRaises(SystemExit, msg=ref):
                self.prc.parse_pr_ref(ref)

    def test_service_must_map_to_exactly_one_repo(self):
        cfg = {"repos": {"a": {"service": "x"}, "b": {"service": "y"}, "c": {"service": "y"}}}
        self.assertEqual(self.prc.slug_for_service(cfg, "x"), "a")
        for svc in ("y", "nope"):
            with self.assertRaises(SystemExit):
                self.prc.slug_for_service(cfg, svc)

    def test_pr_info_output(self):
        class FakeBB:
            def req(self, method, path):
                assert method == "GET" and path.endswith("/repos/web-app/pull-requests/7"), path
                return {"fromRef": {"displayId": "feat/x", "latestCommit": "abc",
                                    "repository": {"slug": "web-app"}},
                        "toRef": {"displayId": "main"}, "state": "OPEN", "title": "t"}
        cfg = {"repos": {"web-app": {"project": "WEB", "service": "frontend",
                                     "repoPath": "/r", "reviewerAgent": "code-reviewer"}}}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.prc.cmd_pr_info(FakeBB(), cfg, argparse.Namespace(slug="web-app", pr=7))
        out = json.loads(buf.getvalue())
        self.assertEqual((out["source"], out["target"], out["service"], out["fork"]),
                         ("feat/x", "main", "frontend", False))

    def test_pr_info_allowed_in_read_only_review(self):
        self.user_root("u1", "review PR 7")
        self.spawn("u1", "r1", "review-orchestrator", "frontend: 7")
        self.assertAllowed(self.shell("r1", f"{PRC} pr-info --service frontend --pr 7"))
        self.assertDenied(self.shell("r1", f"{PRC} stage --slug {SLUG} --pr 7 --text hi"))


if __name__ == "__main__":
    unittest.main()
