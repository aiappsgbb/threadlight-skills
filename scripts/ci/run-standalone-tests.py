#!/usr/bin/env python3
"""Run the stdlib-only test files that pytest cannot collect.

Why this exists
---------------
Several suites in this repo are written as stdlib-only scripts: they define
`t_*` functions and a `main()` that returns a failure count, instead of the
`test_*` functions pytest collects. They are named `test_*.py`, so pytest
imports them, reports "0 collected" for the file, and the overall run stays
green — the assertions inside never execute.

Nine such files existed in `threadlight-production-ready` alone, holding 56
assertions. Three of them were red and one was passing for the wrong reason
(the CLI exited 2 from a stale-fixture pre-flight, which the test read as
"the hard gate fired"). None of it was visible in CI.

Listing the files by hand in the workflow would fix today and rot tomorrow:
the next stdlib-only suite someone adds would be invisible again. So this
runner *discovers* them instead — any `tests/test_*.py` under `skills/` that
declares no pytest-collectable `test_*` function but does declare `t_*`
functions or a `main()` is executed directly, and its exit code is honoured.

Usage:
    python scripts/ci/run-standalone-tests.py [skills/<name> ...]

With no arguments every skill is scanned. Exit code 0 when all discovered
suites pass, 1 otherwise.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# A file pytest can collect: a module-level or class-level `def test_...`.
PYTEST_FUNC = re.compile(r"^\s*def test_\w*\s*\(", re.M)
# The stdlib-only convention used across this repo.
STANDALONE_FUNC = re.compile(r"^def (?:t_\w+|main)\s*\(", re.M)


def discover(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        for path in sorted(root.glob("*/tests/test_*.py")):
            src = path.read_text(encoding="utf-8")
            if PYTEST_FUNC.search(src):
                continue  # pytest owns this file
            if not STANDALONE_FUNC.search(src):
                continue  # no runnable entry point; nothing to do
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    if argv:
        roots = [REPO_ROOT / a for a in argv]
    else:
        roots = [REPO_ROOT / "skills"]

    suites = discover(roots)
    if not suites:
        print("no standalone (non-pytest) test suites discovered")
        return 0

    print(f"Running {len(suites)} standalone test suite(s)\n")
    failed: list[str] = []
    for path in suites:
        rel = path.relative_to(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=path.parent.parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"[{status}] {rel} (exit {proc.returncode})")
        if proc.returncode != 0:
            failed.append(str(rel))
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)

    print()
    if failed:
        print(f"FAIL: {len(failed)} standalone suite(s) failed:")
        for f in failed:
            print(f"  - {f}")
        return 1
    print(f"OK: all {len(suites)} standalone suite(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
