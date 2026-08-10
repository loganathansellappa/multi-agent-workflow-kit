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
                           "--source", "user.yaml:12", "--layer", "contracts"])
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
            cap.main(["--kb-root", d, "--lesson", "same lesson", "--source", "a.py:1"])
            cap.main(["--kb-root", d, "--lesson", "SAME   lesson", "--source", "a.py:9"])
            self.assertEqual(len(cap.read_lessons(d)), 1)

    def test_none_records_nothing_but_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cap.main(["--kb-root", d, "--none"])
            self.assertEqual(rc, 0)
            self.assertEqual(cap.read_lessons(d), [])

    def test_list_ok_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cap.main(["--kb-root", d, "--list"]), 0)


class TestRecallLoopClosure(unittest.TestCase):
    def test_recall_reads_captured_lessons(self):
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "poll timeout lives in receiver.yaml",
                      "--source", "config/receiver.yaml:12", "--layer", "backend",
                      "--repo", "serviceA", "--tags", "retry,config"])
            lessons = rec.read_captured_lessons(d)
            self.assertEqual(len(lessons), 1)

    def test_recall_lessons_matches_repo(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            cap.main(["--kb-root", d, "--lesson", "shared retry budget bug",
                      "--source", "receiver.py:98", "--repo", "serviceA"])
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


if __name__ == "__main__":
    unittest.main()
