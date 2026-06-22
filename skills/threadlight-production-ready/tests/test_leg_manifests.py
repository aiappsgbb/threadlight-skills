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
        "schema": "threadlight-govern-manifest/v1",
        "captured_at": _iso(datetime.now(timezone.utc)),
        "verdict": "wired",
        "capabilities": _caps({
            "middleware_wired_at_boundary": "pass",
            "policy_artefact_present": "pass",
            "policy_versioned": "pass",
            "rai_policy_present": "pass",
            "verifier_artefact_present": "pass",
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
# RAI (pillar 7): govern manifest flips RAI-002/003; red-team emits SAFE-1xx
# ---------------------------------------------------------------------------

def test_rai_manifest_flips_and_emits_safe() -> None:
    ctx = _make_ctx(manifests={
        "govern-manifest.json": _fresh_govern_manifest(),
        "redteam-manifest.json": _fresh_redteam_manifest(),
    })
    f = _by_id(pr._check_rai_static(ctx))
    assert f["RAI-002"].status == "pass", f["RAI-002"].detail
    assert f["RAI-003"].status == "pass", f["RAI-003"].detail
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
