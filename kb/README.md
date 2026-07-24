# Knowledge Base (KB)

A **knowledge base** is durable, project-specific memory your agents read *before* they plan or code.
It is what turns a generic template into an agent that already knows your build commands, conventions,
and landmines — so it stops rediscovering the same facts (and burning tokens) on every task.

## Why a KB

- **Speed & cost** — agents read a few curated pages instead of re-crawling the repo each time.
- **Consistency** — every agent follows the same conventions and uses the same commands.
- **Fewer failures** — known gotchas are documented once and avoided forever.

## How agents use it

1. On start, an agent reads only `00-index.md` (the small map).
2. From the task, it loads *only* the KB pages relevant to the affected area — never the whole KB.
3. As it learns something durable (a new command, a fixed gotcha), it writes it back during the
   `LEARN` step of the lifecycle. The KB is living memory, not a one-time doc.

## Layout

Keep pages small and single-purpose so agents can load just what they need:

```
kb/
  README.md            <- this file
  example/             <- a starter skeleton you copy and fill in
    00-index.md        <- the map: what each page covers (read first)
    01-project-map.md  <- components, repos, ownership, how they connect
    02-build-test-commands.md  <- exact build/test/lint commands per component
    03-conventions.md  <- coding standards, naming, PR/commit rules
    04-gotchas.md      <- known pitfalls and their fixes
```

## Getting started

1. Copy `example/` to your own KB folder (e.g. `kb/myproject/`).
2. Fill each page with real, verified facts about *your* project.
3. Register any new pages in `00-index.md` so agents can find them.
4. Reference the KB root from your agents (e.g. "read `kb/myproject/00-index.md` first").

Keep entries short, dated, and verified. A KB full of stale guesses is worse than no KB.
