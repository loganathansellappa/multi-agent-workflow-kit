# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-08-11

### Added
- **Bounded review/fix loop in `quality-loop-harness`.** Explicit cap of 3 review/fix cycles plus a
  no-progress / oscillation breaker and per-cycle wall-clock guidance; on trip the agent hands off
  **BLOCKED** instead of looping indefinitely (guards against runaway loops / unbounded cost).
- **MCP secrets-via-environment guidance in SECURITY.md.** Reference integration tokens with `${VAR}`
  expansion instead of inline values, scope `TOOLSETS`/permissions, and keep a `~/.copilot/.gitignore`
  backstop.

## [1.1.1] - 2026-08-10

### Added
- **Mandatory agent-review gate in CONTRIBUTING.** Any change to a `*.agent.md` or `skills/**/SKILL.md`
  must get a dedicated review pass (a custom-agent/instruction reviewer if your setup ships one, otherwise
  self-review against the "Adding or changing an agent" checklist) and all issues resolved before opening a
  PR — applies to every edit, including minor/wording-only changes.

### Changed
- Reverted the "exactly one squashed local commit" requirement across developers and the feature
  orchestrator in favor of meaningful, logically-scoped commits (no pre-squash; squashing left to merge time).

## [1.1.0] - 2026

### Added
- **`evidence-discipline` skill (producer-side anti-guessing).** Every material claim is labelled
  OBSERVED (cited) or INFERRED in an **Evidence Ledger**, enforced deterministically by
  `skills/evidence-discipline/evidence_lint.py` and wired into the `quality-loop-harness` clean gate.
- **`learning-capture` skill (per-session self-learning).** `capture_learning.py` appends durable,
  source-cited lessons to an append-only KB inbox (`lessons-log.jsonl`); `episodic_recall.py` re-serves
  them next session (`recall-lessons:`), closing the learn→recall memory loop. Wired into all developers
  and the feature-orchestrator, with a `learned:` handoff stamp field in `delivery-metrics-capture`.
- **Repo-wide "keep it generic" scrub scan in `validate_agents.py`.** Deterministically fails the build
  on personal paths, internal ticket ids, or private hosts, and — via an optional gitignored
  `scripts/company-terms.txt` denylist — on company/product names, so nothing organisation-specific ever
  ships. Enforces the CONTRIBUTING "keep it generic" policy in code.

### Changed
- `agent-preflight-check` recall now accepts `--kb-root` and surfaces captured lessons.
- Developer and orchestrator agents now run the evidence self-check and never-skip learning capture.

## [1.0.0] - 2025

### Added
- Initial public release.
- Generic agents: `feature-orchestrator`, `review-orchestrator`, `backend-developer`,
  `frontend-developer`, `api-developer`, `code-reviewer`, `security-reviewer`.
- Reusable skills: `agent-preflight-check`, `quality-loop-harness`, `review-findings-output`,
  `untrusted-input-guard`, `delivery-metrics-capture`.
- Knowledge-base pattern with an example skeleton (`kb/`).
- Cross-platform Python tooling (stdlib only): `validate_agents.py`, `apply_model_routing.py`,
  `install_to_copilot.py`, `sync_from_live.py`, `metrics_rollup.py`, plus `run_tests.py`.
- `agents.config.example.yaml` template for model routing and the service map.
- Unit-test suite and a CI workflow that runs the validator and tests.
