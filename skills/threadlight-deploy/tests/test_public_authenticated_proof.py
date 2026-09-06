"""Explicit public proof networking is not restricted networking or isolation evidence."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_governance_wiring import module, contract, REFERENCES


def network():
    return {
        "posture": "public-authenticated-proof", "proof_only": True, "cleanup_required": True,
        "environment_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/proof/providers/Microsoft.App/managedEnvironments/proof",
    }


@pytest.mark.parametrize("environment", ["staging", "preproduction"])
def test_public_authenticated_proof_is_explicit_nonproduction_only(environment):
    gen = module("generate")
    assert gen.validate_network(network(), environment=environment) == network()


@pytest.mark.parametrize("environment", [None, "development", "production", "prod", ""])
def test_public_authenticated_proof_rejects_other_environments(environment):
    gen = module("generate")
    with pytest.raises(ValueError):
        gen.validate_network(network(), environment=environment)


@pytest.mark.parametrize("field,value", [
    ("proof_only", None), ("proof_only", False), ("proof_only", 1), ("proof_only", "true"),
    ("cleanup_required", None), ("cleanup_required", False), ("cleanup_required", 1),
    ("allowed_ips", []), ("allowed_ips", ["0.0.0.0/0"]), ("vnet_id", "/unverified"),
])
def test_public_authenticated_proof_rejects_missing_opt_in_or_misleading_network_fields(field, value):
    data = network()
    if value is None:
        data.pop(field)
    else:
        data[field] = value
    with pytest.raises(ValueError):
        module("generate").validate_network(data, environment="preproduction")


def test_restricted_public_mode_does_not_become_public_proof_mode():
    gen = module("generate")
    for ranges in ([], ["0.0.0.0/0"]):
        with pytest.raises(ValueError):
            gen.validate_network({"posture": "public-pilot", "allowed_ips": ranges,
                                  "environment_id": network()["environment_id"]})


def test_frozen_proof_configuration_discloses_no_network_isolation():
    gen = module("generate")
    config = {"environment": "preproduction", "network": network()}
    expected = {
        "posture": "public-authenticated-proof", "scope": "runtime-governance-proof-only",
        "network_isolation": "not-established", "cleanup_required": True,
    }
    result = gen.portable_configuration(config, contract(), "github-copilot-sdk")
    assert result["network_evidence"] == expected
    assert "network" not in result
    legacy = gen.portable_configuration(
        {"environment": "preproduction", "network": {"posture": "public-pilot"}},
        contract(), "github-copilot-sdk")
    assert "network_evidence" not in legacy


def test_compiled_proof_network_is_public_but_authentication_remains_mandatory(tmp_path):
    output = Path(os.environ["THREADLIGHT_GOVERNANCE_BICEP"]) if os.environ.get("THREADLIGHT_GOVERNANCE_BICEP") else tmp_path / "compiled.json"
    if not os.environ.get("THREADLIGHT_GOVERNANCE_BICEP"):
        command = [shutil.which("bicep")] if shutil.which("bicep") else ["az", "bicep"]
        result = subprocess.run([*command, "build", "--file", str(REFERENCES / "governance.bicep"),
                                 "--outfile", str(output)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    template = json.loads(output.read_text())
    assert "publicAuthenticatedProof" in template["variables"], "public proof branch is absent"
    resources = template["resources"]
    resources = list(resources.values()) if isinstance(resources, dict) else resources
    storage = next(r for r in resources if r["type"] == "Microsoft.Storage/storageAccounts")
    cosmos = next(r for r in resources if r["type"] == "Microsoft.DocumentDB/databaseAccounts")
    vault = next(r for r in resources if r["type"] == "Microsoft.KeyVault/vaults")
    assert storage["properties"]["allowSharedKeyAccess"] is False
    assert storage["properties"]["allowBlobPublicAccess"] is False
    assert storage["properties"]["supportsHttpsTrafficOnly"] is True
    assert cosmos["properties"]["disableLocalAuth"] is True
    assert vault["properties"]["enableRbacAuthorization"] is True
    for resource in (storage, vault):
        acls = resource["properties"]["networkAcls"]
        assert acls["bypass"] == "None"
        assert "publicAuthenticatedProof" in acls["defaultAction"]
        assert "'Allow'" in acls["defaultAction"] and "'Deny'" in acls["defaultAction"]
    assert "publicAuthenticatedProof" in json.dumps(cosmos["properties"]["copy"])
    proof = template["variables"]["publicAuthenticatedProof"]
    assert "staging" in proof and "preproduction" in proof
    assert "proof_only" in proof and "cleanup_required" in proof
    assert "TL_GOV_SERVICE_INGRESS" in template["outputs"], "fixture needs the same explicit ingress contract"


@pytest.mark.governance_runtime
def test_real_generation_freezes_proof_disclosure_and_rejects_production_transactionally(tmp_path):
    from test_governance_quality import inputs, snapshot
    gen = module("generate")
    project, document, package, deployment, signer = inputs(tmp_path / "proof", environment="preproduction")
    package["network"] = {**network(), "environment_id": package["network"]["environment_id"]}
    deployment["infrastructure"]["network"] = deepcopy(package["network"])
    gen.foundation(project, document, configuration=deployment["infrastructure"])
    gen.generate(project, document, configuration=package)
    frozen = json.loads((project / "src/agent/governance-config.json").read_text())
    assert frozen["network_evidence"]["network_isolation"] == "not-established"
    parameters = json.loads((project / "infra/main.parameters.json").read_text())
    assert parameters["parameters"]["governanceConfig"]["value"]["network"] == package["network"]
    other, doc, invalid, unused, authority = inputs(tmp_path / "production", environment="production")
    invalid["network"] = {**network(), "environment_id": invalid["network"]["environment_id"]}
    before = snapshot(other)
    with pytest.raises(ValueError, match="nonproduction_public_authenticated_proof"):
        gen.generate(other, doc, configuration=invalid)
    assert snapshot(other) == before


def test_public_proof_documentation_never_calls_this_network_isolation():
    readme = (REFERENCES / "README.md").read_text()
    assert "public-authenticated-proof" in readme
    assert "network isolation is not established" in readme
    assert "cleanup_required" in readme
    assert "TL_GOV_SERVICE_INGRESS" in readme
