# Setup

The skill reads one config file (default `~/.copilot/agents/agents.config.yaml`).
Two things go in it: a host block for code review, and the `services:` map you already
keep for agent routing.

## 1. Services map (reused, not new)

Each slug maps to a service you already define. The skill reuses that entry for the
local `repoPath`, the `baseBranch`, and the owning `developerAgent` / `reviewerAgent`:

```yaml
services:
  service-a:
    repoPath: <REPO_ROOT>/service-a
    baseBranch: main
    developerAgent: backend-developer
    reviewerAgent: code-reviewer
```

## 2. Host block for code review

Add a `bitbucket:` block (alias `codeReview:` also works). `repositories[]` maps each
PR slug to one of the services above:

```yaml
bitbucket:
  baseUrl: https://code.example.com         # your code-review host
  tokenFile: ~/.copilot/.secrets/code-review.token
  insecureTls: false                        # true only for a self-signed dev host
  repositories:
    - slug: service-a                        # the repo slug in PR URLs
      project: TEAM                          # project/namespace key on the host
      service: service-a                     # -> services.service-a above
```

## 3. Token file

Put a read/write API token (scoped to PR comments) in the `tokenFile` path, one line,
no quotes. Keep the file out of version control (for example under a gitignored
`.secrets/` directory). The helper reads it at runtime and never prints or commits it.

## 4. First-run check

```
python pr_comments.py list --slug service-a --pr <an-open-pr-id>
```

You should get JSON with the PR branch, the resolved `repoPath` / `baseBranch`, the
routed `developerAgent`, and the open comments. If the base URL or token is
wrong, the helper fails fast with the host error: fix the config, do not guess.

Install `slophound` so the humanize gate is active:

```
pipx install slophound    # or: uv tool install slophound
```

Without it, posts still go through (the gate skips with a note), but you lose the
AI-voice check.
