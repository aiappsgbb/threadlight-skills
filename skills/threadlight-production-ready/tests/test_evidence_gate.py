#!/usr/bin/env python3
"""Tests for the evidence gate used during production readiness lifecycle.

TDD: these tests are written first and should fail until
`skills/threadlight-production-ready/scripts/evidence_gate.py` exists.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
        "capabilities": {},
    }


def _fresh_evals(verdict: str = "partial") -> dict:
    return {
        "schema": "threadlight-evals-manifest/v1",
        "tool_version": "1.0",
        "captured_at": "2026-01-01T00:00:00Z",
        "verdict": verdict,
        "capabilities": {},
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
        # Spec-compliant shape: top-level 'asr' and 'thresholds' alongside 'capabilities'
        "capabilities": {},
        "asr": {},
        "thresholds": {},
    }


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


def test_readiness_proof_passes_when_assurance_and_readiness_complete():
    root = fixture_workdir("sample-pilot-citadel")
    specs = root / "specs"
    # passing manifests
    _write(specs, "govern-manifest.json", _fresh_govern("governed"))
    _write(specs, "evals-manifest.json", _fresh_evals("comprehensive"))
    _write(specs, "redteam-manifest.json", _fresh_redteam("hardened"))

    # ensure postdeploy manifest has phase=post-deploy and gaps=[]
    post = root / "tests" / "postdeploy-manifest.json"
    post_data = json.loads(post.read_text(encoding="utf-8"))
    post_data["phase"] = "post-deploy"
    post_data["gaps"] = []
    post.write_text(json.dumps(post_data), encoding="utf-8")

    # production readiness manifest with scorecard + readiness fields
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

    out = eg.evaluate_evidence(root, mode="readiness-proof")
    assert out["status"] == "pass"
    assert out["readiness_asserted"] is True
    assert out["mode"] == "readiness-proof"
