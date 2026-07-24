# 04 — Gotchas

> Known pitfalls and their fixes. One entry per gotcha. Add to this page whenever an agent hits a
> surprise that the next task should avoid.

## Template

### <short title of the gotcha>
- **Symptom:** what you see (error message, wrong behavior).
- **Cause:** the underlying reason.
- **Fix:** the exact steps/command to resolve or avoid it.
- _Added: YYYY-MM-DD_

## Examples

### Full test suite is slow — use targeted runs
- **Symptom:** running the whole suite takes many minutes per loop iteration.
- **Cause:** the suite includes slow integration tests unrelated to most changes.
- **Fix:** run the targeted test for the changed module; run the full suite only before final handoff.
- _Added: YYYY-MM-DD_

### Private registry auth required before install
- **Symptom:** dependency install fails with 401/403.
- **Cause:** the package registry requires an auth token not set in a fresh shell.
- **Fix:** export `<TOKEN_VAR>` (see 02-build-test-commands) before installing.
- _Added: YYYY-MM-DD_

_Last updated: YYYY-MM-DD_
