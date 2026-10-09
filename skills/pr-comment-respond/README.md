# PR Comment Respond

Respond to pull-request review comments two ways, from inside your agent session,
without posting text that reads like a bot.

- **Inbound:** reviewers commented on a PR you authored. The skill lists the open
  comments, checks each against the real code, routes the fix to the
  component's developer agent, then replies and resolves behind a confirmation gate.
- **Authoring:** you were asked to review a PR/branch/diff. The skill reviews it,
  shows you the findings first, and posts comments only after you confirm.

Every reply and comment passes a **humanize gate**: AI punctuation tells are
stripped and the text is linted with [slophound](https://github.com/JosXa/slophound)
before anything is posted. Slop is rejected, so review feedback stays in a human
voice.

## Why

Review loops stall in two places: addressing the comments on your own PR, and
posting a review you already did. This skill closes both loops in-session and keeps
the routing config-driven (it reuses the `services:` map you already maintain), so
there is nothing component-specific baked into the skill.

## Setup

No install step. Point your agent at `SKILL.md`; see the repo root `README.md` for
per-platform wiring (Copilot, Claude, OpenCode).

Then configure one host block for code review and a token file: see
[`references/setup.md`](references/setup.md). The reference adapter speaks Bitbucket
Server / Data Center REST; [`references/other-hosts.md`](references/other-hosts.md)
covers GitHub, GitLab, and Bitbucket Cloud.

Requirements: Python 3, `pyyaml`, and (recommended) `slophound` on `PATH` so the
humanize gate is active.

## Usage

```
python pr_comments.py list     --slug <slug> --pr <id> [--all]
python pr_comments.py reply     --slug <slug> --pr <id> --comment <cid> --text "..."
python pr_comments.py comment   --slug <slug> --pr <id> [--path F --line N] --text "..."
python pr_comments.py resolve   --slug <slug> --pr <id> --comment <cid>
python pr_comments.py humanize  --text "..."
```

In practice you invoke the skill in natural language ("address the comments on my
PR 1234 in service-a", "review PR 88 in service-b and post the findings"). The agent
runs the helper for you. See [`examples/`](examples) for fictional walkthroughs.
