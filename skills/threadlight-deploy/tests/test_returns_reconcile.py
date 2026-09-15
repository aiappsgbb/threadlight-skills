import importlib.util
import asyncio
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "references/governance/returns_reconcile.py"
    assert path.is_file()
    spec = importlib.util.spec_from_file_location("returns_reconcile", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def binding():
    return {
        "tenant_id": "tenant", "principal": "agent", "client_id": "client",
        "agent_id": "returns", "agent_version": "5", "image_digest": "sha256:" + "a" * 64,
        "policy_digest": "sha256:" + "b" * 64, "environment": "preproduction",
        "subscription": "subscription", "resource_group": "test",
    }


def response(tool, arguments, output):
    return {
        "id": "response-1", "agent_session_id": "session-1", "status": "completed",
        "agent_reference": {"name": "returns", "version": "5"},
        "created_at": 100, "completed_at": 110,
        "output": [
            {"type": "function_call", "call_id": "call-1", "name": tool,
             "arguments": json.dumps(arguments)},
            {"type": "function_call_output", "call_id": "call-1", "output": json.dumps(output)},
        ],
    }


def test_reconciliation_preserves_native_call_identity_without_raw_payloads():
    m = module()
    result = m.reconcile_response(
        response("returns_get_case", {"case_id": "case"}, {"id": "case", "_etag": '"e"',
                 "status": "in_triage", "amount": 1, "eligible": True, "high_risk": False}),
        binding=binding(), gateway_principal="gateway", central=[], operations=[], audits=[], read_audits=[])
    assert len(result) == 1
    body = result[0]["body"]
    assert body["tool_call_id"] == "call-1" and body["session_id"] == "session-1"
    assert body["status"] == "read-completed"
    assert body["policy_binding"] == "none"
    assert body["read_audit_delivery"] == "post-run-native-response-reconciliation"
    assert "arguments" not in body and "output" not in body


def test_reconciliation_rejects_unknown_tools_and_wrong_hosted_version():
    m = module()
    for value in (response("shell", {}, {}), {
            **response("returns_get_case", {"case_id": "case"}, {"id": "case"}),
            "agent_reference": {"name": "returns", "version": "4"}}):
        with pytest.raises(ValueError):
            m.reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                 central=[], operations=[], audits=[], read_audits=[])


def test_business_success_requires_independent_audit_and_receipt():
    m = module()
    value = response("returns_apply_decision", {"case_id": "case", "reason": "private-reason"},
                     {"audit_id": "decision-1", "case_id": "case", "decision": "approve_refund"})
    with pytest.raises(ValueError, match="business_audit"):
        m.reconcile_response(value, binding=binding(), gateway_principal="gateway",
                             central=[], operations=[], audits=[], read_audits=[])


def test_reconciliation_links_business_result_to_exact_receipt_and_completed_operation():
    m = module()
    expected = binding()
    arguments = {"case_id": "case", "decision": "approve_refund", "reason": "private-reason"}
    result = {"audit_id": "decision-1", "case_id": "case", "decision": "approve_refund"}
    facts = {"tenant": expected["tenant_id"], "subject": expected["principal"],
             "client": expected["client_id"], "action": "returns_apply_decision", "scope": "returns",
             "policy": expected["policy_digest"], "deployment": m.deployment(expected)}
    action_hash = m.digest({"facts": facts, "arguments": arguments})
    receipt = {"receipt_id": "receipt-1", "correlation_id": "operation-1", "decision": "allow",
               "action_hash": action_hash, "policy_digest": expected["policy_digest"],
               "agent_version": "5", "image_digest": expected["image_digest"]}
    central = [{"scope": "tenant", "body": {"owner": "gateway", "receipt": receipt}}]
    audit = {"id": "decision-1", "kind": "decision-audit", "result": result, "arguments": arguments,
             "provenance": {"action_hash": action_hash, "receipt_id": "receipt-1"}}
    operations = [{"id": "operation-1", "scope": m.digest(["tenant", "agent", "returns_apply_decision"]),
                   "body": {"state": "completed", "receipt_id": "receipt-1",
                            "action_hash": action_hash, "facts_hash": m.digest(facts)}}]
    rows = m.reconcile_response(response("returns_apply_decision", arguments, result),
                                binding=expected, gateway_principal="gateway", central=central,
                                operations=operations, audits=[audit], read_audits=[])
    assert rows[0]["body"]["status"] == "completed"
    assert rows[0]["body"]["policy_receipts"] == ["receipt-1"]
    assert "private-reason" not in str(rows)
    scope = operations[0]["scope"]
    operations[0]["scope"] = "foreign"
    with pytest.raises(ValueError, match="completed_gateway_operation"):
        m.reconcile_response(response("returns_apply_decision", arguments, result),
                             binding=expected, gateway_principal="gateway", central=central,
                             operations=operations, audits=[audit], read_audits=[])
    operations[0]["scope"] = scope
    receipt["image_digest"] = "sha256:" + "c" * 64
    with pytest.raises(ValueError, match="allow_receipt"):
        m.reconcile_response(response("returns_apply_decision", arguments, result),
                             binding=expected, gateway_principal="gateway", central=central,
                             operations=operations, audits=[audit], read_audits=[])


def expiry_inputs():
    m = module()
    expected = binding()
    arguments = {"case_id": "case", "decision": "escalate_to_supervisor", "reason": "review"}
    facts = {"tenant": "tenant", "subject": "agent", "client": "client",
             "action": "returns_apply_decision", "scope": "returns",
             "policy": expected["policy_digest"], "deployment": m.deployment(expected)}
    key = m.digest("original-operation")[7:]
    action = m.digest({"facts": facts, "arguments": arguments})
    intent = {"session_id": key, "action_hash": action, "context_identity": m.digest(facts),
              "tenant": "tenant", "principal": "gateway", "agent_id": "returns",
              "policy_hash": expected["policy_digest"], "nonce": "nonce",
              "expires_at": "1970-01-01T00:01:30+00:00"}
    operation = {"id": key, "scope": m.digest(["tenant", "agent", "returns_apply_decision"]),
                 "body": {"state": "awaiting_approval", "input_hash": action, "action_hash": action,
                          "facts_hash": m.digest(facts), "approval_intent": intent}}
    approval = {"id": "approval:nonce", "scope": "tenant",
                "body": {"state": "pending", "intent": intent.copy(), "grant": None}}
    value = response("returns_apply_decision",
                     {**arguments, "governance_operation_id": "original-operation"}, "Error: Function failed.")
    return value, operation, approval


def test_failed_expired_selector_correlates_exact_operation_without_inventing_denial_receipt():
    value, operation, approval = expiry_inputs()
    rows = module().reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                      central=[approval], operations=[operation], audits=[], read_audits=[])
    body = rows[0]["body"]
    assert body["status"] == "failed"
    assert body["approval_observation"] == "expired-ungranted-intent"
    assert body["gateway_correlation_id"] == operation["id"]
    assert body["policy_receipts"] == []
    assert "reason_code" not in body


@pytest.mark.parametrize("fault", ["scope", "arguments", "grant", "not-expired", "missing-output"])
def test_expiry_observation_cannot_borrow_foreign_changed_granted_or_later_state(fault):
    value, operation, approval = expiry_inputs()
    if fault == "scope":
        operation["scope"] = "foreign"
    elif fault == "arguments":
        operation["body"]["input_hash"] = "changed"
    elif fault == "grant":
        approval["body"]["grant"] = {"approved": True}
    elif fault == "not-expired":
        operation["body"]["approval_intent"]["expires_at"] = "1970-01-01T00:03:00+00:00"
        approval["body"]["intent"] = operation["body"]["approval_intent"].copy()
    else:
        value["output"].pop()
    rows = module().reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                      central=[approval], operations=[operation], audits=[], read_audits=[])
    assert rows[0]["body"]["status"] == "failed"
    assert "approval_observation" not in rows[0]["body"]


def test_denial_correlation_excludes_receipts_outside_native_response_window():
    value, operation, _ = expiry_inputs()
    receipt = {"receipt_id": "deny-1", "decision": "deny",
               "action_hash": operation["body"]["action_hash"],
               "policy_digest": binding()["policy_digest"], "agent_version": "5",
               "image_digest": binding()["image_digest"], "recorded_at": "1970-01-01T00:00:01+00:00"}
    central = [{"scope": "tenant", "body": {"owner": "gateway", "receipt": receipt}}]
    m = module()
    result = m.reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                  central=central, operations=[], audits=[], read_audits=[])
    assert result[0]["body"]["policy_receipts"] == []
    receipt["recorded_at"] = "1970-01-01T00:01:45+00:00"
    result = m.reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                  central=central, operations=[], audits=[], read_audits=[])
    assert result[0]["body"]["status"] == "denied"


def test_inline_read_audit_cannot_borrow_a_wrong_kind_or_partition():
    m = module()
    case = {"id": "case", "_etag": '"etag"', "status": "in_triage",
            "amount": 1, "eligible": True, "high_risk": False}
    audit = {"id": "read-1", "scope": "tenant:agent",
             "body": {"kind": "case-read", "action_id": "returns_get_case",
                      "subject": "agent", "client": "client", "case_id": "case", "case_revision": '"etag"',
                      "deployment": m.deployment(binding()), "policy_binding": "none", "result_digest": m.digest(case)}}
    value = response("returns_get_case", {"case_id": "case"}, {**case, "read_audit_id": "read-1"})
    def reconcile():
        return m.reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                    central=[], operations=[], audits=[], read_audits=[audit])
    assert reconcile()[0]["body"]["read_audit_delivery"] == "backend-acknowledged-before-return"
    audit["scope"] = "foreign"
    with pytest.raises(ValueError, match="read_audit"):
        reconcile()
    audit["scope"] = "tenant:agent"
    audit["body"]["kind"] = "native-tool-call"
    with pytest.raises(ValueError, match="read_audit"):
        reconcile()


def test_pending_requires_exact_selector_and_scoped_gateway_record():
    m = module()
    expected = binding()
    value, operation, _ = expiry_inputs()
    arguments = json.loads(value["output"][0]["arguments"])
    facts = {"tenant": "tenant", "subject": "agent", "client": "client",
             "action": "returns_apply_decision", "scope": "returns",
             "policy": expected["policy_digest"], "deployment": m.deployment(expected)}
    value["output"][1]["output"] = json.dumps({
        "status": "pending_approval", "operation_id": arguments["governance_operation_id"],
        "approval_intent": operation["body"]["approval_intent"], "review_context": facts})
    def reconcile():
        return m.reconcile_response(value, binding=expected, gateway_principal="gateway",
                                    central=[], operations=[operation], audits=[], read_audits=[])
    assert reconcile()[0]["body"]["status"] == "pending"
    operation["scope"] = "foreign"
    with pytest.raises(ValueError, match="pending_operation"):
        reconcile()


@pytest.mark.parametrize("fault", [None, "foreign-approval", "different-intent", "unconsumed"])
def test_historical_pending_can_join_a_completed_operation_only_through_its_consumed_intent(fault):
    m = module()
    value, operation, approval = expiry_inputs()
    intent = operation["body"].pop("approval_intent")
    operation["body"]["state"] = "completed"
    approval["body"].update(state="consumed", grant={"intent": intent.copy(), "approved": True})
    arguments = json.loads(value["output"][0]["arguments"])
    facts = {"tenant": "tenant", "subject": "agent", "client": "client",
             "action": "returns_apply_decision", "scope": "returns",
             "policy": binding()["policy_digest"], "deployment": m.deployment(binding())}
    value["output"][1]["output"] = json.dumps({
        "status": "pending_approval", "operation_id": arguments["governance_operation_id"],
        "approval_intent": intent, "review_context": facts})
    if fault == "foreign-approval":
        approval["scope"] = "foreign"
    elif fault == "different-intent":
        approval["body"]["grant"]["intent"]["nonce"] = "different"
    elif fault == "unconsumed":
        approval["body"]["state"] = "pending"
    def reconcile():
        return m.reconcile_response(value, binding=binding(), gateway_principal="gateway",
                                    central=[approval], operations=[operation], audits=[], read_audits=[])
    if fault:
        with pytest.raises(ValueError, match="pending_operation"):
            reconcile()
    else:
        body = reconcile()[0]["body"]
        assert body["status"] == "pending"
        assert body["subsequent_operation_state"] == "completed"


def test_container_selection_preserves_defaults_and_requires_complete_distinct_mapping():
    m = module()
    roles = ("governance-records", "gateway-idempotency", "returns-cases", "runner-activity")
    assert m.selected_containers({}) == {name: name for name in roles}
    selected = {name: "s2-" + name for name in roles}
    assert m.selected_containers({"containers": selected}) == selected


@pytest.mark.parametrize("invalid", [None, {}, {"runner-activity": "s2-audit"},
                                    {"unknown": "container"}, False])
def test_invalid_selected_containers_fail_before_cloud_access_or_output_creation(tmp_path, invalid):
    m = module()
    destination = tmp_path / "not-created"
    with pytest.raises(ValueError, match="container_selection"):
        asyncio.run(m.collect({"containers": invalid}, destination))
    assert not destination.exists()


@pytest.mark.parametrize("value", ["same", "../outside", "", True])
def test_container_names_cannot_alias_or_escape_selected_stores(value):
    m = module()
    names = {name: "same" if value == "same" else name
             for name in ("governance-records", "gateway-idempotency", "returns-cases", "runner-activity")}
    names["runner-activity"] = value
    with pytest.raises(ValueError):
        m.selected_containers({"containers": names})


def test_snapshot_reads_use_selected_containers_but_reconciliation_keeps_canonical_roles():
    m = module()
    roles = ("governance-records", "gateway-idempotency", "returns-cases", "runner-activity")
    selected = {name: "s2-" + name for name in roles}
    accessed = []

    class Container:
        def __init__(self, name):
            self.name = name

        async def query_items(self, query):
            assert query == "SELECT * FROM c"
            yield {"id": self.name}

    class Database:
        def get_container_client(self, name):
            accessed.append(name)
            return Container(name)

    result = asyncio.run(m.read_stores(Database(), selected))
    assert accessed == list(selected.values())
    assert result == {role: [{"id": selected[role]}] for role in roles}
