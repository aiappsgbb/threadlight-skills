#!/usr/bin/env python3
"""Tests for `--include-experimental` end-to-end (v0.3.0).

`_score_pillar`-level coverage lives in test_scoring_no_verification_inflation.py.
This file pins the CLI-level contract: experimental finding IDs are
excluded from scoring + manifest by default, included when the flag is
passed.

stdlib-only; no pytest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
SAMPLE_PILOT = SKILL_DIR / "references" / "fixtures" / "sample-pilot"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402

FAILURES: list[str] = []


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def _experimental_ids() -> set[str]:
    return {fid for fid, meta in pr.FINDING_CATALOG.items()
            if meta.get("experimental")}


def _run(*, include: bool) -> int:
    args = [
        sys.executable, str(SCRIPT),
        "--root", str(SAMPLE_PILOT),
        "--static",
        "--in-postdeploy", "tests/postdeploy-manifest.json",
        "--out", "tests/production-readiness-manifest.json",
        "--report", "docs/production-readiness-report.md",
        "--quiet",
    ]
    if include:
        args.append("--include-experimental")
    proc = subprocess.run(args, capture_output=True, text=True,
                          check=False, timeout=180)
    return proc.returncode


def t_experimental_count_in_catalog() -> None:
    """Sanity: at least one experimental ID exists; if not, the rest of the
    suite is meaningless and the catalog has been mis-labelled."""
    print("\nt_experimental_count_in_catalog")
    exp = _experimental_ids()
    expect(len(exp) >= 10,
           f"catalog: ≥10 experimental IDs flagged (got {len(exp)})")


def t_excluded_by_default_in_manifest() -> None:
    """Default run -> manifest contains zero finding IDs flagged experimental."""
    print("\nt_excluded_by_default_in_manifest")
    if not SAMPLE_PILOT.exists():
        expect(False, "fixture: sample-pilot present")
        return
    out = SAMPLE_PILOT / "tests" / "production-readiness-manifest.json"
    rc = _run(include=False)
    expect(rc == 0, f"default-run: exit 0 (got {rc})")
    m = json.loads(out.read_text(encoding="utf-8"))
    exp = _experimental_ids()
    finding_ids = {f["id"]
                   for p in m.get("pillars", [])
                   for f in p.get("findings", [])}
    leaked = finding_ids & exp
    expect(not leaked,
           f"default-run: 0 experimental IDs in manifest (leaked: {sorted(leaked)[:5]})")
    expect(m.get("include_experimental") is False,
           "default-run: include_experimental == False in manifest "
           f"(got: {m.get('include_experimental')!r})")


def t_included_when_flag_set() -> None:
    """With --include-experimental, manifest MAY surface experimental IDs."""
    print("\nt_included_when_flag_set")
    if not SAMPLE_PILOT.exists():
        expect(False, "fixture: sample-pilot present")
        return
    out = SAMPLE_PILOT / "tests" / "production-readiness-manifest.json"
    rc = _run(include=True)
    expect(rc == 0, f"include-run: exit 0 (got {rc})")
    m = json.loads(out.read_text(encoding="utf-8"))
    finding_ids = {f["id"]
                   for p in m.get("pillars", [])
                   for f in p.get("findings", [])}
    exp = _experimental_ids()
    included = finding_ids & exp
    # We can't assert a hard ≥1 here because some experimental IDs only
    # fire under specific postures / live tiers — but the manifest must
    # at minimum record that the flag was on.
    expect(m.get("include_experimental") is True,
           "include-run: include_experimental flag set to True in manifest")
    # Either we got at least one experimental in the manifest OR the
    # manifest carries the flag (preferred) — informational print.
    print(f"  [info] experimental IDs surfaced under --include-experimental: "
          f"{len(included)}")


def main() -> int:
    tests = [
        t_experimental_count_in_catalog,
        t_excluded_by_default_in_manifest,
        t_included_when_flag_set,
    ]
    print(f"Running {len(tests)} experimental-excluded tests")
    for t in tests:
        try:
            t()
        except Exception as exc:  # pragma: no cover
            FAILURES.append(t.__name__)
            print(f"  [FAIL] {t.__name__} raised: {type(exc).__name__}: {exc}")
    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return len(FAILURES)
    print(f"✅ All {len(tests)} test(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
