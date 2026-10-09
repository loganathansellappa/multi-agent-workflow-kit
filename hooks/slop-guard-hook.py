#!/usr/bin/env python3
"""preToolUse hook: enforce the PR-comment HUMANIZE + GROUNDING gates at the tool layer (fail-open).

WHY THIS EXISTS
---------------
The `pr-comment-respond` skill posts every reply/comment through `pr_comments.py`,
which sanitizes the text (strips AI punctuation) and lints it with slophound before
it reaches the host. An instruction alone can be skipped: an agent could post a PR
comment by calling the host REST API directly (curl / Invoke-RestMethod / gh api /
glab api / inline python urllib) and never touch the gated script, or pass
`--no-lint` to opt out. This hook closes both gaps deterministically, for every
shell call in the session, without the agent's cooperation.

RULES (deny-only, fail-open)
----------------------------
  1. BYPASS: a shell statement that POSTs/PUTs/PATCHes to a pull-request comment
     endpoint on any supported host (GitHub, GitLab, Bitbucket Server/DC/Cloud)
     via a raw HTTP client, WITHOUT going through pr_comments.py, is denied. All
     PR comments must go through pr_comments.py so the gate always runs. Read-only
     GETs that list comments are allowed.
  2. OPT-OUT: pr_comments.py reply/comment invoked with --no-lint is denied, so the
     humanize/slophound gate cannot be switched off for a real post.
  2b. GROUNDING OPT-OUT: pr_comments.py reply/comment invoked with --no-ground is
     denied, so the grounding/target validity preflight (target exists and is open,
     every cited file/line/SHA is in this PR) cannot be switched off for a real post.

FAIL BEHAVIOR
-------------
Always exits 0; allow/deny is expressed only in the JSON body (same discipline as
shell-guard / push-guard). Anything not positively identified as a violation is
ALLOWED. Stdlib only; cross-platform; no network; never executes the command.
"""
import json
import re
import sys

# Pull-request comment endpoints across the supported hosts:
#   Bitbucket Server/DC + Cloud : pull-requests/<id>/comments , pullrequests/<id>/comments
#   GitHub review + general      : pulls/<id>/comments , issues/<id>/comments
#   GitLab notes + discussions   : merge_requests/<id>/notes , merge_requests/<id>/discussions
COMMENT_ENDPOINT = re.compile(
    r"(?i)(?:"
    r"pull-?requests?/[^/\s'\"]+/comments"
    r"|pulls/[^/\s'\"]+/comments"
    r"|issues/[^/\s'\"]+/comments"
    r"|merge_requests/[^/\s'\"]+/(?:notes|discussions)"
    r"|discussions/[^/\s'\"]+/notes"
    r")"
)
# Any raw HTTP client, plus the host CLIs that speak REST (gh api, glab api).
HTTP_CLIENT = re.compile(
    r"(?i)\b(curl|wget|Invoke-RestMethod|Invoke-WebRequest|iwr|irm|gh|glab)\b"
    r"|urllib\.request|requests\.(post|put|patch)|http\.client|HttpClient|System\.Net"
)
# Signals that the call writes rather than lists. Covers curl (-X/-d/--data),
# PowerShell (-Method/-Body), gh/glab api (-f/--field/-F/--input), and python.
POSTISH = re.compile(
    r"(?i)(-Method\s+(POST|PUT|PATCH)|-X\s+(POST|PUT|PATCH)|--request\s+(POST|PUT|PATCH)"
    r"|\bPOST\b|\bPUT\b|\bPATCH\b|(^|[\s;&|])-d\b|--data|-Body\b"
    r"|requests\.(post|put|patch)"
    r"|(^|[\s;&|])-f\b|--field\b|--input\b|(^|[\s;&|])-F\b)"
)
SANCTIONED = re.compile(r"pr_comments\.py", re.I)
NO_LINT = re.compile(r"(?i)pr_comments\.py\b[^\n]*\b(reply|comment)\b[^\n]*--no-lint")
NO_GROUND = re.compile(r"(?i)pr_comments\.py\b[^\n]*\b(reply|comment)\b[^\n]*--no-ground")


def _emit(decision):
    sys.stdout.write(json.dumps(decision))
    sys.exit(0)


def allow():
    _emit({})


def deny(reason):
    _emit({"permissionDecision": "deny", "permissionDecisionReason": reason})


def read_payload():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _collect_strings(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out)


def extract_command(payload):
    args = payload.get("toolArgs")
    if args is None:
        for key in ("toolArguments", "arguments", "args", "input"):
            if isinstance(payload.get(key), (dict, str)):
                args = payload[key]
                break
    if isinstance(args, dict):
        for k in ("command", "script", "cmd", "commandLine"):
            if isinstance(args.get(k), str) and args[k].strip():
                return args[k]
    if isinstance(args, str) and args.strip():
        return args
    collected = []
    _collect_strings(payload, collected)
    return "\n".join(collected)


def split_statements(cmd):
    return [s.strip() for s in re.split(r"&&|\|\||;|\n", cmd) if s.strip()]


def main():
    payload = read_payload()
    # Only inspect shell tool calls (preToolUse). Ignore subagent lifecycle events.
    if not (payload.get("toolName") or payload.get("toolArgs") is not None
            or any(k in payload for k in ("toolArguments", "arguments", "command"))):
        allow()
    cmd = extract_command(payload)
    if not cmd:
        allow()

    # Rule 2: never allow the gate to be switched off on a real post.
    if NO_LINT.search(cmd):
        deny(
            "slop-guard: --no-lint is not allowed on pr_comments.py reply/comment. "
            "PR comments must pass the humanize + slophound gate. Remove --no-lint; "
            "if slophound bites, rewrite the text plainer and more human, then retry."
        )

    # Rule 2b: never allow the grounding/target validity preflight to be switched off.
    if NO_GROUND.search(cmd):
        deny(
            "slop-guard: --no-ground is not allowed on pr_comments.py reply/comment. "
            "PR comments must pass the grounding preflight (target exists and is open, "
            "and every cited file/line/SHA is in this PR). Fix the references instead "
            "of bypassing the check; if a citation is genuinely intentional, a human "
            "should post it."
        )

    # Rule 1: block a raw comment POST/PUT/PATCH that bypasses the gated script.
    for stmt in split_statements(cmd):
        if (COMMENT_ENDPOINT.search(stmt) and HTTP_CLIENT.search(stmt)
                and POSTISH.search(stmt) and not SANCTIONED.search(stmt)):
            deny(
                "slop-guard: posting a PR comment with a raw HTTP client (curl / gh "
                "api / glab api / Invoke-RestMethod / urllib) bypasses the humanize + "
                "slophound gate. Route it through the sanctioned helper: "
                "python skills/pr-comment-respond/pr_comments.py "
                "reply|comment --slug <slug> --pr <id> [--comment <cid>] --text \"...\". "
                "It strips AI punctuation and rejects AI-sounding prose automatically."
            )
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        allow()  # never brick a tool call because the guard errored
