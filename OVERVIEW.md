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

## Guards (hooks) — policy enforced at the CLI tool boundary

Registered on the CLI's `preToolUse` + subagent events, so they run without the agent's cooperation.
They are **defense-in-depth and deliberately fail-open**: on a crash, an unparseable payload, or an
execution path they don't cover (e.g. a push from inside a script, or a non-`git` client), they **allow**
rather than block — the `git-push-guard` skill and server-side branch protection are the backstops. Treat
them as a strong safety layer, **not an absolute sandbox**; bypass-resistance depends on tool/lifecycle
coverage.

- `push-guard-hook.py` — blocks a confirmed `git push` to a repo's protected base branch (config-driven, not just `main`).
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

- **Evidence:** every lesson must cite verifiable repository evidence — a capture whose source anchor doesn't resolve is rejected. (A resolvable anchor proves the *source exists*, not that the conclusion drawn from it is correct — reasoning still needs judgment.)
- **Learning:** developers, reviewers, orchestrators **and** any plain session capture durable lessons.
- **Cost:** metrics logged per handoff for weekly trend review.
- **Review boundary:** reviewers deliver findings only. The CLI removes their `edit`/`create` tools; a fail-open hook additionally blocks workspace mutation via the shell while a reviewer is active (see *Known limitations*).

## Known limitations & deliberate trade-offs

Honest boundaries — stated so intent is never mistaken for a hard guarantee:

- **Hooks are fail-open, not fail-closed.** They add friction and cover the common paths; a determined or prompt-injected agent on an uncovered path can get through. The real backstops are server-side branch protection, repo permissions, and (ideally) a restricted execution environment — the model is *not* the security boundary.
- **Reviewer read-only is enforcement-after-the-fact, not capability isolation.** Reviewers still hold the shell tool; mutation is blocked by a hook, not by withholding the capability. A hard capability sandbox would be stronger.
- **Model independence is limited.** If the developer and reviewer use the same model family, they can share blind spots. The deterministic checks (build/test/lint) are the judgment-independent verification layer.
- **Model tiering is a hypothesis unless you measure it.** Assigning a stronger model to higher-risk components is a reasonable default; justify it with your own defect/escape metrics.
- **The learning loop can propagate a wrong inference.** Anchor validation proves the source exists, not that the lesson's conclusion is right. Lessons now carry `confidence` and a `validated` flag (new captures are `validated: false` and surface as `[unverified]` in recall until kb-curate re-verifies them), but there is still no automatic expiry/revalidation. Treat the KB as *advisory memory*, not authority.
- **Per-domain agents are a deliberate choice.** Keep separate agent identities only where behavior genuinely differs (domain reasoning, model tier, routing); fold purely shared behavior into skills rather than duplicating it.
- **Local push-guard is not a substitute for server-side protection.** Pair it with server-side branch protection and repository permissions.
