#!/usr/bin/env python3
"""PR post gate: deterministic opt-in + exact-text human approval for PR comments.

WHY THIS EXISTS
---------------
Agent instructions ("only post after the user says yes") can be skipped or
self-satisfied: the old flow minted the approval token at `stage`, before any human
saw the text, so an agent could approve itself. Reviews must also stay read-only
even when a reviewer subagent runs under its own session id. This hook moves both
guarantees to the tool layer, where the agent cannot opt out: only a real ask_user
answer from the human produces a grant, and only the human's own prompt can turn
posting on.

WHAT THIS ENFORCES
------------------
1. Posting from a review (pr-review-orchestrator / review-orchestrator, or any
   reviewer it spawns) is OFF
   by default. It is enabled only when `--post-comments=true` appears in the
   review agent's prompt AND that flag traces back to a prompt the human actually
   typed (an agent cannot opt itself in by adding the flag to a `task` prompt).
   Opt-in enables staging + approval only. Nothing is ever posted automatically.
2. Every PR comment (any session) needs a one-use, target-bound GRANT that only this
   hook mints, and only after it has seen, in postToolUse, that the user answered an
   `ask_user` form that showed the exact staged text and returned `post_<token>=post`
   (or `true` for legacy boolean forms). Edit and Skip never mint a grant.
   Editing discards the old stage; replacement text must be staged and approved again.
   `stage` alone no longer authorises anything. Grants are HMAC-signed, bound to
   slug/PR/kind/comment/path/line/text hash/session, expire after 15 min, and are
   consumed atomically by pr_comments.py (replay is refused via a used-token ledger).
3. Comment text rules (deterministic): humanized (humanize() is idempotent on it),
   no hyphen or dash characters and no semicolons outside `code`, and slophound must
   be installed and pass. Missing slophound fails CLOSED.
   New comments require an explicit diff file and positive line. Generic posting
   is disabled; failed anchors must never degrade into PR-level comments.
4. Direct-write bypasses are denied: gh/glab comment commands, MCP comment-writing
   tools, raw HTTP writes to comment endpoints (no exemption for commands that
   merely mention pr_comments.py), importing pr_comments internals, and touching
   the approval/grant state or its signing key.
5. Review sessions stay read-only: no git commit/push/merge/rebase/reset/etc, no
   gh/glab merge or PR mutation, no edit/create tools, no shell writes into the
   configured repos or the agent kit, no resolve/delete of threads.

MODES (argv[1])
---------------
  pre           global preToolUse            (all tools)
  post          global postToolUse           (ask_user + shell tools)
  prompt        global userPromptSubmitted   (lineage + human opt-in)
  review-prompt orchestrator-scoped userPromptSubmitted (marks review session)
  review-pre    orchestrator-scoped preToolUse          (pre, forced review mode)

pr_comments.py imports this file (by path) for humanize(), check_text(),
verify_grant() and consume_grant() so there is exactly one implementation.

FAIL BEHAVIOR
-------------
Always exits 0; the decision is in the JSON body. Unrelated tool calls fail OPEN on
an internal error. Anything that looks like a PR comment action fails CLOSED. A hook
timeout is fail-open in the CLI, which is safe here because pr_comments.py
re-verifies the grant and text rules itself and only this hook can mint grants.
Stdlib only; no network; never executes the inspected command.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

COPILOT_HOME = Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".copilot"))
STATE = COPILOT_HOME / "hooks" / ".pr-post-gate"
APPROVALS = COPILOT_HOME / ".pr-approvals"
KEY_FILE = COPILOT_HOME / "hooks" / ".secrets" / "pr-post-gate.key"
# Repo checkouts that review sessions must not write into (repoPath entries).
CONFIGS = (COPILOT_HOME / "agents" / "service-path.config.yaml",
           COPILOT_HOME / "agents" / "agents.config.yaml")
LEDGER = APPROVALS / "used-tokens.txt"

STAGE_TTL = 900
GRANT_TTL = 900
LAUNCH_TTL = 600
SESSION_TTL = 12 * 3600

OPT_IN = re.compile(r"(?i)(?<![\w-])--post-comments=true(?![\w-])")
TOKEN_RE = r"[0-9a-f]{16,64}"
APPROVAL_MARK = re.compile(r"PR_POST_APPROVAL\s+(" + TOKEN_RE + r")")

ORCHESTRATORS = {"pr-review-orchestrator", "review-orchestrator"}
SHELL_TOOLS = {"bash", "powershell", "shell", "execute"}
EDIT_TOOLS = {"edit", "create", "apply_patch", "str_replace_editor", "write", "multi_edit"}
BUILTIN_TOOLS = SHELL_TOOLS | EDIT_TOOLS | {
    "view", "read", "grep", "glob", "rg", "task", "ask_user", "skill", "sql",
    "web_fetch", "web_search", "read_powershell", "write_powershell", "stop_powershell",
    "list_powershell", "read_agent", "write_agent", "list_agents", "report_intent",
    "update_todo", "fetch_copilot_cli_documentation", "session_store_sql",
}

# Paths an agent must never touch with any tool: grant/approval state and the key.
PROTECTED = re.compile(r"(?i)\.pr-approvals|\.pr-post-gate|pr-post-gate\.key")

# Hyphen/dash family and semicolon family, checked outside `code`.
HYPHENS = re.compile("[\u002D\u00AD\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]")
SEMIS = re.compile("[;\u037E\u204F\uFF1B]")

# ---------------------------------------------------------------- text rules
_SMART = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u201a": "'", "\u201e": '"', "\u2026": "...", "\u00a0": " ", "\u2022": "*",
}
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U00002190-\U000021FF\U0000FE0F]"
)


def humanize(text: str) -> str:
    """Deterministic cleanup of mechanical AI tells. Idempotent."""
    for k, v in _SMART.items():
        text = text.replace(k, v)
    text = _EMOJI.sub("", text)
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)        # em/en dash aside
    text = re.sub(r"(?<=\S) -+ (?=\S)", ", ", text)            # spaced hyphen aside
    text = re.sub(r"(?m)^(\s*)[-+] (?=\S)", r"\1* ", text)     # list bullets
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r",\s*,", ",", text)
    return text.strip()


def _strip_code(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    return re.sub(r"`[^`\n]*`", " ", text)


def slophound(text: str):
    exe = shutil.which("slophound")
    if not exe:
        return False, "slophound is not installed. Install it; posting fails closed without it."
    try:
        p = subprocess.run([exe, "--formal", "--no-footer", "-"], input=text,
                           capture_output=True, text=True, encoding="utf-8", timeout=20)
    except Exception as e:  # noqa: BLE001
        return False, f"slophound could not run ({e}); posting fails closed."
    if p.returncode == 0:
        return True, ""
    if p.returncode == 2:
        return False, "slophound failed to run; posting fails closed."
    return False, (p.stdout or p.stderr).strip()


def check_text(text: str, run_slophound: bool = True) -> list:
    """Return a list of problems. Empty list means the text may be posted."""
    problems = []
    if not text or not text.strip():
        return ["text is empty"]
    if humanize(text) != text:
        problems.append("text is not humanized (run it through pr_comments.py stage)")
    prose = _strip_code(text)
    if HYPHENS.search(prose):
        problems.append("contains a hyphen or dash. Rewrite without '-' (wrap identifiers "
                        "like TEAM-123 or file-names in `backticks`, use 'to' instead of '->')")
    if SEMIS.search(prose):
        problems.append("contains a semicolon. Split into two sentences or use a comma")
    if run_slophound and not problems:
        ok, report = slophound(text)
        if not ok:
            problems.append("slophound rejected the text as AI sounding. Rewrite it plainer:\n"
                            + report)
    return problems


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- state helpers
def _safe(name) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))[:120]


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


def session_rec(sid):
    rec = _read_json(STATE / "sessions" / f"{_safe(sid)}.json") if sid else None
    if rec and time.time() - rec.get("ts", 0) > SESSION_TTL:
        return None
    return rec


def review_marker(sid):
    rec = _read_json(STATE / "review" / f"{_safe(sid)}.json") if sid else None
    if rec and time.time() - rec.get("ts", 0) > SESSION_TTL:
        return None
    return rec


def chain(sid):
    """Session ids from sid up to its root (sid first)."""
    out, cur = [], sid
    for _ in range(12):
        if not cur or cur in out:
            break
        out.append(cur)
        rec = session_rec(cur)
        cur = rec.get("parent") if rec else None
    return out


def is_review_agent(name) -> bool:
    return bool(name) and (name in ORCHESTRATORS or name.endswith("-reviewer")
                           or name == "provider-review")


def is_review_session(sid) -> bool:
    for s in chain(sid):
        if review_marker(s):
            return True
        rec = session_rec(s) or {}
        if is_review_agent(rec.get("agentType")):
            return True
    return False


def effective_opt_in(sid) -> bool:
    """Own prompt carried the flag AND the root prompt (typed by the human) did too."""
    ids = chain(sid)
    if not ids:
        return False
    own = (session_rec(sid) or {}).get("promptFlag") or (review_marker(sid) or {}).get("promptFlag")
    root = session_rec(ids[-1]) or {}
    return bool(own) and bool(root.get("userOptIn")) and not root.get("parent")


# ---------------------------------------------------------------- grants
def _key() -> bytes:
    try:
        k = KEY_FILE.read_bytes().strip()
        if len(k) >= 32:
            return k
    except FileNotFoundError:
        pass
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    k = secrets.token_hex(32).encode()
    try:
        fd = os.open(str(KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        return k
    except FileExistsError:
        return KEY_FILE.read_bytes().strip()


_GRANT_FIELDS = ("token", "slug", "pr", "kind", "comment", "path", "line", "hash", "sid", "exp")


def _mac(g: dict) -> str:
    body = json.dumps({f: g.get(f) for f in _GRANT_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()


def _valid_token(token) -> bool:
    return bool(re.fullmatch(TOKEN_RE, token or ""))


def staged_rec(token):
    if not _valid_token(token):
        return None, "invalid token format"
    rec = _read_json(APPROVALS / f"{token}.json")
    if not rec:
        return None, f"no staged text for token {token}. Run pr_comments.py stage first"
    if time.time() - int(rec.get("created", 0)) > STAGE_TTL:
        return None, "staged text expired. Re-stage"
    if text_hash(rec.get("text", "")) != rec.get("hash"):
        return None, "staged text failed its integrity check. Re-stage"
    return rec, ""


def token_used(token) -> bool:
    try:
        return token in LEDGER.read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return False


def mint_grant(token, rec, sid) -> dict:
    g = {"token": token, "slug": rec.get("slug"), "pr": int(rec.get("pr")),
         "kind": rec.get("kind"), "comment": rec.get("comment"), "path": rec.get("path"),
         "line": rec.get("line"), "hash": rec.get("hash"), "sid": sid,
         "exp": int(time.time()) + GRANT_TTL}
    g["mac"] = _mac(g)
    _write_json(APPROVALS / f"{token}.grant.json", g)
    return g


def _target_ok(g, slug, pr, kind, comment, path, line):
    if g.get("slug") != slug or int(g.get("pr", -1)) != int(pr) or g.get("kind") != kind:
        return "grant is for a different PR or comment kind"
    if kind == "reply" and str(g.get("comment")) != str(comment):
        return "grant is for a different comment thread"
    if kind == "inline":
        gl = g.get("line")
        if g.get("path") != path or (int(gl) if gl is not None else None) != \
                (int(line) if line is not None else None):
            return "grant is for a different file or line"
    return ""


def verify_grant(token, slug, pr, kind, comment=None, path=None, line=None, sid=None,
                 grant=None):
    """(ok, reason). sid=None skips the session binding (pr_comments.py side)."""
    if not _valid_token(token):
        return False, "invalid approval token format"
    if token_used(token):
        return False, "approval token was already used. Re-stage and ask the user again"
    g = grant if grant is not None else _read_json(APPROVALS / f"{token}.grant.json")
    if not g:
        return False, ("no user approval grant for this token. Show the exact staged text "
                       "with ask_user (see pr_comments.py stage output) and wait for yes")
    if not hmac.compare_digest(str(g.get("mac", "")), _mac(g)):
        return False, "approval grant signature is invalid (forged or tampered)"
    if g.get("token") != token:
        return False, "approval grant does not belong to this token"
    if int(g.get("exp", 0)) < time.time():
        return False, "approval grant expired. Re-stage and ask the user again"
    if sid is not None and g.get("sid") != sid:
        return False, "approval grant was issued to a different session"
    why = _target_ok(g, slug, pr, kind, comment, path, line)
    if why:
        return False, why
    rec, why = staged_rec(token)
    if not rec:
        return False, why
    if rec.get("hash") != g.get("hash"):
        return False, "staged text changed after approval. Re-stage and ask again"
    return True, ""


def consume_grant(token, slug, pr, kind, comment=None, path=None, line=None):
    """Atomically take the grant (one use) and verify it. Returns (ok, reason, staged)."""
    if not _valid_token(token):
        return False, "invalid approval token format", None
    src = APPROVALS / f"{token}.grant.json"
    taken = APPROVALS / f"{token}.grant.taken.{os.getpid()}"
    try:
        os.replace(src, taken)
    except FileNotFoundError:
        return False, "no unused approval grant for this token", None
    try:
        g = _read_json(taken)
        ok, why = verify_grant(token, slug, pr, kind, comment, path, line, grant=g)
        if not ok:
            return False, why, None
        rec, why = staged_rec(token)
        APPROVALS.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(token + "\n")
        return True, "", rec
    finally:
        try:
            taken.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- payload helpers
def _emit(d):
    sys.stdout.write(json.dumps(d))
    sys.exit(0)


def allow():
    _emit({})


def deny(reason):
    _emit({"permissionDecision": "deny", "permissionDecisionReason": "pr-post-gate: " + reason})


def _args(payload):
    a = payload.get("toolArgs", payload.get("tool_input"))
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            a = {"command": a}
    return a if isinstance(a, dict) else {}


def _collect(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)
    return out


def _sid(payload):
    return payload.get("sessionId") or payload.get("session_id")


def _tool(payload):
    return str(payload.get("toolName") or payload.get("tool_name") or "")


# ---------------------------------------------------------------- shell analysis
PRC_CALL = re.compile(r"""(?i)pr_comments\.py['"]?((?:\s+--config(?:=|\s+)\S+)?)\s+([a-z]+)""")
PRC_INTERNALS = re.compile(
    r"(?i)import\s+pr_comments|from\s+pr_comments|runpy|importlib[\s\S]*pr_comments"
    r"|\bpost_reply\b|\bpost_comment\b|_write_approval|_consume_approval|consume_grant"
    r"|mint_grant|_load_approval")
CLI_COMMENT = re.compile(
    r"(?i)\b(gh)\s+(pr|issue)\s+(comment|review)\b|\bglab\s+mr\s+(note|comment)\b"
    r"|\bglab\s+mr\s+(approve|revoke)\b|\bgh\s+pr\s+review\b")
GIT_MUT = re.compile(
    r"(?i)\bgit(?:\s+(?:-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)|-c\s+\S+|--[\w-]+(?:=\S+)?))*\s+"
    r"(commit|push|merge|rebase|cherry-pick|revert|tag|reset|checkout|switch|apply|am|add|rm|mv"
    r"|stash|clean|restore|pull|worktree|update-ref|branch\s+-[dDmMf]|notes|filter-branch|replace)\b")
FORGE_MUT = re.compile(
    r"(?i)\b(gh|glab)\s+(pr|mr)\s+(merge|close|reopen|edit|create|ready|revert|update|rebase)\b"
    r"|/merge\b[\s\S]*(-X\s*(PUT|POST)|-Method\s+(PUT|POST)|--request\s+(PUT|POST))"
    r"|(-X\s*(PUT|POST)|-Method\s+(PUT|POST)|--request\s+(PUT|POST))[\s\S]*/merge\b")
# Raw HTTP writes to a PR comment endpoint. Unlike slop-guard there is no exemption
# for commands that merely mention pr_comments.py.
RAW_ENDPOINT = re.compile(
    r"(?i)pull-?requests?/[^/\s'\"]+/comments|pulls/[^/\s'\"]+/(comments|reviews)"
    r"|issues/[^/\s'\"]+/comments|merge_requests/[^/\s'\"]+/(notes|discussions)"
    r"|discussions/[^/\s'\"]+/notes")
RAW_CLIENT = re.compile(
    r"(?i)\b(curl|wget|Invoke-RestMethod|Invoke-WebRequest|iwr|irm|gh\s+api|glab\s+api)\b"
    r"|urllib|requests\.|http\.client|HttpClient|System\.Net|WebClient|httpx|aiohttp")
RAW_WRITE = re.compile(
    r"(?i)\b(POST|PUT|PATCH|DELETE)\b|(^|\s)(-d|-f|-F)\b|--data|--field|--input|-Body\b"
    r"|\.(post|put|patch|delete)\(|urlopen\([^)]*data")
WRITE_VERB = re.compile(
    r"(?i)\b(Set-Content|Add-Content|Clear-Content|Out-File|New-Item|Remove-Item|Move-Item"
    r"|Copy-Item|Rename-Item|Tee-Object|WriteAllText|WriteAllBytes|AppendAllText)\b"
    r"|(^|[\s;&|])(rm|mv|cp|tee|truncate|dd|touch|install)\s|\bsed\b[^\n]*\s-i\b"
    r"|>>?(?![&])\s*(?!\$null\b|/dev/null\b|nul\b)[\"']?[\w./\\~$:-]|open\([^)]*['\"][wax]\+?b?['\"]|\.write_text\(|\.write_bytes\(")


def _flag(stmt, name):
    m = re.search(r"(?i)(?<![\w-])--" + name + r"(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)", stmt)
    return m.group(1).strip("\"'") if m else None


def prc_calls(cmd):
    calls = []
    for m in PRC_CALL.finditer(cmd):
        start = m.start()
        nxt = PRC_CALL.search(cmd, m.end())
        seg = cmd[start: nxt.start() if nxt else len(cmd)]
        calls.append((m.group(2).lower(), seg))
    return calls


def repo_paths():
    out = []
    for cfg in CONFIGS:
        try:
            txt = cfg.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in re.finditer(r"(?m)^\s*repoPath:\s*['\"]?(.+?)['\"]?\s*$", txt):
            p = m.group(1).strip().rstrip("\\/")
            if len(p) > 3:
                out.append(p)
    return out


def _norm(s):
    return s.replace("\\\\", "\\").replace("/", "\\").lower()


def writes_protected_tree(cmd):
    if not WRITE_VERB.search(cmd):
        return None
    n = _norm(cmd)
    targets = [_norm(p) for p in repo_paths()] + [_norm(str(COPILOT_HOME)), "\\.copilot\\"]
    for t in targets:
        if t and t in n:
            return t
    return None


def check_post_call(sub, seg, sid):
    slug, pr, tok = _flag(seg, "slug"), _flag(seg, "pr"), _flag(seg, "approval")
    if not tok:
        return "posting needs --approval <token> from stage plus the user's ask_user yes"
    if not slug or not pr or not pr.isdigit():
        return "posting needs explicit --slug and numeric --pr so the grant target can be checked"
    path, line = _flag(seg, "path"), _flag(seg, "line")
    if sub == "reply":
        kind, comment = "reply", _flag(seg, "comment")
    else:
        if not path or not line or not line.isdigit() or int(line) < 1:
            return "comments require --path and positive --line; generic comments are disabled"
        kind, comment = ("inline" if path else "general"), None
    if line is not None and not line.lstrip("-").isdigit():
        return "--line must be an integer"
    ok, why = verify_grant(tok, slug, int(pr), kind, comment, path,
                           int(line) if line is not None else None, sid=sid)
    return "" if ok else why


# ---------------------------------------------------------------- ask_user approval
def approval_request_problems(args, sid):
    """Validate an ask_user call that requests approval. Returns (tokens, problems)."""
    msg = str(args.get("message") or "")
    tokens = list(dict.fromkeys(APPROVAL_MARK.findall(msg)))
    if not tokens:
        return [], []
    probs = []
    schema = args.get("requestedSchema") or {}
    props = schema.get("properties") or {}
    batch = "post_comments" in props
    expected = {"post_comments"} if batch else {f"post_{t}" for t in tokens}
    if batch:
        field = props["post_comments"]
        if not isinstance(field, dict) or field.get("type") != "array" or \
                field.get("items") != {"type": "string", "enum": tokens} or \
                any(k in field for k in ("default", "minItems", "maxItems")):
            probs.append("post_comments must be an array with items type string and enum "
                         "equal to the displayed tokens in order, without defaults or selection limits")
    extra = [k for k in props if k not in expected]
    if extra:
        probs.append(f"approval form may only contain post_<token> fields, found {extra}")
    review = is_review_session(sid)
    if review and not effective_opt_in(sid):
        probs.append("this review run did not opt in. The human must include "
                     "--post-comments=true in the prompt")
    for t in tokens:
        f = props.get(f"post_{t}")
        choice = isinstance(f, dict) and f.get("type") == "string" \
            and f.get("enum") == ["post", "edit", "skip"] and "oneOf" not in f
        if batch:
            pass
        elif not isinstance(f, dict) or (f.get("type") != "boolean" and not choice):
            probs.append(f"field post_{t} must be boolean or a string with "
                         "enum [post, edit, skip]")
        elif "default" in f:
            probs.append(f"field post_{t} must not have a default (the human must choose)")
        rec, why = staged_rec(t)
        if not rec:
            probs.append(why)
            continue
        if rec.get("kind") == "general":
            probs.append("generic comments are disabled; stage an inline comment with a file and line")
        if batch:
            path, line = rec.get("path"), rec.get("line")
            if not path or not isinstance(line, int) or isinstance(line, bool) or line < 1:
                if rec.get("kind") != "reply":
                    probs.append("approval needs a valid file and line; re-stage it")
            target = (f"reply to {rec.get('comment')}" if rec.get("kind") == "reply"
                      else f"{path}:{line}")
            if target not in msg:
                probs.append("approval must show the file and line as path:line or reply to <id>")
        if token_used(t):
            probs.append(f"token {t} was already used")
        bind = _read_json(STATE / "stages" / f"{t}.json")
        if not bind or bind.get("sid") != sid:
            probs.append(f"token {t} was not staged in this session. Run stage via the shell "
                         "tool in this session")
        if rec["text"] not in msg:
            probs.append(f"the ask_user message must contain the exact staged text for {t}, "
                         "character for character")
        tp = check_text(rec["text"])
        if tp:
            probs.append(f"staged text for {t} breaks the comment rules: " + " | ".join(tp))
    return tokens, probs


def edit_form(rec):
    target = (f"reply to {rec['comment']}" if rec["kind"] == "reply"
              else f"{rec['path']}:{rec['line']}")
    text = rec["text"]
    return {
        "message": (f"Edit comment on {rec['slug']} #{rec['pr']} at {target}\n\n"
                    f"Current staged text, for reference:\n{text}\n\n"
                    "Revise the prefilled text below or replace it entirely\n"
                    "Your wording will be checked by humanizing and slophound, "
                    "then shown for fresh approval before posting"),
        "requestedSchema": {"properties": {"text": {
            "type": "string", "title": "Replacement comment text",
            "description": "Edit the staged text or enter a new comment",
            "default": text,
        }}},
    }


def parse_answers(result_text):
    if not result_text.startswith("User responded:"):
        return None
    answers = {}
    for entry in re.split(r"[,\n]", result_text[len("User responded:"):].strip()):
        match = re.fullmatch(r"\s*post_(" + TOKEN_RE + r")\s*[:=]\s*([^,\n]*)", entry)
        if not match:
            return None
        token, value = match.groups()
        if token in answers:
            return None
        answers[token] = value.strip()
    return answers


def parse_selection(result_text, tokens):
    """Only explicit token selections can approve a multi-comment form."""
    if not isinstance(result_text, str):
        return None
    match = re.fullmatch(r"User responded:\s*post_comments\s*[:=]\s*(.*?)\s*",
                         result_text, re.S)
    if not match:
        return None
    value = match.group(1)
    if value.startswith("["):
        try:
            selected = json.loads(value)
        except (ValueError, TypeError):
            return None
    else:
        selected = [part.strip() for part in value.split(",")] if value else []
    if not isinstance(selected, list) or any(not isinstance(t, str) for t in selected):
        return None
    if len(set(selected)) != len(selected) or any(t not in tokens for t in selected):
        return None
    return {t: "post" if t in selected else "skip" for t in tokens}


# ---------------------------------------------------------------- handlers
def handle_pre(payload, force_review=False):
    tool, sid, args = _tool(payload), _sid(payload), _args(payload)
    flat = "\n".join(_collect(args, []))

    # Shell/other tools: scan everything. File tools: scan only the target path, so
    # maintainers can still edit the gate's own source (which names these paths).
    if tool in EDIT_TOOLS or tool in ("view", "read", "grep", "glob", "rg"):
        scan = " ".join(str(args.get(k) or "") for k in ("path", "paths", "file", "pattern", "glob"))
    else:
        scan = flat
    if PROTECTED.search(scan):
        deny("the PR approval/grant state and its key may not be read or written by any tool. "
             "Use pr_comments.py stage, then ask_user, then reply/comment --approval.")

    review = force_review or is_review_session(sid)

    if tool == "task":
        prompt = str(args.get("prompt") or "")
        if prompt:
            h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
            _write_json(STATE / "launches" / f"{h}.json",
                        {"parent": sid, "agentType": args.get("agent_type"),
                         "prompt": prompt, "ts": time.time()})
        allow()

    if tool == "ask_user":
        tokens, probs = approval_request_problems(args, sid)
        if probs:
            deny("approval request rejected: " + " | ".join(probs))
        allow()

    if tool not in BUILTIN_TOOLS:
        low = tool.lower()
        if re.search(r"comment|review|reply|note|discussion", low) and \
                re.search(r"pull|(^|[_-])prs?([_-]|$)|merge_request|(^|[_-])mrs?([_-]|$)|issue", low) and \
                re.search(r"add|create|post|write|reply|submit|update|edit|delete|resolve", low) and \
                not re.search(r"jira|confluence|atlassian", low):
            deny(f"tool '{tool}' writes PR comments directly and bypasses the humanize and "
                 "approval gate. Use the pr-comment-respond skill (pr_comments.py).")
        if review and re.search(r"merge|push|create_or_update_file|delete_file|create_branch"
                                r"|update_pull_request|create_pull_request|approve|commit", low):
            deny(f"tool '{tool}' mutates the repository or PR. Reviews are read-only.")
        allow()

    if tool in EDIT_TOOLS and review:
        deny("reviews are read-only. Edits, file creation and patches are not allowed.")

    if tool not in SHELL_TOOLS:
        allow()

    cmd = str(args.get("command") or args.get("script") or flat)

    if PRC_INTERNALS.search(cmd):
        deny("calling pr_comments internals directly bypasses the approval gate. Use the "
             "pr_comments.py CLI (stage, then reply/comment --approval).")
    if RAW_ENDPOINT.search(cmd) and RAW_CLIENT.search(cmd) and RAW_WRITE.search(cmd):
        deny("writing to a PR comment endpoint with a raw HTTP client bypasses the humanize "
             "and approval gate. Use pr_comments.py stage, ask_user, then reply/comment.")
    if CLI_COMMENT.search(cmd):
        deny("gh/glab comment and review commands bypass the humanize and approval gate. Use "
             "the pr-comment-respond skill (pr_comments.py stage, ask_user, then post).")

    calls = prc_calls(cmd)
    if "pr_comments" in cmd.lower() and not calls and re.search(r"(?i)python|py\s", cmd):
        deny("pr_comments must be invoked as `python <path>/pr_comments.py <subcommand> ...`.")
    posts = [c for c in calls if c[0] in ("reply", "comment")]
    if len(posts) > 1:
        deny("post one comment per shell call so each grant is checked on its own.")

    for sub, seg in calls:
        if review:
            if sub in ("resolve", "delete"):
                deny(f"pr_comments.py {sub} is not allowed during a review.")
            if sub in ("stage", "reply", "comment") and not effective_opt_in(sid):
                deny("PR comment posting is OFF for this review. The human must add "
                     "--post-comments=true to the review prompt to enable staging and "
                     "approval. Report findings as text instead.")
        if sub == "stage" and _flag(seg, "comment") is None:
            path, line = _flag(seg, "path"), _flag(seg, "line")
            if not path or not line or not line.isdigit() or int(line) < 1:
                deny("new comments require --path and positive --line; no generic comment fallback")
        if sub in ("reply", "comment"):
            why = check_post_call(sub, seg, sid)
            if why:
                deny(why)

    if review:
        if GIT_MUT.search(cmd):
            deny("reviews are read-only: git commit/push/merge/rebase/reset/checkout and "
                 "similar are not allowed. Hand changes to a developer agent.")
        if FORGE_MUT.search(cmd):
            deny("reviews may not merge, create, close or edit PRs.")
        hit = writes_protected_tree(cmd)
        if hit:
            deny(f"reviews may not write into a configured repo or the agent kit ({hit}).")
    allow()


def handle_post(payload):
    tool, sid, args = _tool(payload), _sid(payload), _args(payload)
    res = payload.get("toolResult") or payload.get("tool_result") or {}
    text = str(res.get("textResultForLlm") or res.get("text_result_for_llm") or "")

    if tool in SHELL_TOOLS:
        cmd = str(args.get("command") or "")
        if any(s == "stage" for s, _ in prc_calls(cmd)):
            for t in re.findall(r"APPROVAL_TOKEN=(" + TOKEN_RE + r")", text):
                rec, _ = staged_rec(t)
                p = STATE / "stages" / f"{t}.json"
                if rec and not p.exists():
                    _write_json(p, {"sid": sid, "ts": time.time()})
        allow()

    if tool != "ask_user":
        allow()
    tokens, probs = approval_request_problems(args, sid)
    if not tokens:
        allow()
    # The CLI's short response drops field names; sessionLog retains their binding.
    answer_text = res.get("sessionLog", res.get("session_log", text))
    action = ((res.get("toolTelemetry") or {}).get("properties") or {}).get("elicitation_action")
    valid_result = res.get("resultType", "success") == "success" \
        and action in (None, "accept")
    answers = parse_answers(answer_text) if valid_result and isinstance(answer_text, str) else None
    batch = "post_comments" in ((args.get("requestedSchema") or {}).get("properties") or {})
    if batch:
        answers = parse_selection(answer_text, tokens) if valid_result else None
        if answers is None and valid_result and "sessionLog" not in res and "session_log" not in res and \
                text.startswith("User responded:"):
            answers = parse_selection(
                "User responded:\npost_comments: " + text[len("User responded:"):].strip(),
                tokens)
    # A single-field invocation also binds the short CLI response to its sole token.
    # Never substitute it for a present but malformed detailed log or a batch form.
    if valid_result and "sessionLog" not in res and "session_log" not in res and \
            len(tokens) == 1 and not probs and not batch:
        short = re.fullmatch(r"User responded:\s*(post|edit|skip|true|false)\s*", text)
        if short:
            answers = {tokens[0]: short.group(1)}
    approved, declined, editing = [], [], []
    for t in tokens:
        field = ((args.get("requestedSchema") or {}).get("properties") or {}).get(f"post_{t}") or {}
        answer = answers.get(t) if answers else None
        expected = "true" if field.get("type") == "boolean" and not batch else "post"
        if probs or answer != expected:
            declined.append(t)
            if not probs and field.get("type") == "string" and answer == "edit":
                rec, _ = staged_rec(t)
                editing.append((t, rec))
            continue
        rec, _ = staged_rec(t)
        mint_grant(t, rec, sid)
        approved.append((t, rec))
    for t in declined:
        for p in (APPROVALS / f"{t}.json", STATE / "stages" / f"{t}.json"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    lines = []
    for t, rec in approved:
        tgt = (f"--comment {rec.get('comment')}" if rec.get("kind") == "reply"
               else f"--path {rec.get('path')} --line {rec.get('line')}" if rec.get("kind") == "inline"
               else "")
        sub = "reply" if rec.get("kind") == "reply" else "comment"
        lines.append(f"APPROVED {t}: post once with `python <skilldir>/pr_comments.py {sub} "
                     f"--slug {rec.get('slug')} --pr {rec.get('pr')} {tgt} --approval {t}` "
                     f"(valid {GRANT_TTL // 60} min, one use, this session only).")
    if declined:
        reason = (" | ".join(probs) if probs else
                  "tool result failed or was cancelled" if not valid_result else
                  "missing or malformed token-bound answer" if answers is None else
                  "answer tokens do not match this approval form" if set(answers) != set(tokens) else
                  "user selected Edit text or Skip, or answer does not match the field type")
        lines.append("NOT APPROVED (staged text discarded, do not post): " + ", ".join(declined)
                     + ". Reason: " + reason)
    if editing:
        lines.append("EDIT REQUESTED: " + ", ".join(t for t, _ in editing)
                     + ". Call ask_user with each EDIT_FORM below exactly as provided. "
                     "Keep the current text visible in the message AND prefilled in the input. "
                     "These are editing forms, not approval forms. Then stage the replacement "
                     "for the same PR and target. It must pass humanizing and slophound, "
                     "and receive fresh exact-text approval with a new token before posting.")
        for _, rec in editing:
            lines.append("\nEDIT_FORM=" + json.dumps(edit_form(rec), ensure_ascii=True))
    _emit({"additionalContext": "pr-post-gate: " + " ".join(lines)})


def handle_prompt(payload):
    sid, prompt = _sid(payload), str(payload.get("prompt") or "")
    if not sid:
        allow()
    now = time.time()
    parent = agent = None
    best = None
    ldir = STATE / "launches"
    if ldir.is_dir():
        for p in ldir.glob("*.json"):
            rec = _read_json(p)
            if not rec or now - rec.get("ts", 0) > LAUNCH_TTL:
                try:
                    p.unlink()
                except Exception:
                    pass
                continue
            lp = (rec.get("prompt") or "").strip()
            if lp and lp in prompt and (best is None or rec["ts"] > best[1]["ts"]):
                best = (p, rec)
    if best:
        parent, agent = best[1].get("parent"), best[1].get("agentType")
        try:
            best[0].unlink()
        except Exception:
            pass
    old = session_rec(sid) or {}
    flag = bool(OPT_IN.search(prompt))
    rec = {"ts": now, "parent": old.get("parent") or parent,
           "agentType": old.get("agentType") or agent,
           "promptFlag": bool(old.get("promptFlag")) or flag}
    if not rec["parent"]:
        rec["userOptIn"] = bool(old.get("userOptIn")) or flag
    _write_json(STATE / "sessions" / f"{_safe(sid)}.json", rec)
    allow()


def handle_review_prompt(payload):
    sid, prompt = _sid(payload), str(payload.get("prompt") or "")
    if sid:
        old = review_marker(sid) or {}
        _write_json(STATE / "review" / f"{_safe(sid)}.json",
                    {"ts": time.time(),
                     "promptFlag": bool(old.get("promptFlag")) or bool(OPT_IN.search(prompt))})
    allow()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    globals()["_RAW"] = raw
    if mode == "pre":
        handle_pre(payload)
    elif mode == "review-pre":
        handle_pre(payload, force_review=True)
    elif mode == "post":
        handle_post(payload)
    elif mode == "prompt":
        handle_prompt(payload)
    elif mode == "review-prompt":
        handle_review_prompt(payload)
    allow()


_RAW = ""

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
        if mode in ("pre", "review-pre") and re.search(
                r"(?i)pr_comments|PR_POST_APPROVAL|comment|\.pr-approvals", _RAW):
            deny(f"internal error while checking a PR comment action, failing closed ({e}).")
        if mode == "post":
            _emit({})
        allow()
