# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [1.15.0] - 2026-10-10

### Added
- **Deterministic grounding/validity preflight for `pr-comment-respond`.** `pr_comments.py` `reply`/`comment`
  now run a stdlib preflight before anything posts, so a structurally-detectable false positive never
  reaches the host:
  - **Target check (reply):** the parent comment must exist and must not be `RESOLVED` (no replying to a
    missing or closed thread).
  - **Body check:** empty and placeholder bodies (`...`, `TODO`, `<fill>`, `n/a`, `FIXME`) are rejected.
  - **Grounding check:** every file / `file:line` the reply cites must resolve to THIS PR: it must be in
    the PR's changed-file set, or (existence mode, the default) present at the PR head. With `--strict`,
    cited files AND commit SHAs must be in the PR diff. A post that cites nothing passes (nothing to
    ground). A host/API error fails open with a note (same discipline as the slophound skip). Exit 4 on
    rejection.
  - Honest boundary: this catches made-up references and wrong/closed targets. It does **not** certify that
    a reply is semantically correct or responsive. That stays the agent's job, backed by the SKILL.md
    grounding rule.
  - New flags `--no-ground` (opt out) and `--strict`; `tests/test_pr_grounding.py` (18 offline tests).
- **`slop-guard-hook.py` now also enforces the grounding gate.** Added rule 2b: `pr_comments.py`
  `reply`/`comment` invoked with `--no-ground` is denied at the tool layer, so the preflight cannot be
  switched off for a real post. `tests/test_slop_guard.py` extended (+2).

## [1.14.0] - 2026-10-10

### Added
- **New `pr-comment-respond` skill — close the PR review-comment loop in-session, in a human voice.** Adds
  `skills/pr-comment-respond/SKILL.md` + `pr_comments.py` (stdlib + pyyaml CLI) with references and a
  fictional example. Two paths:
  - **Inbound:** reviewers commented on a PR you authored → list actionable comments, judge each against
    the real code (OBSERVED, cited), delegate the fix to the owning component's `developerAgent` (or an
    orchestrator for cross-component work) through its clean-gate loop, then reply + resolve behind a
    confirmation gate. Never pushes a protected branch (runs `git-push-guard`).
  - **Authoring:** review a PR/branch/diff, present findings first, then post inline/general comments only
    after an explicit `ask_user` confirmation.
  - **Humanize gate (enforced in code):** `reply`/`comment` sanitize AI punctuation tells then lint with
    slophound; slop is rejected (exit 3) so nothing posts in AI voice.
  - **Config-driven routing:** reuses the `services:` map (repoPath/baseBranch/developerAgent/reviewerAgent)
    and a new `bitbucket:` (alias `codeReview:`) host block in `agents.config.example.yaml`. Nothing is
    hard-coded; the Bitbucket Server/DC REST adapter is the reference, with GitHub/GitLab/Bitbucket-Cloud
    documented in `references/other-hosts.md`.
- **OVERVIEW/README updated** with the new skill, the skill count (11), and a PR-comment-loop flow diagram.
- **New `slop-guard-hook.py` — tool-layer enforcement of the humanize gate.** A `preToolUse` hook that
  denies a PR-comment POST made with a raw HTTP client (`curl`/`gh api`/`glab api`/`Invoke-RestMethod`/
  `urllib`) that bypasses the gated `pr_comments.py`, and denies `--no-lint` on a real `reply`/`comment`.
  Covers GitHub, GitLab, and Bitbucket; read-only comment GETs stay allowed. Deny-only + fail-open, stdlib
  only. Registered in `hooks.example.json`, shipped by `install_to_copilot.py --hooks`, documented in
  `hooks/README.md`, and covered by `tests/test_slop_guard.py`.

## [1.13.0] - 2026-09-30

### Added
- **New `workflow-state` skill — deterministic per-run state with resume-time re-validation.** Adds
  `skills/workflow-state/workflow_state.py` (stdlib-only CLI) + `SKILL.md`. State is stored per repo at
  `.git/copilot-workflow-state.json` and each record is stamped with the git commit it was made at.
  Subcommands: `set-plan`, `set-gate`, `set-review`, `set-evidence`, `set-stage`, `set-terminal`, `get`,
  `gate-status`, `drift`, `governance-check`, `validate`. It provides:
  - **Scope-drift detection:** `drift` warns when changed files fall outside the recorded plan.
  - **Stale-evidence / review anchoring:** `set-review`/`set-evidence` auto-stamp `HEAD`; `validate` flags
    them STALE once `HEAD` moves past that commit **or** the working tree has uncommitted edits.
  - **Safe resume:** `validate` re-checks saved gate/review/evidence against the live repo — a restored
    *chat* is not a restored *work state*.
  - **Explicit terminal/block states:** `set-terminal` records an outcome from a fixed enum (`SUCCESS`,
    `FAILED`, `BLOCKED`, `NEEDS_CLARIFICATION`, `NEEDS_AUTHORIZATION`, `NEEDS_HUMAN_REVIEW`,
    `TECHNICAL_BLOCK`, `POLICY_BLOCK`, `ENVIRONMENT_FAILURE`, …).
  - **Governance warn:** `governance-check` flags edits to governance files (`*.agent.md`, policy,
    `agents.config.yaml`) — **warn-only**, never blocks.
- **Push-guard soft gate-status block.** `hooks/push-guard-hook.py` now also denies a `git push` when the
  repo's recorded `workflow-state` gate is `FAIL`. Deliberately **soft** — a missing record (ad-hoc push)
  or a `PASS` gate is allowed, so repos that don't use workflow-state are unaffected.
- Tests: `tests/test_workflow_state.py` (15 tests: core CLI + push-guard gate block + regressions for
  non-ASCII paths, Windows `-C` path extraction, corrupt-state handling, and uncommitted-edit freshness).

### Changed
- `agents/orchestrators/feature-orchestrator.agent.md` — wired `workflow-state` into Operational
  Hardening (validate-first-on-resume, plan/gate/review/evidence stamping, drift-before-push,
  set-terminal, and record `set-gate --status FAIL` on a blocked gate).
- Docs: `evidence-discipline/SKILL.md` (commit-anchoring note), `git-push-guard/SKILL.md` (soft
  gate-status block), `hooks/README.md`, `README.md` (skills table), `OVERVIEW.md` (skill list + hook
  bullet).

## [1.12.1] - 2026-08-12

### Fixed
- **Clarified `--html-report` is not a `copilot` CLI flag (docs-only fix, no behavior change).** The
  1.12.0 docs implied `--html-report=true` could be passed as a top-level CLI argument, which fails with
  `error: unknown option` since the CLI only accepts its own fixed option set. Corrected `code-reviewer`,
  `security-reviewer`, `review-orchestrator`, and `README.md` to show the argument embedded inside the
  quoted `--prompt`/`-p` text instead.

## [1.12.0] - 2026-08-12

### Added
- **Opt-in HTML report for all reviewer agents (default `false`).** `code-reviewer`,
  `security-reviewer`, and `review-orchestrator` now support a `--html-report` boolean argument
  (default `false`): findings are always returned as text, and an HTML file is written to
  `<outputs.reportsRoot>/<component>/...-report.html` only when the caller explicitly passes
  `--html-report=true`. Documented in each agent file, `README.md`, and
  `agents.config.example.yaml`.

## [1.11.0] - 2026-08-11

### Added
- **Lesson provenance + validation lifecycle (guards against propagating wrong learnings).**
  `capture_learning.py` now records `validated: false` on every new capture plus optional
  `--agent` and `--confidence` (low/medium/high) provenance. `episodic_recall.py` marks
  un-curated lessons `[unverified]` and prints an advisory count, so future sessions treat raw
  inbox entries as hints — not authority — until `kb-curate` re-verifies them. `kb-curate` gains
  an explicit "validate the learning inbox" step (confirm → set `validated: true` / promote, or
  delete if wrong). Backward-compatible: entries without the field recall as `[unverified]`.

### Changed
- **Honest enforcement wording (docs + docstrings only, no logic change).** Replaced overclaims
  ("the model can't skip", "100% code-proven", "runtime enforcement") across `OVERVIEW.md`,
  `README.md`, `hooks/README.md`, `skills/learning-capture` and the hook docstrings with accurate
  language: hooks are **defense-in-depth and deliberately fail-open** (bypass-resistance depends on
  coverage; server-side protection is the backstop), and a resolvable source anchor proves the
  source *exists*, not that the conclusion is correct. Added a **Known limitations & deliberate
  trade-offs** section to `OVERVIEW.md`.

## [1.10.1] - 2026-08-11

### Added
- **`OVERVIEW.md` — one-page concept map.** A single-page guide to every orchestrator,
  developer/reviewer pair, skill and guardrail hook, so new users get the big picture
  without reading the full README. Linked from the README header. Docs-only; no agent,
  skill, hook or installer behaviour changed.

## [1.10.0] - 2026-08-11

### Added
- **Always-on session learning loop for plain CLI sessions.** New
  `instructions/learning-loop.instructions.md` is installed into
  `~/.copilot/instructions/` (loaded by the CLI in *every* session, any folder) so a
  top-level session — not just developer/reviewer sub-agents — captures a durable,
  code-proven lesson when it learns something about product code, build/config,
  logging/telemetry, or an investigation method. It writes only when the fact
  generalizes AND has a real `file:line`/command/log anchor, and stays silent
  otherwise. `install_to_copilot.py` gained `_install_instructions()` +
  `_read_kb_root()` to substitute `__KB_ROOT__` with the live `kbRoot`; new
  `TestInstructionsInstall` tests.

## [1.9.0] - 2026-08-11

### Added
- **Auto-capture of learning on review sessions.** The reviewers (`code-reviewer`, `security-reviewer`) and
  `review-orchestrator` now carry a mandatory LEARN step invoking `learning-capture` — previously only
  developer agents + `feature-orchestrator` did, so review/investigation sessions captured nothing.
  Reviewers stay read-only toward the reviewed repo; the KB ledger is an external append.
- **KB scope rule** in `skills/learning-capture/SKILL.md`: the project `kbRoot` captures only product
  lessons; agent-kit self-refinement (agents/skills/hooks/scripts) is out of scope and belongs in a separate
  kit ledger.
- **Evidence hardening — code-proven only.** `capture_learning.py` now validates every `--source` file
  anchor (bare `File.ext:line` OR path-qualified, by known code extension): each named code file must resolve
  to an existing file (absolute, under `--source-root`/cwd, or via a bounded pruned search for bare names) or
  the capture is rejected (exit 2), killing fabricated/stale citations — including a fabricated anchor paired
  with a real one (all anchors must resolve). Commands, log lines and URLs (no file anchor) remain allowed.
  New `--source-root` (resolve relative anchors for no-cd agents) and `--no-verify-source` flags. SKILL.md
  adds an OBSERVED-only guardrail (feature *understanding* is an answer, not a lesson → `--none`). New
  `TestSourceVerification` tests.

## [1.8.0] - 2026-08-11

### Added
- **Self-learning loop enforcement** (`skills/agent-preflight-check/preflight_gate.py`). The `learning-capture`
  step could be silently skipped — if `<kbRoot>/lessons-log.jsonl` is never created, captured lessons never
  accumulate and `episodic_recall` has nothing to serve. The preflight gate (run at task START,
  advisory-only, always exit 0) now reads `kbRoot` and reports a learning-ledger health field in its stamp:
  `learn: ok | no-log | stale(Nd) | empty | unconfigured`. `no-log` (capture never ran) and `stale(>7d)`
  emit an advisory so a skipped LEARN step is impossible to miss at the next task start — the same
  retrospective-visibility model as the existing budget/model checks. New `LEARN_STALE_DAYS = 7` constant.
- **Config**: `agents/agents.config.example.yaml` gains a `learning.kbRoot` key so the gate can locate the
  ledger.
- **Docs**: `skills/agent-preflight-check/SKILL.md` handoff-stamp contract documents the new `learn:` field
  and distinguishes it from the task-END `learned:` field emitted by `learning-capture`.
- **Tests**: `tests/test_preflight_gate.py` — 5 new cases (`no-log`, `ok-when-fresh`, `stale`, `unconfigured`,
  `empty`); `setUp` now seeds `learning.kbRoot`.

## [1.7.0] - 2026-08-11

### Added
- **Push-guard per-repo base-branch protection is now auto-wired at install** (`scripts/install_to_copilot.py`,
  `hooks/push-guard-hook.py`). The hook already protected each repo's configured `baseBranch` (not just the
  hard-coded `main`/`master`) whenever `PUSH_GUARD_CONFIG` pointed at the per-repo config — but `--hooks`
  shipped that env value blank, so the feature was effectively off. `install --hooks` now rewrites the
  installed `kit-hooks.json` so `PUSH_GUARD_CONFIG` points at the live `agents.config.yaml` when it exists
  (new `_write_hook_config` helper). Effect: a repo whose base is `develop`/`release/x` is protected
  automatically; config can only ADD protection, never weaken the unconditional `main`/`master` block; a
  missing/unreadable/malformed config falls back to a blank value + verbatim copy so install never breaks.
- **Tests**: `tests/test_push_guard.py` — new `HookPerRepoBaseBranch` class (configured `develop` denied for
  the matching repo incl. force/refs-prefix normalization; allowed without config; does not leak to another
  repo; `main`/`master` still protected with a config present). `tests/test_install_and_check.py` — new
  `TestHookConfigWiring` class (wires path when config present; blank when absent; malformed source falls
  back to copy).

## [1.6.0] - 2026-08-11

### Changed
- **Reviewer read-only execution boundary made explicit** (`code-reviewer`, `security-reviewer`): added a
  "Read-only execution boundary" clause stating reviewers MAY run only read-only deterministic gates
  (lint / format-check in check mode) and MUST NOT run unit/integration tests or any build that
  executes/compiles code — test adequacy and buildability are assessed statically from the diff; live
  test/build runs are deferred to the developer agent or CI. No tool grants changed — behavior guidance
  only, closing a wording ambiguity that could be misread as permitting test/build execution.

## [1.5.1] - 2026-08-11

### Fixed
- **git-push-guard refspec/force-prefix bypass** (`hooks/push-guard-hook.py` +
  `skills/git-push-guard/push_guard.py`): the guard compared the raw remote-side refspec token to
  `{main, master, baseBranch}` without normalizing, so `git push origin refs/heads/main`,
  `git push origin HEAD:refs/heads/main`, and force pushes `git push origin +main` /
  `+refs/heads/main` all wrote to a protected branch **undetected**. Both files now normalize the
  target — strip a leading force `+` and a `refs/heads/` prefix — before the protected-set check.
  Verified: all four bypass variants (and `--force`/`-f`, `git -C . push`, `:main` delete,
  `--all`/`--mirror`) are now denied, while task/feature branches (incl. `mainline`,
  `refs/heads/feature/x`) stay allowed. Added `tests/test_push_guard.py` (13 cases) — previously
  there was **no** push-guard test coverage. Note: indirection via shell substitution
  (`$(...)`, backticks, `$VAR`) cannot be resolved by a static parser and remains covered only by
  server-side branch protection.

## [1.5.0] - 2026-08-11

### Added
- **Shell trust-boundary enforcement hook** (`hooks/shell-guard-hook.py`): a `preToolUse` +
  `subagentStart`/`subagentStop` hook that promotes the reviewer read-only guarantee and the
  `shell(git:*)` prose to runtime enforcement the model cannot skip. **Layer A** (all agents) denies any
  shell command that references a secret path (`mcp-config.json`, `.secrets/`, `*.token`/`*.pem`/`*.key`) —
  closing the first step of the "hostile diff → shell → read secrets → exfiltrate" path. **Layer B**
  (read-only agents: reviewers + read-only orchestrators, tracked via a per-session marker written by
  `subagentStart` and cleared by `subagentStop`) additionally denies file-mutating shell
  (`Set-Content`/`Out-File`/`rm`/`mv`/`sed -i`/`>` redirect/`git commit`\|`apply`\|`reset`…), while keeping
  read-only gates (`git diff`/`log`/`show`, `npm run lint`, `eslint`) allowed. Intentionally **not**
  "git-only" so reviewers' non-git lint tooling keeps working. Fail-open (always exits 0, deny-only via JSON
  body, `|| exit 0` in the registration) so a mis-correlation degrades to a recoverable false-deny, never a
  session lock-out. Ships offline unit tests (`tests/test_shell_guard.py`).
- Registered the shell-guard hook in `hooks.example.json` (`preToolUse` + `subagentStart`/`subagentStop`).

### Changed
- `install_to_copilot.py --hooks` now ships **both** guard hooks (`push-guard-hook.py` +
  `shell-guard-hook.py`) and writes the combined registration to `kit-hooks.json` (removing the legacy
  single-purpose `push-guard.json` to avoid double-registration).
- `hooks/README.md` documents the shell-guard hook, its two layers, the subagent→session correlation, and
  the live-verification procedure.

## [1.4.0] - 2026-08-11

### Added
- **Portable capability check that travels with the pack** (`scripts/capability_check.py`): a
  self-contained, layout-agnostic drift check that classifies each agent's role by NAME, so it runs
  against both the kit's `agents/<role>/` tree and the flattened `~/.copilot/agents/` install recipients
  receive and hand-edit. Asserts reviewers hold no mutation/delegation tools, developers keep
  `edit`/`task`, and the quality-loop-harness still declares a numeric loop cap. Added as a `verify.py`
  stage.
- **Rotating live backups + post-install verification** in `install_to_copilot.py`: snapshots the current
  live agents+skills into `~/.copilot/.install-backups/<timestamp>/` (keeping the **newest 3**) before
  overwriting, then ships `capability_check.py` into `~/.copilot/scripts/` and runs it against the freshly
  installed agents. `--no-backup` / `--no-verify` opt out. Drift insurance now travels with the
  distributed pack, with easy rollback.

## [1.3.0] - 2026-08-11

### Added
- **Capability-contract test** (`tests/test_capability_contract.py`): locks each agent's
  runtime-enforced `tools:` grant. A frozen per-agent snapshot fails CI on ANY drift (added/removed
  tool, or a new agent not opted into the contract), and independent role invariants forbid reviewers
  and orchestrators from holding mutation/delegation tools and require developers to keep `edit`/`task`.
  A hand-edit that widens an agent's privilege now fails the gate instead of shipping silently.
- **One-command verify gate** (`scripts/verify.py`): chains agent lint → unit + capability-contract
  tests → behavior eval (if the kit ships one) and fails on the first breakage. Makes the harness
  distributable — anyone who receives the kit can prove it sound with one command, not just CI.

### Changed
- CI (`.github/workflows/validate.yml`) now runs the single `scripts/verify.py` gate in place of
  separate lint/test steps, so agent-file changes are gated on the full harness.
- `CONTRIBUTING.md` / `README.md`: document `verify.py` as the pre-commit / pre-distribution gate and
  the capability contract.

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
