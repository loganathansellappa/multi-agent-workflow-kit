#!/usr/bin/env python3
"""Generic PR review-comment primitives for in-session review responses.

Subcommands:
  list     - print actionable reviewer comments on a PR as JSON
  reply    - post a reply to a comment thread
  comment  - post a NEW review comment (general or inline)
  resolve  - mark a comment thread RESOLVED
  humanize - sanitize + slophound-check text; prints cleaned text

`reply`/`comment` run a deterministic validity preflight before posting: the body
must be non-empty/non-placeholder, a `reply` target comment must exist and not be
RESOLVED, and every file / file:line (and, with --strict, every commit SHA) cited
in the text must resolve to THIS PR. It catches structural false positives; it does
not (and cannot) certify that the reply is semantically correct. Opt out per-call
with --no-ground.

Self-configures from the kit config (code-review host base URL, token file, and a
repo-slug -> service map that resolves each slug to its local repoPath, baseBranch,
and owning developer/reviewer agent). Never searches the working directory.

Reference adapter: Bitbucket Server / Data Center REST (the REST shape is swappable
- see references/other-hosts.md). OS-agnostic: Python 3 stdlib + pyyaml.
Used by the `pr-comment-respond` skill so an agent can reply/resolve directly.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

# Kit config (repos, models, and the code-review host block). Override with --config.
DEFAULT_CONFIG = Path.home() / ".copilot" / "agents" / "agents.config.yaml"

# --- humanize: strip the punctuation tells AI loves before anything is posted
_SMART = {
    "\u2014": "-", "\u2013": "-",            # em / en dash -> hyphen
    "\u2018": "'", "\u2019": "'",            # curly single quotes
    "\u201c": '"', "\u201d": '"',            # curly double quotes
    "\u2026": "...", "\u00a0": " ",          # ellipsis, non-breaking space
}
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U00002190-\U000021FF\U0000FE0F]"
)


def humanize_text(text: str) -> str:
    for k, v in _SMART.items():
        text = text.replace(k, v)
    text = _EMOJI.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    return text.strip()


def slophound_lint(text: str):
    """Return (is_human, report). Missing/failed tool is treated as a skip."""
    exe = shutil.which("slophound")
    if not exe:
        return True, "(slophound not installed; skipped)"
    p = subprocess.run([exe, "--formal", "--no-footer", "-"],
                       input=text, capture_output=True, text=True)
    if p.returncode == 2:
        return True, "(slophound failed; skipped)"
    return p.returncode == 0, (p.stdout or p.stderr).strip()


def finalize_text(text: str, no_lint: bool) -> str:
    text = humanize_text(text)
    if not no_lint:
        ok, report = slophound_lint(text)
        if not ok:
            sys.stderr.write(
                "Rejected: the text still reads as AI-generated (slophound bites).\n"
                "Rewrite it plainer and more human, then retry:\n" + report + "\n")
            sys.exit(3)
    return text


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
            + "\nFix the references, or pass --no-ground if the citation is intentional.\n")
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


def cmd_reply(bb, cfg, args):
    proj, _ = project_for(cfg, args.slug)
    text = args.text if args.text is not None else sys.stdin.read()
    preflight(bb, proj, args.slug, args.pr, text, args, comment_id=args.comment)
    text = finalize_text(text, args.no_lint)
    bb.req("POST", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/comments",
           {"text": text, "parent": {"id": args.comment}})
    print(f"replied to #{args.comment}")


def cmd_comment(bb, cfg, args):
    proj, _ = project_for(cfg, args.slug)
    text = args.text if args.text is not None else sys.stdin.read()
    preflight(bb, proj, args.slug, args.pr, text, args,
              extra_files=(args.path,) if args.path else ())
    text = finalize_text(text, args.no_lint)
    body = {"text": text}
    if args.path:
        anchor = {"path": args.path, "diffType": "EFFECTIVE",
                  "lineType": "CONTEXT", "fileType": "TO"}
        if args.line is not None:
            anchor["line"] = args.line
        body["anchor"] = anchor
    res = bb.req("POST", f"/rest/api/1.0/projects/{proj}/repos/{args.slug}/pull-requests/{args.pr}/comments", body)
    where = f"{args.path}:{args.line}" if args.path else "(general)"
    print(f"posted comment #{res.get('id')} on PR #{args.pr} @ {where}")


def cmd_humanize(bb, cfg, args):
    text = args.text if args.text is not None else sys.stdin.read()
    cleaned = humanize_text(text)
    ok, report = slophound_lint(cleaned)
    sys.stderr.write(("HUMAN OK\n" if ok else "AI SLOP DETECTED\n") + report + "\n")
    print(cleaned)
    if not ok:
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

    pr_ = sub.add_parser("reply", help="reply to a comment")
    pr_.add_argument("--slug", required=True)
    pr_.add_argument("--pr", type=int, required=True)
    pr_.add_argument("--comment", type=int, required=True)
    pr_.add_argument("--text", help="reply text (or pass via stdin)")
    pr_.add_argument("--no-lint", action="store_true", help="skip humanize/slophound gate")
    pr_.add_argument("--no-ground", action="store_true",
                     help="skip the grounding/target validity preflight (intentional citations)")
    pr_.add_argument("--strict", action="store_true",
                     help="strict grounding: cited files AND commit SHAs must be in the PR diff")

    nc = sub.add_parser("comment", help="post a NEW review comment (general or inline)")
    nc.add_argument("--slug", required=True)
    nc.add_argument("--pr", type=int, required=True)
    nc.add_argument("--path", help="file path for an inline comment (omit for general)")
    nc.add_argument("--line", type=int, help="line number for an inline comment")
    nc.add_argument("--text", help="comment text (or pass via stdin)")
    nc.add_argument("--no-lint", action="store_true", help="skip humanize/slophound gate")
    nc.add_argument("--no-ground", action="store_true",
                    help="skip the grounding/target validity preflight (intentional citations)")
    nc.add_argument("--strict", action="store_true",
                    help="strict grounding: cited files AND commit SHAs must be in the PR diff")

    hm = sub.add_parser("humanize", help="sanitize + slophound-check text; prints cleaned text")
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
    cfg = load_config(Path(args.config))
    bb = BB(cfg)
    try:
        {"list": cmd_list, "reply": cmd_reply, "comment": cmd_comment,
         "humanize": cmd_humanize, "resolve": cmd_resolve}[args.cmd](bb, cfg, args)
    except urllib.error.HTTPError as e:
        sys.exit(f"Code-review API error {e.code}: {e.read().decode('utf-8', 'replace')}")


if __name__ == "__main__":
    main()
