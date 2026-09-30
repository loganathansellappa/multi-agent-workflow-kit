#!/usr/bin/env python3
"""preToolUse hook: block `git push` to a protected branch at the TOOL LAYER.

WHAT THIS IS
------------
This is the *enforcement* half of the git-push-guard defense-in-depth pair:

  * skills/git-push-guard/  -> the *advisory* half. An agent is told to run it
    before pushing. It only protects if the agent remembers to invoke it.
  * hooks/push-guard-hook.py (this file) -> the *enforcement* half. The Copilot
    CLI runs it automatically before EVERY shell tool call (bash/powershell),
    regardless of what the agent "decides". It runs without the agent's
    cooperation, but is deliberately fail-open (see FAIL BEHAVIOR below) — strong
    defense-in-depth, not an absolute barrier.

WHY A HOOK (and not just the skill)
-----------------------------------
A skill is guidance the agent chooses to follow. A `preToolUse` hook is a
runtime interceptor the CLI invokes on its own. Wiring the guard as a hook turns
"the agent should not push to main" (instruction) into "the tool call to push to
main is denied" (enforcement) — closing the opt-in gap.

HOW THE CLI CALLS IT
--------------------
The CLI pipes a JSON payload for the pending tool call on stdin and reads a JSON
decision on stdout:

  * ALLOW  -> print `{}` and exit 0 (tool proceeds through normal permission flow)
  * DENY   -> print `{"permissionDecision":"deny","permissionDecisionReason":...}`
              and exit 0 (the decision lives in the JSON, not the exit code)

FAIL BEHAVIOR (important)
-------------------------
Command `preToolUse` hooks are FAIL-CLOSED in the CLI: a crash or non-zero exit
denies the pending tool. Because this hook matches ALL shell calls, an
uncaught error here could block every shell command in the session. To stay
safe AND available, this script:

  1. Wraps everything in try/except and ALWAYS exits 0. Allow/deny is expressed
     only through the JSON body, never the exit code.
  2. Only ever DENIES a *confirmed* push to a protected branch. Anything it
     cannot positively identify as such (non-push commands, unparseable input,
     internal errors) is ALLOWED — the git-push-guard skill and server-side
     branch protection remain as backstops.

The installed hook command also appends `|| exit 0` (see hooks.example.json) so
that even "python not found" fails open rather than bricking the whole session.

Protected set: {main, master} always, plus the repo's configured `baseBranch`
from agents.config.yaml when PUSH_GUARD_CONFIG points at it (config can
only ADD protection, never remove it). This mirrors
skills/git-push-guard/push_guard.py.

SOFT GATE-STATUS BLOCK (workflow-state integration)
---------------------------------------------------
Beyond protected branches, this hook also denies a push whose repo has a
workflow-state record with a gate status of FAIL (written by
skills/workflow-state/workflow_state.py). This is deliberately SOFT: if no
workflow-state file exists (ad-hoc push) or the gate is PASS/unknown, the push
is allowed. So the two block conditions are: (a) target is a protected branch,
OR (b) a gate is recorded AND it is FAIL. Everything else is allowed.

Stdlib only; cross-platform; no network. Never runs `git push` itself.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

WORKFLOW_STATE_BASENAME = "copilot-workflow-state.json"

# Always-protected branch names, regardless of any config. Unconditional.
HARD_PROTECTED = {"main", "master"}


def _emit(decision):
    """Write the single final decision JSON and exit 0.

    `decision` is either {} (allow -> normal permission flow) or a deny object.
    Exit code is always 0 so the CLI never fail-closes on our behalf; the
    allow/deny signal is carried entirely by the JSON body.
    """
    sys.stdout.write(json.dumps(decision))
    sys.exit(0)


def allow():
    _emit({})


def deny(reason):
    _emit({"permissionDecision": "deny", "permissionDecisionReason": reason})


def read_payload():
    """Parse the tool-call payload from stdin. Returns {} on any failure so a
    malformed payload fails OPEN (we can't confirm a protected push)."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _collect_strings(obj, out):
    """Recursively gather every string value in the payload. We don't rely on a
    single known key name for the command, so we scan defensively."""
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out)


def extract_command(payload):
    """Best-effort extraction of the shell command text from the payload.
    Prefers the conventional argument keys, then falls back to scanning."""
    args = None
    for key in ("toolArguments", "arguments", "toolArgs", "args", "input"):
        if isinstance(payload.get(key), (dict, str)):
            args = payload[key]
            break
    if isinstance(args, dict):
        for k in ("command", "script", "cmd", "commandLine"):
            if isinstance(args.get(k), str) and args[k].strip():
                return args[k]
    if isinstance(args, str) and args.strip():
        return args
    # Last resort: join every string in the payload and let the git-push
    # detector decide. A false match only causes a harmless extra check.
    collected = []
    _collect_strings(payload, collected)
    return "\n".join(collected)


def extract_cwd(payload):
    for key in ("cwd", "workingDirectory", "workdir", "directory"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    args = payload.get("toolArguments") or payload.get("arguments")
    if isinstance(args, dict) and isinstance(args.get("cwd"), str):
        return args["cwd"]
    return os.getcwd()


def split_statements(cmd):
    """Split a shell command into statements on &&, ||, ;, |, and newlines so we
    inspect each `git push` invocation in a compound command separately."""
    return [s.strip() for s in re.split(r"&&|\|\||;|\||\n", cmd) if s.strip()]


def tokenize(statement):
    try:
        return shlex.split(statement, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — fall back to whitespace split so we never crash.
        return statement.split()


def is_git_push(tokens):
    """True when the statement is a `git ... push` (git subcommand 'push'),
    tolerating global options like `-C <path>` / `-c key=val` before 'push'."""
    if "git" not in tokens or "push" not in tokens:
        return False
    return tokens.index("push") > tokens.index("git")


def repo_from_tokens(tokens, default_cwd):
    """Honor `git -C <path> push ...` so the branch is resolved in the right repo."""
    for i, t in enumerate(tokens):
        if t == "-C" and i + 1 < len(tokens):
            return tokens[i + 1]
    return default_cwd


# Matches `-C <path>` in a raw statement: a double/single-quoted path, or a bare
# unquoted run of non-space chars. Captured from the RAW text (not shlex tokens)
# so Windows backslashes survive — shlex.split(posix=True) silently eats them,
# which would mangle the repo path and make protected-branch/config and gate
# lookups fail open (review F3).
_DASH_C_RE = re.compile(r"(?:^|\s)-C\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))")


def repo_from_statement(statement, tokens, default_cwd):
    """Resolve the repo path for a `git -C <path> push`, preserving backslashes.

    Extract `-C <path>` from the raw statement first (so Windows paths are intact),
    then fall back to the shlex-token method, then the cwd."""
    m = _DASH_C_RE.search(statement)
    if m:
        return m.group(1) or m.group(2) or m.group(3)
    return repo_from_tokens(tokens, default_cwd)


def git_current_branch(repo):
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True, timeout=8,
        )
        return out.stdout.strip()
    except Exception:
        return None


def push_targets(tokens, repo):
    """Resolve the branch name(s) a `git push` statement would write to.

    Returns (targets, wildcard) where `wildcard` is True for --all/--mirror
    (which can push every branch, including protected ones, so must be blocked).
    """
    push_idx = tokens.index("push")
    args = tokens[push_idx + 1:]

    if any(a in ("--all", "--mirror") for a in args):
        return [], True

    positionals = [a for a in args if not a.startswith("-")]
    # First positional after `push` is the remote; the rest are refspecs.
    refspecs = positionals[1:] if positionals else []

    if not refspecs:
        # `git push` / `git push origin` / `git push -u origin HEAD` -> current branch.
        cur = git_current_branch(repo)
        return ([cur] if cur else [None]), False

    targets = []
    for spec in refspecs:
        # A leading '+' is the force-push marker and does not change the target.
        spec = spec.lstrip("+")
        # For `local:remote`, `HEAD:remote`, and `:remote` (delete), the branch
        # that gets written on the remote is the part after the colon.
        remote_side = spec.split(":")[-1] if ":" in spec else spec
        remote_side = _normalize_branch(remote_side)
        if remote_side.upper() == "HEAD" or remote_side == "":
            cur = git_current_branch(repo)
            targets.append(cur)
        else:
            targets.append(remote_side)
    return targets, False


def _normalize_branch(name):
    """Reduce a refspec's remote side to a bare branch name for comparison.

    Strips a leading force-push '+' and a fully-qualified `refs/heads/` prefix so
    that `+main`, `refs/heads/main`, and `HEAD:refs/heads/main` are all recognised
    as the protected branch `main`. Comparison itself is done case-insensitively by
    the caller."""
    if not name:
        return ""
    name = name.strip().lstrip("+")
    m = re.match(r"(?i)^refs/heads/(.+)$", name)
    if m:
        name = m.group(1)
    return name


def read_configured_base_branch(cfg_text, repo_path):
    """Mirror of skills/git-push-guard/push_guard.py: best-effort regex lookup of
    the `baseBranch` for the services.* entry whose `repoPath` matches repo_path.
    Kept self-contained (no import) so this hook is a single portable file that
    can never fail on a bad import path."""
    norm_target = str(Path(repo_path)).rstrip("\\/").lower()
    services_block_match = re.search(r"(?ms)^services:\s*.*?(?=^\S)", cfg_text)
    services_block = services_block_match.group(0) if services_block_match else cfg_text
    for service_match in re.finditer(
        r"(?m)^\s{2}([a-zA-Z0-9_-]+):\s*\n((?:^\s{4}.*\n?)+)", services_block
    ):
        body = service_match.group(2)
        repo_path_match = re.search(r"(?m)^\s{4}repoPath:\s*(.+?)\s*$", body)
        base_branch_match = re.search(r"(?m)^\s{4}baseBranch:\s*(.+?)\s*$", body)
        if repo_path_match and base_branch_match:
            candidate = str(Path(repo_path_match.group(1).strip())).rstrip("\\/").lower()
            if candidate == norm_target:
                return base_branch_match.group(1).strip()
    return None


def protected_set(repo):
    """{main, master} plus this repo's configured baseBranch when
    PUSH_GUARD_CONFIG is set and readable. Config only ADDS protection."""
    protected = set(HARD_PROTECTED)
    cfg = os.environ.get("PUSH_GUARD_CONFIG", "").strip()
    if cfg:
        try:
            base = read_configured_base_branch(Path(cfg).read_text(encoding="utf-8"), repo)
            if base:
                protected.add(base.strip().lower())
        except Exception:
            # A missing/unreadable config never weakens protection — the hard
            # names still apply.
            pass
    return protected


def gate_status(repo):
    """Read the workflow-state gate status for `repo`, or None when absent.

    Mirrors skills/workflow-state/workflow_state.py state location so the hook
    stays a single portable file with no import. Any failure -> None (soft:
    an unreadable/missing record never blocks an otherwise-allowed push).

    Note: this checks BOTH the .git/ location and the dotfile fallback, whereas
    the writer picks exactly one (.git/ when present). In the rare case a repo had
    a fallback file written pre-`git init` and was later `git init`'d, the hook
    may read a stale fallback the writer no longer updates; re-run `set-gate` to
    refresh the .git/ record."""
    try:
        repo_path = Path(repo)
        candidates = [repo_path / ".git" / WORKFLOW_STATE_BASENAME,
                      repo_path / ("." + WORKFLOW_STATE_BASENAME)]
        for p in candidates:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                gate = data.get("gate") or {}
                status = gate.get("status")
                return status.upper() if isinstance(status, str) else None
    except Exception:
        return None
    return None


def main():
    payload = read_payload()
    cmd = extract_command(payload)
    if not cmd or "push" not in cmd:
        allow()  # fast path: not a push at all

    cwd = extract_cwd(payload)

    for statement in split_statements(cmd):
        tokens = tokenize(statement)
        if not is_git_push(tokens):
            continue
        repo = repo_from_statement(statement, tokens, cwd)
        protected = protected_set(repo)

        targets, wildcard = push_targets(tokens, repo)
        if wildcard:
            deny(
                "git-push-guard: refusing `git push --all`/`--mirror` — it can push "
                "protected branches (main/master). Push a single task/feature branch "
                "with an explicit refspec instead."
            )
        for t in targets:
            if t and t.strip().lower() in protected:
                deny(
                    f"git-push-guard: refusing to push to protected branch '{t}'. "
                    f"Protected branches are {sorted(protected)}. Push your task/feature "
                    f"branch and open a pull request into '{t}' instead."
                )
            if t is None:
                # We matched a push but could not resolve its target branch.
                # Fail closed for pushes specifically (safer than allowing a
                # possibly-protected push we cannot verify).
                deny(
                    "git-push-guard: could not determine the target branch of this "
                    "`git push`. Re-run with an explicit refspec (e.g. `git push origin "
                    "<your-task-branch>`) so the guard can verify it is not protected."
                )
        # Soft gate-status block: only when a gate is RECORDED and it is FAIL.
        # No record (ad-hoc push) or PASS -> allowed.
        if gate_status(repo) == "FAIL":
            deny(
                "git-push-guard: refusing to push — the recorded workflow-state gate for "
                "this repo is FAIL. Fix the underlying gate, re-run the deterministic gate "
                "to PASS, then record it with `workflow_state.py --repo <repo> set-gate "
                "--status PASS` before pushing. (Do not just overwrite the record to bypass "
                "the block.)"
            )
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Absolute last resort: never brick a shell call because the guard errored.
        # We could not confirm a protected push, so fail open (the skill + server-side
        # protection remain as backstops).
        allow()
