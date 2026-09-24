"""Requesting users approve directly in native Outlook, never through a command email."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "references/governance/user-confirmation-approval.bicep"


def compiled_template(tmp_path):
    assert SOURCE.is_file(), "Provide native requester approval, not a notification with a CLI command"
    supplied = os.environ.get("THREADLIGHT_USER_CONFIRMATION_APPROVAL_BICEP")
    if supplied:
        return json.loads(Path(supplied).read_text())
    compiler = shutil.which("bicep")
    command = [compiler, "build"] if compiler else ["az", "bicep", "build", "--file"]
    output = tmp_path / "requester-approval.json"
    result = subprocess.run([*command, str(SOURCE), "--outfile", str(output)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text())


def test_requester_email_uses_native_action_and_distinct_confirmation_contract(tmp_path):
    template = compiled_template(tmp_path)
    resources = template["resources"]
    resources = list(resources.values()) if isinstance(resources, dict) else resources
    workflow = next(r for r in resources if r["type"] == "Microsoft.Logic/workflows")
    assert not any(r["type"] == "Microsoft.Web/connections" for r in resources)
    assert template["parameters"]["workflowState"]["defaultValue"] == "Disabled"
    access = workflow["properties"]["accessControl"]["triggers"]
    assert access["sasAuthenticationPolicy"]["state"] == "Disabled"
    policies = access["openAuthenticationPolicies"]["policies"]
    assert set(policies) == {"control_plane"}
    assert {c["name"] for c in policies["control_plane"]["claims"]} == {"iss", "aud", "oid"}
    definition = workflow["properties"]["definition"]
    schema = definition["triggers"]["User_confirmation_requested"]["inputs"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "status", "confirmation_id", "operation_id", "action_hash", "intent_digest",
        "expires_at", "confirmation_intent", "review_context", "proposed_arguments",
    }
    assert schema["properties"]["status"]["enum"] == ["pending_confirmation"]
    assert "allowed_roles" not in schema["properties"]["confirmation_intent"]["properties"]
    gate = definition["actions"]["Require_fresh_pending"]
    for term in ("pending_confirmation", "ticks(utcNow())", "confirmation_intent",
                 "confirmation_id", "operation_id", "action_hash", "policy_expires_at"):
        assert term in gate["expression"]
    assert set(gate["actions"]) == {"Send_approval_email"}
    email = gate["actions"]["Send_approval_email"]
    assert email["type"] == "ApiConnectionWebhook"
    assert email["inputs"]["path"] == "/approvalmail/$subscriptions"
    message = email["inputs"]["body"]["Message"]
    assert message["To"] == "[parameters('requesterEmail')]"
    assert message["Options"] == "Approve,Reject"
    assert message["ShowHTMLConfirmationDialog"] is True
    assert email["runtimeConfiguration"]["secureData"]["properties"] == ["inputs", "outputs"]
    assert definition["outputs"]["outlook_decision"]["value"] == {
        "response": "@body('Send_approval_email')", "request": "@triggerBody()",
        "observed_at": "@utcNow()", "control_plane_grant_created": False,
        "business_effect_executed": False,
    }
    grants = [r for r in resources if r["type"] == "Microsoft.Authorization/roleAssignments"]
    assert len(grants) == 1 and grants[0]["condition"] == "[parameters('grantWorkflowReader')]"
    assert "Microsoft.Logic/workflows" in grants[0]["scope"]
    assert template["parameters"]["grantWorkflowReader"]["defaultValue"] is False


def test_requester_email_is_readable_and_contains_no_command_or_fake_mfa():
    assert SOURCE.is_file()
    text = SOURCE.read_text()
    body = next(line for line in text.splitlines() if line.strip().startswith("Body:"))
    for label in ("Case:", "Proposed action:", "Reason:", "convertTimeZone", "decision_labels"):
        assert label in body
    assert "string(triggerBody()" not in body
    assert all(entity in body for entity in ("&amp;", "&lt;", "&gt;"))
    for prohibited in ("python", "threadlight-confirm-action", "confirmation/open", "/v2/Mail"):
        assert prohibited not in text
    assert "No payment" in body


@pytest.mark.governance_runtime
def test_actual_native_confirmation_payload_matches_compiled_mail_trigger(tmp_path):
    from jsonschema import Draft7Validator, FormatChecker
    from test_control_plane import module
    from test_gateway import GatewayHarness, Credential, registry
    from test_user_confirmation import configure_confirmation, register_context, selection
    from test_native_user_confirmation import NativeConfirmationHarness

    template = compiled_template(tmp_path)
    resources = template["resources"]
    resources = list(resources.values()) if isinstance(resources, dict) else resources
    definition = next(r for r in resources if r["type"] == "Microsoft.Logic/workflows")[
        "properties"]["definition"]
    schema = deepcopy(definition["triggers"]["User_confirmation_requested"]["inputs"]["schema"])
    schema["properties"]["proposed_arguments"]["properties"]["case_id"]["enum"] = ["RMA-1"]

    async def run():
        document = registry()
        action = document["actions"][0]
        action.update(
            name="returns_apply_decision", approval_roles=[],
            input_schema=deepcopy(schema["properties"]["proposed_arguments"]),
            confirmation_requirement=selection())
        h = await GatewayHarness().initialize(tmp_path / "native", document=document)
        native = None
        try:
            await configure_confirmation(h.cp)
            profile = h.cp.service.confirmation.profile("email-basic")
            profile = profile.model_copy(update={"workloads": [
                binding.model_copy(update={"actions": [action["name"]]})
                for binding in profile.workloads]})
            h.cp.service.confirmation.config.profiles["email-basic"] = profile
            native = await NativeConfirmationHarness().initialize(h.cp)
            registered = await register_context(h.cp, key="one", changes={"action": action["name"]})
            assert registered.status_code == 200
            h.dispatcher.confirmations = module("confirmation_client").ConfirmationClient(
                base_url="https://control.example", scope="api://governance/.default",
                credential=Credential(h.cp.token()), http=h.cp.client)
            pending = await h.dispatcher.dispatch(
                authorization="Bearer " + h.cp.token(), action=action["name"],
                arguments={"case_id": "RMA-1", "expected_etag": '"r1"',
                           "decision": "approve_refund", "reason": "Eligible demonstration case."},
                idempotency_key="one", requesting_user_context=registered.json()["context_ref"])
            assert pending["status"] == "pending_confirmation", pending
            assert native.posts == 1 and not h.calls
            Draft7Validator(schema, format_checker=FormatChecker()).validate(native.payload)
        finally:
            if native is not None:
                await native.close()
            else:
                await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())
