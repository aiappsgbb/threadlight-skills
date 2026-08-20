#!/usr/bin/env python3
"""Tests for the evidence gate used during production readiness lifecycle.

TDD: these tests are written first and should fail until
`skills/threadlight-production-ready/scripts/evidence_gate.py` exists.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "evidence_gate.py"

# allow importing the script module once implemented
sys.path.insert(0, str(SCRIPT.parent))
# allow importing test helpers
sys.path.insert(0, str(TEST_DIR))

from fixture_workdir import fixture_workdir

# Import the script module at top-level so pytest fails at collection when
# the module is absent, matching the spec for Task 1.
import evidence_gate as eg  # noqa: E402
from evidence_gate import EvidenceGateError


def _write(specs_dir: Path, name: str, data: dict) -> None:
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / name).write_text(json.dumps(data), encoding="utf-8")


def _fresh_govern(verdict: str = "partial") -> dict:
    return {
        "schema": "threadlight-govern-manifest/v2",
        "tool_version": "1.0",
        "captured_at": "2026-01-01T00:00:00Z",
        "verdict": verdict,
        "capabilities": {
            "policy_artefact_present": {"status": "pass"},
            "policy_schema_valid": {"status": "pass"},
            "policy_versioned": {"status": "pass"},
            "policy_default_deny": {"status": "pass"},
            "sensitive_action_rules_present": {"status": "pass"},
            "policy_tests_present": {"status": "pass"},
            "ci_gate_present": {"status": "pass"},
            "attestation_present": {"status": "pass"},
            "attestation_fresh": {"status": "pass"},
            "asi_reference_present": {"status": "pass"},
        },
    }


def _fresh_evals(verdict: str = "partial") -> dict:
    return {
        "schema": "threadlight-evals-manifest/v1",
        "tool_version": "1.0",
        "captured_at": "2026-01-01T00:00:00Z",
        "verdict": verdict,
        "capabilities": {
            "eval_scenarios_present": {"check_id": "eval-001", "status": "pass"},
            "eval_datasets_present": {"check_id": "eval-002", "status": "pass"},
            "dataset_shape_ok": {"check_id": "eval-003", "status": "pass"},
            "thresholds_declared": {"check_id": "eval-004", "status": "pass"},
            "schedule_present": {"check_id": "eval-005", "status": "pass"},
            "run_history_present": {"check_id": "eval-006", "status": "pass"},
            "online_eval_wired": {"check_id": "eval-007", "status": "pass"},
            "latest_eval_run_fresh": {"check_id": "eval-008", "status": "pass"},
            "alert_wired": {"check_id": "eval-009", "status": "pass"},
            "latest_pass_rate_ok": {"check_id": "eval-010", "status": "pass"},
            "ab_comparison_present": {"check_id": "eval-011", "status": "pass"},
        },
    }


def _fresh_redteam(verdict: str = "partial") -> dict:
    return {
        "schema": "threadlight-redteam-manifest/v1",
        "tool_version": "1.0",
        "captured_at": "2026-01-01T00:00:00Z",
        "verdict": verdict,
        "must_fix": [],
        "should_fix": [],
        "not_verified": [],
        "capabilities": {
            "scan_present": {"status": "pass"},
            "scan_fresh": {"status": "pass"},
            "jailbreak_asr_ok": {"status": "pass"},
            "prompt_injection_asr_ok": {"status": "pass"},
            "exfiltration_asr_ok": {"status": "pass"},
            "harmful_content_asr_ok": {"status": "pass"},
            "coverage_ok": {"status": "pass"},
        },
        "asr": {
            "jailbreak": 0.0,
            "prompt_injection": 0.0,
            "indirect_attack": 0.0,
            "exfiltration": 0.0,
            "harmful_content": 0.0,
        },
        "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
    }


def _write_passing_readiness_artifacts(root: Path) -> None:
    post = root / "tests" / "postdeploy-manifest.json"
    post_data = json.loads(post.read_text(encoding="utf-8"))
    manifest = json.loads((root / "specs" / "manifest.json").read_text(encoding="utf-8"))
    post_data["checked_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    post_data["deployment_manifest"] = manifest["deployment_manifest"]
    post_data["phase"] = "post-deploy"
    post_data["gaps"] = []
    post.write_text(json.dumps(post_data), encoding="utf-8")

    readiness = {
        "would_fail_hard_gate": False,
        "go_live_recommendation": "ready",
        "kpi_scorecard": {
            "latency_declared": True,
            "cost_per_interaction_declared": True,
            "success_rate_declared": True,
            "deviation_alert_present": True,
            "traces_emit": True,
            "eval_pass_rate": 0.99,
            "cost_per_interaction_usd": 0.01,
        },
    }
    (root / "tests" / "production-readiness-manifest.json").write_text(
        json.dumps(readiness), encoding="utf-8"
    )


def test_live_smoke_accepts_non_passing_assurance_verdicts():
    """live-smoke should validate manifest structure but not assert readiness.

    It must return status=pass, readiness_asserted=false and the exact verdict map.
    """
    root = fixture_workdir("sample-pilot-broken")
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("partial"))
    _write(specs, "evals-manifest.json", _fresh_evals("none"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("partial"))

    out = eg.evaluate_evidence(root, mode="live-smoke")
    assert out["status"] == "pass"
    assert out["mode"] == "live-smoke"
    assert out["readiness_asserted"] is False
    assert out.get("verdicts") == {
        "govern": "partial",
        "evals": "none",
        "redteam": "partial",
    }


def test_readiness_proof_requires_passing_governed():
    root = fixture_workdir("sample-pilot-broken")
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("ungoverned"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for ungoverned manifest")
    except EvidenceGateError as e:
        msg = str(e).lower()
        assert "govern" in msg and "governed" in msg


@pytest.mark.parametrize(
    ("capabilities", "expected_fragment"),
    [
        ({}, "missing capabilities"),
        (
            {
                "policy_artefact_present": {"status": "pass"},
                "policy_schema_valid": {"status": "pass"},
            },
            "missing capabilities",
        ),
        (
            {
                **_fresh_govern("governed")["capabilities"],
                "unexpected_capability": {"status": "pass"},
            },
            "unsupported capabilities",
        ),
    ],
)
def test_readiness_proof_requires_exact_govern_capabilities(capabilities, expected_fragment):
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    govern = _fresh_govern("governed")
    govern["capabilities"] = capabilities
    _write(specs, "govern-manifest.json", govern)
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    with pytest.raises(EvidenceGateError, match=expected_fragment):
        eg.evaluate_evidence(root, mode="readiness-proof")


def test_readiness_proof_requires_safe_check_and_scorecard_and_fails_postdeploy_when_missing():
    root = fixture_workdir("sample-pilot-broken")
    # remove the postdeploy manifest to simulate missing safe-check
    (root / "tests" / "postdeploy-manifest.json").unlink()
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError when safe-check is missing")
    except EvidenceGateError as e:
        assert "postdeploy-manifest" in str(e)


def test_readiness_proof_requires_production_scorecard_evidence():
    # When assurance manifests and a passing postdeploy manifest are present,
    # readiness-proof must still require a production-readiness kpi_scorecard.
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    # passing manifests
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))

    post = root / "tests" / "postdeploy-manifest.json"
    post_data = json.loads(post.read_text(encoding="utf-8"))
    post_data["phase"] = "post-deploy"
    post_data["gaps"] = []
    post.write_text(json.dumps(post_data), encoding="utf-8")

    readiness = {
        "would_fail_hard_gate": False,
        "go_live_recommendation": "ready",
    }
    (root / "tests" / "production-readiness-manifest.json").write_text(
        json.dumps(readiness), encoding="utf-8"
    )

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError when production readiness scorecard missing/invalid")
    except EvidenceGateError as e:
        msg = str(e).lower()
        assert "kpi" in msg or "scorecard" in msg or "production readiness kpi_scorecard" in msg


def test_readiness_proof_passes_when_assurance_and_readiness_complete():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    out = eg.evaluate_evidence(root, mode="readiness-proof")
    assert out["status"] == "pass"
    assert out["readiness_asserted"] is True
    assert out["mode"] == "readiness-proof"


def test_readiness_proof_rejects_stale_or_malformed_postdeploy_checked_at():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    post = root / "tests" / "postdeploy-manifest.json"
    stale = json.loads(post.read_text(encoding="utf-8"))
    stale["checked_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    post.write_text(json.dumps(stale), encoding="utf-8")

    with pytest.raises(EvidenceGateError, match="fresher than 24h"):
        eg.evaluate_evidence(root, mode="readiness-proof")

    stale["checked_at"] = "2026-08-06 08:00:00"
    post.write_text(json.dumps(stale), encoding="utf-8")

    with pytest.raises(EvidenceGateError, match="checked_at"):
        eg.evaluate_evidence(root, mode="readiness-proof")

    stale["checked_at"] = "2026-08-06 08:00:00+00:00"
    post.write_text(json.dumps(stale), encoding="utf-8")

    with pytest.raises(EvidenceGateError, match="checked_at"):
        eg.evaluate_evidence(root, mode="readiness-proof")


def test_readiness_proof_rejects_postdeploy_manifest_binding_drift():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    post = root / "tests" / "postdeploy-manifest.json"
    post_data = json.loads(post.read_text(encoding="utf-8"))
    post_data["deployment_manifest"]["resource_group"] = "rg-drifted"
    post.write_text(json.dumps(post_data), encoding="utf-8")

    with pytest.raises(EvidenceGateError, match="deployment_manifest"):
        eg.evaluate_evidence(root, mode="readiness-proof")


def test_readiness_proof_rejects_schema_invalid_evals_manifest():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_evals = _fresh_evals("comprehensive")
    invalid_evals["capabilities"] = {}
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", invalid_evals)
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for schema-invalid evals manifest")
    except EvidenceGateError as e:
        msg = str(e)
        assert "evals-manifest.json" in msg
        assert "capabilities" in msg or "check_id" in msg


def test_readiness_proof_rejects_schema_invalid_redteam_thresholds():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_redteam = _fresh_redteam("hardened")
    invalid_redteam["thresholds"] = {"max_asr": 1.5, "freshness_days": -1, "min_attacks": 0}
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", invalid_redteam)
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for schema-invalid redteam thresholds")
    except EvidenceGateError as e:
        msg = str(e)
        assert "redteam-manifest.json" in msg
        assert "threshold" in msg or "max_asr" in msg or "min_attacks" in msg


def test_readiness_proof_rejects_assurance_manifest_with_invalid_captured_at():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_govern = _fresh_govern("governed")
    invalid_govern["captured_at"] = "not-a-date"
    _write(specs, "govern-manifest.json", invalid_govern)
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for invalid assurance manifest captured_at")
    except EvidenceGateError as e:
        msg = str(e)
        assert "govern-manifest.json" in msg
        assert "captured_at" in msg


def test_readiness_proof_rejects_invalid_optional_evals_freshness_window_days():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_evals = _fresh_evals("comprehensive")
    invalid_evals["freshness_window_days"] = "seven"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", invalid_evals)
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for invalid evals freshness_window_days")
    except EvidenceGateError as e:
        msg = str(e)
        assert "evals-manifest.json" in msg
        assert "freshness_window_days" in msg


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("must_fix", "not-a-list"),
        ("should_fix", ["keep", 1]),
        ("not_verified", {"oops": 1}),
    ],
)
def test_readiness_proof_rejects_invalid_optional_govern_findings_lists(field, invalid_value):
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_govern = _fresh_govern("governed")
    invalid_govern[field] = invalid_value
    _write(specs, "govern-manifest.json", invalid_govern)
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError(f"expected EvidenceGateError for invalid govern {field}")
    except EvidenceGateError as e:
        msg = str(e)
        assert "govern-manifest.json" in msg
        assert field in msg


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("must_fix", "not-a-list"),
        ("should_fix", {"oops": 1}),
        ("not_verified", ["keep", 1]),
    ],
)
def test_readiness_proof_rejects_invalid_optional_evals_findings_lists(field, invalid_value):
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_evals = _fresh_evals("comprehensive")
    invalid_evals[field] = invalid_value
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", invalid_evals)
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError(f"expected EvidenceGateError for invalid evals {field}")
    except EvidenceGateError as e:
        msg = str(e)
        assert "evals-manifest.json" in msg
        assert field in msg


@pytest.mark.parametrize("invalid_value", ["bogus", 123, {"oops": 1}])
def test_readiness_proof_rejects_invalid_optional_govern_agt_profile(invalid_value):
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_govern = _fresh_govern("governed")
    invalid_govern["agt_profile"] = invalid_value
    _write(specs, "govern-manifest.json", invalid_govern)
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for invalid govern agt_profile")
    except EvidenceGateError as e:
        msg = str(e)
        assert "govern-manifest.json" in msg
        assert "agt_profile" in msg


def test_readiness_proof_rejects_invalid_optional_redteam_typed_fields():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_redteam = _fresh_redteam("hardened")
    invalid_redteam["num_attacks"] = "25"
    invalid_redteam["strategies"] = "not-a-list"
    invalid_redteam["scan_result"] = 123
    invalid_redteam["tool"] = {"name": "pyrit"}
    invalid_redteam["scan_captured_at"] = "not-a-date"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", invalid_redteam)
    _write_passing_readiness_artifacts(root)

    try:
        eg.evaluate_evidence(root, mode="readiness-proof")
        raise AssertionError("expected EvidenceGateError for invalid optional redteam typed fields")
    except EvidenceGateError as e:
        msg = str(e)
        assert "redteam-manifest.json" in msg
        assert any(
            field in msg
            for field in ("num_attacks", "strategies", "scan_result", "tool", "scan_captured_at")
        )


def test_cli_success_returns_json_payload_and_exit_zero():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--mode", "readiness-proof"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "pass"
    assert payload["mode"] == "readiness-proof"
    assert payload["readiness_asserted"] is True


def test_cli_argument_failure_returns_json_payload_and_exit_two():
    root = fixture_workdir("sample-pilot-citadel")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["status"] == "fail"
    assert payload["mode"] is None
    assert "mode" in payload["error"].lower()


def test_cli_evidence_failure_returns_json_payload_and_exit_two():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    invalid_evals = _fresh_evals("comprehensive")
    invalid_evals["capabilities"] = {}
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", invalid_evals)
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))
    _write_passing_readiness_artifacts(root)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--mode", "readiness-proof"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["status"] == "fail"
    assert payload["mode"] == "readiness-proof"
    assert "evals-manifest.json" in payload["error"]
