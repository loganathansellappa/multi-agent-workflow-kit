#!/usr/bin/env python3
"""preToolUse + subagent hooks: enforce a shell TRUST BOUNDARY the model can't skip.

WHY THIS EXISTS
---------------
The reviewer read-only guarantee is only *half* enforced by the runtime: the CLI
strips reviewers of the `edit`/`create` tools (real), but every agent — reviewers
included — still holds the full, unrestricted `powershell`/`bash` tool. The
`shell(git:*)` line in the agent files is prose with no runtime effect. So a
prompt-injected or simply-misbehaving reviewer analysing a hostile diff can, with
one shell line, (a) read the configured secrets and exfiltrate them, or
(b) mutate the workspace despite lacking the `edit` tool. This hook closes both.

It is the enforcement half of the same pattern as push-guard: a `preToolUse` hook
the CLI runs before every shell tool call, plus `subagentStart`/`subagentStop`
handlers that track which read-only agent is active.

TWO LAYERS
----------
  A. Secret/exfil guard (ALL agents, no identity needed): deny any shell statement
     that references a known secret path (mcp-config.json, .secrets/, *.token/
     *.pem/*.key). Reading those into a shell command is never legitimate agent
     behaviour and is the first step of the mapped exfiltration path.
  B. Read-only-agent mutation guard (reviewers/read-only orchestrators): while such
     an agent is the active subagent, additionally deny file-mutating shell
     (Set-Content/Out-File/rm/mv/sed -i/`>` redirection/git commit|apply|reset|...).
     Reviewers keep their read-only gates (git diff/log, npm lint, eslint).

HOW IDENTITY IS TRACKED (preToolUse carries NO agent name)
----------------------------------------------------------
The `preToolUse` payload has only sessionId/cwd/toolName/toolArgs — no agent name.
So `subagentStart` (which DOES carry agentName + sessionId) writes a marker file
`<hooks>/.readonly-sessions/<sessionId>` when a read-only agent starts, and
`subagentStop` removes it. `preToolUse` looks up its own sessionId to know whether
layer B applies. If subagents share the parent sessionId, the marker simply covers
the reviewer's active window (cleared on stop) — still correct, and deny-only +
fail-open means a misfire is at worst a recoverable false-deny, never a lock-out.

FAIL BEHAVIOR
-------------
Command preToolUse hooks are FAIL-CLOSED on crash/non-zero exit, but this script
ALWAYS exits 0 and expresses allow/deny only in the JSON body (same discipline as
push-guard). Anything it cannot positively identify as a violation is ALLOWED. The
installed command also appends `|| exit 0` so even "python not found" fails open.

Stdlib only; cross-platform; no network; never executes the inspected command.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

MARKER_DIR = Path(__file__).resolve().parent / ".readonly-sessions"

# Layer A — secret paths that must never appear in a shell command (any agent).
SECRET_PATTERNS = [
    re.compile(r"mcp-config\.json", re.I),
    re.compile(r"\.secrets\b", re.I),
    re.compile(r"\.(token|pem|key)(['\"\s;|&>]|$)", re.I),
]

# Layer B — mutation stems denied while a read-only agent is active.
MUTATION_PATTERNS = [
    re.compile(r"(?i)\b(Set-Content|Add-Content|Clear-Content|Out-File|New-Item|"
               r"Remove-Item|Move-Item|Rename-Item|Set-ItemProperty|Tee-Object)\b"),
    re.compile(r"(?i)(^|[\s;&|])(rm|mv|cp|tee|truncate|dd|shred|install)\s"),
    re.compile(r"(?i)\bsed\b[^\n]*\s-i\b"),                       # in-place sed
    re.compile(r"(?i)\bgit\s+(?:-[^\s]+\s+)*"
               r"(commit|apply|reset|restore|checkout|clean|add|rm|stash|mv)\b"),
    re.compile(r"(?i)\bpatch\b\s"),
    re.compile(r">>?(?![&])\s*[\"']?[\w./\\~$-]"),                # `>`/`>>` redirect to a file
]

# Read-only agents (layer B applies). Classified by NAME so it survives flattening.
READONLY_ORCHESTRATORS = {
    "review-orchestrator", "feature-orchestrator",
}


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


def is_readonly_agent(name):
    if not name:
        return False
    return name.endswith("-reviewer") or name in READONLY_ORCHESTRATORS


def _marker(session_id):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id))
    return MARKER_DIR / safe


def handle_subagent_start(payload):
    """Mark this session read-only if a read-only agent just started."""
    try:
        if is_readonly_agent(payload.get("agentName")) and payload.get("sessionId"):
            MARKER_DIR.mkdir(parents=True, exist_ok=True)
            _marker(payload["sessionId"]).write_text(payload.get("agentName", ""), encoding="utf-8")
    except Exception:
        pass
    allow()  # subagentStart output can't block anyway; never interfere.


def handle_subagent_stop(payload):
    try:
        sid = payload.get("sessionId")
        if sid:
            m = _marker(sid)
            if m.exists():
                m.unlink()
    except Exception:
        pass
    allow()


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
    return [s.strip() for s in re.split(r"&&|\|\||;|\||\n", cmd) if s.strip()]


def secret_violation(cmd):
    for pat in SECRET_PATTERNS:
        if pat.search(cmd):
            return pat.pattern
    return None


def mutation_violation(cmd):
    for stmt in split_statements(cmd):
        for pat in MUTATION_PATTERNS:
            if pat.search(stmt):
                return stmt
    return None


def session_is_readonly(payload):
    sid = payload.get("sessionId")
    try:
        return bool(sid) and _marker(sid).exists()
    except Exception:
        return False


def handle_pre_tool_use(payload):
    cmd = extract_command(payload)
    if not cmd:
        allow()

    # Layer A — secret/exfil guard for every agent.
    hit = secret_violation(cmd)
    if hit:
        deny(
            "shell-guard: refusing a shell command that references a secret path "
            f"(matched /{hit}/). Secrets are provided via environment variables and "
            "must never be read through the shell. If this is a false positive, run "
            "the command without touching secret files."
        )

    # Layer B — mutation guard while a read-only agent is active.
    if session_is_readonly(payload):
        stmt = mutation_violation(cmd)
        if stmt:
            deny(
                "shell-guard: this read-only agent (reviewer/orchestrator) may not "
                f"mutate the workspace via the shell. Denied statement: {stmt!r}. "
                "Read-only inspection (git diff/log/show, npm lint, eslint) is "
                "allowed; file writes/commits are not. Hand the change to a developer agent."
            )
    allow()


def main():
    payload = read_payload()
    # Dispatch on payload shape: subagentStop has agentId/response; subagentStart has
    # agentName (no toolName); preToolUse has toolName.
    if payload.get("agentId") or "response" in payload or payload.get("stopReason"):
        handle_subagent_stop(payload)
    elif payload.get("agentName") and not payload.get("toolName"):
        handle_subagent_start(payload)
    elif payload.get("toolName") or payload.get("toolArgs") is not None or \
            any(k in payload for k in ("toolArguments", "arguments", "command")):
        handle_pre_tool_use(payload)
    else:
        allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Last resort: never brick a tool call because the guard errored.
        allow()
