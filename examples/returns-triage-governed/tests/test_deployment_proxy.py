"""Customer portability never makes the agent's model destination caller-owned."""
import importlib.util
from pathlib import Path

import pytest


EXAMPLE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "returns_proxy_config", EXAMPLE / "src/agent/deployment_config.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def configuration(host="customer-citadel.azure-api.net"):
    return {
        "environment": "preproduction",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "agent_client_id": "22222222-2222-2222-2222-222222222222",
        "agent_id": "returns-triage",
        "control_plane_url": "https://control.example",
        "control_plane_scope": "api://33333333-3333-3333-3333-333333333333/.default",
        "key_id": "https://example.vault.azure.net/keys/policy/" + "a" * 32,
        "approver_roles": ["returns-supervisor"],
        "cosmos_url": "https://example.documents.azure.com",
        "cosmos_database": "returns",
        "cosmos_container": "cases",
        "citadel_apim_host": host,
        "citadel_project_endpoint": f"https://{host}/api/projects/returns",
        "signed_envelope": "policy-envelope.json",
        "policy_id": "returns-write-v1",
        "policy_version": "1",
        "policy_digest": "sha256:" + "a" * 64,
    }


@pytest.mark.parametrize("host", [
    "customer-north.azure-api.net", "customer-south.azure-api.net",
])
def test_existing_customer_apim_can_be_selected_without_source_edits(host):
    settings = module.DeploymentConfiguration.model_validate(configuration(host))
    assert settings.citadel_apim_host == host
    assert settings.citadel_project_endpoint == f"https://{host}/api/projects/returns"


@pytest.mark.parametrize("endpoint", [
    "https://another-hub.azure-api.net/api/projects/returns",
    "https://direct.services.ai.azure.com/api/projects/returns",
    "https://customer-citadel.azure-api.net.evil.example/api/projects/returns",
    "https://customer-citadel.azure-api.net:8443/api/projects/returns",
    "https://user@customer-citadel.azure-api.net/api/projects/returns",
    "https://customer-citadel.azure-api.net/api/projects/../admin",
    "https://customer-citadel.azure-api.net/api/projects/%2e%2e",
    "https://customer-citadel.azure-api.net/api/projects/returns?redirect=elsewhere",
    "https://customer-citadel.azure-api.net/api/projects/returns#fragment",
    "https://customer-citadel.azure-api.net/api/projects/",
    " https://customer-citadel.azure-api.net/api/projects/returns",
    "https://customer-citadel.azure-api.net/api/projects/ret\nurns",
])
def test_proxy_must_match_the_exact_approved_canonical_authority(endpoint):
    data = configuration()
    data["citadel_project_endpoint"] = endpoint
    with pytest.raises(ValueError, match="existing_citadel_project_proxy_required"):
        module.DeploymentConfiguration.model_validate(data)


def test_approved_apim_authority_is_required_not_inferred_from_the_destination():
    data = configuration()
    del data["citadel_apim_host"]
    with pytest.raises(ValueError, match="citadel_apim_host"):
        module.DeploymentConfiguration.model_validate(data)


def test_portability_documentation_keeps_the_customer_acceptance_boundary():
    guide = (EXAMPLE / "README.md").read_text()
    assert "citadel_apim_host" in guide
    assert "configuration validation is not proof of that route" in guide
