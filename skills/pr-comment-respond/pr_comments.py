#!/usr/bin/env python3
"""Generic PR review-comment primitives for in-session review responses.

New comments require verified diff anchors. Duplicate checks at staging and posting
include every author's open comments/tasks; invalid anchors never fall back to a
generic PR-level comment.

Subcommands:
  list     - print actionable reviewer comments on a PR as JSON
  stage    - humanize + rule-check + grounding-check text, print APPROVAL_TOKEN
  reply    - post a staged reply (requires --approval and the user's approval)
  comment  - post a staged NEW inline review comment
  resolve  - mark a comment thread RESOLVED
  humanize - sanitize + rule-check text only; prints cleaned text
  pr-info  - read only: source/target branch for a PR number, PR#<n> or PR URL

Posting is approval-gated and enforced by ~/.copilot/hooks/pr-post-gate-hook.py:
  1. stage   humanizes the text, applies the deterministic comment rules (no
             hyphen/dash, no semicolon outside `code`, slophound must pass, fail
             closed if slophound is missing), runs the grounding preflight and
             prints APPROVAL_TOKEN=<t>. The token alone authorises nothing.
  2. ask_user must show the exact staged text plus the line `PR_POST_APPROVAL <t>`
             and a string field `post_<t>` with enum [post, edit, skip], no default.
             The hook mints a
             signed, one-use, target-bound grant only when the user selects Post.
  3. reply/comment --approval <t> atomically consumes that grant, re-checks the
             text and posts exactly the staged text.
The grounding preflight (run at stage, before the user is asked): the body must be
non-empty/non-placeholder, a reply target comment must exist and not be RESOLVED,
and every file / file:line (and, with --strict, every commit SHA) cited in the
text must resolve to THIS PR. It catches structural false positives; it does not
certify that the reply is semantically correct.

Self-configures from the kit config (code-review host base URL, token file, and a
repo-slug -> service map that resolves each slug to its local repoPath, baseBranch,
and owning developer/reviewer agent). Never searches the working directory.

Reference adapter: Bitbucket Server / Data Center REST (the REST shape is swappable
- see references/other-hosts.md). OS-agnostic: Python 3 stdlib + pyyaml.
Used by the `pr-comment-respond` skill so an agent can reply/resolve directly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

COPILOT_HOME = Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".copilot"))
# Kit config (repos, models, and the code-review host block). Override with --config.
DEFAULT_CONFIG = COPILOT_HOME / "agents" / "agents.config.yaml"
APPROVAL_DIR = COPILOT_HOME / ".pr-approvals"
GATE_FILE = COPILOT_HOME / "hooks" / "pr-post-gate-hook.py"


def load_gate():
    """Import the gate hook by path. Without it nothing can be staged or posted."""
    import importlib.util
    if not GATE_FILE.exists():
        sys.exit(f"pr-post-gate hook not found at {GATE_FILE}. Install the kit hooks "
                 f"(scripts/install_to_copilot.py --hooks); posting fails closed without it.")
    spec = importlib.util.spec_from_file_location("pr_post_gate", GATE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- staged records: stage writes the exact final text and PR target. The token
# authorises nothing by itself. Posting needs the hook minted grant (see docstring).
def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _approval_path(token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16,64}", token or ""):
        sys.exit("Invalid approval token format.")
    return APPROVAL_DIR / f"{token}.json"


def _write_approval(record: dict) -> str:
    APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(12)
    record["token"] = token
    record["created"] = int(time.time())
    _approval_path(token).write_text(json.dumps(record), encoding="utf-8")
    return token


APPROVAL_TTL = 15 * 60


def _load_approval(token: str, slug: str, pr: int, kind: str) -> dict:
    p = _approval_path(token)
    if not p.exists():
        sys.exit(f"No staged approval for token {token}. Run `stage` first and get "
                 f"the user's explicit yes before posting.")
    rec = json.loads(p.read_text(encoding="utf-8"))
    age = int(time.time()) - int(rec.get("created", 0))
    if age > APPROVAL_TTL:
        p.unlink(missing_ok=True)
        sys.exit(f"Approval token expired ({age}s > {APPROVAL_TTL}s). Re-stage and "
                 f"re-confirm with the user.")
    if rec.get("slug") != slug or int(rec.get("pr")) != int(pr) or rec.get("kind") != kind:
        sys.exit("Approval token does not match this PR/target. Re-stage.")
    if _text_hash(rec.get("text", "")) != rec.get("hash"):
        p.unlink(missing_ok=True)
        sys.exit("Staged text failed integrity check (tampered). Re-stage.")
    return rec


def _consume_approval(token: str) -> None:
    _approval_path(token).unlink(missing_ok=True)


# --- grounding: every checkable reference in a post must resolve to THIS PR ------
# A deterministic preflight. It cannot judge whether a reply is *correct* or
# *responsive* (that is semantic and stays the agent's job), but it eliminates the
# structurally-detectable false positives: replying to a missing/resolved comment,
# an empty/placeholder body, or citing a file / line / commit that is not in the PR.
_CODE_EXT = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".cpp", ".cc", ".cxx",
    ".h", ".hpp", ".hh", ".cs", ".rb", ".rs", ".kt", ".kts", ".swift", ".m", ".mm",
    ".php", ".scala", ".sql", ".sh", ".ps1", ".psm1", ".yaml", ".yml", ".json",
    ".xml", ".gradle", ".toml", ".ini", ".cfg", ".md", ".proto", ".vue", ".css",
    ".scss", ".html", ".tf", ".bicep", ".dockerfile",
)
_FILELINE_RE = re.compile(r"\b([\w./\-]+\.[A-Za-z0-9]+):(\d+)(?:-\d+)?\b")
_PATH_RE = re.compile(r"\b([\w./\-]*[\w\-]+\.[A-Za-z0-9]+)\b")
_SHA_RE = re.compile(r"\b([0-9a-fA-F]{7,40})\b")
_PLACEHOLDER_RE = re.compile(r"^(?:\.{2,}|todo|tbd|wip|xxx|fixme|<[^>]*>|n/?a)$", re.I)


def _looks_like_path(tok: str) -> bool:
    ext = "." + tok.rsplit(".", 1)[-1].lower() if "." in tok else ""
    return ("/" in tok and "." in tok.rsplit("/", 1)[-1]) or ext in _CODE_EXT


def extract_refs(text: str):
    """Return (file_tokens, (path,line) pairs, sha_tokens) cited in the text."""
    files, filelines, shas = set(), [], set()
    for m in _FILELINE_RE.finditer(text):
        files.add(m.group(1))
        filelines.append((m.group(1), int(m.group(2))))
    for m in _PATH_RE.finditer(text):
        tok = m.group(1).strip(".,);:'\"")
        if _looks_like_path(tok):
            files.add(tok)
    for m in _SHA_RE.finditer(text):
        shas.add(m.group(1).lower())
    return files, filelines, shas


def body_ok(text: str):
    stripped = text.strip()
    if not stripped:
        return False, "empty body"
    if _PLACEHOLDER_RE.match(stripped):
        return False, f"placeholder body: {stripped!r}"
    return True, ""


def pr_changed_files(bb, proj, slug, pr):
    out = set()
    for v in bb.paged(f"/rest/api/1.0/projects/{proj}/repos/{slug}/pull-requests/{pr}/changes"):
        p = (v.get("path") or {}).get("toString")
        if p:
            out.add(p)
    return out


def pr_commit_shas(bb, proj, slug, pr):
    vals = bb.paged(f"/rest/api/1.0/projects/{proj}/repos/{slug}/pull-requests/{pr}/commits")
    full = {v["id"].lower() for v in vals if v.get("id")}
    short = {v["displayId"].lower() for v in vals if v.get("displayId")}
    return full, short


def _file_grounded(tok, changed):
    tok = tok.lstrip("./")
    if not tok:
        return False
    base = tok.rsplit("/", 1)[-1]
    for cf in changed:
        if cf == tok or cf.endswith("/" + tok):
            return True
        if "/" not in tok and cf.rsplit("/", 1)[-1] == base:
            return True
    return False


def browse_exists(bb, proj, slug, path, at_ref):
    q = "?type=true" + (f"&at={urllib.parse.quote(at_ref, safe='')}" if at_ref else "")
    try:
        bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{slug}/browse/{path.lstrip('./')}{q}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def reply_target_ok(bb, proj, slug, pr, comment_id):
    try:
        c = bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{slug}/pull-requests/{pr}/comments/{comment_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, f"comment #{comment_id} does not exist on PR #{pr}"
        return True, ""  # transient host error -> do not block a human
    if (c.get("state") or "").upper() == "RESOLVED":
        return False, f"comment #{comment_id} is already RESOLVED; do not reply to a closed thread"
    return True, ""


def ground_check(bb, proj, slug, pr, text, strict, extra_files=()):
    """Return (ok, problems). A post with no checkable reference passes (nothing to
    ground). Host/API errors fail open with a note (same discipline as slophound)."""
    files, _filelines, shas = extract_refs(text)
    files |= {f for f in extra_files if f}
    if not files and not (strict and shas):
        return True, []
    try:
        changed = pr_changed_files(bb, proj, slug, pr)
        full_shas, short_shas = (pr_commit_shas(bb, proj, slug, pr) if strict and shas else (set(), set()))
        head_ref = None
        if not strict and any("/" in f for f in files):
            info = bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{slug}/pull-requests/{pr}")
            head_ref = (info.get("fromRef") or {}).get("latestCommit")
    except urllib.error.HTTPError:
        return True, ["(grounding skipped: could not fetch the PR diff/commits)"]
    problems = []
    for f in sorted(files):
        if _file_grounded(f, changed):
            continue
        if not strict and "/" in f and browse_exists(bb, proj, slug, f, head_ref):
            continue
        problems.append(
            f"file not in this PR: {f}" + ("" if strict else " (and not found at PR head)"))
    if strict:
        for sha in sorted(shas):
            if not any(fs.startswith(sha) for fs in full_shas) and sha not in short_shas:
                problems.append(f"commit not in this PR: {sha}")
    return (not problems), problems


def preflight(bb, proj, slug, pr, text, args, comment_id=None, extra_files=()):
    """Deterministic validity gate shared by reply/comment. Exits non-zero on a
    structurally-detectable false positive; stays silent on success."""
    ok, why = body_ok(text)
    if not ok:
        sys.stderr.write(f"Rejected (body): {why}\n")
        sys.exit(4)
    if getattr(args, "no_ground", False):
        return
    if comment_id is not None:
        ok, why = reply_target_ok(bb, proj, slug, pr, comment_id)
        if not ok:
            sys.stderr.write(f"Rejected (target): {why}\n")
            sys.exit(4)
    ok, problems = ground_check(bb, proj, slug, pr, text, getattr(args, "strict", False), extra_files)
    if not ok:
        sys.stderr.write(
            "Rejected (grounding): the post cites things that are not in this PR:\n  - "
            + "\n  - ".join(problems)
            + "\nFix the references in the text and stage again.\n")
        sys.exit(4)


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Code-review host block. `bitbucket` is the reference key; `codeReview` is an
    # accepted alias so the config reads host-agnostic if you prefer.
    host = cfg.get("bitbucket") or cfg.get("codeReview") or {}
    services = cfg.get("services", {}) or {}
    repos = {}
    for r in host.get("repositories", []) or []:
        svc = services.get(r.get("service"), {}) or {}
        if r.get("slug"):
            repos[r["slug"]] = {
                "project": r.get("project"),
                "service": r.get("service"),
                "repoPath": svc.get("repoPath"),
                "baseBranch": svc.get("baseBranch"),
                "developerAgent": svc.get("developerAgent"),
                "reviewerAgent": svc.get("reviewerAgent"),
            }
    token_file = host.get("tokenFile")
    if not token_file:
        sys.exit("Config error: code-review host block needs a 'tokenFile'.")
    token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    if not host.get("baseUrl"):
        sys.exit("Config error: code-review host block needs a 'baseUrl'.")
    return {
        "baseUrl": host["baseUrl"].rstrip("/"),
        "insecure": bool(host.get("insecureTls", False)),
        "token": token,
        "repos": repos,
    }


class BB:
    def __init__(self, cfg):
        self.base = cfg["baseUrl"]
        self.token = cfg["token"]
        self.ctx = ssl._create_unverified_context() if cfg["insecure"] else None

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        r.add_header("Authorization", f"Bearer {self.token}")
        r.add_header("Accept", "application/json")
        if data is not None:
            r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, context=self.ctx) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def paged(self, path):
        sep = "&" if "?" in path else "?"
        out, start = [], 0
        while True:
            d = self.req("GET", f"{path}{sep}limit=100&start={start}")
            out.extend(d.get("values", []))
            if d.get("isLastPage", True):
                break
            start = d.get("nextPageStart", start + 100)
        return out


def project_for(cfg, slug):
    meta = cfg["repos"].get(slug)
    if not meta:
        sys.exit(f"Unknown repo slug '{slug}'. Known: {', '.join(cfg['repos'])}")
    if not meta.get("project"):
        sys.exit(f"Repo slug '{slug}' has no 'project' in config.")
    return meta["project"], meta


def flatten(comment, anchor, acc, me):
    reply_authors = [c.get("author", {}).get("name") for c in comment.get("comments", [])]
    acc.append({
        "id": comment.get("id"),
        "version": comment.get("version"),
        "state": comment.get("state"),
        "author": comment.get("author", {}).get("name"),
        "text": comment.get("text"),
        "anchorPath": (anchor or {}).get("path"),
        "anchorLine": (anchor or {}).get("line"),
        "iReplied": me in reply_authors if me else False,
    })
    for child in comment.get("comments", []):
        flatten(child, anchor, acc, me)


PR_URL = re.compile(r"/repos/([^/]+)/pull-requests/(\d+)", re.I)
PR_NUM = re.compile(r"^\s*(?:(?:pr|mr)\s*#?|#|!)?\s*(\d+)\s*$", re.I)


def parse_pr_ref(ref):
    """'4521', 'PR#4521', '#4521' or a .../repos/<slug>/pull-requests/<n> URL -> (slug or None, int)."""
    m = PR_URL.search(ref or "")
    if m:
        return m.group(1), int(m.group(2))
    m = PR_NUM.match(ref or "")
    if m:
        return None, int(m.group(1))
    sys.exit(f"Not a PR reference: {ref!r}. Use a number, PR#<n> or a PR URL.")


def slug_for_service(cfg, service):
    hits = sorted(s for s, m in cfg["repos"].items() if m.get("service") == service)
    if len(hits) != 1:
        sys.exit(f"Service '{service}' maps to {hits or 'no repo'}. Pass --slug instead.")
    return hits[0]


def cmd_pr_info(bb, cfg, args):
    """Read only: the branches a PR compares, so a reviewer can diff it like a branch."""
    proj, meta = project_for(cfg, args.slug)
    d = bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}")
    src, dst = d.get("fromRef") or {}, d.get("toRef") or {}
    out = {"source": src.get("displayId"), "target": dst.get("displayId"),
           "head": src.get("latestCommit"), "state": d.get("state"), "title": d.get("title"),
           "fork": (src.get("repository") or {}).get("slug") not in (None, args.slug),
           "slug": args.slug, "pr": int(args.pr), "service": meta.get("service"),
           "repoPath": meta.get("repoPath"), "reviewerAgent": meta.get("reviewerAgent")}
    print(json.dumps(out, indent=2))
    if out["fork"]:
        sys.stderr.write("NOTE source branch lives in a fork, origin/<source> will not exist.\n")


def cmd_list(bb, cfg, args):
    proj, meta = project_for(cfg, args.slug)
    pr = bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}")
    me = pr.get("author", {}).get("user", {}).get("name")
    acts = bb.paged(f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/activities")
    flat = []
    for a in acts:
        if a.get("action") == "COMMENTED" and a.get("comment"):
            flatten(a["comment"], a.get("commentAnchor"), flat, me)
    todo = [c for c in flat
            if c["author"] != me and c["state"] != "RESOLVED" and not c["iReplied"]]
    if args.all:
        todo = flat
    print(json.dumps({
        "pr": args.pr, "slug": args.slug, "branch": pr.get("fromRef", {}).get("displayId"),
        "title": pr.get("title"),
        "service": meta.get("service"),
        "repoPath": meta.get("repoPath"),
        "baseBranch": meta.get("baseBranch"),
        "developerAgent": meta.get("developerAgent"),
        "reviewerAgent": meta.get("reviewerAgent"),
        "comments": todo,
    }, indent=2))


def stage_text(gate, text: str) -> str:
    """Humanize + apply the deterministic comment rules. Exit 3 on any problem."""
    final = gate.humanize(text)
    problems = gate.check_text(final)
    if problems:
        sys.stderr.write("Rejected, rewrite the comment and stage again:\n  * "
                         + "\n  * ".join(problems) + "\n")
        sys.exit(3)
    return final


def require_inline(path, line):
    if not path or not isinstance(line, int) or isinstance(line, bool) or line < 1:
        sys.exit("Refused: provide a PR diff file and positive line. Generic posting is disabled.")


def open_bitbucket_items(bb, proj, slug, pr):
    """Read all authors' open comments/tasks, retaining their diff anchors."""
    items = []

    def walk(comment, anchor):
        if (comment.get("state") or "").upper() == "RESOLVED":
            return
        items.append({"id": comment.get("id"), "text": comment.get("text") or "",
                      "severity": comment.get("severity"), "path": anchor.get("path"),
                      "line": anchor.get("line"), "orphaned": anchor.get("orphaned", False)})
        for child in comment.get("comments", []) or []:
            walk(child, anchor)

    for activity in bb.paged(
            f"/rest/api/1.0/projects/{proj}/repos/{slug}/pull-requests/{pr}/activities"):
        if activity.get("action") == "COMMENTED" and activity.get("comment"):
            walk(activity["comment"], activity.get("commentAnchor") or {})
    return items


def release_reminder(text):
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return bool(words & {"release", "released", "unreleased", "stable", "prerelease", "nonstable"}) \
        and bool(words & {"merge", "merging", "version", "versions", "package", "packages",
                          "dependency", "dependencies", "contract", "contracts",
                          "reference", "references", "publish", "promote", "switch", "bump"})


def similar_text(left, right):
    """Deterministic lexical matching; unrelated concerns on the same line stay distinct."""
    a = re.findall(r"[a-z0-9]+", (left or "").lower())
    b = re.findall(r"[a-z0-9]+", (right or "").lower())
    if not a or not b:
        return False
    if a == b:
        return True
    if release_reminder(left) and release_reminder(right):
        return True
    common, union = set(a) & set(b), set(a) | set(b)
    return len(common) >= 4 and len(common) / len(union) >= 0.8


def duplicate_item(items, path, line, text):
    return next((item for item in items
                 if not item.get("orphaned") and item.get("path") == path
                 and item.get("line") == line and similar_text(item["text"], text)), None)


def duplicate_notice(item):
    kind = "BLOCKER task" if item.get("severity") == "BLOCKER" else "comment (not a BLOCKER task)"
    return f"SKIPPED_DUPLICATE: existing {kind} #{item.get('id')} already covers this line."


def bitbucket_anchor(bb, proj, slug, pr, path, line):
    """Validate the current diff location; never degrade a failed anchor to a general comment."""
    require_inline(path, line)
    diff = bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{slug}/pull-requests/{pr}"
                       f"/diff/{urllib.parse.quote(path, safe='')}?contextLines=3")
    for file_diff in diff.get("diffs", []) or []:
        for hunk in file_diff.get("hunks", []) or []:
            for segment in hunk.get("segments", []) or []:
                if segment.get("type") not in ("ADDED", "CONTEXT"):
                    continue
                if any(ln.get("destination") == line for ln in segment.get("lines", []) or []):
                    return {"path": path, "line": line, "lineType": segment["type"],
                            "fileType": "TO", "diffType": "EFFECTIVE"}
    sys.exit(f"Refused: {path}:{line} is not an available PR diff line. Nothing was posted.")


def cmd_stage(bb, cfg, args):
    gate = load_gate()
    proj, _ = project_for(cfg, args.slug)
    if args.comment is not None:
        kind, target = "reply", {"comment": str(args.comment)}
    elif args.path:
        require_inline(args.path, args.line)
        kind, target = "inline", {"path": args.path, "line": args.line}
    else:
        sys.exit("Refused: new comments need --path and --line. Generic comments are disabled.")
    if kind == "inline":
        bitbucket_anchor(bb, proj, args.slug, args.pr, args.path, args.line)
    text = args.text if args.text is not None else sys.stdin.read()
    final = stage_text(gate, text)
    if kind == "inline":
        duplicate = duplicate_item(open_bitbucket_items(bb, proj, args.slug, args.pr),
                                   args.path, args.line, final)
        if duplicate:
            print(duplicate_notice(duplicate))
            return
    preflight(bb, proj, args.slug, args.pr, final, args,
              comment_id=args.comment if kind == "reply" else None,
              extra_files=(args.path,) if args.path else ())
    rec = {"slug": args.slug, "pr": int(args.pr), "kind": kind,
           "text": final, "hash": _text_hash(final), **target}
    token = _write_approval(rec)
    where = (f"reply to {args.comment}" if kind == "reply"
             else f"inline {args.path}:{args.line}")
    sys.stderr.write(
        f"STAGED {kind} on {args.slug} #{args.pr} ({where}). Nothing is approved yet.\n"
        f"Next: call ask_user with a message that contains the line\n"
        f"  PR_POST_APPROVAL {token}\n"
        f"followed by the final text below copied character for character, and a\n"
        f"requestedSchema with ONLY a string field `post_{token}` (no default),\n"
        f"enum [\"post\", \"edit\", \"skip\"] and enumNames [\"Post\", \"Edit text\", \"Skip\"].\n"
        f"Only post approves. On edit, collect replacement text in a separate ask_user,\n"
        f"then stage it for the same PR and target and request fresh exact-text approval.\n"
        f"The pr-post-gate hook mints a one-use grant only if the user selects Post.\n")
    print(f"APPROVAL_TOKEN={token}")
    print("--- final text (copy exactly into ask_user) ---")
    print(final)


def _post_guarded(args, kind, comment=None, path=None, line=None):
    gate = load_gate()
    rec = _load_approval(args.approval, args.slug, args.pr, kind)
    ok, why, _ = gate.consume_grant(args.approval, args.slug, int(args.pr), kind,
                                    comment, path, line)
    if not ok:
        sys.exit(f"Refused: {why}")
    problems = gate.check_text(rec["text"])
    if problems:
        _consume_approval(args.approval)
        sys.exit("Refused: staged text breaks the comment rules: " + " | ".join(problems))
    return rec


def cmd_reply(bb, cfg, args):
    proj, _ = project_for(cfg, args.slug)
    rec = _post_guarded(args, "reply", comment=str(args.comment))
    try:
        bb.req("POST", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/comments",
               {"text": rec["text"], "parent": {"id": int(args.comment)}})
    finally:
        _consume_approval(args.approval)
    print(f"replied to #{args.comment}")


def cmd_comment(bb, cfg, args):
    require_inline(args.path, args.line)
    proj, _ = project_for(cfg, args.slug)
    rec = _post_guarded(args, "inline", path=args.path, line=args.line)
    try:
        body = {"text": rec["text"], "anchor": bitbucket_anchor(
            bb, proj, args.slug, args.pr, args.path, args.line)}
        duplicate = duplicate_item(open_bitbucket_items(bb, proj, args.slug, args.pr),
                                   args.path, args.line, rec["text"])
        if duplicate:
            print(duplicate_notice(duplicate))
            return
        res = bb.req("POST", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/comments", body)
    finally:
        _consume_approval(args.approval)
    where = f"{args.path}:{args.line}"
    print(f"posted comment #{res.get('id')} on PR #{args.pr} @ {where}")


def cmd_humanize(bb, cfg, args):
    gate = load_gate()
    text = args.text if args.text is not None else sys.stdin.read()
    cleaned = gate.humanize(text)
    problems = gate.check_text(cleaned)
    sys.stderr.write(("HUMAN OK\n" if not problems else "REJECTED\n  * "
                      + "\n  * ".join(problems)) + "\n")
    print(cleaned)
    if problems:
        sys.exit(3)


def cmd_resolve(bb, cfg, args):
    proj, _ = project_for(cfg, args.slug)
    cur = bb.req("GET", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/comments/{args.comment}")
    bb.req("PUT", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/comments/{args.comment}",
           {"version": cur.get("version"), "state": "RESOLVED"})
    print(f"resolved #{args.comment}")


def main():
    ap = argparse.ArgumentParser(description="Generic PR review-comment primitives.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list actionable comments on a PR")
    pl.add_argument("--slug", required=True)
    pl.add_argument("--pr", type=int, required=True)
    pl.add_argument("--all", action="store_true", help="include resolved/own/replied too")

    st = sub.add_parser("stage", help="humanize + rule-check + ground text and print a one-use approval token")
    st.add_argument("--slug", required=True)
    st.add_argument("--pr", type=int, required=True)
    st.add_argument("--comment", type=int, help="reply target comment id (omit for a new comment)")
    st.add_argument("--path", help="file path (for an inline comment)")
    st.add_argument("--line", type=int, help="line number (for an inline comment)")
    st.add_argument("--text", help="text (or pass via stdin)")
    st.add_argument("--strict", action="store_true",
                    help="strict grounding: cited files AND commit SHAs must be in the PR diff")

    pr_ = sub.add_parser("reply", help="post a staged reply (requires --approval and the user's yes)")
    pr_.add_argument("--slug", required=True)
    pr_.add_argument("--pr", type=int, required=True)
    pr_.add_argument("--comment", type=int, required=True)
    pr_.add_argument("--approval", required=True, help="one-use token from `stage`, granted via ask_user")

    nc = sub.add_parser("comment", help="post a staged NEW review comment (requires --approval)")
    nc.add_argument("--slug", required=True)
    nc.add_argument("--pr", type=int, required=True)
    nc.add_argument("--path", required=True, help="related PR diff file")
    nc.add_argument("--line", type=int, required=True, help="positive PR diff line")
    nc.add_argument("--approval", required=True, help="one-use token from `stage`, granted via ask_user")

    pi = sub.add_parser("pr-info", help="read only: source/target branch of a PR (number, PR#<n> or URL)")
    pi.add_argument("--slug", help="repo slug (optional with --service or a PR URL)")
    pi.add_argument("--service", help="service key from agents.config.yaml")
    pi.add_argument("--pr", required=True, help="4521, PR#4521 or the PR URL")

    hm = sub.add_parser("humanize", help="sanitize + rule-check text; prints cleaned text")
    hm.add_argument("--text", help="text to check (or pass via stdin)")

    rs = sub.add_parser("resolve", help="resolve a comment thread")
    rs.add_argument("--slug", required=True)
    rs.add_argument("--pr", type=int, required=True)
    rs.add_argument("--comment", type=int, required=True)

    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    handler = {"list": cmd_list, "stage": cmd_stage, "reply": cmd_reply, "comment": cmd_comment,
               "humanize": cmd_humanize, "resolve": cmd_resolve, "pr-info": cmd_pr_info}[args.cmd]
    if args.cmd == "humanize":
        handler(None, None, args)
        return
    cfg = load_config(Path(args.config))
    if args.cmd == "pr-info":
        url_slug, args.pr = parse_pr_ref(args.pr)
        args.slug = args.slug or url_slug or (args.service and slug_for_service(cfg, args.service))
        if not args.slug:
            sys.exit("pr-info needs --slug, --service or a PR URL.")
    bb = BB(cfg)
    try:
        handler(bb, cfg, args)
    except urllib.error.HTTPError as e:
        sys.exit(f"Code-review API error {e.code}: {e.read().decode('utf-8', 'replace')}")


if __name__ == "__main__":
    main()
