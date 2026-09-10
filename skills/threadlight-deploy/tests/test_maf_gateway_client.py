"""Actual MAF functions, MCP protocol and native ACS gateway; external stores are fixtures."""
import asyncio
from contextlib import AsyncExitStack, suppress
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
from test_gateway import Credential, GatewayHarness, gateway

REFERENCE = ROOT / "skills/threadlight-deploy/references/governance/maf_gateway.py"


def client_module():
    assert REFERENCE.is_file(), "Executable MAF governed MCP client is missing"
    spec = importlib.util.spec_from_file_location("maf_gateway", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def hosted_module():
    client_module()
    path = REFERENCE.with_name("maf-gateway-container.py")
    assert path.is_file(), "Executable governed MAF host is missing"
    spec = importlib.util.spec_from_file_location("maf_gateway_host", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.governance_runtime
def test_gateway_file_host_defaults_to_loopback_outside_foundry():
    module = hosted_module()
    assert hasattr(module, "listen_host")
    assert module.listen_host({}) == "127.0.0.1"
    assert module.listen_host({"FOUNDRY_AGENT_NAME": "agent"}) == "127.0.0.1"
    assert module.listen_host({"FOUNDRY_AGENT_NAME": "agent", "FOUNDRY_AGENT_VERSION": "1"}) == "0.0.0.0"


@pytest.mark.governance_runtime
def test_maf_gateway_accepts_only_fixed_canonical_configured_paths():
    module = client_module()

    async def authorize():
        pass

    for url in ("https://gateway.example/mcp", "https://gateway.example/selected/mcp"):
        client = module.GovernedMCPTools(
            url=url, scope="api://gateway/.default", credential=Credential(),
            authorize=authorize, selected_tools=["refund"])
        assert client.url == url
    for url in ("http://gateway.example/mcp", "https://gateway.example/",
                "https://gateway.example/mcp?target=elsewhere", "https://gateway.example/mcp#fragment",
                "https://user:password@gateway.example/mcp", "https://gateway.example:8443/mcp"):
        with pytest.raises(ValueError):
            module.GovernedMCPTools(
                url=url, scope="api://gateway/.default", credential=Credential(),
                authorize=authorize, selected_tools=["refund"])


def test_maf_gateway_dependency_and_skill_contract():
    project = tomllib.loads(REFERENCE.with_name("pyproject-maf.toml").read_text())
    assert "httpcore==1.0.9" in project["project"]["dependencies"]
    skill = (ROOT / "skills/threadlight-deploy/SKILL.md").read_text()
    assert 'version: "1.8.0"' in skill
    assert "maf-gateway-container.py" in skill
    assert "OBO is not provided by this app-only path" in skill
    assert "no mixed local/gateway bindings" in skill
    collector = ROOT / "skills/threadlight-safe-check"
    package = tomllib.loads((collector / "pyproject.toml").read_text())
    assert package["project"]["version"] == "0.3.0"
    assert {"*.yaml", "*.yml"} <= set(package["tool"]["setuptools"]["package-data"]["govern_deployment"])
    assert 'version: "1.4.0"' in (collector / "SKILL.md").read_text()
    assert "MAF Responses can use the gateway producer" in (collector / "SKILL.md").read_text()


@pytest.mark.governance_runtime
@pytest.mark.parametrize("decision,effects", [("allow", 1), ("deny", 0)])
def test_maf_function_calls_actual_governed_mcp(tmp_path, decision, effects):
    async def run():
        import httpx
        from agent_framework import FunctionTool
        module = client_module()
        h = await GatewayHarness().initialize(tmp_path, {"decision": decision})
        app = gateway("server").create_app(h.dispatcher)
        observed = []

        class ObservedASGI(httpx.ASGITransport):
            async def handle_async_request(self, request):
                observed.append((request.method, request.url, dict(request.headers),
                                 json.loads(request.content) if request.content else None))
                return await super().handle_async_request(request)

        authorizations = []

        async def authorize():
            h.policy.fresh()
            authorizations.append(True)

        try:
            async with app.router.lifespan_context(app):
                tools = module.GovernedMCPTools(
                    url="https://gateway.example/mcp",
                    scope="api://gateway/.default",
                    credential=Credential(h.cp.token()), authorize=authorize,
                    selected_tools=["refund"], transport_factory=lambda: ObservedASGI(app=app))
                await tools.connect()
                assert len(tools.functions) == 1 and isinstance(tools.functions[0], FunctionTool)
                original_schema = deepcopy(tools.functions[0].parameters())
                if decision == "deny":
                    with pytest.raises(module.GatewayToolError, match="threadlight:gateway_denied"):
                        await tools.functions[0].invoke(arguments={"amount": 5}, skip_parsing=True)
                else:
                    assert await tools.functions[0].invoke(
                        arguments={"amount": 5}, skip_parsing=True) == {"status": "refunded"}
                assert tools.functions[0].parameters() == original_schema
                assert len(h.calls) == effects
                calls = [entry for entry in observed if entry[3] and entry[3].get("method") == "tools/call"]
                assert len(calls) == 1
                assert calls[0][2]["idempotency-key"]
                assert calls[0][3]["params"]["arguments"] == {"amount": 5}
                assert all(entry[2].get("authorization", "").startswith("Bearer ") for entry in observed)
                assert not any(entry[0] in ("GET", "DELETE") for entry in observed)
                assert authorizations
                assert h.receipt_bodies()[0]["decision"] == decision
                assert "amount" not in json.dumps(h.receipt_bodies())
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_maf_gateway_rechecks_after_credentials_and_preserves_unselected_tools(tmp_path):
    async def run():
        import httpx
        from agent_framework import tool
        module = client_module()
        h = await GatewayHarness().initialize(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        valid = True
        seen = []

        async def authorize():
            if not valid:
                raise RuntimeError("authorization expired")

        class Observe(httpx.ASGITransport):
            async def handle_async_request(self, request):
                seen.append(request)
                return await super().handle_async_request(request)

        credential = Credential(h.cp.token())
        try:
            async with app.router.lifespan_context(app):
                tools = module.GovernedMCPTools(
                    url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=credential, authorize=authorize, selected_tools=["refund"],
                    transport_factory=lambda: Observe(app=app))
                await tools.connect()
                seen.clear()

                async def revoke():
                    nonlocal valid
                    valid = False

                credential.hook = revoke
                with pytest.raises(module.GatewayToolError):
                    await tools.functions[0].invoke(arguments={"amount": 5}, skip_parsing=True)
                assert not seen and not h.calls

                @tool
                def read() -> str:
                    return "unbound-read"

                assert await read.invoke(skip_parsing=True) == "unbound-read"
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_maf_mcp_refresh_cannot_extend_an_inflight_session_lease(tmp_path, monkeypatch):
    async def run():
        import httpx
        module = client_module()
        h = await GatewayHarness().initialize(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        base = module.time.time()
        clock = [base]
        monkeypatch.setattr(module.time, "time", lambda: clock[0])

        class RefreshingCredential:
            calls = 0

            async def get_token(self, scope):
                self.calls += 1
                if self.calls > 1:
                    clock[0] = base + 6
                return SimpleNamespace(token=h.cp.token(),
                                       expires_on=base + (5 if self.calls == 1 else 300))

        async def authorize():
            h.policy.fresh()

        try:
            async with app.router.lifespan_context(app):
                tools = module.GovernedMCPTools(
                    url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=RefreshingCredential(), authorize=authorize, selected_tools=["refund"],
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                with pytest.raises(module.GatewayToolError):
                    await tools.connect()
                assert not h.calls
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("selected", [[], ["refund", "refund"], ["*"], ["execute_shell"]])
def test_maf_gateway_requires_exact_selected_inventory(tmp_path, selected):
    async def run():
        import httpx
        module = client_module()
        h = await GatewayHarness().initialize(tmp_path)
        app = gateway("server").create_app(h.dispatcher)

        async def authorize():
            h.policy.fresh()

        try:
            async with app.router.lifespan_context(app):
                with pytest.raises((ValueError, module.GatewayToolError)):
                    tools = module.GovernedMCPTools(
                        url="https://gateway.example/mcp", scope="api://gateway/.default",
                        credential=Credential(h.cp.token()), authorize=authorize,
                        selected_tools=selected, transport_factory=lambda: httpx.ASGITransport(app=app))
                    await tools.connect()
                assert not h.calls
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("decision,effects", [("allow", 1), ("deny", 0)])
def test_real_maf_agent_dispatches_only_selected_gateway_actions(tmp_path, decision, effects):
    async def run():
        import httpx
        from agent_framework import tool
        from skills._shared.local_model_fixture import native_model_client, tool_responses
        module = client_module()
        assert hasattr(module, "create_gateway_agent"), "Real MAF agent factory is missing"
        h = await GatewayHarness().initialize(tmp_path, {"decision": decision})
        app = gateway("server").create_app(h.dispatcher)

        @tool
        def read() -> str:
            return "unbound-read"

        document = {
            "framework": "microsoft-agent-framework",
            "governance": {
                "mode": "selective", "lifecycle_bindings": [],
                "environment_modes": {"development": "evaluate_only", "staging": "evaluate_only",
                                      "preproduction": "enforce", "production": "enforce"},
            },
            "tools": [
                {"id": "refund", "consequence": "write", "policy_binding": "safe",
                 "enforcement_path": "governed-tool-gateway", "intervention_points": ["pre_tool_call"],
                 "safe_principles": ["Safety"], "requires": []},
                {"id": "read", "consequence": "read", "policy_binding": "none",
                 "enforcement_path": "none", "intervention_points": [],
                 "safe_principles": [], "requires": []},
            ],
        }
        config = {"contract": document, "agent_id": "agent-1", "environment": "preproduction",
                  "gateway_url": "https://gateway.example/mcp", "gateway_scope": "api://gateway/.default"}
        model = native_model_client(tool_responses("refund", {"amount": 5}))

        async def authorize():
            h.policy.fresh()

        try:
            async with app.router.lifespan_context(app):
                agent = await module.create_gateway_agent(
                    config=config, client=model, local_tools=[read], instructions="Use the selected tools.",
                    credential=Credential(h.cp.token()), authorize=authorize,
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                result = await agent.run("Apply the decision")
                assert result.text == "finished"
                assert len(h.calls) == effects
                assert len(model.requests) == 2
                assert h.receipt_bodies()[0]["decision"] == decision
                with pytest.raises(module.GatewayToolError, match="configuration_override"):
                    await agent.run("Run code", tools=[{"type": "code_interpreter"}])
                assert len(h.calls) == effects
                assert await read.invoke(skip_parsing=True) == "unbound-read"
                with pytest.raises(ValueError, match="local_tool_inventory"):
                    await module.create_gateway_agent(
                        config=config, client=model, local_tools=[read, read], instructions="No duplicates",
                        credential=Credential(h.cp.token()), authorize=authorize,
                        transport_factory=lambda: httpx.ASGITransport(app=app))
        finally:
            await model.client.close()
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_native_responses_host_uses_signed_authority_and_mcp(tmp_path, monkeypatch):
    async def run():
        import httpx
        from test_runtime_provider import native_model_client, tool_responses
        from test_control_plane import TENANT, KEY
        module = hosted_module()
        h = await GatewayHarness().initialize(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        config = {
            "agent_id": "agent-1", "environment": "preproduction",
            "tenant_id": TENANT, "key_id": KEY, "policy_id": "safe", "policy_version": "1",
            "policy_digest": h.bundle.bundle_digest, "gateway_url": "https://gateway.example/mcp",
            "gateway_scope": "api://gateway/.default", "control_plane_url": "https://control.example",
            "control_plane_scope": "api://governance/.default",
            "contract": {
                "framework": "microsoft-agent-framework",
                "governance": {
                    "mode": "selective", "lifecycle_bindings": [],
                    "environment_modes": {"development": "evaluate_only", "staging": "evaluate_only",
                                          "preproduction": "enforce", "production": "enforce"},
                },
                "tools": [{"id": "refund", "consequence": "write", "policy_binding": "safe",
                           "enforcement_path": "governed-tool-gateway", "intervention_points": ["pre_tool_call"],
                           "safe_principles": ["Safety"], "requires": []}],
            },
        }
        (tmp_path / "copilot-instructions.md").write_text("Use the registered business action.")
        monkeypatch.setattr(module, "BASE", tmp_path)
        model = native_model_client(tool_responses("refund", {"amount": 5}))
        gateway_transport = httpx.ASGITransport(app=app)

        class Router(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if request.url.host == "control.example":
                    return await h.cp.client._transport.handle_async_request(request)
                assert request.url.host == "gateway.example"
                return await gateway_transport.handle_async_request(request)

        try:
            async with app.router.lifespan_context(app), AsyncExitStack() as stack:
                http = await stack.enter_async_context(httpx.AsyncClient(transport=Router()))
                host = await module.build_host(
                    config, credential=Credential(h.cp.token()), signer=h.cp.signer,
                    stack=stack, client=model, http=http,
                    application=SimpleNamespace(tools=[], middleware=[]),
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                async with host.router.lifespan_context(host):
                    async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=host), base_url="http://local-agent") as local:
                        response = await local.post("/responses", json={
                            "input": "Apply the decision", "stream": True, "store": False})
                        assert response.status_code == 200, response.text
                        assert "response.completed" in response.text, response.text
                        assert len(h.calls) == 1
                        assert h.receipt_bodies()[0]["decision"] == "allow"
                        async def unavailable_key():
                            raise RuntimeError("signing key unavailable")

                        monkeypatch.setattr(h.cp.signer, "health", unavailable_key)
                        assert (await host._readiness_endpoint(None)).status_code == 503
        finally:
            await model.client.close()
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("approved", [True, False])
def test_maf_gateway_uses_authenticated_one_use_human_decision(tmp_path, approved):
    async def run():
        import httpx
        from test_gateway import cp
        module = client_module()
        h = await GatewayHarness().initialize(tmp_path, {"decision": "escalate"}, approval=True)
        approvals = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client, poll_interval=0.01)
        post = approvals.post

        async def decide(body):
            response = await post(body)
            if body["operation"] == "request" and response[0] == 202:
                result = await h.cp.post(
                    "decide", human=True, intent=body["intent"],
                    approved=approved, approving_role="Approver")
                assert result.status_code == 200
            return response

        approvals.post = decide
        h.approval = approvals
        h.dispatcher = h.new_dispatcher()
        app = gateway("server").create_app(h.dispatcher)

        async def authorize():
            h.policy.fresh()

        try:
            async with app.router.lifespan_context(app):
                tools = module.GovernedMCPTools(
                    url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=Credential(h.cp.token()), authorize=authorize, selected_tools=["refund"],
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                await tools.connect()
                if approved:
                    assert await tools.functions[0].invoke(
                        arguments={"amount": 5}, skip_parsing=True) == {"status": "refunded"}
                else:
                    with pytest.raises(module.GatewayToolError):
                        await tools.functions[0].invoke(arguments={"amount": 5}, skip_parsing=True)
                assert len(h.calls) == int(approved)
                record = next(body for (_, key), (body, _) in h.cp.store.docs.items()
                              if key.startswith("approval:"))
                assert record["state"] == "consumed"

                async def replay(intent):
                    return cp("models").parse(cp("models").ApprovalGrant, json.dumps(record["grant"]).encode())

                approvals.resolve = replay
                with pytest.raises(module.GatewayToolError):
                    await tools.functions[0].invoke(arguments={"amount": 5}, skip_parsing=True)
                assert len(h.calls) == int(approved)
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault,reason", [
    ("output", "threadlight:gateway_output_denied"),
    ("lost-outcome", "threadlight:gateway_outcome_unknown"),
])
def test_maf_gateway_does_not_label_post_effect_failure_as_authorization_deny(tmp_path, fault, reason):
    async def run():
        import httpx
        module = client_module()
        h = await GatewayHarness().initialize(tmp_path, post={"decision": "deny"} if fault == "output" else None)
        if fault == "lost-outcome":
            original = h.downstream.request

            async def lost(*args, **kwargs):
                await original(*args, **kwargs)
                raise OSError("private lost acknowledgement")

            h.downstream.request = lost
        app = gateway("server").create_app(h.dispatcher)

        async def authorize():
            h.policy.fresh()

        try:
            async with app.router.lifespan_context(app):
                tools = module.GovernedMCPTools(
                    url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=Credential(h.cp.token()), authorize=authorize, selected_tools=["refund"],
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                await tools.connect()
                with pytest.raises(module.GatewayToolError, match=reason):
                    await tools.functions[0].invoke(arguments={"amount": 5}, skip_parsing=True)
                assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("revoke", [False, True])
def test_maf_mcp_actual_tls_send_rechecks_authority(tmp_path, revoke):
    async def run():
        import httpx
        from test_bootstrap_transport import tls_contexts
        module = client_module()
        server_ssl, client_ssl = tls_contexts(tmp_path)
        socket_path = ROOT / ".governance-validation" / f"mcp-{uuid.uuid4().hex}.sock"
        received, finished = [], asyncio.Event()
        checks = 0

        async def handle(reader, writer):
            try:
                with suppress(ConnectionResetError):
                    raw = await reader.read(4096)
                    if raw:
                        received.append(raw)
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                        await writer.drain()
            finally:
                writer.close()
                with suppress(ConnectionResetError):
                    await writer.wait_closed()
                finished.set()

        async def guard():
            nonlocal checks
            checks += 1
            if revoke and checks > 1:
                raise module.GatewayToolError("threadlight:gateway_authorization_expired")

        server = await asyncio.start_unix_server(handle, path=str(socket_path), ssl=server_ssl)
        try:
            transport = module.AuthorizedTransport(
                httpx.AsyncHTTPTransport(uds=str(socket_path), verify=client_ssl, http2=False), guard)
            async with httpx.AsyncClient(transport=transport, timeout=2) as client:
                if revoke:
                    with pytest.raises(module.GatewayToolError):
                        await client.post("https://localhost/mcp", json={"method": "ping"})
                else:
                    assert (await client.post("https://localhost/mcp", json={"method": "ping"})).status_code == 200
            await asyncio.wait_for(finished.wait(), 2)
            assert bool(received) is not revoke
            assert checks >= 2
        finally:
            server.close()
            await server.wait_closed()
            socket_path.unlink(missing_ok=True)
    asyncio.run(run())
