"""Actual hosted/native/ACS/OPA/Task8/fixture execution with external IO seams."""
import asyncio
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import httpx
import pytest

from test_governance_gates import ROOT, reference
from test_governance_observation import ARMFoundry, PROJECT, ENDPOINT
from test_control_plane import APP, HUMAN, TENANT, WORKLOAD, OTHER, KEY, MemoryStore, module as cp
from test_gateway import GatewayHarness, Credential, gateway
from test_probe_telemetry import probe_registry, controller_config, controller_token, fixture_module

CONTROLLER_CLIENT = "88888888-8888-8888-8888-888888888888"
DOWNSTREAM_CLIENT = "55555555-5555-5555-5555-555555555555"
FIXTURE_CLIENT = "77777777-7777-7777-7777-777777777777"
PRODUCER_AUDIENCE = "99999999-9999-9999-9999-999999999999"


async def sign(h, bundle, policy_id="safe"):
    models = cp("models")
    envelope = models.BundleEnvelope(policy_id=policy_id, version="1",
        tenant_id=TENANT, key_id=KEY, content_digest=bundle.bundle_digest,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    return models.SignedBundle(envelope=envelope, signature=base64.b64encode(
        await h.cp.signer.sign(models.envelope_digest(envelope))).decode()).model_dump(mode="json")


@asynccontextmanager
async def native_collector_harness(path, monkeypatch, *, fault=None):
    from test_policy_bundle import bundle_module
    from test_runtime_provider import contract, runtime, native_model_client, tool_responses
    from test_governance_wiring import module
    from skills._shared.governance import validate_governance_contract
    registry_input = probe_registry()
    registry_input["actions"][0]["credential_scope"] = f"api://{FIXTURE_CLIENT}/.default"
    h = await GatewayHarness().initialize(path,
        document=registry_input,
        decision='{"decision": "deny"} if input.policy_target.value.variant == "deny" else := {"decision": "allow"}')
    builder = bundle_module()
    native_source = path / "native-source"
    native_source.mkdir()
    for name in ("manifest.yaml", "safe.rego"):
        (native_source / name).write_bytes((path / "source" / name).read_bytes())
    native_bundle = builder.build_bundle(source=native_source, destination=path / "native-bundle",
                                         policy_id="safe", version="1")
    native_signed = await sign(h, native_bundle)
    h.document["native_policy_digest"] = native_bundle.bundle_digest
    (path / "source/gateway-registry.json").write_text(json.dumps(h.document))
    association = builder.build_bundle(source=path / "source", destination=path / "association",
                                       policy_id="safe-probe", version="1")
    signed = await sign(h, association, "safe-probe")
    registry = gateway("dispatcher").Registry.model_validate(h.document)
    probes = cp("probes")
    native = probes.ProbeService(store=MemoryStore(), registry=registry,
        policy_digest=native_bundle.bundle_digest, producer="native", fresh=lambda: None)
    effects = probes.ProbeService(store=MemoryStore(), registry=registry,
        policy_digest=native_bundle.bundle_digest, producer="fixture", fresh=lambda: None)
    settings = h.cp.settings.model_dump()
    settings["audience"] = APP
    original_token = h.cp.token
    monkeypatch.setattr(h.cp, "token", lambda *, changes=None, **kwargs: original_token(
        changes={"aud": APP, **(changes or {})}, **kwargs))
    h.receipts.credential = Credential(h.cp.token())
    settings["probe_controllers"] = controller_config()
    settings["probe_controllers"][HUMAN]["client_id"] = CONTROLLER_CLIENT
    settings["workloads"][OTHER]["client_id"] = DOWNSTREAM_CLIENT
    h.cp.settings = cp("auth").Settings.model_validate(settings)
    h.cp.auth = cp("auth").EntraAuth(h.cp.settings, h.cp.http)
    h.cp.service.settings = h.cp.settings
    h.cp.app.state.auth = h.cp.auth
    fixture_settings = {**settings, "audience": FIXTURE_CLIENT}
    fixture_auth = cp("auth").EntraAuth(cp("auth").Settings.model_validate(fixture_settings), h.cp.http)
    producer_settings = {**settings, "audience": PRODUCER_AUDIENCE}
    producer_auth = cp("auth").EntraAuth(cp("auth").Settings.model_validate(producer_settings), h.cp.http)
    fixture = fixture_module().create_app(probes=effects, auth=fixture_auth, callers={OTHER: DOWNSTREAM_CLIENT})
    downstream = gateway("dispatcher").DownstreamClient(
        credential=Credential(h.cp.token(changes={"oid": OTHER, "azp": DOWNSTREAM_CLIENT, "aud": FIXTURE_CLIENT})),
        transport=httpx.ASGITransport(app=fixture))
    rt = runtime()
    monkeypatch.setitem(sys.modules, "runtime", rt)
    class AuditCredential(Credential):
        async def close(self):
            pass
    audit = module("audit_delivery").AuditDelivery(
        path / "audit", base_url="https://control.example", scope="api://governance/.default",
        required=True, credential_factory=lambda: AuditCredential(h.cp.token()),
        transport_factory=lambda: httpx.ASGITransport(app=h.cp.app), timeout=2)
    doc = contract()
    doc["tools"][0].update(id="governance_probe_noop", requires=["durable-audit"])
    provider = rt.AcsGovernanceProvider(
        contract=doc, bundle_path=native_bundle.root, expected_digest=native_bundle.bundle_digest,
        bundle_verifier=builder.verify_bundle, contract_validator=validate_governance_contract,
        signature_verifier=cp("client").PolicySnapshot(rt.VerifiedPolicy(
            native_bundle.bundle_digest, datetime.now(timezone.utc) + timedelta(minutes=10))),
        safe_provider=lambda _: {"scope": "governance-probe"}, principal=WORKLOAD, tenant=TENANT,
        agent_version="1", image_digest=registry.deployment.image_digest,
        audit=audit, environment="preproduction")
    provider.deployment_agent_id = "agent-1"
    telemetry = rt.NativeProbeTelemetry(provider=provider, service=native, downstream=downstream, client_id=APP)
    telemetry.auth = producer_auth
    responses = []
    async def model_request(request):
        if not responses:
            body = request.content.decode()
            run = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", body)[0]
            variant = "deny" if '\\"variant\\":\\"deny\\"' in body else "allow"
            generated = tool_responses("governance_probe_noop", {"probe_run_id": run, "variant": variant})
            responses.extend(generated[-1:] if fault == "ignored" else generated)
    client = native_model_client(responses, foundry=True, request_hook=model_request)
    monkeypatch.setitem(sys.modules, "runtime", rt)
    host_module = module("maf-container")
    host_module.BASE = path
    (path / "skills").mkdir()
    (path / "copilot-instructions.md").write_text("Invoke only the explicit noop in the request.")
    monkeypatch.setitem(sys.modules, "governance_application", SimpleNamespace(tools=[], middleware=[]))
    host = host_module.build_host(provider, client=client, configure_observability=None)
    oracle = ARMFoundry()
    oracle.subscription.update(subscriptionId=TENANT, tenantId=TENANT)
    project_id = PROJECT.replace("22222222-2222-2222-2222-222222222222", TENANT).replace("/staging/", "/rg-staging/")
    oracle.project["id"] = project_id
    oracle.version.update(name="agent-1", id="agent-1:1",
                          instance_identity={"principal_id": WORKLOAD, "client_id": APP})
    oracle.version["definition"]["container_configuration"]["image"] = "account.azurecr.io/agent@" + registry.deployment.image_digest
    oracle.version["definition"]["environment_variables"]["TL_GOV_IMAGE_DIGEST"] = registry.deployment.image_digest
    oracle.agent.update(name="agent-1", id="agent-1")
    # ARMFoundry deliberately supports only its selected project.
    oracle.resources[project_id] = oracle.project
    config = {
        "schema": "threadlight-governance-probe/v1", "producer": "native",
        "selection": {"subscription": TENANT, "resource_group": "rg-staging",
                      "project_resource_id": project_id, "agent_name": "agent-1", "requested_version": "1"},
        "contract": doc, "tenant_id": TENANT, "subject": WORKLOAD, "client_id": APP,
        "policy": {"bundle_path": str(association.root), "signed": signed,
                   "policy_id": "safe-probe", "policy_version": "1", "policy_digest": association.bundle_digest,
                   "key_id": KEY},
        "native_policy": {"bundle_path": str(native_bundle.root), "signed": native_signed,
                          "policy_id": "safe", "policy_version": "1",
                          "policy_digest": native_bundle.bundle_digest, "key_id": KEY},
        "producer_url": "https://gateway.example", "producer_scope": f"api://{PRODUCER_AUDIENCE}/.default",
        "fixture_url": "https://fixture.example", "fixture_scope": f"api://{FIXTURE_CLIENT}/.default",
        "control_plane_url": "https://control.example", "control_plane_scope": f"api://{APP}/.default",
        "allowed_endpoints": [a for a in (registry.actions[0].endpoint, registry.actions[0].outcome_endpoint)],
        "controller_principal": HUMAN, "controller_client_id": CONTROLLER_CLIENT,
        "auth": {"producer": producer_settings, "fixture": fixture_settings, "control_plane": settings},
        "services": {},
    }
    for index, name in enumerate(("fixture", "control_plane")):
        rid = f"/subscriptions/{TENANT}/resourceGroups/rg-staging/providers/Microsoft.App/containerApps/{name}"
        item = {"resource_id": rid, "url": config[name + "_url"], "image": "account.azurecr.io/service@" + "sha256:" + "a"*64,
                "principal_id": FIXTURE_CLIENT if name == "fixture" else PRODUCER_AUDIENCE,
                "client_id": FIXTURE_CLIENT if name == "fixture" else PRODUCER_AUDIENCE}
        config["services"][name] = item
        oracle.resources[rid] = {"id": rid, "type": "Microsoft.App/containerApps",
            "identity": {"userAssignedIdentities": {"id": {"principalId": item["principal_id"], "clientId": item["client_id"]}}},
            "properties": {"provisioningState": "Succeeded", "runningStatus": "Running",
                "latestReadyRevisionName": "one", "latestRevisionName": "one",
                "configuration": {"activeRevisionsMode": "Single", "ingress": {"fqdn": item["url"].removeprefix("https://")}},
                "template": {"containers": [{"image": item["image"]}]}}}
    control_configuration = {
        **settings, "blob_url": "https://fixture.blob.core.windows.net", "blob_container": "signed-catalog",
        "cosmos_url": "https://fixture.documents.azure.com:443/", "cosmos_database": "governance",
        "cosmos_container": "governance-records",
    }
    config["expected_deployment"] = registry.deployment.model_dump(mode="json")
    config["runtime_configuration"] = {
        "agent": module("generate").agent_environment(
            {"control_plane_url": config["control_plane_url"], "gateway_url": config["producer_url"] + "/mcp"},
            oracle.version["definition"]["container_configuration"]["image"], "/mnt/audit"),
        "services": {"fixture": {}, "control_plane": {
            "TL_GOV_SERVICE": "control-plane", "AZURE_CLIENT_ID": config["services"]["control_plane"]["client_id"],
            "GOV_CONFIG_JSON": json.dumps(control_configuration),
        }},
    }
    oracle.version["definition"]["environment_variables"].update(config["runtime_configuration"]["agent"])
    control_container = oracle.resources[config["services"]["control_plane"]["resource_id"]]["properties"]["template"]["containers"][0]
    control_container["env"] = [{"name": k, "value": v} for k, v in config["runtime_configuration"]["services"]["control_plane"].items()]
    calls, statuses = [], []
    receipt_reads = 0
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal receipt_reads
            calls.append((request.method, request.url.host, request.url.path))
            destination = {"gateway.example": host, "fixture.example": fixture, "control.example": h.cp.app}
            if request.url.host.endswith(".services.ai.azure.com"):
                request.url = httpx.URL("https://gateway.example/responses")
                app = host
            else:
                app = destination[request.url.host]
            if fault == "503" and request.url.path == "/readiness":
                return httpx.Response(503, json={"message": "PRIVATE"})
            if fault == "replay" and request.method == "POST" and request.url.path.startswith("/governance/probes"):
                return httpx.Response(409, json={"error": "probe_conflict"})
            response = await httpx.ASGITransport(app=app).handle_async_request(request)
            statuses.append((request.url.path, response.status_code))
            if fault == "private-health" and request.url.path == "/readiness":
                await response.aread()
                return httpx.Response(response.status_code, json={**response.json(), "debug": "PRIVATE TOKEN MESSAGE"})
            if request.url.path.startswith("/receipts/") and request.method == "GET":
                receipt_reads += 1
                await response.aread()
                body = response.json()
                if fault == "wrong-receipt-time":
                    body["recorded_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                elif fault == "wrong-receipt-actionhash":
                    body["action_hash"] = "sha256:" + "c" * 64
                elif fault == "wrong-receipt-policy":
                    body["policy_digest"] = "sha256:" + "c" * 64
                elif fault == "wrong-receipt-context":
                    body["probe"]["subject"] = OTHER
                elif fault == "private-receipt":
                    body["message"] = "PRIVATE TOKEN MESSAGE"
                return httpx.Response(response.status_code, json=body)
            if fault == "expired" and request.url.path.startswith("/governance/probes") and request.method == "POST":
                await response.aread()
                body = response.json()
                body["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                return httpx.Response(response.status_code, json=body)
            if request.url.path.startswith("/governance/probes") and request.method == "GET":
                await response.aread()
                state = response.json()
                if fault == "baseline-bool" and state.get("terminal") is None:
                    state["counts"]["effect"] = False
                    return httpx.Response(response.status_code, json=state)
                if fault == "poststatechanged" and receipt_reads == 2:
                    state["fixture_id"] = "changed"
                    return httpx.Response(response.status_code, json=state)
                if state.get("terminal") is not None and request.url.host == "gateway.example":
                    if fault == "counter-bool":
                        state["counts"]["completed"] = True
                    elif fault == "reset":
                        state["counts"] = dict.fromkeys(probes.PHASES, 0)
                        state["events"], state["terminal"], state["context"] = [], None, None
                    elif fault == "receipt":
                        state["events"][1]["receipt_id"] = "f" * 32
                    elif fault == "late-version":
                        oracle.version["definition"]["container_configuration"]["image"] = "account.azurecr.io/agent@sha256:" + "f"*64
                    return httpx.Response(response.status_code, json=state)
            return response
    class ControllerCredential:
        async def get_token(self, scope, **kwargs):
            audience = scope[6:-9] if scope.startswith("api://") else APP
            return SimpleNamespace(token=h.cp.token(changes={"oid": HUMAN, "azp": CONTROLLER_CLIENT,
                "aud": audience, "roles": ["Governance.Probe.Control"]}), expires_on=4102444800)
    credential = ControllerCredential()
    # The OpenAI token provider expects an AccessToken-shaped expiry.
    async with audit, httpx.AsyncClient(transport=Transport(), trust_env=False) as http:
        try:
            yield SimpleNamespace(config=config, run=oracle, http=http, signer=h.cp.signer,
                                  credential=credential, native=native, effects=effects,
                                  provider=provider, calls=calls, cp=h.cp, gateway=h,
                                  fixture_auth=fixture_auth, producer_auth=producer_auth, statuses=statuses)
        finally:
            await downstream.aclose()
            await client.client.close()
            await h.close()


def test_collector_actual_native_responses_deny_positive_and_binding_specific(tmp_path, monkeypatch):
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            report = await probe.collect(h.config, credential=h.credential, signer=h.signer,
                                         run=h.run, http=h.http, timeout=8, poll_interval=0.01)
            assert report["governance_gaps"] == [], (report["governance_gaps"], [s for s in h.statuses if s[1] >= 400])
            assert len(report["governance_probes"]) == 2
            assert {p["decision"] for p in report["governance_probes"]} == {"allow", "deny"}
            bindings = report["governance_health"]["bindings"]
            assert bindings[0]["status"] == "enforced"
            assert bindings[1]["status"] == "unbound"
            assert len(h.effects.store.docs) == 2
            assert any(path.endswith("/openai/responses") for _, _, path in h.calls)
            assert any(call[:2] == ["az", "rest"] for call in h.run.calls)
            assert "PRIVATE" not in json.dumps(report)
            from skills._shared.governance import validate_governance_manifest
            from jsonschema import Draft7Validator
            manifest = report["governance_manifest"]
            validate_governance_manifest(manifest)
            Draft7Validator(json.loads((ROOT / "skills/_shared/governance-manifest.schema.json").read_text())).validate(manifest)
    asyncio.run(case())


def test_noop_proof_cannot_certify_business_or_lifecycle_bindings(tmp_path, monkeypatch):
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            h.config["contract"]["tools"].append({
                **deepcopy(h.config["contract"]["tools"][0]), "id": "business-write"})
            report = await probe.collect(h.config, credential=h.credential, signer=h.signer,
                                         run=h.run, http=h.http, timeout=8)
            assert report["governance_gaps"] == ["binding-live-evidence-missing:business-write"]
            assert report["governance_health"]["bindings"][-1]["status"] == "unverified"
            from skills._shared.governance import validate_governance_manifest, GovernanceContractError
            manifest = report["governance_manifest"]
            validate_governance_manifest(manifest)
            forged = deepcopy(manifest)
            forged["collection_evidence"]["records"][1]["receipt"]["action_hash"] = "sha256:" + "e" * 64
            with pytest.raises(GovernanceContractError):
                validate_governance_manifest(forged)
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["private-health", "private-receipt"])
def test_collector_never_exports_unbounded_service_health_fields(tmp_path, monkeypatch, fault):
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch, fault=fault) as h:
            report = await probe.collect(h.config, credential=h.credential, signer=h.signer,
                                         run=h.run, http=h.http, timeout=8)
            assert "PRIVATE" not in json.dumps(report)
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["503", "counter-bool", "baseline-bool", "reset", "receipt", "late-version",
                                   "ignored", "replay", "expired", "poststatechanged", "wrong-receipt-time",
                                   "wrong-receipt-actionhash", "wrong-receipt-policy", "wrong-receipt-context"])
def test_actual_collector_refuses_incomplete_or_wrong_evidence(tmp_path, monkeypatch, fault):
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch, fault=fault) as h:
            report = await probe.collect(h.config, credential=h.credential, signer=h.signer,
                                         run=h.run, http=h.http, timeout=8, poll_interval=0.01)
            assert report["governance_gaps"]
            assert not any(b["status"] == "enforced" for b in report["governance_health"]["bindings"])
            assert "PRIVATE" not in json.dumps(report)
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["signature", "unsafe", "undeclared", "endpoint", "evaluate-only",
                                   "version", "native-association", "local-only", "shared-controller-app", "controller-token-mismatch",
                                   "private-selector"])
def test_refusal_precedes_every_agent_invocation_even_force(tmp_path, monkeypatch, fault):
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            if fault == "signature":
                h.config["policy"]["signed"]["signature"] = base64.b64encode(b"wrong").decode()
            elif fault in ("unsafe", "undeclared"):
                path = Path(h.config["policy"]["bundle_path"]) / "gateway-registry.json"
                document = json.loads(path.read_text())
                document["actions"][0]["probe_safe"] = 1 if fault == "unsafe" else False
                path.write_text(json.dumps(document))
            elif fault == "endpoint":
                h.config["fixture_url"] = "https://other.example"
            elif fault == "evaluate-only":
                h.provider.mode = "evaluate_only"
            elif fault == "version":
                h.config["selection"]["requested_version"] = "2"
            elif fault == "native-association":
                h.config["native_policy"]["policy_digest"] = "sha256:" + "f"*64
            elif fault == "local-only":
                h.config["local_only"] = True
            elif fault == "shared-controller-app":
                h.config["controller_client_id"] = APP
                for auth in h.config["auth"].values():
                    auth["probe_controllers"][HUMAN]["client_id"] = APP
            elif fault == "controller-token-mismatch":
                h.config["controller_principal"] = DOWNSTREAM_CLIENT
                for auth in h.config["auth"].values():
                    auth["probe_controllers"][DOWNSTREAM_CLIENT] = deepcopy(auth["probe_controllers"][HUMAN])
            else:
                h.config["selection"]["token"] = "PRIVATE"
            report = await probe.collect(h.config, credential=h.credential, signer=h.signer,
                                         run=h.run, http=h.http, force=True, timeout=2)
            assert report["governance_gaps"]
            assert "PRIVATE" not in json.dumps(report)
            assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
    asyncio.run(case())


def test_collector_actual_copilot_invocations_mcp_gateway_fixture(tmp_path, monkeypatch):
    """Unmodified Copilot SDK/CLI; only its upstream model HTTP is synthetic."""
    probe = reference("governance_probe")
    async def case():
        import socket
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Route
        from test_runtime_provider import native_model_client, tool_responses
        from test_governance_wiring import module
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            gateway_h = h.gateway
            producer = cp("probes").ProbeService(store=MemoryStore(), registry=gateway_h.policy.registry,
                policy_digest=gateway_h.policy.digest, producer="gateway", fresh=gateway_h.policy.fresh)
            effects = cp("probes").ProbeService(store=MemoryStore(), registry=gateway_h.policy.registry,
                policy_digest=gateway_h.policy.digest, producer="fixture", fresh=gateway_h.policy.fresh)
            fixture = fixture_module().create_app(probes=effects, auth=h.fixture_auth, callers={OTHER: DOWNSTREAM_CLIENT})
            await gateway_h.downstream.aclose()
            gateway_h.downstream = gateway("dispatcher").DownstreamClient(
                credential=Credential(h.cp.token(changes={"oid": OTHER, "azp": DOWNSTREAM_CLIENT, "aud": FIXTURE_CLIENT})),
                transport=httpx.ASGITransport(app=fixture))
            gateway_h.dispatcher.downstream = gateway_h.downstream
            gateway_h.dispatcher.probes, gateway_h.dispatcher.auth = producer, h.producer_auth
            app = gateway("server").create_app(gateway_h.dispatcher)
            config = deepcopy(h.config)
            config.update(producer="gateway", native_policy=None)
            config["policy"].update(bundle_path=str(gateway_h.bundle.root), signed=gateway_h.signed,
                                    policy_id="safe", policy_digest=gateway_h.policy.digest)
            config["contract"]["framework"] = "github-copilot-sdk"
            config["contract"]["tools"][0]["enforcement_path"] = "governed-tool-gateway"
            config["auth"]["producer"]["workloads"].pop(OTHER)
            gateway_h.dispatcher.auth = cp("auth").EntraAuth(
                cp("auth").Settings.model_validate(config["auth"]["producer"]), h.cp.http)
            rid = f"/subscriptions/{TENANT}/resourceGroups/rg-staging/providers/Microsoft.App/containerApps/gateway"
            config["services"]["producer"] = {**config["services"]["fixture"], "resource_id": rid,
                                               "url": "https://gateway.example"}
            gateway_configuration = {
                **config["auth"]["producer"], "gateway_url": config["producer_url"] + "/mcp",
                "control_plane_url": config["control_plane_url"], "control_plane_scope": config["control_plane_scope"],
                "service_client_id": config["services"]["producer"]["client_id"], "service_principal": WORKLOAD,
                "service_agent_id": "agent-1", "downstream_client_id": DOWNSTREAM_CLIENT,
                "cosmos_url": "https://fixture.documents.azure.com:443/", "cosmos_database": "governance",
                "cosmos_container": "gateway-records", "bundle_path": "/app/policy",
                "policy_id": "safe", "policy_version": "1", "policy_digest": gateway_h.policy.digest,
                "allowed_endpoints": config["allowed_endpoints"], "probe_enabled": True, "probe_container": "probe-gateway",
            }
            config["runtime_configuration"]["services"]["producer"] = {
                "TL_GOV_SERVICE": "gateway",
                "GATEWAY_CONFIG_JSON": gateway("server").Configuration.model_validate(gateway_configuration).model_dump_json()}
            h.run.resources[rid] = deepcopy(h.run.resources[config["services"]["fixture"]["resource_id"]])
            h.run.resources[rid]["id"] = rid
            h.run.resources[rid]["properties"]["configuration"]["ingress"]["fqdn"] = "gateway.example"
            h.run.resources[rid]["properties"]["template"]["containers"][0]["env"] = [
                {"name": k, "value": v} for k, v in config["runtime_configuration"]["services"]["producer"].items()]
            h.run.version["definition"]["protocol_versions"] = [{"protocol": "invocations", "version": "2.0.0"}]

            responses, model_calls = [], []
            shim = native_model_client(responses)
            async def model(request):
                body = await request.json()
                model_calls.append(body)
                if not responses:
                    text = json.dumps(body["input"])
                    run_id = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", text)[0]
                    variant = "deny" if '\\"variant\\":\\"deny\\"' in text else "allow"
                    tool_name = next(tool["name"] for tool in body["tools"]
                                     if "governance_probe_noop" in tool.get("name", ""))
                    responses.extend(tool_responses(tool_name, {"probe_run_id": run_id, "variant": variant}))
                output = await shim.client._client._transport.handle_async_request(
                    httpx.Request("POST", "https://model.example/responses", json=body))
                await output.aread()
                return Response(output.content, media_type=output.headers["content-type"])
            model_app = Starlette(routes=[Route("/{path:path}", model, methods=["POST"])])
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            server = uvicorn.Server(uvicorn.Config(model_app, access_log=False, log_level="critical"))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.01)
            monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", f"http://127.0.0.1:{sock.getsockname()[1]}")
            monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "local")
            monkeypatch.setenv("GOVERNED_TOOL_GATEWAY_URL", "https://gateway.example/mcp")
            home = tmp_path / "copilot-home"
            home.mkdir()
            monkeypatch.setenv("HOME", str(home))
            # Copilot CLI work files must remain under the project's writable scratch.
            monkeypatch.setenv("TMPDIR", str(home))
            class WorkloadCredential(Credential):
                async def close(self):
                    pass
            import azure.identity.aio
            monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential",
                                lambda: WorkloadCredential(h.cp.token(changes={"aud": PRODUCER_AUDIENCE})))
            ghcp = module("ghcp-container")
            monkeypatch.setattr(ghcp, "__file__", str(tmp_path / "container.py"))
            host = ghcp.build_host({
                "mcp_servers": {"original": {"type": "http", "url": "https://original.example/mcp",
                                              "tools": ["governance_probe_noop", "read"]}},
                "mcp_bindings": {"governance_probe_noop": {"server": "original", "tool": "governance_probe_noop"}},
                "contract": config["contract"], "gateway_scope": config["producer_scope"],
            }, configure_observability=None)
            original_client = httpx.AsyncClient
            hosted_requests = []
            class Routes(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if request.url.host.endswith(".services.ai.azure.com"):
                        hosted_requests.append(json.loads(request.content))
                        request.url = httpx.URL("https://agent.example/invocations")
                        destination = host
                    else:
                        destination = {"gateway.example": app, "fixture.example": fixture, "control.example": h.cp.app}[request.url.host]
                    return await httpx.ASGITransport(app=destination).handle_async_request(request)
            def clients(*args, **kwargs):
                kwargs.setdefault("transport", Routes())
                return original_client(*args, **kwargs)
            monkeypatch.setattr(httpx, "AsyncClient", clients)
            try:
                async with app.router.lifespan_context(app), original_client(transport=Routes()) as http:
                    report = await probe.collect(config, credential=h.credential, signer=h.signer,
                                                 run=h.run, http=http, timeout=30, poll_interval=0.01)
                assert report["governance_gaps"] == [], report
                assert len(model_calls) == 4 and len(hosted_requests) == 2
                assert [p["decision"] for p in report["governance_probes"]] == ["allow", "deny"]
                assert len(effects.store.docs) == 2
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 10)
                sock.close()
                await shim.client.close()
    asyncio.run(case())


def packaged_collector_project(path, h):
    """Actual generated source + bound service declarations (not observed Azure facts)."""
    import shutil
    from test_governance_wiring import module
    project = path / "pilot"
    agent = project / "src/agent"
    agent.mkdir(parents=True)
    (agent / "governance_application.py").write_text(
        "tools = []\nmiddleware = []\ndef safe_evidence(identity):\n    return {'scope':'governance-probe'}\n")
    (agent / "copilot-instructions.md").write_text("Use only the explicitly installed noop.")
    (project / "azure.yaml").write_text("name: pilot\nservices:\n  agent:\n    host: azure.ai.agent\n    project: ./src/agent\n")
    native = h.config["native_policy"]
    native_signed = path / "native-envelope.json"
    native_signed.write_text(json.dumps(native["signed"]))
    package_config = {
        "agent_service": "agent", "agent_id": "agent-1", "environment": "preproduction",
        "tenant_id": TENANT, "key_id": KEY, "policy_id": "safe", "policy_version": "1",
        "policy_digest": native["policy_digest"], "bundle_path": native["bundle_path"],
        "signed_envelope": str(native_signed), "control_plane_url": h.config["control_plane_url"],
        "control_plane_scope": h.config["control_plane_scope"],
        "gateway_url": h.config["producer_url"] + "/mcp", "gateway_scope": h.config["producer_scope"],
        "approver_roles": ["Approver"], "subscription": TENANT, "resource_group": "rg-staging",
        "probe_observability": {"enabled": True, "configuration_file": "/mnt/governance-probe/config.json"},
        "network": {"posture": "public-pilot", "allowed_ips": ["192.0.2.10/32"],
                    "environment_id": f"/subscriptions/{TENANT}/resourceGroups/rg-staging/providers/Microsoft.App/managedEnvironments/env"},
    }
    gen = module("generate")
    gen.generate(project, h.config["contract"], configuration=package_config)
    image = h.run.version["definition"]["container_configuration"]["image"]
    gen.agent_image(project, h.config["contract"], configuration={"agent_image": image, "spool_directory": "/mnt/audit"})
    native_config = {
        **h.config["auth"]["producer"], "enabled": True, "producer": "native", "service_client_id": APP,
        "downstream_client_id": DOWNSTREAM_CLIENT, "cosmos_url": "https://fixture.documents.azure.com:443/",
        "cosmos_database": "governance", "cosmos_container": "probe-native",
        "bundle_path": "/mnt/governance-probe/policy", "signed_envelope_path": "/mnt/governance-probe/envelope.json",
        "policy_id": "safe-probe", "policy_version": "1", "policy_digest": h.config["policy"]["policy_digest"],
        "gateway_url": h.config["producer_url"] + "/mcp", "allowed_endpoints": h.config["allowed_endpoints"],
        "expected_deployment": h.native.registry.deployment.model_dump(mode="json"), "fixture_callers": {},
    }
    control = {
        **h.config["auth"]["control_plane"], "blob_url": "https://fixture.blob.core.windows.net",
        "blob_container": "signed-catalog", "cosmos_url": "https://fixture.documents.azure.com:443/",
        "cosmos_database": "governance", "cosmos_container": "governance-records",
    }
    binding = {
        "native_probe_config": native_config, "control_config": control,
        "gateway_config": {**h.config["auth"]["producer"], "control_plane_url": h.config["control_plane_url"]},
        "policy_digest": native["policy_digest"], "agent_principal": WORKLOAD, "agent_client_id": APP,
        "agent_version": "1", "downstream_principal": OTHER, "downstream_client": DOWNSTREAM_CLIENT,
        "control_principal": h.config["services"]["control_plane"]["principal_id"],
        "control_client": h.config["services"]["control_plane"]["client_id"],
    }
    deployment = {"schema": "threadlight-governance-deployment/v1",
        "infrastructure": {"runtime": "microsoft-agent-framework", "tenant_id": TENANT, "environment": "preproduction"},
        "images": {"agent": image, "control_plane": h.config["services"]["control_plane"]["image"]}, "bindings": binding}
    (project / ".threadlight/governance-deployment.json").write_text(json.dumps(deployment))
    fixture = {
        **native_config, **h.config["auth"]["fixture"], "producer": "fixture", "service_client_id": FIXTURE_CLIENT,
        "downstream_client_id": None, "cosmos_container": "probe-fixture", "fixture_callers": {OTHER: DOWNSTREAM_CLIENT}}
    (project / ".threadlight/fixture.json").write_text(json.dumps(fixture))
    shutil.copytree(h.config["policy"]["bundle_path"], project / ".threadlight/probe-policy")
    (project / ".threadlight/probe-envelope.json").write_text(json.dumps(h.config["policy"]["signed"]))
    options = {"schema": "threadlight-governance-probe-input/v1", "selection": h.config["selection"],
        "controller_principal": HUMAN, "controller_client_id": CONTROLLER_CLIENT, "bundle_path": ".threadlight/probe-policy",
        "signed_envelope_path": ".threadlight/probe-envelope.json",
        "fixture_configuration_file": ".threadlight/fixture.json", "services": h.config["services"]}
    config = project / ".threadlight/governance-probe.json"
    config.write_text(json.dumps(options))
    (project / "specs").mkdir()
    manifest = {**h.config["contract"], "deployment_manifest": {
        "module_selectors": {}, "services": [], "scheduled_jobs": [], "channels": [],
        "expected_resource_types": ["Microsoft.Storage/storageAccounts"]}}
    (project / "specs/manifest.json").write_text(json.dumps(manifest))
    return project, config


@pytest.mark.parametrize("custom_manifest", [False, True])
def test_postdeploy_runs_real_collector_and_retains_original_gaps(tmp_path, monkeypatch, custom_manifest):
    from test_governance_gates import safe_check
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            manifest_path = project / "specs/manifest.json"
            if custom_manifest:
                selected = project / "nested/specs/custom.json"
                selected.parent.mkdir(parents=True)
                manifest_path.rename(selected)
                manifest_path = selected
            loaded = probe.load_configuration(project, config)
            assert loaded["native_policy"]["policy_digest"] == h.config["native_policy"]["policy_digest"]
            from test_governance_gates import empty_parent_azure
            monkeypatch.setattr(safe_check, "_az", empty_parent_azure)
            output = project / "post.json"
            result = await asyncio.to_thread(safe_check.phase_postdeploy, manifest_path,
                output, "rg-staging", project, {"credential": h.credential, "signer": h.signer,
                                              "run": h.run, "http": h.http, "timeout": 8})
            assert result == 1
            report = json.loads(output.read_text())
            assert report["governance_gaps"] == [], report["governance_gaps"]
            assert len(report["governance_probes"]) == 2
            assert report["gaps"] == ["missing resource type: Microsoft.Storage/storageAccounts"]
    asyncio.run(case())


def test_static_rejects_native_probe_association_for_another_image(tmp_path, monkeypatch):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, _ = packaged_collector_project(tmp_path, h)
            binding_path = project / ".threadlight/governance-deployment.json"
            data = json.loads(binding_path.read_text())
            data["bindings"]["native_probe_config"]["expected_deployment"]["image_digest"] = "sha256:" + "e" * 64
            binding_path.write_text(json.dumps(data))
            gate = reference("governance_static")
            result = gate.check(project, h.config["contract"])
            assert result["gaps"]
    asyncio.run(case())
