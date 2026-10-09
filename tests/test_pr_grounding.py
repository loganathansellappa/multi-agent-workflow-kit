"""Unit tests for the deterministic grounding/validity preflight in pr_comments.py.

No network: a FakeBB returns canned PR ground truth so ground_check / reply_target_ok
can be exercised offline. These cover the structurally-detectable false positives the
preflight is meant to catch, plus the two honest boundaries (no-refs pass, API-error
fail-open).
"""
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "pr-comment-respond"))
import pr_comments as m  # noqa: E402


class FakeBB:
    """Minimal stand-in for the BB REST client used by the grounding helpers."""

    def __init__(self, changed=(), commits=(), head="deadbeefcafe",
                 browse_ok=(), target=None, http_error_on=None):
        self.changed = list(changed)
        self.commits = list(commits)   # list of (full, short)
        self.head = head
        self.browse_ok = set(browse_ok)
        self.target = target           # dict for a reply target, or None -> 404
        self.http_error_on = http_error_on  # substring that forces an HTTPError(500)

    def _maybe_raise(self, path, code=500):
        if self.http_error_on and self.http_error_on in path:
            raise urllib.error.HTTPError(path, code, "boom", {}, None)

    def paged(self, path):
        self._maybe_raise(path)
        if path.endswith("/changes"):
            return [{"path": {"toString": p}} for p in self.changed]
        if path.endswith("/commits"):
            return [{"id": f, "displayId": s} for f, s in self.commits]
        return []

    def req(self, method, path, body=None):
        self._maybe_raise(path)
        if "/browse/" in path:
            rel = path.split("/browse/", 1)[1].split("?", 1)[0]
            if rel in self.browse_ok:
                return {"type": "FILE"}
            raise urllib.error.HTTPError(path, 404, "nope", {}, None)
        if "/comments/" in path:
            if self.target is None:
                raise urllib.error.HTTPError(path, 404, "nope", {}, None)
            return self.target
        # pull-request info (head ref lookup)
        return {"fromRef": {"latestCommit": self.head}}


class ExtractRefsTests(unittest.TestCase):
    def test_extracts_path_fileline_and_sha(self):
        files, filelines, shas = m.extract_refs(
            "fixed src/a/login.cpp:42 see Foo.ts, sha a1b2c3d4e5f6")
        self.assertIn("src/a/login.cpp", files)
        self.assertIn("Foo.ts", files)
        self.assertIn(("src/a/login.cpp", 42), filelines)
        self.assertIn("a1b2c3d4e5f6", shas)

    def test_prose_dots_are_not_paths(self):
        files, _, _ = m.extract_refs("i.e. this is fine, e.g. nothing here")
        self.assertEqual(files, set())

    def test_no_refs_means_empty(self):
        files, filelines, shas = m.extract_refs("Good catch, done.")
        self.assertEqual((files, filelines), (set(), []))


class BodyOkTests(unittest.TestCase):
    def test_empty_rejected(self):
        self.assertFalse(m.body_ok("   ")[0])

    def test_placeholder_rejected(self):
        for p in ("TODO", "...", "<fill>", "n/a", "FIXME"):
            self.assertFalse(m.body_ok(p)[0], p)

    def test_real_body_ok(self):
        self.assertTrue(m.body_ok("Done, moved the null check up.")[0])


class FileGroundedTests(unittest.TestCase):
    def test_exact_and_suffix_and_basename(self):
        changed = {"src/auth/login.cpp"}
        self.assertTrue(m._file_grounded("src/auth/login.cpp", changed))
        self.assertTrue(m._file_grounded("auth/login.cpp", changed))
        self.assertTrue(m._file_grounded("login.cpp", changed))   # bare basename
        self.assertFalse(m._file_grounded("src/other/login.cpp", changed))  # wrong dir
        self.assertFalse(m._file_grounded("bogus.cpp", changed))


class GroundCheckTests(unittest.TestCase):
    def test_grounded_file_passes(self):
        bb = FakeBB(changed=["src/auth/login.cpp"])
        ok, problems = m.ground_check(bb, "P", "repo", 7,
                                      "fixed in src/auth/login.cpp:42", strict=False)
        self.assertTrue(ok, problems)

    def test_ungrounded_file_denied(self):
        bb = FakeBB(changed=["src/auth/login.cpp"])
        ok, problems = m.ground_check(bb, "P", "repo", 7,
                                      "see src/made/up.cpp", strict=False)
        self.assertFalse(ok)
        self.assertTrue(any("up.cpp" in p for p in problems))

    def test_existence_mode_browse_fallback_passes(self):
        # file not changed in the PR but exists at head -> grounded in existence mode
        bb = FakeBB(changed=["other.cpp"], browse_ok=["src/util/helper.cpp"])
        ok, _ = m.ground_check(bb, "P", "repo", 7,
                               "context in src/util/helper.cpp", strict=False)
        self.assertTrue(ok)

    def test_strict_mode_requires_diff_not_just_existence(self):
        bb = FakeBB(changed=["other.cpp"], browse_ok=["src/util/helper.cpp"])
        ok, _ = m.ground_check(bb, "P", "repo", 7,
                               "context in src/util/helper.cpp", strict=True)
        self.assertFalse(ok)

    def test_no_refs_passes_without_api(self):
        bb = FakeBB(http_error_on="/")  # any call would raise; none should happen
        ok, problems = m.ground_check(bb, "P", "repo", 7, "Good catch, done.", strict=False)
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_api_error_fails_open(self):
        bb = FakeBB(changed=["x.cpp"], http_error_on="/changes")
        ok, problems = m.ground_check(bb, "P", "repo", 7, "see made/up.cpp", strict=False)
        self.assertTrue(ok)
        self.assertTrue(any("grounding skipped" in p for p in problems))

    def test_strict_sha_denied_when_absent(self):
        bb = FakeBB(changed=[], commits=[("a1b2c3d4e5f6a7b8", "a1b2c3d")])
        ok, problems = m.ground_check(bb, "P", "repo", 7,
                                      "landed in ffffffffffff", strict=True)
        self.assertFalse(ok)
        self.assertTrue(any("commit not in this PR" in p for p in problems))

    def test_strict_sha_prefix_passes(self):
        bb = FakeBB(changed=[], commits=[("a1b2c3d4e5f6a7b8", "a1b2c3d")])
        ok, _ = m.ground_check(bb, "P", "repo", 7, "landed in a1b2c3d", strict=True)
        self.assertTrue(ok)


class ReplyTargetTests(unittest.TestCase):
    def test_missing_comment_denied(self):
        bb = FakeBB(target=None)
        ok, why = m.reply_target_ok(bb, "P", "repo", 7, 999)
        self.assertFalse(ok)
        self.assertIn("does not exist", why)

    def test_resolved_comment_denied(self):
        bb = FakeBB(target={"state": "RESOLVED"})
        ok, why = m.reply_target_ok(bb, "P", "repo", 7, 5)
        self.assertFalse(ok)
        self.assertIn("RESOLVED", why)

    def test_open_comment_ok(self):
        bb = FakeBB(target={"state": "OPEN"})
        ok, _ = m.reply_target_ok(bb, "P", "repo", 7, 5)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
