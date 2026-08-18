#!/usr/bin/env python3
"""Tests for the threadlight leg-manifest integration (govern / evals / red-team).

These pin the behaviour: when a pilot has *run* the Discover/Protect/Govern legs
and committed their manifests under ``specs/``, the production-ready scorecard
flips the corresponding pillar findings from "go remediate X" to "verify the leg
ran + artefact fresh", and emits the SAFE-1xx red-team findings.

Backward-compat contract: with NO leg manifests present, the red-team leg emits
nothing (so existing fixtures keep their finding sets) and the AGT/evals/RAI
checks fall back to their legacy heuristics.

pytest-style (bare ``test_`` functions + ``assert``); no extra deps.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _caps(keys_to_status: dict[str, str]) -> dict:
    return {k: {"check_id": k, "status": v, "evidence": "fixture", "hint": None}
            for k, v in keys_to_status.items()}


def _make_ctx(*, manifests: dict[str, dict] | None = None) -> "pr.RepoContext":
    """Minimal RepoContext backed by a temp dir, with optional leg manifests
    written under ``specs/<name>``."""
    tmpdir = Path(tempfile.mkdtemp())
    specs = tmpdir / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    for name, data in (manifests or {}).items():
        (specs / name).write_text(json.dumps(data), encoding="utf-8")
    bg = pr.BicepGraph(resources=[], source_files=[])
    return pr.RepoContext(
        root=tmpdir,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text="",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest={},
        bicep_text="",
        src_text="",
        bicep_graph=bg,
    )


def _fresh_govern_manifest() -> dict:
    return {
        "schema": "threadlight-govern-manifest/v2",
        "captured_at": _iso(datetime.now(timezone.utc)),
        "verdict": "governed",
        "capabilities": _caps({
            "policy_artefact_present": "pass",
            "policy_schema_valid": "pass",
            "policy_versioned": "pass",
            "policy_default_deny": "pass",
            "sensitive_action_rules_present": "pass",
            "policy_tests_present": "pass",
            "ci_gate_present": "pass",
            "attestation_present": "pass",
            "attestation_fresh": "pass",
            "asi_reference_present": "pass",
        }),
    }


def _fresh_evals_manifest() -> dict:
    return {
        "schema": "threadlight-evals-manifest/v1",
        "captured_at": _iso(datetime.now(timezone.utc)),
        "verdict": "comprehensive",
        "capabilities": _caps({
            "eval_scenarios_present": "pass",
            "eval_datasets_present": "pass",
            "schedule_present": "pass",
            "thresholds_declared": "pass",
            "online_eval_wired": "pass",
            "ab_comparison_present": "pass",
        }),
    }


def _fresh_redteam_manifest() -> dict:
    return {
        "schema": "threadlight-redteam-manifest/v1",
        "captured_at": _iso(datetime.now(timezone.utc)),
        "verdict": "hardened",
        "capabilities": _caps({
            "scan_present": "pass",
            "jailbreak_asr_ok": "pass",
            "prompt_injection_asr_ok": "pass",
            "exfiltration_asr_ok": "pass",
            "harmful_content_asr_ok": "pass",
            "coverage_ok": "pass",
        }),
    }


def _by_id(findings) -> dict[str, "pr.Finding"]:
    return {f.id: f for f in findings}


# ---------------------------------------------------------------------------
# AGT (pillar 2): govern manifest flips AGT-001..005 to manifest-sourced pass
# ---------------------------------------------------------------------------

def test_agt_manifest_flips_to_pass() -> None:
    ctx = _make_ctx(manifests={"govern-manifest.json": _fresh_govern_manifest()})
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    for fid in ("AGT-001", "AGT-002", "AGT-003", "AGT-004", "AGT-005"):
        assert fid in f, f"{fid} missing"
        assert f[fid].status == "pass", f"{fid} expected pass, got {f[fid].status}: {f[fid].detail}"
    assert "threadlight-govern manifest" in f["AGT-001"].detail
    assert "AGT-006" in f, "AGT-006 still emitted (legacy telemetry heuristic)"


def test_agt_no_manifest_uses_legacy() -> None:
    ctx = _make_ctx()  # no manifests
    f = _by_id(pr._check_agt_static(ctx, "auto"))
    # Legacy heuristic: bare repo has no AGT import -> AGT-001 must-fix.
    assert f["AGT-001"].status == "must-fix", f["AGT-001"].detail
    assert "manifest" not in f["AGT-001"].detail.lower(), \
        "AGT-001 detail must not cite a manifest on the legacy path"


# ---------------------------------------------------------------------------
# Evals (pillar 6): evals manifest flips EVAL-001..004 + online/AB note
# ---------------------------------------------------------------------------

def test_evals_manifest_flips_and_notes_online_ab() -> None:
    ctx = _make_ctx(manifests={"evals-manifest.json": _fresh_evals_manifest()})
    f = _by_id(pr._check_evals_static(ctx))
    for fid in ("EVAL-001", "EVAL-002", "EVAL-003", "EVAL-004"):
        assert fid in f, f"{fid} missing"
        assert f[fid].status == "pass", f"{fid} expected pass, got {f[fid].status}: {f[fid].detail}"
    detail = f["EVAL-003"].detail.lower()
    assert "online" in detail and "a/b" in detail, \
        f"EVAL-003 should note online + A/B evidence: {f['EVAL-003'].detail}"


# ---------------------------------------------------------------------------
# RAI (pillar 7): govern manifest flips RAI-002 (sensitive-action rules);
# RAI-003 (prompt shields) is a model-edge control, NOT sourced from the govern
# manifest, so it stays on its own evidence heuristic. Red-team emits SAFE-1xx.
# ---------------------------------------------------------------------------

def test_rai_manifest_flips_and_emits_safe() -> None:
    ctx = _make_ctx(manifests={
        "govern-manifest.json": _fresh_govern_manifest(),
        "redteam-manifest.json": _fresh_redteam_manifest(),
    })
    f = _by_id(pr._check_rai_static(ctx))
    assert f["RAI-002"].status == "pass", f["RAI-002"].detail
    assert "sensitive_action_rules_present" in f["RAI-002"].detail
    # RAI-003 is decoupled from the govern manifest (prompt shields = model-edge
    # Content Safety). The synthetic ctx has no shield evidence -> must-fix.
    assert f["RAI-003"].status == "must-fix", f["RAI-003"].detail
    assert "manifest" not in f["RAI-003"].detail.lower(), \
        "RAI-003 must not cite the govern manifest (model-edge control)"
    for fid in ("SAFE-101", "SAFE-102", "SAFE-103", "SAFE-104", "SAFE-105", "SAFE-106"):
        assert fid in f, f"{fid} not emitted"
        assert f[fid].status == "pass", f"{fid} expected pass, got {f[fid].status}: {f[fid].detail}"


# ---------------------------------------------------------------------------
# Red-team leg: backward-compat + freshness behaviour
# ---------------------------------------------------------------------------

def test_redteam_absent_emits_nothing() -> None:
    ctx = _make_ctx()  # no manifests
    out = pr._check_redteam_static(ctx)
    assert out == [], f"no redteam manifest must emit zero SAFE findings, got {[x.id for x in out]}"


def test_redteam_stale_downgrades_must_fix() -> None:
    stale = _fresh_redteam_manifest()
    stale["captured_at"] = _iso(datetime.now(timezone.utc) - timedelta(days=200))
    # A stale scan with a FAILING jailbreak ASR must not score as pass.
    stale["capabilities"]["jailbreak_asr_ok"]["status"] = "must-fix"
    ctx = _make_ctx(manifests={"redteam-manifest.json": stale})
    f = _by_id(pr._check_redteam_static(ctx))
    assert f["SAFE-101"].status == "not-verified", \
        f"stale + failing SAFE-101 should downgrade to not-verified: {f['SAFE-101'].detail}"
    assert f["SAFE-104"].status == "should-fix", \
        f"stale scan should make SAFE-104 should-fix: {f['SAFE-104'].detail}"


def test_safe_catalog_severities() -> None:
    cat = pr.FINDING_CATALOG
    for fid in ("SAFE-101", "SAFE-102", "SAFE-103"):
        assert cat.get(fid, {}).get("severity") == "must-fix", f"{fid} should be must-fix"
    for fid in ("SAFE-104", "SAFE-105", "SAFE-106"):
        assert cat.get(fid, {}).get("severity") == "should-fix", f"{fid} should be should-fix"


# ---------------------------------------------------------------------------
# Live-leg gap evidence (Task 7): shared-envelope legs -> INT/GRD/LOAD/UPG.
# _check_gap_leg_manifests never lets an incomplete leg inflate readiness.
# ---------------------------------------------------------------------------

def gap_manifest(name: str, **body) -> dict:
    """Build a shared-envelope leg manifest (schema/tool_version/generated_at/
    freshness/status/findings) with sensible defaults."""
    generated_at = body.pop("generated_at", _iso(datetime.now(timezone.utc)))
    valid_for_hours = body.pop("valid_for_hours", 24)
    return {
        "schema": f"threadlight.{name}/v1",
        "tool_version": "0.1.0",
        "generated_at": generated_at,
        "freshness": {"valid_for_hours": valid_for_hours, "source_oldest_at": None},
        **body,
    }


@pytest.mark.parametrize("name,finding_id", [
    ("connect", "INT-001"),
    ("ground", "GRD-001"),
    ("load", "LOAD-001"),
    ("upgrade", "UPG-001"),
])
def test_missing_new_leg_manifest_is_not_verified(name: str, finding_id: str) -> None:
    findings = pr._check_gap_leg_manifests(_make_ctx())
    assert _by_id(findings)[finding_id].status == "not-verified"


def test_gap_check_returns_all_14_exactly_once() -> None:
    findings = pr._check_gap_leg_manifests(_make_ctx())
    ids = [f.id for f in findings]
    expected = [
        "INT-001", "INT-002", "INT-003", "INT-004",
        "GRD-001", "GRD-002", "GRD-003", "GRD-004",
        "LOAD-001", "LOAD-002", "LOAD-003",
        "UPG-001", "UPG-002", "UPG-003",
    ]
    assert ids == expected, ids
    assert len(ids) == len(set(ids)) == 14


def test_executed_acl_failure_is_must_fix() -> None:
    ctx = _make_ctx(manifests={
        "ground-manifest.json": gap_manifest(
            "ground",
            status="complete",
            findings=[{"id": "GRD-001", "status": "must-fix"}],
        ),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["GRD-001"].status == "must-fix"


def test_aborted_load_manifest_never_counts_as_pass() -> None:
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load",
            status="aborted",
            findings=[{"id": "LOAD-001", "status": "must-fix"}],
        ),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["LOAD-001"].status == "must-fix"


def test_aborted_dominates_even_for_findings_absent_from_envelope() -> None:
    # LOAD-002/LOAD-003 are not in the aborted manifest — they must still be
    # must-fix (never pass) because the run aborted.
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest("load", status="aborted", findings=[]),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("LOAD-001", "LOAD-002", "LOAD-003"):
        assert f[fid].status == "must-fix", f"{fid}: {f[fid].detail}"


def test_partial_or_stale_pass_evidence_is_not_verified() -> None:
    stale = _iso(datetime.now(timezone.utc) - timedelta(days=2))
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect",
            status="partial",
            findings=[{"id": "INT-001", "status": "pass"}],
        ),
        "upgrade-manifest.json": gap_manifest(
            "upgrade",
            generated_at=stale,
            status="complete",
            findings=[{"id": "UPG-001", "status": "pass"}],
        ),
    })
    findings = _by_id(pr._check_gap_leg_manifests(ctx))
    assert findings["INT-001"].status == "not-verified"
    assert findings["UPG-001"].status == "not-verified"


def test_must_fix_dominates_partial_and_stale_envelopes() -> None:
    # Negative evidence dominates even when the envelope itself is not fresh /
    # complete: a partial or stale run that recorded a must-fix stays must-fix.
    stale = _iso(datetime.now(timezone.utc) - timedelta(days=5))
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="partial",
            findings=[{"id": "INT-002", "status": "must-fix"}]),
        "upgrade-manifest.json": gap_manifest(
            "upgrade", generated_at=stale, status="complete",
            findings=[{"id": "UPG-002", "status": "must-fix"}]),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["INT-002"].status == "must-fix"
    assert f["UPG-002"].status == "must-fix"


def test_complete_fresh_envelope_propagates_pass_and_should_fix() -> None:
    ctx = _make_ctx(manifests={
        "ground-manifest.json": gap_manifest(
            "ground",
            status="complete",
            findings=[
                {"id": "GRD-001", "status": "pass"},
                {"id": "GRD-002", "status": "pass"},
                {"id": "GRD-003", "status": "should-fix"},
                {"id": "GRD-004", "status": "not-applicable"},
            ],
        ),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["GRD-001"].status == "pass"
    assert f["GRD-002"].status == "pass"
    assert f["GRD-003"].status == "should-fix"
    assert f["GRD-004"].status == "not-applicable"


def test_complete_fresh_not_verified_source_stays_not_verified() -> None:
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete",
            findings=[{"id": "INT-001", "status": "not-verified"}]),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].status == "not-verified"


def test_missing_finding_in_complete_envelope_is_not_verified() -> None:
    # A fresh, complete connect envelope that simply omits INT-003 cannot prove
    # INT-003 — it stays not-verified rather than defaulting to pass.
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete",
            findings=[{"id": "INT-001", "status": "pass"}]),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-003"].status == "not-verified"


def test_duplicate_source_finding_cannot_prove_readiness() -> None:
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load", status="complete",
            findings=[
                {"id": "LOAD-001", "status": "pass"},
                {"id": "LOAD-001", "status": "pass"},
            ]),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["LOAD-001"].status == "not-verified"


def test_duplicate_with_a_must_fix_still_dominates() -> None:
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load", status="complete",
            findings=[
                {"id": "LOAD-001", "status": "pass"},
                {"id": "LOAD-001", "status": "must-fix"},
            ]),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["LOAD-001"].status == "must-fix"


def test_unknown_or_malformed_status_cannot_prove_readiness() -> None:
    ctx = _make_ctx(manifests={
        "upgrade-manifest.json": gap_manifest(
            "upgrade", status="complete",
            findings=[{"id": "UPG-001", "status": "green"}]),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["UPG-001"].status == "not-verified"


def test_unparseable_manifest_is_not_verified() -> None:
    import tempfile
    from pathlib import Path as _Path
    ctx = _make_ctx()
    (_Path(ctx.root) / "specs" / "connect-manifest.json").write_text("{ not json", encoding="utf-8")
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].status == "not-verified"


def test_gap_findings_registered_in_expected_pillars() -> None:
    cat = pr.FINDING_CATALOG
    expected_pillar = {
        "INT-001": "supply-chain", "INT-002": "supply-chain",
        "INT-003": "reliability", "INT-004": "reliability",
        "GRD-001": "identity-access",
        "GRD-002": "responsible-ai", "GRD-003": "responsible-ai", "GRD-004": "responsible-ai",
        "LOAD-001": "reliability", "LOAD-002": "cost", "LOAD-003": "reliability",
        "UPG-001": "model-lifecycle", "UPG-002": "model-lifecycle", "UPG-003": "supply-chain",
    }
    for fid, pillar in expected_pillar.items():
        assert fid in cat, f"{fid} missing from FINDING_CATALOG"
        assert cat[fid]["pillar"] == pillar, f"{fid} pillar {cat[fid]['pillar']} != {pillar}"
        assert cat[fid]["tier"] == 0, f"{fid} must be tier-0 (static synthesis)"
        assert not cat[fid].get("experimental"), f"{fid} must not be experimental"


def test_gap_findings_dispatched_through_pillars() -> None:
    # The gap findings must reach the pillar dispatch so they appear in scoring,
    # not just via the standalone helper. A fresh complete ground manifest with a
    # GRD-001 must-fix should surface in the identity-access pillar's static set.
    ctx = _make_ctx(manifests={
        "ground-manifest.json": gap_manifest(
            "ground", status="complete",
            findings=[{"id": "GRD-001", "status": "must-fix"}]),
    })
    findings, _ = pr._run_pillar(
        "identity-access", ctx, static_only=True, tiers={}, sub=None, rg=None,
        resolved_posture="none", agt_profile="none", quick=False,
    )
    by_id = _by_id(findings)
    assert "GRD-001" in by_id, "GRD-001 not dispatched into identity-access pillar"
    assert by_id["GRD-001"].status == "must-fix"
    # And the target-id appears exactly once (no double-emit from fail-closed).
    assert [f.id for f in findings].count("GRD-001") == 1


# ---------------------------------------------------------------------------
# Legacy freshness window (captured_at + 90 days) stays unchanged.
# ---------------------------------------------------------------------------

def test_legacy_captured_at_freshness_unchanged() -> None:
    fresh = _fresh_govern_manifest()
    ctx = _make_ctx(manifests={"govern-manifest.json": fresh})
    loaded = pr._load_leg_manifest(ctx, "govern-manifest.json")
    assert loaded is not None and loaded["_fresh"] is True

    stale = _fresh_govern_manifest()
    stale["captured_at"] = _iso(datetime.now(timezone.utc) - timedelta(days=120))
    ctx2 = _make_ctx(manifests={"govern-manifest.json": stale})
    loaded2 = pr._load_leg_manifest(ctx2, "govern-manifest.json")
    assert loaded2 is not None and loaded2["_fresh"] is False


def test_shared_envelope_freshness_uses_valid_for_hours() -> None:
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", valid_for_hours=1,
            generated_at=_iso(datetime.now(timezone.utc) - timedelta(hours=3)),
            findings=[{"id": "INT-001", "status": "pass"}]),
    })
    loaded = pr._load_leg_manifest(ctx, "connect-manifest.json")
    assert loaded is not None and loaded["_fresh"] is False, "3h old with 1h validity is stale"
