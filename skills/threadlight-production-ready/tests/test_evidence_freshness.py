#!/usr/bin/env python3
"""Boundary tests for per-evidence freshness (issue #22).

Pins the contract for `evidence_freshness` and the executive-summary
staleness banner. stdlib-only; no pytest. Run with:

    python skills/threadlight-production-ready/tests/test_evidence_freshness.py

Exit codes: 0 = all green, N = number of failed assertions.

Test catalog (matches ADR `2026-06-10-per-evidence-freshness-design.md`):

    t_static_mode                — empty register → null timestamps, stale=false
    t_fresh_evidence             — 5 min old   → stale=false, no banner
    t_stale_evidence             — 30 h old    → stale=true,  banner present
    t_exact_boundary             — exactly 24h → stale=false (strict ">")
    t_unparseable_only           — bad ISO     → null + warning, stale=false
    t_mixed_parseable            — bad+good    → freshness from good, warning
    t_custom_flag                — --freshness-hours 72 + 30h row → stale=false
    t_freshness_hours_coupling   — single flag drives both gates (regression pin)
    t_clock_skew                 — captured_at > checked_at → clamped, warning
    t_renderer_manifest_agree    — banner present iff stale=true
    t_renderer_banner_wording    — exact regex pin on the exec-summary bullet text
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Layout: tests/  → skills/threadlight-production-ready/  → skills/  → repo
# ---------------------------------------------------------------------------

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
SAMPLE_PILOT = SKILL_DIR / "references" / "fixtures" / "sample-pilot"
REPO = SKILL_DIR.parent.parent

# Import the module under test as a sibling, not via the package path.
sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

FAILURES: list[str] = []


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def ts(delta_hours: float, *, base: datetime | None = None) -> str:
    base = base or datetime.now(timezone.utc)
    t = base - timedelta(hours=delta_hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def mk_evidence(captured_at: str, *, ref: str = "E-TEST-001", pillar: str = "network-posture",
                command: str = "az test", result: str = "ok") -> "pr.EvidenceEntry":
    return pr.EvidenceEntry(
        ref=ref, pillar=pillar, description="test row",
        command=command, scope="sub=x rg=y", tier=1,
        captured_at=captured_at, result=result, notes="",
    )


# ---------------------------------------------------------------------------
# Helper-level tests (import _compute_evidence_freshness directly)
# ---------------------------------------------------------------------------


def t_static_mode() -> None:
    print("\nt_static_mode")
    checked_at = ts(0)
    block = pr._compute_evidence_freshness([], checked_at, freshness_hours=24)
    expect(block["oldest_captured_at"] is None, "static: oldest null")
    expect(block["newest_captured_at"] is None, "static: newest null")
    expect(block["span_hours"] is None, "static: span null")
    expect(block["stale"] is False, "static: not stale")
    expect(block["threshold_hours"] == 24, "static: threshold echoed")


def t_fresh_evidence() -> None:
    print("\nt_fresh_evidence")
    checked_at = ts(0)
    ev = [mk_evidence(ts(5 / 60))]  # 5 minutes ago
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=24)
    expect(block["stale"] is False, "fresh: not stale")
    expect(block["span_hours"] == 0, "fresh: span 0 hours (5 min < 1h rounds down)")
    expect(block["oldest_captured_at"] == ev[0].captured_at, "fresh: oldest = the one row")
    expect(block["newest_captured_at"] == ev[0].captured_at, "fresh: newest = the one row")


def t_stale_evidence() -> None:
    print("\nt_stale_evidence")
    checked_at = ts(0)
    ev = [mk_evidence(ts(30))]  # 30 hours ago
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=24)
    expect(block["stale"] is True, "stale: flag set")
    expect(block["span_hours"] == 0, "stale: only one row → span 0")


def t_exact_boundary() -> None:
    print("\nt_exact_boundary")
    # Exactly 24h before checked_at — must not be stale (strict ">" per D6)
    checked_at = ts(0)
    ev = [mk_evidence(ts(24))]
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=24)
    expect(block["stale"] is False, "exact-24h: NOT stale (strict >)")
    # 24h + 1s should be stale
    base = datetime.now(timezone.utc)
    just_over = (base - timedelta(hours=24, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    block2 = pr._compute_evidence_freshness(
        [mk_evidence(just_over)],
        base.strftime("%Y-%m-%dT%H:%M:%SZ"),
        freshness_hours=24,
    )
    expect(block2["stale"] is True, "24h+1s: stale")


def t_unparseable_only() -> None:
    print("\nt_unparseable_only")
    checked_at = ts(0)
    ev = [mk_evidence("not-a-timestamp"), mk_evidence("")]
    warnings: list[str] = []
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=24, warnings=warnings)
    expect(block["oldest_captured_at"] is None, "unparseable-only: oldest null")
    expect(block["newest_captured_at"] is None, "unparseable-only: newest null")
    expect(block["span_hours"] is None, "unparseable-only: span null")
    expect(block["stale"] is False, "unparseable-only: not stale (we can't know)")
    expect(
        any("could not be evaluated" in w or "unparseable" in w.lower() for w in warnings),
        "unparseable-only: loud warning surfaced",
    )


def t_mixed_parseable() -> None:
    print("\nt_mixed_parseable")
    checked_at = ts(0)
    ev = [
        mk_evidence(ts(2), ref="E-A"),
        mk_evidence(ts(10), ref="E-B"),
        mk_evidence("garbage", ref="E-BAD"),
    ]
    warnings: list[str] = []
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=24, warnings=warnings)
    expect(block["oldest_captured_at"] == ev[1].captured_at, "mixed: oldest = 10h row")
    expect(block["newest_captured_at"] == ev[0].captured_at, "mixed: newest = 2h row")
    expect(block["span_hours"] == 8, "mixed: span 8h")
    expect(block["stale"] is False, "mixed: 10h < 24h, not stale")
    expect(any("E-BAD" in w or "1 evidence" in w for w in warnings),
           "mixed: warning enumerates the skipped row count or ref")


def t_clock_skew() -> None:
    print("\nt_clock_skew")
    # captured_at is in the FUTURE relative to checked_at (clock skew on probe host)
    checked_at = ts(0)
    ev = [mk_evidence(ts(-2))]  # 2 hours in the future
    warnings: list[str] = []
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=24, warnings=warnings)
    expect(block["stale"] is False, "skew: future captured_at must not flag stale")
    expect(block["span_hours"] == 0, "skew: span clamped to 0")
    expect(any("future" in w.lower() or "skew" in w.lower() or "clock" in w.lower()
               for w in warnings),
           "skew: warning surfaced")


def t_custom_flag_in_helper() -> None:
    print("\nt_custom_flag_in_helper")
    # 30h-old row with threshold 72 → not stale
    checked_at = ts(0)
    ev = [mk_evidence(ts(30))]
    block = pr._compute_evidence_freshness(ev, checked_at, freshness_hours=72)
    expect(block["stale"] is False, "custom-flag: 30h < 72h, not stale")
    expect(block["threshold_hours"] == 72, "custom-flag: threshold echoed")


def t_renderer_manifest_agree() -> None:
    print("\nt_renderer_manifest_agree")
    # Build a minimal manifest and render — banner must appear iff stale=true.
    base_manifest = {
        "schema_version": "1.0",
        "tool": "threadlight-production-ready",
        "tool_version": pr.VERSION,
        "checked_at": ts(0),
        "mode": "full",
        "agt_profile": "v3_7",
        "posture": {"declared": None, "detected": None, "resolved": "standard-ai-gateway",
                    "resolution_path": []},
        "score": {"raw_percent": 80, "with_waivers_percent": 80},
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "permission_tiers": {"0": True},
        "warnings": [],
        "safe_check_reference": {"path": "x", "checked_at": ts(0), "subscription_id": "s",
                                  "resource_group": "r"},
        "pillars": [],
        "evidence_register": [],
        "waivers": [],
        "not_verified_count": 0,
    }

    # Stale case
    m_stale = dict(base_manifest)
    m_stale["evidence_freshness"] = {
        "oldest_captured_at": ts(30),
        "newest_captured_at": ts(30),
        "span_hours": 0,
        "stale": True,
        "threshold_hours": 24,
    }
    md_stale = pr._render_report(m_stale, m_stale["posture"], {}, [], {}, [])
    expect("Oldest evidence" in md_stale and "exceeds freshness window" in md_stale,
           "render: stale → banner bullet present")

    # Fresh case
    m_fresh = dict(base_manifest)
    m_fresh["evidence_freshness"] = {
        "oldest_captured_at": ts(0.1),
        "newest_captured_at": ts(0.1),
        "span_hours": 0,
        "stale": False,
        "threshold_hours": 24,
    }
    md_fresh = pr._render_report(m_fresh, m_fresh["posture"], {}, [], {}, [])
    expect("Oldest evidence" not in md_fresh,
           "render: fresh → no banner bullet")

    # No-evidence case (static mode shape)
    m_static = dict(base_manifest)
    m_static["evidence_freshness"] = {
        "oldest_captured_at": None, "newest_captured_at": None,
        "span_hours": None, "stale": False, "threshold_hours": 24,
    }
    md_static = pr._render_report(m_static, m_static["posture"], {}, [], {}, [])
    expect("Oldest evidence" not in md_static,
           "render: static-mode → no banner bullet")


def t_renderer_banner_wording() -> None:
    """Exec-summary banner wording is regex-pinned.

    Rationale (from creator-session sign-off): the exec summary is the
    single highest-leverage surface a CISO reviewer reads. Silent wording
    drift (someone changes the bullet text in a refactor and the manifest
    still says `stale: true`) costs nothing technically but erodes the
    human signal. This is a one-line guard against that.

    Pinned shape:
        - **Oldest evidence:** YYYY-MM-DDTHH:MMZ (Nh before report) —
          exceeds freshness window (Th). Some evidence may be stale.
    """
    print("\nt_renderer_banner_wording")
    base_manifest = {
        "schema_version": "1.0",
        "tool": "threadlight-production-ready",
        "tool_version": pr.VERSION,
        "checked_at": ts(0),
        "mode": "full",
        "agt_profile": "v3_7",
        "posture": {"declared": None, "detected": None,
                    "resolved": "standard-ai-gateway", "resolution_path": []},
        "score": {"raw_percent": 80, "with_waivers_percent": 80},
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "permission_tiers": {"0": True},
        "warnings": [],
        "safe_check_reference": {"path": "x", "checked_at": ts(0),
                                  "subscription_id": "s", "resource_group": "r"},
        "pillars": [],
        "evidence_register": [],
        "waivers": [],
        "not_verified_count": 0,
        "evidence_freshness": {
            "oldest_captured_at": ts(30),
            "newest_captured_at": ts(30),
            "span_hours": 0,
            "stale": True,
            "threshold_hours": 24,
        },
    }
    md = pr._render_report(base_manifest, base_manifest["posture"], {}, [], {}, [])

    # The em-dash (—, U+2014) is intentional and must match what the
    # renderer emits. If a refactor swaps it for a hyphen, this test fires.
    # Timestamp format is full ISO 8601 UTC with seconds (ADR D5: "round-
    # trip-ability beats 5 chars of table density"); regex requires seconds
    # so a silent truncation to minute precision is also caught.
    pattern = re.compile(
        r"\*\*Oldest evidence:\*\* "
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        r" \(\d+h before report\)"
        r" — exceeds freshness window \(\d+h\)\."
        r" Some evidence may be stale\."
    )
    matches = pattern.findall(md)
    expect(len(matches) == 1,
           f"wording: pinned banner regex matched exactly once "
           f"(got {len(matches)}); rendered=...{md[-400:]!r}")

    # Pin the threshold from the manifest, not just any number — guards
    # against a refactor that hardcodes 24 instead of reading threshold_hours.
    expect("freshness window (24h)" in md,
           "wording: threshold hours come from manifest, not hardcoded")


def t_collected_column_present() -> None:
    """Evidence register table renders a 'Collected' column when evidence is non-empty."""
    print("\nt_collected_column_present")
    checked_at = ts(0)
    ev = [mk_evidence(ts(0.5), ref="E-X")]
    manifest = {
        "schema_version": "1.0",
        "tool_version": pr.VERSION,
        "checked_at": checked_at,
        "mode": "full",
        "agt_profile": "v3_7",
        "posture": {"declared": None, "detected": None, "resolved": "standard-ai-gateway",
                    "resolution_path": []},
        "score": {"raw_percent": 0, "with_waivers_percent": 0},
        "verification_coverage": {"verified": 0, "total_scoreable": 0, "percent": 0},
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "permission_tiers": {"1": True},
        "warnings": [],
        "safe_check_reference": {"path": "x", "checked_at": checked_at, "subscription_id": "s",
                                  "resource_group": "r"},
        "pillars": [],
        "evidence_register": [pr.asdict(e) for e in ev],
        "evidence_freshness": {
            "oldest_captured_at": ev[0].captured_at, "newest_captured_at": ev[0].captured_at,
            "span_hours": 0, "stale": False, "threshold_hours": 24,
        },
        "waivers": [],
        "not_verified_count": 0,
    }
    md = pr._render_report(manifest, manifest["posture"], {}, ev, {}, [])
    expect("Collected" in md, "render: evidence table has 'Collected' header")
    expect(ev[0].captured_at in md, "render: evidence row's captured_at value appears")


# ---------------------------------------------------------------------------
# CLI-level test: static-mode end-to-end shape
# ---------------------------------------------------------------------------


def t_cli_static_mode_shape() -> None:
    """Run the CLI in --static mode against the sample-pilot fixture; assert
    the manifest carries evidence_freshness with the no-evidence shape."""
    print("\nt_cli_static_mode_shape")
    if not SAMPLE_PILOT.exists():
        expect(False, "cli-static: sample-pilot fixture missing")
        return
    with tempfile.TemporaryDirectory() as td:
        out_json = Path(td) / "manifest.json"
        out_md = Path(td) / "report.md"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--root", str(SAMPLE_PILOT),
             "--in-manifest", str(SAMPLE_PILOT / "specs" / "manifest.json"),
             "--in-postdeploy", str(SAMPLE_PILOT / "tests" / "postdeploy-manifest.json"),
             "--static",
             "--accept-stale-safe-check",
             "--out", str(out_json),
             "--report", str(out_md),
             "--quiet"],
            check=False, capture_output=True, text=True,
        )
        expect(proc.returncode == 0,
               f"cli-static: exits 0 (got {proc.returncode}; stderr: {proc.stderr[:200]})")
        if not out_json.exists():
            expect(False, "cli-static: manifest written")
            return
        m = json.loads(out_json.read_text())
        ef = m.get("evidence_freshness")
        expect(isinstance(ef, dict), "cli-static: evidence_freshness block present")
        if isinstance(ef, dict):
            expect(ef.get("oldest_captured_at") is None, "cli-static: oldest null")
            expect(ef.get("newest_captured_at") is None, "cli-static: newest null")
            expect(ef.get("stale") is False, "cli-static: not stale")
            expect(ef.get("threshold_hours") == 24, "cli-static: threshold default 24")


# ---------------------------------------------------------------------------
# Freshness-hours-coupling test (pins the documented dual semantic from D6)
# ---------------------------------------------------------------------------


def t_freshness_hours_coupling() -> None:
    """Single --freshness-hours flag drives both safe-check pre-flight AND
    evidence staleness. Pin this so a future split is a visible diff."""
    print("\nt_freshness_hours_coupling")
    # Synthesize a sample-pilot copy with a stale safe-check manifest (40h old).
    if not SAMPLE_PILOT.exists():
        expect(False, "coupling: sample-pilot fixture missing")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "pilot"
        # Recursive copy of the fixture
        import shutil
        shutil.copytree(SAMPLE_PILOT, root)

        # Backdate the post-deploy manifest 40h
        pd_path = root / "tests" / "postdeploy-manifest.json"
        pd = json.loads(pd_path.read_text())
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pd["checked_at"] = old_ts
        pd_path.write_text(json.dumps(pd, indent=2))

        out_json = Path(td) / "manifest.json"
        out_md = Path(td) / "report.md"
        # 72h tolerance → safe-check pre-flight accepts 40h-stale manifest;
        # also widens evidence-staleness window. Static mode → empty evidence,
        # so evidence_freshness.stale must be False (no rows to evaluate).
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--root", str(root),
             "--in-manifest", str(root / "specs" / "manifest.json"),
             "--in-postdeploy", str(pd_path),
             "--static",
             "--freshness-hours", "72",
             "--out", str(out_json),
             "--report", str(out_md),
             "--quiet"],
            check=False, capture_output=True, text=True,
        )
        expect(proc.returncode == 0,
               f"coupling: --freshness-hours 72 accepts 40h-stale safe-check "
               f"(rc={proc.returncode}; stderr: {proc.stderr[:200]})")
        if out_json.exists():
            m = json.loads(out_json.read_text())
            ef = m.get("evidence_freshness", {})
            expect(ef.get("threshold_hours") == 72,
                   "coupling: same flag echoed into evidence_freshness")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        t_static_mode,
        t_fresh_evidence,
        t_stale_evidence,
        t_exact_boundary,
        t_unparseable_only,
        t_mixed_parseable,
        t_clock_skew,
        t_custom_flag_in_helper,
        t_renderer_manifest_agree,
        t_renderer_banner_wording,
        t_collected_column_present,
        t_cli_static_mode_shape,
        t_freshness_hours_coupling,
    ]
    print(f"Running {len(tests)} per-evidence freshness tests")
    print(f"  SCRIPT       = {SCRIPT}")
    print(f"  SAMPLE_PILOT = {SAMPLE_PILOT}")
    for t in tests:
        try:
            t()
        except Exception as exc:  # pragma: no cover — defensive
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
