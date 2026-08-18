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

# The REAL producer: import threadlight-connect so a genuine emitted
# connect-manifest.json (not a hand-built one) can be fed to the consumer.
CONNECT_SCRIPTS = SKILL_DIR.parent / "threadlight-connect" / "scripts"
sys.path.insert(0, str(CONNECT_SCRIPTS))
import connect  # noqa: E402

# The other three live-leg producers, imported the same way, so their ACTUAL
# emitted manifests (ground/load/upgrade) can be fed end-to-end to the consumer.
REPO_ROOT = SKILL_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
for _leg in ("threadlight-ground", "threadlight-loadtest", "threadlight-upgrade"):
    sys.path.insert(0, str(SKILL_DIR.parent / _leg / "scripts"))
import ground  # noqa: E402
import loadtest  # noqa: E402
import upgrade  # noqa: E402


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

# The exact per-file identity + finding-id contract the consumer now enforces.
_LEG_SCHEMAS = {
    "connect": "threadlight-connect-manifest/v1",
    "ground": "threadlight.ground/v1",
    "load": "threadlight.load/v1",
    "upgrade": "threadlight.upgrade/v1",
}
_LEG_IDS = {
    "connect": ["INT-001", "INT-002", "INT-003", "INT-004"],
    "ground": ["GRD-001", "GRD-002", "GRD-003", "GRD-004"],
    "load": ["LOAD-001", "LOAD-002", "LOAD-003"],
    "upgrade": ["UPG-001", "UPG-002", "UPG-003"],
}


def gap_manifest(name: str, *, status: str = "complete", findings=None,
                 overrides: dict | None = None, generated_at: str | None = None,
                 valid_for_hours=24, source_oldest_at=None,
                 tool_version: str = "0.1.0", schema: str | None = None) -> dict:
    """Build a shared-envelope leg manifest.

    By default emits a STRUCTURALLY VALID manifest: the correct per-file schema,
    a non-empty tool_version, a strict-RFC3339 generated_at, a freshness object,
    and the FULL required finding-id tuple all ``pass`` (apply per-id
    ``overrides`` to flip specific findings). Pass ``findings=[...]`` to author a
    raw (possibly invalid-shape) findings list for the negative-path tests, or
    ``schema=``/``tool_version=`` to author an invalid envelope."""
    gen = generated_at if generated_at is not None else _iso(datetime.now(timezone.utc))
    if findings is None:
        ov = overrides or {}
        findings = [{"id": fid, "status": ov.get(fid, "pass")} for fid in _LEG_IDS[name]]
    return {
        "schema": schema if schema is not None else _LEG_SCHEMAS[name],
        "tool_version": tool_version,
        "generated_at": gen,
        "freshness": {"valid_for_hours": valid_for_hours, "source_oldest_at": source_oldest_at},
        "status": status,
        "findings": findings,
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
            overrides={"GRD-001": "must-fix"},
        ),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["GRD-001"].status == "must-fix"


def test_aborted_load_manifest_never_counts_as_pass() -> None:
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load",
            status="aborted",
            overrides={"LOAD-001": "must-fix"},
        ),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["LOAD-001"].status == "must-fix"


def test_aborted_valid_envelope_flips_even_pass_findings_to_must_fix() -> None:
    # A structurally valid aborted envelope that (dishonestly) claims every
    # finding passed must still flip all of them to must-fix — an aborted run
    # can never certify readiness.
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest("load", status="aborted"),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("LOAD-001", "LOAD-002", "LOAD-003"):
        assert f[fid].status == "must-fix", f"{fid}: {f[fid].detail}"


def test_partial_or_stale_pass_evidence_is_not_verified() -> None:
    stale = _iso(datetime.now(timezone.utc) - timedelta(days=2))
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest("connect", status="partial"),
        "upgrade-manifest.json": gap_manifest(
            "upgrade", generated_at=stale, status="complete"),
    })
    findings = _by_id(pr._check_gap_leg_manifests(ctx))
    assert findings["INT-001"].status == "not-verified"
    assert findings["UPG-001"].status == "not-verified"


def test_must_fix_dominates_partial_and_stale_envelopes() -> None:
    # Negative evidence dominates even when the (still structurally valid)
    # envelope itself is not fresh / complete: a partial or stale run that
    # recorded a must-fix stays must-fix.
    stale = _iso(datetime.now(timezone.utc) - timedelta(days=5))
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="partial", overrides={"INT-002": "must-fix"}),
        "upgrade-manifest.json": gap_manifest(
            "upgrade", generated_at=stale, status="complete",
            overrides={"UPG-002": "must-fix"}),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["INT-002"].status == "must-fix"
    assert f["UPG-002"].status == "must-fix"


def test_complete_fresh_envelope_propagates_producer_statuses() -> None:
    ctx = _make_ctx(manifests={
        "ground-manifest.json": gap_manifest(
            "ground",
            status="complete",
            findings=[
                {"id": "GRD-001", "status": "pass"},
                {"id": "GRD-002", "status": "pass"},
                {"id": "GRD-003", "status": "should-fix"},
                {"id": "GRD-004", "status": "pass"},
            ],
        ),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["GRD-001"].status == "pass"
    assert f["GRD-002"].status == "pass"
    assert f["GRD-003"].status == "should-fix"
    assert f["GRD-004"].status == "pass"


def test_complete_fresh_not_verified_source_stays_not_verified() -> None:
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", overrides={"INT-001": "not-verified"}),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].status == "not-verified"


def test_missing_finding_in_envelope_invalidates_whole_manifest() -> None:
    # A connect envelope that omits a required finding id (INT-003) violates the
    # exact one-each identity contract -> the whole manifest is untrusted and
    # every INT finding degrades to not-verified (never a default pass).
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete",
            findings=[
                {"id": "INT-001", "status": "pass"},
                {"id": "INT-002", "status": "pass"},
                {"id": "INT-004", "status": "pass"},
            ]),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("INT-001", "INT-002", "INT-003", "INT-004"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_duplicate_source_finding_cannot_prove_readiness() -> None:
    # A duplicated id (full tuple + one repeat) breaks the one-each contract.
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load", status="complete",
            findings=[
                {"id": "LOAD-001", "status": "pass"},
                {"id": "LOAD-002", "status": "pass"},
                {"id": "LOAD-003", "status": "pass"},
                {"id": "LOAD-001", "status": "pass"},
            ]),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["LOAD-001"].status == "not-verified"


def test_duplicate_must_fix_is_untrusted_and_not_propagated() -> None:
    # A duplicated id is structurally invalid, so even a handcrafted must-fix on
    # the duplicate is UNTRUSTED: it degrades to not-verified and must never trip
    # the hard gate.
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load", status="complete",
            findings=[
                {"id": "LOAD-001", "status": "pass"},
                {"id": "LOAD-002", "status": "pass"},
                {"id": "LOAD-003", "status": "pass"},
                {"id": "LOAD-001", "status": "must-fix"},
            ]),
    })
    gap = pr._check_gap_leg_manifests(ctx)
    assert _by_id(gap)["LOAD-001"].status == "not-verified"
    assert not pr._hard_gate_would_fail(gap), "untrusted must-fix tripped the hard gate"


def test_unknown_id_invalidates_manifest() -> None:
    # An unexpected finding id (not in the required tuple) is untrusted.
    ctx = _make_ctx(manifests={
        "upgrade-manifest.json": gap_manifest(
            "upgrade", status="complete",
            findings=[
                {"id": "UPG-001", "status": "pass"},
                {"id": "UPG-002", "status": "pass"},
                {"id": "UPG-003", "status": "pass"},
                {"id": "UPG-999", "status": "pass"},
            ]),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("UPG-001", "UPG-002", "UPG-003"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_unknown_or_malformed_status_cannot_prove_readiness() -> None:
    ctx = _make_ctx(manifests={
        "upgrade-manifest.json": gap_manifest(
            "upgrade", status="complete", overrides={"UPG-001": "green"}),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["UPG-001"].status == "not-verified"


# ---------------------------------------------------------------------------
# Strict common-envelope + identity contract: every structural defect makes
# the whole manifest untrusted, so all its findings degrade to not-verified.
# ---------------------------------------------------------------------------

def test_missing_schema_invalidates_manifest() -> None:
    m = gap_manifest("connect", status="complete")
    del m["schema"]
    ctx = _make_ctx(manifests={"connect-manifest.json": m})
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("INT-001", "INT-002", "INT-003", "INT-004"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_wrong_schema_invalidates_manifest() -> None:
    # connect file carrying the WRONG schema string (the exact bug class the
    # consumer must now catch: a plausible-but-wrong 'threadlight.connect/v1').
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", schema="threadlight.connect/v1"),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("INT-001", "INT-002", "INT-003", "INT-004"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_missing_tool_version_invalidates_manifest() -> None:
    m = gap_manifest("ground", status="complete")
    del m["tool_version"]
    ctx = _make_ctx(manifests={"ground-manifest.json": m})
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("GRD-001", "GRD-002", "GRD-003", "GRD-004"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_empty_tool_version_invalidates_manifest() -> None:
    ctx = _make_ctx(manifests={
        "ground-manifest.json": gap_manifest("ground", status="complete", tool_version=""),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["GRD-001"].status == "not-verified"


def test_invalid_generated_at_timestamp_invalidates_manifest() -> None:
    # A naive (timezone-less) timestamp is not strict RFC3339.
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load", status="complete", generated_at="2026-08-18T10:00:00"),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("LOAD-001", "LOAD-002", "LOAD-003"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_invalid_envelope_status_invalidates_manifest() -> None:
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest("load", status="done"),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("LOAD-001", "LOAD-002", "LOAD-003"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_missing_freshness_source_oldest_at_invalidates_manifest() -> None:
    m = gap_manifest("upgrade", status="complete")
    del m["freshness"]["source_oldest_at"]
    ctx = _make_ctx(manifests={"upgrade-manifest.json": m})
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("UPG-001", "UPG-002", "UPG-003"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_invalid_source_oldest_at_timestamp_invalidates_manifest() -> None:
    # source_oldest_at must be null or strict RFC3339 — a bare date is neither.
    ctx = _make_ctx(manifests={
        "upgrade-manifest.json": gap_manifest(
            "upgrade", status="complete", source_oldest_at="2026-06-01"),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["UPG-001"].status == "not-verified"


def test_valid_source_oldest_at_timestamp_is_accepted() -> None:
    # A proper RFC3339 source_oldest_at must NOT invalidate an otherwise-valid,
    # fresh, complete manifest.
    ctx = _make_ctx(manifests={
        "upgrade-manifest.json": gap_manifest(
            "upgrade", status="complete",
            source_oldest_at="2026-06-01T00:00:00+00:00"),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("UPG-001", "UPG-002", "UPG-003"):
        assert f[fid].status == "pass", f"{fid}: {f[fid].detail}"


def test_missing_findings_list_invalidates_manifest() -> None:
    m = gap_manifest("connect", status="complete")
    del m["findings"]
    ctx = _make_ctx(manifests={"connect-manifest.json": m})
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].status == "not-verified"


def test_integral_float_valid_for_hours_is_accepted() -> None:
    # A manifest that serialized valid_for_hours as 24.0 (integral float) is a
    # valid Draft7 integer and must not be rejected.
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", valid_for_hours=24.0),
    })
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("INT-001", "INT-002", "INT-003", "INT-004"):
        assert f[fid].status == "pass", f"{fid}: {f[fid].detail}"


def test_bool_valid_for_hours_is_rejected() -> None:
    # bool is a subclass of int but must NOT satisfy the integer freshness field.
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", valid_for_hours=True),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].status == "not-verified"


def test_nonpositive_valid_for_hours_is_rejected() -> None:
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", valid_for_hours=0),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].status == "not-verified"


@pytest.mark.parametrize(
    "valid_for_hours",
    [8761, 10**1000, float("nan"), float("inf"), [], {}],
)
def test_unsafe_valid_for_hours_is_rejected_without_exception(valid_for_hours) -> None:
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", valid_for_hours=valid_for_hours),
    })
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("INT-")
    ]
    assert all(f.status == "not-verified" for f in findings)
    assert pr._score_pillar(findings)[1] == 0
    assert sum(f.status != "not-verified" for f in findings) == 0
    assert not pr._hard_gate_would_fail(findings)


def test_maximum_valid_for_hours_is_accepted() -> None:
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", valid_for_hours=8760),
    })
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("INT-")
    ]
    assert all(f.status == "pass" for f in findings)


@pytest.mark.parametrize("freshness", [[], {}, 10**1000, float("nan")])
def test_malformed_freshness_is_not_verified_without_exception(freshness) -> None:
    manifest = gap_manifest("ground")
    manifest["freshness"] = freshness
    ctx = _make_ctx(manifests={"ground-manifest.json": manifest})
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("GRD-")
    ]
    assert all(f.status == "not-verified" for f in findings)
    assert pr._score_pillar(findings)[1] == 0
    assert not pr._hard_gate_would_fail(findings)


@pytest.mark.parametrize("status", [[], {}, 10**1000, float("nan")])
def test_malformed_envelope_status_is_not_verified_without_exception(status) -> None:
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest("load", status=status),
    })
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("LOAD-")
    ]
    assert all(f.status == "not-verified" for f in findings)
    assert pr._score_pillar(findings)[1] == 0
    assert not pr._hard_gate_would_fail(findings)


@pytest.mark.parametrize("status", [[], {}, 10**1000, float("nan"), "not-applicable"])
def test_malformed_or_nonproducer_finding_status_cannot_inflate_readiness(status) -> None:
    manifest = gap_manifest("upgrade", status="complete")
    manifest["findings"][0]["status"] = status
    ctx = _make_ctx(manifests={"upgrade-manifest.json": manifest})
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("UPG-")
    ]
    assert all(f.status == "not-verified" for f in findings)
    assert pr._score_pillar(findings)[1] == 0
    assert sum(f.status != "not-verified" for f in findings) == 0
    assert not pr._hard_gate_would_fail(findings)


def test_stale_new_envelope_cannot_be_freshened_by_captured_at() -> None:
    manifest = gap_manifest(
        "connect",
        generated_at=_iso(datetime.now(timezone.utc) - timedelta(days=2)),
        valid_for_hours=1,
    )
    manifest["captured_at"] = _iso(datetime.now(timezone.utc))
    ctx = _make_ctx(manifests={"connect-manifest.json": manifest})
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("INT-")
    ]
    assert all(f.status == "not-verified" for f in findings)
    assert all("failed manifest validation" in f.detail for f in findings)
    assert pr._score_pillar(findings)[1] == 0
    assert sum(f.status != "not-verified" for f in findings) == 0
    assert not pr._hard_gate_would_fail(findings)


@pytest.mark.parametrize("legacy_key", ["captured_at", "freshness_window_days"])
def test_new_envelope_rejects_legacy_freshness_top_level_fields(legacy_key) -> None:
    manifest = gap_manifest("ground")
    manifest[legacy_key] = (
        _iso(datetime.now(timezone.utc)) if legacy_key == "captured_at" else 90
    )
    ctx = _make_ctx(manifests={"ground-manifest.json": manifest})
    findings = [
        f for f in pr._check_gap_leg_manifests(ctx) if f.id.startswith("GRD-")
    ]
    assert all(f.status == "not-verified" for f in findings)


def test_recognized_new_schema_never_uses_legacy_captured_at_path() -> None:
    manifest = gap_manifest(
        "connect",
        generated_at=_iso(datetime.now(timezone.utc) - timedelta(days=2)),
        valid_for_hours=1,
    )
    manifest["captured_at"] = _iso(datetime.now(timezone.utc))
    ctx = _make_ctx(manifests={"govern-manifest.json": manifest})
    loaded = pr._load_leg_manifest(ctx, "govern-manifest.json")
    assert loaded is not None
    assert loaded.get("_fresh") is False


def test_validation_detail_never_echoes_raw_payload() -> None:
    # The invalidation detail must identify the evidence as invalid WITHOUT
    # echoing the raw (possibly oversharing) payload — only the filename +
    # a short structural reason.
    secret = "s3cr3t-not-for-logs"
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", schema="threadlight.connect/v1",
            tool_version=secret),
    })
    detail = _by_id(pr._check_gap_leg_manifests(ctx))["INT-001"].detail
    assert "connect-manifest.json" in detail
    assert "failed manifest validation" in detail
    assert secret not in detail


# ---------------------------------------------------------------------------
# Trust-boundary security invariants: a forged manifest can neither trip the
# hard go-live gate nor score. Contrasted against a genuine, valid manifest so
# the mechanism is proven, not merely absent.
# ---------------------------------------------------------------------------

def test_valid_must_fix_trips_hard_gate_but_invalid_one_cannot() -> None:
    # A genuine, structurally valid connect manifest with an INT-001 must-fix
    # DOES trip the hard gate...
    valid = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", overrides={"INT-001": "must-fix"}),
    })
    valid_gap = pr._check_gap_leg_manifests(valid)
    assert _by_id(valid_gap)["INT-001"].status == "must-fix"
    assert pr._hard_gate_would_fail(valid_gap), "a real must-fix must fail the gate"

    # ...but the SAME handcrafted must-fix on a structurally invalid (wrong
    # schema) manifest is untrusted: it degrades to not-verified and the hard
    # gate stays green.
    forged = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect", status="complete", schema="threadlight.connect/v1",
            overrides={"INT-001": "must-fix"}),
    })
    forged_gap = pr._check_gap_leg_manifests(forged)
    assert _by_id(forged_gap)["INT-001"].status == "not-verified"
    assert not pr._hard_gate_would_fail(forged_gap), \
        "a forged must-fix must NOT trip the hard gate"


def test_valid_pass_scores_but_invalid_pass_cannot() -> None:
    # A genuine, valid, fresh, complete connect manifest with all-pass INT
    # findings scores full marks for those findings...
    valid_gap = pr._check_gap_leg_manifests(_make_ctx(manifests={
        "connect-manifest.json": gap_manifest("connect", status="complete"),
    }))
    int_valid = [f for f in valid_gap if f.id.startswith("INT-")]
    assert all(f.status == "pass" for f in int_valid)
    _, pct_valid, _, _ = pr._score_pillar(int_valid)
    assert pct_valid == 100

    # ...but the same all-pass claims on a structurally invalid (missing
    # tool_version) manifest are untrusted: not-verified earns nothing.
    m = gap_manifest("connect", status="complete")
    del m["tool_version"]
    forged_gap = pr._check_gap_leg_manifests(_make_ctx(manifests={
        "connect-manifest.json": m,
    }))
    int_forged = [f for f in forged_gap if f.id.startswith("INT-")]
    assert all(f.status == "not-verified" for f in int_forged)
    _, pct_forged, _, _ = pr._score_pillar(int_forged)
    assert pct_forged == 0, "a forged pass must not score"


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
            "ground", status="complete", overrides={"GRD-001": "must-fix"}),
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
        ),
    })
    loaded = pr._load_leg_manifest(ctx, "connect-manifest.json")
    assert loaded is not None and loaded["_fresh"] is False, "3h old with 1h validity is stale"


# ---------------------------------------------------------------------------
# REAL producer -> consumer integration: emit an ACTUAL connect-manifest.json
# via threadlight-connect's own run_connect, then feed it to the consumer's
# _check_gap_leg_manifests. No INT ids are hand-authored here — they come from
# the producer — so this pins the end-to-end contract, not a mock of it.
# ---------------------------------------------------------------------------

_CONNECT_TOOL_SOURCE = "return {'id': row['id'], 'status': row.get('status')}"
_CONNECT_SAMPLE = {"id": "R-1", "status": "open"}
_CONNECT_OBO = {"present": True, "user_scoped": True}
_CONNECT_ROLE = {
    "revalidated": True,
    "required_roles": ["Case.Read"],
    "validated_roles": ["Case.Read"],
    "agent_identity": "agent-xyz",
}


def _emit_real_connect_manifest(**run_kwargs) -> "pr.RepoContext":
    """Run the real connect producer into a fresh ctx root, writing an actual
    specs/connect-manifest.json for the consumer to read."""
    ctx = _make_ctx()
    defaults = dict(
        project_root=ctx.root,
        tool_name="returns_get_case",
        tool_source=_CONNECT_TOOL_SOURCE,
        sample=_CONNECT_SAMPLE,
        obo_evidence=_CONNECT_OBO,
        role_evidence=_CONNECT_ROLE,
        current_agent_identity="agent-xyz",
        generated_at=_iso(datetime.now(timezone.utc)),  # fresh envelope
        apply=False,
    )
    defaults.update(run_kwargs)
    connect.run_connect(**defaults)
    return ctx


def test_real_connect_manifest_emits_exactly_the_int_tuple() -> None:
    ctx = _emit_real_connect_manifest(
        real_response={"items": [{"id": "R-1", "status": "open"}]})
    raw = json.loads(
        (ctx.root / "specs" / "connect-manifest.json").read_text(encoding="utf-8"))
    assert [f["id"] for f in raw["findings"]] == [
        "INT-001", "INT-002", "INT-003", "INT-004"]


def test_real_connect_success_propagates_all_int_pass() -> None:
    # A genuine APPLIED swap (apply=True + a validated real endpoint) runs the
    # real connect transaction, persisting integration_state real-verified into
    # the real production SPEC.md + infra/mcp-config.json + connect-manifest.json,
    # with servers[tool].url pointed at the real endpoint. Only then does INT-002
    # legitimately reach pass, and the consumer propagates all four.
    ctx = _emit_real_connect_manifest(
        real_response={"items": [{"id": "R-1", "status": "open"}]},
        apply=True,
        real_endpoint="https://api.example.com/mcp")
    # the apply transaction actually wrote the real production config + advanced
    # the persisted binding — this is what makes INT-002 pass truthful.
    mcp_path = ctx.root / "infra" / "mcp-config.json"
    assert mcp_path.exists()
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert mcp["servers"]["returns_get_case"]["url"] == "https://api.example.com/mcp"
    manifest = json.loads(
        (ctx.root / "specs" / "connect-manifest.json").read_text(encoding="utf-8"))
    assert manifest["integration_state"] == "real-verified"
    assert manifest["target_state"] == "real-verified"
    # privacy: the URL is persisted only in mcp-config.json, never the manifest.
    assert "api.example.com" not in json.dumps(manifest)
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("INT-001", "INT-002", "INT-003", "INT-004"):
        assert f[fid].status == "pass", f"{fid}: {f[fid].detail}"


def test_real_connect_dry_run_holds_int_002_not_verified_while_others_pass() -> None:
    # A dry run (apply=False) with FULL evidence proves conformance, OBO, and
    # role revalidation, but does NOT persist the binding: integration_state
    # stays mock even though target_state is real-verified. The producer emits
    # INT-002 not-verified (evidence supports the swap, but --apply has not
    # persisted the binding) while INT-001/003/004 reflect the completed
    # evidence as pass. The consumer must not let the un-applied binding claim
    # readiness — so production-ready and safe-check agree on the applied state.
    ctx = _emit_real_connect_manifest(
        real_response={"items": [{"id": "R-1", "status": "open"}]},
        apply=False)
    # a dry run writes NO production config — nothing is actually bound.
    assert not (ctx.root / "infra" / "mcp-config.json").exists()
    manifest = json.loads(
        (ctx.root / "specs" / "connect-manifest.json").read_text(encoding="utf-8"))
    assert manifest["integration_state"] == "mock"
    assert manifest["target_state"] == "real-verified"
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["INT-002"].status == "not-verified"   # binding not persisted
    assert f["INT-001"].status == "pass"           # conformance verified
    assert f["INT-003"].status == "pass"           # OBO user-scoped
    assert f["INT-004"].status == "pass"           # roles revalidated


def test_real_connect_drift_propagates_must_fix() -> None:
    # A wrong-typed field is a real conformance difference -> real-drift.
    ctx = _emit_real_connect_manifest(
        real_response={"items": [{"id": "R-1", "status": 42}]})
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["INT-001"].status == "must-fix"  # conformance diverged
    assert f["INT-002"].status == "must-fix"  # runtime must not bind to drift
    # OBO + role evidence still hold, and the run WAS evaluated (complete), so
    # those two propagate pass rather than being downgraded.
    assert f["INT-003"].status == "pass"
    assert f["INT-004"].status == "pass"


def test_real_connect_incomplete_holds_every_int_not_verified() -> None:
    # No real records to check -> unevaluated conformance -> partial envelope.
    ctx = _emit_real_connect_manifest(real_response={"items": []})
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    # A partial envelope can never inflate readiness: even the OBO/role passes
    # recorded in the manifest are downgraded to not-verified by the consumer.
    for fid in ("INT-001", "INT-002", "INT-003", "INT-004"):
        assert f[fid].status == "not-verified", f"{fid}: {f[fid].detail}"


def test_real_connect_missing_evidence_propagates_identity_not_verified() -> None:
    # Conformance passes, but OBO absent and no --current-agent-identity: the
    # producer emits INT-003/INT-004 not-verified, which propagate 1:1.
    ctx = _emit_real_connect_manifest(
        real_response={"items": [{"id": "R-1", "status": "open"}]},
        obo_evidence={"present": False, "user_scoped": False},
        current_agent_identity=None,
    )
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    assert f["INT-001"].status == "pass"           # conformance still passes
    assert f["INT-002"].status == "not-verified"   # binding held at unverified
    assert f["INT-003"].status == "not-verified"   # OBO absent
    assert f["INT-004"].status == "not-verified"   # roles stale (no current id)


# ---------------------------------------------------------------------------
# REAL producer -> consumer integration for the other three legs. Each emits
# an ACTUAL manifest via its own producer (ground/loadtest/upgrade), writes it
# to specs/<leg>-manifest.json, and asserts the consumer validates + propagates
# the genuine all-pass evidence. No finding ids or envelope fields are
# hand-authored — they come from the producers, so these pin the end-to-end
# contract, not a mock of it.
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _write_manifest(ctx: "pr.RepoContext", name: str, manifest: dict) -> None:
    (ctx.root / "specs" / name).write_text(json.dumps(manifest), encoding="utf-8")


def test_real_ground_manifest_propagates_all_grd_pass() -> None:
    now = _now_iso()
    src = {
        "id": "policy-library", "type": "documents", "permission_model": "acl",
        "refresh_cadence": "daily", "citation_required": True,
        "refuse_when_unsupported": True,
    }
    manifest = ground.assess_grounding(
        sources=[src],
        acl_runs=[
            {"principal": "entitled-analyst", "document_ids": ["doc-1"],
             "source_id": "policy-library", "captured_at": now,
             "expected_entitled": True},
            {"principal": "unentitled-guest", "document_ids": [],
             "source_id": "policy-library", "captured_at": now,
             "expected_entitled": False},
        ],
        citation_runs=[{"citations": ["doc-1"], "retrieved_ids": ["doc-1", "doc-2"],
                        "source_id": "policy-library", "captured_at": now}],
        refusal_runs=[{"query_id": "q1", "refused": True,
                       "source_id": "policy-library", "captured_at": now}],
        generated_at=now,
        retrieval_quality_baseline="specs/baselines/retrieval-quality.json",
    )
    assert manifest["schema"] == "threadlight.ground/v1"
    assert manifest["status"] == "complete"
    ctx = _make_ctx()
    _write_manifest(ctx, "ground-manifest.json", manifest)
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("GRD-001", "GRD-002", "GRD-003", "GRD-004"):
        assert f[fid].status == "pass", f"{fid}: {f[fid].detail}"


def test_real_load_manifest_propagates_all_load_pass() -> None:
    now = _now_iso()

    class _FakeAdapter:
        name = "fake-engine"

        def run(self, profile):
            return {"status": "complete",
                    "samples": [{"latency_ms": v, "success": True, "tokens": 50}
                                for v in (100, 200, 300, 400, 500)]}

    profile = {
        "name": "checkout-agent-smoke",
        "endpoint": {"url": "https://staging.example.test/api",
                     "credential_ref": "kv:load-test-key"},
        "duration_s": 30, "virtual_users": 10,
        "tokens_per_request_estimate": 500, "price_per_1k_tokens_usd": 0.002,
        "projected_token_cost_usd": 0.30,
        "slo": {"max_p95_latency_ms": 900, "max_error_rate": 0.5},
    }
    manifest = loadtest.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=_FakeAdapter(), generated_at=now,
    )
    assert manifest["schema"] == "threadlight.load/v1"
    assert manifest["status"] == "complete"
    ctx = _make_ctx()
    _write_manifest(ctx, "load-manifest.json", manifest)
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("LOAD-001", "LOAD-002", "LOAD-003"):
        assert f[fid].status == "pass", f"{fid}: {f[fid].detail}"


def test_real_upgrade_manifest_propagates_all_upg_pass() -> None:
    entry = {
        "surface": "agent-framework", "target": "agent-framework", "state": "stable",
        "source": "https://learn.microsoft.com/example", "last_reviewed": "2026-06-01",
        "review_window_days": 120, "stable": "2.0.0",
    }
    matrix = {
        "schema": "threadlight-upgrade-compatibility-matrix/v1", "version": "1.0",
        "date": "2026-06-01", "source": "https://learn.microsoft.com/matrix",
        "entries": [entry],
    }
    # scan_project stamps generated_at from its own clock, so the envelope is
    # always fresh at emit time.
    manifest = upgrade.scan_project(
        {}, matrix, "2026-06-15",
        source_results={"agent-framework:agent-framework": {"state": "stable"}},
    )
    assert manifest["schema"] == "threadlight.upgrade/v1"
    assert manifest["status"] == "complete"
    ctx = _make_ctx()
    _write_manifest(ctx, "upgrade-manifest.json", manifest)
    f = _by_id(pr._check_gap_leg_manifests(ctx))
    for fid in ("UPG-001", "UPG-002", "UPG-003"):
        assert f[fid].status == "pass", f"{fid}: {f[fid].detail}"
