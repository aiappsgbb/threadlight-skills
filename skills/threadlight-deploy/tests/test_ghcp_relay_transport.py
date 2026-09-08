"""Real McpRelay + signed bootstrap authority + loopback HTTP/TLS transmission."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
from test_remote_bootstrap import binding, harness
from test_control_plane import module
from test_bootstrap_transport import tls_contexts


def relay_module():
    path = ROOT / "skills/threadlight-deploy/references/governance/ghcp-container.py"
    spec = importlib.util.spec_from_file_location("ghcp_relay_wire_tests", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@asynccontextmanager
async def authority():
    from govern_control_plane.bootstrap import BootstrapBinding, BootstrapClient, BootstrapGate
    from govern_control_plane.models import canonical, parse
    h = await harness()
    expected = binding()
    signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(expected)))
    class Credential:
        async def get_token(self, scope):
            return SimpleNamespace(token=h.token())
    client = BootstrapClient(base_url="https://control.example", scope="api://governance/.default",
                             credential=Credential(), http=h.client)
    async def fetch():
        return await client.load("attempt-1")
    async def initialize(binding, stack):
        return lambda scope, receive, send: None
    gate = BootstrapGate(expected=expected, fetch=fetch, signer=h.service.signer, initialize=initialize)
    await gate.activate()
    try:
        yield gate, h
    finally:
        await gate.aclose()
        await client.aclose()
        await h.close()


async def make_relay(http, gate, url):
    class Credential:
        async def get_token(self, scope):
            assert scope == "api://gateway/.default"
            return SimpleNamespace(token="local-gateway-only", expires_on=4102444800)
    relay = relay_module().McpRelay(gateway_url=url, scope="api://gateway/.default",
        credential=Credential(), invocation_id="invocation-1", tools=["act"], http=http, bootstrap_gate=gate)
    ticket = await relay.pre_mcp({"serverName": "threadlight-governed", "toolName": "act",
        "toolCallId": "call-1", "sessionId": "session-1", "arguments": {"value": "YES"}}, {})
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "act", "arguments": {"value": "YES"}, "_meta": ticket["metaToUse"]}}
    return relay, body


@pytest.fixture
def native_instrumentation(monkeypatch):
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")

    @contextmanager
    def instrument(client, enabled=True, request_hook=None):
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        if enabled:
            HTTPXClientInstrumentor.instrument_client(
                client, tracer_provider=provider, request_hook=request_hook)
        try:
            yield exporter
        finally:
            if enabled:
                HTTPXClientInstrumentor.uninstrument_client(client)
            provider.shutdown()
    return instrument


@pytest.mark.parametrize("method", ["initialize", "notifications/initialized", "ping", "tools/list", "tools/call"])
@pytest.mark.parametrize("mutation", ["none", "authorization", "extra-header", "baggage"])
def test_native_instrumented_relay_handshake(
    method, mutation, native_instrumentation,
):
    from opentelemetry import baggage, context, trace
    async def scenario():
        requests, spans, tasks = [], [], set()
        async def receiver(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                try:
                    raw = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    return
                headers = dict(line.split(b": ", 1) for line in raw.split(b"\r\n")[1:] if b": " in line)
                body = await reader.readexactly(int(headers[b"Content-Length"]))
                requests.append((headers, json.loads(body)))
                output = b'{"jsonrpc":"2.0","id":1,"result":{}}'
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                             + str(len(output)).encode() + b"\r\n\r\n" + output)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        async def instrumentation_hook(span, request):
            spans.append(span.get_span_context())
            if mutation != "none":
                name = {"authorization": "Authorization", "extra-header": "X-Unapproved",
                        "baggage": "baggage"}[mutation]
                request.headers[name] = "unapproved"
        listener = await asyncio.start_server(receiver, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}/mcp"
        try:
            async with authority() as (gate, _):
                async with httpx.AsyncClient(trust_env=False, timeout=5) as wire, AsyncExitStack() as stack:
                    exporter = stack.enter_context(native_instrumentation(wire, request_hook=instrumentation_hook))
                    token = context.attach(
                        baggage.clear() if mutation == "baggage" else baggage.set_baggage("proof", "local"))
                    stack.callback(context.detach, token)
                    parent = trace.NonRecordingSpan(trace.SpanContext(
                        trace_id=1, span_id=2, is_remote=True, trace_flags=trace.TraceFlags(1),
                        trace_state=trace.TraceState([("proof", "state")])))
                    stack.enter_context(trace.use_span(parent))
                    relay, body = await make_relay(wire, gate, url)
                    if method != "tools/call":
                        body = {"jsonrpc": "2.0", "id": 1, "method": method}
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                                base_url="http://relay") as caller:
                        response = await caller.post("/mcp", json=body, headers={"X-Threadlight-Relay": relay.secret})
                    assert response.status_code == (200 if mutation == "none" else 503)
                    assert len(requests) == (1 if mutation == "none" else 0)
                    assert spans and exporter.get_finished_spans(), "native HTTPX instrumentation must remain active"
                    if mutation == "none":
                        headers, actual = requests[0]
                        span = spans[0]
                        assert headers[b"traceparent"] == f"00-{span.trace_id:032x}-{span.span_id:016x}-01".encode()
                        assert headers[b"tracestate"] == b"proof=state"
                        assert headers[b"baggage"] == b"proof=local"
                        assert headers[b"Authorization"] == b"Bearer local-gateway-only"
                        assert actual == body
        finally:
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("instrumented", [False, True])
@pytest.mark.parametrize("secure", [False, True])
@pytest.mark.parametrize("failure", ["valid", "revoke", "expire"])
def test_actual_relay_pool_wait_rechecks_signing_authority(
    tmp_path, secure, failure, instrumented, native_instrumentation,
):
    async def scenario():
        server_ssl, client_ssl = tls_contexts(tmp_path) if secure else (None, None)
        occupied, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls, tasks = [], set()
        async def receiver(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                while True:
                    try:
                        header = await reader.readuntil(b"\r\n\r\n")
                    except asyncio.IncompleteReadError:
                        return
                    length = next((int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                                   if line.lower().startswith(b"content-length:")), 0)
                    body = await reader.readexactly(length)
                    if header.startswith(b"GET /occupied "):
                        occupied.set()
                        await release.wait()
                    else:
                        calls.append(header + body)
                    response = b'{"jsonrpc":"2.0","id":1,"result":{}}'
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                 + str(len(response)).encode() + b"\r\n\r\n" + response)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        listener = await asyncio.start_server(receiver, "127.0.0.1", 0, ssl=server_ssl)
        origin = f"{'https' if secure else 'http'}://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        async def hook(request):
            if request.url.path == "/mcp":
                queued.set()
        try:
            async with authority() as (gate, h):
                async with httpx.AsyncClient(verify=client_ssl or True, trust_env=False, timeout=8,
                        limits=httpx.Limits(max_connections=1), event_hooks={"request": [hook]}) as wire, \
                        AsyncExitStack() as stack:
                    exporter = stack.enter_context(native_instrumentation(wire, instrumented))
                    blocking = asyncio.create_task(wire.get(origin + "/occupied"))
                    await asyncio.wait_for(occupied.wait(), 3)
                    relay, body = await make_relay(wire, gate, origin + "/mcp")
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                                base_url="http://relay") as caller:
                        pending = asyncio.create_task(caller.post("/mcp", json=body,
                            headers={"X-Threadlight-Relay": relay.secret}))
                        await asyncio.wait_for(queued.wait(), 3)
                        await asyncio.sleep(0.05)
                        if failure == "revoke":
                            h.keys.revoked = True
                        elif failure == "expire":
                            gate.signed.binding.__dict__["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
                        release.set()
                        await blocking
                        response = await pending
                        assert len(calls) == (1 if failure == "valid" else 0), (
                            f"ACTUAL_RELAY_CALLS_AFTER_{failure.upper()}: {len(calls)}")
                        assert response.status_code == (200 if failure == "valid" else 503)
                        if instrumented:
                            assert exporter.get_finished_spans()
                            if failure == "valid":
                                assert b"traceparent:" in calls[0].lower()
                        if failure == "revoke":
                            with pytest.raises(RuntimeError):
                                await gate.authorize()
        finally:
            release.set()
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("mutation", ["none", "stream", "request", "revoke", "authorization", "traceparent", "extra-header", "method", "url"])
@pytest.mark.parametrize("instrumented", [False, True])
def test_relay_validates_actual_core_bytes_after_retained_trace(
    tmp_path, phase, mutation, instrumented, native_instrumentation,
):
    import httpcore
    async def scenario():
        bodies, seen_headers, callbacks, tasks = [], [], [], set()
        async def receiver(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                try:
                    header = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    return
                seen_headers.append(header)
                length = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                try:
                    body = await reader.readexactly(length)
                except asyncio.IncompleteReadError as error:
                    bodies.append(error.partial)
                    return
                bodies.append(body)
                response = b'{"jsonrpc":"2.0","id":1,"result":{}}'
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                             + str(len(response)).encode() + b"\r\n\r\n" + response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        listener = await asyncio.start_server(receiver, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}/mcp"
        async def hook(request):
            async def retained(event, info):
                callbacks.append(event)
                if mutation != "none" and event == f"http11.send_request_{phase}.started":
                    core = info["request"]
                    body = request.content.replace(b"YES", b"BAD")
                    if mutation == "revoke":
                        h.keys.revoked = True
                    elif mutation == "stream":
                        core.stream = httpx.ByteStream(body)
                    elif mutation == "request":
                        info["request"] = httpcore.Request(method=core.method, url=core.url,
                            headers=core.headers, content=httpx.ByteStream(body), extensions=core.extensions)
                    elif mutation == "method":
                        core.method = b"PUT"
                    elif mutation == "url":
                        core.url = httpcore.URL(scheme=core.url.scheme, host=core.url.host,
                                                port=core.url.port, target=b"/unapproved")
                    else:
                        name = {"authorization": b"Authorization", "traceparent": b"traceparent",
                                "extra-header": b"X-Unapproved"}[mutation]
                        core.headers = [(key, value) for key, value in core.headers
                                        if key.lower() != name.lower()]
                        value = b"00-" + b"1" * 32 + b"-" + b"2" * 16 + b"-01" if mutation == "traceparent" else b"unapproved"
                        core.headers.append((name, value))
                    assert b"YES" in request.content
                await asyncio.sleep(0)
            request.extensions["trace"] = retained
        try:
            async with authority() as (gate, h):
                async with httpx.AsyncClient(trust_env=False, timeout=5, event_hooks={"request": [hook]}) as wire, \
                        AsyncExitStack() as stack:
                    stack.enter_context(native_instrumentation(wire, instrumented))
                    relay, body = await make_relay(wire, gate, url)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                                base_url="http://relay") as caller:
                        response = await caller.post("/mcp", json=body, headers={"X-Threadlight-Relay": relay.secret})
                    await asyncio.sleep(0.02)
                    assert not any(b"BAD" in body for body in bodies), "ACTUAL_RELAY_WIRE_BODY_CHANGED"
                    assert callbacks
                    assert response.status_code == (200 if mutation == "none" else 503)
                    if mutation != "none" and phase == "headers":
                        assert seen_headers == []
        finally:
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("instrumented", [False, True])
def test_actual_relay_rejects_http2_before_mcp_request_frames(
    tmp_path, warm, instrumented, native_instrumentation,
):
    import h2.config
    import h2.connection
    import h2.events
    async def scenario():
        server_ssl, client_ssl = tls_contexts(tmp_path)
        server_ssl.set_alpn_protocols(["h2"])
        mcp_headers, data_frames, tasks = [], [], set()
        async def receiver(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            connection = h2.connection.H2Connection(config=h2.config.H2Configuration(
                client_side=False, header_encoding="utf-8"))
            connection.initiate_connection()
            writer.write(connection.data_to_send())
            await writer.drain()
            methods = {}
            try:
                while raw := await reader.read(65536):
                    for event in connection.receive_data(raw):
                        if isinstance(event, h2.events.RequestReceived):
                            methods[event.stream_id] = dict(event.headers).get(":method")
                            if methods[event.stream_id] == "POST":
                                mcp_headers.append(event.stream_id)
                        elif isinstance(event, h2.events.DataReceived) and methods[event.stream_id] == "POST":
                            data_frames.append(event.data)
                        elif isinstance(event, h2.events.StreamEnded):
                            payload = b'{"jsonrpc":"2.0","id":1,"result":{}}'
                            connection.send_headers(event.stream_id, [
                                (":status", "200"), ("content-type", "application/json"),
                                ("content-length", str(len(payload)))])
                            connection.send_data(event.stream_id, payload, end_stream=True)
                    writer.write(connection.data_to_send())
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        listener = await asyncio.start_server(receiver, "127.0.0.1", 0, ssl=server_ssl)
        origin = f"https://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with authority() as (gate, h):
                async with httpx.AsyncClient(http2=True, verify=client_ssl, trust_env=False, timeout=5) as wire, \
                        AsyncExitStack() as stack:
                    stack.enter_context(native_instrumentation(wire, instrumented))
                    if warm:
                        response = await wire.get(origin + "/warm")
                        assert response.http_version == "HTTP/2"
                    relay, body = await make_relay(wire, gate, origin + "/mcp")
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                                base_url="http://relay") as caller:
                        response = await caller.post("/mcp", json=body, headers={"X-Threadlight-Relay": relay.secret})
                    assert mcp_headers == [], f"UNSUPPORTED_RELAY_H2_HEADERS: {len(mcp_headers)}"
                    assert data_frames == []
                    assert response.status_code == 503
        finally:
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize("instrumented", [False, True])
def test_actual_relay_reauthorizes_after_tls_handshake_wait(
    tmp_path, revoke, instrumented, native_instrumentation,
):
    async def scenario():
        server_ssl, client_ssl = tls_contexts(tmp_path)
        waiting, release = asyncio.Event(), asyncio.Event()
        received, handshakes, transports = [], [], []
        loop = asyncio.get_running_loop()
        class Receiver(asyncio.Protocol):
            def connection_made(self, transport):
                self.transport, self.buffer, self.ready = transport, bytearray(), False
                transports.append(transport)
                transport.pause_reading()
                waiting.set()
                async def handshake():
                    await release.wait()
                    self.transport = await loop.start_tls(
                        transport, self, server_ssl, server_side=True, ssl_handshake_timeout=5)
                    transports.append(self.transport)
                    self.ready = True
                    self.process()
                handshakes.append(asyncio.create_task(handshake()))
            def data_received(self, data):
                received.append(data)
                self.buffer.extend(data)
                if self.ready:
                    self.process()
            def process(self):
                if b"\r\n\r\n" not in self.buffer:
                    return
                header, body = self.buffer.split(b"\r\n\r\n", 1)
                length = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                if len(body) < length:
                    return
                response = b'{"jsonrpc":"2.0","id":1,"result":{}}'
                self.transport.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                                     + str(len(response)).encode() + b"\r\n\r\n" + response)
                self.transport.close()
        listener = await loop.create_server(Receiver, "127.0.0.1", 0)
        url = f"https://127.0.0.1:{listener.sockets[0].getsockname()[1]}/mcp"
        try:
            async with authority() as (gate, h):
                async with httpx.AsyncClient(verify=client_ssl, trust_env=False, timeout=5) as wire, \
                        AsyncExitStack() as stack:
                    stack.enter_context(native_instrumentation(wire, instrumented))
                    relay, body = await make_relay(wire, gate, url)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                                base_url="http://relay") as caller:
                        pending = asyncio.create_task(caller.post("/mcp", json=body,
                            headers={"X-Threadlight-Relay": relay.secret}))
                        await asyncio.wait_for(waiting.wait(), 3)
                        h.keys.revoked = revoke
                        release.set()
                        response = await pending
                    assert bool(received) == (not revoke), f"ACTUAL_RELAY_BYTES_AFTER_TLS_REVOCATION: {sum(map(len, received))}"
                    assert response.status_code == (503 if revoke else 200)
        finally:
            release.set()
            listener.close()
            await listener.wait_closed()
            for transport in transports:
                transport.close()
            await asyncio.gather(*handshakes, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("instrumented", [False, True])
def test_native_relay_rejects_proxy_connect_before_wire(instrumented, native_instrumentation):
    async def scenario():
        received, tasks = [], set()
        async def proxy(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                data = await reader.read(65536)
                if data:
                    received.append(data)
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        listener = await asyncio.start_server(proxy, "127.0.0.1", 0)
        proxy_url = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with authority() as (gate, _):
                async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=3) as wire, \
                        AsyncExitStack() as stack:
                    stack.enter_context(native_instrumentation(wire, instrumented))
                    relay, body = await make_relay(wire, gate, "https://gateway.invalid/mcp")
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                                base_url="http://relay") as caller:
                        response = await caller.post("/mcp", json=body, headers={"X-Threadlight-Relay": relay.secret})
                    assert response.status_code == 503
                    assert received == []
        finally:
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())
