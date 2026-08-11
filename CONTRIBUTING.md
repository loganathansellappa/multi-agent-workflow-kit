# Contributing

Thanks for your interest in improving the Multi-Agent Workflow Kit! This is a generic, vendor-neutral
starter kit — contributions should keep it that way.

## Ground rules

- **Keep it generic.** No company names, private URLs, real service names, personal paths, or secrets.
  Use placeholders (`serviceA`, `<REPO_ROOT>`, `<REPORTS>`, `<ticket-id>`).
- **Stdlib only.** The Python tooling must run on a clean Python 3.8+ install with no third-party deps.
- **Cross-platform.** Scripts must work on Windows, macOS, and Linux.
- **Config formatting is significant.** To stay stdlib-only, the scripts parse `agents.config.yaml` with
  targeted **regexes, not a real YAML parser**. Keep the indentation and layout identical to
  `agents.config.example.yaml`: 2-space top-level keys, 4-space entries under `models:`, tiers on a single
  line (`premium: { model: X, effortLevel: high }`), and the `exempt: [...]` list inline. Reformatting
  (different indentation, mid-line comments, multi-line tiers) can make the parser match nothing and exit
  with `ERROR: no models.<...> found`. If you need richer config, add a real parser behind an optional
  dependency rather than loosening the regexes silently.
- **Review agent/skill changes before you commit (mandatory gate — do not skip).** Any change to a
  `*.agent.md` or `skills/**/SKILL.md` must get a dedicated review pass before it lands: run it past a
  custom-agent / instruction reviewer if your setup ships one, otherwise self-review against the "Adding or
  changing an agent" checklist below, and resolve any issue (contradictions, bloat, tool/model mismatch,
  reviewer scope-lock regressions) first. The `validate_agents.py` + test checks do NOT cover this. Apply it
  to EVERY agent/skill edit, including minor or wording-only changes.

## Before you open a PR

Run the gate locally — it must be green:

```bash
python scripts/verify.py     # chains validate_agents + run_tests (one command)
```

`scripts/verify.py` is the distributable gate: it runs the agent lint and the unit +
capability-contract tests (and a behavior eval if the kit ships one) and fails on the first breakage.
Run it before opening a PR and before handing the kit to another developer. The CI workflow runs the
same `verify.py`.

**Agent-review gate (mandatory).** These two checks do not review instruction quality. For any changed
`*.agent.md` or `skills/**/SKILL.md`, also run a review pass on the changes (a dedicated agent-instruction
reviewer if you have one, otherwise self-review against "Adding or changing an agent" below) and resolve
every issue before opening the PR — never skip it for minor/wording-only edits.

## Adding or changing an agent

- The file must be `name.agent.md` and its frontmatter `name:` must equal the filename basename.
- Include frontmatter `description`, `name`, and `tools`.
- Include exactly one `## Operational Hardening` section.
- Use the gate wording `0 Critical / 0 High / 0 Medium`.
- Reviewer agents (name contains `review`) must use **read-only** tools only — no `edit`/`write`.
- Only reference skills that actually ship in `skills/`.

## Adding a skill

- Create `skills/<name>/SKILL.md` with `name` and `description` frontmatter.
- Keep it model- and vendor-agnostic.

## Commits

- Use clear, descriptive commit messages.
- Bump `VERSION` and add a `CHANGELOG.md` entry for user-facing changes (SemVer).
