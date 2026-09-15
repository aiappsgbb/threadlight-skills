"""Native Outlook decisions remain distinct from control-plane approval grants."""
import json
from pathlib import Path
import shutil
import subprocess


def test_native_outlook_approval_uses_existing_connection_and_waits_for_choice(tmp_path):
    source = Path(__file__).resolve().parents[1] / "references/governance/review-approval.bicep"
    assert source.is_file(), "Provide native Outlook approval, not a notification or terminal handoff"
    compiler = shutil.which("bicep")
    command = [compiler, "build"] if compiler else ["az", "bicep", "build", "--file"]
    output = tmp_path / "approval.json"
    result = subprocess.run([*command, str(source), "--outfile", str(output)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    template = json.loads(output.read_text())
    resources = template["resources"]
    resources = list(resources.values()) if isinstance(resources, dict) else resources
    assert len(resources) == 1
    workflow = resources[0]
    assert workflow["type"] == "Microsoft.Logic/workflows"
    assert "identity" not in workflow
    assert template["parameters"]["workflowState"]["defaultValue"] == "Disabled"
    properties = workflow["properties"]
    access = properties["accessControl"]["triggers"]
    assert access["sasAuthenticationPolicy"]["state"] == "Disabled"
    policies = access["openAuthenticationPolicies"]["policies"]
    assert set(policies) == {"control_plane"}
    claims = policies["control_plane"]["claims"]
    assert {c["name"] for c in claims} == {"iss", "aud", "oid"}
    assert next(c["value"] for c in claims if c["name"] == "oid") == "[parameters('controlPlaneObjectId')]"
    definition = properties["definition"]
    trigger = definition["triggers"]["Review_notification_requested"]
    assert trigger["inputs"]["schema"]["additionalProperties"] is False
    assert {"operation_id", "action_hash", "case_id", "proposed_arguments", "expires_at",
            "approval_intent", "review_context"} == set(
        trigger["inputs"]["schema"]["required"])
    assert trigger["inputs"]["schema"]["properties"]["review_context"]["properties"][
        "policy"] == {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"}
    gate = definition["actions"]["Require_fresh_pending"]
    assert gate["type"] == "If" and "ticks(utcNow())" in gate["expression"]
    assert gate["else"]["actions"]["Reject_expired_request"]["inputs"]["runStatus"] == "Failed"
    email = gate["actions"]["Send_approval_email"]
    assert email["type"] == "ApiConnectionWebhook"
    assert email["inputs"]["path"] == "/approvalmail/$subscriptions"
    assert email["inputs"]["body"]["NotificationUrl"] == "@listCallbackUrl()"
    message = email["inputs"]["body"]["Message"]
    assert message["Options"] == "Approve,Reject"
    assert message["SelectionText"] == "Do you authorize this request?"
    assert message["To"] == "[parameters('operatorEmail')]"
    assert message["UseOnlyHTMLMessage"] is False
    assert message["ShowHTMLConfirmationDialog"] is True
    assert email["limit"]["timeout"] == "PT15M"
    assert email["runtimeConfiguration"]["secureData"]["properties"] == ["inputs", "outputs"]
    decision = definition["outputs"]["outlook_decision"]["value"]
    assert decision["response"] == "@body('Send_approval_email')"
    assert decision["request"] == "@triggerBody()"
    assert decision["operation_id"] == "@triggerBody()['operation_id']"
    assert decision["action_hash"] == "@triggerBody()['action_hash']"
    assert decision["control_plane_grant_created"] is False
    assert decision["business_effect_executed"] is False
    text = source.read_text()
    for prohibited in ("approvals/resolve", "/v2/Mail", "Task8 review client", "roleAssignments"):
        assert prohibited not in text


def test_native_approval_email_is_readable_without_raw_protocol_payloads():
    source = Path(__file__).resolve().parents[1] / "references/governance/review-approval.bicep"
    text = source.read_text()
    body = next(line for line in text.splitlines() if line.strip().startswith("Body:"))
    assert "string(triggerBody()" not in body
    assert "Hash azione" not in body and "Operazione:" not in body
    assert "Case:" in body and "Proposed action:" in body and "Reason:" in body
    assert "decision_labels" in body
    assert "convertTimeZone" in body and "review_timezone" in body
    assert "Supervisor handoff" in text
    assert "No payment" in body


def test_native_approval_email_escapes_untrusted_reason_markup():
    source = Path(__file__).resolve().parents[1] / "references/governance/review-approval.bicep"
    body = next(line for line in source.read_text().splitlines() if line.strip().startswith("Body:"))
    assert "replace(replace(replace(triggerBody()" in body
    assert all(entity in body for entity in ("&amp;", "&lt;", "&gt;"))
