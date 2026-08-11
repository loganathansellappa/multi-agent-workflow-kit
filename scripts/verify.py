#!/usr/bin/env python3
"""One-command verification gate for the agent kit — the distributable harness.

The kit is hand-authored and passed dev-to-dev: whoever receives it must be able to
prove it is sound *without* CI. This script is that proof. It runs the whole
deterministic harness in order and fails fast on the first breakage:

  1. Agent lint         (scripts/validate_agents.py) — frontmatter, hardening,
     reviewer allowlist, skill refs, no leaked paths.
  2. Unit + contract tests (run_tests.py) — grader unit tests, validator tests, and
     the capability-contract test that locks each agent's tool grants.
  3. Behavior eval      (eval/run_eval.py, non-strict) — grades captured golden
     responses; PENDING cases (no response yet) do not fail the gate.

Exit code: 0 only if every stage passes; non-zero (and the failing stage's output)
otherwise. Run this before committing any agent/skill change and before handing the
kit to another developer:

    python scripts/verify.py            # full gate
    python scripts/verify.py --strict   # also fail on eval PENDING cases

Stdlib only; cross-platform; no third-party deps.
"""
import argparse
import subprocess
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent


def _run(title, argv):
    print(f"\n{'=' * 70}\n== {title}\n{'=' * 70}", flush=True)
    proc = subprocess.run([sys.executable, *argv], cwd=str(KIT_ROOT))
    ok = proc.returncode == 0
    print(f"-- {title}: {'PASS' if ok else 'FAIL'} (exit {proc.returncode})", flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser(description="Run the full agent-kit verification gate.")
    ap.add_argument("--strict", action="store_true",
                    help="Treat eval PENDING (no captured response) cases as failures.")
    args = ap.parse_args()

    stages = [
        ("Agent lint (validate_agents.py)", ["scripts/validate_agents.py"]),
        ("Unit + contract tests (run_tests.py)", ["run_tests.py"]),
    ]
    # The behavior eval is optional — only present in kits that ship eval/.
    if (KIT_ROOT / "eval" / "run_eval.py").exists():
        stages.append(("Behavior eval (run_eval.py)",
                       ["eval/run_eval.py"] + (["--strict"] if args.strict else [])))

    results = [(title, _run(title, argv)) for title, argv in stages]

    print(f"\n{'=' * 70}\n== VERIFY SUMMARY\n{'=' * 70}")
    for title, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {title}")
    all_ok = all(ok for _, ok in results)
    print(f"\nGate: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
