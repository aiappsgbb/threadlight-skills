"""Real loopback HTTPX/OpenAI sends, not mocked transport completion events."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import ssl

import pytest

from test_control_plane import APP, DIGEST, KEY, TENANT, WORKLOAD, module
from test_remote_bootstrap import binding, harness
from test_runtime_provider import contract, provider, runtime

pytestmark = pytest.mark.governance_runtime


def tls_contexts(path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert, private = path / "localhost.pem", path / "localhost-key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, private)
    client = ssl.create_default_context(cafile=str(cert))
    return server, client


@asynccontextmanager
async def native_gate(path, *, seconds=60, tool=False):
    from govern_control_plane.bootstrap import BootstrapBinding, BootstrapGate
    from govern_control_plane.models import BundleEnvelope, canonical, parse
    h = await harness()
    point = "pre_tool_call" if tool else "pre_model_call"
    p, authority, built = provider(path, document=contract(
        points=("pre_tool_call",) if tool else (), lifecycle=() if tool else ("pre_model_call",)),
        decisions={point: {"decision": "allow"}}, principal=WORKLOAD, tenant=TENANT,
        agent_version="17", image_digest=DIGEST)
    h.store.blobs.clear()
    await h.service.publish(BundleEnvelope(
        policy_id="safe", version="1", content_digest=built.bundle_digest,
        tenant_id=TENANT, key_id=KEY, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    document = {**binding(), "native_policy_digest": built.bundle_digest, "policy_digest": built.bundle_digest,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}
    signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(document)))
    from govern_control_plane.bootstrap import BootstrapClient
    from azure.core.credentials import AccessToken
    class Credential:
        async def get_token(self, scope):
            return AccessToken(h.token(), 4102444800)
    client = BootstrapClient(base_url="https://control.example", scope="api://governance/.default",
                             credential=Credential(), http=h.client)
    async def fetch():
        return await client.load("attempt-1")
    async def initialize(binding, stack):
        return lambda scope, receive, send: None
    gate = BootstrapGate(expected=document, fetch=fetch, signer=h.service.signer, initialize=initialize)
    await gate.activate()
    p.bootstrap_gate = gate
    try:
        yield p, gate, h
    finally:
        await gate.aclose()
        await client.aclose()
        await h.close()


@pytest.mark.parametrize("secure", [False, True])
@pytest.mark.parametrize("failure", ["expiry", "revocation", "valid"])
def test_real_model_pool_wait_reauthorizes_before_any_http_bytes(tmp_path, secure, failure):
    import httpx
    from openai import AsyncOpenAI
    from agent_framework.openai import OpenAIChatClient

    async def scenario():
        server_ssl, client_ssl = tls_contexts(tmp_path) if secure else (None, None)
        occupied, release, entering = asyncio.Event(), asyncio.Event(), asyncio.Event()
        model_bytes, observed_traces, tasks = [], [], set()
        async def connection(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                while True:
                    try:
                        header = await reader.readuntil(b"\r\n\r\n")
                    except (asyncio.IncompleteReadError, ConnectionError):
                        return
                    length = next((int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                                   if line.lower().startswith(b"content-length:")), 0)
                    body = await reader.readexactly(length)
                    if header.startswith(b"GET /occupied "):
                        occupied.set()
                        await release.wait()
                        content = b"{}"
                    else:
                        model_bytes.append(header + body)
                        content = json.dumps({
                            "id": "resp_loopback", "object": "response", "created_at": 0,
                            "status": "completed", "model": "test", "output": [],
                        }).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                 + str(len(content)).encode() + b"\r\n\r\n" + content)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        server = await asyncio.start_server(connection, "127.0.0.1", 0, ssl=server_ssl)
        port = server.sockets[0].getsockname()[1]
        url = f"{'https' if secure else 'http'}://127.0.0.1:{port}"
        async def request_hook(request):
            if request.url.path.endswith("/responses"):
                async def original_trace(event, info):
                    observed_traces.append(event)
                request.extensions["trace"] = original_trace
                entering.set()
        try:
            async with native_gate(tmp_path / "runtime", seconds=2 if failure == "expiry" else 60) as (p, gate, h):
                async with httpx.AsyncClient(verify=client_ssl or True, trust_env=False,
                        limits=httpx.Limits(max_connections=1), timeout=10,
                        event_hooks={"request": [request_hook]}) as wire:
                    blocking = asyncio.create_task(wire.get(url + "/occupied"))
                    await asyncio.wait_for(occupied.wait(), 5)
                    sdk = AsyncOpenAI(base_url=url + "/v1", api_key="loopback-only", http_client=wire, max_retries=0)
                    model = OpenAIChatClient(model="test", async_client=sdk)
                    agent = runtime().create_governed_agent(p, client=model, tools=[], id="agent-1")
                    pending = asyncio.create_task(agent.run("local model transport check"))
                    await asyncio.wait_for(entering.wait(), 5)
                    await asyncio.sleep(0.05)
                    if failure == "expiry":
                        await asyncio.sleep(max(0, (gate.signed.binding.expires_at - datetime.now(timezone.utc)).total_seconds()) + 0.05)
                    elif failure == "revocation":
                        h.keys.revoked = True
                    release.set()
                    await blocking
                    outcome = await asyncio.gather(pending, return_exceptions=True)
                    assert len(model_bytes) == (1 if failure == "valid" else 0), (
                        f"ACTUAL_HTTP_MODEL_SENDS_AFTER_{failure.upper()}: {len(model_bytes)}")
                    assert observed_traces, "caller trace was discarded"
                    assert isinstance(outcome[0], BaseException) == (failure != "valid")
        finally:
            release.set()
            server.close()
            await server.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("wait_at", ["tls", "credential", "caller-trace"])
@pytest.mark.parametrize("failure", ["expiry", "revocation", "valid"])
def test_real_model_send_rechecks_after_handshake_credentials_and_caller_trace(tmp_path, wait_at, failure):
    import httpx
    from openai import AsyncOpenAI
    from agent_framework.openai import OpenAIChatClient

    async def scenario():
        server_ssl, client_ssl = tls_contexts(tmp_path)
        waiting, release = asyncio.Event(), asyncio.Event()
        captured, clients, handshakes, traces = [], [], [], []
        loop = asyncio.get_running_loop()

        class Receiver(asyncio.Protocol):
            def connection_made(self, transport):
                self.transport = transport
                self.buffer = bytearray()
                clients.append(transport)
                if wait_at == "tls":
                    transport.pause_reading()
                    waiting.set()
                    async def handshake():
                        await release.wait()
                        self.transport = await loop.start_tls(
                            transport, self, server_ssl, server_side=True, ssl_handshake_timeout=5)
                        clients.append(self.transport)
                    handshakes.append(asyncio.create_task(handshake()))

            def data_received(self, data):
                captured.append(data)
                self.buffer.extend(data)
                if b"\r\n\r\n" not in self.buffer:
                    return
                header, body = self.buffer.split(b"\r\n\r\n", 1)
                length = next((int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                               if line.lower().startswith(b"content-length:")), 0)
                if len(body) < length:
                    return
                response = json.dumps({"id": "resp_actual", "object": "response", "created_at": 0,
                                       "status": "completed", "model": "test", "output": []}).encode()
                self.transport.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                     + str(len(response)).encode() + b"\r\n\r\n" + response)
                self.buffer.clear()

        class CredentialWait(httpx.Auth):
            async def async_auth_flow(self, request):
                if wait_at == "credential":
                    waiting.set()
                    await release.wait()
                yield request

        async def hook(request):
            async def caller_trace(event, info):
                traces.append(event)
                if wait_at == "caller-trace" and event == "http11.send_request_headers.started":
                    waiting.set()
                    await release.wait()
            request.extensions["trace"] = caller_trace

        server = await loop.create_server(Receiver, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"{'https' if wait_at == 'tls' else 'http'}://127.0.0.1:{port}/v1"
        try:
            async with native_gate(tmp_path / "runtime", seconds=2 if failure == "expiry" else 60) as (p, gate, h):
                async with httpx.AsyncClient(verify=client_ssl, auth=CredentialWait(), trust_env=False,
                        timeout=10, event_hooks={"request": [hook]}) as wire:
                    model = OpenAIChatClient(model="test", async_client=AsyncOpenAI(
                        base_url=url, api_key="loopback-only", http_client=wire, max_retries=0))
                    agent = runtime().create_governed_agent(p, client=model, tools=[], id="agent-1")
                    pending = asyncio.create_task(agent.run("actual native transport"))
                    await asyncio.wait_for(waiting.wait(), 5)
                    if failure == "expiry":
                        await asyncio.sleep(max(0, (gate.signed.binding.expires_at - datetime.now(timezone.utc)).total_seconds()) + 0.05)
                    elif failure == "revocation":
                        h.keys.revoked = True
                    release.set()
                    outcome = await asyncio.gather(pending, return_exceptions=True)
                    assert bool(captured) == (failure == "valid"), (
                        f"UNAUTHORIZED_ACTUAL_REQUEST_BYTES_AFTER_{wait_at}: {sum(map(len, captured))}")
                    assert isinstance(outcome[0], BaseException) == (failure != "valid")
                    if failure == "valid" or wait_at != "credential":
                        assert traces
        finally:
            release.set()
            server.close()
            await server.wait_closed()
            for transport in clients:
                transport.close()
            await asyncio.gather(*handshakes, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("revoke", [False, True])
def test_terminal_authorization_rechecks_live_key_after_await(tmp_path, asynchronous, revoke):
    import httpx
    from agent_framework import FunctionTool
    from test_runtime_provider import model_client, tool_responses
    if asynchronous:
        assert callable(getattr(runtime(), "require_effect_authorization_async", None)), "async terminal authority missing"

    async def scenario():
        sends = []
        async def server(reader, writer):
            sends.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        listener = await asyncio.start_server(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            async with native_gate(tmp_path / "runtime", tool=True) as (p, gate, h):
                async with httpx.AsyncClient(trust_env=False) as http:
                    async def act():
                        if asynchronous:
                            check = await runtime().require_effect_authorization_async("act", {})
                        else:
                            check = runtime().require_effect_authorization("act", {})
                        await asyncio.sleep(0)
                        h.keys.revoked = revoke
                        if asynchronous:
                            await check()
                        else:
                            check()
                        await http.post(f"http://127.0.0.1:{port}/terminal", content=b"effect")
                        return "done"
                    agent = runtime().create_governed_agent(
                        p, client=model_client(tool_responses()), tools=[FunctionTool(name="act", func=act)])
                    await asyncio.gather(agent.run("call the terminal"), return_exceptions=True)
                    expected = 1 if asynchronous and not revoke else 0
                    assert len(sends) == expected, f"ACTUAL_TERMINAL_SENDS_WITH_UNCHECKED_KEY: {len(sends)}"
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", [False, True])
def test_real_cosmos_batch_reauthorizes_after_tls_wait(tmp_path, revoke, monkeypatch):
    import sys
    from pathlib import Path
    from contextlib import AsyncExitStack
    from azure.core.credentials import AccessToken
    example = Path(__file__).resolve().parents[3] / "examples/returns-triage-governed/src/agent"
    monkeypatch.syspath_prepend(str(example))
    monkeypatch.setitem(sys.modules, "runtime", runtime())
    import importlib.util
    spec = importlib.util.spec_from_file_location("bootstrap_cosmos_transport", example / "cosmos_effect.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    CosmosEffectTransport = implementation.CosmosEffectTransport
    from azure.core.pipeline.transport import AioHttpTransport

    async def scenario():
        server_ssl, _ = tls_contexts(tmp_path)
        waiting, release = asyncio.Event(), asyncio.Event()
        batches, clients, handshakes = [], [], []
        block_post = False
        loop = asyncio.get_running_loop()
        class Receiver(asyncio.Protocol):
            def connection_made(self, transport):
                self.transport, self.buffer = transport, bytearray()
                self.tls_ready = False
                clients.append(transport)
                transport.pause_reading()
                async def handshake():
                    if block_post:
                        waiting.set()
                        await release.wait()
                    self.transport = await loop.start_tls(
                        transport, self, server_ssl, server_side=True, ssl_handshake_timeout=5)
                    clients.append(self.transport)
                    self.tls_ready = True
                    self.process_data()
                handshakes.append(asyncio.create_task(handshake()))

            def data_received(self, data):
                self.buffer.extend(data)
                if self.tls_ready:
                    self.process_data()

            def process_data(self):
                if b"\r\n\r\n" not in self.buffer:
                    return
                header, body = self.buffer.split(b"\r\n\r\n", 1)
                length = next((int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                               if line.lower().startswith(b"content-length:")), 0)
                if len(body) < length:
                    return
                if header.startswith(b"POST "):
                    batches.append(bytes(self.buffer))
                    response = [{"statusCode": 200, "resourceBody": {"id": "case"}},
                                {"statusCode": 201, "resourceBody": {"id": "audit"}}]
                elif header.startswith(b"GET / HTTP/"):
                    response = {"id": "fixture", "_rid": "fixture", "readableLocations": [],
                                "writableLocations": [], "userConsistencyPolicy": {"defaultConsistencyLevel": "Session"}}
                else:
                    response = {"id": "cases", "_rid": "YQ==", "_self": "dbs/test/colls/cases/",
                                "partitionKey": {"paths": ["/case_id"], "kind": "Hash", "version": 2}}
                encoded = json.dumps(response).encode()
                self.transport.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"x-ms-activity-id: fixture\r\nConnection: close\r\nContent-Length: "
                    + str(len(encoded)).encode() + b"\r\n\r\n" + encoded)
                self.transport.close()
        listener = await loop.create_server(Receiver, "127.0.0.1", 0)
        endpoint = f"https://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with native_gate(tmp_path / "runtime", tool=True) as (p, gate, h), AsyncExitStack() as stack:
                class Credential:
                    async def get_token(self, *args, **kwargs):
                        return AccessToken("local-cosmos-token", 4102444800)
                def hook(request):
                    nonlocal block_post
                    if request.http_request.headers.get("x-ms-cosmos-is-batch-request") == "True":
                        block_post = True
                transport = CosmosEffectTransport(
                    AioHttpTransport(connection_verify=str(tmp_path / "localhost.pem")), endpoint=endpoint)
                stack.push_async_callback(transport.close)
                container = await transport.connect(stack=stack, credential=Credential(),
                    database="test", container="cases", raw_request_hook=hook)
                operations = [("replace", ("case", {"id": "case", "case_id": "case"}), {"if_match_etag": "etag"}),
                              ("create", ({"id": "audit", "case_id": "case"},), {})]
                async def dispatch():
                    with transport.batch(gate.authorize, container, "case", operations):
                        return await container.execute_item_batch(batch_operations=operations, partition_key="case")
                pending = asyncio.create_task(dispatch())
                await asyncio.wait_for(waiting.wait(), 5)
                h.keys.revoked = revoke
                release.set()
                outcome = await asyncio.gather(pending, return_exceptions=True)
                assert len(batches) == (0 if revoke else 1), f"ACTUAL_UNAUTHORIZED_COSMOS_BATCHES: {len(batches)}"
                assert isinstance(outcome[0], BaseException) == revoke
        finally:
            release.set()
            listener.close()
            await listener.wait_closed()
            for client in clients:
                client.close()
            await asyncio.gather(*handshakes, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", [False, True])
def test_registered_native_noop_async_guard_precedes_real_fixture_send(tmp_path, revoke):
    import httpx
    import uuid
    from azure.core.credentials import AccessToken
    from test_probe_telemetry import probe_registry, gateway
    async def scenario():
        server_ssl, client_ssl = tls_contexts(tmp_path)
        sends = []
        async def fixture(reader, writer):
            sends.append(await reader.readuntil(b"\r\n\r\n"))
            body = b'{"receipt_id":"noop-1","result":{"status":"noop"}}'
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        listener = await asyncio.start_server(fixture, "127.0.0.1", 443, ssl=server_ssl)
        try:
            async with native_gate(tmp_path / "runtime", tool=True) as (p, gate, h):
                action = probe_registry()["actions"][0]
                action.update(endpoint="https://127.0.0.1/governance/noop",
                              outcome_endpoint="https://127.0.0.1/governance/outcomes")
                action = gateway("dispatcher").Action.model_validate(action)
                class Credential:
                    async def get_token(self, *args, **kwargs):
                        await asyncio.sleep(0)
                        h.keys.revoked = revoke
                        return AccessToken("local-fixture-token", 4102444800)
                downstream = gateway("dispatcher").DownstreamClient(
                    credential=Credential(), transport=httpx.AsyncHTTPTransport(verify=client_ssl), timeout=5)
                try:
                    result = await asyncio.gather(downstream.request(
                        action=action, arguments={"probe_run_id": str(uuid.uuid4()), "variant": "allow"},
                        key="operation-1", action_hash=DIGEST, provenance="receipt-1",
                        facts={"tenant": TENANT, "subject": WORKLOAD, "client": APP,
                               "action": action.name, "policy": DIGEST, "deployment": {}},
                        guard=gate.authorize), return_exceptions=True)
                    assert len(sends) == (0 if revoke else 1), f"UNAUTHORIZED_ACTUAL_FIXTURE_SENDS: {len(sends)}"
                    assert isinstance(result[0], BaseException) == revoke
                finally:
                    await downstream.aclose()
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", [False, True])
def test_native_sync_tool_reauthorizes_after_executor_queue(tmp_path, revoke):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import socket
    from agent_framework import FunctionTool
    from test_runtime_provider import model_client, tool_responses
    async def scenario():
        loop = asyncio.get_running_loop()
        queued, received = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        sends = []
        class Executor(ThreadPoolExecutor):
            def submit(self, function, *args, **kwargs):
                target = getattr(function, "args", [None])[0]
                if "_guard_tool.<locals>.invoke.<locals>.call" in getattr(target, "__qualname__", ""):
                    super().submit(release.wait)
                    future = super().submit(function, *args, **kwargs)
                    loop.call_soon_threadsafe(queued.set)
                    return future
                return super().submit(function, *args, **kwargs)
        executor = Executor(max_workers=1)
        loop.set_default_executor(executor)
        async def server(reader, writer):
            sends.append(await reader.readuntil(b"\r\n\r\n"))
            received.set()
            writer.close()
            await writer.wait_closed()
        listener = await asyncio.start_server(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            async with native_gate(tmp_path / "runtime", tool=True) as (p, gate, h):
                def act():
                    with socket.create_connection(("127.0.0.1", port), timeout=3) as wire:
                        wire.sendall(b"POST /terminal HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
                    return "done"
                agent = runtime().create_governed_agent(
                    p, client=model_client(tool_responses()), tools=[FunctionTool(name="act", func=act)])
                pending = asyncio.create_task(agent.run("sync terminal"))
                await asyncio.wait_for(queued.wait(), 5)
                h.keys.revoked = revoke
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                if not revoke:
                    await asyncio.wait_for(received.wait(), 3)
                await asyncio.sleep(0.02)
                assert len(sends) == (0 if revoke else 1), f"UNAUTHORIZED_QUEUED_TOOL_SENDS: {len(sends)}"
        finally:
            release.set()
            listener.close()
            await listener.wait_closed()
    asyncio.run(scenario())
