# 03 — Conventions

> Coding standards, naming, and commit/PR rules agents must follow.

## Code style

- Formatter: `<tool>` — run `<command>` on changed files only.
- Linter: `<tool>` — fix all errors before finishing.
- Naming: `<your naming rules>`.

## Versioning (SemVer)

- **Patch** — non-functional (docs, comments, formatting), no contract change.
- **Minor** — backward-compatible additions (new optional fields, endpoints, enum values).
- **Major** — breaking changes (removed/renamed/required-tightened). Call these out explicitly.

## Branches & commits

- Branch from the component's base branch (see `agents.config.yaml`).
- Branch name: `<ticket-id>-<short-kebab-description>`.
- Commit subject: `[<ticket-id>] <message>`.
- **Never push** — agents create local commits only; a human pushes.

## Backward compatibility

- Prefer backward-compatible changes by default.
- A breaking change requires an explicit call-out and a major version bump.

_Last updated: YYYY-MM-DD_
