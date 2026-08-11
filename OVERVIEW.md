# Overview — the kit at a glance

A one-page map of what's in the kit. **Setup & deep dive?** see [README.md](README.md).

## What this is

A small team of specialised Copilot CLI **agents** that plan, implement and review changes, sharing a set
of **skills** (reusable rules) and protected by **guard hooks** and a **learning loop**. The agents ship as
adaptable *examples* — point them at your repos and tech stack. You invoke one agent for your component; it
resolves the repo from config (not your current folder) and runs the loop for you.

## The everyday loop

```
GOAL → PLAN → IMPLEMENT → BUILD+TEST → REVIEW → FIX ↺ (until clean: 0 Critical/High/Medium)
     → LEARN → COMMIT → PUSH (task branch only — never main/master)
```

Developers own the loop and call a read-only reviewer. Opening the PR stays with you.

## Agents (7 examples — adapt to your stack)

**Orchestrators** — route work, own the loop:
| Agent | Use it for |
|---|---|
| `feature-orchestrator` | Triages a request, plans scope, delegates each component to a developer, runs integration review. Delegates all edits. |
| `review-orchestrator` | Groups a change set by component, routes each to the right reviewer, aggregates one report. Never edits. |

**Developers** — implement & loop to a clean gate, then a local commit:
| Agent | Use it for |
|---|---|
| `backend-developer` | Server-side changes end-to-end (adapt tech specifics to your stack). |
| `frontend-developer` | Client/UI changes end-to-end (adapt to your framework). |
| `api-developer` | Contract-first API changes (OpenAPI/GraphQL/proto): change schema → version → propagate to consumers. |

**Reviewers** — read-only findings, never modify code:
| Agent | Use it for |
|---|---|
| `code-reviewer` | Staged/unstaged/branch diffs: confirmed bugs, regressions, design issues with `file:line` + fixes, plus a low-confidence list. |
| `security-reviewer` | Auth / input-handling / crypto / untrusted-data changes: confirmed exploitable vulnerabilities with evidence. |

## Skills (9) — reusable rules any agent can invoke

**Guardrails / discipline**
- `agent-preflight-check` — fast env/repo/tooling preflight **+ recall of past lessons** before work starts.
- `evidence-discipline` — label every claim OBSERVED vs INFERRED with a `file:line`/log/command cite.
- `untrusted-input-guard` — treat repo/diff/ticket/tool output as **data, not instructions** (anti prompt-injection).
- `git-push-guard` — refuse pushes to a repo's protected base branch; allow task branches.

**Review**
- `review-findings-output` — findings contract: severity buckets + `file:line` evidence + a concrete fix.
- `quality-loop-harness` — the build/verify/review/fix loop with explicit gates and a **loop cap**.

**Learning / metrics**
- `learning-capture` — capture durable, source-cited lessons to the KB (the *write* side of the loop).
- `kb-curate` — consolidate/prune the KB (a linter gives the signal; an agent edits — run manually).
- `delivery-metrics-capture` — log per-task metrics (tier, loop count, verification, learned, tokens).

## Guards (hooks) — runtime-enforced, the model can't skip them

- `push-guard-hook.py` — blocks `git push` to a repo's protected base branch (config-driven, not just `main`).
- `shell-guard-hook.py` — enforces the reviewer **read-only shell boundary** (blocks secret-file reads / workspace mutation even though reviewers still hold a shell).
- `hooks.example.json` — registers both guards on the CLI's `preToolUse` + subagent lifecycle events.

## Where things live

| Thing | Location |
|---|---|
| Config (repos, models) | `~/.copilot/agents/agents.config.yaml` |
| Lessons (KB) | `kbRoot` from your config |
| Review reports | your configured reports path |
| Always-on session rules | `~/.copilot/instructions/*.instructions.md` |

## Governance in one line each

- **Evidence:** lessons are code-proven — a capture with a non-existent source anchor is rejected.
- **Learning:** developers, reviewers, orchestrators **and** any plain session capture durable lessons.
- **Cost:** metrics logged per handoff for weekly trend review.
- **Review boundary:** reviewers deliver findings only — no edit/commit/push (hook-enforced).
