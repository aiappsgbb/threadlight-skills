"""Inspect compiled ARM: gateway MAF must not receive native-probe database permissions."""
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.governance_runtime


def test_native_probe_permissions_require_an_actual_native_binding():
    compiled = Path(os.environ.get(
        "THREADLIGHT_GOVERNANCE_BICEP", ROOT / ".governance-validation/mcp-governance-bicep.json"))
    assert compiled.is_file(), "Compile governance.bicep into .governance-validation/mcp-governance-bicep.json"
    template = json.loads(compiled.read_text())
    resources = template["resources"]
    if isinstance(resources, dict):
        resources = resources.values()
    grants = [
        resource for resource in resources
        if resource["type"] == "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments"
        and "probe-native" in resource["name"]
    ]
    assert len(grants) == 2
    for grant in grants:
        condition = grant["condition"]
        assert "equals(parameters('phase'), 'services')" in condition
        assert "equals(parameters('config').runtime, 'microsoft-agent-framework')" in condition
        assert "contains(parameters('bindings'), 'native_probe_config')" in condition
        assert grant["properties"]["principalId"] == "[parameters('bindings').agent_principal]"


def test_confirmation_store_is_opt_in_ephemeral_and_control_plane_only():
    compiled = Path(os.environ.get(
        "THREADLIGHT_GOVERNANCE_BICEP", ROOT / ".governance-validation/mcp-governance-bicep.json"))
    template = json.loads(compiled.read_text())
    resources = template["resources"]
    resources = list(resources.values()) if isinstance(resources, dict) else resources
    ephemeral = [r for r in resources
                 if r["type"] == "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers"
                 and r["properties"]["resource"].get("defaultTtl") == 3600]
    assert len(ephemeral) == 1
    assert ephemeral[0]["condition"] == "[variables('confirmationEnabled')]"
    assert ephemeral[0]["properties"]["resource"]["partitionKey"]["paths"] == ["/scope"]
    grants = [r for r in resources
              if r["type"] == "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments"
              and "confirmationContainer" in json.dumps(r["properties"])]
    assert len(grants) == 1
    assert grants[0]["condition"] == "[variables('confirmationEnabled')]"
    assert "format('{0}-control', variables('prefix'))" in grants[0]["properties"]["principalId"]
