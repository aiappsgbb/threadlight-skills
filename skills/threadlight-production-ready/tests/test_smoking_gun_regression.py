#!/usr/bin/env python3
"""Smoking-gun regression test (v0.3.0).

This is the test the v0.3.0 overhaul exists to make green. Before v0.3.0
the production-readiness skill scored a comment-only Bicep pilot as
"READY WITH WAIVERS" because the regex parser matched substrings inside
Bicep comments. v0.3.0 replaces that parser with BicepGraph (real ARM
compile) and tightens scoring so verification debt no longer inflates.

This test runs the CLI against `references/fixtures/sample-pilot-broken`
end-to-end and asserts:

  1. Exit code is 0 (the CLI completed; it does not crash on a hostile pilot).
  2. The recommendation is NOT one of the "ready_*" variants — broken
     pilots must never be reported as production-ready.
  3. A meaningful number of critical IDs are non-pass (we encode the
     "any non-pass" interpretation: at least 10 of the 16 critical IDs
     must be in {must-fix, should-fix, not-verified, not-applicable}).
  4. The vnet/APIM-detection findings under network-posture do NOT pass
     (these are the literal smoking-gun IDs).

stdlib-only; no pytest. Run with:

    python skills/threadlight-production-ready/tests/test_smoking_gun_regression.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
FIXTURE = SKILL_DIR / "references" / "fixtures" / "sample-pilot-broken"

FAILURES: list[str] = []

# Critical IDs the kickoff handoff doc says MUST not pass on a broken
# pilot. Mix of network, identity, secrets, observability, reliability,
# AGT and model lifecycle.
CRITICAL_IDS = [
    "NET-001", "NET-002", "NET-003", "NET-004",
    "IAM-001", "IAM-005",
    "SEC-001", "SEC-005", "SEC-006",
    "OBS-001", "OBS-003",
    "REL-002", "REL-003",
    "AGT-001",
    "MDL-001", "MDL-002",
]


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def t_runs_and_returns_zero() -> None:
    print("\nt_runs_and_returns_zero")
    if not FIXTURE.exists():
        expect(False, "fixture: sample-pilot-broken present")
        return
    out_manifest = FIXTURE / "tests" / "production-readiness-manifest.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--root", str(FIXTURE),
         "--static",
         "--in-postdeploy", "tests/postdeploy-manifest.json",
         "--out", "tests/production-readiness-manifest.json",
         "--report", "docs/production-readiness-report.md",
         "--quiet"],
        capture_output=True, text=True, check=False, timeout=180,
    )
    expect(proc.returncode == 0,
           f"cli: exit 0 (got {proc.returncode}; stderr: {proc.stderr[:300]})")
    expect(out_manifest.exists(), "cli: manifest written")


def _load_manifest() -> dict:
    out_manifest = FIXTURE / "tests" / "production-readiness-manifest.json"
    return json.loads(out_manifest.read_text(encoding="utf-8"))


def t_recommendation_is_not_ready_variant() -> None:
    print("\nt_recommendation_is_not_ready_variant")
    m = _load_manifest()
    rec = m.get("go_live_recommendation", "")
    expect(not rec.startswith("ready"),
           f"recommendation: not a 'ready_*' variant (got {rec!r})")


def t_critical_ids_non_pass_count() -> None:
    """At least 10/16 critical IDs must be in a non-pass state."""
    print("\nt_critical_ids_non_pass_count")
    m = _load_manifest()
    by_id: dict[str, dict] = {}
    for pillar in m.get("pillars", []):
        for f in pillar.get("findings", []):
            by_id[f["id"]] = f
    non_pass: list[str] = []
    pass_ids: list[str] = []
    for fid in CRITICAL_IDS:
        f = by_id.get(fid)
        if f is None:
            # ID didn't even run -> verification debt, counts as non-pass
            non_pass.append(fid)
            continue
        status = f.get("status", "")
        if status != "pass":
            non_pass.append(fid)
        else:
            pass_ids.append(fid)
    expect(len(non_pass) >= 10,
           f"critical-ids: {len(non_pass)}/16 non-pass "
           f"(threshold ≥10; passing IDs: {pass_ids})")


def t_smoking_gun_ids_do_not_pass() -> None:
    """The literal smoking-gun IDs MUST not pass on the broken fixture.

    NET-001 (vnet exists) and SEC-001 (key vault exists) were the IDs
    that incorrectly passed against the comment-only Bicep under the
    v0.2.0 regex parser. They are the regression contract.
    """
    print("\nt_smoking_gun_ids_do_not_pass")
    m = _load_manifest()
    by_id = {f["id"]: f for p in m.get("pillars", []) for f in p.get("findings", [])}
    for fid in ("NET-001", "NET-002", "SEC-001"):
        f = by_id.get(fid)
        if f is None:
            expect(False, f"smoking-gun: {fid} present in manifest")
            continue
        expect(f.get("status") != "pass",
               f"smoking-gun: {fid} does NOT pass (got {f.get('status')!r})")


def t_verification_debt_surfaced() -> None:
    """The new top-level verification_debt field must be present and >0."""
    print("\nt_verification_debt_surfaced")
    m = _load_manifest()
    vd = m.get("verification_debt") or {}
    expect("total" in vd, "verification_debt: 'total' key present")
    expect(isinstance(vd.get("by_pillar"), dict), "verification_debt: by_pillar dict present")
    expect(vd.get("total", 0) > 0,
           f"verification_debt: total > 0 on broken fixture (got {vd.get('total')})")


def main() -> int:
    tests = [
        t_runs_and_returns_zero,
        t_recommendation_is_not_ready_variant,
        t_critical_ids_non_pass_count,
        t_smoking_gun_ids_do_not_pass,
        t_verification_debt_surfaced,
    ]
    print(f"Running {len(tests)} smoking-gun regression tests against {FIXTURE.name}")
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
