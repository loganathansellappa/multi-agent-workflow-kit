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
- **No auto-push.** Agents create local commits only; a human reviews and pushes.

## Hardening when you adapt the kit

- Keep the CI leak-scan (in `.github/workflows/validate.yml`) enabled so company/personal strings and a real
  `agents.config.yaml` can't be committed accidentally.
- Review any new tool grants you add to an agent's `tools` list.
