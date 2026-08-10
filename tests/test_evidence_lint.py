"""Unit tests for evidence_lint.py (the enforcement half of evidence-discipline).

run_tests.py discovers this via tests/test_*.py. Proves the linter reliably
catches unbacked/uncited claims and passes a properly cited Evidence Ledger.
"""
import sys
import tempfile
import unittest
from pathlib import Path

LINT_DIR = Path(__file__).resolve().parent.parent / "skills" / "evidence-discipline"
sys.path.insert(0, str(LINT_DIR))

import evidence_lint as el  # noqa: E402

CLEAN = """# Root cause

## Evidence Ledger

| Claim | Status | Source |
|-------|--------|--------|
| The poller gives up after 3 failed attempts | OBSERVED | `receiver.py:142` |
| The web client shares the lifecycle | INFERRED | not read yet - confirm in web_client.py |
"""

VAGUE = """## Evidence Ledger

| Claim | Status | Source |
|-------|--------|--------|
| It always retries three times | OBSERVED | the code |
"""

NOCITE = """## Evidence Ledger

| Claim | Status | Source |
|-------|--------|--------|
| Default timeout is 30 seconds | OBSERVED | it is set somewhere in config |
"""

BADSTATUS = """## Evidence Ledger

| Claim | Status | Source |
|-------|--------|--------|
| Channel dies permanently | probably | guess |
"""

NO_LEDGER_RISK = "The default timeout is 30s and it never retries after that."


def sevs(findings):
    return [f[0] for f in findings]


class TestEvidenceLint(unittest.TestCase):
    def test_clean_ledger_has_no_fail(self):
        findings = el.lint(CLEAN, require_ledger=True)
        self.assertNotIn("FAIL", sevs(findings), findings)

    def test_vague_source_fails(self):
        findings = el.lint(VAGUE, require_ledger=True)
        self.assertIn("FAIL", sevs(findings))

    def test_observed_without_citation_fails(self):
        findings = el.lint(NOCITE, require_ledger=True)
        self.assertIn("FAIL", sevs(findings))

    def test_invalid_status_fails(self):
        findings = el.lint(BADSTATUS, require_ledger=True)
        self.assertIn("FAIL", sevs(findings))

    def test_missing_ledger_fails_when_required(self):
        findings = el.lint(NO_LEDGER_RISK, require_ledger=True)
        self.assertIn("FAIL", sevs(findings))

    def test_missing_ledger_warns_when_not_required(self):
        findings = el.lint(NO_LEDGER_RISK, require_ledger=False)
        self.assertNotIn("FAIL", sevs(findings))
        self.assertIn("WARN", sevs(findings))

    def test_prose_uncited_assertion_warns(self):
        text = CLEAN + "\n\nSeparately, it returns 42 in all cases with no error handling.\n"
        findings = el.lint(text, require_ledger=True)
        self.assertTrue(any(s == "WARN" for s, _, _ in findings))

    def test_has_citation_detects_file_line(self):
        self.assertTrue(el.has_citation("see foo.py:12"))
        self.assertTrue(el.has_citation("per the log line"))
        self.assertFalse(el.has_citation("it just works"))

    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.md"
            good.write_text(CLEAN, encoding="utf-8")
            self.assertEqual(el.main([str(good), "--require-ledger"]), 0)
            bad = Path(d) / "bad.md"
            bad.write_text(VAGUE, encoding="utf-8")
            self.assertEqual(el.main([str(bad), "--require-ledger"]), 1)
            self.assertEqual(el.main([str(bad), "--require-ledger", "--advisory"]), 0)

    def test_missing_file_is_usage_error(self):
        self.assertEqual(el.main(["does-not-exist-xyz.md"]), 2)


if __name__ == "__main__":
    unittest.main()
