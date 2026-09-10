"""MAF gateway-only generation through real native policy and signed registry inputs."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_governance_quality import inputs, snapshot
from test_governance_wiring import REFERENCES, contract, module

pytestmark = pytest.mark.governance_runtime


def test_maf_gateway_documents_host_owned_middleware_boundary():
    readme = (REFERENCES / "README.md").read_text()
    assert "an empty `middleware` list" in readme
    assert "Nonempty application middleware is unsupported" in readme


def gateway_inputs(path, **kwargs):
    data = inputs(path, framework="github-copilot-sdk", **kwargs)
    project, document, config, deployment, _ = data
    document["framework"] = "microsoft-agent-framework"
    deployment["infrastructure"].update(runtime=document["framework"], enable_gateway=True)
    config.pop("mcp_servers")
    config.pop("mcp_bindings")
    (project / "src/agent/governance_application.py").write_text(
        "from agent_framework import tool\n"
        "middleware = []\n"
        "@tool(approval_mode='never_require')\n"
        "def read() -> str:\n"
        "    '''Read without a selected policy.'''\n"
        "    return 'read'\n"
        "tools = [read]\n")
    return data


def package(data):
    project, document, config, deployment, _ = data
    generator = module("generate")
    generator.generate(project, document, configuration=config)
    generator.agent_image(project, document, configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
    return generator


def stage(generator, data, *, bundle=None, signed=None, digest=None):
    project, document, config, deployment, _ = data
    deployment["gateway_bundle"] = str(bundle or config["bundle_path"])
    staged = generator.stage_gateway(project, document, configuration={
        "gateway_bundle": deployment["gateway_bundle"],
        "policy_digest": digest or config["policy_digest"],
        "signed_envelope": str(signed or config["signed_envelope"]),
        "agent_image": deployment["images"]["agent"]})
    deployment["gateway_source_digest"] = staged["gateway_source_digest"]
    return staged


def test_maf_gateway_generate_stage_bind_preserves_responses_and_remote_policy(tmp_path):
    data = gateway_inputs(tmp_path, requires=[
        "human-approval-record", "decision-receipt", "idempotency-or-transaction",
        "signed-policy-bundle", "authorization", "output-mediation"],
        points=("pre_tool_call", "post_tool_call"),
        decisions={"pre_tool_call": {"decision": "allow"}, "post_tool_call": {"decision": "deny"}})
    project, document, config, deployment, _ = data
    generator = package(data)
    agent = project / "src/agent"
    frozen_bytes = (agent / "governance-config.json").read_bytes()
    frozen = json.loads(frozen_bytes)
    assert frozen["contract"]["framework"] == "microsoft-agent-framework"
    assert "policy_digest" not in frozen and "audit_delivery" not in frozen
    assert "mcp_servers" not in frozen and "mcp_bindings" not in frozen
    for key in ("gateway_url", "gateway_scope", "control_plane_url", "control_plane_scope",
                "key_id", "tenant_id", "policy_id", "policy_version", "agent_id"):
        assert frozen[key] == config[key]
    assert (agent / "container.py").read_bytes() == (REFERENCES / "maf-gateway-container.py").read_bytes()
    assert (agent / "maf_gateway.py").read_bytes() == (REFERENCES / "maf_gateway.py").read_bytes()
    assert not (agent / "policy").exists()
    assert not (agent / "policy-envelope.json").exists()
    assert not (agent / "vendor/gateway").exists()
    assert not (agent / "audit_delivery.py").exists()
    assert (agent / "vendor/control-plane/bootstrap.py").exists()
    assert "./vendor/gateway" not in (agent / "Dockerfile").read_text()
    dependencies = tomllib.loads((agent / "pyproject.toml").read_text())["project"]["dependencies"]
    assert "agent-framework-core==1.14.0" in dependencies
    assert not any(dep.startswith("github-copilot-sdk") for dep in dependencies)
    agent_before = snapshot(agent)
    staged = stage(generator, data)
    assert staged["policy_digest"] == config["policy_digest"]
    assert generator.bind(project, document, configuration=deployment)["status"] == "bound-unverified"
    assert snapshot(agent) == agent_before
    azure = yaml.safe_load((project / "azure.yaml").read_text())
    assert azure["services"]["agent"]["protocols"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert (agent / "governance-config.json").read_bytes() == frozen_bytes
    bound = json.loads((project / ".threadlight/governance-deployment.json").read_text())
    assert bound["bindings"]["gateway_config"]["policy_digest"] == config["policy_digest"]
    assert "native_probe_config" not in bound["bindings"]


def test_generated_maf_gateway_imports_without_catalog_namespace(tmp_path):
    data = gateway_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    generator.bind(data[0], data[1], configuration=data[3])
    agent = (data[0] / "src/agent").resolve()
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; from pathlib import Path; "
         f"app=Path({str(agent)!r}); sys.path.insert(0,str(app)); "
         "import container, maf_gateway, govern_control_plane.client, skills._shared.governance; "
         "assert Path(container.__file__).parent == app; "
         "assert Path(maf_gateway.__file__).parent == app; "
         "assert Path(skills._shared.governance.__file__).is_relative_to(app); "
         "assert callable(container.build_host) and callable(maf_gateway.create_gateway_agent); "
         "print('portable-maf-gateway-imports')"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "portable-maf-gateway-imports"


@pytest.mark.parametrize("fault", ["mixed", "lifecycle", "evaluate-only", "intervention"])
def test_maf_gateway_unsupported_contract_rejected_before_writes(tmp_path, fault):
    data = gateway_inputs(tmp_path)
    project, document, config, _, _ = data
    if fault == "mixed":
        document["tools"].append({**deepcopy(document["tools"][0]), "id": "local",
                                  "enforcement_path": "local-agent-hooks"})
    elif fault == "lifecycle":
        document["governance"]["lifecycle_bindings"] = [{
            "lifecycle_point": "startup", "policy_binding": "safe",
            "enforcement_path": "local-agent-hooks", "safe_principles": ["Safety"], "requires": []}]
    elif fault == "evaluate-only":
        config["environment"] = "staging"
    else:
        document["tools"][0]["intervention_points"] = ["post_tool_call"]
    before = snapshot(project)
    with pytest.raises(ValueError, match="gateway.*(mixed|lifecycle|environment|intervention)"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


@pytest.mark.parametrize("consequence", ["write", "external-egress", "irreversible", "unknown"])
@pytest.mark.parametrize("accepted", [False, True])
def test_maf_gateway_unbound_consequential_tools_rejected_before_writes(tmp_path, consequence, accepted):
    project, document, config, _, _ = gateway_inputs(tmp_path)
    unbound = document["tools"][1]
    unbound["consequence"] = consequence
    if accepted:
        now = datetime.now(timezone.utc)
        unbound["acceptance_record"] = {
            "owner": "fixture-risk-owner", "justification": "Explicit fixture acceptance",
            "review_date": (now - timedelta(hours=1)).isoformat(),
            "expiry": (now + timedelta(hours=1)).isoformat()}
    before = snapshot(project)
    with pytest.raises(ValueError, match="maf_gateway_unbound_consequential_unsupported"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_empty_maf_selection_cannot_choose_gateway_host():
    document = contract("microsoft-agent-framework", off=True)
    document["governance"]["mode"] = "selective"
    generator = module("generate")
    assert not generator.uses_gateway(document)
    assert not generator.uses_gateway(generator.validate_contract(document))


@pytest.mark.parametrize("fault", ["post", "approval", "environment"])
def test_maf_gateway_registry_contract_rejected_before_writes(tmp_path, fault):
    def change(registry):
        if fault == "post":
            registry["actions"][0]["post_policy_binding"] = None
        elif fault == "approval":
            registry["actions"][0]["approval_roles"] = []
        else:
            registry["deployment"]["environment"] = "staging"
    data = gateway_inputs(tmp_path, requires=["human-approval-record"],
                          points=("pre_tool_call", "post_tool_call"),
                          decisions={"pre_tool_call": {"decision": "allow"},
                                     "post_tool_call": {"decision": "deny"}},
                          registry_change=change)
    project, document, config, _, _ = data
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_registry.*(binding|approval|environment)"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


@pytest.mark.parametrize("final_version", [None, "1"])
def test_maf_gateway_bootstrap_requires_distinct_final_policy_version(tmp_path, final_version):
    project, document, config, _, _ = gateway_inputs(tmp_path)
    config["remote_bootstrap"] = {
        "reference": "attempt-1", "project_endpoint": "https://test.services.ai.azure.com/api/projects/test",
        "subscription": config["tenant_id"], "resource_group": "fixture",
        "native_policy_digest": config["policy_digest"]}
    if final_version is not None:
        config["remote_bootstrap"]["final_policy_version"] = final_version
    before = snapshot(project)
    with pytest.raises(ValueError, match="distinct_final_gateway_policy_version_required"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_maf_gateway_remote_bootstrap_stages_final_policy_without_agent_rewrite(tmp_path):
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    data = gateway_inputs(tmp_path, environment="preproduction")
    project, document, config, deployment, signer = data
    original_digest = config["policy_digest"]
    config["remote_bootstrap"] = {
        "reference": "attempt-1", "project_endpoint": "https://test.services.ai.azure.com/api/projects/test",
        "subscription": config["tenant_id"], "resource_group": "fixture",
        "native_policy_digest": original_digest, "final_policy_version": "2"}
    generator = package(data)
    agent_before = snapshot(project / "src/agent")
    source = tmp_path / "final-source"
    source.mkdir()
    for file in Path(config["bundle_path"]).iterdir():
        if file.name != "bundle-metadata.json":
            shutil.copyfile(file, source / file.name)
    final = bundle_module().build_bundle(
        source=source, destination=tmp_path / "final-policy", policy_id="safe", version="2")
    envelope = BundleEnvelope(
        policy_id="safe", version="2", tenant_id=config["tenant_id"], key_id=config["key_id"],
        content_digest=final.bundle_digest,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    signed_path = tmp_path / "final-signed.json"
    signed_path.write_bytes(canonical(signed))
    stage(generator, data, bundle=final.root, signed=signed_path, digest=final.bundle_digest)
    deployment["bindings"].update(policy_version="2", policy_digest=final.bundle_digest)
    assert generator.bind(project, document, configuration=deployment)["status"] == "bound-unverified"
    assert snapshot(project / "src/agent") == agent_before
    frozen = json.loads((project / "src/agent/governance-config.json").read_text())
    assert frozen["remote_bootstrap"]["native_policy_digest"] == original_digest
    assert frozen["policy_version"] == "1"
    assert "policy_digest" not in frozen
    metadata = json.loads((project / ".threadlight/governance-package.json").read_text())
    assert metadata["configuration"]["policy_digest"] == original_digest


@pytest.mark.parametrize("fault", ["image", "source"])
def test_maf_gateway_bind_requires_final_image_bound_source(tmp_path, fault):
    data = gateway_inputs(tmp_path)
    project, document, _, deployment, _ = data
    generator = package(data)
    stage(generator, data)
    if fault == "image":
        deployment["images"]["agent"] = "fixture.azurecr.io/agent@sha256:" + "d" * 64
    else:
        (project / "src/govern-gateway/service_entry.py").write_text("# changed after build\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_registry_deployment_mismatch|gateway_image_must_match"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


@pytest.mark.parametrize("enabled", [False, None, "true"])
def test_maf_gateway_cannot_bind_a_disabled_or_ambiguous_service(tmp_path, enabled):
    data = gateway_inputs(tmp_path)
    project, document, _, deployment, _ = data
    generator = package(data)
    stage(generator, data)
    if enabled is None:
        deployment["infrastructure"].pop("enable_gateway")
    else:
        deployment["infrastructure"]["enable_gateway"] = enabled
    before = snapshot(project)
    with pytest.raises(ValueError, match="selected_gateway_service_required"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


def test_maf_gateway_rejects_leftover_native_probe_binding(tmp_path):
    data = gateway_inputs(tmp_path)
    project, document, _, deployment, _ = data
    generator = package(data)
    stage(generator, data)
    deployment["bindings"]["native_probe_config"] = {"producer": "native"}
    before = snapshot(project)
    with pytest.raises(ValueError, match="gateway_must_not_have_native_probe_binding"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


def test_maf_gateway_probe_uses_gateway_not_native_producer(tmp_path):
    from test_probe_telemetry import probe_registry
    def change(registry):
        action = registry["actions"][0]
        registry["actions"] = [{**probe_registry()["actions"][0], "workloads": action["workloads"]}]
        registry["deployment"]["resource_group"] = "fixture"
    data = gateway_inputs(tmp_path, environment="preproduction", registry_change=change)
    project, document, config, deployment, _ = data
    document["tools"][0]["id"] = "governance_probe_noop"
    action = probe_registry()["actions"][0]
    deployment["bindings"]["allowed_endpoints"] = [action["endpoint"], action["outcome_endpoint"]]
    config.update(subscription=config["tenant_id"], resource_group="fixture", probe_observability={
        "enabled": True, "configuration_file": "/mnt/governance-probe/config.json"})
    deployment["infrastructure"]["probe_observability"] = config["probe_observability"]
    deployment["bindings"]["probe_controllers"] = {
        deployment["infrastructure"]["approver_subjects"][0]: {
            "client_id": deployment["infrastructure"]["human_clients"][0],
            "subjects": [deployment["bindings"]["agent_principal"]], "actions": ["governance_probe_noop"]}}
    generator = package(data)
    assert not (project / "src/agent/vendor/gateway").exists()
    stage(generator, data)
    generator.bind(project, document, configuration=deployment)
    output = json.loads((project / ".threadlight/governance-deployment.json").read_text())
    assert output["bindings"]["gateway_config"]["probe_enabled"] is True
    assert "native_probe_config" not in output["bindings"]
    assert output["probe_observability"] == {
        "status": "declared-unverified", "registry_digest": config["policy_digest"],
        "policy_digest": config["policy_digest"], "fixture_installed": False}


@pytest.mark.parametrize("operation", ["generate", "agent_image", "stage_gateway", "bind", "package_native"])
def test_maf_gateway_off_remains_byte_noop(tmp_path, operation):
    (tmp_path / "azure.yaml").write_bytes(b"name: preserve\n# formatting\n")
    before = snapshot(tmp_path)
    result = getattr(module("generate"), operation)(tmp_path, contract("microsoft-agent-framework", off=True))
    assert result == {"status": "off"}
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("framework", ["microsoft-agent-framework", "github-copilot-sdk"])
def test_existing_selected_routes_still_package_and_bind(tmp_path, framework):
    data = inputs(tmp_path, framework=framework)
    project, document, _, deployment, _ = data
    generator = package(data)
    if framework == "github-copilot-sdk":
        stage(generator, data)
    assert generator.bind(project, document, configuration=deployment)["status"] == "bound-unverified"
    agent = project / "src/agent"
    assert not (agent / "maf_gateway.py").exists()
    assert (agent / "policy").exists() == (framework == "microsoft-agent-framework")
    assert (agent / "audit_delivery.py").exists() == (framework == "microsoft-agent-framework")


def test_native_source_only_package_still_rejects_gateway(tmp_path):
    project, document, config, _, _ = gateway_inputs(tmp_path)
    before = snapshot(project)
    with pytest.raises(ValueError, match="gateway"):
        module("generate").package_native(project, document, configuration=config)
    assert snapshot(project) == before
