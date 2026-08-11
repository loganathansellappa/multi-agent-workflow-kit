#!/usr/bin/env python3
"""Deterministic (non-LLM) hard gate that blocks `git push` to protected branches.

Unlike agent-preflight-check (advisory only), this gate is a BLOCKING safety check:
developer agents may push freely to a task/feature branch they created, but must
NEVER push to a repo's protected default branch (main/master, or whatever
`baseBranch` is configured for that repo in agents.config.yaml).

This script does not perform the push itself - it only decides whether a push
of <target-branch> to a given repo is allowed. The calling agent must run this
BEFORE `git push` and abort (ask_user, do not push) on a non-zero exit code.

Exit codes:
  0 = ALLOWED  - target branch is not protected, push may proceed
  3 = BLOCKED  - target branch is a protected branch (main/master/base branch)
  4 = usage/config error (bad invocation, repo not found in config, git failure)

Stdlib only; cross-platform. No network access; does not run `git push` itself.

Known limitations (this is defense-in-depth, not a full guarantee): it checks a
single branch name, so `git push --all`/`--mirror`, tag pushes, and multi-refspec
pushes can bypass it, and it only fires when the agent actually runs it. Pair it
with server-side branch protection for a hard guarantee.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

# Always-protected branch names, regardless of what any config says.
# These are blocked unconditionally, even if a repo's configured baseBranch
# is misconfigured or missing.
HARD_PROTECTED = {"main", "master"}


def _normalize_branch(name):
    """Reduce a refspec/branch string to a bare branch name for comparison.

    Strips a leading force-push '+' and a fully-qualified `refs/heads/` prefix so
    that `+main`, `refs/heads/main`, and `HEAD:refs/heads/main` are all recognised
    as the protected branch `main`. If a full `local:remote` refspec is passed, the
    remote side (after the last colon) is what gets written and is used."""
    if not name:
        return ""
    name = name.strip()
    if ":" in name:
        name = name.split(":")[-1]
    name = name.lstrip("+")
    m = re.match(r"(?i)^refs/heads/(.+)$", name)
    if m:
        name = m.group(1)
    return name.strip()


def read_configured_base_branch(cfg_text: str, repo_path: str):
    """Best-effort lookup of the `baseBranch` for the `services.*` entry whose
    `repoPath` matches repo_path (normalized, case-insensitive). Returns None
    if not found. Uses simple regex scanning to avoid a YAML dependency."""
    norm_target = str(Path(repo_path)).rstrip("\\/").lower()
    # Isolate the top-level `services:` block so we don't match repoPath/baseBranch
    # keys that might appear elsewhere in the config.
    services_block_match = re.search(r"(?ms)^services:\s*.*?(?=^\S)", cfg_text)
    services_block = services_block_match.group(0) if services_block_match else cfg_text
    # Walk each 2-space-indented service entry and its 4-space-indented body.
    for service_match in re.finditer(
        r"(?m)^\s{2}([a-zA-Z0-9_-]+):\s*\n((?:^\s{4}.*\n?)+)", services_block
    ):
        body = service_match.group(2)
        repo_path_match = re.search(r"(?m)^\s{4}repoPath:\s*(.+?)\s*$", body)
        base_branch_match = re.search(r"(?m)^\s{4}baseBranch:\s*(.+?)\s*$", body)
        # Only the service whose repoPath matches this repo contributes a baseBranch.
        if repo_path_match and base_branch_match:
            candidate = str(Path(repo_path_match.group(1).strip())).rstrip("\\/").lower()
            if candidate == norm_target:
                return base_branch_match.group(1).strip()
    return None


def git_current_branch(repo: str):
    # Resolve the checked-out branch so the common `git push`/`git push -u origin
    # HEAD` case needs no explicit --target-branch.
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"ERROR: could not determine current git branch in {repo}: {exc}", file=sys.stderr)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Blocking guard against pushing to a protected branch.")
    ap.add_argument("--config", required=True, help="Path to agents.config.yaml")
    ap.add_argument("--repo", required=True, help="Filesystem path to the repo about to be pushed")
    ap.add_argument(
        "--target-branch",
        default=None,
        help="Branch name being pushed to (defaults to the repo's current checked-out branch, "
             "which covers the common `git push` / `git push -u origin HEAD` case). "
             "Pass explicitly for `git push origin <local>:<remote>` style refspecs.",
    )
    args = ap.parse_args(argv)

    # Config errors are exit 4 (fix the invocation), never a silent pass.
    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 4
    cfg_text = cfg_path.read_text(encoding="utf-8")

    repo_path = Path(args.repo)
    if not repo_path.is_dir():
        print(f"ERROR: repo path not found: {repo_path}", file=sys.stderr)
        return 4

    target = args.target_branch
    if not target:
        # No explicit refspec given -> guard the branch that's currently checked out.
        target = git_current_branch(str(repo_path))
        if target is None:
            return 4

    target_lc = _normalize_branch(target).lower()

    # Protected set = the always-on hard names plus this repo's configured
    # baseBranch (if any). Config can only ADD protection, never remove it, so a
    # missing/typo'd baseBranch can't accidentally expose main/master.
    configured_base = read_configured_base_branch(cfg_text, str(repo_path))
    protected = set(HARD_PROTECTED)
    if configured_base:
        protected.add(configured_base.strip().lower())

    if target_lc in protected:
        # Fail closed: exit 3 tells the agent to STOP and hand off, not retry.
        reason = "hard-coded protected name" if target_lc in HARD_PROTECTED else "configured baseBranch"
        print(f"push-guard: BLOCKED | repo={repo_path} | target-branch={target} | reason={reason}")
        print(f"  Refusing push: '{target}' is a protected branch. "
              f"Push only agent-created task/feature branches; open a PR for {target} instead.")
        return 3

    print(f"push-guard: ALLOWED | repo={repo_path} | target-branch={target} "
          f"| protected-branches={sorted(protected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
