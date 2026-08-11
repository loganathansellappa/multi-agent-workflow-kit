"""Unit tests for learning-capture (capture_learning.py) + recall loop closure.

run_tests.py discovers this via tests/test_*.py. Proves capture appends a
source-cited entry, enforces the source, de-duplicates, and that episodic_recall
re-serves captured lessons (the closed loop).
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "learning-capture"))
sys.path.insert(0, str(ROOT / "skills" / "agent-preflight-check"))

import capture_learning as cap  # noqa: E402
import episodic_recall as rec   # noqa: E402


class TestCaptureLearning(unittest.TestCase):
    def test_capture_appends_entry(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--lesson", "a read-only field maps to an immutable property",
                           "--source", "user.yaml:12", "--no-verify-source", "--layer", "contracts"])
            self.assertEqual(rc, 0)
            entries = cap.read_lessons(d)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["source"], "user.yaml:12")
            self.assertEqual(entries[0]["layer"], "contracts")
            self.assertIn("date", entries[0])

    def test_source_required(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--lesson", "something learned"])
            self.assertEqual(rc, 2)  # a lesson with no source is a guess
            self.assertEqual(cap.read_lessons(d), [])

    def test_lesson_required(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--source", "foo.py:1"])
            self.assertEqual(rc, 2)

    def test_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "same lesson", "--source", "a.py:1", "--no-verify-source"])
            cap.main(["--kb-root", d, "--lesson", "SAME   lesson", "--source", "a.py:9", "--no-verify-source"])
            self.assertEqual(len(cap.read_lessons(d)), 1)

    def test_none_records_nothing_but_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--none"])
            self.assertEqual(rc, 0)
            self.assertEqual(cap.read_lessons(d), [])

    def test_list_ok_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cap.main(["--kb-root", d, "--list"]), 0)

    def test_new_capture_is_unverified_with_default_confidence(self):
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "unverified by default",
                      "--source", "a.cs:1", "--no-verify-source"])
            e = cap.read_lessons(d)[0]
            self.assertIs(e.get("validated"), False)
            self.assertEqual(e.get("confidence"), "medium")

    def test_confidence_and_agent_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "high conf lesson",
                      "--source", "a.cs:1", "--no-verify-source",
                      "--confidence", "high", "--agent", "backend-developer"])
            e = cap.read_lessons(d)[0]
            self.assertEqual(e.get("confidence"), "high")
            self.assertEqual(e.get("agent"), "backend-developer")


class TestRecallLoopClosure(unittest.TestCase):
    def test_recall_reads_captured_lessons(self):
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "poll timeout lives in receiver.yaml",
                      "--source", "config/receiver.yaml:12", "--no-verify-source", "--layer", "backend",
                      "--repo", "serviceA", "--tags", "retry,config"])
            lessons = rec.read_captured_lessons(d)
            self.assertEqual(len(lessons), 1)

    def test_recall_lessons_matches_repo(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "shared retry budget bug",
                      "--source", "receiver.py:98", "--no-verify-source", "--repo", "serviceA"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rec.recall_lessons(d, "serviceA", [], 5)
            out = buf.getvalue()
            self.assertIn("recall-lessons:", out)
            self.assertIn("shared retry budget bug", out)

    def test_recall_lessons_silent_when_no_log(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rec.recall_lessons(d, "anything", [], 5)
            self.assertEqual(buf.getvalue(), "")

    def test_recall_marks_new_capture_unverified(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "raw inbox lesson",
                      "--source", "x.cs:1", "--no-verify-source", "--repo", "svc"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rec.recall_lessons(d, "svc", [], 5)
            out = buf.getvalue()
            self.assertIn("[unverified]", out)
            self.assertIn("unverified", out)

    def test_recall_no_unverified_mark_when_validated(self):
        import io
        import json
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "lessons-log.jsonl"
            log.write_text(json.dumps({"date": "2026-01-01", "lesson": "curated fact",
                                       "source": "x.cs:1", "repo": "svc", "validated": True}) + "\n",
                           encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rec.recall_lessons(d, "svc", [], 5)
            out = buf.getvalue()
            self.assertIn("curated fact", out)
            self.assertNotIn("[unverified]", out)


class TestSourceVerification(unittest.TestCase):
    def test_dangling_path_anchor_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--lesson", "x", "--source", "src/nope/Missing.cs:5"])
            self.assertEqual(rc, 2)
            self.assertEqual(cap.read_lessons(d), [])

    def test_real_path_anchor_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "sub"
            sub.mkdir()
            (sub / "real.py").write_text("x = 1\n", encoding="utf-8")
            rc = cap.main(["--kb-root", d, "--lesson", "real anchor",
                           "--source", "sub/real.py:1", "--source-root", d])
            self.assertEqual(rc, 0)
            self.assertEqual(len(cap.read_lessons(d)), 1)

    def test_multi_anchor_all_must_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "sub"
            sub.mkdir()
            (sub / "real.py").write_text("x = 1\n", encoding="utf-8")
            rc = cap.main(["--kb-root", d, "--lesson", "one real one fake",
                           "--source", "sub/real.py:1 and sub/fake.py:2", "--source-root", d])
            self.assertEqual(rc, 2)
            self.assertEqual(cap.read_lessons(d), [])

    def test_bare_filename_rejected_when_absent(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as root:
            rc = cap.main(["--kb-root", d, "--lesson", "bare absent",
                           "--source", "NoSuchFileXYZ.cs:9", "--source-root", root])
            self.assertEqual(rc, 2)
            self.assertEqual(cap.read_lessons(d), [])

    def test_bare_filename_found_by_search(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as root:
            deep = Path(root) / "a" / "b"
            deep.mkdir(parents=True)
            (deep / "Found.cs").write_text("// x\n", encoding="utf-8")
            rc = cap.main(["--kb-root", d, "--lesson", "bare found nested",
                           "--source", "Found.cs:3", "--source-root", root])
            self.assertEqual(rc, 0)
            self.assertEqual(len(cap.read_lessons(d)), 1)

    def test_command_source_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--lesson", "cmd ok", "--source", "yarn run build failed"])
            self.assertEqual(rc, 0)

    def test_no_verify_source_bypass(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--lesson", "external anchor",
                           "--source", "src/nope/Missing.cs:5", "--no-verify-source"])
            self.assertEqual(rc, 0)

    def test_verify_source_unit(self):
        ok, bad = cap.verify_source("src/nope/Missing.cs:5")
        self.assertFalse(ok)
        self.assertEqual(bad, "src/nope/Missing.cs")
        ok2, _ = cap.verify_source("just a log line, no path")
        self.assertTrue(ok2)


if __name__ == "__main__":
    unittest.main()
