#!/usr/bin/env python3
"""Deterministic (non-LLM) workflow-state store: persist + validate the invariants
an orchestrated run relies on, so a resumed run RE-VALIDATES against live git
instead of trusting stale prose in the transcript.

WHY THIS EXISTS
---------------
Copilot CLI `/resume` restores the *conversation* (events + tool I/O) and the
tool session DB. It does NOT persist, and never re-checks, workflow invariants
such as: what files were planned, which commit a gate/review passed against, or
whether the tree still matches. This script is that missing piece. It is the
data layer the reviewer asked for: state is written after each stage, and
`validate` re-checks it against the live repo on resume.

It backs three review items:
  * #6 scope-drift  -> `drift`   (planned files vs actually-changed files)
  * #4 immutable/stale evidence & review -> SHA stamped on gate/review/evidence,
                       `validate` flags anything recorded against an old HEAD
  * #10 resume safety -> `validate` (re-check recorded state vs live git)
It is also read by the push-guard (skill + hook) for the soft gate-status block.

WHERE STATE LIVES
-----------------
`<repo>/.git/copilot-workflow-state.json` when the repo has a `.git` directory
(so it is never committed), else `<repo>/.copilot-workflow-state.json`. One file
per repo, keyed by nothing else, so any tool (hook included) can find it from the
repo path alone.

Stdlib only; cross-platform; no network. Never mutates the repo (only reads git
and writes its own state file).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
STATE_BASENAME = "copilot-workflow-state.json"

# Explicit terminal states (review #11). Storage lives here; the human-readable
# schema is documented in the e2e agent's HANDOFF section.
TERMINAL_STATES = {
    "SUCCESS", "FAILED", "BLOCKED", "ESCALATED", "LOOP_LIMIT", "BUDGET_EXCEEDED",
    "NEEDS_CLARIFICATION", "NEEDS_AUTHORIZATION", "NEEDS_HUMAN_REVIEW",
    "TECHNICAL_BLOCK", "POLICY_BLOCK", "ENVIRONMENT_FAILURE",
}
GATE_STATUSES = {"PASS", "FAIL"}

# Files whose edit means an agent is changing its own governance (review #8).
# Detection is WARN-only: we surface it, we never block.
GOVERNANCE_GLOBS = ("*.agent.md", "service-path.config.yaml", "*.policy.md")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _state_path(repo: Path) -> Path:
    git_dir = repo / ".git"
    if git_dir.is_dir():
        return git_dir / STATE_BASENAME
    return repo / ("." + STATE_BASENAME)


def _load(repo: Path) -> dict:
    p = _state_path(repo)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            # Never silently discard a possibly-load-bearing record (e.g. gate FAIL):
            # warn on stderr so the loss is visible, then fall through to fresh state.
            print(f"workflow-state: WARNING - state file unreadable/corrupt at {p} "
                  f"({exc}); treating as empty", file=sys.stderr)
    return {"schema": SCHEMA, "repo": str(repo), "updated_at": None,
            "stage": None, "terminal": None, "plan": None, "gate": None,
            "reviews": {}, "evidence": None}


def _save(repo: Path, state: dict):
    state["schema"] = SCHEMA
    state["repo"] = str(repo)
    state["updated_at"] = _now()
    p = _state_path(repo)
    # Atomic write: a crash or a concurrent invocation must never leave a torn
    # file that _load would then treat as "no record" (silently dropping a FAIL).
    data = json.dumps(state, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _git(repo: Path, *args, timeout=10):
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout.strip(), out.stderr.strip()
    except Exception as exc:  # git missing, timeout, etc.
        return 1, "", str(exc)


def _head_sha(repo: Path):
    rc, out, _ = _git(repo, "rev-parse", "HEAD")
    return out if rc == 0 and out else None


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip().lstrip("./")


def _changed_files(repo: Path, base_sha=None):
    """Union of files changed vs base (if given), plus staged, unstaged, untracked."""
    files = set()
    specs = [("diff", "--name-only"), ("diff", "--name-only", "--cached")]
    if base_sha:
        specs.insert(0, ("diff", "--name-only", f"{base_sha}..HEAD"))
    for spec in specs:
        rc, out, _ = _git(repo, *spec)
        if rc == 0 and out:
            files.update(_norm(x) for x in out.splitlines() if x.strip())
    rc, out, _ = _git(repo, "ls-files", "--others", "--exclude-standard")
    if rc == 0 and out:
        files.update(_norm(x) for x in out.splitlines() if x.strip())
    return files


def _uncommitted_files(repo: Path):
    """Files with edits NOT yet in HEAD (working tree + staged + untracked).

    A gate/review/evidence stamped at HEAD does NOT cover these — SHA equality
    alone would wrongly report 'fresh' (review #4/#5)."""
    files = set()
    for spec in (("diff", "--name-only"), ("diff", "--name-only", "--cached"),
                 ("ls-files", "--others", "--exclude-standard")):
        rc, out, _ = _git(repo, *spec)
        if rc == 0 and out:
            files.update(_norm(x) for x in out.splitlines() if x.strip())
    return files


# ----------------------------- subcommands --------------------------------

def cmd_set_plan(repo, args):
    st = _load(repo)
    st["plan"] = {"files": sorted({_norm(f) for f in args.files}),
                  "base_sha": _head_sha(repo), "set_at": _now()}
    _save(repo, st)
    print(f"workflow-state: plan set | {len(st['plan']['files'])} file(s) | base={st['plan']['base_sha']}")
    return 0


def cmd_set_gate(repo, args):
    status = args.status.upper()
    if status not in GATE_STATUSES:
        print(f"ERROR: --status must be one of {sorted(GATE_STATUSES)}", file=sys.stderr)
        return 4
    st = _load(repo)
    st["gate"] = {"status": status, "sha": _head_sha(repo),
                  "detail": args.detail or "", "set_at": _now()}
    _save(repo, st)
    print(f"workflow-state: gate {status} | sha={st['gate']['sha']}")
    return 0


def cmd_set_review(repo, args):
    st = _load(repo)
    st.setdefault("reviews", {})[args.layer] = {
        "sha": args.sha or _head_sha(repo),
        "status": (args.status or "PASS").upper(), "set_at": _now()}
    _save(repo, st)
    print(f"workflow-state: review[{args.layer}]={st['reviews'][args.layer]['status']} "
          f"| sha={st['reviews'][args.layer]['sha']}")
    return 0


def cmd_set_evidence(repo, args):
    st = _load(repo)
    st["evidence"] = {"sha": args.sha or _head_sha(repo), "set_at": _now()}
    _save(repo, st)
    print(f"workflow-state: evidence stamped | sha={st['evidence']['sha']}")
    return 0


def cmd_set_stage(repo, args):
    st = _load(repo)
    st["stage"] = args.stage
    _save(repo, st)
    print(f"workflow-state: stage={args.stage}")
    return 0


def cmd_set_terminal(repo, args):
    state = args.state.upper()
    if state not in TERMINAL_STATES:
        print(f"ERROR: --state must be one of {sorted(TERMINAL_STATES)}", file=sys.stderr)
        return 4
    st = _load(repo)
    st["terminal"] = {"state": state, "note": args.note or "", "set_at": _now()}
    _save(repo, st)
    print(f"workflow-state: terminal={state}")
    return 0


def cmd_get(repo, args):
    print(json.dumps(_load(repo), indent=2))
    return 0


def cmd_gate_status(repo, args):
    """Print PASS / FAIL / NONE - consumed by the push-guard soft gate."""
    st = _load(repo)
    gate = st.get("gate") or {}
    print(gate.get("status", "NONE") or "NONE")
    return 0


def cmd_drift(repo, args):
    """Scope-drift (review #6): files changed but not planned => drift."""
    st = _load(repo)
    plan = st.get("plan")
    if not plan:
        print("workflow-state drift: no plan recorded - run `set-plan` first (skipping, not a drift).")
        return 0
    planned = set(plan.get("files", []))
    actual = _changed_files(repo, plan.get("base_sha"))
    unexpected = sorted(actual - planned)
    untouched = sorted(planned - actual)
    gov = [f for f in unexpected if _is_governance(f)]
    print(f"workflow-state drift: planned={len(planned)} actual={len(actual)} "
          f"unexpected={len(unexpected)} untouched-planned={len(untouched)}")
    for f in unexpected:
        tag = " [GOVERNANCE]" if f in gov else ""
        print(f"  + UNPLANNED: {f}{tag}")
    for f in untouched:
        print(f"  . planned-but-untouched: {f}")
    if unexpected:
        print("workflow-state drift: BLOCK - unexpected files changed; update the plan or get approval.")
        return 3
    print("workflow-state drift: OK - actual changes are within the plan.")
    return 0


def _is_governance(path: str) -> bool:
    name = Path(path).name
    return any(Path(name).match(g) or Path(path).match(g) for g in GOVERNANCE_GLOBS)


def cmd_governance_check(repo, args):
    """Warn-only (review #8): flag changed governance files; never block."""
    files = {_norm(f) for f in args.files} if args.files else _changed_files(repo)
    flagged = sorted(f for f in files if _is_governance(f))
    if flagged:
        print("workflow-state governance: WARN - this run edits governance/policy files:")
        for f in flagged:
            print(f"  ! {f}")
        print("  These change the rules that govern agents. Call them out in the handoff "
              "for explicit human review before merge.")
    else:
        print("workflow-state governance: OK - no governance/policy files changed.")
    return 0  # warn-only: always success


def cmd_validate(repo, args):
    """Validate-on-resume (review #10 + #4): re-check recorded state vs live git.
    Exit 3 if anything is stale/invalid; 0 if everything is fresh."""
    st = _load(repo)
    head = _head_sha(repo)
    problems = []
    print(f"workflow-state validate: repo={repo} HEAD={head}")

    if head is None:
        print("  ! cannot resolve HEAD (not a git repo / git unavailable) - cannot validate.")
        return 3

    # Uncommitted edits are not covered by any HEAD-stamped record: a gate/review
    # stamped at HEAD can be SHA-"fresh" yet no longer match the working tree.
    dirty = _uncommitted_files(repo)

    gate = st.get("gate")
    if gate:
        fresh = gate.get("sha") == head and not dirty
        why = "fresh" if fresh else (
            "STALE - uncommitted edits since stamp" if gate.get("sha") == head
            else "STALE - HEAD moved")
        print(f"  gate: {gate.get('status')} @ {gate.get('sha')} ({why})")
        if gate.get("status") == "FAIL":
            problems.append("last recorded gate is FAIL")
        if gate.get("sha") != head:
            problems.append("gate was recorded against an older commit - re-run the gate")
        elif dirty:
            problems.append("gate is stamped at HEAD but the tree has uncommitted edits - re-run the gate")

    for layer, rv in (st.get("reviews") or {}).items():
        fresh = rv.get("sha") == head and not dirty
        why = "fresh" if fresh else (
            "STALE - uncommitted edits since review" if rv.get("sha") == head
            else "STALE - re-review")
        print(f"  review[{layer}]: {rv.get('status')} @ {rv.get('sha')} ({why})")
        if rv.get("sha") != head:
            problems.append(f"review[{layer}] is stale - re-review at current HEAD")
        elif dirty:
            problems.append(f"review[{layer}] predates uncommitted edits - re-review")
        if rv.get("status") == "FAIL":
            problems.append(f"review[{layer}] is FAIL")

    ev = st.get("evidence")
    if ev:
        fresh = ev.get("sha") == head and not dirty
        why = "fresh" if fresh else (
            "STALE - uncommitted edits since gather" if ev.get("sha") == head
            else "STALE - re-gather/re-cite")
        print(f"  evidence: @ {ev.get('sha')} ({why})")
        if ev.get("sha") != head:
            problems.append("evidence was gathered against an older commit - treat as stale")
        elif dirty:
            problems.append("evidence predates uncommitted edits - re-gather/re-cite")

    if dirty and (gate or st.get("reviews") or ev):
        print(f"  note: {len(dirty)} uncommitted change(s) not covered by any HEAD-stamped record")

    plan = st.get("plan")
    if plan:
        actual = _changed_files(repo, plan.get("base_sha"))
        unexpected = sorted(actual - set(plan.get("files", [])))
        print(f"  plan: {len(plan.get('files', []))} planned | {len(unexpected)} unplanned change(s)")
        if unexpected:
            problems.append(f"{len(unexpected)} unplanned file change(s) (scope drift)")

    term = st.get("terminal")
    if term:
        print(f"  terminal: {term.get('state')}")

    if problems:
        print("workflow-state validate: NOT SAFE TO RESUME AS-IS -")
        for p in problems:
            print(f"    - {p}")
        return 3
    print("workflow-state validate: OK - recorded state matches live git.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="Persist + validate orchestration workflow state.")
    ap.add_argument("--repo", required=True, help="absolute path to the repo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("set-plan", help="record the planned file set (+ base SHA)")
    p.add_argument("--files", nargs="+", required=True)
    p.set_defaults(fn=cmd_set_plan)

    p = sub.add_parser("set-gate", help="record deterministic gate status (+ SHA)")
    p.add_argument("--status", required=True)
    p.add_argument("--detail", default="")
    p.set_defaults(fn=cmd_set_gate)

    p = sub.add_parser("set-review", help="record a layer review result (+ SHA)")
    p.add_argument("--layer", required=True)
    p.add_argument("--sha", default=None)
    p.add_argument("--status", default="PASS")
    p.set_defaults(fn=cmd_set_review)

    p = sub.add_parser("set-evidence", help="stamp the SHA evidence was gathered at")
    p.add_argument("--sha", default=None)
    p.set_defaults(fn=cmd_set_evidence)

    p = sub.add_parser("set-stage", help="record the current stage name")
    p.add_argument("--stage", required=True)
    p.set_defaults(fn=cmd_set_stage)

    p = sub.add_parser("set-terminal", help="record an explicit terminal/block state")
    p.add_argument("--state", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_set_terminal)

    p = sub.add_parser("get", help="print the full state JSON")
    p.set_defaults(fn=cmd_get)

    p = sub.add_parser("gate-status", help="print PASS/FAIL/NONE (for push-guard)")
    p.set_defaults(fn=cmd_gate_status)

    p = sub.add_parser("drift", help="scope-drift check: changed vs planned files")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("governance-check", help="warn-only: flag changed governance files")
    p.add_argument("--files", nargs="*", default=None)
    p.set_defaults(fn=cmd_governance_check)

    p = sub.add_parser("validate", help="validate-on-resume: re-check state vs live git")
    p.set_defaults(fn=cmd_validate)
    return ap


def main(argv=None):
    # Guard against a non-UTF-8 console code page (Windows cp1252): a repo path or
    # detail string with non-ASCII chars must never crash a print() with
    # UnicodeEncodeError and break the documented 0/3/4 exit contract.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"ERROR: repo path not found: {repo}", file=sys.stderr)
        return 4
    return args.fn(repo, args)


if __name__ == "__main__":
    sys.exit(main())
