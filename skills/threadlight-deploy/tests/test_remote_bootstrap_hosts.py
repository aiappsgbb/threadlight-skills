"""Actual generated entrypoint / pinned SDK checks; no Azure traffic."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.governance_runtime
@pytest.mark.parametrize("template", ["maf-container", "ghcp-container"])
def test_generated_host_remote_factory_is_exported(tmp_path, template, monkeypatch):
    from test_governance_wiring import module
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_remote_bootstrap import APP, DIGEST, KEY, TENANT, harness
    from govern_control_plane.bootstrap import BootstrapUnavailable
    from govern_control_plane.models import canonical, parse

    async def scenario():
        gen = module("generate")
        pilot = tmp_path / "pilot"
        pilot.mkdir()
        gen.copy_sources(pilot)
        for source in (template + ".py", "audit_delivery.py"):
            shutil.copyfile(ROOT / "skills/threadlight-deploy/references/governance" / source,
                            pilot / ("container.py" if source == template + ".py" else source))
        original = list(sys.path)
        portable_modules = {name: value for name, value in sys.modules.items()
                            if name.split(".")[0] in {"skills", "runtime", "govern_bundle", "audit_delivery"}}
        sys.path.insert(0, str(pilot))
        try:
            spec = importlib.util.spec_from_file_location("remote_generated_host", pilot / "container.py")
            host = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(host)
            assert callable(getattr(host, "remote_host", None)), "generated entrypoint lacks remote bootstrap"
            h = await harness()
            try:
                config = dict(
                    tenant_id=TENANT, key_id=KEY, agent_id="agent-1", policy_id="safe",
                    environment="preproduction", control_plane_url="https://control.example",
                    control_plane_scope="api://governance/.default", remote_bootstrap={
                        "reference": "attempt-1", "project_endpoint":
                            "https://test.services.ai.azure.com/api/projects/test",
                        "subscription": TENANT, "resource_group": "test-rg", "native_policy_digest": DIGEST})
                env = dict(FOUNDRY_AGENT_VERSION="17", FOUNDRY_AGENT_NAME="agent-1",
                    FOUNDRY_PROJECT_ENDPOINT=config["remote_bootstrap"]["project_endpoint"],
                    TL_GOV_IMAGE_DIGEST=DIGEST, GOV_CONTROL_PLANE_URL=config["control_plane_url"])
                if template == "ghcp-container":
                    from test_governance_wiring import contract
                    config.update(
                        contract=contract(), gateway_scope="api://gateway/.default",
                        gateway_url="https://gateway.example/mcp",
                        mcp_servers={"original": {"type": "http", "url": "https://original.example/mcp",
                                                 "tools": ["act", "read"]}},
                        mcp_bindings={"act": {"server": "original", "tool": "act"}})
                    monkeypatch.setenv("GOVERNED_TOOL_GATEWAY_URL", config["gateway_url"])

                class Credential:
                    async def get_token(self, scope):
                        from types import SimpleNamespace
                        return SimpleNamespace(token=h.token())

                gate = host.remote_host(config, credential=Credential(), signer=h.service.signer,
                                        http=h.client, env=env, configure_observability=None)
                import httpx
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                             base_url="http://local") as client:
                    for path in ("/readiness", "/responses", "/invocations"):
                        assert (await client.post(path)).status_code == 503
                    assert (await client.get("/liveness")).status_code == 200
                if template == "maf-container":
                    from test_remote_bootstrap import binding
                    from govern_control_plane.bootstrap import BootstrapBinding
                    envelope = {**binding(), **gate.expected}
                    await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(envelope)))
                    with pytest.raises((ValueError, OSError), match="policy|bundle|No such"):
                        await gate.activate()
                else:
                    from test_remote_bootstrap import binding
                    from govern_control_plane.bootstrap import BootstrapBinding
                    from azure.ai.agentserver.invocations import InvocationAgentServerHost
                    envelope = {**binding(), **gate.expected}
                    await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(envelope)))
                    await asyncio.gather(*(gate.activate() for _ in range(3)))
                    assert isinstance(gate.app, InvocationAgentServerHost)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                                 base_url="http://local") as client:
                        checked = await client.post("/invocations", json={
                            "input": "", "bootstrap_reference": "attempt-1"})
                        assert checked.status_code == 200
                        assert checked.json()["bootstrap"]["binding"]["agent_version"] == "17"
                        assert (await client.post("/invocations", json={"input": ""})).status_code == 400
                assert "governance_application" not in sys.modules
                await gate.aclose()
            finally:
                await h.close()
        finally:
            sys.path[:] = original
            for name in list(sys.modules):
                if name.split(".")[0] in {"skills", "runtime", "govern_bundle", "audit_delivery"}:
                    sys.modules.pop(name)
            sys.modules.update(portable_modules)
    asyncio.run(scenario())


@pytest.mark.governance_runtime
def test_real_native_effect_boundary_rechecks_bootstrap_after_await(tmp_path):
    from test_governance_wiring import module
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_remote_bootstrap import binding, harness
    from test_runtime_provider import provider, runtime, model_client, tool_responses
    from govern_control_plane.bootstrap import BootstrapGate, BootstrapBinding
    from govern_control_plane.models import canonical, parse
    from datetime import datetime, timedelta, timezone
    from agent_framework import FunctionTool

    async def scenario():
        h = await harness()
        try:
            envelope = parse(BootstrapBinding, canonical(binding()))
            signed = await h.service.publish_bootstrap(envelope)
            async def fetch():
                return signed
            async def initialize(binding, stack):
                return lambda scope, receive, send: None
            gate = BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                 initialize=initialize)
            await gate.activate()
            p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
            p.bootstrap_gate = gate
            entered, effects = [], []

            async def act(amount: int = 1):
                entered.append("tool")
                authorization = runtime().require_effect_authorization("act", {"amount": amount})
                entered.append("authorized")
                await asyncio.sleep(0)
                signed.binding.__dict__["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
                authorization()
                effects.append("terminal")
                return "done"

            client = model_client(tool_responses(args={"amount": 1}))
            agent = runtime().create_governed_agent(
                p, client=client,
                tools=[FunctionTool(name="act", func=act)],
                id="agent-1", instructions="test", default_options={"store": False})
            error = None
            try:
                await agent.run("call act")
            except Exception as caught:
                error = caught
            assert entered == ["tool", "authorized"], repr(error)
            assert effects == [], "expired signed bootstrap reached native terminal effect"
            assert len(client.requests) == 1, "expired bootstrap issued another model request"
            await gate.aclose()
        finally:
            await h.close()
    asyncio.run(scenario())


def test_ghcp_relay_rechecks_binding_after_credential_wait():
    from test_governance_wiring import module
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_remote_bootstrap import binding, harness
    from govern_control_plane.bootstrap import BootstrapGate, BootstrapBinding
    from govern_control_plane.models import canonical, parse
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    import httpx

    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(binding())))
            async def fetch():
                return signed
            async def initialize(binding, stack):
                return lambda scope, receive, send: None
            gate = BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                 initialize=initialize)
            await gate.activate()
            effects = []
            async def downstream(request):
                effects.append("gateway")
                return httpx.Response(200, json={})
            class Credential:
                async def get_token(self, scope):
                    await asyncio.sleep(0)
                    signed.binding.__dict__["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
                    return SimpleNamespace(token="local-only")
            async with httpx.AsyncClient(transport=httpx.MockTransport(downstream)) as transport:
                relay = module("ghcp-container").McpRelay(
                    gateway_url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=Credential(), invocation_id="inv-1", tools=["act"],
                    http=transport, bootstrap_gate=gate)
                ticket = await relay.pre_mcp(
                    {"serverName": "threadlight-governed", "toolName": "act",
                     "toolCallId": "call-1", "sessionId": "session-1", "arguments": {}}, None)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                            base_url="http://localhost") as client:
                    response = await client.post("/mcp", headers={"X-Threadlight-Relay": relay.secret},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "act", "arguments": {}, "_meta": ticket["metaToUse"]}})
                    assert response.status_code == 503
                    assert effects == []
            await gate.aclose()
        finally:
            await h.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("protocol", ["invocations", "responses"])
def test_collector_checks_actual_pending_host_signed_binding_before_probe(protocol):
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_remote_bootstrap import binding, harness
    from govern_control_plane.bootstrap import BootstrapGate, BootstrapBinding
    from govern_control_plane.models import canonical, parse
    import importlib
    collector = importlib.import_module("skills.threadlight-safe-check.references.governance_probe")
    assert callable(getattr(collector, "verify_host_bootstrap", None)), "collector remote bootstrap chain missing"
    import httpx
    from types import SimpleNamespace
    from starlette.responses import JSONResponse

    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(binding())))
            async def fetch():
                return signed
            async def initialize(binding, stack):
                return JSONResponse({"unexpected": "application"})
            gate = BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer, initialize=initialize)
            transport = httpx.ASGITransport(app=gate)
            class Platform(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if protocol == "invocations":
                        assert request.url.path.endswith("/agents/agent-1/endpoint/protocols/invocations")
                    else:
                        assert request.url.path.endswith("/responses")
                    request.url = httpx.URL("https://host/" + protocol)
                    return await transport.handle_async_request(request)
            class Credential:
                async def get_token(self, *scopes, **kwargs):
                    return SimpleNamespace(token="platform-external-fixture", expires_on=9999999999)
            config = {"bootstrap": signed.model_dump(mode="json"), "tenant_id": signed.binding.tenant_id,
                      "policy": {"key_id": signed.binding.key_id, "policy_digest": signed.binding.policy_digest}}
            config["native_policy"] = dict(config["policy"])
            target = {**{key: getattr(signed.binding, key) for key in (
                "agent_id", "agent_version", "image_digest", "project_endpoint", "subscription", "resource_group",
                "environment", "client_id")}, "subject": signed.binding.principal,
                "tenant": signed.binding.tenant_id, "protocol": protocol}
            async with httpx.AsyncClient(transport=Platform()) as http:
                with pytest.raises(ValueError):
                    await collector.verify_host_bootstrap(config, target, Credential(), http, h.service.signer)
                await gate.activate()
                result = await collector.verify_host_bootstrap(config, target, Credential(), http, h.service.signer)
                assert result == config["bootstrap"]
                with pytest.raises(ValueError):
                    await collector.verify_host_bootstrap(config, {**target, "agent_version": "18"},
                                                          Credential(), http, h.service.signer)
            await gate.aclose()
        finally:
            await h.close()
    asyncio.run(scenario())


@pytest.mark.governance_runtime
def test_remote_gateway_stages_distinct_final_immutable_policy_version(tmp_path):
    from test_governance_quality import inputs
    from test_governance_wiring import module
    from test_policy_bundle import bundle_module
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    from datetime import datetime, timedelta, timezone
    import base64
    data = inputs(tmp_path, framework="github-copilot-sdk", environment="preproduction")
    project, document, config, deployment, signer = data
    config["remote_bootstrap"] = dict(
        reference="attempt-1", project_endpoint="https://test.services.ai.azure.com/api/projects/test",
        subscription=config["tenant_id"], resource_group="fixture",
        native_policy_digest=config["policy_digest"], final_policy_version="2")
    gen = module("generate")
    gen.generate(project, document, configuration=config)
    gen.agent_image(project, document, configuration={
        "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
    source = tmp_path / "final-policy-source"
    source.mkdir()
    for file in Path(config["bundle_path"]).iterdir():
        if file.name != "bundle-metadata.json":
            shutil.copyfile(file, source / file.name)
    final = bundle_module().build_bundle(
        source=source, destination=tmp_path / "final-policy", policy_id="safe", version="2")
    envelope = BundleEnvelope(policy_id="safe", version="2", tenant_id=config["tenant_id"],
        key_id=config["key_id"], content_digest=final.bundle_digest,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        asyncio.run(signer.sign(envelope_digest(envelope)))).decode())
    signed_path = tmp_path / "final-signed.json"
    signed_path.write_bytes(canonical(signed))
    staged = gen.stage_gateway(project, document, configuration={
        "gateway_bundle": str(final.root), "policy_digest": final.bundle_digest,
        "signed_envelope": str(signed_path), "agent_image": deployment["images"]["agent"]})
    assert staged["policy_digest"] == final.bundle_digest


@pytest.mark.governance_runtime
def test_native_bootstrap_check_uses_actual_responses_sdk_without_inference():
    sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
    from test_remote_bootstrap import binding, harness
    from govern_control_plane import bootstrap
    from govern_control_plane.models import canonical, parse
    from types import SimpleNamespace
    import httpx
    assert callable(getattr(bootstrap, "read_host_binding", None)), "native Responses bootstrap check missing"

    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(parse(bootstrap.BootstrapBinding, canonical(binding())))
            effects = []
            async def fetch():
                return signed
            async def application(scope, receive, send):
                effects.append("inference")
                raise AssertionError("bootstrap check must not enter the application")
            async def initialize(binding, stack):
                return application
            gate = bootstrap.BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                           initialize=initialize)
            await gate.activate()
            asgi = httpx.ASGITransport(app=gate)
            class Platform(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    assert request.url.path.endswith("/responses")
                    body = json.loads(request.content)
                    assert body["input"] == [] and body["store"] is False
                    assert body["agent_reference"]["version"] == "17"
                    request.url = httpx.URL("https://host/responses")
                    return await asgi.handle_async_request(request)
            class Credential:
                async def get_token(self, *scopes, **kwargs):
                    return SimpleNamespace(token="platform-local-fixture", expires_on=9999999999)
            target = {"protocol": "responses", "project_endpoint": signed.binding.project_endpoint,
                      "agent_id": signed.binding.agent_id, "agent_version": "17"}
            async with httpx.AsyncClient(transport=Platform()) as http:
                await bootstrap.read_host_binding(target, signed, credential=Credential(), http=http)
            assert effects == []
            await gate.aclose()
        finally:
            await h.close()
    asyncio.run(scenario())
