# 01 — Project Map

> What are the components, where do they live, and how do they talk to each other?

## Components

| Component | Path / repo | Tech | Developer agent | Reviewer agent |
| --- | --- | --- | --- | --- |
| serviceA | `<REPO_ROOT>/serviceA` | _e.g. server-side_ | `backend-developer` | `code-reviewer` |
| serviceB | `<REPO_ROOT>/serviceB` | _e.g. client/UI_ | `frontend-developer` | `code-reviewer` |
| serviceC | `<REPO_ROOT>/serviceC` | _e.g. API contract_ | `api-developer` | `code-reviewer` |

## How they connect

Describe the call paths and shared contracts, e.g.:

- serviceB calls serviceA over the API defined by serviceC.
- Contract changes start in serviceC (contract-first), then propagate to serviceA and serviceB.

```
serviceB (UI) ──HTTP──▶ serviceA (server)
      ▲                        ▲
      └──── contract (serviceC) ┘
```

## Ownership / boundaries

- One change starts in the smallest component that can satisfy it.
- Cross-component changes go through the `feature-orchestrator`, which delegates per component.

_Last updated: YYYY-MM-DD_
