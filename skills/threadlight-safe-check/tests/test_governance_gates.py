"""Static gates use the actual Task10 generator, not a claimed adapter boolean."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
for directory in ("threadlight-govern/tests", "threadlight-deploy/tests",
                  "threadlight-safe-check/scripts", "threadlight-safe-check/references"):
    sys.path.insert(0, str(ROOT / "skills" / directory))

import safe_check


def reference(name):
    path = ROOT / "skills/threadlight-safe-check/references" / (name + ".py")
    assert path.exists(), f"missing implemented {name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def empty_parent_azure(*args, **kwargs):
    if args[:2] == ("account", "show"):
        return json.dumps({"id": "11111111-1111-1111-1111-111111111111",
                           "tenantId": "11111111-1111-1111-1111-111111111111"})
    return "[]"


def generated(tmp_path):
    from test_governance_quality import inputs
    from test_governance_wiring import module
    project, contract, config, deployment, signer = inputs(tmp_path, environment="preproduction")
    module("generate").generate(project, contract, configuration=config)
    manifest = {**contract, "deployment_manifest": {
        "module_selectors": {}, "services": [
            {"name": name, "host": "containerapp", "src": "src/" + name}
            for name in ("govern-control-plane", "govern-gateway")],
        "expected_resource_types": []}}
    (project / "specs").mkdir()
    path = project / "specs/manifest.json"
    path.write_text(json.dumps(manifest))
    return project, manifest, path, config, deployment, signer


@pytest.mark.governance_runtime
def test_selected_binding_missing_runtime_adapter_is_gap(tmp_path):
    project, _, path, *_ = generated(tmp_path)
    (project / "src/agent/runtime/maf_agent_hooks_acs.py").unlink()
    output = project / "gate.json"
    assert safe_check.phase_predeploy(project, path, output) == 1
    assert "selected binding has no runtime adapter" in " ".join(json.loads(output.read_text())["gaps"])


@pytest.mark.governance_runtime
def test_intentionally_unbound_read_tool_is_not_gap(tmp_path):
    project, document, path, *_ = generated(tmp_path)
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == []
    assert result["stage"] == "pre-image"
    assert "image-not-built" in result["unverified"]
    assert all(binding["status"] != "enforced" for binding in result["bindings"])
    assert next(b for b in result["bindings"] if b["tool_id"] == "read")["status"] == "unbound"
    assert safe_check.phase_predeploy(project, path, project / "predeploy.json") == 0


def staged_gateway_project(tmp_path, *, phase="bound"):
    """Build bootstrap first; add the signed registry only after freezing the agent image."""
    import asyncio
    import base64
    from datetime import datetime, timedelta, timezone
    from test_governance_quality import inputs
    from test_governance_wiring import module
    from test_gateway import registry
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest

    project, document, config, deployment, signer = inputs(tmp_path, environment="preproduction")
    document["framework"] = "github-copilot-sdk"
    document["tools"][0]["enforcement_path"] = "governed-tool-gateway"
    config.update(
        mcp_servers={"original": {"type": "http", "url": "https://original.example/mcp", "tools": ["act", "read"]}},
        mcp_bindings={"act": {"server": "original", "tool": "act"}})
    deployment["infrastructure"].update(runtime=document["framework"], enable_gateway=True)
    gen = module("generate")
    gen.foundation(project, document, configuration=deployment["infrastructure"])
    gen.generate(project, document, configuration=config)
    package_path = project / ".threadlight/governance-package.json"
    frozen = package_path.read_bytes()
    agent_source = gen.tree_digest(project / "src/agent")
    (project / "specs").mkdir()
    manifest = {**document, "deployment_manifest": {
        "module_selectors": {}, "services": [
            {"name": name, "host": "containerapp", "src": "src/" + name}
            for name in ("govern-control-plane", "govern-gateway")], "expected_resource_types": []}}
    (project / "specs/manifest.json").write_text(json.dumps(manifest))
    if phase == "bootstrap":
        return project, document, config, deployment, signer
    gen.agent_image(project, document, configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
    if phase == "image":
        return project, document, config, deployment, signer
    registered = registry()
    registered.update(tenant_id=config["tenant_id"], gateway_url=config["gateway_url"])
    registered["deployment"].update(agent_id=config["agent_id"],
        image_digest=deployment["images"]["agent"].split("@")[1])
    registered["actions"][0].update(name="act", workloads=[deployment["bindings"]["agent_principal"]],
        endpoint=deployment["bindings"]["allowed_endpoints"][0],
        outcome_endpoint=deployment["bindings"]["allowed_endpoints"][1])
    (tmp_path / "bundle-input/source/gateway-registry.json").write_text(json.dumps(registered))
    final = bundle_module().build_bundle(source=tmp_path / "bundle-input/source",
        destination=tmp_path / "final-policy", policy_id="safe", version="1")
    envelope = BundleEnvelope(policy_id="safe", version="1", tenant_id=config["tenant_id"],
        key_id=config["key_id"], content_digest=final.bundle_digest,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    envelope_path = tmp_path / "final-envelope.json"
    envelope_path.write_bytes(canonical(signed))
    staged = gen.stage_gateway(project, document, configuration={
        "gateway_bundle": str(final.root), "policy_digest": final.bundle_digest,
        "signed_envelope": str(envelope_path), "agent_image": deployment["images"]["agent"]})
    assert final.bundle_digest != config["policy_digest"]
    deployment.update(gateway_bundle=str(final.root), gateway_source_digest=staged["gateway_source_digest"])
    deployment["bindings"]["policy_digest"] = final.bundle_digest
    if phase == "bound":
        gen.bind(project, document, configuration=deployment)
    assert package_path.read_bytes() == frozen
    assert gen.tree_digest(project / "src/agent") == agent_source
    return project, document, config, deployment, signer


@pytest.mark.parametrize("phase", ["bootstrap", "image", "staged", "bound"])
@pytest.mark.governance_runtime
def test_real_gateway_staging_static_gate(tmp_path, phase):
    project, document, config, deployment, _ = staged_gateway_project(tmp_path, phase=phase)
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == [], result
    assert all(b["status"] != "enforced" for b in result["bindings"])
    expected = config["policy_digest"] if phase in ("bootstrap", "image") else deployment["bindings"]["policy_digest"]
    assert result["bindings"][0]["policy_digest"] == expected
    assert result["policy_trust"] == {
        "source": {"bootstrap": "bootstrap-package", "image": "bootstrap-package",
                   "staged": "staged-envelope", "bound": "deployment-binding"}[phase],
        "digest": expected, "signature_verified": False}
    if phase in ("bootstrap", "image"):
        assert "final-gateway-policy-not-staged" in result["unverified"]
    assert safe_check.phase_predeploy(project, project / "specs/manifest.json", project / "predeploy.json") == 0


@pytest.mark.parametrize("phase", ["bootstrap", "image", "staged"])
@pytest.mark.governance_runtime
def test_gateway_without_final_binding_cannot_collect_live_evidence(tmp_path, phase):
    import asyncio
    project, _, *_ = staged_gateway_project(tmp_path, phase=phase)
    options = project / ".threadlight/governance-probe.json"
    options.write_text("{}")
    def forbidden(*args, **kwargs):
        pytest.fail("Unbound deployment must not invoke external observations or an agent")
    result = asyncio.run(reference("governance_probe").collect_project(
        project, options, run=forbidden, force=True))
    assert result["governance_gaps"] and not result["governance_probes"]
    assert all(b["status"] != "enforced" for b in result["governance_health"]["bindings"])


@pytest.mark.governance_runtime
def test_native_package_digest_cannot_be_replaced_by_deployment_digest(tmp_path):
    from test_governance_wiring import module
    project, document, _, config, deployment, _ = generated(tmp_path)
    contract = {k: document[k] for k in ("framework", "governance", "tools")}
    gen = module("generate")
    gen.agent_image(project, contract, configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
    gen.bind(project, contract, configuration=deployment)
    for path in (project / ".threadlight/governance-package.json", project / "src/agent/governance-config.json"):
        value = json.loads(path.read_text())
        (value["configuration"] if "configuration" in value else value)["policy_digest"] = "sha256:" + "e" * 64
        path.write_text(json.dumps(value))
    assert reference("governance_static").check(project, document)["gaps"]


@pytest.mark.parametrize("fault", [
    "bundle", "envelope", "digest", "image", "version", "agent", "tenant", "native-association",
    "roles", "workload", "gateway-client", "agent-client", "endpoint", "policy-id", "missing-envelope",
])
@pytest.mark.governance_runtime
def test_real_gateway_staging_static_rejects_tamper(tmp_path, fault):
    project, document, _, _, _ = staged_gateway_project(tmp_path)
    if fault == "bundle":
        (project / "src/govern-gateway/policy/safe.rego").write_text("package bypass")
    elif fault in ("envelope", "missing-envelope"):
        path = project / "src/govern-gateway/policy-envelope.json"
        if fault == "missing-envelope":
            path.unlink()
        else:
            value = json.loads(path.read_text())
            value["envelope"]["content_digest"] = "sha256:" + "e" * 64
            path.write_text(json.dumps(value))
    else:
        path = project / ".threadlight/governance-deployment.json"
        value = json.loads(path.read_text())
        b = value["bindings"]
        if fault == "digest":
            b["policy_digest"] = "sha256:" + "e" * 64
        elif fault == "image":
            value["images"]["agent"] = value["images"]["agent"].replace("a" * 64, "e" * 64)
        elif fault == "version":
            b["agent_version"] = "2"
        elif fault == "agent":
            value["infrastructure"]["agent_id"] = "another-agent"
        elif fault == "tenant":
            b["gateway_config"]["tenant_id"] = "99999999-9999-9999-9999-999999999999"
        elif fault == "roles":
            b["gateway_config"]["approver_roles"] = ["OtherApprover"]
        elif fault == "workload":
            b["gateway_config"]["workloads"].clear()
        elif fault == "gateway-client":
            b["gateway_config"]["service_client_id"] = b["agent_client_id"]
        elif fault == "agent-client":
            b["agent_client_id"] = b["gateway_client"]
        elif fault == "endpoint":
            b["gateway_config"]["allowed_endpoints"] = ["https://other.example/action"]
        elif fault == "policy-id":
            b["gateway_config"]["policy_id"] = "other-policy"
        else:
            # A fresh digest alone cannot turn a native association into a GHCP policy.
            registry_path = project / "src/govern-gateway/policy/gateway-registry.json"
            registry = json.loads(registry_path.read_text())
            registry["native_policy_digest"] = "sha256:" + "e" * 64
            registry_path.write_text(json.dumps(registry))
        path.write_text(json.dumps(value))
        parameters = project / "infra/main.parameters.json"
        params = json.loads(parameters.read_text())
        for key, source in (("governanceBindings", "bindings"), ("governanceImages", "images"),
                            ("governanceConfig", "infrastructure")):
            params["parameters"][key]["value"] = value[source]
        parameters.write_text(json.dumps(params))
    assert reference("governance_static").check(project, document)["gaps"]


@pytest.mark.parametrize("fault", [
    "image", "version", "agent", "tenant", "native-association", "roles", "binding", "workload",
])
@pytest.mark.governance_runtime
def test_final_gateway_signed_digest_does_not_override_frozen_association(tmp_path, fault):
    import asyncio
    import base64
    import shutil
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import SignedBundle, canonical, envelope_digest, parse
    project, document, _, _, signer = staged_gateway_project(tmp_path)
    source = tmp_path / "bundle-input/source"
    registry_path = source / "gateway-registry.json"
    registry = json.loads(registry_path.read_text())
    if fault in ("image", "version", "agent"):
        field, value = {
            "image": ("image_digest", "sha256:" + "e" * 64),
            "version": ("agent_version", "2"), "agent": ("agent_id", "other-agent")}[fault]
        registry["deployment"][field] = value
    elif fault == "tenant":
        registry["tenant_id"] = "99999999-9999-9999-9999-999999999999"
    elif fault == "native-association":
        registry["native_policy_digest"] = "sha256:" + "e" * 64
    elif fault == "roles":
        registry["actions"][0]["approval_roles"] = ["OtherApprover"]
    elif fault == "binding":
        registry["actions"][0]["policy_binding"] = "other-policy"
    else:
        registry["actions"][0]["workloads"] = ["99999999-9999-9999-9999-999999999999"]
    registry_path.write_text(json.dumps(registry))
    replacement = bundle_module().build_bundle(source=source, destination=tmp_path / "replacement",
        policy_id="safe", version="1")
    target = project / "src/govern-gateway/policy"
    shutil.rmtree(target)
    shutil.copytree(replacement.root, target)
    envelope_path = project / "src/govern-gateway/policy-envelope.json"
    envelope = parse(SignedBundle, envelope_path.read_bytes()).envelope.model_copy(
        update={"content_digest": replacement.bundle_digest})
    envelope_path.write_bytes(canonical(SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())))
    path = project / ".threadlight/governance-deployment.json"
    deployment = json.loads(path.read_text())
    deployment["bindings"]["policy_digest"] = replacement.bundle_digest
    deployment["bindings"]["gateway_config"]["policy_digest"] = replacement.bundle_digest
    path.write_text(json.dumps(deployment))
    params_path = project / "infra/main.parameters.json"
    params = json.loads(params_path.read_text())
    params["parameters"]["governanceBindings"]["value"] = deployment["bindings"]
    params_path.write_text(json.dumps(params))
    # Content hashing and a genuine signature cannot bless a different runtime association.
    assert reference("governance_static").check(project, document)["gaps"]


@pytest.mark.parametrize("change", ["adapter", "bundle", "config", "service", "requires", "image", "docker", "bundle-loader"])
@pytest.mark.governance_runtime
def test_static_tamper_is_actionable_gap(tmp_path, change):
    project, document, _, config, deployment, _ = generated(tmp_path)
    if change == "adapter":
        (project / "src/agent/container.py").write_text("# no host")
    elif change == "bundle":
        (project / "src/agent/policy/safe.rego").write_text("package bypass")
    elif change == "config":
        path = project / "src/agent/governance-config.json"
        value = json.loads(path.read_text())
        value["control_plane_url"] = "https://other.example"
        path.write_text(json.dumps(value))
    elif change == "service":
        (project / "src/govern-control-plane/vendor/control-plane/app.py").unlink()
    elif change == "requires":
        document["tools"][0]["requires"] = ["approval"]
    elif change == "docker":
        docker = project / "src/agent/Dockerfile"
        docker.write_text(docker.read_text() + "\nRUN echo bypass > /app/container.py\n")
    elif change == "bundle-loader":
        (project / "src/agent/govern_bundle/policy_bundle.py").write_text("# bypass")
    else:
        from test_governance_wiring import module
        module("generate").agent_image(project, {k: document[k] for k in ("framework", "governance", "tools")}, configuration={
            "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
        path = project / "azure.yaml"
        path.write_text(path.read_text().replace("a" * 64, "b" * 64, 1))
    result = reference("governance_static").check(project, document)
    assert result["gaps"] and all(type(gap) is str for gap in result["gaps"])
    assert not any(b["status"] == "enforced" for b in result["bindings"])


def test_governance_off_and_absent_are_true_noops(tmp_path):
    gate = reference("governance_static")
    for document in ({}, {"governance": {"mode": "off"}}):
        assert gate.check(tmp_path, document) == {}


@pytest.mark.governance_runtime
def test_predeploy_keeps_existing_non_governance_gaps(tmp_path):
    project, document, path, *_ = generated(tmp_path)
    document["deployment_manifest"]["module_selectors"]["aca-bot"] = "yes"
    path.write_text(json.dumps(document))
    output = project / "gate.json"
    assert safe_check.phase_predeploy(project, path, output) == 1
    value = json.loads(output.read_text())
    assert any("aca-bot" in gap for gap in value["gaps"])
    assert value["governance_health"]["scope"] == "static-declarations-not-enforcement"


@pytest.mark.governance_runtime
def test_postdeploy_no_contract_never_automatically_invokes_and_keeps_gaps(tmp_path, monkeypatch):
    project, document, path, *_ = generated(tmp_path)
    document["deployment_manifest"]["expected_resource_types"] = ["Microsoft.Storage/storageAccounts"]
    path.write_text(json.dumps(document))
    monkeypatch.setattr(safe_check, "_az", empty_parent_azure)
    output = project / "post.json"
    assert safe_check.phase_postdeploy(path, output, "staging", repo_root=project) == 1
    report = json.loads(output.read_text())
    assert any("Microsoft.Storage/storageAccounts" in gap for gap in report["gaps"])
    assert report["governance_probes"] == []
    assert report["governance_gaps"]
    assert all("enforced" != b["status"] for b in report["governance_health"]["bindings"])


def test_collector_cli_is_runnable_and_off_is_no_network_noop(tmp_path):
    probe = reference("governance_probe")
    import asyncio
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/manifest.json").write_text(json.dumps({"governance": {"mode": "off"}}))
    def prohibited(*args, **kwargs):
        pytest.fail("off mode must not touch external dependencies")
    assert asyncio.run(probe.collect_project(tmp_path, tmp_path / "absent.json",
                                             run=prohibited)) == {}
    script = ROOT / "skills/threadlight-safe-check/references/governance_probe.py"
    result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--project" in result.stdout and "--configuration" in result.stdout


@pytest.mark.governance_runtime
def test_collect_project_has_no_automatic_probe_without_configuration(tmp_path):
    import asyncio
    project, _, *_ = generated(tmp_path)
    probe = reference("governance_probe")
    result = asyncio.run(probe.collect_project(
        project, project / "absent.json", run=lambda *args: pytest.fail("no declared probe")))
    assert result["governance_gaps"] and result["governance_probes"] == []


def test_skill_and_ci_cover_real_governance_not_local_badges():
    skill = (ROOT / "skills/threadlight-safe-check/SKILL.md").read_text()
    assert "probe_safe: true" in skill
    assert "governance_probe_noop" in skill
    assert "cannot certify business bindings" in skill
    assert "--force" in skill
    runner = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    for selector in ("skills/threadlight-safe-check/tests",
                     "test_collector_actual_copilot_invocations_mcp_gateway_fixture",
                     "test_postdeploy_runs_real_collector_and_retains_original_gaps"):
        assert selector in runner


@pytest.mark.parametrize("malformed", [None, False, [], "selective"])
@pytest.mark.governance_runtime
def test_invalid_governance_is_a_gap_not_an_uncaught_exception(tmp_path, malformed):
    project, document, path, *_ = generated(tmp_path)
    document["governance"] = malformed
    path.write_text(json.dumps(document))
    output = project / "bad.json"
    assert safe_check.phase_predeploy(project, path, output) == 1
    assert any("governance" in gap for gap in json.loads(output.read_text())["gaps"])
