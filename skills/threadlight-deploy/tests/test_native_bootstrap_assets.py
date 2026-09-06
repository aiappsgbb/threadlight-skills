"""Native signed asset validation with the actual ACS loader and frozen policy."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))


def implementation():
    path = ROOT / "skills/threadlight-govern/references/gateway/bootstrap_native.py"
    assert path.exists(), "native signed bootstrap asset validator missing"
    from test_probe_telemetry import gateway
    return gateway("bootstrap_native")


def fixture(tmp_path):
    from test_governance_quality import inputs
    from test_probe_telemetry import probe_registry, controller_config
    from test_control_plane import TENANT, WORKLOAD, APP, HUMAN, Harness
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    project, doc, package, deployment, signer = inputs(
        tmp_path, environment="preproduction", decisions={"pre_tool_call": {"decision": "allow"}})
    registry = probe_registry()
    registry["native_policy_digest"] = package["policy_digest"]
    registry["deployment"].update(agent_id=package["agent_id"], agent_version="17")
    native = Path(package["bundle_path"])
    source = tmp_path / "probe-source"
    source.mkdir()
    for name in ("manifest.yaml", "safe.rego"):
        shutil.copyfile(native / name, source / name)
    (source / "gateway-registry.json").write_bytes(canonical(registry))
    (tmp_path / "probe-assets").mkdir()
    probe = bundle_module().build_bundle(source=source, destination=tmp_path / "probe-assets/policy",
                                         policy_id="safe-probe", version="1")
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    envelope = BundleEnvelope(policy_id="safe-probe", version="1", content_digest=probe.bundle_digest,
        tenant_id=TENANT, key_id=package["key_id"], expires_at=expires)
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    root = probe.root.parent
    (root / "envelope.json").write_bytes(canonical(signed))
    h = Harness()
    config = {
        **h.settings.model_dump(mode="json"), "key_id": package["key_id"], "enabled": True,
        "producer": "native", "credential_mode": "platform-noop", "service_client_id": APP,
        "cosmos_url": "https://probe.documents.azure.com:443/", "cosmos_database": "governance",
        "cosmos_container": "probe-native", "bundle_path": "/mnt/governance-probe/policy",
        "signed_envelope_path": "/mnt/governance-probe/envelope.json",
        "policy_id": "safe-probe", "policy_version": "1", "policy_digest": probe.bundle_digest,
        "gateway_url": registry["gateway_url"],
        "allowed_endpoints": [registry["actions"][0]["endpoint"], registry["actions"][0]["outcome_endpoint"]],
        "expected_deployment": registry["deployment"], "probe_controllers": controller_config(),
        "workloads": {WORKLOAD: {"client_id": APP, "agent_id": package["agent_id"], "policies": ["safe-probe"]}},
    }
    asyncio.run(h.close())
    (root / "config.json").write_bytes(canonical(config))
    binding = {
        "schema": "threadlight-hosted-bootstrap/v1", "reference": "attempt-1", "tenant_id": TENANT,
        "principal": WORKLOAD, "client_id": APP, "key_id": package["key_id"], "policy_id": "safe",
        "policy_version": "1", "policy_digest": package["policy_digest"],
        "native_policy_digest": package["policy_digest"], "config_digest": "sha256:" + "a" * 64,
        "project_endpoint": "https://test.services.ai.azure.com/api/projects/test",
        **registry["deployment"], "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (expires - timedelta(seconds=30)).isoformat(),
    }
    constraints = {
        "audience": config["audience"], "cosmos_url": config["cosmos_url"],
        "cosmos_database": "governance", "gateway_url": config["gateway_url"],
        "allowed_endpoints": list(config["allowed_endpoints"]), "fixture_scope": registry["actions"][0]["credential_scope"],
        "policy_id": "safe-probe", "policy_version": "1",
        "controller_digest": "sha256:" + hashlib.sha256(canonical({
            key: {name: value[name] for name in ("client_id", "actions")}
            for key, value in config["probe_controllers"].items()})).hexdigest(),
    }
    return root, native, binding, constraints, signer, config


@pytest.mark.governance_runtime
@pytest.mark.parametrize("mutation", [None, "version", "identity", "cosmos", "endpoint", "controller", "policy-code"])
def test_native_assets_reuse_frozen_code_exact_identity_and_registered_endpoints(tmp_path, mutation):
    mod = implementation()
    root, native, binding, constraints, signer, config = fixture(tmp_path)
    from govern_control_plane.models import canonical, parse
    from govern_control_plane.bootstrap import BootstrapBinding
    if mutation == "version":
        config["expected_deployment"]["agent_version"] = "18"
    elif mutation == "identity":
        config["service_client_id"] = "99999999-9999-9999-9999-999999999999"
    elif mutation == "cosmos":
        config["cosmos_url"] = "https://other.documents.azure.com:443/"
    elif mutation == "endpoint":
        config["allowed_endpoints"][0] = "https://other.example/governance/noop"
    elif mutation == "controller":
        config["probe_controllers"] = {}
    elif mutation == "policy-code":
        (root / "policy/safe.rego").write_text("package replaced\nimport rego.v1\nallow := true\n")
    (root / "config.json").write_bytes(canonical(config))
    async def scenario():
        result = await mod.validate_assets(root, native_bundle=native,
            binding=parse(BootstrapBinding, canonical(binding)), constraints=constraints, signer=signer)
        assert result.credential_mode == "platform-noop"
        assert result.downstream_client_id is None
        assert result.service_client_id == binding["client_id"]
    if mutation is None:
        asyncio.run(scenario())
    else:
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(scenario())


@pytest.mark.governance_runtime
def test_generated_native_host_prepares_remote_data_without_mounts(tmp_path, monkeypatch):
    from test_governance_wiring import module as generated
    from test_bootstrap_assets import descriptors
    from test_remote_bootstrap import harness
    from test_control_plane import module as cp
    from test_runtime_provider import contract
    from govern_control_plane.models import canonical, parse, BundleEnvelope
    from govern_control_plane.bootstrap import BootstrapBinding
    from types import SimpleNamespace
    root, native, binding, constraints, signer, producer = fixture(tmp_path)
    agent = tmp_path / "standalone-agent"
    agent.mkdir()
    generated("generate").copy_sources(agent)
    reference = ROOT / "skills/threadlight-deploy/references/governance"
    shutil.copyfile(reference / "maf-container.py", agent / "container.py")
    shutil.copyfile(reference / "audit_delivery.py", agent / "audit_delivery.py")
    shutil.copytree(native, agent / "policy")
    monkeypatch.syspath_prepend(str(agent))
    spec = importlib.util.spec_from_file_location("remote_native_assets_host", agent / "container.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    config = {
        "tenant_id": binding["tenant_id"], "agent_id": binding["agent_id"], "key_id": binding["key_id"],
        "policy_id": "safe", "policy_version": "1", "policy_digest": binding["native_policy_digest"],
        "contract": contract(), "environment": "preproduction",
        "control_plane_url": "https://control.example", "control_plane_scope": "api://governance/.default",
        "probe_observability": {"enabled": True, "configuration_file": "/mnt/governance-probe/config.json"},
        "remote_bootstrap": {
            **{key: binding[key] for key in (
                "reference", "project_endpoint", "subscription", "resource_group", "native_policy_digest")},
            "native_probe": constraints,
        },
    }
    env = {"FOUNDRY_AGENT_NAME": binding["agent_id"], "FOUNDRY_AGENT_VERSION": "17",
           "FOUNDRY_PROJECT_ENDPOINT": binding["project_endpoint"], "TL_GOV_IMAGE_DIGEST": binding["image_digest"],
           "TL_GOV_SPOOL_DIR": str(tmp_path / "spool"), "GOV_CONTROL_PLANE_URL": config["control_plane_url"]}
    async def scenario():
        h = await harness()
        try:
            h.store.blobs.clear()
            settings = cp("auth").Settings.model_validate({
                **h.settings.model_dump(), "key_id": binding["key_id"], "workloads": {
                    binding["principal"]: {"client_id": binding["client_id"], "agent_id": binding["agent_id"],
                                           "policies": ["safe", "safe-probe"]}}})
            h.service.settings = settings
            h.service.signer = signer
            h.app.state.auth = cp("auth").EntraAuth(settings, h.http)
            native_signed = await h.service.publish(parse(BundleEnvelope, canonical({
                "tenant_id": binding["tenant_id"], "key_id": binding["key_id"],
                "policy_id": "safe", "version": "1", "content_digest": binding["native_policy_digest"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()})))
            (agent / "policy-envelope.json").write_bytes(canonical(native_signed))
            files = {file.relative_to(root).as_posix(): file.read_bytes()
                     for file in root.rglob("*") if file.is_file()}
            signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical({
                **binding, "config_digest": "sha256:" + hashlib.sha256(canonical(config)).hexdigest(),
                "native_probe_assets": descriptors(files)})), assets=files)
            class Credential:
                async def get_token(self, scope):
                    return SimpleNamespace(token=h.token())
            gate = host.remote_host(config, env=env, credential=Credential(), signer=signer, http=h.client)
            assert callable(getattr(gate, "prepare", None)), "native host does not prepare signed remote assets"
            await gate.prepare(signed, gate.resources)
            materialized = list((tmp_path / "spool/bootstrap").iterdir())
            assert len(materialized) == 1
            assert (materialized[0] / "policy/safe.rego").read_bytes() == (native / "safe.rego").read_bytes()
            assert not gate.active and gate.app is None
            await gate.aclose()
            assert not materialized[0].exists()
        finally:
            await h.close()
    asyncio.run(scenario())


@pytest.mark.governance_runtime
def test_native_binding_accepts_observed_platform_credential_not_downstream_uami(tmp_path):
    from test_governance_wiring import module as generated
    from test_runtime_provider import contract
    root, native, binding, constraints, signer, producer = fixture(tmp_path)
    configuration = {
        "probe_runtime_configuration": producer, "probe_bundle": str(root / "policy"),
        "probe_signed_envelope": str(root / "envelope.json"),
        "observations": {"foundation": {"cosmos_url": producer["cosmos_url"]}},
    }
    bindings = {
        "agent_version": binding["agent_version"], "agent_client_id": binding["client_id"],
        "agent_principal": binding["principal"], "policy_digest": binding["native_policy_digest"],
        "downstream_client": "99999999-9999-9999-9999-999999999999",
        "probe_controllers": producer["probe_controllers"],
    }
    packaged = {**{key: binding[key] for key in (
        "agent_id", "tenant_id", "key_id", "environment", "subscription", "resource_group")},
        "approver_roles": ["Approver"]}
    document = contract()
    document["tools"][0]["id"] = "governance_probe_noop"
    result = generated("generate").native_probe_binding(
        configuration, bindings, packaged,
        {"agent": "fixture.azurecr.io/agent@" + binding["image_digest"]}, document)
    assert result["status"] == "declared-unverified"


@pytest.mark.governance_runtime
def test_collector_native_platform_mode_preserves_separate_fixture_writer(tmp_path):
    import importlib
    from govern_gateway.probe_runtime import ProbeConfiguration
    from govern_control_plane.models import canonical, parse
    collector = importlib.import_module("skills.threadlight-safe-check.references.governance_probe")
    assert callable(getattr(collector, "validate_fixture_identity", None)), "native collector identity contract missing"
    root, native, binding, constraints, signer, producer = fixture(tmp_path)
    identities = {"agent_principal": binding["principal"], "agent_client_id": binding["client_id"],
                  "downstream_principal": "99999999-9999-9999-9999-999999999999",
                  "downstream_client": "88888888-8888-8888-8888-888888888888"}
    separate = {**producer, "producer": "fixture", "credential_mode": "separate-managed-identity",
                "cosmos_container": "probe-fixture", "service_client_id": "77777777-7777-7777-7777-777777777777",
                "fixture_callers": {binding["principal"]: binding["client_id"]}}
    p = parse(ProbeConfiguration, canonical(producer))
    f = parse(ProbeConfiguration, canonical(separate))
    collector.validate_fixture_identity(p, f, identities, native=True)
    for change in (
        {"service_client_id": binding["client_id"]},
        {"fixture_callers": {**f.fixture_callers, identities["downstream_principal"]: identities["downstream_client"]}},
    ):
        with pytest.raises(ValueError):
            collector.validate_fixture_identity(
                p, parse(ProbeConfiguration, canonical({**separate, **change})), identities, native=True)
    with pytest.raises(ValueError):
        collector.validate_fixture_identity(p, f, identities, native=False)
