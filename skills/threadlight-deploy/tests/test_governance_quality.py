"""Task10 quality regressions using the published runtime and real service protocols."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from test_governance_wiring import ROOT, REFERENCES, contract, deployment_fixture, module

sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))


@pytest.fixture(autouse=True)
def sdk_log_policy_isolation():
    """Only the harness restores lifetime policy, after native readers are joined."""
    import logging
    import threading
    original_get_logger = logging.Manager.getLogger
    filters = {name: list(logger.filters)
               for name, logger in logging.Logger.manager.loggerDict.copy().items()
               if isinstance(logger, logging.Logger)}
    yield
    readers = [thread for thread in threading.enumerate()
               if getattr(getattr(thread, "_target", None), "__module__", "").startswith("copilot.")]
    assert not readers, "native SDK threads must finish before the harness removes privacy filters"
    logging.Manager.getLogger = original_get_logger
    for name, logger in logging.Logger.manager.loggerDict.copy().items():
        if isinstance(logger, logging.Logger) and (
                name == "copilot.client" or name.startswith("copilot.client.")
                or name == "copilot._jsonrpc" or name.startswith("copilot._jsonrpc.")):
            logger.filters[:] = filters.get(name, [])


def snapshot(project):
    return {
        str(p.relative_to(project)): (
            ("link", p.readlink().as_posix()) if p.is_symlink()
            else ("dir", p.stat().st_mode) if p.is_dir()
            else ("file", p.stat().st_mode, p.read_bytes()))
        for p in project.rglob("*")
    }


def inputs(path, *, framework="microsoft-agent-framework", environment="production",
           requires=(), points=("pre_tool_call",), decisions=None, registry_change=None):
    from test_runtime_provider import build_policy
    from test_policy_bundle import bundle_module
    from test_control_plane import TestSigner
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    from test_gateway import registry

    built = build_policy(path / "bundle-input", decisions)
    document = contract(framework)
    document["tools"][0].update(requires=list(requires), intervention_points=list(points))
    configuration = {
        "agent_service": "agent", "agent_id": "test-agent", "environment": environment,
        "policy_id": "safe", "policy_version": "1", "bundle_path": str(built.root),
        "policy_digest": built.bundle_digest, "signed_envelope": str(path / "signed.json"),
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "key_id": "https://testvault.vault.azure.net/keys/policy/" + "a" * 32,
        "control_plane_scope": "api://22222222-2222-2222-2222-222222222222/.default",
        "gateway_scope": "api://33333333-3333-3333-3333-333333333333/.default",
        "control_plane_url": "https://fixture-control.fixture.azurecontainerapps.io",
        "gateway_url": "https://fixture-gateway.fixture.azurecontainerapps.io/mcp",
        "approver_roles": ["Approver"],
        "network": {
            "posture": "public-pilot", "allowed_ips": ["192.0.2.10/32"],
            "environment_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/fixture/providers/Microsoft.App/managedEnvironments/test",
        },
    }
    deployment = deployment_fixture(configuration)
    deployment["infrastructure"].update(environment=environment, runtime=framework)
    if framework == "github-copilot-sdk":
        configuration.update(
            mcp_servers={"mixed": {"type": "http", "url": "https://original.example/mcp",
                                  "tools": ["act", "read"]}},
            mcp_bindings={"act": {"server": "mixed", "tool": "act"}})
        registered = registry()
        registered.update(tenant_id=configuration["tenant_id"], gateway_url=configuration["gateway_url"])
        registered["deployment"].update(
            agent_id=configuration["agent_id"], environment=environment,
            image_digest=deployment["images"]["agent"].split("@")[1])
        action = registered["actions"][0]
        action.update(name="act", workloads=[deployment["bindings"]["agent_principal"]],
                      endpoint=deployment["bindings"]["allowed_endpoints"][0],
                      outcome_endpoint=deployment["bindings"]["allowed_endpoints"][1],
                      approval_roles=["Approver"] if requires else [],
                      post_policy_binding="safe" if "post_tool_call" in points else None)
        if registry_change:
            registry_change(registered)
        (path / "bundle-input/source/gateway-registry.json").write_text(json.dumps(registered))
        built = bundle_module().build_bundle(
            source=path / "bundle-input/source", destination=path / "gateway-bundle",
            policy_id="safe", version="1")
        configuration.update(bundle_path=str(built.root), policy_digest=built.bundle_digest)
        deployment["bindings"]["policy_digest"] = built.bundle_digest
        deployment["gateway_bundle"] = str(built.root)
    signer = TestSigner()
    envelope = BundleEnvelope(
        policy_id="safe", version="1", content_digest=built.bundle_digest,
        tenant_id=configuration["tenant_id"], key_id=configuration["key_id"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    Path(configuration["signed_envelope"]).write_bytes(canonical(signed))
    project = path / "pilot"
    agent = project / "src/agent"
    agent.mkdir(parents=True)
    (agent / "governance_application.py").write_text(
        "from agent_framework import tool\n"
        "effects = []\nmiddleware = []\n"
        "@tool(approval_mode='never_require')\n"
        "def act() -> str:\n    '''Act.'''\n    effects.append('act')\n    return 'changed'\n"
        "tools = [act]\n"
        "def safe_evidence(identity):\n    return {'verified': True}\n")
    (agent / "copilot-instructions.md").write_text("Call selected tools.")
    (agent / "untouched.txt").write_text("preserve user source")
    (project / "azure.yaml").write_text(
        "name: pilot\nservices:\n  agent:\n    host: azure.ai.agent\n"
        "    project: ./src/agent\nhooks:\n  postdeploy:\n    run: echo keep\n")
    return project, document, configuration, deployment, signer


def package(inputs):
    project, document, config, deployment, _ = inputs
    generator = module("generate")
    generator.generate(project, document, configuration=config)
    generator.agent_image(project, document, configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
    if document["framework"] == "github-copilot-sdk":
        staged = generator.stage_gateway(project, document, configuration={
            "gateway_bundle": config["bundle_path"], "policy_digest": config["policy_digest"],
            "signed_envelope": config["signed_envelope"], "agent_image": deployment["images"]["agent"]})
        deployment["gateway_source_digest"] = staged["gateway_source_digest"]
    return generator


@pytest.mark.parametrize("environment", [None, "", "prod", "development", "staging", "preproduction"])
def test_quality_bind_environment_cannot_downgrade_frozen_production(tmp_path, environment):
    data = inputs(tmp_path)
    generator = package(data)
    project, document, _, deployment, _ = data
    if environment is None:
        deployment["infrastructure"].pop("environment")
    else:
        deployment["infrastructure"]["environment"] = environment
    before = snapshot(project)
    with pytest.raises(ValueError, match="environment"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


def test_quality_bind_development_package_cannot_claim_production(tmp_path):
    data = inputs(tmp_path, environment="development")
    generator = package(data)
    project, document, _, deployment, _ = data
    deployment["infrastructure"]["environment"] = "production"
    before = snapshot(project)
    with pytest.raises(ValueError, match="environment"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


@pytest.mark.parametrize("field,value", [
    ("policy_id", "other-policy"), ("policy_version", "2"), ("approver_roles", ["OtherApprover"]),
])
def test_quality_bind_frozen_metadata_not_independent_service_authority(tmp_path, field, value):
    data = inputs(tmp_path, requires=["human-approval-record"])
    generator = package(data)
    project, document, _, deployment, _ = data
    if field == "approver_roles":
        deployment["infrastructure"][field] = value
    else:
        deployment["bindings"][field] = value
        if field == "policy_id":
            for workloads in ("control_workloads", "gateway_workloads"):
                for workload in deployment["bindings"][workloads].values():
                    workload["policies"] = [value]
    before = snapshot(project)
    with pytest.raises(ValueError, match="frozen|policy|approval"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


@pytest.mark.parametrize("field,value", [
    ("environment", "development"), ("policy_id", "other-policy"), ("policy_version", "2"),
    ("approver_roles", ["OtherApprover"]),
])
def test_quality_bind_reads_actual_frozen_agent_config(tmp_path, field, value):
    data = inputs(tmp_path)
    generator = package(data)
    project, document, _, deployment, _ = data
    path = project / "src/agent/governance-config.json"
    frozen = json.loads(path.read_text())
    frozen[field] = value
    path.write_text(json.dumps(frozen))
    before = snapshot(project)
    with pytest.raises(ValueError, match="frozen|environment|policy|approval"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


@pytest.mark.parametrize("fault", ["post", "pre-id", "post-id", "approval", "environment"])
def test_quality_gateway_full_contract_rejects_signed_misconfiguration_before_writes(tmp_path, fault):
    def change(registry):
        action = registry["actions"][0]
        if fault == "post":
            action["post_policy_binding"] = None
        elif fault in ("pre-id", "post-id"):
            action["policy_binding" if fault == "pre-id" else "post_policy_binding"] = "unselected"
        elif fault == "approval":
            action["approval_roles"] = []
        else:
            registry["deployment"]["environment"] = "staging"
    data = inputs(tmp_path, framework="github-copilot-sdk",
        requires=["human_approval_record", "decision_receipt", "idempotency_or_transaction",
                  "signed_policy_bundle", "authorization", "output_mediation"],
        points=("pre_tool_call", "post_tool_call"),
        decisions={"pre_tool_call": {"decision": "allow"}, "post_tool_call": {"decision": "deny"}},
        registry_change=change)
    project, document, config, _, _ = data
    before = snapshot(project)
    with pytest.raises(ValueError, match="contract|binding|approval|environment"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


@pytest.mark.parametrize("fault", ["subscription", "parameters", "managed-dir", "symlink"])
def test_quality_generation_preflight_failure_leaves_entire_project_unchanged(tmp_path, fault):
    data = inputs(tmp_path)
    project, document, config, _, _ = data
    (project / "infra").mkdir()
    main = project / "infra/main.bicep"
    main.write_text("targetScope = 'resourceGroup'\n")
    if fault == "subscription":
        main.write_text("targetScope = 'subscription'\n")
    elif fault == "parameters":
        # Foundation must parse existing parameters before adding any modules.
        (project / "infra/main.parameters.json").write_text("{")
    elif fault == "managed-dir":
        (project / "src/agent/runtime").mkdir()
        (project / "src/agent/runtime/user.py").write_text("keep = True")
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (project / "src/agent/runtime").symlink_to(outside, target_is_directory=True)
    before = snapshot(project)
    generator = module("generate")
    with pytest.raises((ValueError, OSError)):
        if fault == "parameters":
            generator.foundation(project, document, configuration=data[3]["infrastructure"])
        else:
            generator.generate(project, document, configuration=config)
    assert snapshot(project) == before
    if fault == "subscription":
        main.write_text("targetScope = 'resourceGroup'\n")
        assert generator.generate(project, document, configuration=config)["status"] == "packaged-not-deployed"


def test_quality_generation_mid_publish_failure_rolls_back_only_our_writes(tmp_path, monkeypatch):
    data = inputs(tmp_path)
    project, document, config, _, _ = data
    before = snapshot(project)
    original = Path.replace
    failed = False
    def fail_one(source, target):
        nonlocal failed
        if Path(target) == project / "src/agent/governance-config.json" and not failed:
            failed = True
            # A cooperative writer's unrelated new file is not ours to delete.
            (project / "concurrent-user.txt").write_text("keep this")
            raise OSError("synthetic write failure")
        return original(source, target)
    monkeypatch.setattr(Path, "replace", fail_one)
    with pytest.raises(OSError, match="synthetic write failure"):
        module("generate").generate(project, document, configuration=config)
    after = snapshot(project)
    assert after.pop("concurrent-user.txt")[2] == b"keep this"
    assert after == before and failed


def test_quality_rollback_io_failure_keeps_recoverable_originals(tmp_path, monkeypatch):
    data = inputs(tmp_path)
    project, document, config, _, _ = data
    original_yaml = (project / "azure.yaml").read_bytes()
    original = Path.replace
    def failure(source, target):
        target = Path(target)
        if target == project / "src/agent/governance-config.json":
            raise OSError("publish failed")
        if target == project / "azure.yaml" and "backups" in source.parts:
            raise OSError("restore failed")
        return original(source, target)
    monkeypatch.setattr(Path, "replace", failure)
    with pytest.raises(OSError, match="generation_rollback_conflict_recovery"):
        module("generate").generate(project, document, configuration=config)
    recovery = list(project.glob(".governance-transaction-*/backups/azure.yaml"))
    assert len(recovery) == 1 and recovery[0].read_bytes() == original_yaml


def test_quality_gateway_rollback_restores_old_empty_directories(tmp_path, monkeypatch):
    data = inputs(tmp_path, framework="github-copilot-sdk")
    generator = package(data)
    project, document, config, deployment, _ = data
    obsolete = project / "src/govern-gateway/policy/obsolete"
    (obsolete / "nested").mkdir(parents=True)
    before = snapshot(project)
    original = Path.rmdir
    def failure(path):
        if path == obsolete:
            raise OSError("remove failed")
        return original(path)
    monkeypatch.setattr(Path, "rmdir", failure)
    with pytest.raises(OSError, match="remove failed"):
        generator.stage_gateway(project, document, configuration={
            "gateway_bundle": config["bundle_path"], "policy_digest": config["policy_digest"],
            "signed_envelope": config["signed_envelope"], "agent_image": deployment["images"]["agent"]})
    assert snapshot(project) == before


@pytest.mark.parametrize("command", ["stage_gateway", "bind"])
@pytest.mark.parametrize("fault", ["post", "approval", "environment", "identity", "expired"])
def test_quality_final_gateway_registry_revalidated_at_each_boundary(tmp_path, command, fault):
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import SignedBundle, canonical, envelope_digest, parse
    data = inputs(tmp_path, framework="github-copilot-sdk", requires=["approval"],
        points=("pre_tool_call", "post_tool_call"),
        decisions={"pre_tool_call": {"decision": "allow"}, "post_tool_call": {"decision": "deny"}})
    generator = package(data)
    project, document, config, deployment, signer = data
    source = tmp_path / "bundle-input/source"
    registry = json.loads((source / "gateway-registry.json").read_text())
    if fault == "post":
        registry["actions"][0]["post_policy_binding"] = None
    elif fault == "approval":
        registry["actions"][0]["approval_roles"] = []
    elif fault == "environment":
        registry["deployment"]["environment"] = "staging"
    (source / "gateway-registry.json").write_text(json.dumps(registry))
    built = bundle_module().build_bundle(source=source, destination=tmp_path / "replacement",
                                         policy_id="safe", version="1")
    signed = parse(SignedBundle, Path(config["signed_envelope"]).read_bytes())
    changes = {"content_digest": built.bundle_digest}
    if fault == "identity":
        changes["policy_id"] = "other"
    if fault == "expired":
        changes["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    envelope = signed.envelope.model_copy(update=changes)
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    Path(config["signed_envelope"]).write_bytes(canonical(signed))
    if command == "bind":
        import shutil
        current = project / "src/govern-gateway"
        shutil.rmtree(current / "policy")
        shutil.copytree(built.root, current / "policy")
        (current / "policy-envelope.json").write_bytes(canonical(signed))
        deployment.update(gateway_bundle=str(built.root), gateway_source_digest=generator.tree_digest(current))
        deployment["bindings"]["policy_digest"] = built.bundle_digest
        options = deployment
    else:
        options = {
            "gateway_bundle": str(built.root), "policy_digest": built.bundle_digest,
            "signed_envelope": config["signed_envelope"], "agent_image": deployment["images"]["agent"]}
    before = snapshot(project)
    with pytest.raises(ValueError, match="binding|approval|environment|policy"):
        getattr(generator, command)(project, document, configuration=options)
    assert snapshot(project) == before


@pytest.mark.parametrize("requires,points", [
    (["operator-review"], ("pre_tool_call",)),
    (["output-mediation"], ("pre_tool_call",)),
    ([], ("post_tool_call",)), ([], ("pre_model_call",)),
])
def test_quality_unrepresentable_gateway_contract_rejected(tmp_path, requires, points):
    data = inputs(tmp_path, framework="github-copilot-sdk", requires=requires, points=points,
                  decisions={point: {"decision": "allow"} for point in points})
    project, document, config, _, _ = data
    before = snapshot(project)
    with pytest.raises(ValueError, match="unsupported"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


@pytest.mark.parametrize("token", ["audit", "durable_audit", "decision_receipt",
                                   "HUMAN_APPROVAL_RECORD", "signed_policy_bundle"])
def test_quality_portable_requirements_are_normalized_not_dropped(tmp_path, token):
    data = inputs(tmp_path, requires=[token])
    package(data)
    portable = json.loads((data[0] / "src/agent/governance-config.json").read_text())
    assert portable["contract"]["tools"][0]["requires"] == [token.lower().replace("_", "-")]
    assert data[1]["tools"][0]["requires"] == [token], "do not mutate the caller contract"


@pytest.mark.parametrize("token", ["idempotency", "idempotency-or-transaction"])
def test_quality_maf_cannot_claim_gateway_transaction_requirements(tmp_path, token):
    data = inputs(tmp_path, requires=[token])
    project, document, config, _, _ = data
    before = snapshot(project)
    with pytest.raises(ValueError, match="unsupported"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_quality_bound_control_plane_accepts_frozen_approval_scope(tmp_path):
    import httpx
    from test_control_plane import Harness, intent
    from govern_control_plane.auth import Settings, EntraAuth
    from govern_control_plane.app import ControlPlane, create_app
    from govern_control_plane.models import SignedBundle, canonical, parse
    data = inputs(tmp_path, requires=["approval"])
    generator = package(data)
    project, document, config, deployment, _ = data
    generator.bind(project, document, configuration=deployment)
    body = json.loads((project / ".threadlight/governance-deployment.json").read_text())
    settings = parse(Settings, canonical({key: value for key, value in body["bindings"]["control_config"].items()
                                         if key in Settings.model_fields}))
    async def run():
        h = Harness()
        service = ControlPlane(settings, h.store, h.signer)
        signed = parse(SignedBundle, Path(config["signed_envelope"]).read_bytes())
        await service.publish(signed.envelope)
        app = create_app(service=service, auth=EntraAuth(settings, h.http))
        try:
            principal = deployment["bindings"]["agent_principal"]
            scope = {**intent(), "principal": principal, "agent_id": config["agent_id"],
                     "tenant": config["tenant_id"], "policy_hash": config["policy_digest"],
                     "allowed_roles": config["approver_roles"],
                     "policy_expires_at": signed.envelope.expires_at.isoformat()}
            token = h.token(changes={
                "oid": principal, "azp": deployment["bindings"]["agent_client_id"], "aud": settings.audience})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=config["control_plane_url"]) as client:
                response = await client.post("/approvals/resolve", headers={"Authorization": "Bearer " + token},
                                             json={"operation": "request", "intent": scope})
                assert response.status_code == 202, response.text
                scope["allowed_roles"] = ["OtherApprover"]
                response = await client.post("/approvals/resolve", headers={"Authorization": "Bearer " + token},
                                             json={"operation": "request", "intent": scope})
                assert response.status_code == 403
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("approved,post_decision", [(False, "allow"), (True, "deny"), (True, "allow")])
def test_quality_bound_native_gateway_approval_and_post_policy_effects(tmp_path, approved, post_decision):
    import httpx
    from test_control_plane import Harness, MemoryStore
    from test_gateway import Credential
    from govern_control_plane.auth import Settings, EntraAuth
    from govern_control_plane.app import ControlPlane, create_app
    from govern_control_plane.models import SignedBundle, canonical, parse
    from govern_gateway.dispatcher import NativePolicy, GovernedDispatcher, DownstreamClient
    from govern_gateway.receipts import HTTPControlPlaneApprovalService, ReceiptClient

    data = inputs(tmp_path, framework="github-copilot-sdk",
        requires=["human_approval_record", "decision_receipt", "idempotency",
                  "signed_policy_bundle", "authorization", "output_mediation"],
        points=("pre_tool_call", "post_tool_call"),
        decisions={"pre_tool_call": {"decision": "allow"}, "post_tool_call": {"decision": post_decision}})
    project, document, config, deployment, signer = data
    # The action's intent stays narrower than the configured service role set.
    config["approver_roles"].append("OtherApprover")
    deployment["infrastructure"]["approver_roles"].append("OtherApprover")
    generator = package(data)
    assert generator.bind(project, document, configuration=deployment)["status"] == "bound-unverified"
    body = json.loads((project / ".threadlight/governance-deployment.json").read_text())["bindings"]

    def settings(which):
        return parse(Settings, canonical({key: value for key, value in body[which].items()
                                         if key in Settings.model_fields}))

    async def run():
        h = Harness()
        cp_settings, gw_settings = settings("control_config"), settings("gateway_config")
        service = ControlPlane(cp_settings, h.store, h.signer)
        signed = parse(SignedBundle, Path(config["signed_envelope"]).read_bytes())
        await service.publish(signed.envelope)
        app = create_app(service=service, auth=EntraAuth(cp_settings, h.http))
        control_token = h.token(changes={"aud": cp_settings.audience,
            "oid": body["gateway_principal"], "azp": body["gateway_client"]})
        human_token = h.token(human=True, changes={"aud": cp_settings.audience,
            "oid": cp_settings.approver_subjects[0], "azp": cp_settings.human_clients[0]})
        approval_requests, effects = [], []

        class Transport(httpx.ASGITransport):
            async def handle_async_request(self, request):
                response = await super().handle_async_request(request)
                if request.url.path == "/approvals/resolve" and request.method == "POST":
                    operation = json.loads(request.content)
                    if operation["operation"] == "request":
                        assert response.status_code == 202
                        approval_requests.append(operation["intent"])
                        decision = await control.post("/approvals/resolve",
                            headers={"Authorization": "Bearer " + human_token}, json={
                                "operation": "decide", "intent": operation["intent"],
                                "approved": approved, "approving_role": "Approver"})
                        assert decision.status_code == 200, decision.text
                return response

        async with httpx.AsyncClient(transport=Transport(app=app), base_url=config["control_plane_url"]) as control:
            receipts = ReceiptClient(base_url=config["control_plane_url"], scope=config["control_plane_scope"],
                                     credential=Credential(control_token), http=control)
            approvals = HTTPControlPlaneApprovalService(
                base_url=config["control_plane_url"], scope=config["control_plane_scope"],
                credential=Credential(control_token), http=control, poll_interval=0.01)
            policy = await NativePolicy.load(bundle_path=Path(config["bundle_path"]), signed=signed,
                signer=signer, tenant=config["tenant_id"], key_id=config["key_id"],
                policy_id=config["policy_id"], version=config["policy_version"],
                expected_digest=config["policy_digest"], allowed_endpoints=body["allowed_endpoints"],
                gateway_url=config["gateway_url"])
            def effect(request):
                if request.method == "POST":
                    assert approved and approval_requests
                    assert any(key.startswith("receipt:") for _, key in h.store.docs), "ACK precedes effect"
                    effects.append(json.loads(request.content))
                return httpx.Response(200, json={"receipt_id": "outcome-1", "result": {"status": "refunded"}})
            downstream = DownstreamClient(credential=Credential(), transport=httpx.MockTransport(effect))
            dispatcher = GovernedDispatcher(
                policy=policy, auth=EntraAuth(gw_settings, h.http), store=MemoryStore(),
                receipts=receipts, downstream=downstream, approvals=approvals,
                safe_provider=lambda identity: {"scope": "refunds", "verified": True},
                approval_principal=body["gateway_principal"], approval_agent_id=config["agent_id"])
            token = h.token(changes={"aud": gw_settings.audience, "oid": body["agent_principal"],
                                     "azp": body["agent_client_id"]})
            try:
                result = await dispatcher.dispatch(authorization="Bearer " + token, action="act",
                                                   arguments={"amount": 5}, idempotency_key="one")
                assert result["status"] == ("completed" if approved and post_decision == "allow" else "blocked")
                assert effects == ([{"amount": 5}] if approved else [])
                assert approval_requests[0]["allowed_roles"] == ["Approver"]
                if post_decision == "deny":
                    assert "result" not in result, "post-denied output must not escape"
                if result["status"] == "completed":
                    replay = await dispatcher.dispatch(authorization="Bearer " + token, action="act",
                        arguments={"amount": 5}, idempotency_key="one")
                    assert replay == result and len(effects) == 1
                assert all("amount" not in json.dumps(value, default=str) for value in h.store.docs.values())
            finally:
                await downstream.aclose()
                await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["raise", "timeout"])
def test_quality_native_copilot_stop_failure_kills_child_and_redacts_debug_logs(monkeypatch, caplog, fault):
    import logging
    import subprocess
    from copilot import CopilotClient
    ghcp = module("ghcp-container")
    monkeypatch.setattr(ghcp, "CLEANUP_TIMEOUT", 0.05)
    caplog.set_level(logging.DEBUG)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    client = CopilotClient()
    client._process = child
    class Transport:
        async def stop(self):
            if fault == "timeout":
                await asyncio.sleep(60)
            raise RuntimeError("PRIVATE TRANSPORT TOKEN")
    client._client = Transport()
    try:
        failures = asyncio.run(ghcp.close_invocation(
            unsubscribe=None, session=None, client=client, server=None, task=None, sock=None,
            http=None, credential=None))
        assert child.returncode is not None, "cleanup must reap, not merely signal, its owned process"
        child.wait(timeout=2)
        assert failures and child.poll() is not None
        assert "PRIVATE" not in caplog.text
        assert "governance_cleanup_" in caplog.text
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)


@pytest.mark.parametrize("fault", ["error", "timeout", "cancel", "normal"])
def test_quality_native_jsonrpc_disconnect_diagnostics(monkeypatch, caplog, fault):
    """Unmodified SDK request, frame reader, disconnect and stop over real stdio."""
    import logging
    import socket
    import subprocess
    import httpx
    from copilot import CopilotClient
    from copilot._jsonrpc import JsonRpcClient, JsonRpcError
    from copilot.session import CopilotSession

    ghcp = module("ghcp-container")
    monkeypatch.setattr(ghcp, "CLEANUP_TIMEOUT", 0.3)
    caplog.set_level(logging.DEBUG)
    marker = "PRIVATE DISCONNECT CREDENTIAL"
    child = subprocess.Popen([sys.executable, "-u", "-c", r"""
import json, sys, time
def send(frame):
    body = json.dumps(frame).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()
while header := sys.stdin.buffer.readline():
    size = int(header.decode().split(":")[1])
    assert sys.stdin.buffer.readline() == b"\r\n"
    request = json.loads(sys.stdin.buffer.read(size))
    if request["method"] == "ping":
        send({"jsonrpc": "2.0", "id": request["id"], "result": {}})
        continue
    assert request["method"] == "session.destroy"
    send({"jsonrpc": "2.0", "method": "fixture.destroy_started", "params": {}})
    if sys.argv[1] == "timeout":
        time.sleep(60)
    if sys.argv[1] == "cancel":
        header = sys.stdin.buffer.readline()
        size = int(header.decode().split(":")[1])
        assert sys.stdin.buffer.readline() == b"\r\n"
        release = json.loads(sys.stdin.buffer.read(size))
        assert release["method"] == "fixture.release"
        send({"jsonrpc": "2.0", "id": release["id"], "result": {}})
    if sys.argv[1] == "normal":
        response = {"result": {}}
    else:
        print("PRIVATE DISCONNECT CREDENTIAL", file=sys.stderr, flush=True)
        response = {"error": {"code": -32000, "message": "PRIVATE DISCONNECT CREDENTIAL",
                              "data": {"token": "PRIVATE DISCONNECT CREDENTIAL"}}}
    send({"jsonrpc": "2.0", "id": request["id"], **response})
    break
""", fault], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    async def run():
        transport = JsonRpcClient(child)
        transport.start()
        destroy_started = asyncio.Event()
        transport.set_notification_handler(lambda method, params: destroy_started.set())
        await asyncio.wait_for(transport.request("ping"), 2)
        session = CopilotSession(marker, transport)
        unsubscribe = session.on(lambda event: None)
        client = CopilotClient()
        client._process, client._client = child, transport
        client._sessions[session.session_id] = session
        http = httpx.AsyncClient()
        sock = socket.socket()
        server = SimpleNamespace(should_exit=False)
        closed = []

        class Credential:
            async def close(self):
                closed.append("credential")

        async def relay():
            try:
                while not server.should_exit:
                    await asyncio.sleep(0.001)
            finally:
                closed.append("relay")

        relay_task = asyncio.create_task(relay())
        closer = asyncio.create_task(ghcp.close_invocation(
            unsubscribe=unsubscribe, session=session, client=client, server=server,
            task=relay_task, sock=sock, http=http, credential=Credential()))
        if fault == "cancel":
            await asyncio.wait_for(destroy_started.wait(), 2)
            assert closer.cancel()
            await transport.request("fixture.release", timeout=2)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(closer, 4)
        else:
            failures = await asyncio.wait_for(closer, 4)
            assert failures == ([] if fault == "normal" else [
                ("disconnect", TimeoutError if fault == "timeout" else JsonRpcError)])
        assert session._destroyed and not session._event_handlers
        assert not client._sessions and client._client is None
        assert not transport.pending_requests and not transport._running
        assert child.returncode is not None
        assert sock.fileno() == -1 and http.is_closed and relay_task.done()
        assert set(closed) == {"relay", "credential"}
        for thread in (transport._read_thread, transport._stderr_thread):
            await asyncio.to_thread(thread.join, 1)
            assert not thread.is_alive()

    try:
        asyncio.run(run())
        rpc_records = [r for r in caplog.records if r.name == "copilot._jsonrpc"]
        assert rpc_records, "the native SDK must emit its own diagnostics"
        assert marker not in caplog.text
        assert marker not in repr([r.__dict__ for r in caplog.records])
        assert all(r.getMessage() == "governance_cleanup_sdk_transport_diagnostic"
                   for r in rpc_records)
        if fault in ("error", "cancel"):
            assert any(r.levelno == logging.WARNING
                       and r.funcName == "_log_request_timing"
                       and r.getMessage() == "governance_cleanup_sdk_transport_diagnostic"
                       for r in rpc_records)
        for record in rpc_records:
            if record.getMessage().startswith("governance_cleanup_"):
                assert record.args == () and record.exc_info is None
                assert record.exc_text is None and record.stack_info is None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()


@pytest.mark.parametrize("invocations", [1, 2])
def test_quality_native_late_stderr_thread_stays_private_after_close(caplog, invocations):
    """Pause before the unchanged SDK warning, past its pre-kill timed reader join."""
    import inspect
    import logging
    import socket
    import subprocess
    import threading
    import httpx
    from copilot import CopilotClient
    from copilot._jsonrpc import JsonRpcClient
    from copilot.session import CopilotSession

    ghcp = module("ghcp-container")
    caplog.set_level(logging.DEBUG)
    source, start = inspect.getsourcelines(JsonRpcClient._stderr_loop)
    warning_line = start + next(i for i, line in enumerate(source) if "logger.warning(" in line)
    gates, children, transports, records = {}, [], [], []
    trace_failures = []
    original_trace = threading.gettrace()
    logger = logging.getLogger("copilot._jsonrpc")

    class Capture(logging.Handler):
        def emit(self, record):
            records.append((record.__dict__.copy(), self.format(record)))

    handler = Capture()
    logger.addHandler(handler)

    def schedule(frame, event, arg):
        if (event == "line" and frame.f_code is JsonRpcClient._stderr_loop.__code__
                and frame.f_lineno == warning_line):
            entered, release = gates[frame.f_locals["self"].process.pid]
            entered.set()
            if not release.wait(20):
                trace_failures.append("stderr barrier timed out")
        return schedule

    async def run():
        owned, closers = [], []
        second_closing, finish_second = asyncio.Event(), asyncio.Event()
        try:
            for index in range(invocations):
                child = subprocess.Popen([sys.executable, "-u", "-c", r"""
import json, sys
while header := sys.stdin.buffer.readline():
    size = int(header.decode().split(":")[1])
    assert sys.stdin.buffer.readline() == b"\r\n"
    request = json.loads(sys.stdin.buffer.read(size))
    if request["method"] == "fixture.stderr":
        print("PRIVATE CLEANUP STDERR", file=sys.stderr, flush=True)
    else:
        assert request["method"] in ("ping", "session.destroy")
    body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()
"""], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                children.append(child)
                gates[child.pid] = (threading.Event(), threading.Event())
                transport = JsonRpcClient(child)
                transports.append(transport)
                transport.start()
                await asyncio.wait_for(transport.request("ping"), 2)
                await asyncio.wait_for(transport.request("fixture.stderr"), 2)
                assert await asyncio.to_thread(gates[child.pid][0].wait, 2)
                session = CopilotSession(f"PRIVATE SESSION {index}", transport)
                client = CopilotClient()
                client._process, client._client = child, transport
                client._sessions[session.session_id] = session
                server = SimpleNamespace(should_exit=False, closed=False)
                sock, http = socket.socket(), httpx.AsyncClient()

                async def relay(server=server):
                    try:
                        while not server.should_exit:
                            await asyncio.sleep(0.001)
                    finally:
                        server.closed = True

                class Credential:
                    def __init__(self, index):
                        self.index, self.closed = index, False

                    async def close(self):
                        if self.index == 1:
                            second_closing.set()
                            await finish_second.wait()
                        self.closed = True

                credential = Credential(index)
                relay_task = asyncio.create_task(relay())
                owned.append((server, sock, http, credential, relay_task, session, client))

            for server, sock, http, credential, relay_task, session, client in owned:
                closers.append(asyncio.create_task(ghcp.close_invocation(
                    unsubscribe=session.on(lambda event: None), session=session, client=client,
                    server=server, task=relay_task, sock=sock, http=http, credential=credential)))

            assert await asyncio.wait_for(asyncio.shield(closers[0]), 10) == []
            assert transports[0]._stderr_thread.is_alive(), "must exercise the native timed-join gap"
            assert children[0].returncode is not None, "owned subprocess must already be reaped"
            server, sock, http, credential, relay_task, _, _ = owned[0]
            assert sock.fileno() == -1 and http.is_closed and relay_task.done()
            assert server.closed and credential.closed
            if invocations == 2:
                await asyncio.wait_for(second_closing.wait(), 2)
                assert not closers[1].done(), "one invocation closes while another is still closing"
            gates[children[0].pid][1].set()
            await asyncio.to_thread(transports[0]._stderr_thread.join, 2)
            assert not transports[0]._stderr_thread.is_alive()
            assert "PRIVATE" not in repr(records) + caplog.text

            if invocations == 2:
                finish_second.set()
                assert await asyncio.wait_for(closers[1], 3) == []
                assert transports[1]._stderr_thread.is_alive()
                gates[children[1].pid][1].set()

            for child, transport, resources in zip(children, transports, owned):
                server, sock, http, credential, relay_task, session, client = resources
                for thread in (transport._read_thread, transport._stderr_thread):
                    await asyncio.to_thread(thread.join, 2)
                    assert not thread.is_alive()
                assert child.returncode is not None
                assert session._destroyed and not session._event_handlers
                assert not client._sessions and client._client is None
                assert not transport.pending_requests and not transport._running
                assert sock.fileno() == -1 and http.is_closed and relay_task.done()
                assert server.closed and credential.closed
            warnings = [record for record, _ in records if record["funcName"] == "_stderr_loop"]
            assert len(warnings) == invocations, "real native stderr warnings must reach handlers"
            assert all(record["msg"] == "governance_cleanup_sdk_transport_diagnostic"
                       for record in warnings)
            assert not trace_failures
            assert "PRIVATE" not in repr(records) + caplog.text
            logging.getLogger("application.normal").warning("application PRIVATE stays unchanged")
            assert "application PRIVATE stays unchanged" in caplog.text
        finally:
            finish_second.set()
            for _, release in gates.values():
                release.set()
            for closer in closers:
                if not closer.done():
                    await closer
            for server, sock, http, credential, relay_task, _, _ in owned:
                server.should_exit = True
                await relay_task
                sock.close()
                await http.aclose()
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=2)
            for transport in transports:
                transport._running = False
                for thread in (transport._read_thread, transport._stderr_thread):
                    await asyncio.to_thread(thread.join, 2)
                    assert not thread.is_alive()

    try:
        threading.settrace(schedule)
        asyncio.run(run())
    finally:
        threading.settrace(original_trace)
        logger.removeHandler(handler)
        for child in children:
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()


@pytest.mark.parametrize("logger_name", [
    "copilot._jsonrpc", "copilot._jsonrpc.wire", "copilot.client", "copilot.client.wire"])
@pytest.mark.parametrize("propagate", [False, True])
def test_quality_sdk_cleanup_redacts_before_user_handlers(monkeypatch, caplog, logger_name, propagate):
    """Lifetime privacy covers existing and newly-created child loggers at all levels."""
    import logging
    logger = logging.getLogger(logger_name)
    ghcp = module("ghcp-container")
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(logger, "propagate", propagate)
    records, formatted = [], []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.__dict__.copy())
            formatted.append(self.format(record))

    handler = Capture()
    logger.addHandler(handler)
    marker = "PRIVATE PAYLOAD"

    def emit_private(target=logger, level=logging.WARNING):
        try:
            raise ValueError(marker)
        except ValueError:
            target.log(level, "SDK %s", marker, exc_info=True, stack_info=True,
                       extra={"payload": {"token": marker}, "message_cache": marker})

    class Session:
        async def disconnect(self):
            emit_private()
            logging.getLogger("application.normal").info("normal process message")
            # A fresh worker thread does not inherit task ContextVars.
            await asyncio.get_running_loop().run_in_executor(None, emit_private)

    async def run():
        assert await ghcp.close_invocation(
            unsubscribe=None, session=Session(), client=None, server=None, task=None,
            sock=None, http=None, credential=None) == []

    try:
        logger.info("SDK PRIVATE before")
        asyncio.run(run())
        logger.debug("SDK PRIVATE after")
        cached = logger.makeRecord(logger.name, logging.ERROR, __file__, 0, "SDK %s",
                                   (marker,), None, extra={"payload": marker})
        cached.exc_text = cached.stack_info = marker
        logger.handle(cached)
        from uuid import uuid4
        late = logger.getChild("late_" + uuid4().hex)
        late.propagate = propagate
        late.addHandler(handler)
        try:
            for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR):
                emit_private(late, level)
        finally:
            late.removeHandler(handler)
        assert "normal process message" in caplog.text
        assert "PRIVATE" not in repr(records) + "\n".join(formatted) + caplog.text
        reason = ("governance_cleanup_sdk_transport_diagnostic" if "_jsonrpc" in logger_name
                  else "governance_cleanup_sdk_diagnostic")
        assert set(formatted) == {reason}
        for record in records:
            assert record["args"] == ()
            assert all(record.get(field) is None for field in (
                "exc_info", "exc_text", "stack_info", "payload", "message_cache"))
    finally:
        logger.removeHandler(handler)


def test_quality_sdk_lifetime_policy_installs_once_without_muting_application():
    import logging
    from concurrent.futures import ThreadPoolExecutor
    ghcp = module("ghcp-container")
    original = logging.Manager.getLogger
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: ghcp.install_sdk_log_privacy(), range(20)))
    module("ghcp-container")
    assert logging.Manager.getLogger is original
    for name in ("copilot.client", "copilot._jsonrpc", "copilot.client.new.child"):
        logger = logging.getLogger(name)
        for _ in range(10):
            assert logging.getLogger(name) is logger
        assert len([f for f in logger.filters if hasattr(f, "reason")]) == 1
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.__dict__.copy())

    handler = Capture()
    for name in ("application.normal", "copilot.client_other", "copilot._jsonrpc_other"):
        logger = logging.getLogger(name)
        assert not any(hasattr(f, "reason") for f in logger.filters)
        record = logger.makeRecord(name, logging.WARNING, __file__, 0, "application %s",
            ("PRIVATE unchanged",), (ValueError, ValueError("PRIVATE exception"), None),
            extra={"payload": "PRIVATE extra"})
        record.stack_info = "PRIVATE stack"
        expected = record.__dict__.copy()
        logger.addHandler(handler)
        try:
            logger.handle(record)
            assert captured[-1] == expected
        finally:
            logger.removeHandler(handler)


@pytest.mark.parametrize("environment,effects", [("development", ["act"]), ("production", []),
                                               ("preproduction", [])])
def test_quality_actual_foundry_native_environment_deny(tmp_path, monkeypatch, environment, effects):
    import httpx
    from agent_framework.foundry import FoundryChatClient
    from openai import AsyncOpenAI
    sys.path.insert(0, str(REFERENCES))
    monkeypatch.syspath_prepend(str(ROOT / "skills/threadlight-govern/references"))
    data = inputs(tmp_path, environment=environment)
    generator = package(data)
    project, document, _, deployment, signer = data
    assert generator.bind(project, document, configuration=deployment)["status"] == "bound-unverified"
    host_module = module("maf-container")
    agent_path = project / "src/agent"
    monkeypatch.setattr(host_module, "BASE", agent_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location("governance_application", agent_path / "governance_application.py")
    application = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(application)
    monkeypatch.setitem(sys.modules, "governance_application", application)
    config = json.loads((agent_path / "governance-config.json").read_text())
    config.update(principal=deployment["bindings"]["agent_principal"], agent_version="1",
                  image_digest=deployment["images"]["agent"].split("@")[1], spool_dir=str(tmp_path / "audit"))
    async def run():
        p = await host_module.build_provider(config, signer=signer, credential=object())
        calls = []
        def exchange(request):
            calls.append(json.loads(request.content))
            output = ([{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                        "name": "act", "arguments": "{}", "status": "completed"}]
                      if len(calls) == 1 else [{"type": "message", "id": "msg_1", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text", "text": "done", "annotations": []}]}])
            return httpx.Response(200, json={"id": "resp_1", "object": "response", "created_at": 0,
                "status": "completed", "model": "test", "output": output})
        async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as wire:
            model = FoundryChatClient(project_endpoint="https://model.invalid/api/projects/test",
                                      model="test", credential=object())
            model.client = AsyncOpenAI(base_url="https://model.invalid/v1/", api_key="synthetic", http_client=wire)
            host = host_module.build_host(p, client=model, configure_observability=None)
            try:
                assert host_module.readiness(p).status_code == 200
                await host._agent.run("Call act.")
                assert application.effects == effects
                assert p.mode == ("evaluate_only" if environment == "development" else "enforce")
            finally:
                await p.approval_resolver.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["disconnect", "disconnect-timeout", "stop", "stop-timeout",
                                   "unsubscribe", "create-session", "cancel", "normal",
                                   "relay", "relay-timeout", "credential", "self-cancel"])
def test_quality_native_ghcp_independent_cleanup(tmp_path, monkeypatch, caplog, fault):
    """Host fault isolation with transport doubles, not SDK diagnostic evidence."""
    import copilot
    from copilot.session import CopilotSession
    from copilot.session_events import SessionEventType
    import azure.identity.aio
    import uvicorn
    from starlette.requests import Request

    ghcp = module("ghcp-container")
    monkeypatch.setattr(ghcp, "__file__", str(tmp_path / "container.py"))
    monkeypatch.setattr(ghcp, "CLEANUP_TIMEOUT", 0.05, raising=False)
    (tmp_path / "copilot-instructions.md").write_text("test")
    for key, value in {
        "GOVERNED_TOOL_GATEWAY_URL": "https://gateway.example/mcp",
        "FOUNDRY_PROJECT_ENDPOINT": "https://model.invalid",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test",
    }.items():
        monkeypatch.setenv(key, value)
    state = SimpleNamespace(stop=False, force=False, socket=None, task=None, session=None)
    started = None
    class Credential:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.close()
        async def close(self):
            state.credential_closed = True
            if fault == "credential":
                raise RuntimeError("PRIVATE CREDENTIAL")
        async def get_token(self, scope):
            return SimpleNamespace(token="PRIVATE TOKEN", expires_on=9999999999)
    class Transport:
        async def request(self, method, params):
            if method == "session.destroy":
                if fault == "self-cancel":
                    raise asyncio.CancelledError("PRIVATE CANCELLATION")
                if fault == "disconnect-timeout":
                    await asyncio.sleep(60)
                if fault in ("disconnect", "cancel"):
                    raise RuntimeError("PRIVATE DISCONNECT")
    class Client:
        async def start(self): pass
        async def create_session(self, **kwargs):
            if fault == "create-session":
                raise RuntimeError("PRIVATE SETUP")
            session = CopilotSession("native-test", Transport())
            state.session = session
            original_on = session.on
            def on(handler):
                state.handler = handler
                unsubscribe = original_on(handler)
                def remove():
                    unsubscribe()
                    if fault == "unsubscribe":
                        raise RuntimeError("PRIVATE UNSUBSCRIBE")
                return remove
            session.on = on
            async def send(prompt):
                started.set()
                if fault == "cancel":
                    await asyncio.sleep(60)
                state.handler(SimpleNamespace(type=SessionEventType.SESSION_IDLE))
            session.send = send
            return session
        async def stop(self):
            state.stop = True
            if fault == "stop-timeout":
                await asyncio.sleep(60)
            if fault == "stop":
                raise RuntimeError("PRIVATE STOP")
        async def force_stop(self): state.force = True
    class Server:
        started = False
        should_exit = False
        def __init__(self, config): pass
        async def serve(self, sockets):
            state.socket = sockets[0]
            state.task = asyncio.current_task()
            self.started = True
            try:
                if fault == "relay-timeout":
                    await asyncio.sleep(60)
                while not self.should_exit:
                    await asyncio.sleep(0.001)
                if fault == "relay":
                    raise RuntimeError("PRIVATE RELAY")
            finally:
                state.relay_closed = True
    monkeypatch.setattr(copilot, "CopilotClient", Client)
    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(uvicorn, "Server", Server)
    async def run():
        nonlocal started
        started = asyncio.Event()
        host = ghcp.build_host({
            "mcp_servers": {"mixed": {"type": "http", "url": "https://original.example/mcp",
                                      "tools": ["act", "read"]}},
            "mcp_bindings": {"act": {"server": "mixed", "tool": "act"}},
            "contract": contract(), "gateway_scope": "api://test/.default",
        }, configure_observability=None)
        async def receive():
            return {"type": "http.request", "body": b'{"input":"test"}'}
        response = await host._invoke_fn(Request(
            {"type": "http", "state": {"invocation_id": "invocation-1"}}, receive))
        output, errors = [], []
        async def consume():
            try:
                async for item in response.body_iterator:
                    output.append(item)
            except Exception as error:
                errors.append(error)
        consumer = asyncio.create_task(consume())
        if fault == "cancel":
            await started.wait()
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
        elif fault == "self-cancel":
            with pytest.raises(asyncio.CancelledError):
                await consumer
        else:
            await asyncio.wait_for(consumer, 2)
        assert state.stop, "disconnect failure skipped CopilotClient.stop"
        assert state.socket.fileno() == -1 and state.task.done() and state.relay_closed
        assert not errors, "private cleanup exception escaped the stream sanitizer"
        assert state.credential_closed
        if fault in ("stop", "stop-timeout"):
            assert state.force, "use pinned force_stop if graceful stop fails"
        if state.session:
            assert state.session._destroyed and not state.session._event_handlers
        if fault == "normal":
            assert b"event: done" in b"".join(output)
        if fault not in ("normal", "create-session", "cancel"):
            assert "governance_cleanup_" in caplog.text
        assert "PRIVATE" not in b"".join(output).decode() + caplog.text
    asyncio.run(run())
