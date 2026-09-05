"""Real ACS/OPA and MCP protocol; only external HTTP/storage authorities are local doubles."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

from test_control_plane import Harness, MemoryStore, TENANT, WORKLOAD, APP, DIGEST, KEY, module as cp

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "skills/threadlight-govern/references/gateway"


def gateway(name):
    assert (GATEWAY / "dispatcher.py").exists(), "Task9 genuine gateway missing"
    if "govern_gateway" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "govern_gateway", GATEWAY / "__init__.py", submodule_search_locations=[str(GATEWAY)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
    return __import__(f"govern_gateway.{name}", fromlist=[name])


def registry():
    return {
        "schema": "threadlight-gateway-registry/v1", "tenant_id": TENANT,
        "gateway_url": "https://gateway.example/mcp",
        "deployment": {
            "agent_id": "agent-1", "agent_version": "1", "image_digest": DIGEST,
            "environment": "preproduction", "subscription": TENANT, "resource_group": "rg-staging",
        },
        "actions": [{
            "name": "refund", "policy_binding": "safe", "post_policy_binding": None,
            "workloads": [WORKLOAD], "scope": "refunds", "approval_roles": [],
            "endpoint": "https://downstream.example/refunds",
            "outcome_endpoint": "https://downstream.example/refund-outcomes",
            "credential_scope": "api://refunds/.default",
            "input_schema": {
                "type": "object", "properties": {"amount": {"type": "integer", "minimum": 1, "maximum": 1000}},
                "required": ["amount"], "additionalProperties": False,
            },
            "output_schema": {
                "type": "object", "properties": {"status": {"type": "string", "enum": ["refunded"]}},
                "required": ["status"], "additionalProperties": False,
            },
        }],
    }


class Credential:
    def __init__(self, token="downstream-only", hook=None):
        self.scopes, self.value, self.hook = [], token, hook

    async def get_token(self, scope):
        self.scopes.append(scope)
        if self.hook:
            await self.hook()
        return SimpleNamespace(token=self.value, expires_on=datetime.now(timezone.utc).timestamp() + 300)


class GatewayHarness:
    async def initialize(self, path, decision=None, post=None, approval=False, policy_id="safe"):
        import yaml
        from test_policy_bundle import bundle_module
        self.cp = Harness()
        self.cp.settings.workloads[WORKLOAD] = self.cp.settings.workloads[WORKLOAD].model_copy(
            update={"policies": [policy_id]})
        self.document = registry()
        if approval:
            self.document["actions"][0]["approval_roles"] = ["Approver"]
        if post:
            self.document["actions"][0]["post_policy_binding"] = "safe"
        source = path / "source"
        source.mkdir(parents=True)
        points = {"pre_tool_call": ("$.tool_call.args", decision or {"decision": "allow"})}
        if post:
            points["post_tool_call"] = ("$.tool_result", post)
        manifest = {
            "agent_control_specification_version": "0.3.1-beta",
            "policies": {"safe": {"type": "rego", "data": ["safe.rego"], "query": "data.gateway.pre_tool_call"}},
            "intervention_points": {
                point: {"policy_target": target, "policy": {"id": "safe", "query": f"data.gateway.{point}"}}
                for point, (target, _) in points.items()
            },
        }
        (source / "manifest.yaml").write_text(yaml.safe_dump(manifest))
        (source / "safe.rego").write_text("package gateway\nimport rego.v1\n" + "\n".join(
            f"{point} := " + (value if isinstance(value, str) else json.dumps(value))
            for point, (_, value) in points.items()))
        (source / "gateway-registry.json").write_text(json.dumps(self.document))
        self.bundle = bundle_module().build_bundle(
            source=source, destination=path / "bundle", policy_id=policy_id, version="1")
        envelope = cp("models").parse(cp("models").BundleEnvelope, json.dumps({
            "policy_id": policy_id, "version": "1", "content_digest": self.bundle.bundle_digest,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "tenant_id": TENANT, "key_id": KEY,
        }).encode())
        self.signed = await self.cp.service.publish(envelope)
        self.store = MemoryStore()
        self.calls, self.gets = [], []
        self.downstream_status = 200
        self.reply = {"receipt_id": "outcome-1", "result": {"status": "refunded"}}
        self.credential = Credential()
        self.receipts = gateway("receipts").ReceiptClient(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(self.cp.token()), http=self.cp.client)
        self.policy = await gateway("dispatcher").NativePolicy.load(
            bundle_path=self.bundle.root, signed=self.signed, signer=self.cp.signer,
            tenant=TENANT, key_id=KEY, policy_id=policy_id, version="1",
            expected_digest=self.bundle.bundle_digest,
            allowed_endpoints={"https://downstream.example/refunds",
                               "https://downstream.example/refund-outcomes"},
            gateway_url="https://gateway.example/mcp")

        async def remote(request):
            trace = request.extensions.get("trace")
            if trace:
                await trace("http11.send_request_headers.started", {})
                await trace("http11.send_request_body.started", {})
            if request.method == "POST":
                self.calls.append(request)
            else:
                self.gets.append(request)
            return httpx.Response(self.downstream_status, json=self.reply)

        self.downstream = gateway("dispatcher").DownstreamClient(
            credential=self.credential, transport=httpx.MockTransport(remote))
        self.approval = None
        self.dispatcher = self.new_dispatcher()
        return self

    def new_dispatcher(self):
        return gateway("dispatcher").GovernedDispatcher(
            policy=self.policy, auth=self.cp.auth, store=self.store, receipts=self.receipts,
            downstream=self.downstream, approvals=self.approval,
            safe_provider=lambda identity: {"scope": "refunds", "verified": True},
            approval_principal=WORKLOAD, approval_agent_id="agent-1")

    async def call(self, key="one", arguments=None, dispatcher=None, **kwargs):
        return await (dispatcher or self.dispatcher).dispatch(
            authorization="Bearer " + self.cp.token(), action="refund",
            arguments={"amount": 5} if arguments is None else arguments,
            idempotency_key=key, **kwargs)

    def receipt_bodies(self):
        return [body["receipt"] for (scope, key), (body, _) in self.cp.store.docs.items()
                if key.startswith("receipt:")]

    async def close(self):
        await self.downstream.aclose()
        await self.cp.close()


@pytest.mark.governance_runtime
def test_gateway_native_deny_receipt_zero_effects(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path, {"decision": "deny"})
        try:
            assert await h.call() == {"status": "blocked", "reason_code": "policy_deny"}
            assert h.calls == []
            receipt = h.receipt_bodies()[0]
            assert receipt["decision"] == "deny"
            assert receipt["policy_digest"] == h.bundle.bundle_digest
            assert "amount" not in json.dumps(h.receipt_bodies())
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_duplicate_same_outcome_one_effect(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            first = await h.call()
            assert first == {"status": "completed", "result": {"status": "refunded"}}
            assert await h.call(dispatcher=h.new_dispatcher()) == first
            assert len(h.calls) == 1
            assert len(h.gets) == 1
            assert h.credential.scopes == ["api://refunds/.default"] * 2
            assert h.calls[0].headers["authorization"] == "Bearer downstream-only"
            assert json.loads(h.calls[0].content) == {"amount": 5}
            assert h.calls[0].headers["x-action-hash"] == h.receipt_bodies()[0]["action_hash"]
            assert "refunded" not in json.dumps(list(h.store.docs.values()), default=str)
            assert (await h.call(arguments={"amount": 6}))["reason_code"] == "idempotency_conflict"
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("point", ["receipt", "reservation", "credential"])
def test_gateway_native_expiry_after_await_has_zero_effects(tmp_path, point):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        async def expire():
            h.policy.deadline = 0
        if point == "credential":
            h.credential.hook = expire
        else:
            obj, name = (h.receipts, "append") if point == "receipt" else (h.store, "create")
            original = getattr(obj, name)
            async def delayed(*args, **kwargs):
                result = await original(*args, **kwargs)
                await expire()
                return result
            setattr(obj, name, delayed)
        try:
            assert (await h.call())["status"] == "unavailable"
            assert h.calls == []
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault", ["store", "receipt", "outcome", "downstream", "output"])
def test_gateway_native_failures_never_retry_unknown_effect(tmp_path, fault):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        async def fail(*args, **kwargs):
            raise OSError("PRIVATE DOWNSTREAM PAYLOAD")
        if fault == "store":
            h.store.failed = True
        elif fault == "receipt":
            h.receipts.append = fail
        elif fault == "outcome":
            original = h.store.replace
            async def failed_completion(scope, key, body, etag):
                if body["state"] == "completed":
                    await fail()
                return await original(scope, key, body, etag)
            h.store.replace = failed_completion
        elif fault == "downstream":
            h.downstream_status = 403
        else:
            h.reply["result"]["secret"] = "PRIVATE DOWNSTREAM PAYLOAD"
        try:
            result = await h.call()
            assert result["status"] == "unavailable"
            assert "PRIVATE" not in json.dumps(result)
            count = len(h.calls)
            assert count == (0 if fault in ("store", "receipt") else 1)
            await h.call()
            assert len(h.calls) == count
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_transform_and_post_deny(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(
            tmp_path, {"decision": "transform", "transform": {"path": "$policy_target", "value": {"amount": 2}}},
            post={"decision": "deny"})
        try:
            assert (await h.call())["status"] == "blocked"
            assert json.loads(h.calls[0].content) == {"amount": 2}
            assert h.calls[0].headers["x-action-hash"] == h.receipt_bodies()[0]["action_hash"]
            assert h.receipt_bodies()[0]["decision"] == "transform"
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_concurrent_reservation_single_winner(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            results = await asyncio.gather(h.call(), h.call(dispatcher=h.new_dispatcher()))
            assert len(h.calls) == 1
            assert all(r["status"] in ("completed", "unavailable") for r in results)
            assert any(r["status"] == "completed" for r in results)
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("arguments", [{"amount": "5"}, {"amount": True},
    {"amount": 5, "safe": {"verified": True}}, {"amount": 0}, {"url": "https://evil.example"}])
def test_gateway_native_strict_arguments(tmp_path, arguments):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            assert (await h.call(arguments=arguments))["status"] == "blocked"
            assert h.calls == []
            assert not h.receipt_bodies()
        finally:
            await h.close()
    asyncio.run(case())


def test_gateway_installed_package_has_no_maf_import():
    import subprocess
    subprocess.run([sys.executable, "-c",
        "import govern_gateway.server, sys; assert 'agent_framework' not in sys.modules"], check=True)


@pytest.mark.governance_runtime
def test_gateway_native_actual_mcp_authenticated_protocol(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url="https://gateway.example") as client:
                    base = {"Accept": "application/json, text/event-stream"}
                    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"}}}
                    assert (await client.post("/mcp", json=init, headers=base)).status_code == 401
                    headers = {**base, "Authorization": "Bearer " + h.cp.token()}
                    assert (await client.post("/mcp", json=init, headers=headers)).status_code == 200
                    response = await client.post("/mcp", headers=headers, json={
                        "jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                    tools = response.json()["result"]["tools"]
                    assert [tool["name"] for tool in tools] == ["refund"]
                    assert tools[0]["inputSchema"] == h.document["actions"][0]["input_schema"]
                    headers["Idempotency-Key"] = "mcp-one"
                    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "refund", "arguments": {"amount": 5}}}
                    reply = (await client.post("/mcp", headers=headers, json=call)).json()["result"]
                    assert reply["structuredContent"]["status"] == "completed"
                    assert len(h.calls) == 1
                    for method in ("GET", "DELETE"):
                        assert (await client.request(method, "/mcp", headers=base)).status_code == 401
                    call["params"]["arguments"] = {"amount": "PRIVATE INVALID"}
                    response = await client.post("/mcp", headers=headers, json=call)
                    assert "PRIVATE INVALID" not in response.text
                    assert len(h.calls) == 1
                    call["params"]["name"] = "shell"
                    assert (await client.post("/mcp", headers=headers, json=call)).json()["result"]["isError"]
                    assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("tamper", ["signature", "tenant", "version", "expiry", "registry", "binding"])
def test_gateway_native_signed_registry_cannot_be_forged(tmp_path, tamper):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            signed = h.signed.model_dump(mode="json")
            if tamper == "signature":
                signed["signature"] = "AAAA"
            elif tamper in ("tenant", "version", "expiry"):
                key, value = {
                    "tenant": ("tenant_id", APP), "version": ("version", "2"),
                    "expiry": ("expires_at", "2000-01-01T00:00:00+00:00"),
                }[tamper]
                signed["envelope"][key] = value
            else:
                doc = deepcopy(h.document)
                if tamper == "registry":
                    doc["actions"][0]["endpoint"] = "https://evil.example/refunds"
                else:
                    doc["actions"][0]["policy_binding"] = "none"
                (h.bundle.root / "gateway-registry.json").write_text(json.dumps(doc))
            with pytest.raises(Exception):
                await gateway("dispatcher").NativePolicy.load(
                    bundle_path=h.bundle.root, signed=signed, signer=h.cp.signer,
                    tenant=TENANT, key_id=KEY, policy_id="safe", version="1",
                    expected_digest=h.bundle.bundle_digest,
                    allowed_endpoints={a for item in h.document["actions"]
                                       for a in (item["endpoint"], item["outcome_endpoint"])},
                    gateway_url=h.document["gateway_url"])
            assert h.calls == []
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_approval_consume_replay_and_fresh_positive(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path, {"decision": "escalate"}, approval=True)
        approvals = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client, poll_interval=0.01)
        post = approvals.post
        async def human_decision(body):
            response = await post(body)
            if body["operation"] == "request" and response[0] == 202:
                decided = await h.cp.post("decide", human=True, intent=body["intent"],
                                         approved=True, approving_role="Approver")
                assert decided.status_code == 200
            return response
        approvals.post = human_decision
        h.approval = approvals
        h.dispatcher = h.new_dispatcher()
        try:
            first = await h.call()
            assert first["status"] == "completed"
            original = [body for (_, k), (body, _) in h.cp.store.docs.items() if k.startswith("approval:")]
            assert len(original) == 1 and original[0]["state"] == "consumed"
            assert await h.call(dispatcher=h.new_dispatcher()) == first
            assert len(h.calls) == 1
            assert len([k for _, k in h.cp.store.docs if k.startswith("approval:")]) == 1
            resolve = approvals.resolve
            async def replay(intent):
                return cp("models").parse(cp("models").ApprovalGrant,
                                         json.dumps(original[0]["grant"]).encode())
            approvals.resolve = replay
            assert (await h.call(key="second"))["status"] == "unavailable"
            assert len(h.calls) == 1
            approvals.resolve = resolve
            assert (await h.call(key="third"))["status"] == "completed"
            assert len(h.calls) == 2
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_transformed_approval_binds_actual_effect(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path, {
            "decision": "transform", "transform": {"path": "$policy_target", "value": {"amount": 2}}},
            approval=True)
        approvals = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client, poll_interval=0.01)
        post = approvals.post
        async def decide(body):
            response = await post(body)
            if body["operation"] == "request":
                assert (await h.cp.post("decide", human=True, intent=body["intent"],
                    approved=True, approving_role="Approver")).status_code == 200
            return response
        approvals.post = decide
        h.approval = approvals
        h.dispatcher = h.new_dispatcher()
        try:
            assert (await h.call())["status"] == "completed"
            grant = next(body for (_, k), (body, _) in h.cp.store.docs.items() if k.startswith("approval:"))
            assert grant["intent"]["action_hash"] == h.calls[0].headers["x-action-hash"]
            assert json.loads(h.calls[0].content) == {"amount": 2}
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_transport_wait_expiry_and_no_redirect(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        async def queued(request):
            await asyncio.sleep(0)
            h.policy.deadline = 0
            await request.extensions["trace"]("http11.send_request_headers.started", {})
            h.calls.append(request)
            return httpx.Response(200, json=h.reply)
        await h.downstream.aclose()
        h.downstream = gateway("dispatcher").DownstreamClient(
            credential=h.credential, transport=httpx.MockTransport(queued))
        h.dispatcher = h.new_dispatcher()
        try:
            assert (await h.call())["status"] == "unavailable"
            assert not h.calls
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_post_transform_duplicate_uses_same_enforced_arguments(tmp_path):
    async def case():
        post = ('{"decision":"allow"} if { input.snapshot.tool_call.args.amount == 2 } '
                'else := {"decision":"deny"}')
        h = await GatewayHarness().initialize(tmp_path, {
            "decision": "transform", "transform": {"path": "$policy_target", "value": {"amount": 2}}},
            post=post)
        try:
            first = await h.call()
            assert first["status"] == "completed"
            assert await h.call() == first
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


def test_registry_rejects_bounded_but_nonfinite_or_untyped_values():
    value = registry()
    value["actions"][0]["input_schema"]["properties"]["amount"] = {"type": "string"}
    with pytest.raises(Exception):
        gateway("dispatcher").parse(gateway("dispatcher").Registry, json.dumps(value).encode())


def test_downstream_idempotency_is_scoped_and_provenance_is_complete(tmp_path):
    async def case():
        calls = []
        client = gateway("dispatcher").DownstreamClient(credential=Credential(),
            transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(
                200, json={"receipt_id": "one", "result": {"status": "refunded"}})))
        action = gateway("dispatcher").parse(gateway("dispatcher").Action,
                                             json.dumps(registry()["actions"][0]).encode())
        facts = {"tenant": TENANT, "subject": WORKLOAD, "action": "refund",
                 "policy": DIGEST, "deployment": registry()["deployment"]}
        try:
            await client.request(action=action, arguments={"amount": 1}, key="one",
                action_hash=DIGEST, provenance="receipt", facts=facts, guard=lambda: None)
            assert calls[0].headers["x-tenant-id"] == TENANT
            assert calls[0].headers["x-action-id"] == "refund"
            assert calls[0].headers["x-policy-digest"] == DIGEST
        finally:
            await client.aclose()
    asyncio.run(case())


def test_gateway_pin_gate_rejects_missing_or_skipped_native_cases(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "gateway_pin_gate", ROOT / "scripts/ci/run-governance-pin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert hasattr(runner, "verify_gateway_junit"), "native gateway must be part of the exact-pin gate"
    report = tmp_path / "gateway.xml"
    for contents in ('<testsuite/>', '<testsuite><testcase name="unrelated"/></testsuite>',
                     '<testsuite><testcase name="test_gateway_native_deny_receipt_zero_effects">'
                     '<skipped/></testcase></testsuite>'):
        report.write_text(contents)
        with pytest.raises(RuntimeError):
            runner.verify_gateway_junit(report)


@pytest.mark.governance_runtime
@pytest.mark.parametrize("expire", [False, True])
def test_gateway_native_httpcore_checks_after_connection_wait(tmp_path, expire):
    """Real httpx/httpcore/h11: only socket/TLS bytes are a fake external transport."""
    import httpcore
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        wire = []
        response = json.dumps(h.reply).encode()
        class Stream(httpcore.AsyncNetworkStream):
            async def read(self, max_bytes, timeout=None):
                return (b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(response)).encode()
                        + b"\r\nContent-Type: application/json\r\n\r\n" + response)
            async def write(self, buffer, timeout=None):
                wire.append(buffer)
            async def aclose(self):
                pass
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                return self
            def get_extra_info(self, info):
                return None
        class Network(httpcore.AsyncNetworkBackend):
            async def connect_tcp(self, *args, **kwargs):
                await asyncio.sleep(0)
                if expire:
                    h.policy.deadline = 0
                return Stream()
        transport = httpx.AsyncHTTPTransport(retries=0, http2=False)
        await transport._pool.aclose()
        transport._pool = httpcore.AsyncConnectionPool(network_backend=Network(), retries=0, http2=False)
        await h.downstream.aclose()
        h.downstream = gateway("dispatcher").DownstreamClient(credential=h.credential, transport=transport)
        h.dispatcher = h.new_dispatcher()
        try:
            result = await h.call()
            assert result["status"] == ("unavailable" if expire else "completed")
            if expire:
                assert wire == []
            else:
                assert b'{"amount":5}' in b"".join(wire)
                assert len(h.receipt_bodies()) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_redirect_does_not_forward_token(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        requests = []
        def redirect(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "https://evil.example"})
        await h.downstream.aclose()
        h.downstream = gateway("dispatcher").DownstreamClient(
            credential=h.credential, transport=httpx.MockTransport(redirect))
        h.dispatcher = h.new_dispatcher()
        try:
            assert (await h.call())["status"] == "unavailable"
            assert len(requests) == 1
            assert str(requests[0].url) == "https://downstream.example/refunds"
            assert (await h.call())["reason_code"] == "outcome_unknown"
            assert len(requests) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_post_transform_removes_private_payload(tmp_path, caplog):
    async def case():
        h = await GatewayHarness().initialize(tmp_path, post={
            "decision": "transform", "transform": {"path": "$policy_target", "value": {"status": "refunded"}}})
        h.reply["result"]["secret"] = "PRIVATE-DOWNSTREAM-RESULT"
        try:
            result = await h.call()
            assert result == {"status": "completed", "result": {"status": "refunded"}}
            assert "PRIVATE-DOWNSTREAM-RESULT" not in json.dumps(h.receipt_bodies())
            assert "PRIVATE-DOWNSTREAM-RESULT" not in caplog.text
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_malformed_mcp_never_echoes_payload(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url="https://gateway.example") as client:
                    response = await client.post("/mcp", headers={
                        "Authorization": "Bearer " + h.cp.token(),
                        "Accept": "application/json, text/event-stream",
                    }, json={"jsonrpc": "WRONG-PRIVATE-PAYLOAD", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": {"PRIVATE-PAYLOAD": 1}}})
                    assert response.status_code == 400
                    assert "PRIVATE-PAYLOAD" not in response.text
                    assert not h.calls
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("mutation", ["body", "url", "header"])
def test_gateway_native_async_http_hook_cannot_change_authorized_wire(tmp_path, mutation):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        async def mutate(request):
            await asyncio.sleep(0)
            if mutation == "body":
                request.stream = httpx.ByteStream(b'{"amount":999}')
                request._content = b'{"amount":999}'
            elif mutation == "url":
                request.url = httpx.URL("https://downstream.example/other")
            else:
                request.headers["X-Action-Hash"] = DIGEST
        h.downstream.http.event_hooks["request"].append(mutate)
        try:
            assert (await h.call())["status"] == "unavailable"
            assert h.calls == []
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_startup_requires_pinned_opa(tmp_path, monkeypatch):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        monkeypatch.setenv("ACS_OPA_PATH", str(tmp_path / "missing-opa"))
        try:
            with pytest.raises(Exception):
                await gateway("dispatcher").NativePolicy.load(
                    bundle_path=h.bundle.root, signed=h.signed, signer=h.cp.signer,
                    tenant=TENANT, key_id=KEY, policy_id="safe", version="1",
                    expected_digest=h.bundle.bundle_digest,
                    allowed_endpoints={a for item in h.document["actions"]
                                       for a in (item["endpoint"], item["outcome_endpoint"])},
                    gateway_url=h.document["gateway_url"])
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_changed_key_reports_conflict_code(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            assert (await h.call())["status"] == "completed"
            conflict = await h.call(arguments={"amount": 6})
            assert conflict["status_code"] == 409
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


def test_gateway_response_rejects_compressed_or_oversized_payload():
    import gzip
    async def case():
        action = gateway("dispatcher").parse(gateway("dispatcher").Action,
                                             json.dumps(registry()["actions"][0]).encode())
        large = json.dumps({"receipt_id": "one", "result": {"status": "x" * 20000}}).encode()
        for content, headers in ((large, {}), (gzip.compress(b'{"receipt_id":"one","result":{}}'),
                                              {"Content-Encoding": "gzip"})):
            client = gateway("dispatcher").DownstreamClient(credential=Credential(),
                transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content, headers=headers)))
            try:
                with pytest.raises(Exception):
                    await client.request(action=action, arguments={"amount": 1}, key="one",
                        action_hash=DIGEST, provenance="receipt",
                        facts={"tenant": TENANT, "subject": WORKLOAD, "action": "refund",
                               "policy": DIGEST, "deployment": registry()["deployment"]},
                        guard=lambda: None)
            finally:
                await client.aclose()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_gateway_native_bundle_id_differs_from_native_binding(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path, policy_id="returns-safe")
        try:
            assert h.document["actions"][0]["policy_binding"] == "safe"
            assert (await h.call())["status"] == "completed"
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())
