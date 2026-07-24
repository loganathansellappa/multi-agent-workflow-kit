# 02 — Build / Test / Lint Commands

> The exact, copy-pasteable commands per component. Prefer the smallest targeted command that covers
> the changed scope; escalate to full-suite only when needed.

## serviceA

```bash
# build
<your build command>
# test (targeted)
<your test command for a single module/file>
# test (full suite)
<your full test command>
# lint
<your lint command>
```

## serviceB

```bash
<your build command>
<your test command>
<your lint command>
```

## serviceC (API contract)

```bash
# validate / lint the schema
<your schema lint command>
# regenerate consumers
<your codegen command>
```

## Notes

- Record env setup that trips people up (required toolchain versions, env vars, auth for private registries).
- If a command changed or a new one is needed, update this page during the `LEARN` step.

_Last updated: YYYY-MM-DD_
