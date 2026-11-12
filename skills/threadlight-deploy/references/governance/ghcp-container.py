"""Copilot SDK / Invocations adapter. Only selected MCP effects use the gateway."""
import asyncio
from copy import copy, deepcopy
from contextvars import ContextVar
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time
from urllib.parse import urlsplit
import uuid

CLEANUP_TIMEOUT = 5.0
# Session-wide tool allowlist: the relayed MCP tools (the governed binding goes
# through the relay) and the skill tool. No Copilot CLI built-ins (bash, edit,
# web_fetch, task, ...), so the model cannot reach a backend outside the
# relay/gateway path. https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/custom-agents
GOVERNED_AVAILABLE_TOOLS = ("mcp:*", "builtin:skill")
LOGGER = logging.getLogger(__name__)
_LOG_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class SdkLogFilter(logging.Filter):
    def __init__(self, reason):
        super().__init__()
        self.reason = reason

    def filter(self, record):
        record.msg, record.args = self.reason, ()
        record.exc_info = record.exc_text = record.stack_info = None
        for key in record.__dict__.keys() - _LOG_RECORD_FIELDS:
            record.__dict__[key] = None
        return True


def install_sdk_log_privacy():
    """Install once for process lifetime, including readers outliving SDK stop."""
    # Use logging's lock so concurrent imports/installers cannot stack policies.
    with logging._lock:
        original = logging.Manager.getLogger
        if getattr(original, "_threadlight_sdk_privacy", False):
            return
        filters = (
            ("copilot.client", SdkLogFilter("governance_cleanup_sdk_diagnostic")),
            ("copilot._jsonrpc", SdkLogFilter("governance_cleanup_sdk_transport_diagnostic")),
        )

        def protect(logger):
            for name, redaction in filters:
                if logger.name == name or logger.name.startswith(name + "."):
                    logger.addFilter(redaction)
                    break
            return logger

        def get_logger(manager, name):
            # Ancestor filters do not see propagated records. Protect future
            # descendants (including getChild) before returning them to callers.
            with logging._lock:
                return protect(original(manager, name))

        get_logger._threadlight_sdk_privacy = True
        for logger in logging.Logger.manager.loggerDict.values():
            if isinstance(logger, logging.Logger):
                protect(logger)
        logging.Manager.getLogger = get_logger


install_sdk_log_privacy()


async def close_invocation(*, unsubscribe, session, client, server, task, sock, http, credential):
    """Isolate cleanup failures and join shielded work before propagating cancellation."""
    failures: list[tuple[str, type[BaseException]]] = []
    cancelled = False

    async def close(code, callback):
        nonlocal cancelled
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT):
                await callback()
            return True
        except asyncio.CancelledError as error:
            cancelled = True
            failures.append((code, type(error)))
        except Exception as error:
            failures.append((code, type(error)))
        LOGGER.warning("governance_cleanup_%s", code)
        return False

    async def cleanup():
        nonlocal cancelled
        if unsubscribe:
            try:
                unsubscribe()
            except asyncio.CancelledError as error:
                cancelled = True
                failures.append(("unsubscribe", type(error)))
                LOGGER.warning("governance_cleanup_unsubscribe")
            except Exception as error:
                failures.append(("unsubscribe", type(error)))
                LOGGER.warning("governance_cleanup_unsubscribe")
        if session:
            await close("disconnect", session.disconnect)
        if client:
            process = getattr(client, "_process", None)
            if not await close("client_stop", client.stop):
                await close("client_force_stop", client.force_stop)
            # Pinned force_stop kills but does not reap its owned subprocess.
            if isinstance(process, subprocess.Popen) and not client._is_external_server:
                async def reap():
                    if process.poll() is None:
                        process.kill()
                    await asyncio.to_thread(process.wait, timeout=CLEANUP_TIMEOUT)
                await close("client_reap", reap)
        if server:
            server.should_exit = True
        if task:
            async def join_relay():
                # timeout cancels and joins the relay, not an orphaned shield.
                await task
            await close("relay_stop", join_relay)
        if sock:
            try:
                sock.close()
            except Exception as error:
                failures.append(("socket_close", type(error)))
                LOGGER.warning("governance_cleanup_socket_close")
        if http:
            await close("http_close", http.aclose)
        if credential:
            await close("credential_close", credential.close)

    worker = asyncio.create_task(cleanup())
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
    worker.result()
    if cancelled:
        raise asyncio.CancelledError()
    return failures


def route_mcp_servers(servers, contract, bindings, gateway_url):
    result = deepcopy(servers)
    selected = [t for t in contract["tools"] if t["policy_binding"] not in (None, "none")]
    if not selected:
        return result
    return _route_selected(result, servers, selected, bindings, gateway_url)


def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _message_payload(event):
    """Serialise an assistant message event; a failing SDK ``to_dict()`` keeps the text.

    github-copilot-sdk 1.0.x ``to_dict()`` can raise AssertionError on usage
    fields (``timeToFirstTokenMs``); in the 2026-11-07 live deploy that ended
    the ungoverned stream. Here it would end the invocation as agent_unavailable.
    """
    try:
        return event.to_dict()
    except Exception as exc:  # noqa: BLE001
        event_type = getattr(getattr(event, "type", None), "value", str(getattr(event, "type", "")))
        LOGGER.warning("Reducing unserialisable %s event to its text: %s", event_type, type(exc).__name__)
        data = getattr(event, "data", None)
        content = getattr(data, "content", None) or getattr(data, "delta_content", None) or ""
        return {"type": event_type, "data": {"content": content}}


OPERATION_ARGUMENT = "governance_operation_id"
OPERATION_SCHEMA = {
    "type": "string", "minLength": 1, "maxLength": 128,
    "description": "Resume an existing pending operation; this reference is not approval.",
}


def _pending_event(body):
    """Host-owned SSE frame for a gateway pending_approval result (no effect has occurred)."""
    return b"event: governance_pending\ndata: " + canonical(body) + b"\n\n"


# ---------------------------------------------------------------------------
# Optional voice front-end: Voice Live Bridge Protocol 1.0 on /invocations_ws
# ---------------------------------------------------------------------------
# https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent
# With THREADLIGHT_VOICE_BRIDGE=1 a managed `kind: voice` wrapper uses this governed
# host as its conversation engine. Voice Live owns audio; each finalised caller
# utterance runs the SAME governed invocation as POST /invocations (relay,
# bootstrap gate, gateway, central ACK before any effect).
# * A `governance_pending` record is sent to the caller as unspoken Bridge application
#   data (`response.output_data.done`, delivered as `response.invocation.delta`), so the
#   Approver gets the exact record that threadlight-review-action needs.
# * The host keeps the call's latest `governance_pending` record and gives its exact
#   proposed arguments and operation id back on the next turn; the gateway still
#   re-checks policy, facts, identity, expiry and the one-use human grant.
# * Barge-in, cancel and timeout silence the reply but never cancel a governed turn
#   in flight (a cancelled write would be an unknown outcome); the unheard final
#   answer is carried into the next turn as a host note.
# * Hold messages are spoken before Voice Live's announced deadlines while a turn
#   (model, tools, gateway) runs; markdown markup is removed before speech.
VOICE_BRIDGE = os.environ.get("THREADLIGHT_VOICE_BRIDGE", "").strip().lower() in ("1", "true")
_VOICE_MAX_INPUT_CHARS = 16_000
_VOICE_MAX_PENDING_TURNS = 4
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|`)")
_MD_LINE_PREFIX = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|>[ \t]?)", re.M)


def _speakable(text):
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_EMPHASIS.sub("", text)
    text = _MD_LINE_PREFIX.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _voice_hold_schedule(*, first_output_ms, idle_ms, hold_seconds):
    first = min(hold_seconds, first_output_ms / 2000)
    idle = idle_ms / 1000
    return first, min(20.0, idle - 10 if idle >= 20 else idle / 2)


def _sse_frame(chunk):
    """(event, body) of one frame yielded by the governed stream."""
    event, data = "message", []
    for line in chunk.decode("utf-8").splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return event, (json.loads("\n".join(data)) if data else {})


_VOICE_NOTE = ("Host note: this is a spoken call. Answer in the same language as the caller, every word "
               "including labels and decision names (say \"approvare il rimborso\", not \"approve_refund\", to an "
               "Italian caller). Do not read long identifiers aloud (operation_id, "
               "expected_etag, audit_id); say that they are recorded. Keep answers to one or two short sentences. "
               "The caller's words come from speech recognition: identifiers may have lost their separators or "
               "spacing (ABC-2026-0042 heard as ABC20260042); restore the format your tools expect before calling "
               "them, and if a lookup still finds nothing, ask the caller to spell the identifier.")

# Hold message in the caller's language when THREADLIGHT_VOICE_HOLD_TEXT is not set
# (review 2026-11-12: Italian calls had an English hold message).
_VOICE_HOLD_TEXTS = {
    "en": "One moment, I'm checking that.", "it": "Un momento, sto verificando.",
    "es": "Un momento, lo estoy comprobando.", "fr": "Un instant, je vérifie.",
    "de": "Einen Moment, ich prüfe das.", "pt": "Um momento, estou a verificar.",
    "el": "Μια στιγμή, το ελέγχω.",
}
_VOICE_LANGUAGE_WORDS = {
    "en": {"the", "please", "and", "to", "of", "is", "it", "my", "should", "we", "return", "hi", "can", "you", "this", "what"},
    "it": {"il", "lo", "la", "di", "che", "per", "un", "una", "reso", "può", "puoi", "valuti", "dobbiamo", "non", "è",
           "mi", "buongiorno", "grazie", "sono", "favore", "del", "della", "questo", "vorrei", "ciao"},
    "es": {"el", "los", "por", "favor", "que", "devolución", "puede", "hola", "quiero", "esta", "gracias"},
    "fr": {"le", "les", "pour", "est", "bonjour", "retour", "vous", "je", "merci", "une", "des"},
    "de": {"der", "die", "das", "und", "bitte", "ist", "ich", "rückgabe", "guten", "können", "eine"},
    "pt": {"o", "os", "por", "favor", "devolução", "obrigado", "olá", "uma", "não", "você"},
}


def _voice_language(text: str) -> str:
    """Best-effort language of a recognised utterance; English when unsure."""
    if any("\u0370" <= ch <= "\u03ff" for ch in text):
        return "el"
    words = re.findall(r"[^\W\d_]+", text.lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in _VOICE_LANGUAGE_WORDS.items()}
    best = max(scores, key=lambda lang: (scores[lang], lang == "en"))
    return best if scores[best] > scores["en"] else "en"


def _voice_hold_text(utterance: str) -> str:
    configured = os.environ.get("THREADLIGHT_VOICE_HOLD_TEXT", "").strip()
    return configured or _VOICE_HOLD_TEXTS[_voice_language(utterance)]


def _voice_prompt(call, utterance):
    notes = [_VOICE_NOTE]
    if call["unheard"]:
        notes.append("Host note: the caller interrupted your previous answer before hearing it. "
                     "Its text was: " + json.dumps(call["unheard"]))
    pending = call["pending"]
    if pending:
        arguments = dict(pending["proposed_arguments"], governance_operation_id=pending["operation_id"])
        notes.append(
            "Host record (from the gateway, not from the caller; not an approval): operation "
            + pending["operation_id"] + " returned pending_approval earlier in this call; its current state is "
            "not known to the host (it may since have been approved, rejected, completed or expired).\n"
            "Exact arguments: " + json.dumps(arguments, sort_keys=True) + "\n"
            "Only if the caller asks to complete, continue or check this decision: do NOT call returns_get_case and "
            "do not change any value; call returns_apply_decision exactly once with exactly these arguments and "
            "report the tool result (calling it again never repeats an effect). Otherwise answer the caller "
            "normally and do not describe this operation's state.")
    return "\n\n".join(notes + ["Caller: " + utterance])


def attach_voice_bridge(host, run_turn):
    """Serve Voice Live Bridge 1.0 on ``host`` (a VoiceAgentServerHost); turns run ``run_turn``."""
    from azure.ai.agentserver.invocations.voice import (
        AgentError, ResponseCreated, ResponseDone, ResponseNone, ResponseOutputDataDone, ResponseOutputTextDone,
        SessionReady, SessionRejected, new_item_id, new_response_id)

    hold_seconds = float(os.environ.get("THREADLIGHT_VOICE_HOLD_SECONDS", "") or "4")
    calls, background = {}, set()

    async def send(call, response_id, message):
        if response_id in call["muted"] or call["closed"]:
            return
        try:
            await call["session"].send(message)
        except Exception:  # noqa: BLE001 - the caller hung up; the governed turn still completes
            call["closed"] = True

    async def deliver_pending(call, response_id):
        """The exact host record for the Approver's threadlight-review-action, as unspoken application data
        (the voice analogue of the `event: governance_pending` SSE frame). A barged-in response cannot carry
        it, so it is delivered on the caller's next response instead."""
        if call["pending"] and not call["pending_delivered"] and response_id not in call["muted"]:
            await send(call, response_id, ResponseOutputDataDone(
                response_id=response_id, item_id=new_item_id(), data=call["pending"]))
            call["pending_delivered"] = not call["closed"]

    async def governed_turn(call, utterance, response_id):
        final, failed = "", False
        async with call["lock"]:
            await deliver_pending(call, response_id)  # a record the previous (interrupted) turn could not send
            async for chunk in run_turn("voice-" + uuid.uuid4().hex, _voice_prompt(call, utterance)):
                event, body = _sse_frame(chunk)
                if event == "governance_pending":
                    call["pending"], call["pending_delivered"] = body, False
                    await deliver_pending(call, response_id)
                elif body.get("type") == "error":
                    failed = True
                elif body.get("type") == "assistant.message":
                    text = (body.get("data") or {}).get("content") or ""
                    if text.strip():
                        final = text
            call["unheard"] = final if response_id in call["muted"] else ""
        return final, failed

    async def voice_turn(call, item_id, utterance):
        response_id = new_response_id()
        call["current"] = response_id
        try:
            await send(call, response_id, ResponseCreated(response_id=response_id, in_reply_to=(item_id,)))
            await deliver_pending(call, response_id)
            work = asyncio.ensure_future(governed_turn(call, utterance, response_id))
            wait = call["hold"][0]
            while True:
                done, _ = await asyncio.wait({work}, timeout=wait)
                if done:
                    break
                await send(call, response_id, ResponseOutputTextDone(
                    response_id=response_id, item_id=new_item_id(), text=_voice_hold_text(utterance)))
                wait = call["hold"][1]
            try:
                final, failed = work.result()
            except Exception:  # noqa: BLE001
                LOGGER.warning("governed voice turn failed")
                final, failed = "", True
            if failed or not final.strip():
                await send(call, response_id, AgentError(
                    code="generation_failed", message="The agent could not answer this turn.",
                    response_id=response_id))
                return
            await send(call, response_id, ResponseOutputTextDone(
                response_id=response_id, item_id=new_item_id(), text=_speakable(final)))
            await send(call, response_id, ResponseDone(response_id=response_id))
        finally:
            call["tasks"].discard(asyncio.current_task())

    def silence(session, response_id):
        call = calls.get(id(session))
        if call is not None and response_id:
            call["muted"].add(response_id)

    @host.on_session_start
    async def _start(session, event):
        if id(session) in calls:
            return
        if event.protocol_version != "1.0":
            await session.send(SessionRejected(code="protocol_mismatch", retriable=False))
            return
        timeouts = getattr(event, "response_timeouts", None)
        hold = (_voice_hold_schedule(first_output_ms=timeouts.first_output_ms, idle_ms=timeouts.idle_ms,
                                     hold_seconds=hold_seconds) if timeouts is not None else (hold_seconds, 20.0))
        calls[id(session)] = {"session": session, "lock": asyncio.Lock(), "tasks": set(), "muted": set(),
                              "pending": None, "pending_delivered": True, "unheard": "", "current": None, "closed": False, "hold": hold}
        await session.send(SessionReady())

    @host.on_user_message
    async def _message(session, event):
        call = calls.get(id(session))
        if call is None:
            return
        text = "".join(getattr(part, "text", "") or "" for part in event.content).strip()
        reason = ("empty_input" if not text else "input_too_large" if len(text) > _VOICE_MAX_INPUT_CHARS
                  else "capacity_exceeded" if len(call["tasks"]) >= _VOICE_MAX_PENDING_TURNS else None)
        if reason:
            await session.send(ResponseNone(in_reply_to=(event.item_id,), reason=reason))
            return
        task = asyncio.get_running_loop().create_task(voice_turn(call, event.item_id, text))
        call["tasks"].add(task)
        background.add(task)
        task.add_done_callback(background.discard)

    barge_in = getattr(host, "on_user_barge_in", None) or getattr(host, "on_barge_in")

    @barge_in
    async def _barge_in(session, event):
        silence(session, event.response_id)

    @host.on_response_cancelled
    async def _cancelled(session, event):
        silence(session, event.response_id)

    @host.on_response_timeout
    async def _timeout(session, event):
        call = calls.get(id(session))
        silence(session, event.response_id or (call or {}).get("current"))

    def _finish(session):
        call = calls.pop(id(session), None)
        if call is not None:
            call["closed"] = True  # governed turns in flight run to completion; nothing more is sent

    @host.on_session_end
    async def _end(session, event):
        _finish(session)

    @host.on_connection_terminating
    def _terminating(session):
        _finish(session)


class McpRelay:
        """The pinned SDK hook changes _meta, NOT headers. Bridge only on loopback.

        The relay refreshes the gateway-audience bearer for bootstrap and every call.
        Host-issued tickets bind SDK call identity to exact arguments; no model token,
        caller URL, caller Authorization header or model-provided nonce is forwarded.
        H1 header/body transmission reauthorizes the signed bootstrap after waits
        and retained callbacks, then validates the actual core wire request. H2 is
        explicitly unsupported; its body-start event precedes flow-control waits.
        Native OTel's bounded W3C propagation must exactly match its active span
        context; the complete resulting header set is then frozen for transmission.
        """
        def __init__(self, *, gateway_url, scope, credential, invocation_id, tools, http,
                     bootstrap_gate=None, on_pending=None):
            self.url, self.scope, self.credential = gateway_url, scope, credential
            self.http = copy(http)
            self._outbound = ContextVar("threadlight_relay_wire", default=None)
            # The caller still owns the shared pools. Copy hook lists so their
            # legitimate callbacks run before our final trace is installed.
            self.http.event_hooks = {
                "request": [*http.event_hooks.get("request", []), self._bind_request],
                "response": list(http.event_hooks.get("response", [])),
            }
            self.bootstrap_gate = bootstrap_gate
            self.invocation_id, self.tools = invocation_id, frozenset(tools)
            self.secret = secrets.token_urlsafe(32)
            self.tickets = {}
            # Deferred tools learned from the authenticated gateway inventory.
            self.on_pending, self.resumable = on_pending, set()

        async def _authorize(self):
            if self.bootstrap_gate is not None:
                async with asyncio.timeout(10):
                    await self.bootstrap_gate.authorize()
                self.bootstrap_gate.check()

        async def _bind_request(self, request):
            import httpcore
            import httpx
            expected = self._outbound.get()
            if (expected is None or request.method != "POST" or request.url != httpx.URL(self.url)
                    or type(request.stream) is not httpx.ByteStream
                    or request.content != expected["body"] or b"".join(request.stream) != expected["body"]
                    or any(request.headers.get(name) != value for name, value in expected["headers"].items())):
                raise ValueError("relay_wire_changed")
            method = request.method.encode("ascii")
            url = (request.url.raw_scheme, request.url.raw_host, request.url.port, request.url.raw_path)
            original_headers = tuple(request.headers.raw)
            headers = None
            previous = request.extensions.get("trace")

            def bind_headers():
                nonlocal headers
                if headers is not None:
                    return
                observed = tuple(request.headers.raw)
                if observed != original_headers:
                    from opentelemetry.propagate import inject
                    propagation = {}
                    inject(propagation)
                    limits = {"traceparent": 55, "tracestate": 512, "baggage": 8192}
                    if any(name not in limits or not isinstance(value, str)
                           or not value.isascii() or len(value) > limits[name]
                           or any(ord(char) < 32 or ord(char) == 127 for char in value)
                           for name, value in propagation.items()):
                        raise ValueError("unsupported_relay_trace_propagation")
                    propagated = httpx.Headers(original_headers)
                    for name, value in propagation.items():
                        propagated[name] = value
                    if observed != tuple(propagated.raw):
                        raise ValueError("relay_wire_changed")
                headers = observed

            def check(core):
                if (type(core) is not httpcore.Request or core.method != method
                        or type(core.url) is not httpcore.URL
                        or (core.url.scheme, core.url.host, core.url.port, core.url.target) != url
                        or tuple(core.headers) != headers or type(core.stream) is not httpx.ByteStream
                        or b"".join(core.stream) != expected["body"]):
                    raise ValueError("relay_wire_changed")

            async def trace(event, info):
                if event.startswith("http2."):
                    raise ValueError("unsupported_relay_transport")
                # Native OTel injects after HTTPX hooks, inside its client span.
                # Freeze only that exact propagation, before retained callbacks.
                bind_headers()
                if previous is not None:
                    result = previous(event, info)
                    if inspect.isawaitable(result):
                        await result
                if event in ("http11.send_request_headers.started", "http11.send_request_body.started"):
                    await self._authorize()
                    check(info.get("request"))
            await self._authorize()
            request.extensions["trace"] = trace

        async def pre_mcp(self, event, context):
            await self._authorize()
            if event["serverName"] != "threadlight-governed":
                return None
            if event["toolName"] not in self.tools or not event.get("toolCallId"):
                raise ValueError("governance_call_identity_required")
            key = hashlib.sha256(canonical([
                self.invocation_id, event["sessionId"], event["toolCallId"],
            ])).hexdigest()
            body = hashlib.sha256(canonical([event["toolName"], event["arguments"]])).digest()
            previous = self.tickets.get(key)
            if previous is not None and previous != body:
                raise ValueError("governance_call_identity_reused")
            self.tickets[key] = body
            return {"metaToUse": {**(event.get("_meta") or {}), "threadlight/call": key}}

        def _observe(self, method, operation, response, output):
            """Expose deferred resume and surface pending results; never alter an effect result."""
            if response.status_code != 200 or method not in ("tools/list", "tools/call"):
                return output
            if not response.headers.get("Content-Type", "").startswith("application/json"):
                if method == "tools/call" and b"pending_approval" in output:
                    raise ValueError("unsupported_pending_transport")
                return output
            document = json.loads(output)
            result = document.get("result")
            if not isinstance(result, dict):
                return output
            if method == "tools/list":
                for tool in result.get("tools", []):
                    if (tool.get("name") in self.tools
                            and (tool.get("_meta") or {}).get("threadlight.approval_mode") == "deferred"):
                        properties = tool.setdefault("inputSchema", {}).setdefault("properties", {})
                        if OPERATION_ARGUMENT in properties:
                            raise ValueError("reserved_operation_argument")
                        properties[OPERATION_ARGUMENT] = deepcopy(OPERATION_SCHEMA)
                        self.resumable.add(tool["name"])
                return canonical(document)
            body = result.get("structuredContent")
            if isinstance(body, dict) and body.get("status") == "pending_approval":
                from govern_control_plane.review import validate_pending_review
                validate_pending_review(body)
                if result.get("isError") or body["operation_id"] != operation:
                    raise ValueError("invalid_pending_result")
                if self.on_pending is not None:
                    self.on_pending(deepcopy(body))
            return output

        def app(self):
            from starlette.applications import Starlette
            from starlette.responses import JSONResponse, Response
            from starlette.routing import Route

            async def forward(request):
                if not secrets.compare_digest(request.headers.get("X-Threadlight-Relay", ""), self.secret):
                    return JSONResponse({"error": "unauthorized"}, 401)
                try:
                    raw = bytearray()
                    async with asyncio.timeout(5):
                        async for part in request.stream():
                            raw.extend(part)
                            if len(raw) > 32768:
                                raise ValueError()
                    document = json.loads(raw)
                    method = document["method"]
                    if method not in ("initialize", "notifications/initialized", "ping", "tools/list", "tools/call"):
                        raise ValueError()
                    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
                    operation = None
                    if method == "tools/call":
                        params = document["params"]
                        key = params.get("_meta", {}).get("threadlight/call")
                        if (not key or self.tickets.get(key) != hashlib.sha256(canonical([
                                params["name"], params.get("arguments", {})])).digest()):
                            return JSONResponse({"error": "invalid_call_identity"}, 403)
                        if params["name"] in self.resumable and OPERATION_ARGUMENT in params.get("arguments", {}):
                            # A selector for the ORIGINAL operation, never consent: the gateway
                            # rebinds principal, action, arguments, policy, facts and the grant.
                            from govern_control_plane.models import Identifier, parse
                            key = parse(Identifier, canonical(params["arguments"].pop(OPERATION_ARGUMENT)))
                        operation = key
                        headers["Idempotency-Key"] = key
                        headers["X-Correlation-ID"] = key
                        from govern_control_plane.attestations import EVIDENCE_ARGUMENT, EVIDENCE_META
                        evidence = params.get("arguments", {}).pop(EVIDENCE_ARGUMENT, None)
                        if EVIDENCE_META in params.get("_meta", {}):
                            raise ValueError("caller_evidence_metadata_unsupported")
                        if evidence is not None:
                            params.setdefault("_meta", {})[EVIDENCE_META] = evidence
                    # MCP's protocol header is not an identity claim.
                    if "MCP-Protocol-Version" in request.headers:
                        headers["MCP-Protocol-Version"] = request.headers["MCP-Protocol-Version"]
                    token = await self.credential.get_token(self.scope)
                    await self._authorize()
                    headers["Authorization"] = f"Bearer {token.token}"
                    body = canonical(document)
                    marker = self._outbound.set({"body": body, "headers": dict(headers)})
                    try:
                        async with self.http.stream("POST", self.url, content=body,
                                                    headers=headers, follow_redirects=False) as response:
                            output = bytearray()
                            async for part in response.aiter_bytes():
                                output.extend(part)
                                if len(output) > 65536:
                                    raise ValueError()
                            await self._authorize()
                            output = self._observe(method, operation, response, bytes(output))
                            return Response(output, status_code=response.status_code,
                                            media_type=response.headers.get("Content-Type", "application/json"))
                    finally:
                        self._outbound.reset(marker)
                except Exception:
                    return JSONResponse({"error": "gateway_unavailable"}, 503)
            return Starlette(routes=[Route("/mcp", forward, methods=["POST"])])


def build_host(config, *, bootstrap_gate=None, **host_options):
        from azure.ai.agentserver.invocations import InvocationAgentServerHost
        if VOICE_BRIDGE:
            # Same Invocations host plus the typed Voice Live Bridge routes.
            from azure.ai.agentserver.invocations.voice import VoiceAgentServerHost as InvocationAgentServerHost
        from azure.identity.aio import DefaultAzureCredential
        from copilot import CopilotClient, PermissionHandler, ProviderConfig
        from copilot.session_events import SessionEventType
        import httpx
        from starlette.responses import JSONResponse, StreamingResponse
        import uvicorn

        base = Path(__file__).resolve().parent
        routed = route_mcp_servers(
            config["mcp_servers"], config["contract"], config["mcp_bindings"],
            os.environ["GOVERNED_TOOL_GATEWAY_URL"])

        class GovernedHost(InvocationAgentServerHost):
            async def _readiness_endpoint(self, request):
                try:
                    from datetime import datetime, timezone
                    from govern_control_plane.client import ServiceTransport
                    from govern_control_plane.models import SignedBundle, canonical, parse
                    async with DefaultAzureCredential() as credential, httpx.AsyncClient(
                            timeout=5, trust_env=False, follow_redirects=False) as http:
                        async with ServiceTransport(
                                base_url=os.environ["GOV_CONTROL_PLANE_URL"],
                                scope=config["control_plane_scope"], credential=credential, http=http) as service:
                            _, signed = await service.request("GET", f"/bundles/{config['policy_id']}/{config['policy_version']}")
                        policy = parse(SignedBundle, canonical(signed)).envelope
                        if (policy.tenant_id != config["tenant_id"] or policy.key_id != config["key_id"]
                                or policy.policy_id != config["policy_id"] or policy.version != config["policy_version"]
                                or policy.expires_at <= datetime.now(timezone.utc)):
                            raise ValueError("policy_unavailable")
                        token = await credential.get_token(config["gateway_scope"])
                        response = await http.get(os.environ["GOVERNED_TOOL_GATEWAY_URL"].rsplit("/", 1)[0] + "/health",
                                                  headers={"Authorization": f"Bearer {token.token}"})
                        health = response.json()
                        if (response.status_code != 200 or health.get("policy_digest") != policy.content_digest
                                or any(not health["bindings"][name]["healthy"]
                                       for name in routed["threadlight-governed"]["tools"])):
                            raise ValueError()
                        return JSONResponse({"scope": "gateway-controls-not-effect-closure",
                                             "bindings": health["bindings"]})
                except Exception:
                    return JSONResponse({"status": "unavailable"}, 503)

        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
        host = GovernedHost(**host_options)

        async def stream(invocation_id, prompt):
            credential = http = sock = server = task = client = session = unsubscribe = None
            try:
                credential = DefaultAzureCredential()
                http = httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False)
                model_token = await credential.get_token("https://ai.azure.com/.default")
                if bootstrap_gate is not None:
                    await bootstrap_gate.authorize()
                queue = asyncio.Queue()
                relay = McpRelay(
                    gateway_url=os.environ["GOVERNED_TOOL_GATEWAY_URL"], scope=config["gateway_scope"],
                    credential=credential, invocation_id=invocation_id,
                    tools=routed["threadlight-governed"]["tools"], http=http,
                    bootstrap_gate=bootstrap_gate,
                    on_pending=lambda body: queue.put_nowait(("governance_pending", body)))
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                sock.listen()
                server = uvicorn.Server(uvicorn.Config(relay.app(), access_log=False, log_level="critical"))
                task = asyncio.create_task(server.serve(sockets=[sock]))
                client = CopilotClient()
                async with asyncio.timeout(10):
                    while not server.started:
                        if task.done():
                            task.result()
                            raise RuntimeError("relay_start_failed")
                        await asyncio.sleep(0.01)
                servers = deepcopy(routed)
                servers["threadlight-governed"].update(
                    url=f"http://127.0.0.1:{port}/mcp", headers={"X-Threadlight-Relay": relay.secret})
                await client.start()
                if bootstrap_gate is not None:
                    await bootstrap_gate.authorize()
                session = await client.create_session(
                    provider=ProviderConfig(type="azure", base_url=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                                            wire_api="responses", bearer_token=model_token.token),
                    model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                    system_message={"mode": "replace", "content": (base / "copilot-instructions.md").read_text()},
                    skill_directories=[str(base / "skills")],
                    available_tools=list(GOVERNED_AVAILABLE_TOOLS),
                    working_directory=str(Path.home()), streaming=True,
                    mcp_servers=servers, hooks={"on_pre_mcp_tool_call": relay.pre_mcp},
                    on_permission_request=PermissionHandler.approve_all,
                    enable_config_discovery=False,
                )
                def event_received(event):
                    queue.put_nowait(event)
                unsubscribe = session.on(event_received)
                # New session + fresh BYOK bearer per invocation; never run past its validity.
                deadline = model_token.expires_on - 60
                if bootstrap_gate is not None:
                    await bootstrap_gate.authorize()
                    deadline = min(deadline, bootstrap_gate.signed.binding.expires_at.timestamp())
                async with asyncio.timeout(max(0, deadline - time.time())):
                    await session.send(prompt)
                    while True:
                        event = await queue.get()
                        if isinstance(event, tuple):
                            yield _pending_event(event[1])
                            continue
                        if event.type == SessionEventType.SESSION_IDLE:
                            break
                        if event.type == SessionEventType.SESSION_ERROR:
                            raise RuntimeError("agent_unavailable")
                        if event.type.value in ("assistant.message", "assistant.message_delta"):
                            yield b"data: " + canonical(_message_payload(event)) + b"\n\n"
                yield b"event: done\ndata: " + canonical({"invocation_id": invocation_id}) + b"\n\n"
            except Exception:
                yield b'data: {"type":"error","message":"agent_unavailable"}\n\n'
            finally:
                await close_invocation(unsubscribe=unsubscribe, session=session, client=client,
                                       server=server, task=task, sock=sock, http=http, credential=credential)

        @host.invoke_handler
        async def invoke(request):
            try:
                data = await request.json()
                if not isinstance(data.get("input"), str) or not data["input"].strip():
                    raise ValueError()
            except Exception:
                return JSONResponse({"error": "invalid_request"}, 400)
            return StreamingResponse(stream(request.state.invocation_id, data["input"]),
                                     media_type="text/event-stream")
        if VOICE_BRIDGE:
            attach_voice_bridge(host, stream)
        return host


def _route_selected(result, servers, selected, bindings, gateway_url):
    url = urlsplit(gateway_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path):
        raise ValueError("invalid_gateway_url")
    if "threadlight-governed" in servers:
        raise ValueError("reserved_gateway_server")
    actions = []
    for tool in selected:
        if tool["enforcement_path"] != "governed-tool-gateway":
            raise ValueError("github_copilot_local_hooks_unsupported")
        binding = bindings.get(tool["id"])
        if not binding or binding["server"] not in result:
            raise ValueError("missing_mcp_binding")
        name, original = binding["server"], binding["tool"]
        for other, candidate in servers.items():
            if other != name and candidate.get("url") == servers[name].get("url"):
                inventory = candidate.get("tools", ["*"])
                if original in inventory or "*" in inventory:
                    raise ValueError("bound_tool_alias_accessible")
        configured = result[name]
        inventory = configured.get("tools")
        if not isinstance(inventory, list) or "*" in inventory or original not in inventory:
            raise ValueError("explicit_tool_inventory_required")
        if original != tool["id"]:
            raise ValueError("gateway_action_name_must_match_tool_id")
        configured["tools"] = [t for t in inventory if t != original]
        if not configured["tools"]:
            del result[name]
        actions.append(tool["id"])
    result["threadlight-governed"] = {
        "type": "http", "url": gateway_url, "tools": actions,
    }
    return result


def remote_host(config, *, credential, signer, http=None, env=None, **host_options):
    from govern_control_plane.bootstrap import runtime_gate

    async def initialize(binding, stack):
        resolved = {**config, "policy_digest": binding.policy_digest,
                    "policy_version": binding.policy_version}
        host = build_host(resolved, bootstrap_gate=gate, **host_options)
        await stack.enter_async_context(host.router.lifespan_context(host))
        return host

    gate = runtime_gate(config, env=env or os.environ, credential=credential,
                        signer=signer, initialize=initialize, http=http)
    return gate


if __name__ == "__main__":
    configuration = json.loads((Path(__file__).resolve().parent / "governance-config.json").read_text())
    if "remote_bootstrap" in configuration:
        from govern_control_plane.bootstrap import serve_remote
        asyncio.run(serve_remote(configuration, remote_host))
    else:
        build_host(configuration).run()
