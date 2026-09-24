"""Requesting-user consent is neither reviewer approval nor noop live proof."""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft7Validator
import pytest

from governance_consumer_fixtures import contract
from skills._shared.governance import (
    GovernanceContractError, normalize_requirement_token, probe_requirement_dimension,
    validate_governance_contract,
)


def confirmation_contract():
    document = contract(selected=True, consequence="write", tool_id="record_decision")
    document["framework"] = "microsoft-agent-framework"
    document["tools"][0]["requires"] = ["user-confirmation"]
    return document


def test_confirmation_vocabulary_is_distinct_from_review():
    assert normalize_requirement_token("User_Confirmation") == "user-confirmation"
    assert probe_requirement_dimension("user-confirmation") is None
    assert probe_requirement_dimension("approval") == "approval"


def test_confirmation_contract_preserves_unbound_reads_and_independent_review():
    document = confirmation_contract()
    document["tools"][0]["requires"].append("human-approval-record")
    document["tools"].append(contract()["tools"][0])
    original = deepcopy(document)
    normalized = validate_governance_contract(document, deployment_target="customer-pilot")
    assert normalized["tools"][0]["requires"] == ["user-confirmation", "human-approval-record"]
    assert normalized["tools"][1]["policy_binding"] is None
    assert document == original
    schema = Path(__file__).resolve().parents[3] / (
        "skills/threadlight-design/references/governance-contract.schema.json")
    Draft7Validator(json.loads(schema.read_text())).validate(document)


@pytest.mark.parametrize("change", [
    lambda d: d.update(framework="github-copilot-sdk"),
    lambda d: d["tools"][0].update(enforcement_path="local-agent-hooks"),
    lambda d: d["tools"][0].update(intervention_points=["post_tool_call"]),
    lambda d: d["tools"][0].update(id="governance_probe_noop"),
    lambda d: d["tools"][0].update(
        policy_binding="none", enforcement_path="none", intervention_points=[]),
])
def test_confirmation_rejects_unsupported_execution_paths(change):
    document = confirmation_contract()
    change(document)
    with pytest.raises(GovernanceContractError, match="user-confirmation"):
        validate_governance_contract(document, deployment_target="customer-pilot")


def test_confirmation_is_not_a_lifecycle_obligation():
    document = confirmation_contract()
    document["tools"][0]["requires"] = []
    document["governance"]["lifecycle_bindings"] = [{
        "lifecycle_point": "input", "policy_binding": "safe",
        "enforcement_path": "local-agent-hooks", "safe_principles": ["scope"],
        "requires": ["user-confirmation"],
    }]
    with pytest.raises(GovernanceContractError, match="user-confirmation"):
        validate_governance_contract(document, deployment_target="customer-pilot")
