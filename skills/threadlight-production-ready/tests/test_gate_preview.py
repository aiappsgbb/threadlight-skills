#!/usr/bin/env python3
"""Tests for `--gate-preview` (v0.3.0).

When `--gate-preview` is set, the CLI exits 2 if any must-fix finding
would fail the production-readiness hard gate (regardless of waivers).
This is the flag the v0.3.0 GitHub Actions workflow uses on `push` to
turn a NOT READY report into a failing pipeline.

We exercise both branches end-to-end on the existing sample-pilot
fixtures rather than fabricating synthetic manifests, so the test also
pins the manifest's `would_fail_hard_gate` field.

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
SAMPLE_BROKEN = SKILL_DIR / "references" / "fixtures" / "sample-pilot-broken"

FAILURES: list[str] = []


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def _run(root: Path, *, gate_preview: bool) -> subprocess.CompletedProcess:
    args = [
        sys.executable, str(SCRIPT),
        "--root", str(root),
        "--static",
        "--in-postdeploy", "tests/postdeploy-manifest.json",
        "--out", "tests/production-readiness-manifest.json",
        "--report", "docs/production-readiness-report.md",
        "--quiet",
    ]
    if gate_preview:
        args.append("--gate-preview")
    return subprocess.run(args, capture_output=True, text=True,
                          check=False, timeout=180)


def t_broken_fixture_gate_preview_exits_2() -> None:
    """sample-pilot-broken has must-fix findings -> --gate-preview -> exit 2."""
    print("\nt_broken_fixture_gate_preview_exits_2")
    if not SAMPLE_BROKEN.exists():
        expect(False, "fixture: sample-pilot-broken present")
        return
    proc = _run(SAMPLE_BROKEN, gate_preview=True)
    expect(proc.returncode == 2,
           f"broken+gate: exit 2 (got {proc.returncode}; "
           f"stderr: {proc.stderr[:200]})")
    expect("must-fix" in (proc.stderr or "").lower(),
           "broken+gate: stderr mentions must-fix block")


def t_broken_fixture_no_gate_exits_0() -> None:
    """Same fixture WITHOUT --gate-preview still exits 0 (report only)."""
    print("\nt_broken_fixture_no_gate_exits_0")
    if not SAMPLE_BROKEN.exists():
        expect(False, "fixture: sample-pilot-broken present")
        return
    proc = _run(SAMPLE_BROKEN, gate_preview=False)
    expect(proc.returncode == 0,
           f"broken-no-gate: exit 0 (got {proc.returncode}; "
           f"stderr: {proc.stderr[:200]})")


def t_manifest_would_fail_hard_gate_field() -> None:
    """Manifest must surface a boolean `would_fail_hard_gate` field."""
    print("\nt_manifest_would_fail_hard_gate_field")
    if not SAMPLE_BROKEN.exists():
        expect(False, "fixture: sample-pilot-broken present")
        return
    out = SAMPLE_BROKEN / "tests" / "production-readiness-manifest.json"
    m = json.loads(out.read_text(encoding="utf-8"))
    expect("would_fail_hard_gate" in m,
           "manifest: would_fail_hard_gate key present")
    expect(m.get("would_fail_hard_gate") is True,
           "manifest: would_fail_hard_gate True on broken fixture")


def main() -> int:
    tests = [
        t_broken_fixture_no_gate_exits_0,  # run first; populates manifest
        t_broken_fixture_gate_preview_exits_2,
        t_manifest_would_fail_hard_gate_field,
    ]
    print(f"Running {len(tests)} --gate-preview tests")
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
