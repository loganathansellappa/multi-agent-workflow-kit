"""Capability-contract test — the per-agent tool-grant boundary, enforced in CI.

The Copilot CLI *runtime-enforces* the `tools:` frontmatter — an agent handed no
`edit`/`create`/`task` genuinely cannot call those tools. That makes the `tools:`
line a real privilege boundary, not documentation. This test locks that boundary so
a hand-edit that widens an agent's privilege (e.g. giving a reviewer `edit`) FAILS
CI on drift.

Two independent layers:
  1. Frozen snapshot (EXPECTED_TOOLS): the exact tool set each shipped agent may
     hold. ANY change — a new agent, an added/removed tool — fails until the
     contract is consciously updated. This is the "fails on drift" gate.
  2. Role invariants: even a careless snapshot edit cannot grant a reviewer a
     mutation/delegation tool, or drop a developer's edit/task.

Stdlib only; discovered automatically by run_tests.py (tests/test_*.py).
"""
import re
import unittest
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = KIT_ROOT / "agents"

# Tools that can mutate the workspace or repo state. A read-only reviewer must
# hold NONE of these — this is the core of the "reviewers are read-only" claim.
MUTATION_TOOLS = {
    "edit", "create", "write", "apply_patch", "str_replace", "multi_edit", "insert",
}
# Tools that let an agent spawn / message other agents (sub-delegation).
DELEGATION_TOOLS = {"task", "write_agent"}

# Read-only inspection baseline shared by every reviewer.
_R = {"execute", "read", "search", "skill", "ask_user"}
# Developer / edit-capable baseline.
_D = {"execute", "read", "search", "edit", "task", "skill", "ask_user"}

# --- Layer 1: frozen contract. Update this MAP deliberately when a grant changes. ---
EXPECTED_TOOLS = {
    # Reviewers — strictly read-only, no delegation.
    "code-reviewer": _R,
    "security-reviewer": _R,
    # Developers — edit + delegate.
    "api-developer": _D,
    "backend-developer": _D,
    "frontend-developer": _D,
    # Orchestrators — route/delegate; no direct edit.
    "feature-orchestrator": _R | {"task"},
    "review-orchestrator": _R | {"task"},
}


def _parse_tools(text):
    m = re.search(r"(?m)^tools:[ \t]*\[([^\]]*)\]", text)
    return set(re.findall(r"'([^']+)'", m.group(1))) if m else set()


def _parse_name(text, fallback):
    m = re.search(r"(?m)^name:[ \t]*(\S+)", text)
    return m.group(1).strip() if m else fallback


def _load_agents():
    """Return {name: (tools_set, path)} for every shipped agent."""
    out = {}
    for p in sorted(AGENTS_DIR.rglob("*.agent.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        name = _parse_name(text, p.stem.replace(".agent", ""))
        out[name] = (_parse_tools(text), p)
    return out


class TestCapabilityContract(unittest.TestCase):
    def setUp(self):
        self.agents = _load_agents()

    # ---- Layer 1: frozen snapshot ----
    def test_no_unlisted_agent(self):
        """Every shipped agent must be in the contract (new agents must opt in)."""
        unlisted = sorted(set(self.agents) - set(EXPECTED_TOOLS))
        self.assertFalse(
            unlisted,
            f"Agent(s) missing from EXPECTED_TOOLS capability contract: {unlisted}. "
            f"Add them with their intended tool set.",
        )

    def test_no_stale_contract_entry(self):
        """Contract must not name agents that no longer exist."""
        stale = sorted(set(EXPECTED_TOOLS) - set(self.agents))
        self.assertFalse(stale, f"EXPECTED_TOOLS names non-existent agent(s): {stale}.")

    def test_tools_match_contract_exactly(self):
        """Each agent's declared tools must equal its frozen contract (drift gate)."""
        drift = []
        for name, expected in EXPECTED_TOOLS.items():
            if name not in self.agents:
                continue
            actual, _ = self.agents[name]
            if actual != expected:
                added = sorted(actual - expected)
                removed = sorted(expected - actual)
                drift.append(f"{name}: +{added} -{removed}")
        self.assertFalse(
            drift,
            "Tool-grant drift vs capability contract (update EXPECTED_TOOLS only if "
            "the change is intended):\n  " + "\n  ".join(drift),
        )

    # ---- Layer 2: role invariants (independent of the snapshot) ----
    def test_reviewers_have_no_mutation_tools(self):
        """Any agent under agents/reviewers/ must hold zero mutation tools."""
        violations = []
        for name, (tools, path) in self.agents.items():
            if path.parent.name == "reviewers":
                bad = sorted(tools & MUTATION_TOOLS)
                if bad:
                    violations.append(f"{name} holds mutation tool(s) {bad}")
        self.assertFalse(
            violations,
            "Reviewers must be read-only:\n  " + "\n  ".join(violations),
        )

    def test_reviewers_cannot_delegate(self):
        """Reviewers cannot spawn/message other agents (no sub-delegation)."""
        violations = []
        for name, (tools, path) in self.agents.items():
            if path.parent.name == "reviewers":
                bad = sorted(tools & DELEGATION_TOOLS)
                if bad:
                    violations.append(f"{name} holds delegation tool(s) {bad}")
        self.assertFalse(
            violations,
            "Reviewers must not spawn/message other agents:\n  " + "\n  ".join(violations),
        )

    def test_orchestrators_cannot_edit(self):
        """Orchestrators route/delegate; they must not edit code directly."""
        violations = []
        for name, (tools, path) in self.agents.items():
            if path.parent.name == "orchestrators":
                bad = sorted(tools & MUTATION_TOOLS)
                if bad:
                    violations.append(f"{name} holds mutation tool(s) {bad}")
        self.assertFalse(
            violations,
            "Orchestrators must not edit directly:\n  " + "\n  ".join(violations),
        )

    def test_developers_can_edit_and_delegate(self):
        """Developers must retain edit + task, or they cannot do their job."""
        missing = []
        for name, (tools, path) in self.agents.items():
            if path.parent.name == "developers":
                if "edit" not in tools:
                    missing.append(f"{name} missing 'edit'")
                if "task" not in tools:
                    missing.append(f"{name} missing 'task'")
        self.assertFalse(missing, "Developer capability regressions:\n  " + "\n  ".join(missing))


if __name__ == "__main__":
    unittest.main()
