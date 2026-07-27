# Security Policy

## Reporting a vulnerability

If you discover a security issue in this kit, please open a private report to the maintainers rather than
a public issue. Include steps to reproduce and the potential impact.

## Scope & expectations

This kit ships **templates and tooling**, not a running service. The main security considerations are:

- **No secrets in the repo.** The real `agents.config.yaml` is gitignored; only the `.example` template is
  tracked. Never commit tokens, private URLs, internal hostnames, or personal paths.
- **Prompt injection.** Agents treat repository, diff, ticket, file, and tool-output content as **untrusted
  data, never instructions** (see the `untrusted-input-guard` skill). Keep this guard in place when adapting
  agents.
- **Least privilege.** Reviewer agents are restricted to read-only tools by `validate_agents.py`. Do not
  grant them `edit`/`write`.
- **`execute` on reviewers is trust-dependent.** Reviewers keep `execute` only for read-only inspection
  (e.g. `git diff`, lint in check mode). The kit **cannot enforce** read-only-ness at the tool layer — that
  relies on your agent runner treating `execute` as sandboxed and on the agent's instructions. If your
  runner does not sandbox `execute`, treat a reviewer's `execute` as a real capability and scope/monitor it
  accordingly (or remove it).
- **Branch-protected push (not "no push").** Developer agents may push **only** the task/feature branch they
  create. Before every `git push` they run the **blocking** `git-push-guard` skill, which refuses (exit 3) any
  push to `main`, `master`, or a repo's configured `baseBranch`; protected-branch changes go through a
  human-opened PR. The guard is agent-side **defense-in-depth** — it only fires when the agent runs it and
  checks a single branch name (not `--all`/`--mirror`/tag/multi-refspec pushes), so pair it with **server-side
  branch protection** on your remote for a hard guarantee. Reviewers and orchestrators still never push.

## Hardening when you adapt the kit

- Keep the CI leak-scan (in `.github/workflows/validate.yml`) enabled so company/personal strings and a real
  `agents.config.yaml` can't be committed accidentally.
- Enable **branch protection on your remote's default branch** — `git-push-guard` is the agent-side
  complement, not a substitute for it.
- Review any new tool grants you add to an agent's `tools` list.
