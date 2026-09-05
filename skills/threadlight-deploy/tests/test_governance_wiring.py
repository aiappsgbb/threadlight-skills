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


@pytest.mark.governance_runtime
def test_generated_native_probe_package_is_explicit_and_requires_external_signed_binding(tmp_path):
    from test_governance_quality import inputs
    from test_control_plane import HUMAN, APP, WORKLOAD, TENANT
    project, doc, config, deployment, signer = inputs(tmp_path, environment="preproduction")
    doc["tools"][0]["id"] = "governance_probe_noop"
    doc["tools"][0]["requires"] = ["durable-audit"]
    config.update(subscription=TENANT, resource_group="fixture", probe_observability={
        "enabled": True, "configuration_file": "/mnt/governance-probe/config.json"})
    deployment["infrastructure"]["probe_observability"] = config["probe_observability"]
    deployment["bindings"]["probe_controllers"] = {
        HUMAN: {"client_id": APP, "subjects": [deployment["bindings"]["agent_principal"]],
                "actions": ["governance_probe_noop"]}}
    gen = module("generate")
    gen.generate(project, doc, configuration=config)
    agent = project / "src/agent"
    assert (agent / "vendor/gateway/probe_runtime.py").exists()
    assert (agent / "runtime/probe_telemetry.py").exists()
    assert "./vendor/gateway" in (agent / "Dockerfile").read_text()
    assert not (project / "src/govern-probe-fixture").exists(), "operator must explicitly install the fixture"
    gen.agent_image(project, doc, configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
    with pytest.raises(ValueError, match="native_probe_binding_required"):
        gen.bind(project, doc, configuration=deployment)
    import asyncio
    import base64
    from datetime import datetime, timedelta, timezone
    from test_probe_telemetry import probe_registry
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    reg = probe_registry()
    reg["gateway_url"] = config["gateway_url"]
    reg["native_policy_digest"] = config["policy_digest"]
    reg["deployment"].update(agent_id=config["agent_id"], resource_group="fixture")
    reg["actions"][0]["workloads"] = [deployment["bindings"]["agent_principal"]]
    source = tmp_path / "probe-source"
    source.mkdir()
    for name in ("manifest.yaml", "safe.rego"):
        (source / name).write_bytes((Path(config["bundle_path"]) / name).read_bytes())
    (source / "gateway-registry.json").write_text(json.dumps(reg))
    probe_bundle = bundle_module().build_bundle(
        source=source, destination=tmp_path / "probe-bundle", policy_id="safe-probe", version="1")
    envelope = BundleEnvelope(policy_id="safe-probe", version="1", content_digest=probe_bundle.bundle_digest,
        tenant_id=TENANT, key_id=config["key_id"], expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    signed_path = tmp_path / "probe-signed.json"
    signed_path.write_bytes(canonical(signed))
    b = deployment["bindings"]
    probe_config = {
        "tenant_id": TENANT, "audience": APP, "key_id": config["key_id"],
        "workloads": {b["agent_principal"]: b["gateway_workloads"][b["agent_principal"]]},
        "human_clients": [APP], "approver_subjects": [HUMAN], "approver_roles": ["Approver"],
        "probe_controllers": b["probe_controllers"], "enabled": True, "producer": "native",
        "service_client_id": b["agent_client_id"], "downstream_client_id": b["downstream_client"],
        "cosmos_url": deployment["observations"]["foundation"]["cosmos_url"],
        "cosmos_database": "governance", "cosmos_container": "probe-native",
        "bundle_path": "/mnt/governance-probe/policy",
        "signed_envelope_path": "/mnt/governance-probe/envelope.json",
        "policy_id": "safe-probe", "policy_version": "1", "policy_digest": probe_bundle.bundle_digest,
        "gateway_url": config["gateway_url"], "expected_deployment": reg["deployment"],
        "allowed_endpoints": [reg["actions"][0]["endpoint"], reg["actions"][0]["outcome_endpoint"]],
    }
    deployment.update(probe_runtime_configuration=probe_config, probe_bundle=str(probe_bundle.root),
                      probe_signed_envelope=str(signed_path))
    gen.bind(project, doc, configuration=deployment)
    output = json.loads((project / ".threadlight/governance-deployment.json").read_text())
    assert output["bindings"]["native_probe_config"] == probe_config
    assert output["probe_observability"] == {
        "status": "declared-unverified", "registry_digest": probe_bundle.bundle_digest,
        "policy_digest": config["policy_digest"], "fixture_installed": False}
    for field, value in (("service_client_id", APP), ("policy_digest", "sha256:" + "f"*64)):
        bad = copy.deepcopy(deployment)
        bad["probe_runtime_configuration"][field] = value
        with pytest.raises(ValueError):
            gen.bind(project, doc, configuration=bad)


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


def test_native_source_packaging_preserves_existing_hub_and_has_no_gateway_service(tmp_path):
    generator = module("generate")
    assert hasattr(generator, "package_native"), "Task10 native existing-infrastructure packaging missing"
    project = tmp_path / "example"
    source = project / "src/agent"
    source.mkdir(parents=True)
    (project / "azure.yaml").write_text(
        "name: returns\nservices:\n  returns:\n    host: azure.ai.agent\n    project: ./src/agent\n")
    (source / "container.py").write_text("from governance_host import main\n")
    (source / "governance_application.py").write_text("# trusted application supplied separately\n")
    (source / "pyproject.toml").write_text('[project]\nname="returns"\nversion="1"\ndependencies=[]\n')
    (source / "skills").mkdir()
    (source / "skills/domain").mkdir()
    (source / "skills/domain/SKILL.md").write_text("existing business skill")
    (project / "infra").mkdir()
    (project / "infra/main.bicep").write_text("// existing customer hub: do not replace")
    original = (project / "azure.yaml").read_bytes()
    result = generator.package_native(project, contract("microsoft-agent-framework"),
                                      configuration={"agent_service": "returns"})
    assert result["status"] == "source-packaged-unverified"
    assert (project / "azure.yaml").read_bytes() == original
    assert (project / "infra/main.bicep").read_text() == "// existing customer hub: do not replace"
    assert (source / "governance_host.py").read_bytes() == (REFERENCES / "maf-container.py").read_bytes()
    assert (source / "runtime/trusted_context.py").is_file()
    assert (source / "skills/_shared/manifest.py").is_file()
    assert (source / "skills/domain/SKILL.md").read_text() == "existing business skill"
    assert (source / "vendor/control-plane/app.py").is_file()
    assert (project / "src/governance-control-plane/vendor/control-plane/app.py").is_file()
    assert not (project / "src/govern-gateway").exists()
    assert not (source / "governance-config.json").exists()
    assert not (source / "policy-envelope.json").exists()
    assert "threadlight-govern-control-plane==0.1.0" in (source / "pyproject.toml").read_text()


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
        "environment": configuration["environment"],
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


def binding_project(tmp_path):
    configuration = {
        "agent_service": "agent", "agent_id": "test-agent", "policy_id": "safe", "policy_version": "1",
        "environment": "production",
        "policy_digest": "sha256:" + "a" * 64,
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "key_id": "https://testvault.vault.azure.net/keys/policy/" + "a" * 32,
        "control_plane_scope": "api://aaaaaaaa-2222-2222-2222-222222222222/.default",
        "gateway_scope": "api://bbbbbbbb-3333-3333-3333-333333333333/.default",
        "control_plane_url": "https://fixture-control.fixture.azurecontainerapps.io",
        "gateway_url": "https://fixture-gateway.fixture.azurecontainerapps.io/mcp",
        "approver_roles": ["Approver"],
        "network": {
            "posture": "public-pilot", "allowed_ips": ["192.0.2.10/32"],
            "environment_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/fixture/providers/Microsoft.App/managedEnvironments/test",
        },
    }
    import asyncio
    import base64
    import shutil
    from datetime import datetime, timedelta, timezone
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_runtime_provider import build_policy
    from test_control_plane import TestSigner
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    built = build_policy(tmp_path / "source-policy")
    configuration["policy_digest"] = built.bundle_digest
    envelope = BundleEnvelope(policy_id="safe", version="1", content_digest=built.bundle_digest,
        tenant_id=configuration["tenant_id"], key_id=configuration["key_id"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(TestSigner().sign(envelope_digest(envelope)))).decode())
    deployment = deployment_fixture(configuration)
    document = contract("microsoft-agent-framework")
    (tmp_path / ".threadlight").mkdir()
    (tmp_path / "infra").mkdir()
    (tmp_path / ".threadlight/governance-package.json").write_text(json.dumps({
        "configuration": configuration, "framework": document["framework"], "contract": document,
        "signed_policy": signed.model_dump(mode="json")}))
    agent = tmp_path / "src/agent"
    agent.mkdir(parents=True)
    (agent / "governance-config.json").write_text(json.dumps(module("generate").portable_configuration(
        configuration, document, document["framework"])))
    shutil.copytree(built.root, agent / "policy")
    (agent / "policy-envelope.json").write_bytes(canonical(signed))
    (tmp_path / "azure.yaml").write_text(
        "services:\n  agent:\n    image: " + deployment["images"]["agent"] + "\n"
        "    project: ./src/agent\n"
        "    env:\n      GOV_CONTROL_PLANE_URL: " + configuration["control_plane_url"] + "\n"
        "      GOVERNED_TOOL_GATEWAY_URL: " + configuration["gateway_url"] + "\n"
        "      TL_GOV_IMAGE_DIGEST: " + deployment["images"]["agent"].split("@")[1] + "\n")
    return configuration, deployment, document


@pytest.mark.parametrize("field,value", [
    ("control_plane_scope", "api://99999999-9999-9999-9999-999999999999/.default"),
    ("gateway_scope", "api://99999999-9999-9999-9999-999999999999/.default"),
    ("gateway_scope", "api://aaaaaaaa-2222-2222-2222-222222222222/.default"),
    ("control_plane_scope", "api://aaaaaaaa-2222-2222-2222-222222222222/extra/.default"),
    ("control_plane_scope", "API://AAAAAAAA-2222-2222-2222-222222222222/.default"),
    ("control_plane_url", "https://other.example"),
    ("gateway_url", "https://other.example/mcp"),
])
def test_bind_rejects_scope_or_endpoint_drift_without_writes(tmp_path, field, value):
    configuration, deployment, document = binding_project(tmp_path)
    configuration[field] = value
    package = tmp_path / ".threadlight/governance-package.json"
    body = json.loads(package.read_text())
    body["configuration"] = configuration
    package.write_text(json.dumps(body))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="service_auth_binding_mismatch"):
        module("generate").bind(tmp_path, document, configuration=deployment)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_bind_canonical_v2_scopes_match_actual_token_verifier(tmp_path):
    import asyncio
    configuration, deployment, document = binding_project(tmp_path)
    assert module("generate").bind(tmp_path, document, configuration=deployment)["status"] == "bound-unverified"
    body = json.loads((tmp_path / ".threadlight/governance-deployment.json").read_text())
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_control_plane import Harness, WORKLOAD, APP
    from govern_control_plane.auth import EntraAuth, Settings, Unauthorized
    async def check():
        h = Harness()
        try:
            for service in ("control_plane", "gateway"):
                audience = body["bindings"]["control_config" if service == "control_plane" else "gateway_config"]["audience"]
                settings = Settings(**{
                    **h.settings.model_dump(), "audience": audience})
                auth = EntraAuth(settings, h.http)
                identity = await auth.authenticate("Bearer " + h.token(changes={"aud": audience}))
                assert identity.subject == WORKLOAD and identity.client == APP
                for wrong in ("api://" + audience, audience.upper()):
                    with pytest.raises(Unauthorized):
                        await auth.authenticate("Bearer " + h.token(changes={"aud": wrong}))
                assert configuration[service + "_scope"] == "api://" + audience + "/.default"
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.parametrize("name", ["GOV_CONTROL_PLANE_URL", "GOVERNED_TOOL_GATEWAY_URL", "TL_GOV_IMAGE_DIGEST"])
def test_bind_rejects_bootstrapped_environment_drift_without_writes(tmp_path, name):
    import yaml
    _, deployment, document = binding_project(tmp_path)
    path = tmp_path / "azure.yaml"
    body = yaml.safe_load(path.read_text())
    body["services"]["agent"]["env"][name] = "https://different.example"
    path.write_text(yaml.safe_dump(body))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="service_auth_binding_mismatch"):
        module("generate").bind(tmp_path, document, configuration=deployment)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_audit_worker_shutdown_cancels_slow_credential_close(tmp_path):
    import asyncio
    import time
    audit, h, state = audit_harness(tmp_path, timeout=0.15)
    original = audit.credential_factory
    class SlowCredential(original):
        async def close(self):
            try:
                await asyncio.sleep(60)
            finally:
                state.closed = True
    audit.credential_factory = SlowCredential
    start = time.monotonic()
    with audit:
        assert audit.check()
    assert time.monotonic() - start < 2
    assert state.closed and not audit.worker.is_alive()
    asyncio.run(h.close())


def audit_harness(tmp_path, *, required=True, timeout=1, retry_interval=60):
    import asyncio
    import httpx
    import threading
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/references"))
    from test_control_plane import Harness
    h = Harness()
    state = SimpleNamespace(outage=False, lose_ack=False, bad_ack=False, hang=False,
                            calls=[], scopes=[], closed=False, recovered=threading.Event())
    class Credential:
        async def get_token(self, scope):
            state.scopes.append(scope)
            return SimpleNamespace(token=h.token())
        async def close(self):
            state.closed = True
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if state.hang:
                await asyncio.sleep(60)
            if state.outage:
                return httpx.Response(503, json={"error": "PRIVATE EXCEPTION"})
            if request.method == "POST":
                state.calls.append(bytes(request.content))
                # Local persistence must precede even the remote request.
                receipt_id = json.loads(request.content)["receipt_id"]
                assert (tmp_path / "audit" / (receipt_id + ".json")).exists()
            response = await httpx.ASGITransport(app=h.app).handle_async_request(request)
            if request.method == "POST" and state.lose_ack:
                state.lose_ack = False
                raise httpx.ReadError("PRIVATE LOST ACK")
            if request.method == "POST" and state.bad_ack:
                return httpx.Response(200, json={"receipt_id": "0" * 32})
            if request.method == "POST" and response.status_code == 200:
                state.recovered.set()
            return response
    audit = module("audit_delivery").AuditDelivery(
        tmp_path / "audit", base_url="https://control.example", scope="api://governance/.default",
        required=required, credential_factory=Credential, transport_factory=Transport,
        timeout=timeout, retry_interval=retry_interval)
    return audit, h, state


def append_audit(audit, **changes):
    return audit.append(**{
        "correlation_id": "sha256:" + "c" * 64, "action_id": "act",
        "interception_point": "pre_tool_call", "decision": "allow",
        "action_hash": "sha256:" + "a" * 64, "policy_hash": "sha256:" + "b" * 64,
        "reason_code": "threadlight:policy_allow", "agent_version": "deployed-7",
        "image_digest": "sha256:" + "d" * 64, **changes})


def test_audit_remote_ack_lost_restart_replays_identical_schema_and_single_record(tmp_path):
    import asyncio
    audit, h, state = audit_harness(tmp_path)
    from test_control_plane import TENANT
    with audit:
        state.lose_ack = True
        with pytest.raises(OSError, match="audit_unavailable"):
            append_audit(audit)
        pending = json.loads(next(audit.directory.glob("*.json")).read_text())
        assert pending["delivery_status"] == "pending"
        assert len(h.store.docs) == 1, "Task8 persisted before its ACK was lost"
    assert state.closed and not audit.worker.is_alive()
    # A fresh delivery worker, same persisted file: no regenerated timestamps/body.
    replay = module("audit_delivery").AuditDelivery(
        audit.directory, base_url=audit.url, scope=audit.scope, required=True,
        credential_factory=audit.credential_factory, transport_factory=audit.transport_factory,
        timeout=1, retry_interval=60)
    with replay:
        assert state.recovered.wait(3)
        assert replay.check()
        assert state.calls[0] == state.calls[1]
        assert len(state.calls) == 2 and len(h.store.docs) == 1
        stored = json.loads(next(audit.directory.glob("*.json")).read_text())
        assert stored["delivery_status"] == "delivered"
        wire = json.loads(state.calls[0])
        assert wire["action_id"] == "act" and wire["agent_version"] == "deployed-7"
        assert wire["image_digest"] == "sha256:" + "d" * 64
        assert len(wire["correlation_id"]) <= 128
        async def get():
            result = await h.client.get("/receipts/" + wire["receipt_id"],
                headers={"Authorization": "Bearer " + h.token()})
            assert result.status_code == 200 and result.json() == wire
        asyncio.run(get())
        assert h.store.docs[(TENANT, "receipt:" + wire["receipt_id"])][0]["receipt"] == wire
    assert state.scopes and set(state.scopes) == {"api://governance/.default"}
    asyncio.run(h.close())


def test_audit_initial_outage_pending_then_background_recovery(tmp_path):
    import asyncio
    audit, h, state = audit_harness(tmp_path, retry_interval=0.05)
    state.outage = True
    try:
        with audit:
            with pytest.raises(OSError, match="audit_unavailable"):
                append_audit(audit)
            assert not audit.check()
            assert json.loads(next(audit.directory.glob("*.json")).read_text())["delivery_status"] == "pending"
            state.outage = False
            assert state.recovered.wait(3), "running retry loop must recover without a new invocation"
            assert audit.check()
            assert len(h.store.docs) == 1
    finally:
        asyncio.run(h.close())


def test_audit_bad_ack_and_timeout_never_mark_delivered_and_shutdown_bounded(tmp_path):
    import asyncio
    import time
    audit, h, state = audit_harness(tmp_path, timeout=0.15)
    start = time.monotonic()
    with audit:
        state.bad_ack = True
        with pytest.raises(OSError, match="audit_unavailable"):
            append_audit(audit)
        assert not audit.check()
        state.hang = True
        with pytest.raises(OSError, match="audit_unavailable"):
            append_audit(audit)
    assert time.monotonic() - start < 3
    assert state.closed and not audit.worker.is_alive()
    assert all(json.loads(p.read_text())["delivery_status"] == "pending" for p in audit.directory.glob("*.json"))
    asyncio.run(h.close())


def test_audit_health_rejects_unwritable_local_safety_spool(tmp_path):
    import asyncio
    audit, h, _ = audit_harness(tmp_path)
    try:
        with audit:
            assert audit.check()
            audit.directory.chmod(0o777)
            assert not audit.check(), "remote health cannot mask an unsafe local retry directory"
            with pytest.raises(OSError):
                append_audit(audit)
            assert not h.store.docs
    finally:
        audit.directory.chmod(0o700)
        asyncio.run(h.close())


def test_audit_background_corrupt_record_is_unhealthy_not_silently_delivered(tmp_path):
    import asyncio
    audit, h, _ = audit_harness(tmp_path, retry_interval=0.05)
    audit.directory.mkdir()
    path = audit.directory / ("a" * 32 + ".json")
    path.write_text('{"delivery_status":"pending","audit_id":"' + "a" * 32 + '"}')
    with audit:
        assert not audit.check()
        assert json.loads(path.read_text())["delivery_status"] == "pending"
        assert not h.store.docs
    asyncio.run(h.close())


@pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                    reason="requires exact published Linux runtime")
def test_native_audit_error_and_lifecycle_use_exact_action_identity(tmp_path):
    import asyncio
    audit, h, state = audit_harness(tmp_path)
    from test_runtime_provider import provider, contract as native_contract, native_model_client, tool_responses, runtime
    from agent_framework import FunctionTool
    p, _, _ = provider(tmp_path / "policy",
        decisions={point: {"decision": "allow"} for point in ("input", "pre_tool_call", "post_tool_call")},
        document=native_contract(points=("pre_tool_call", "post_tool_call"),
                                 lifecycle=("input",), requires=["durable-audit"]),
        audit=audit, agent_version="native-9", image_digest="sha256:" + "e" * 64)
    def act():
        raise RuntimeError("PRIVATE ARGUMENT OUTPUT EXCEPTION")
    client = native_model_client(tool_responses())
    try:
        with audit:
            agent = runtime().create_governed_agent(p, client=client, tools=[FunctionTool(
                name="act", description="Test", func=act)])
            asyncio.run(agent.run("PRIVATE PROMPT"))
            bodies = [json.loads(raw) for raw in state.calls]
            assert any(b["action_id"] == "input" for b in bodies)
            assert any(b["action_id"] == "act" and b["decision"] == "error" for b in bodies)
            assert "PRIVATE" not in b"".join(state.calls).decode()
    finally:
        asyncio.run(h.close())


@pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                    reason="requires exact published Linux runtime")
@pytest.mark.parametrize("outage", [False, True])
def test_native_audit_ack_saved_before_effect_or_zero_effect(tmp_path, outage):
    import asyncio
    audit, h, state = audit_harness(tmp_path)
    from test_runtime_provider import provider, contract as native_contract, run_tool
    state.outage = outage
    p, _, _ = provider(
        tmp_path / "policy", decisions={"pre_tool_call": {"decision": "allow"}},
        document=native_contract(requires=["durable-audit"]), audit=audit,
        agent_version="native-9", image_digest="sha256:" + "e" * 64)
    def effect():
        receipts = [doc["receipt"] for doc, _ in h.store.docs.values()]
        assert any(r["decision"] == "allow" and r["action_id"] == "act"
                   and r["agent_version"] == "native-9" and r["image_digest"] == "sha256:" + "e" * 64
                   for r in receipts), "actual Task8 saved the receipt BEFORE application effect"
    try:
        with audit:
            _, effects, _, _ = run_tool(p, on_effect=effect)
            assert effects == ([] if outage else [{}])
            files = list(audit.directory.glob("*.json"))
            assert files
            assert "arguments" not in "".join(f.read_text() for f in files)
            if not outage:
                assert json.loads(state.calls[0])["action_id"] == "act"
    finally:
        asyncio.run(h.close())


@pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                    reason="requires exact published Linux runtime")
def test_native_audit_dependency_readiness_recovers_only_after_replay(tmp_path):
    import asyncio
    audit, h, state = audit_harness(tmp_path, retry_interval=0.05)
    sys.path.insert(0, str(REFERENCES))
    host = module("maf-container")
    from test_runtime_provider import provider, contract as native_contract, run_tool
    p, authority, _ = provider(tmp_path / "policy",
        decisions={"pre_tool_call": {"decision": "allow"}},
        document=native_contract(requires=["durable-audit"]), audit=audit,
        agent_version="native-9", image_digest="sha256:" + "e" * 64)
    state.outage = True
    try:
        with audit:
            assert run_tool(p)[1] == []
            assert asyncio.run(host.dependency_readiness(p)).status_code == 503
            state.outage = False
            assert state.recovered.wait(3)
            assert asyncio.run(host.dependency_readiness(p)).status_code == 200
            authority.valid = False
            assert asyncio.run(host.dependency_readiness(p)).status_code == 503
    finally:
        asyncio.run(h.close())


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
        "environment": "production",
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
    assert (await container.dependency_readiness(audit_provider)).status_code == 503
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
    # Standalone native/Task8 proof is also run in Docker's root overlay, with no catalog mount.
    import shutil
    allowed = build_policy(tmp_path / "allow-policy", {"pre_tool_call": {"decision": "allow"}})
    audit_fixture = agent / "audit-fixture"
    audit_fixture.mkdir()
    shutil.copytree(allowed.root, audit_fixture / "policy")
    (audit_fixture / "copilot-instructions.md").write_text("Only call the governed act tool.")
    allow_envelope = envelope.model_copy(update={"content_digest": allowed.bundle_digest})
    allow_signature = private.sign(cp_models.canonical(allow_envelope), padding.PKCS1v15(), hashes.SHA256())
    (audit_fixture / "policy-envelope.json").write_bytes(cp_models.canonical(cp_models.SignedBundle(
        envelope=allow_envelope, signature=base64.b64encode(allow_signature).decode())))
    (audit_fixture / "authority-public.pem").write_bytes(public_path.read_bytes())
    audit_configuration = {**json.loads((agent / "governance-config.json").read_text()),
        "policy_digest": allowed.bundle_digest, "agent_version": "portable-9",
        "image_digest": "sha256:" + "e" * 64, "principal": "44444444-4444-4444-4444-444444444444"}
    audit_configuration["contract"]["tools"][0]["requires"] = ["durable-audit"]
    (audit_fixture / "config.json").write_text(json.dumps(audit_configuration))
    (audit_fixture / "probe.py").write_text(PORTABLE_AUDIT_PROBE)
    completed = subprocess.run([sys.executable, "-c", PORTABLE_AUDIT_PROBE], cwd=agent,
        capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
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


PORTABLE_AUDIT_PROBE = r'''
import asyncio, json, os, base64, time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import httpx, jwt
from agent_framework import tool
from agent_framework.foundry import FoundryChatClient
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils, rsa
from govern_control_plane.app import ControlPlane, create_app
from govern_control_plane.auth import Settings, EntraAuth
from govern_control_plane.storage import Conflict, Missing
from openai import AsyncOpenAI
import container
import governance_application as application

async def main():
    if os.environ.get("THREADLIGHT_OVERLAY_PROBE"):
        assert not Path("/work").exists()
        root = next(line for line in Path("/proc/self/mountinfo").read_text().splitlines()
                    if line.split()[4] == "/")
        assert root.split(" - ")[1].split()[0] == "overlay"
    container.BASE = Path.cwd() / "audit-fixture"
    config = json.loads((container.BASE / "config.json").read_text())
    config["spool_dir"] = str(Path.cwd() / "ephemeral-required-audit")
    public = serialization.load_pem_public_key((container.BASE / "authority-public.pem").read_bytes())
    class Signer:
        async def health(self): pass
        async def verify(self, digest, signature):
            public.verify(signature, digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
            return True
    # Test-only Task8 storage protocol; the actual service owns receipt validation/dedup.
    class Store:
        def __init__(self): self.docs = {}
        async def health(self): pass
        async def create(self, tenant, key, body):
            if (tenant, key) in self.docs: raise Conflict()
            self.docs[tenant, key] = deepcopy(body)
        async def read(self, tenant, key):
            if (tenant, key) not in self.docs: raise Missing()
            return deepcopy(self.docs[tenant, key]), "1"
    store = Store()
    settings = Settings(tenant_id=config["tenant_id"], audience=config["control_plane_scope"][6:-9],
        key_id=config["key_id"], workloads={config["principal"]: {
            "client_id": config["principal"], "agent_id": config["agent_id"], "policies": ["safe"]}},
        human_clients=["55555555-5555-5555-5555-555555555555"],
        approver_subjects=["66666666-6666-6666-6666-666666666666"], approver_roles=["Approver"])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test", use="sig", alg="RS256")
    jwks = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"keys":[jwk]})))
    auth = EntraAuth(settings, jwks)
    app = create_app(service=ControlPlane(settings, store, Signer()), auth=auth)
    effects, scopes = [], []
    class Credential:
        async def get_token(self, scope):
            scopes.append(scope)
            now = int(time.time())
            return SimpleNamespace(token=jwt.encode(dict(iss=settings.issuer, aud=settings.audience,
                tid=settings.tenant_id, oid=config["principal"], azp=config["principal"], ver="2.0",
                roles=["Governance.Workload"], idtyp="app", exp=now+300, nbf=now-1, iat=now-1),
                key, algorithm="RS256", headers={"kid":"test"}))
        async def close(self): pass
    @tool(approval_mode="never_require")
    def act() -> str:
        """Synthetic application effect."""
        assert any(record["receipt"]["decision"] == "allow" and record["receipt"]["action_id"] == "act"
            and record["receipt"]["agent_version"] == config["agent_version"]
            and record["receipt"]["image_digest"] == config["image_digest"]
            for record in store.docs.values()), "effect ran before Task8 persistence"
        effects.append("act")
        return "changed"
    application.tools = [act]
    for remote in (False, True):
        provider = await container.build_provider(config, signer=Signer(), credential=Credential())
        provider.audit.credential_factory = Credential
        provider.audit.transport_factory = lambda: httpx.ASGITransport(app=app)
        if remote:
            await provider.audit.__aenter__()
        else:
            Path(config["spool_dir"]).mkdir(mode=0o700, exist_ok=True)
        assert (await container.dependency_readiness(provider)).status_code == (200 if remote else 503)
        calls = []
        def model(request):
            calls.append(json.loads(request.content))
            output = ([{"type":"function_call","id":"fc_1","call_id":"call_1",
                        "name":"act","arguments":"{}","status":"completed"}] if len(calls)==1 else [])
            return httpx.Response(200, json={"id":"resp_1","object":"response",
                "created_at":0,"status":"completed","model":"test","output":output})
        wire = httpx.AsyncClient(transport=httpx.MockTransport(model))
        client = FoundryChatClient(project_endpoint="https://test.services.ai.azure.com/api/projects/test",
                                  model="test", credential=Credential())
        client.client = AsyncOpenAI(base_url="https://model.invalid/v1/", api_key="synthetic", http_client=wire)
        host = container.build_host(provider, client=client, configure_observability=None)
        await host._agent.run("Call act.")
        assert effects == (["act"] if remote else [])
        await wire.aclose()
        await provider.approval_resolver.aclose()
        if remote:
            await provider.audit.__aexit__(None, None, None)
            assert not provider.audit.worker.is_alive()
    assert scopes and set(scopes) == {config["control_plane_scope"]}
    await jwks.aclose()
    print("PORTABLE_NATIVE_REMOTE_ACK_PASS: Task8 saved before effect; local-only directory denied")
asyncio.run(main())
'''
