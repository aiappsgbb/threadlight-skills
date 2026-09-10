"""MAF remains Responses while static/collector authority follows its gateway path."""
import asyncio
import base64
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
import hashlib
import json
import re
import sys
from types import SimpleNamespace

import httpx
import pytest

from test_governance_gates import reference, staged_gateway_project
from test_governance_wiring import module

pytestmark = pytest.mark.governance_runtime


@pytest.fixture(autouse=True)
def current_deployment_sources(monkeypatch):
    # Exercise this checkout's generator; installed native SDKs/validators stay untouched.
    monkeypatch.setitem(sys.modules, "govern_deployment.generate", module("generate"))


@pytest.mark.parametrize("phase", ["bootstrap", "image", "staged", "bound"])
def test_maf_gateway_static_keeps_gateway_policy_trust_declarative(tmp_path, phase):
    project, document, config, deployment, _ = staged_gateway_project(
        tmp_path, phase=phase, framework="microsoft-agent-framework")
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == [], result
    assert result["scope"] == "static-declarations-not-enforcement"
    assert all(binding["status"] != "enforced" for binding in result["bindings"])
    assert next(binding for binding in result["bindings"] if binding["tool_id"] == "read")["status"] == "unbound"
    final = phase in ("staged", "bound")
    assert result["policy_trust"] == {
        "source": {"bootstrap": "bootstrap-package", "image": "bootstrap-package",
                   "staged": "staged-envelope", "bound": "deployment-binding"}[phase],
        "digest": deployment["bindings"]["policy_digest"] if final else config["policy_digest"],
        "signature_verified": False}
    if not final:
        assert "final-gateway-policy-not-staged" in result["unverified"]
    assert "effect-closure:act" in result["unverified"]


@pytest.mark.parametrize("fault", ["client-missing", "client-changed", "host-changed", "registry-image"])
def test_maf_gateway_static_rejects_changed_adapter_or_image(tmp_path, fault):
    project, document, _, _, _ = staged_gateway_project(
        tmp_path, framework="microsoft-agent-framework")
    if fault == "registry-image":
        path = project / ".threadlight/governance-deployment.json"
        deployment = json.loads(path.read_text())
        deployment["images"]["agent"] = "fixture.azurecr.io/agent@sha256:" + "f" * 64
        path.write_text(json.dumps(deployment))
    else:
        path = project / "src/agent" / ("container.py" if fault == "host-changed" else "maf_gateway.py")
        if fault == "client-missing":
            path.unlink()
        else:
            path.write_text("# changed adapter\n")
    result = reference("governance_static").check(project, document)
    assert result["gaps"]
    if fault.startswith("client"):
        assert any("maf_gateway.py" in gap for gap in result["gaps"])
    assert all(binding["status"] != "enforced" for binding in result["bindings"])


@pytest.mark.parametrize("framework", ["microsoft-agent-framework", "github-copilot-sdk"])
@pytest.mark.parametrize("enabled", [False, None, "true"])
def test_bound_gateway_static_requires_explicit_enabled_service(tmp_path, framework, enabled):
    project, document, _, _, _ = staged_gateway_project(tmp_path, framework=framework)
    path = project / ".threadlight/governance-deployment.json"
    deployment = json.loads(path.read_text())
    deployment["infrastructure"]["enable_gateway"] = enabled
    path.write_text(json.dumps(deployment))
    path = project / "infra/main.parameters.json"
    parameters = json.loads(path.read_text())
    parameters["parameters"]["governanceConfig"]["value"] = deployment["infrastructure"]
    path.write_text(json.dumps(parameters))
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == ["governance: gateway-service-not-enabled"], result
    assert all(binding["status"] != "enforced" for binding in result["bindings"])


def test_gateway_static_rejects_leftover_native_probe_permissions(tmp_path):
    project, document, _, _, _ = staged_gateway_project(
        tmp_path, framework="microsoft-agent-framework")
    path = project / ".threadlight/governance-deployment.json"
    deployment = json.loads(path.read_text())
    deployment["bindings"]["native_probe_config"] = {"producer": "native"}
    path.write_text(json.dumps(deployment))
    path = project / "infra/main.parameters.json"
    parameters = json.loads(path.read_text())
    parameters["parameters"]["governanceBindings"]["value"] = deployment["bindings"]
    path.write_text(json.dumps(parameters))
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == ["governance: gateway-native-probe-binding"], result


def test_maf_gateway_bootstrap_responses_checks_gateway_not_native_policy():
    from test_remote_bootstrap import binding, harness
    from test_governance_observation import ARMFoundry, selection
    from govern_control_plane.bootstrap import BootstrapGate, BootstrapBinding, SignedBootstrap
    from govern_control_plane.models import canonical, parse
    probe = reference("governance_probe")

    async def case():
        h = await harness()
        gate = None
        try:
            target = probe.observation.observe(selection(), ARMFoundry())
            assert target["protocol"] == "responses"
            declared = binding()
            declared.update({key: target[key] for key in (
                "agent_id", "agent_version", "image_digest", "project_endpoint",
                "subscription", "resource_group", "client_id")})
            declared.update(tenant_id=target["tenant"], principal=target["subject"],
                            native_policy_digest="sha256:" + "d" * 64)
            parsed = parse(BootstrapBinding, canonical(declared))
            signed = SignedBootstrap(binding=parsed, signature=base64.b64encode(
                await h.signer.sign(hashlib.sha256(canonical(parsed)).digest())).decode())

            async def fetch():
                return signed

            async def initialize(binding, stack):
                async def unexpected(scope, receive, send):
                    pytest.fail("Bootstrap metadata must not invoke business code")
                return unexpected

            gate = BootstrapGate(expected=declared, fetch=fetch, signer=h.service.signer, initialize=initialize)
            transport = httpx.ASGITransport(app=gate)
            requests = []

            class Platform(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    requests.append(request.url.path)
                    assert request.url.path.endswith("/responses")
                    request.url = httpx.URL("https://host/responses")
                    return await transport.handle_async_request(request)

            class Credential:
                async def get_token(self, *scopes, **kwargs):
                    return SimpleNamespace(token="external-platform-fixture", expires_on=4102444800)

            config = {
                "producer": "gateway", "bootstrap": signed.model_dump(mode="json"),
                "tenant_id": parsed.tenant_id, "subject": parsed.principal, "client_id": parsed.client_id,
                "policy": {"key_id": parsed.key_id, "policy_digest": parsed.policy_digest},
                "native_policy": None,
                "expected_deployment": {key: declared[key] for key in (
                    "agent_id", "agent_version", "image_digest", "environment", "subscription", "resource_group")}}
            await gate.activate()
            async with httpx.AsyncClient(transport=Platform()) as http:
                assert await probe.verify_host_bootstrap(
                    config, target, Credential(), http, h.service.signer) == config["bootstrap"]
                assert requests
                before = len(requests)
                config["policy"]["policy_digest"] = parsed.native_policy_digest
                with pytest.raises(ValueError, match="bootstrap-observed-target-mismatch"):
                    await probe.verify_host_bootstrap(config, target, Credential(), http, h.service.signer)
                assert len(requests) == before
        finally:
            if gate is not None:
                await gate.aclose()
            await h.close()
    asyncio.run(case())


@asynccontextmanager
async def gateway_collector_harness(path, monkeypatch):
    from test_governance_probe import (
        native_collector_harness, cp, gateway, fixture_module, MemoryStore,
        Credential, TENANT, WORKLOAD, OTHER, DOWNSTREAM_CLIENT, FIXTURE_CLIENT,
    )
    async with native_collector_harness(path, monkeypatch) as h:
        gateway_h = h.gateway
        producer = cp("probes").ProbeService(
            store=MemoryStore(), registry=gateway_h.policy.registry,
            policy_digest=gateway_h.policy.digest, producer="gateway", fresh=gateway_h.policy.fresh)
        effects = cp("probes").ProbeService(
            store=MemoryStore(), registry=gateway_h.policy.registry,
            policy_digest=gateway_h.policy.digest, producer="fixture", fresh=gateway_h.policy.fresh)
        fixture = fixture_module().create_app(
            probes=effects, auth=h.fixture_auth, callers={OTHER: DOWNSTREAM_CLIENT})
        await gateway_h.downstream.aclose()
        gateway_h.downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(h.cp.token(changes={
                "oid": OTHER, "azp": DOWNSTREAM_CLIENT, "aud": FIXTURE_CLIENT})),
            transport=httpx.ASGITransport(app=fixture))
        gateway_h.dispatcher.downstream = gateway_h.downstream
        gateway_h.dispatcher.probes = producer
        config = deepcopy(h.config)
        config.update(producer="gateway", native_policy=None)
        config["policy"].update(
            bundle_path=str(gateway_h.bundle.root), signed=gateway_h.signed,
            policy_id="safe", policy_digest=gateway_h.policy.digest)
        config["contract"]["tools"][0]["enforcement_path"] = "governed-tool-gateway"
        config["auth"]["producer"]["workloads"].pop(OTHER)
        gateway_h.dispatcher.auth = cp("auth").EntraAuth(
            cp("auth").Settings.model_validate(config["auth"]["producer"]), h.cp.http)
        rid = f"/subscriptions/{TENANT}/resourceGroups/rg-staging/providers/Microsoft.App/containerApps/gateway"
        config["services"]["producer"] = {
            **config["services"]["fixture"], "resource_id": rid, "url": "https://gateway.example"}
        gateway_configuration = {
            **config["auth"]["producer"], "gateway_url": config["producer_url"] + "/mcp",
            "control_plane_url": config["control_plane_url"], "control_plane_scope": config["control_plane_scope"],
            "service_client_id": config["services"]["producer"]["client_id"], "service_principal": WORKLOAD,
            "service_agent_id": "agent-1", "downstream_client_id": DOWNSTREAM_CLIENT,
            "cosmos_url": "https://fixture.documents.azure.com:443/", "cosmos_database": "governance",
            "cosmos_container": "gateway-records", "bundle_path": "/app/policy",
            "policy_id": "safe", "policy_version": "1", "policy_digest": gateway_h.policy.digest,
            "allowed_endpoints": config["allowed_endpoints"], "probe_enabled": True,
            "probe_container": "probe-gateway"}
        config["runtime_configuration"]["services"]["producer"] = {
            "TL_GOV_SERVICE": "gateway",
            "GATEWAY_CONFIG_JSON": gateway("server").Configuration.model_validate(
                gateway_configuration).model_dump_json()}
        h.run.resources[rid] = deepcopy(h.run.resources[config["services"]["fixture"]["resource_id"]])
        h.run.resources[rid]["id"] = rid
        h.run.resources[rid]["properties"]["configuration"]["ingress"]["fqdn"] = "gateway.example"
        h.run.resources[rid]["properties"]["template"]["containers"][0]["env"] = [
            {"name": key, "value": value}
            for key, value in config["runtime_configuration"]["services"]["producer"].items()]
        yield SimpleNamespace(
            base=h, config=config, producer=producer, effects=effects, fixture=fixture,
            app=gateway("server").create_app(gateway_h.dispatcher))


def test_generated_maf_gateway_collector_loads_gateway_configuration(tmp_path, monkeypatch):
    from test_governance_probe import packaged_gateway_project

    async def case():
        async with gateway_collector_harness(tmp_path, monkeypatch) as g:
            project, _, loaded = await packaged_gateway_project(tmp_path, g.base, g.config)
            assert loaded["producer"] == "gateway"
            assert loaded["native_policy"] is None
            assert loaded["contract"]["framework"] == "microsoft-agent-framework"
            assert set(loaded["services"]) == {"producer", "fixture", "control_plane"}
            assert loaded["policy"]["policy_digest"] == g.base.gateway.policy.digest
            assert "native_probe" not in loaded["declared_file_digests"]
            assert not reference("governance_static").check(project, loaded["contract"])["gaps"]
    asyncio.run(case())


@pytest.mark.parametrize("fault", [
    None, "wrong-protocol", "wrong-producer", "signature", "receipt", "replay", "business-binding"])
def test_collector_maf_gateway_real_responses_noop_allow_deny(tmp_path, monkeypatch, fault):
    from agent_framework import tool
    from test_governance_probe import Credential
    from test_runtime_provider import native_model_client, tool_responses
    from test_maf_gateway_client import hosted_module
    from skills._shared.governance import validate_governance_manifest
    probe = reference("governance_probe")

    async def case():
        async with gateway_collector_harness(tmp_path, monkeypatch) as g:
            h, config = g.base, g.config
            host_module = hosted_module()
            monkeypatch.setattr(host_module, "BASE", tmp_path)
            responses, model_requests, hosted_requests = [], [], []

            async def model_request(request):
                model_requests.append(request)
                if not responses:
                    body = request.content.decode()
                    run_id = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", body)[0]
                    variant = "deny" if '\\"variant\\":\\"deny\\"' in body else "allow"
                    responses.extend(tool_responses(
                        "governance_probe_noop", {"probe_run_id": run_id, "variant": variant}))

            model = native_model_client(responses, foundry=True, request_hook=model_request)
            host = None

            @tool
            def read() -> str:
                """Read without a selected policy."""
                return "read"

            class WorkloadCredential:
                async def get_token(self, scope, **kwargs):
                    audience = scope[6:-9]
                    return await Credential(h.cp.token(changes={"aud": audience})).get_token(scope)

            class Router(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if request.url.host.endswith(".services.ai.azure.com"):
                        hosted_requests.append((request.url.path, json.loads(request.content)))
                        request.url = httpx.URL("https://agent.example/responses")
                        destination = host
                    else:
                        destination = {
                            "gateway.example": g.app, "fixture.example": g.fixture,
                            "control.example": h.cp.app}[request.url.host]
                    transport = httpx.ASGITransport(app=destination)
                    response = await transport.handle_async_request(request)
                    if fault == "receipt" and request.url.path.startswith("/receipts/"):
                        await response.aread()
                        receipt = response.json()
                        receipt["policy_digest"] = "sha256:" + "e" * 64
                        return httpx.Response(response.status_code, json=receipt)
                    if (fault == "replay" and request.method == "POST"
                            and request.url.path.startswith("/governance/probes/")):
                        await response.aread()
                        await response.aclose()
                        return await transport.handle_async_request(request)
                    return response

            host_config = {
                "agent_id": "agent-1", "environment": "preproduction", "tenant_id": config["tenant_id"],
                "key_id": config["policy"]["key_id"], "policy_id": "safe", "policy_version": "1",
                "policy_digest": config["policy"]["policy_digest"], "gateway_url": config["producer_url"] + "/mcp",
                "gateway_scope": config["producer_scope"], "control_plane_url": config["control_plane_url"],
                "control_plane_scope": config["control_plane_scope"], "contract": deepcopy(config["contract"])}
            try:
                async with g.app.router.lifespan_context(g.app), AsyncExitStack() as stack:
                    http = await stack.enter_async_context(httpx.AsyncClient(transport=Router(), trust_env=False))
                    host = await host_module.build_host(
                        host_config, credential=WorkloadCredential(), signer=h.signer, stack=stack,
                        client=model, http=http, application=SimpleNamespace(tools=[read], middleware=[]),
                        transport_factory=lambda: httpx.ASGITransport(app=g.app))
                    if fault == "wrong-protocol":
                        h.run.version["definition"]["protocol_versions"] = [
                            {"protocol": "invocations", "version": "2.0.0"}]
                        h.run.agent["agent_endpoint"]["protocols"] = ["invocations"]
                        h.run.agent["agent_endpoint"]["protocol_configuration"] = {"invocations": {}}
                    elif fault == "wrong-producer":
                        config["producer"] = "native"
                    elif fault == "signature":
                        config["policy"]["signed"] = config["policy"]["signed"].model_dump(mode="json")
                        config["policy"]["signed"]["signature"] = base64.b64encode(b"invalid").decode()
                    elif fault == "business-binding":
                        config["contract"]["tools"].append({
                            **deepcopy(config["contract"]["tools"][0]), "id": "business-write"})
                    async with host.router.lifespan_context(host):
                        report = await probe.collect(
                            config, credential=h.credential, signer=h.signer, run=h.run,
                            http=http, timeout=10, poll_interval=0.01)
                if fault in ("wrong-protocol", "wrong-producer", "signature", "replay"):
                    assert report["governance_gaps"]
                    assert not report["governance_probes"] and not hosted_requests
                    assert not model_requests and not g.effects.store.docs
                    if fault == "wrong-protocol":
                        assert report["governance_gaps"] == ["runtime-invocation-path-mismatch"]
                    elif fault == "wrong-producer":
                        assert report["governance_gaps"] == ["runtime-producer-path-mismatch"]
                    return
                if fault == "receipt":
                    assert report["governance_gaps"] and not report["governance_probes"]
                    assert len(hosted_requests) == 1 and len(model_requests) == 2
                    assert all(binding["status"] != "enforced" for binding in report["governance_health"]["bindings"])
                    return
                assert report["governance_gaps"] == (
                    ["binding-live-evidence-missing:business-write"] if fault else []), report
                assert [item["decision"] for item in report["governance_probes"]] == ["allow", "deny"]
                assert len(hosted_requests) == 2 and len(model_requests) == 4
                assert all(path.endswith("/openai/responses") for path, _ in hosted_requests)
                assert all(body["agent_reference"]["version"] == "1" for _, body in hosted_requests)
                records = report["probe_evidence"]
                assert records[0]["run_id"] != records[1]["run_id"]
                deny = next(item for item in records if item["variant"] == "deny")
                assert deny["producer"]["counts"]["dispatch"] == 0
                assert deny["fixture"]["counts"]["received"] == deny["fixture"]["counts"]["effect"] == 0
                assert len(g.effects.store.docs) == 2
                assert all(item["receipt"]["policy_digest"] == config["policy"]["policy_digest"] for item in records)
                manifest = report["governance_manifest"]
                assert manifest["agent"]["runtime"] == "microsoft-agent-framework"
                assert manifest["enforcement"]["adapter"] == "governed-tool-gateway"
                assert set(manifest["collection_evidence"]["verified_policies"]) == {"policy"}
                validate_governance_manifest(manifest)
                if fault == "business-binding":
                    assert report["governance_health"]["bindings"][-1]["status"] == "unverified"
            finally:
                await model.client.close()
    asyncio.run(case())
