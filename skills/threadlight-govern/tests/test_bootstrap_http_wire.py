"""Portable real-wire execution of the production HTTP adapter function.

Native policy/middleware prerequisites are supplied explicitly here; this is not
MAF/ACS conformance. test_bootstrap_transport.py runs the corresponding native
agent paths when the published Linux environment is available.
"""
import ast
import asyncio
from contextvars import ContextVar
from copy import copy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("openai", reason="portable SDK wire checks require the existing pinned OpenAI dependency")
pytest.importorskip("h2", reason="portable HTTP/2 wire checks require the hosting stack's h2 dependency")

from test_remote_bootstrap import binding, harness
from test_control_plane import module


def apply_production_adapter(gate, sdk):
    """Execute the exact production function, not a reimplementation of its trace."""
    source = Path(__file__).parents[1] / "references/runtime/maf_agent_hooks_acs.py"
    node = next(node for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "_guard_http_client")
    context = ContextVar("portable_native_prerequisites",
                         default={"model_scope": {"wire": None, "target": "explicit-local-prerequisite"}})
    def digest(value):
        return "sha256:" + hashlib.sha256(module("models").canonical(value)).hexdigest()
    def deny(provider, selected, reason, **kwargs):
        raise RuntimeError(reason)
    async def authorize(provider, selected, **kwargs):
        await gate.authorize()
    namespace = {
        "copy": copy, "inspect": inspect, "json": json, "digest": digest, "wraps": __import__("functools").wraps,
        "_check_model_transport": lambda *_: None, "_check_model_config": lambda *_: None,
        "_model_bindings": lambda _: [{"point": "pre_model_call"}], "_execution": context,
        "_check_lifecycle": lambda *_: gate.check(), "_deny_boundary": deny,
        "_reauthorize_bootstrap": authorize,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    holder = SimpleNamespace(client=sdk)
    namespace["_guard_http_client"](holder, SimpleNamespace(mode="enforce"))
    return holder.client


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("replacement", ["stream", "request", "unchanged"])
def test_portable_real_h1_core_request_trace(phase, replacement):
    import httpx
    import httpcore
    from openai import AsyncOpenAI
    from govern_control_plane.bootstrap import BootstrapBinding, BootstrapGate
    from govern_control_plane.models import canonical, parse

    async def scenario():
        h = await harness()
        signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(binding())))
        async def fetch():
            return signed
        async def initialize(binding, stack):
            return lambda scope, receive, send: None
        gate = BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer, initialize=initialize)
        await gate.activate()
        bodies, headers, callbacks, tasks = [], [], [], set()
        async def receiver(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                try:
                    header = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    return
                headers.append(header)
                length = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                try:
                    body = await reader.readexactly(length)
                except asyncio.IncompleteReadError as error:
                    bodies.append(error.partial)
                    return
                bodies.append(body)
                response = b'{"id":"resp_portable","object":"response","status":"completed","created_at":0,"model":"test","output":[]}'
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                             + str(len(response)).encode() + b"\r\n\r\n" + response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        listener = await asyncio.start_server(receiver, "127.0.0.1", 0)
        endpoint = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}/v1"
        async def hook(request):
            async def trace(event, info):
                callbacks.append(event)
                if event == f"http11.send_request_{phase}.started" and replacement != "unchanged":
                    core = info["request"]
                    changed = request.content.replace(b"YES", b"BAD")
                    if replacement == "stream":
                        core.stream = httpx.ByteStream(changed)
                    else:
                        info["request"] = httpcore.Request(method=core.method, url=core.url,
                            headers=core.headers, content=httpx.ByteStream(changed), extensions=core.extensions)
                    assert b"YES" in request.content
                await asyncio.sleep(0)
            request.extensions["trace"] = trace
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=3, event_hooks={"request": [hook]}) as wire:
                original = AsyncOpenAI(base_url=endpoint, api_key="local-only", http_client=wire, max_retries=0)
                sdk = apply_production_adapter(gate, original)
                result = await asyncio.gather(sdk.responses.create(input="YES", model="test", store=False),
                                              return_exceptions=True)
                await asyncio.sleep(0.02)
                assert not any(b"BAD" in body for body in bodies), "REAL_CORE_STREAM_SENT_BAD_BODY"
                assert callbacks, repr(result[0]) + " cause=" + repr(getattr(result[0], "__cause__", None))
                if replacement == "unchanged":
                    assert not isinstance(result[0], BaseException)
                    assert len(bodies) == 1 and b"YES" in bodies[0]
                else:
                    assert isinstance(result[0], BaseException)
                    cause = result[0]
                    messages = []
                    while cause is not None:
                        messages.append(str(cause))
                        cause = cause.__cause__
                    assert any("threadlight:model_target_changed" in text for text in messages)
                    if phase == "headers":
                        assert headers == []
        finally:
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()
            await h.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("warm_connection", [False, True])
def test_portable_real_h2_zero_window_is_unsupported(tmp_path, warm_connection):
    import httpx
    import h2.config
    import h2.connection
    import h2.events
    import h2.settings
    from openai import AsyncOpenAI
    from govern_control_plane.bootstrap import BootstrapBinding, BootstrapGate
    from govern_control_plane.models import canonical, parse
    from test_bootstrap_transport import tls_contexts

    async def scenario():
        h = await harness()
        signed = await h.service.publish_bootstrap(parse(BootstrapBinding, canonical(binding())))
        async def fetch():
            return signed
        async def initialize(binding, stack):
            return lambda scope, receive, send: None
        gate = BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer, initialize=initialize)
        await gate.activate()
        server_ssl, client_ssl = tls_contexts(tmp_path)
        server_ssl.set_alpn_protocols(["h2"])
        headers, data_frames, peers, tasks = [], [], [], set()
        body_started = asyncio.Event()
        async def receiver(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            connection = h2.connection.H2Connection(config=h2.config.H2Configuration(
                client_side=False, header_encoding="utf-8"))
            peers.append((connection, writer))
            connection.initiate_connection()
            connection.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 0})
            writer.write(connection.data_to_send())
            await writer.drain()
            methods = {}
            try:
                while raw := await reader.read(65536):
                    for event in connection.receive_data(raw):
                        if isinstance(event, h2.events.RequestReceived):
                            methods[event.stream_id] = dict(event.headers).get(":method")
                            if methods[event.stream_id] == "POST":
                                headers.append(event.stream_id)
                        elif isinstance(event, h2.events.DataReceived):
                            if methods[event.stream_id] == "POST":
                                data_frames.append(event.data)
                        elif isinstance(event, h2.events.StreamEnded):
                            payload = (b"{}" if methods[event.stream_id] == "GET" else
                                b'{"id":"resp_portable_h2","object":"response","status":"completed","created_at":0,"model":"test","output":[]}')
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
        endpoint = f"https://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        async def hook(request):
            async def trace(event, info):
                if event == "http2.send_request_body.started":
                    body_started.set()
            request.extensions["trace"] = trace
        pending = waiting = None
        try:
            async with httpx.AsyncClient(http2=True, verify=client_ssl, trust_env=False,
                    timeout=4, event_hooks={"request": [hook]}) as wire:
                if warm_connection:
                    response = await wire.get(endpoint + "/warm")
                    assert response.http_version == "HTTP/2"
                    body_started.clear()
                sdk = apply_production_adapter(gate, AsyncOpenAI(
                    base_url=endpoint + "/v1", api_key="local-only", http_client=wire, max_retries=0))
                pending = asyncio.create_task(sdk.responses.create(
                    input="PROMPT_MUST_NOT_BE_SENT_ON_H2", model="test", store=False))
                waiting = asyncio.create_task(body_started.wait())
                await asyncio.wait({pending, waiting}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
                if not pending.done():
                    await asyncio.sleep(0.1)
                    if warm_connection:
                        assert data_frames == [], "zero-window fixture failed to hold model DATA"
                    h.keys.revoked = True
                    for connection, writer in peers:
                        for stream_id in headers:
                            if not connection.streams[stream_id].closed:
                                connection.increment_flow_control_window(65535, stream_id=stream_id)
                        writer.write(connection.data_to_send())
                        await writer.drain()
                outcome = await asyncio.gather(pending, return_exceptions=True)
                assert headers == [], f"REAL_H2_MODEL_HEADERS_SENT: {len(headers)}; BODY_BYTES: {sum(map(len, data_frames))}"
                assert data_frames == []
                assert isinstance(outcome[0], BaseException)
                errors, cause = [], outcome[0]
                while cause is not None:
                    errors.append(str(cause))
                    cause = cause.__cause__
                assert any("threadlight:unsupported_model_transport" in error for error in errors)
        finally:
            for task in (pending, waiting):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (pending, waiting) if task is not None), return_exceptions=True)
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()
            await h.close()
    asyncio.run(scenario())


def test_http2_restriction_and_actual_core_validation_are_documented():
    readme = (Path(__file__).parents[1] / "references/runtime/README.md").read_text()
    assert "HTTP/2 is explicitly unsupported" in readme
    assert "flow-control" in readme
    assert "actual httpcore request" in readme
