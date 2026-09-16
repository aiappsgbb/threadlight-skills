"""Release acceptance consumes canonical evidence, never a verdict alone."""
import copy
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "release_evidence_gate",
    ROOT / "skills/threadlight-production-ready/scripts/evidence_gate.py",
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
NOW = datetime(2026, 9, 16, 10, tzinfo=timezone.utc)
START = NOW - timedelta(minutes=2)


def document(domain):
    if domain == "mcp":
        return {
            "schema_version": "1.0", "generator": "threadlight-production-ready/mcp_sbom",
            "generator_version": "0.1.0", "servers": [],
            "summary": dict(server_count=0, pinned=0, unpinned=0, remote=0,
                            inline_creds=0, must_fix=0, should_fix=0),
        }
    names = gate._EVALS_CAPABILITIES if domain == "evals" else gate._REDTEAM_CAPABILITIES
    result = {
        "schema": f"threadlight-{domain}-manifest/v1", "tool_version": "1.0.0",
        "captured_at": NOW.isoformat(),
        "verdict": "comprehensive" if domain == "evals" else "hardened",
        "must_fix": [], "should_fix": [], "not_verified": [],
        "capabilities": {key: dict(status="pass", **(
            {"check_id": key} if domain == "evals" else {})) for key in names},
    }
    if domain == "evals":
        result["metrics"] = dict(pass_rate=0.98, threshold=0.95, latest_run="evals/runs/current.json")
    else:
        result.update(
            scan_captured_at=NOW.isoformat(), num_attacks=100,
            asr={key: 0.01 for key in ("jailbreak", "prompt_injection", "exfiltration", "harmful_content")},
            thresholds=dict(max_asr=0.1, freshness_days=1, min_attacks=100),
        )
    return result


def accept(domain, data, **options):
    return gate.validate_release_manifest(
        domain, data, now=NOW, not_before=START, max_age_seconds=900, **options)


@pytest.mark.parametrize("domain", ["evals", "redteam", "mcp"])
def test_complete_current_canonical_evidence_passes(domain):
    assert accept(domain, document(domain))["domain"] == domain


@pytest.mark.parametrize("domain", ["evals", "redteam", "mcp"])
@pytest.mark.parametrize("data", [{}, {"verdict": "partial"}, {"summary": {"must_fix": 0}}])
def test_empty_and_verdict_only_documents_never_pass(domain, data):
    with pytest.raises(gate.EvidenceGateError):
        accept(domain, data)


@pytest.mark.parametrize("domain", ["evals", "redteam"])
@pytest.mark.parametrize("age", [timedelta(days=60), timedelta(minutes=3), timedelta(seconds=-60)])
def test_reissued_or_future_evidence_fails_current_execution_window(domain, age):
    data = document(domain)
    data["captured_at"] = (NOW - age).isoformat()
    with pytest.raises(gate.EvidenceGateError):
        accept(domain, data)


@pytest.mark.parametrize("domain", ["evals", "redteam"])
@pytest.mark.parametrize("field,value", [
    ("must_fix", ["contradiction"]), ("must_fix", None),
    ("should_fix", ["contradiction"]), ("not_verified", ["contradiction"]),
])
def test_aggregate_pass_does_not_hide_findings(domain, field, value):
    data = document(domain)
    data[field] = value
    with pytest.raises(gate.EvidenceGateError):
        accept(domain, data)


@pytest.mark.parametrize("value", [0.8, None, True, float("nan"), float("inf")])
def test_quality_threshold_is_numeric_and_actually_met(value):
    data = document("evals")
    data["metrics"]["pass_rate"] = value
    with pytest.raises(gate.EvidenceGateError):
        accept("evals", data)


def test_weaker_declared_quality_threshold_cannot_weaken_release_policy():
    data = document("evals")
    data["metrics"]["threshold"] = 0.5
    with pytest.raises(gate.EvidenceGateError):
        accept("evals", data, min_pass_rate=0.95)


@pytest.mark.parametrize("mutation", ["old_scan", "few_attacks", "missing_domain", "high_asr", "weaker_threshold"])
def test_redteam_checks_real_scan_and_each_required_attack_domain(mutation):
    data = document("redteam")
    if mutation == "old_scan":
        data["scan_captured_at"] = (NOW - timedelta(days=60)).isoformat()
    elif mutation == "few_attacks":
        data["num_attacks"] = 1
    elif mutation == "missing_domain":
        data["asr"].pop("exfiltration")
    elif mutation == "high_asr":
        data["asr"]["prompt_injection"] = 0.7
    else:
        data["thresholds"]["max_asr"] = 0.9
    with pytest.raises(gate.EvidenceGateError):
        accept("redteam", data)


@pytest.mark.parametrize("mutation", ["missing_count", "negative_count", "bool_count", "wrong_count", "hidden_findings"])
def test_mcp_missing_and_contradictory_summary_fails(mutation):
    data = document("mcp")
    if mutation == "missing_count":
        data["summary"].pop("must_fix")
    elif mutation == "negative_count":
        data["summary"]["must_fix"] = -1
    elif mutation == "bool_count":
        data["summary"]["must_fix"] = False
    elif mutation == "wrong_count":
        data["summary"]["server_count"] = 3
    else:
        data["servers"] = [{"id": "one", "findings": [{"status": "must-fix"}]}]
        data["summary"]["server_count"] = 1
    with pytest.raises(gate.EvidenceGateError):
        accept("mcp", data)


def test_partial_is_not_an_implicit_release_waiver():
    data = document("evals")
    data["verdict"] = "partial"
    data["capabilities"]["ab_comparison_present"]["status"] = "should-fix"
    data["should_fix"] = ["ab_comparison_present"]
    with pytest.raises(gate.EvidenceGateError):
        accept("evals", data)


def test_explicit_optional_capability_scope_does_not_waive_quality():
    data = document("evals")
    data["verdict"] = "partial"
    data["capabilities"]["ab_comparison_present"]["status"] = "should-fix"
    data["should_fix"] = ["ab_comparison_present"]
    required = sorted(gate._EVALS_CAPABILITIES - {"ab_comparison_present"})
    assert accept("evals", data, required_capabilities=required)["domain"] == "evals"
    broken = copy.deepcopy(data)
    broken["metrics"]["pass_rate"] = 0.1
    with pytest.raises(gate.EvidenceGateError):
        accept("evals", broken, required_capabilities=required)


def test_required_scope_cannot_disable_threshold_and_freshness_checks():
    with pytest.raises(gate.EvidenceGateError):
        accept("evals", document("evals"), required_capabilities=["eval_scenarios_present"])
