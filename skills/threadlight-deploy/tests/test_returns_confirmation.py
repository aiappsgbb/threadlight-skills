"""The running returns reference must stop on requester consent, not invent it."""
import importlib.util
from pathlib import Path

import pytest

from skills._shared.governance import validate_governance_contract


def reference():
    source = Path(__file__).resolve().parents[1] / "references/governance/returns_mcp_agent.py"
    spec = importlib.util.spec_from_file_location("returns_confirmation_reference", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_returns_confirmation_is_an_explicit_binding_and_reads_remain_unbound():
    module = reference()
    original = module.contract()
    assert "user-confirmation" not in original["tools"][0]["requires"]
    selected = validate_governance_contract(
        module.contract(confirmation=True, evidence=True), deployment_target="customer-pilot")
    assert {"signed-evidence", "user-confirmation"} <= set(selected["tools"][0]["requires"])
    assert all(tool["policy_binding"] is None for tool in selected["tools"][1:])


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_returns_confirmation_cannot_silently_coerce_configuration(value):
    with pytest.raises(ValueError, match="boolean_confirmation"):
        reference().contract(confirmation=value)


def test_returns_instructions_preserve_real_context_and_stop_for_user_confirmation():
    text = reference().agent_instructions(confirmation=True, evidence=True)
    for term in ("pending_confirmation", "governance_request_context", "governance_operation_id",
                 "Never create", "outside the agent", "same operation", "returns_verify_purchase"):
        assert term in text
    assert "pending_approval" in text
    assert "NOT financial settlement" in text
    assert "Approve or Reject directly in the email" in text
    assert "Do not tell the user to run commands" in text
