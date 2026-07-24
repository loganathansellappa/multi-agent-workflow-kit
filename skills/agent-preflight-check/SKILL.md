---
name: agent-preflight-check
description: "Run a fast environment/repo/tooling preflight before implementation or review work to reduce avoidable failures."
---

# agent-preflight-check

Use this skill at the start of every task.

## Advisory gate (deterministic, informs — run FIRST)

Before any other work, run the bundled gate script and **read its advisories**. The check is made in
code, not by model judgment, but it is advisory only: it never blocks the task — it surfaces the
model/budget signals so the agent (or developer) can make an informed choice:

```
python <copilot-skills-dir>/agent-preflight-check/preflight_gate.py \
  --config <path to agents.config.yaml> \
  --agent <this agent's name> --model <active model id> --tier <trivial|standard|complex> \
  [--estimated-tokens <n>]
```

Exit-code contract (advisory):
- `0` → always, when the check ran. Any `info:` lines printed are advisories — heed them, but they do
  not block. Typical advisories: a standard-tier role on a premium model (~5x cost), or today's rolling
  spend having passed the daily budget ("enough for today — consider deferring non-urgent work").
- `4` → usage/config error (missing config / bad invocation). This is a setup bug, not governance —
  fix the invocation or config.

The gate never hard-stops. `--override` silences the advisories entirely. Agents listed under
`models.exempt` in the config are intentionally exempt and produce no model/budget advisories.
The sections below (tiering, model-routing, budget) document what the gate reports, and remain the
guidance an agent applies when the script cannot be run (e.g. no shell available).

## Objective

Catch environment and configuration problems early (paths, tools, repo access, branch readiness,
config files) before expensive work begins.

## Standard checks

1. Confirm required repo path(s) from `agents.config.yaml` exist and are readable.
2. Confirm required tools for the scope are available (for example: `git`, and whatever your stack
   uses to build/test/lint — `npm`/`yarn`, `python`, `go`, `dotnet`, `make`, etc.).
3. Confirm required config files exist (for example `AGENTS.md`, `agents.config.yaml`, repo-specific
   instruction files).
4. Confirm git readiness (fetch works, branch reference can be resolved when review/build needs it).
5. Confirm output/report directories are writable when report generation is part of the workflow.

## Output contract

- If checks pass: provide concise ready status and continue.
- If checks fail: stop and return a blocked status with: blocker, impact, owner, exact next action.

Do not continue into implementation/review loops when preflight has blocking failures.

## Task tier (classify first — drives everything below)

Classify the task before loading heavy skills or code:

- **trivial** — single file/module, no cross-repo/contract impact, no new deps (e.g. copy tweak,
  one-line fix, doc edit).
- **standard** — one component, multiple files, or a scoped feature within a module.
- **complex** — cross-component/cross-repo, contract/version impact, concurrency/perf/security-sensitive,
  or ambiguous scope.

Emit `tier: <trivial|standard|complex>` in the handoff stamp.

## Downstream skill gating (tiered fixed-cost control)

Load only what the tier needs — do not pay the full hardening tax on trivial work:

| Skill | trivial | standard | complex |
|---|---|---|---|
| `untrusted-input-guard` (security, cheap) | always | always | always |
| `agent-preflight-check` (this gate) | always | always | always |
| `quality-loop-harness` | skip | invoke | invoke |
| `delivery-metrics-capture` | lightweight line (below) | invoke | invoke |

For **trivial** tasks, skip `quality-loop-harness` and, instead of the full metrics skill, append ONE
minimal line to `outputs.metricsLog` so cost data is never lost:
`{"ts":"...","agent":"<name>","task_type":"...","tier":"trivial","tokens":"<n|unknown>"}`.

## Model-routing assertion (the 5× lever — driven by config, surfaced advisory)

Per-agent model bindings are declarative: they live in `agents.config.yaml` under `models.agents`
(each agent mapped to `premium` or `standard`) with a `models.exempt` list for personal agents, and are
written into the Copilot store by `scripts/apply_model_routing.py`. This check surfaces mismatches:

1. State the **active model** you are running on.
2. Determine this agent's tier from `models.agents` (fallback if unmapped: reviewers/orchestrators =
   standard). Agents in `models.exempt` are exempt — do not tier or flag them.
3. Assert:
   - Standard-tier role on a premium (Opus-class) model → emit `model: mismatch` and recommend
     `/model` (repo/local) or `/subagents`, or re-run `apply_model_routing.py`. Advisory only — surfaces
     the ~5× cost signal, does not block.
   - Premium-tier agent on a cheap model for a complex task → recommend escalation (quality risk). Advisory only.
   - Otherwise → `model: ok`.

## Budget / alert (close the cost loop)

Turn metrics from logging into governance:

1. Read `outputs.budgets` from `agents.config.yaml` (`perTaskTokenCeiling`, `rollingDailyTokenBudget`).
   If unset, use defaults `perTaskTokenCeiling=150000`, `rollingDailyTokenBudget=4000000` and note `budget: defaults`.
2. Read `outputs.metricsLog`; sum today's `tokens` for a rolling total.
3. Assert:
   - Rolling total already over `rollingDailyTokenBudget` → `budget: over (rolling)` — "enough for
     today": prefer cheap tier, minimize scope, or defer non-urgent work. Advisory only.
   - This task is projected to exceed `perTaskTokenCeiling` → `budget: near (task ceiling)` and consider
     tightening scope. Advisory only.
   - Otherwise → `budget: ok`.

## Token & cost discipline (every task)

Optimize for the fewest tokens that preserve context and quality:

- **Batch** independent tool calls in one turn (parallel reads/searches); don't serialize what can run together.
- **Never re-read** a file already read this session; reference what you have.
- **Scope validation** to the smallest command that proves the change (targeted build/test/lint over
  full-suite) unless a full run is justified.
- **Cheapest sufficient model:** reviewers, triage, routing, preflight, and routine implementation run
  on the standard tier; escalate to a high-capability model only for deep reasoning. See README "Model tiering".
- **Bound parallelism** when fanning out sub-agents (cap in-flight work) to avoid rate-limit/cost spikes.
- **Load context on demand** — only the files the current scope needs, never bulk reads.

## Autonomy defaults (reduce human round-trips)

- Proceed on safe, reversible inferences; do not ask for confirmation on low-risk decisions.
- Use `ask_user` only when genuinely blocked: missing acceptance criteria, ambiguous scope with
  materially different outcomes, or destructive/irreversible actions.
- On recoverable failures (build error, lint failure, missing dep after a manifest change), self-recover
  through the loop before escalating.
- State assumptions explicitly in the plan instead of pausing to confirm each one.

## Handoff evidence stamp (reliability)

At handoff, emit one concise line so gate execution is auditable, e.g.:
`preflight: ok | tier: standard | model: ok | budget: ok | loops: <n> | verify: pass/blocked | guard: on | metrics: written`.
This turns soft "invoke skill" instructions into a verifiable signal.
