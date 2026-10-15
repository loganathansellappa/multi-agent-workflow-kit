# docs/pr-post-gate — opt-in PR comment posting (animated)

[![PR post gate walkthrough](poster.png)](pr-post-gate.mp4)

A short animated walkthrough of how `review-orchestrator` posts PR comments
**only** when you opt in and approve the exact text:

1. Default review is read-only. Posting is OFF.
2. `--post-comments=true` in *your* prompt enables staging + approval (never auto-post).
3. `pr_comments.py stage` humanizes, lints (slophound, no hyphens/semicolons) and
   grounds the text, then prints an `APPROVAL_TOKEN`.
4. `ask_user` shows you the exact text with one boolean field and no default.
5. Your `true` answer makes `pr-post-gate-hook.py` mint a one-use, 15-minute,
   target-bound HMAC grant.
6. `pr_comments.py reply|comment --approval <t>` consumes the grant and posts the staged text.
7. Bypasses (raw `curl`/`gh api`/`az` writes, forged or replayed tokens, edited text,
   agent self-opt-in, reading the signing key) are denied.

## Files

| File | What it is |
|------|------------|
| `pr-post-gate.html` | The animation. Self-contained, autoplays in a browser. |
| `pr-post-gate.mp4`  | Rendered 1080p H.264 (≈1:30, 30fps, silent, on-screen captions). |
| `poster.png`        | Still frame used as the README thumbnail. |

## Re-render

Same protocol as [`docs/flow`](../flow/README.md) (`window.__ready` / `__start()` /
`__done`). Use the `record.mjs` snippet from that README:

```
node record.mjs pr-post-gate.html pr-post-gate.mp4
ffmpeg -y -ss 40 -i pr-post-gate.mp4 -frames:v 1 poster.png
```
