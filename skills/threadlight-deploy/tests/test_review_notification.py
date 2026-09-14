"""The optional native email workflow notifies; it never creates approval authority."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def test_notification_template_is_disabled_entra_only_and_has_no_grant_action(tmp_path):
    source = Path(__file__).resolve().parents[1] / "references/governance/review-notification.bicep"
    assert source.is_file()
    supplied = os.environ.get("THREADLIGHT_REVIEW_NOTIFICATION_BICEP")
    output = Path(supplied) if supplied else tmp_path / "notification.json"
    if not supplied:
        compiler = shutil.which("bicep")
        command = [compiler, "build"] if compiler else ["az", "bicep", "build", "--file"]
        result = subprocess.run([*command, str(source), "--outfile", str(output)],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
    template = json.loads(output.read_text())
    resources = template["resources"]
    resources = list(resources.values()) if isinstance(resources, dict) else resources
    workflow = next(r for r in resources if r["type"] == "Microsoft.Logic/workflows")
    assert workflow["properties"]["state"] == "Disabled"
    access = workflow["properties"]["accessControl"]["triggers"]
    assert access["sasAuthenticationPolicy"]["state"] == "Disabled"
    claims = access["openAuthenticationPolicies"]["policies"]["operator"]["claims"]
    assert {claim["name"] for claim in claims} == {"iss", "aud", "oid"}
    actions = workflow["properties"]["definition"]["actions"]
    assert set(actions) == {"Notify_operator"}
    email = actions["Notify_operator"]
    assert email["type"] == "ApiConnection" and email["inputs"]["path"] == "/v2/Mail"
    assert email["inputs"]["body"]["To"] == "[parameters('operatorEmail')]"
    assert "triggerBody" not in email["inputs"]["body"]["To"]
    assert "approvals/resolve" not in source.read_text()
    assert "SecurityControl" not in source.read_text()
    assert "IncludeAuthorizationHeadersInOutputs" not in source.read_text()
    connection = next(r for r in resources if r["type"] == "Microsoft.Web/connections")
    assert connection["kind"] == "V1"
    assert "parameterValues" not in connection["properties"]
