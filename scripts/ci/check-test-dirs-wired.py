#!/usr/bin/env python3
"""Assert every skills/*/tests/ directory is actually wired into CI.

`.github/workflows/python-pytest.yml` hardcodes one step per test directory.
That is fine until someone adds a new suite: pytest passes locally, the PR goes
green, and the suite simply never runs in CI. Nothing fails — the tests are just
silently unwatched, which is worse than not having them, because the green check
is now lying about coverage.

This is the same failure class the repo already fixed once for stdlib-only
suites (`run-standalone-tests.py` discovers rather than hard-codes). This script
closes it for pytest suites too: it does not run any tests, it only checks that
every suite has somewhere to run.

Exit 0 when every test directory is referenced, 1 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "python-pytest.yml"
SKILLS_DIR = REPO_ROOT / "skills"


def main() -> int:
    if not WORKFLOW.is_file():
        print(f"ERROR: {WORKFLOW.relative_to(REPO_ROOT)} not found", file=sys.stderr)
        return 1

    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    test_dirs = sorted(
        p for p in SKILLS_DIR.glob("*/tests") if p.is_dir() and any(p.glob("test_*.py"))
    )

    if not test_dirs:
        print("ERROR: no skills/*/tests directories found — is the layout right?",
              file=sys.stderr)
        return 1

    unwired = []
    for test_dir in test_dirs:
        rel = test_dir.relative_to(REPO_ROOT).as_posix()
        if rel not in workflow_text:
            unwired.append(rel)

    if unwired:
        print(
            "ERROR: these pytest suites exist but are not referenced in "
            f"{WORKFLOW.relative_to(REPO_ROOT)}, so they never run in CI:",
            file=sys.stderr,
        )
        for rel in unwired:
            print(f"  - {rel}", file=sys.stderr)
        print(
            "\nAdd a step to the workflow:\n"
            f'      - name: Run <skill> tests\n'
            f'        run: python -m pytest {unwired[0]}/ -v',
            file=sys.stderr,
        )
        return 1

    print(f"OK: all {len(test_dirs)} pytest suites are wired into CI")
    for test_dir in test_dirs:
        print(f"  - {test_dir.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
