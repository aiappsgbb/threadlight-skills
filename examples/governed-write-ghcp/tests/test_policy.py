"""Native ACS/OPA evaluation of the governed-write policy: the four business rules.

Runs the real published ACS engine (Linux amd64, governance_runtime marker) on the
exact bundle that scripts/build_policy.py builds. Offline policy evidence only:
not gateway dispatch, approval, audit or live Azure proof.
"""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.governance_runtime

EXAMPLE = Path(__file__).resolve().parents[1]


def build_policy():
    spec = importlib.util.spec_from_file_location("governed_write_build", EXAMPLE / "scripts/build_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG = {
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "gateway_url": "https://gateway.example/mcp",
    "agent_id": "returns-governed", "agent_version": "1",
    "agent_image": "registry.example/agent@sha256:" + "a" * 64,
    "agent_principal": "22222222-2222-2222-2222-222222222222",
    "environment": "preproduction",
    "subscription": "33333333-3333-3333-3333-333333333333", "resource_group": "rg-example",
    "business_url": "https://business.example", "business_scope": "api://44444444-4444-4444-4444-444444444444/.default",
}


@pytest.mark.parametrize("case_id,decision,expected", [
    ("RMA-W01-A", "approve_refund", ("allow", "ordinary_return_decision")),
    ("RMA-W01-A", "request_more_info", ("allow", "ordinary_return_decision")),
    ("RMA-W01-A", "deny_refund", ("deny", "refund_eligibility_mismatch")),
    ("RMA-W01-B", "approve_refund", ("deny", "refund_eligibility_mismatch")),
    ("RMA-W01-B", "deny_refund", ("allow", "ordinary_return_decision")),
    ("RMA-W01-C", "escalate_to_supervisor", ("escalate", "supervisor_handoff_requires_approval")),
    ("RMA-W01-C", "approve_refund", ("deny", "high_value_requires_supervisor")),
    ("RMA-UNKNOWN", "approve_refund", ("deny", "case_not_in_signed_snapshot")),
])
def test_four_business_rules_under_native_acs(tmp_path, case_id, decision, expected):
    from agent_control_specification import AgentControl
    bundle = build_policy().build(tmp_path / "bundle", policy_id="returns-write", version="1", config=CONFIG)
    engine = AgentControl.from_path(str(bundle.manifest_path))
    arguments = {"case_id": case_id, "expected_etag": "\"etag\"", "decision": decision, "reason": "fixture"}
    outcome = asyncio.run(engine.evaluate_intervention_point(
        "pre_tool_call", {"tool_call": {"name": "returns_apply_decision", "args": arguments},
                          "safe": {"verified": True}}, mode="enforce"))
    assert (outcome.verdict.decision.value, outcome.verdict.reason) == expected


def test_final_bundle_registry_selects_policy_deferred_review(tmp_path):
    from govern_gateway.dispatcher import Registry
    bundle = build_policy().build(tmp_path / "bundle", policy_id="returns-write", version="1", config=CONFIG)
    registry = Registry.model_validate_json((bundle.root / "gateway-registry.json").read_bytes())
    action = registry.actions[0]
    assert (action.name, action.approval_mode, action.approval_requirement, action.approval_roles) == (
        "returns_apply_decision", "deferred", "policy", ["Approver"])
    assert action.workloads == [CONFIG["agent_principal"]]
    bootstrap = build_policy().build(tmp_path / "bootstrap", policy_id="returns-write", version="1")
    assert not (bootstrap.root / "gateway-registry.json").exists()
    assert json.loads((EXAMPLE / "governance/cases.json").read_text())["cases"][0]["case_id"] == "RMA-W01-A"
