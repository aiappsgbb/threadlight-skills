"""Generated deployment contracts; native probes run in the published-pin Linux gate."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import os

import pytest

ROOT = Path(__file__).resolve().parents[3]
REFERENCES = ROOT / "skills/threadlight-deploy/references/governance"


def module(name):
    path = REFERENCES / f"{name}.py"
    assert path.exists(), f"missing runnable governance reference: {path.name}"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def contract(framework="github-copilot-sdk", *, off=False):
    return {
        "framework": framework,
        "governance": {
            "mode": "off" if off else "selective",
            "environment_modes": {
                "development": "evaluate_only", "staging": "evaluate_only",
                "preproduction": "enforce", "production": "enforce",
            },
            "lifecycle_bindings": [],
        },
        "tools": [
            {"id": "act", "consequence": "write", "policy_binding": "none" if off else "safe",
             "enforcement_path": "none" if off else (
                 "local-agent-hooks" if framework == "microsoft-agent-framework"
                 else "governed-tool-gateway"),
             "intervention_points": [] if off else ["pre_tool_call"],
             "safe_principles": [] if off else ["Safety"], "requires": []},
            {"id": "read", "consequence": "read", "policy_binding": "none",
             "enforcement_path": "none", "intervention_points": [],
             "safe_principles": [], "requires": []},
        ],
    }


def test_generation_off_is_byte_for_byte_noop(tmp_path):
    project = tmp_path / "pilot"
    project.mkdir()
    (project / "azure.yaml").write_bytes(b"name: keep\n# retain formatting\n")
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert module("generate").generate(project, contract(off=True)) == {"status": "off"}
    assert before == {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}


def test_selected_generation_requires_real_inputs_and_never_partial_writes(tmp_path):
    (tmp_path / "azure.yaml").write_text("name: existing\n")
    before = list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="configuration_required"):
        module("generate").generate(tmp_path, contract())
    assert list(tmp_path.iterdir()) == before


def test_ghcp_rejects_local_hooks_without_runtime_replacement():
    document = contract()
    document["tools"][0]["enforcement_path"] = "local-agent-hooks"
    with pytest.raises(ValueError):
        module("generate").validate_contract(document)
    assert document["framework"] == "github-copilot-sdk"


def test_ghcp_splits_bound_subset_and_preserves_unbound_auth_tools():
    servers = {
        "mixed": {"type": "http", "url": "https://original.example/mcp",
                  "tools": ["act", "read"], "headers": {"X-Original": "retained"}, "timeout": 4000},
        "lookup": {"type": "http", "url": "https://lookup.example/mcp",
                   "tools": ["lookup"], "headers": {"X-Other": "retained"}},
    }
    before = copy.deepcopy(servers)
    result = module("ghcp-container").route_mcp_servers(
        servers, contract(), {"act": {"server": "mixed", "tool": "act"}},
        "https://gateway.example/mcp")
    assert result["lookup"] == before["lookup"]
    assert result["mixed"] == {**before["mixed"], "tools": ["read"]}
    assert result["threadlight-governed"]["url"] == "https://gateway.example/mcp"
    assert result["threadlight-governed"]["tools"] == ["act"]
    assert "headers" not in result["threadlight-governed"]
    assert servers == before


def test_ghcp_wildcard_mixed_server_is_not_silently_exposed():
    servers = {"mixed": {"type": "http", "url": "https://original.example/mcp", "tools": ["*"]}}
    with pytest.raises(ValueError, match="explicit_tool_inventory"):
        module("ghcp-container").route_mcp_servers(
            servers, contract(), {"act": {"server": "mixed", "tool": "act"}},
            "https://gateway.example/mcp")


def test_governance_dependencies_match_shared_pins():
    path = REFERENCES / "pyproject-maf.toml"
    assert path.is_file(), "missing exact dependency template"
    project = tomllib.loads(path.read_text())
    deps = set(project["project"]["dependencies"])
    pins = json.loads((ROOT / "skills/_shared/governance-upstream-pin.json").read_text())
    for key in ("agt", "acs", "agent_hooks"):
        assert f"{pins[key]['distribution']}=={pins[key]['version']}" in deps
    for name, version in pins["maf"].items():
        assert f"{name}=={version}" in deps
    assert "threadlight-govern-control-plane==0.1.0" in deps


def test_reference_cli_is_executable_without_catalog_import_path():
    result = subprocess.run([sys.executable, str(REFERENCES / "generate.py"), "--help"],
                            cwd=REFERENCES.parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "generate" in result.stdout and "bind" in result.stdout


def test_ghcp_no_direct_bound_alias_on_another_server():
    servers = {
        "mixed": {"type": "http", "url": "https://original.example/mcp", "tools": ["act", "read"]},
        "alias": {"type": "http", "url": "https://original.example/mcp", "tools": ["act"]},
    }
    with pytest.raises(ValueError, match="bound_tool_alias"):
        module("ghcp-container").route_mcp_servers(
            servers, contract(), {"act": {"server": "mixed", "tool": "act"}},
            "https://gateway.example/mcp")


def test_private_generation_requires_network_dependency_not_boolean_claim():
    with pytest.raises(ValueError, match="private_network_dependencies"):
        module("generate").validate_network({"posture": "private-required", "validated": True})


def test_private_binding_requires_dns_vnet_link_observations():
    network = {
        "posture": "private-required", "environment_id": "/environment",
        "vnet_id": "/vnet", "foundry_injection_subnet_id": "/vnet/subnets/agents",
        "blob_dns_zone_id": "/dns/blob", "cosmos_dns_zone_id": "/dns/cosmos",
        "keyvault_dns_zone_id": "/dns/vault",
    }
    observations = {
        "environment": {"id": "/environment", "properties": {
            "defaultDomain": "internal.example", "vnetConfiguration": {
                "internal": True, "infrastructureSubnetId": "/vnet/subnets/aca"}}},
        "control_plane_url": "https://fixture-control.internal.example",
        "gateway_url": "https://fixture-gateway.internal.example/mcp",
        "foundry_agent_subnet_id": "/vnet/subnets/agents",
    }
    with pytest.raises(ValueError, match="private_dns"):
        module("generate").verify_observations({"network": network, "prefix": "fixture"}, {}, observations)


def test_public_pilot_requires_explicit_ip_allowlist():
    for ranges in ([], ["0.0.0.0/0"], ["::/0"], ["0.0.0.0/32"], ["::1/128"], ["203.0.113.2/31"]):
        with pytest.raises(ValueError):
            module("generate").validate_network({
                "posture": "public-pilot", "allowed_ips": ranges,
                "environment_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/y/providers/Microsoft.App/managedEnvironments/z",
            })


def test_ghcp_pre_mcp_bridge_refreshes_gateway_not_model_token():
    import asyncio
    import httpx
    from types import SimpleNamespace
    requests, scopes = [], []
    class Credential:
        async def get_token(self, scope):
            scopes.append(scope)
            return SimpleNamespace(token=f"gateway-token-{len(scopes)}")
    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    async def run():
        ghcp = module("ghcp-container")
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            relay = ghcp.McpRelay(
                gateway_url="https://gateway.example/mcp",
                scope="api://22222222-2222-2222-2222-222222222222/.default",
                credential=Credential(), invocation_id="invocation-1", tools=["act"], http=http)
            original = {"serverName": "lookup", "toolName": "read", "toolCallId": "read-1"}
            assert await relay.pre_mcp(original, {}) is None
            hook = await relay.pre_mcp({
                "serverName": "threadlight-governed", "toolName": "act",
                "toolCallId": "call-1", "sessionId": "session-1", "arguments": {"amount": 10},
            }, {})
            assert set(hook) == {"metaToUse"}, "SDK hook cannot set HTTP headers"
            app = relay.app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://localhost") as client:
                headers = {"X-Threadlight-Relay": relay.secret}
                assert (await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "method": "initialize", "id": 1})).status_code == 200
                call = {"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                        "params": {"name": "act", "arguments": {"amount": 10},
                                   "_meta": hook["metaToUse"]}}
                assert (await client.post("/mcp", headers=headers, json=call)).status_code == 200
                assert (await client.post("/mcp", headers=headers, json=call)).status_code == 200
                call["params"]["arguments"]["amount"] = 11
                assert (await client.post("/mcp", headers=headers, json=call)).status_code == 403
        assert len(requests) == 3
        assert scopes == ["api://22222222-2222-2222-2222-222222222222/.default"] * 3
        assert requests[1].headers["idempotency-key"] == requests[2].headers["idempotency-key"]
        assert requests[1].headers["authorization"] != requests[2].headers["authorization"]
        assert requests[1].url == httpx.URL("https://gateway.example/mcp")
    asyncio.run(run())


def test_bicep_compiles_and_has_separate_scoped_service_identities(tmp_path):
    path = REFERENCES / "governance.bicep"
    assert path.exists(), "missing composed governance infrastructure"
    import shutil
    if os.environ.get("THREADLIGHT_GOVERNANCE_BICEP"):
        output = Path(os.environ["THREADLIGHT_GOVERNANCE_BICEP"])
    else:
        command = [shutil.which("bicep")] if shutil.which("bicep") else ["az", "bicep"]
        output = tmp_path / "compiled.json"
        result = subprocess.run([*command, "build", "--file", str(path), "--outfile", str(output)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    compiled = json.loads(output.read_text())
    resources = compiled["resources"]
    if isinstance(resources, dict):
        resources = resources.values()
    resources = list(resources)
    identities = [r for r in resources if r["type"] == "Microsoft.ManagedIdentity/userAssignedIdentities"]
    assert len(identities) >= 4
    apps = [r for r in resources if r["type"] == "Microsoft.App/containerApps"]
    assert len(apps) == 2
    assert all("condition" in app for app in apps), "foundation must deploy zero placeholder apps"
    source = path.read_text()
    assert "/scope" in source and "defaultTtl" not in source
    assert "privateEndpoints" in source and "privateDnsZoneGroups" in source
    assert "governance-records" in source and "gateway-idempotency" in source
    assert "azd-service-name" in source
    assert "GOV_CONFIG_JSON" in source and "GATEWAY_CONFIG_JSON" in source


def test_ci_runs_exact_pin_generation_gate():
    workflow = (ROOT / ".github/workflows/python-pytest.yml").read_text()
    assert "run-governance-pin-tests.py --deployment" in workflow
    assert "--ignore=skills/threadlight-deploy/tests/test_governance_wiring.py" in workflow
    runner = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    assert "skills/threadlight-deploy/tests/test_governance_wiring.py" in runner
    assert "deployment-proof.json" in runner


def test_bind_rejects_missing_and_tag_only_images_before_writing(tmp_path):
    generator = module("generate")
    for images in ({}, {"agent": "registry.azurecr.io/agent:latest"}):
        with pytest.raises(ValueError, match="deployment_images"):
            generator.validate_images(images)
    assert list(tmp_path.iterdir()) == []


def test_binding_validates_trust_before_emitting_deployable_config(tmp_path):
    with pytest.raises(ValueError, match="deployment_images"):
        module("generate").bind(tmp_path, contract(), configuration={"images": {}})
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                    reason="requires exact published Linux runtime")
def test_ghcp_native_host_and_actual_hook_schema(monkeypatch):
    from azure.ai.agentserver.invocations import InvocationAgentServerHost
    from copilot import CopilotClient
    from copilot.session import PreMcpToolCallHookOutput
    import inspect
    monkeypatch.setenv("GOVERNED_TOOL_GATEWAY_URL", "https://gateway.example/mcp")
    ghcp = module("ghcp-container")
    host = ghcp.build_host({
        "mcp_servers": {"mixed": {"type": "http", "url": "https://original.example/mcp",
                                  "tools": ["act", "read"]}},
        "mcp_bindings": {"act": {"server": "mixed", "tool": "act"}},
        "contract": contract(), "gateway_scope": "api://test/.default",
    }, configure_observability=None)
    assert isinstance(host, InvocationAgentServerHost)
    assert any(route.path == "/invocations" for route in host.routes)
    assert set(PreMcpToolCallHookOutput.__annotations__) == {"metaToUse"}
    assert {"provider", "hooks", "mcp_servers", "enable_config_discovery"} <= set(
        inspect.signature(CopilotClient.create_session).parameters)


def test_dependency_merge_preserves_application_extras_and_configuration():
    original = ('[project]\nname="app"\nversion="1"\ndependencies = ["httpx==0.28.1", '
                '"agent-framework-core==1.3.0"]\n[tool.application]\nkeep=true\n')
    result = module("generate").merge_dependencies(original, (REFERENCES / "pyproject-maf.toml").read_text())
    parsed = tomllib.loads(result)
    assert parsed["tool"]["application"]["keep"] is True
    assert "httpx==0.28.1" in parsed["project"]["dependencies"]
    assert "agent-framework-core==1.3.0" not in parsed["project"]["dependencies"]
    assert parsed["tool"]["setuptools"]["packages"] == [], "vendored namespaces must not trigger flat discovery"


def deployment_fixture(configuration):
    def uid(number):
        return f"{number:08x}-1111-1111-1111-111111111111"
    policy = configuration["policy_digest"]
    infrastructure = {
        "prefix": "fixture", "storage_name": "fixturestorage", "cosmos_name": "fixturecosmos",
        "vault_name": "testvault", "acr_id": (
            f"/subscriptions/{uid(1)}/resourceGroups/fixture/providers/Microsoft.ContainerRegistry/registries/fixture"),
        "acr_authorization": "rbac", "tenant_id": configuration["tenant_id"],
        "control_plane_app_id": configuration["control_plane_scope"][6:-9],
        "gateway_app_id": configuration["gateway_scope"][6:-9],
        "network": configuration["network"], "agent_id": "test-agent",
        "runtime": "microsoft-agent-framework", "enable_gateway": False,
        "human_clients": [uid(6)], "approver_subjects": [uid(7)], "auditor_subjects": [],
        "approver_roles": ["Approver"],
    }
    bindings = {
        "key_id": configuration["key_id"], "agent_principal": uid(2), "agent_client_id": uid(3),
        "agent_version": "1", "gateway_principal": uid(4), "gateway_client": uid(5),
        "downstream_principal": uid(8), "downstream_client": uid(9),
        "control_principal": uid(10), "control_client": uid(11),
        "policy_id": "safe", "policy_version": "1", "policy_digest": policy,
        "allowed_endpoints": ["https://downstream.example/act", "https://downstream.example/outcome"],
        "control_workloads": {
            uid(2): {"client_id": uid(3), "agent_id": "test-agent", "policies": ["safe"]},
            uid(4): {"client_id": uid(5), "agent_id": "test-agent", "policies": ["safe"]}},
        "gateway_workloads": {uid(2): {"client_id": uid(3), "agent_id": "test-agent", "policies": ["safe"]}},
    }
    applications, assignments = [], []
    for i, key in enumerate(("control_plane", "gateway")):
        app_id, service_id, role_id = infrastructure[key + "_app_id"], uid(20 + i), uid(30 + i)
        applications.append({
            "appId": app_id, "servicePrincipalId": service_id, "identifierUris": ["api://" + app_id],
            "api": {"requestedAccessTokenVersion": 2},
            "appRoles": [{"id": role_id, "value": "Governance.Workload", "isEnabled": True,
                          "allowedMemberTypes": ["Application"]}],
        })
        for principal in ([uid(2), uid(4)] if key == "control_plane" else [uid(2)]):
            assignments.append({"principalId": principal, "resourceId": service_id, "appRoleId": role_id})
    return {
        "images": {key: f"fixture.azurecr.io/{key}@sha256:" + char * 64
                   for key, char in (("agent", "a"), ("control_plane", "b"), ("gateway", "c"))},
        "infrastructure": infrastructure, "bindings": bindings,
        "spool_directory": "/mnt/governance-audit",
        "observations": {
            "environment": {"id": infrastructure["network"]["environment_id"],
                            "properties": {"defaultDomain": "fixture.azurecontainerapps.io"}},
            "foundation": {**bindings, "blob_url": "https://fixturestorage.blob.core.windows.net",
                           "cosmos_url": "https://fixturecosmos.documents.azure.com:443/"},
            "applications": applications, "app_role_assignments": assignments,
            "downstream_authorizations": [{"principal_id": uid(8), "scope": "api://downstream/.default",
                                          "role_id": uid(40)}],
            "control_plane_url": "https://fixture-control.fixture.azurecontainerapps.io",
            "gateway_url": "https://fixture-gateway.fixture.azurecontainerapps.io/mcp",
        },
    }


@pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                    reason="requires exact published Linux runtime")
def test_generated_maf_native_constructor_host_and_failed_signature(tmp_path):
    """Actual Agent + native hook bundle + actual host, never an AST proxy."""
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_runtime_provider import build_policy, contract as maf_contract
    built = build_policy(tmp_path / "policy")
    project = tmp_path / "external-pilot"
    (project / "src/agent").mkdir(parents=True)
    (project / "src/agent/governance_application.py").write_text(
        "from agent_framework import tool\n"
        "effects = []\nmiddleware = []\n"
        "@tool(approval_mode='never_require')\n"
        "def act() -> str:\n    '''Change a record.'''\n    effects.append('act')\n    return 'changed'\n"
        "@tool(approval_mode='never_require')\n"
        "def read() -> str:\n    '''Read a record.'''\n    effects.append('read')\n    return 'read-ok'\n"
        "tools = [act, read]\n"
        "def safe_evidence(identity):\n    return {'principal': identity['principal']}\n")
    (project / "src/agent/copilot-instructions.md").write_text("Use approved tools only.")
    (project / "src/agent/skills/domain").mkdir(parents=True)
    (project / "src/agent/skills/domain/SKILL.md").write_text(
        "---\nname: domain\ndescription: Use for domain tasks\n---\nUse approved tools.\n")
    (project / "azure.yaml").write_text(
        "name: pilot\nservices:\n  agent:\n    host: azure.ai.agent\n"
        "    project: ./src/agent\n  existing:\n    host: containerapp\n"
        "hooks:\n  postdeploy:\n    run: echo preserve\n")
    legacy = "kind: hosted\nenvironment_variables:\n  - name: KEEP\n    value: keep\n"
    (project / "agent.yaml").write_text(legacy)
    (project / "src/agent/agent.yaml").write_text(legacy)
    configuration = {
        "agent_service": "agent", "agent_id": "test-agent",
        "policy_id": "safe", "policy_version": "1", "bundle_path": str(built.root),
        "policy_digest": built.bundle_digest,
        "signed_envelope": str(tmp_path / "signed.json"),
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "key_id": "https://testvault.vault.azure.net/keys/policy/0123456789abcdef0123456789abcdef",
        "control_plane_scope": "api://22222222-2222-2222-2222-222222222222/.default",
        "gateway_scope": "api://33333333-3333-3333-3333-333333333333/.default",
        "approver_roles": ["Approver"], "network": {
            "posture": "public-pilot", "allowed_ips": ["192.0.2.10/32"],
            "environment_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/fixture/providers/Microsoft.App/managedEnvironments/test",
        },
        "control_plane_url": "https://fixture-control.fixture.azurecontainerapps.io",
        "gateway_url": "https://fixture-gateway.fixture.azurecontainerapps.io/mcp",
    }
    from datetime import datetime, timedelta, timezone
    import base64
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
    # Test-only external authority; no production signing keys are generated.
    cp_models = __import__("govern_control_plane.models", fromlist=["BundleEnvelope"])
    envelope = cp_models.BundleEnvelope(
        policy_id="safe", version="1", content_digest=built.bundle_digest,
        tenant_id=configuration["tenant_id"], key_id=configuration["key_id"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization
    public_path = tmp_path / "authority-public.pem"
    public_path.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    signature = private.sign(cp_models.canonical(envelope), padding.PKCS1v15(), hashes.SHA256())
    signed = cp_models.SignedBundle(envelope=envelope, signature=base64.b64encode(signature).decode())
    Path(configuration["signed_envelope"]).write_bytes(cp_models.canonical(signed))
    result = module("generate").generate(project, maf_contract(), configuration=configuration)
    assert result["status"] == "packaged-not-deployed"
    agent = project / "src/agent"
    assert (agent / "policy/bundle-metadata.json").read_bytes() == (
        built.root / "bundle-metadata.json").read_bytes()
    assert (agent / "policy-envelope.json").read_bytes() == Path(
        configuration["signed_envelope"]).read_bytes()
    assert not (agent / "deployment.json").exists(), "image digest must not feed its own image hash"
    assert (agent / "skills/domain/SKILL.md").is_file()
    import yaml
    azure = yaml.safe_load((project / "azure.yaml").read_text())
    assert azure["services"]["existing"] == {"host": "containerapp"}
    assert azure["hooks"]["postdeploy"]["run"] == "echo preserve"
    assert azure["services"]["govern-control-plane"]["project"] == "./src/govern-control-plane"
    assert azure["services"]["govern-gateway"]["project"] == "./src/govern-gateway"
    assert "governance.bicep" in (project / "infra/main.bicep").read_text()
    assert (agent / "Dockerfile").is_file()
    dockerfile = (agent / "Dockerfile").read_text()
    assert dockerfile.index("COPY . ./") < dockerfile.index("RUN python -m pip install --no-cache-dir .\n")
    for service in ("govern-control-plane", "govern-gateway"):
        source = project / "src" / service
        assert (source / "Dockerfile").is_file()
        assert (source / "vendor/control-plane/client.py").is_file()
        assert (source / "service_entry.py").is_file()
        wheels = tmp_path / service / "wheels"
        wheels.mkdir(parents=True)
        projects = [source / "vendor/control-plane"]
        if service == "govern-gateway":
            projects.append(source / "vendor/gateway")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation",
             "--wheel-dir", str(wheels), *(str(path) for path in projects)], cwd=source,
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    # This is a fresh process, cwd outside the catalog import root; only generated source imports.
    probe = r'''
import asyncio, importlib.util, json, os
from pathlib import Path
from unittest.mock import patch
from agent_framework import MiddlewareBundle
from agent_framework.foundry import FoundryChatClient
from govern_control_plane.storage import KeyVaultSigner
from runtime import create_governed_agent
import container
import sys
import httpx
from types import SimpleNamespace
from openai import AsyncOpenAI
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils
from azure.keyvault.keys.crypto import SignatureAlgorithm
import governance_application as application

async def main():
    config = json.loads(Path("governance-config.json").read_text())
    config.update(principal="44444444-4444-4444-4444-444444444444",
                  agent_version="1", image_digest="sha256:" + "d" * 64,
                  control_plane_url="https://control.example", spool_dir="audit")
    class Signer:
        async def health(self): pass
        async def verify(self, digest, signature): return False
    provider = await container.build_provider(config, signer=Signer(), credential=object())
    assert provider.health()["bindings"]["act:pre_tool_call"]["healthy"] is False
    client = FoundryChatClient(project_endpoint="https://test.services.ai.azure.com/api/projects/test",
                               model="test", credential=object())
    host = container.build_host(provider, client=client, configure_observability=None)
    assert provider._claimed
    assert host._agent is not None
    assert host._agent.id == config["agent_id"]
    health = container.readiness(provider)
    assert health.status_code == 503
    assert "policy_unavailable" in health.body.decode()
    # A failed signature must not construct a second, unguarded agent.
    assert host._agent.run.__wrapped__
    public = serialization.load_pem_public_key(Path(sys.argv[1]).read_bytes())
    class Crypto:
        key_id = config["key_id"]
        async def verify(self, algorithm, digest, signature):
            assert algorithm == SignatureAlgorithm.rs256
            try:
                public.verify(signature, digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
                return SimpleNamespace(is_valid=True)
            except Exception:
                return SimpleNamespace(is_valid=False)
    class Keys:
        async def get_key(self, name, version):
            assert f"/keys/{name}/{version}" in config["key_id"]
            return SimpleNamespace(id=config["key_id"], properties=SimpleNamespace(
                enabled=True, expires_on=None, not_before=None))
    signed_provider = await container.build_provider(
        config, signer=KeyVaultSigner(Crypto(), key_client=Keys()), credential=object())
    assert container.readiness(signed_provider).status_code == 200
    assert isinstance(signed_provider.audit, container.DurableSpool)
    assert isinstance(signed_provider.approval_resolver, container.ApprovalClient)
    audit_config = json.loads(json.dumps(config))
    audit_config["spool_dir"] = "required-host-mount"
    audit_config["contract"]["tools"][0]["requires"] = ["durable-audit"]
    audit_provider = await container.build_provider(
        audit_config, signer=KeyVaultSigner(Crypto(), key_client=Keys()), credential=object())
    assert (await container.dependency_readiness(audit_provider)).status_code == 503
    try:
        audit_provider.audit.append(correlation_id="test", decision="allow",
                                    action_hash=config["policy_digest"], policy_hash=config["policy_digest"])
    except OSError:
        pass
    else:
        raise AssertionError("must not manufacture an ephemeral substitute for a required audit mount")
    assert not Path("required-host-mount").exists()
    Path("required-host-mount").mkdir(mode=0o700)
    assert (await container.dependency_readiness(audit_provider)).status_code == 200
    assert list(Path("required-host-mount").iterdir()) == []
    original_envelope = Path("policy-envelope.json").read_bytes()
    tampered = json.loads(original_envelope)
    tampered["signature"] = ("A" if tampered["signature"][0] != "A" else "B") + tampered["signature"][1:]
    Path("policy-envelope.json").write_text(json.dumps(tampered))
    invalid = await container.build_provider(
        config, signer=KeyVaultSigner(Crypto(), key_client=Keys()), credential=object())
    assert container.readiness(invalid).status_code == 503
    Path("policy-envelope.json").unlink()
    unsigned = await container.build_provider(
        config, signer=KeyVaultSigner(Crypto(), key_client=Keys()), credential=object())
    assert container.readiness(unsigned).status_code == 503
    Path("policy-envelope.json").write_bytes(original_envelope)
    import base64
    from urllib.parse import parse_qs
    payload = base64.urlsafe_b64encode(json.dumps({
        "oid": config["principal"], "tid": config["tenant_id"]}).encode()).decode().rstrip("=")
    class Credential:
        async def get_token(self, scope):
            assert scope == config["control_plane_scope"]
            return SimpleNamespace(token="header." + payload + ".synthetic")
    observed_contexts = []
    def context_health(request):
        context = json.loads(parse_qs(request.url.query.decode())["approval_context"][0])
        observed_contexts.append(context)
        assert context["principal"] == config["principal"]
        return httpx.Response(200, json={
            "status": "healthy", "authenticated": True, "approval_context_validated": True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(context_health)) as http:
        assert await container.resolve_identity(config, Credential(), http=http) == config["principal"]
    assert observed_contexts[0]["agent_id"] == config["agent_id"]
    for provider, name in ((provider, "read"), (signed_provider, "act")):
        application.effects.clear()
        calls = []
        def exchange(request):
            calls.append(json.loads(request.content))
            output = ([{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                        "name": name, "arguments": "{}", "status": "completed"}]
                      if len(calls) == 1 else [{"type": "message", "id": "msg_1",
                        "role": "assistant", "status": "completed", "content": [{
                            "type": "output_text", "text": "done", "annotations": []}]}])
            return httpx.Response(200, json={"id": "resp_1", "object": "response",
                "created_at": 0, "status": "completed", "model": "test", "output": output})
        wire = httpx.AsyncClient(transport=httpx.MockTransport(exchange))
        model = FoundryChatClient(project_endpoint="https://test.services.ai.azure.com/api/projects/test",
                                  model="test", credential=object())
        model.client = AsyncOpenAI(base_url="https://model.invalid/v1/", api_key="synthetic-test", http_client=wire)
        if provider._claimed:
            # Each actual agent requires its own provider, including the failed-signature case.
            provider = await container.build_provider(config, signer=Signer(), credential=object())
        real_host = container.build_host(provider, client=model, configure_observability=None)
        await real_host._agent.run("Use the requested tool.")
        assert all(call["store"] is False for call in calls)
        if name == "read":
            assert application.effects == ["read"]
        else:
            assert application.effects == []
            assert "threadlight:policy_deny" in json.dumps(calls[-1])
        await wire.aclose()
        await provider.approval_resolver.aclose()

asyncio.run(main())
'''
    completed = subprocess.run([sys.executable, "-c", probe, str(public_path)], cwd=agent,
                               capture_output=True, text=True,
                               env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    assert completed.returncode == 0, completed.stderr
    before = module("generate").tree_digest(agent)
    deployment = deployment_fixture(configuration)
    module("generate").agent_image(project, maf_contract(), configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": deployment["spool_directory"]})
    bootstrapped = (project / "azure.yaml").read_bytes()
    bound = module("generate").bind(project, maf_contract(), configuration=deployment)
    assert bound["status"] == "bound-unverified"
    assert module("generate").tree_digest(agent) == before, "binding must not change agent image source"
    assert (project / "azure.yaml").read_bytes() == bootstrapped, (
        "rebinding the hosted definition would create a new instance and an identity cycle")
    assert yaml.safe_load((project / "azure.yaml").read_text())["services"]["agent"]["image"] == (
        deployment["images"]["agent"])
    parameters = json.loads((project / "infra/main.parameters.json").read_text())["parameters"]
    assert parameters["governancePhase"]["value"] == "services"
    assert parameters["governanceBindings"]["value"]["control_config"]["cosmos_container"] == "governance-records"
    assert parameters["governanceBindings"]["value"]["gateway_config"]["cosmos_container"] == "gateway-idempotency"
    assert (project / "agent.yaml").read_bytes() == (agent / "agent.yaml").read_bytes()
    assert {"name": "KEEP", "value": "keep"} in yaml.safe_load((agent / "agent.yaml").read_text())["environment_variables"]
