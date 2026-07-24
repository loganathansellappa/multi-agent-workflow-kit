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


if __name__ == "__main__":
    unittest.main()
