# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

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
