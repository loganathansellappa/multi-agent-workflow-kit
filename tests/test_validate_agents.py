#!/usr/bin/env python3
"""Tests for scripts/validate_agents.py."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATE = ROOT / "scripts" / "validate_agents.py"

GOOD_DEV = """\
---
name: sample-developer
description: A sample developer agent for tests.
tools: ['execute', 'read', 'search', 'edit', 'skill', 'ask_user']
---
Body text. Operational Hardening section present. Clean gate: 0 Critical / 0 High / 0 Medium.
"""


def new_kit():
    # Create a throwaway kit skeleton (agents subdirs + skills dir) in temp.
    root = Path(tempfile.mkdtemp(prefix="kit-"))
    (root / "agents" / "developers").mkdir(parents=True)
    (root / "agents" / "reviewers").mkdir(parents=True)
    (root / "skills").mkdir()
    return root


def add_agent(root, sub, file, content):
    # Write an agent file into the given agents/<sub>/ folder.
    (root / "agents" / sub / file).write_text(content)


def run(root):
    # Run the validator against a kit root; return (exit_code, combined_output).
    r = subprocess.run([sys.executable, str(VALIDATE), "--kit-root", str(root)],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def run_terms(root, terms):
    # Run the validator with an explicit --terms denylist file.
    r = subprocess.run([sys.executable, str(VALIDATE), "--kit-root", str(root),
                        "--terms", str(terms)], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


class ValidateAgents(unittest.TestCase):
    def test_passes_clean_kit(self):
        # A well-formed agent passes with exit 0.
        k = new_kit()
        try:
            add_agent(k, "developers", "sample-developer.agent.md", GOOD_DEV)
            code, _ = run(k)
            self.assertEqual(code, 0)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_flags_reviewer_disallowed_tool(self):
        # A reviewer agent holding a write tool (edit) is a finding -> exit 1.
        k = new_kit()
        try:
            add_agent(k, "developers", "sample-developer.agent.md", GOOD_DEV)
            add_agent(k, "reviewers", "sample-reviewer.agent.md", """\
---
name: sample-reviewer
description: A sample reviewer.
tools: ['execute', 'read', 'search', 'edit', 'skill', 'ask_user']
---
Body. Operational Hardening.
""")
            code, text = run(k)
            self.assertEqual(code, 1)
            self.assertIn("disallowed tool 'edit'", text)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_flags_legacy_major_wording(self):
        # Legacy "0 Major" gate wording is rejected in favour of the canonical phrasing.
        k = new_kit()
        try:
            add_agent(k, "developers", "sample-developer.agent.md",
                      GOOD_DEV.replace("0 Critical / 0 High / 0 Medium", "0 Major"))
            code, text = run(k)
            self.assertEqual(code, 1)
            self.assertIn("0 Major", text)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_flags_unresolved_skill_reference(self):
        # Referencing a skill that isn't shipped in skills/ is a finding -> exit 1.
        k = new_kit()
        try:
            add_agent(k, "developers", "sample-developer.agent.md",
                      GOOD_DEV + "\nInvoke skill `nonexistent-skill`.")
            code, text = run(k)
            self.assertEqual(code, 1)
            self.assertIn("nonexistent-skill", text)
        finally:
            shutil.rmtree(k, ignore_errors=True)


class ScrubScan(unittest.TestCase):
    """Repo-wide 'keep it generic' scrub scan in validate_agents.py."""

    def _kit_with_doc(self, body):
        k = new_kit()
        add_agent(k, "developers", "sample-developer.agent.md", GOOD_DEV)
        (k / "docs").mkdir(exist_ok=True)
        (k / "docs" / "note.md").write_text(body, encoding="utf-8")
        return k

    def test_flags_internal_ticket_id(self):
        k = self._kit_with_doc("See PROJ-4321 for the incident details.\n")
        try:
            code, text = run(k)
            self.assertEqual(code, 1)
            self.assertIn("PROJ-4321", text)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_allows_standards_and_placeholder_tokens(self):
        # ISO-8601 (standard) and TICKET-123 (placeholder num) must NOT be flagged.
        k = self._kit_with_doc("Timestamps are ISO-8601. Try TICKET-123 as a demo.\n")
        try:
            code, text = run(k)
            self.assertEqual(code, 0, msg=text)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_flags_personal_home_path(self):
        k = self._kit_with_doc("Config at C:\\Users\\jdoe\\.copilot lives there.\n")
        try:
            code, text = run(k)
            self.assertEqual(code, 1)
            self.assertIn("personal path", text)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_flags_company_term_from_denylist(self):
        k = self._kit_with_doc("The AcmeCorp service handles this.\n")
        terms = k / "terms.txt"
        terms.write_text("# names\nAcmeCorp\n", encoding="utf-8")
        try:
            code, text = run_terms(k, terms)
            self.assertEqual(code, 1)
            self.assertIn("AcmeCorp", text)
        finally:
            shutil.rmtree(k, ignore_errors=True)

    def test_denylist_word_boundary_no_false_positive(self):
        # A denylist term must not match inside an unrelated word.
        k = self._kit_with_doc("The app concatenates buffers cleanly.\n")
        terms = k / "terms.txt"
        terms.write_text("cat\n", encoding="utf-8")
        try:
            code, text = run_terms(k, terms)
            self.assertEqual(code, 0, msg=text)
        finally:
            shutil.rmtree(k, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
