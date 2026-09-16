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
