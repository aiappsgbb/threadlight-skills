"""Foundry Hosted Agent — GHCP SDK + BYOK via Invocations protocol.

Reference template for deploying a hosted agent using the GitHub Copilot SDK
with BYOK authentication and SSE streaming.

Two auth modes, selected automatically from environment variables:
  * BYOK Foundry model: ``FOUNDRY_PROJECT_ENDPOINT`` (auto-injected by the
    platform) + ``AZURE_AI_MODEL_DEPLOYMENT_NAME`` -> Managed Identity.
    No ``GITHUB_TOKEN`` required.
  * GitHub Copilot model: ``GITHUB_TOKEN`` set -> uses GitHub-hosted models.
    Fallback path; useful for local dev or quickstarts.

Optional voice front-end (Foundry Hosted Voice, preview): with
``THREADLIGHT_VOICE_BRIDGE=1`` the same container also serves Voice Live Bridge
Protocol 1.0 on ``/invocations_ws``, so a managed ``kind: voice`` wrapper can
use this agent as its conversation engine. Voice Live owns audio; this file
maps ``user.message`` text onto the same Copilot conversation logic. Off by
default; the generator sets it only on an explicit user opt-in.

Usage:
    Copy this file as ``main.py`` in your agent project root.
    Adapt ``_load_mcp_servers()`` for your MCP server configuration
    (``dict[str, dict]`` keyed by server name).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import pathlib
import re
import sys
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from copilot import CopilotClient, PermissionHandler, ProviderConfig
from copilot.session_events import SessionEventType

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = pathlib.Path(os.getenv("PROJECT_DIR", "/app")).resolve()

# ---------------------------------------------------------------------------
# Turn telemetry (latency waterfall)
# ---------------------------------------------------------------------------
# The Invocations host exports one server span per invocation to the project's
# Application Insights (https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry).
# The Copilot CLI makes the model and MCP calls in its own process, so these
# child spans, built from the SDK's session events, are what lets a load test
# (threadlight-loadtest waterfall) separate session start, model calls and tool
# calls from runtime overhead. Metadata only: never prompt, answer, tool
# arguments, tool results or error messages. A telemetry failure never affects
# the answer.
try:
    from opentelemetry import trace as _otel_trace
    _TRACER = _otel_trace.get_tracer("threadlight.ghcp.turn")
except Exception:  # noqa: BLE001 - telemetry is optional
    _otel_trace = None
    _TRACER = None

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _ns(value) -> int | None:
    """A duration (timedelta, or milliseconds as a number) in nanoseconds."""
    if value is None:
        return None
    if hasattr(value, "total_seconds"):
        return (value.days * 86_400 + value.seconds) * 1_000_000_000 + value.microseconds * 1_000
    if isinstance(value, (int, float)) and value >= 0:
        return int(value * 1_000_000)
    return None


def _event_ns(event) -> int:
    stamp = getattr(event, "timestamp", None)
    if stamp is not None and getattr(stamp, "tzinfo", None) is not None:
        return _ns(stamp - _EPOCH)
    return time.time_ns()


def _failure_status(data) -> int | None:
    """HTTP status of a failed model call; read from the message only when the SDK leaves it empty.

    Live 2026-11-11: throttled calls arrived with ``status_code`` None and the
    status only in ``error_message``. Only the number is kept, never the text.
    """
    status = getattr(data, "status_code", None)
    if isinstance(status, int):
        return status
    message = str(getattr(data, "error_message", "") or "")
    found = re.search(r"\b([45]\d\d)\b", message)
    if found:
        return int(found.group(1))
    lowered = message.lower()
    return 429 if "rate limit" in lowered or "too many requests" in lowered else None


def _event_kind(event) -> str:
    kind = getattr(event, "type", None)
    return str(getattr(kind, "value", kind) or "")


class _TurnTelemetry:
    """Child spans for one turn: session open, model calls, tool calls."""

    def __init__(self, invocation_id: str = "") -> None:
        self._turn = None
        self._context = None
        self._tools: dict[str, tuple[int, str, str]] = {}
        self._sent_ns: int | None = None
        self._first_token_ns: int | None = None
        self.totals = {"model_calls": 0, "tool_calls": 0, "model_failures": 0, "throttled": 0,
                       "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        self._model = ""
        try:
            if _TRACER is not None:
                # The invocation id joins these spans to the caller's request:
                # the host's own baggage is detached before the body streams.
                self._turn = _TRACER.start_span("threadlight.copilot.turn", attributes=(
                    {"threadlight.invocation_id": invocation_id} if invocation_id else None))
                self._context = _otel_trace.set_span_in_context(self._turn)
        except Exception:  # noqa: BLE001
            self._turn = None

    def _span(self, name: str, start: int, end: int, attributes: dict, error: str | None = None) -> None:
        if self._turn is None:
            return
        try:
            span = _TRACER.start_span(name, context=self._context, start_time=start,
                                      attributes={k: v for k, v in attributes.items() if v is not None})
            if error is not None:
                span.set_attribute("error.type", error)
                span.set_status(_otel_trace.Status(_otel_trace.StatusCode.ERROR))
            span.end(end_time=max(start, end))
        except Exception:  # noqa: BLE001
            pass

    def session_open(self, started_ns: int, *, client_cold_start: bool, kind: str) -> None:
        self._span("threadlight.copilot.session_open", started_ns, time.time_ns(),
                   {"threadlight.session.client_cold_start": client_cold_start, "threadlight.session.kind": kind})

    def sent(self) -> None:
        self._sent_ns = time.time_ns()

    def observe(self, event) -> None:
        try:
            self._observe(_event_kind(event), getattr(event, "data", None), event)
        except Exception:  # noqa: BLE001
            pass

    def _observe(self, kind: str, data, event) -> None:
        if kind in ("assistant.message_delta", "assistant.streaming_delta") and self._first_token_ns is None:
            self._first_token_ns = time.time_ns()
        elif kind == "tool.execution_start":
            name = getattr(data, "mcp_tool_name", None) or getattr(data, "tool_name", None) or "tool"
            self._tools[str(getattr(data, "tool_call_id", ""))] = (
                _event_ns(event), str(name), str(getattr(data, "mcp_server_name", None) or ""))
        elif kind == "tool.execution_complete":
            started = self._tools.pop(str(getattr(data, "tool_call_id", "")), None)
            if started is None:
                return
            start, name, server = started
            success = bool(getattr(data, "success", False))
            self.totals["tool_calls"] += 1
            self._span(f"execute_tool {name}", start, _event_ns(event),
                       {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                        "threadlight.mcp.server": server or None, "threadlight.tool.success": success},
                       None if success else "tool_error")
        elif kind == "assistant.usage":
            end = _event_ns(event)
            model = str(getattr(data, "model", "") or self._model or "model")
            self._model = model
            ttft = _ns(getattr(data, "time_to_first_token", None))
            usage = {key: getattr(data, key, None) for key in
                     ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens")}
            self.totals["model_calls"] += 1
            for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
                self.totals[key] += int(usage[key] or 0)
            self._span(f"chat {model}", end - (_ns(getattr(data, "duration", None)) or 0), end, {
                "gen_ai.operation.name": "chat", "gen_ai.request.model": model,
                "gen_ai.usage.input_tokens": usage["input_tokens"],
                "gen_ai.usage.output_tokens": usage["output_tokens"],
                "threadlight.usage.reasoning_tokens": usage["reasoning_tokens"],
                "threadlight.usage.cache_read_tokens": usage["cache_read_tokens"],
                "threadlight.time_to_first_token_ms": None if ttft is None else ttft // 1_000_000,
                "gen_ai.response.finish_reasons": (str(getattr(data, "finish_reason", "") or "") or None),
            })
        elif kind == "model.call_failure":
            end = _event_ns(event)
            model = str(getattr(data, "model", "") or self._model or "model")
            status = _failure_status(data)
            error_class = ("throttled" if status == 429 else "server_error" if status and status >= 500
                           else "client_error" if status else "unknown")
            self.totals["model_failures"] += 1
            if status == 429:
                self.totals["throttled"] += 1
            # The Azure Monitor exporter folds http.response.status_code/error.type
            # into its own columns, so the threadlight.* copies stay queryable.
            self._span(f"chat {model}", end - (_ns(getattr(data, "duration", None)) or 0), end,
                       {"gen_ai.operation.name": "chat", "gen_ai.request.model": model,
                        "http.response.status_code": status, "threadlight.model.status_code": status,
                        "threadlight.error_class": error_class},
                       str(status) if status is not None else "model_call_failure")

    def finish(self, error: bool = False) -> None:
        if self._turn is None:
            return
        try:
            now = time.time_ns()
            for start, name, server in self._tools.values():
                self._span(f"execute_tool {name}", start, now,
                           {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                            "threadlight.mcp.server": server or None}, "incomplete")
            self._tools.clear()
            for key, value in self.totals.items():
                name = f"gen_ai.usage.{key}" if key in ("input_tokens", "output_tokens") else f"threadlight.turn.{key}"
                self._turn.set_attribute(name, value)
            if self._model:
                self._turn.set_attribute("gen_ai.request.model", self._model)
            if self._sent_ns is not None and self._first_token_ns is not None:
                self._turn.set_attribute("threadlight.turn.first_token_ms",
                                         (self._first_token_ns - self._sent_ns) // 1_000_000)
            if error:
                self._turn.set_status(_otel_trace.Status(_otel_trace.StatusCode.ERROR))
            self._turn.end()
        except Exception:  # noqa: BLE001
            pass
        self._turn = None

# ---------------------------------------------------------------------------
# BYOK Authentication
# ---------------------------------------------------------------------------
# Primary shape (matches the official Microsoft sample, ~2x faster than the
# legacy dict form): ProviderConfig(type="azure", base_url=<bare project
# endpoint>, wire_api="responses").
#
# The SDK appends the ``?api-version=...`` query param itself when
# ``type="azure"`` — do NOT add ``/openai/v1/`` to the URL.
#
# Token scope is ``https://ai.azure.com/.default`` (NOT
# ``cognitiveservices.azure.com`` — renamed in the May 2026 Foundry
# data-plane rename).

_BYOK_CREDENTIAL = None
_BYOK_ENDPOINT = ""
_MODEL_BASE_URL = ""
_MODEL = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "") or os.getenv("MODEL_DEPLOYMENT_NAME", "")


def _model_base_url(project_endpoint: str) -> str:
    """Where MODEL calls go: the customer's model gateway when set, else the project endpoint.

    ``MODEL_GATEWAY_URL`` (private profile with an API Management gateway) is a
    pass-through route to the same project endpoint, so the token audience and the
    Responses wire format stay the same. It must end in ``/openai/v1``: with
    ``type="azure"`` the SDK keeps only the origin of any other base URL. Unset
    or empty: the Fast-PoC behaviour. Only model calls use it; project calls
    (toolbox MCP) keep ``_BYOK_ENDPOINT``.
    """
    return os.getenv("MODEL_GATEWAY_URL", "").strip().rstrip("/") or project_endpoint


def _init_byok() -> bool:
    """Initialise the BYOK credential. Returns False if BYOK is not configured."""
    global _BYOK_CREDENTIAL, _BYOK_ENDPOINT, _MODEL_BASE_URL
    endpoint = (
        os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/")
        or os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip().rstrip("/")
    )
    if not endpoint or not _MODEL:
        return False
    try:
        from azure.identity import DefaultAzureCredential
        _BYOK_CREDENTIAL = DefaultAzureCredential()
        _BYOK_ENDPOINT = endpoint
        _MODEL_BASE_URL = _model_base_url(endpoint)
        token = _BYOK_CREDENTIAL.get_token("https://ai.azure.com/.default")
        logger.info("BYOK verified: %s (model=%s, model route %s, token expires in %ds)",
                    endpoint, _MODEL, _MODEL_BASE_URL, int(token.expires_on - time.time()))
        return True
    except Exception as exc:
        logger.error("BYOK init failed: %s", exc)
        return False


def _get_provider() -> ProviderConfig | None:
    """Mint a fresh BYOK ProviderConfig for CopilotClient session.

    Returns ``None`` when BYOK is not configured — caller falls back to the
    GITHUB_TOKEN path.
    """
    if not _BYOK_CREDENTIAL or not _BYOK_ENDPOINT:
        return None
    try:
        token = _BYOK_CREDENTIAL.get_token("https://ai.azure.com/.default").token
        return ProviderConfig(
            type="azure",
            base_url=_MODEL_BASE_URL or _BYOK_ENDPOINT,
            wire_api="responses",
            bearer_token=token,
        )
    except Exception as exc:
        logger.error("BYOK token mint failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Instructions & MCP
# ---------------------------------------------------------------------------

def _load_instructions() -> str:
    ci = BASE_DIR / "copilot-instructions.md"
    return ci.read_text(encoding="utf-8").strip() if ci.exists() else ""


def _load_mcp_servers() -> dict[str, dict] | None:
    """Load MCP servers from environment variables.

    Adapt this function for your agent's MCP server configuration. Return a
    ``dict[str, dict]`` keyed by server name: github-copilot-sdk 1.0.x converts
    ``mcp_servers`` with ``servers.items()``, so any other shape fails session
    creation and the Invocations call returns 502.

    Each value needs ``type`` plus ``url`` (``"http"``) or ``command``
    (``"stdio"``), and a ``tools`` allow-list. Pass an HTTPS URL ending in
    ``/mcp`` for HTTP servers.
    """
    servers: dict[str, dict] = {}
    mcp_fqdn = os.environ.get("MCP_SERVER_FQDN", "").strip()
    if mcp_fqdn:
        servers["mcp"] = {"type": "http", "url": f"https://{mcp_fqdn}/mcp", "tools": ["*"]}
    return servers or None


def _toolbox_enabled() -> bool:
    return bool(os.environ.get("TOOLBOX_NAME", "").strip())


class _ToolboxBridge:
    """Minimal MCP (streamable HTTP, JSON-RPC) client for one Foundry toolbox request.

    Bridges toolbox tools into Copilot SDK custom tools instead of handing the
    toolbox to the SDK's native MCP client: through the toolbox the native client
    rejected every result because the proxy returns ``structuredContent: null``
    (live, 2026-11-11). Learn shows the same bridge pattern for the Copilot SDK
    (https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox).

    The agent authenticates with its own agent identity (``https://ai.azure.com``).
    Per-user tools (``AgentOnBehalfOfUser`` / ``UserEntraToken`` connections) also
    need the platform's per-request ``x-agent-foundry-call-id``: the toolbox
    resolves the caller from it. It is forwarded unchanged, never parsed, logged
    or reused across requests
    (https://learn.microsoft.com/azure/foundry/agents/how-to/tools/tool-authentication).
    """

    def __init__(self, url: str, token: str, call_id: str) -> None:
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json"}
        if call_id:
            self.headers["x-agent-foundry-call-id"] = call_id
        self._id = 0

    async def _rpc(self, http, method: str, params: dict | None = None, *, notify: bool = False) -> dict:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._id += 1
            body["id"] = self._id
        async with http.post(self.url, json=body, headers=self.headers) as resp:
            if "mcp-session-id" in resp.headers:
                self.headers["mcp-session-id"] = resp.headers["mcp-session-id"]
            text = await resp.text()
            if notify:
                return {}
            if resp.status >= 400:
                raise RuntimeError(f"toolbox HTTP {resp.status}")
        messages = [text] if not text.lstrip().startswith(("event:", "data:")) else [
            line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        for raw in messages:
            msg = json.loads(raw) if raw else {}
            if msg.get("id") == body.get("id"):
                if "error" in msg:
                    raise RuntimeError(str(msg["error"].get("message", msg["error"]))[:500])
                return msg.get("result") or {}
        raise RuntimeError(f"toolbox: no JSON-RPC result for {method}")

    async def _session(self, http) -> None:
        await self._rpc(http, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                             "clientInfo": {"name": "threadlight-ghcp", "version": "1"}})
        await self._rpc(http, "notifications/initialized", notify=True)

    async def list_tools(self) -> list[dict]:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as http:
            await self._session(http)
            return (await self._rpc(http, "tools/list")).get("tools") or []

    async def call_tool(self, name: str, arguments: dict) -> str:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as http:
            await self._session(http)
            result = await self._rpc(http, "tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("structuredContent") is not None:
            return json.dumps(result["structuredContent"])
        return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")


async def _toolbox_tools(call_id: str) -> list:
    """Copilot SDK tools for the configured toolbox (empty when not configured)."""
    name = os.environ.get("TOOLBOX_NAME", "").strip()
    if not name or not _BYOK_ENDPOINT or not _BYOK_CREDENTIAL:
        return []
    from copilot.tools import Tool, ToolResult

    if not call_id:
        logger.warning("toolbox: request without x-agent-foundry-call-id; per-user tools cannot resolve the caller")
    token = _BYOK_CREDENTIAL.get_token("https://ai.azure.com/.default").token
    url = f"{_BYOK_ENDPOINT}/toolboxes/{name}/mcp?api-version=v1"
    bridge = _ToolboxBridge(url, token, call_id)
    try:
        listed = await bridge.list_tools()
    except Exception as exc:  # noqa: BLE001 - a failing toolbox must not fail the turn
        logger.warning("toolbox: tools/list failed: %s", str(exc)[:300])
        return []

    def make(original: str, schema: dict | None, description: str):
        async def handler(invocation):
            try:
                # One bridge (one MCP session) per call: parallel tool calls never share state.
                text = await _ToolboxBridge(url, token, call_id).call_tool(original, invocation.arguments or {})
                return ToolResult(text_result_for_llm=text)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(text_result_for_llm=f"tool error: {exc}", result_type="failure", error=str(exc)[:300])
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", original)[:64]
        return Tool(name=safe, description=description or original, handler=handler,
                    parameters=schema or {"type": "object", "properties": {}})

    def needs_approval(tool: dict) -> bool:
        config = ((tool.get("_meta") or {}).get("tool_configuration") or {})
        return str(config.get("require_approval", "never")).lower() == "always"

    # The toolbox does not block tools/call for require_approval "always"; the
    # runtime must. This runtime has no approval step, so those tools are not
    # exposed (https://learn.microsoft.com/azure/foundry/agents/how-to/tools/use-toolbox-hosted-agent).
    held = [t.get("name") for t in listed if needs_approval(t)]
    if held:
        logger.warning("toolbox: %d tool(s) need approval and are not exposed: %s", len(held), held)
    tools = [make(t["name"], t.get("inputSchema"), t.get("description", ""))
             for t in listed if t.get("name") and not needs_approval(t)]
    logger.info("toolbox: %d tool(s) bridged", len(tools))
    return tools


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------
# One CopilotClient per container, one Copilot session per FOUNDRY session.
#
# The caller's ``agent_session_id`` query parameter selects the conversation:
# the same id continues it, a different id never sees it. Without one, each
# invocation gets a fresh session that is closed afterwards. The container-wide
# ``FOUNDRY_AGENT_SESSION_ID`` is deliberately NOT a conversation key: one
# shared session made every Foundry evaluation row join the same conversation
# in the 2026-11-07 live run (21 s -> 135 s per row, earlier rows visible to
# later ones). A body with ``"new_conversation": true`` always gets a fresh
# session too.
#
# Lifecycle (github-copilot-sdk keeps transcripts on disk until
# ``client.delete_session(id)``; ``disconnect()`` only frees memory):
# * fresh / new_conversation sessions: disconnect, then delete from disk;
# * keyed sessions use the Foundry session id as the Copilot session id
#   (``create_session(session_id=key)``), so ``resume_session(key)`` restores
#   the conversation. The in-memory cache is LRU-bounded
#   (``COPILOT_MAX_SESSIONS``) and a session is reopened after
#   ``_SESSION_MAX_AGE_SECONDS`` so a BYOK bearer token minted at creation never
#   outlives its validity; both only disconnect and keep the transcript on disk.
# * at most ``COPILOT_MAX_DORMANT_SESSIONS`` (default 4x the cache) evicted
#   transcripts stay on disk; the oldest beyond that are deleted.
# Turns in one session are serialised.

VOICE_BRIDGE = os.environ.get("THREADLIGHT_VOICE_BRIDGE", "").strip().lower() in ("1", "true")

if VOICE_BRIDGE:
    # Same Invocations host plus the typed Voice Live Bridge routes
    # (/invocations_ws and /voice_conversation_engine).
    from azure.ai.agentserver.invocations.voice import (
        AgentError, ResponseCreated, ResponseDone, ResponseNone, ResponseOutputTextDone,
        SessionReady, SessionRejected, VoiceAgentServerHost, new_item_id, new_response_id,
    )
    app = VoiceAgentServerHost()
else:
    app = InvocationAgentServerHost()

_client: CopilotClient | None = None
_client_lock = asyncio.Lock()
_skills_dir = str(BASE_DIR / "skills")
_MAX_SESSIONS = max(1, int(os.environ.get("COPILOT_MAX_SESSIONS", "") or "16"))
_SESSION_MAX_AGE_SECONDS = 45 * 60
_MAX_DORMANT_SESSIONS = max(0, int(os.environ.get("COPILOT_MAX_DORMANT_SESSIONS", "") or str(4 * _MAX_SESSIONS)))
_dormant: "OrderedDict[str, None]" = OrderedDict()
_sessions: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()
_session_locks: dict[str, asyncio.Lock] = {}
_sessions_lock = asyncio.Lock()


async def _ensure_client() -> CopilotClient:
    """Start the CopilotClient once per worker."""
    global _client
    async with _client_lock:
        if _client is not None:
            return _client
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if _get_provider():
            # BYOK mode: Foundry model via Managed Identity, no token needed.
            client = CopilotClient()
        elif github_token:
            # Copilot mode: GitHub-hosted models via personal/app token.
            client = CopilotClient(github_token=github_token)
        else:
            raise RuntimeError(
                "No auth configured. Set FOUNDRY_PROJECT_ENDPOINT + "
                "AZURE_AI_MODEL_DEPLOYMENT_NAME (BYOK Foundry) or GITHUB_TOKEN (GHCP)."
            )
        await client.start()
        logger.info("CopilotClient started (mode=%s)", "BYOK" if _BYOK_ENDPOINT else "GITHUB_TOKEN")
        _client = client
        return client


# Tool allowlist. Without it a session also exposes the Copilot CLI built-ins
# (bash, create, edit, web_fetch, task, sql, ...): in the 2026-11-11 load test a
# recommend-only pilot ran `bash` 359 times (up to 260 s) and `web_fetch` (#192).
# `available_tools` is the session-wide allowlist, "only these tools exist for
# anyone" (https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/custom-agents);
# entries are source-qualified (SDK ToolSet: builtin:/mcp:/custom:). The pilot gets
# its MCP tools, its custom (toolbox) tools and the `skill` tool for its skills.
# A built-in is added only by name through THREADLIGHT_BUILTIN_TOOLS, which the
# generator sets from an explicit SPEC § 11e `copilot_builtin_tools` opt-in.
_DEFAULT_TOOLS = ("mcp:*", "custom:*", "builtin:skill")
_BUILTIN_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _available_tools() -> list[str]:
    tools = list(_DEFAULT_TOOLS)
    for name in os.environ.get("THREADLIGHT_BUILTIN_TOOLS", "").split(","):
        name = name.strip()
        if not name:
            continue
        if not _BUILTIN_NAME.fullmatch(name):
            logger.warning("Ignoring THREADLIGHT_BUILTIN_TOOLS entry %r: only plain built-in tool names", name)
            continue
        if f"builtin:{name}" not in tools:
            tools.append(f"builtin:{name}")
    return tools


def _session_kwargs() -> dict[str, Any]:
    """Session options; mints a fresh BYOK provider token for every new session."""
    kwargs: dict[str, Any] = {
        "on_permission_request": PermissionHandler.approve_all,
        "available_tools": _available_tools(),
        "streaming": True,
        "working_directory": str(pathlib.Path.home()),
        "provider": _get_provider(),
        "model": _MODEL or None,
    }
    instructions = _load_instructions()
    if instructions:
        kwargs["system_message"] = {"mode": "replace", "content": instructions}
    if pathlib.Path(_skills_dir).is_dir():
        kwargs["skill_directories"] = [_skills_dir]
    mcp = _load_mcp_servers()
    if mcp:
        kwargs["mcp_servers"] = mcp
    return kwargs


async def _delete_transcript(session_id: str) -> None:
    """Best-effort ``client.delete_session``: removes the transcript from disk."""
    if _client is None or not session_id:
        return
    try:
        await _client.delete_session(session_id)
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail the caller
        logger.warning("Copilot session delete failed for %s: %s", session_id, type(exc).__name__)


async def _close_session(session, *, delete: bool) -> None:
    """Disconnect; with ``delete`` also remove the on-disk transcript (best-effort)."""
    close = getattr(session, "disconnect", None)
    if close is not None:
        try:
            await close()
        except Exception as exc:  # noqa: BLE001 - cleanup must not fail the caller
            logger.warning("Copilot session disconnect failed: %s", type(exc).__name__)
    if delete:
        await _delete_transcript(str(getattr(session, "session_id", "") or ""))


class SessionResumeError(RuntimeError):
    """A stored conversation exists but could not be reopened (never replaced by an empty one)."""


_RESUME_ATTEMPTS = 2  # one retry for a transient CLI/transport error


def _is_not_found(exc: Exception) -> bool:
    return "not found" in str(exc).lower()


async def _stored_session_exists(client: CopilotClient, key: str) -> bool | None:
    """True/False from the SDK's O(1) metadata lookup; None when it cannot tell.

    Checking first avoids calling ``resume_session`` for every new Foundry
    session: a failing resume makes the SDK log a full "Session not found"
    traceback even though creating the session is the normal path.
    """
    lookup = getattr(client, "get_session_metadata", None)
    if lookup is None:
        return None
    try:
        return await lookup(key) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session lookup for Foundry session %s failed (%s); trying resume", key, type(exc).__name__)
        return None


async def _open_keyed_session(client: CopilotClient, key: str):
    """Resume the stored conversation for ``key`` or create it when none exists.

    A resume error other than "not found" is retried once and then raised as
    ``SessionResumeError``: silently creating a new session would drop the
    caller's history without any trace.
    """
    exists = await _stored_session_exists(client, key)
    if exists is False:
        session = await client.create_session(session_id=key, **_session_kwargs())
        logger.info("Created Copilot session for Foundry session %s", key)
        return session
    last_error: Exception | None = None
    for attempt in range(1, _RESUME_ATTEMPTS + 1):
        try:
            session = await client.resume_session(key, **_session_kwargs())
            logger.info("Resumed Copilot session for Foundry session %s", key)
            return session
        except Exception as exc:  # noqa: BLE001
            if exists is None and _is_not_found(exc):
                session = await client.create_session(session_id=key, **_session_kwargs())
                logger.info("No stored Copilot session for Foundry session %s; created it", key)
                return session
            last_error = exc
            logger.warning("Resume of Foundry session %s failed (attempt %d/%d): %s: %s",
                           key, attempt, _RESUME_ATTEMPTS, type(exc).__name__, exc)
    logger.error("Could not resume Foundry session %s after %d attempts (%s); not starting an empty conversation",
                 key, _RESUME_ATTEMPTS, type(last_error).__name__)
    raise SessionResumeError(
        f"Could not resume conversation {key} ({type(last_error).__name__}); its history is kept. "
        "Retry the turn, or start a new conversation with a different agent_session_id."
    ) from last_error


async def _keyed_session(client: CopilotClient, key: str):
    """Cached session for ``key`` (caller holds the key's lock), creating it if needed."""
    stale = None
    async with _sessions_lock:
        entry = _sessions.get(key)
        if entry is not None and time.time() - entry[1] < _SESSION_MAX_AGE_SECONDS:
            _sessions.move_to_end(key)
            return entry[0]
        if entry is not None:
            stale = _sessions.pop(key)[0]
    if stale is not None:
        await _close_session(stale, delete=False)  # reopened below with a fresh token
    session = await _open_keyed_session(client, key)
    evicted = []
    async with _sessions_lock:
        _dormant.pop(key, None)
        _sessions[key] = (session, time.time())
        for old_key in list(_sessions):
            if len(_sessions) <= _MAX_SESSIONS:
                break
            lock = _session_locks.get(old_key)
            if old_key == key or (lock is not None and lock.locked()):
                continue  # never close a session in the middle of a turn
            evicted.append(_sessions.pop(old_key)[0])
            _session_locks.pop(old_key, None)
            _dormant[old_key] = None
        expired = []
        while len(_dormant) > _MAX_DORMANT_SESSIONS:
            expired.append(_dormant.popitem(last=False)[0])
    for old in evicted:
        await _close_session(old, delete=False)  # transcript kept: resume_session(key) restores it
    for old_key in expired:
        await _delete_transcript(old_key)
    return session


@asynccontextmanager
async def _conversation(session_key: str, call_id: str = "", telemetry: "_TurnTelemetry | None" = None):
    """Yield the Copilot session for one turn of ``session_key`` (fresh when empty)."""
    started, cold = time.time_ns(), _client is None
    client = await _ensure_client()
    if not session_key:
        kwargs = _session_kwargs()
        tools = await _toolbox_tools(call_id) if _toolbox_enabled() else []
        if tools:
            kwargs["tools"] = tools
        session = await client.create_session(**kwargs)
        if telemetry is not None:
            telemetry.session_open(started, client_cold_start=cold, kind="fresh")
        try:
            yield session
        finally:
            await _close_session(session, delete=True)
        return
    async with _sessions_lock:
        lock = _session_locks.setdefault(session_key, asyncio.Lock())
    async with lock:
        session = await _keyed_session(client, session_key)
        if telemetry is not None:
            telemetry.session_open(started, client_cold_start=cold, kind="keyed")
        yield session


def _session_key(request: Request) -> str:
    """The caller's Foundry ``agent_session_id``; empty means a fresh conversation."""
    params = getattr(request, "query_params", None) or {}
    return str(params.get("agent_session_id") or "").strip()


# ---------------------------------------------------------------------------
# SSE Streaming Handler
# ---------------------------------------------------------------------------

def _event_payload(event) -> dict | None:
    """Serialise one SDK event without letting a bad field abort the SSE stream.

    github-copilot-sdk 1.0.x ``to_dict()`` raises AssertionError on some usage
    fields (e.g. ``timeToFirstTokenMs``); in the 2026-11-07 live deploy that
    killed the stream mid-response. Message events keep their text.
    """
    try:
        return event.to_dict()
    except Exception as exc:  # noqa: BLE001
        event_type = getattr(getattr(event, "type", None), "value", str(getattr(event, "type", "")))
        logger.warning("Skipping unserialisable %s event: %s", event_type, type(exc).__name__)
        if event_type in ("assistant.message", "assistant.message_delta"):
            data = getattr(event, "data", None)
            content = getattr(data, "content", None) or getattr(data, "delta_content", None) or ""
            return {"type": event_type, "data": {"content": content}}
        return None


async def _stream_response(invocation_id: str, input_text: str, session_key: str = "", call_id: str = ""):
    """Stream CopilotClient events as SSE."""
    telemetry = _TurnTelemetry(invocation_id)
    try:
        async with _conversation(session_key, call_id, telemetry) as session:
            async for chunk in _stream_session(session, invocation_id, input_text, telemetry):
                yield chunk
    except SessionResumeError as exc:
        telemetry.finish(error=True)
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n".encode()
        yield f"event: done\ndata: {json.dumps({'invocation_id': invocation_id})}\n\n".encode()
    finally:
        telemetry.finish()


async def _stream_session(session, invocation_id: str, input_text: str, telemetry: "_TurnTelemetry | None" = None):
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event):
        if telemetry is not None:
            telemetry.observe(event)
        if event.type == SessionEventType.SESSION_IDLE:
            queue.put_nowait(None)
        elif event.type == SessionEventType.SESSION_ERROR:
            queue.put_nowait(RuntimeError(getattr(event.data, "message", "error")))
        else:
            queue.put_nowait(event)

    unsubscribe = session.on(on_event)
    try:
        if telemetry is not None:
            telemetry.sent()
        await session.send(input_text)
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                yield f"data: {json.dumps({'type': 'error', 'message': str(item)})}\n\n".encode()
                break
            payload = _event_payload(item)
            if payload is not None:
                yield f"data: {json.dumps(payload)}\n\n".encode()

        yield f"event: done\ndata: {json.dumps({'invocation_id': invocation_id})}\n\n".encode()
    finally:
        unsubscribe()


_HEARTBEAT_SECONDS = 15


async def _final_answer_response(input_text: str, session_key: str = "", call_id: str = ""):
    """Non-streaming mode (``"stream": false``): emit only the final answer text.

    Used by Foundry evaluations: the full SSE event stream is 200-300 KB per
    case, which exhausted the judge model's TPM (429) and exceeded the eval
    result size limit (413). Newline heartbeats keep the connection alive
    while the agent works; they are whitespace for the evaluators.
    """
    telemetry = _TurnTelemetry()
    try:
        async with _conversation(session_key, call_id, telemetry) as session:
            async for chunk in _final_answer_session(session, input_text, telemetry):
                yield chunk
    except SessionResumeError as exc:
        telemetry.finish(error=True)
        yield f"Error: {exc}".encode("utf-8")
    finally:
        telemetry.finish()


async def _final_answer_session(session, input_text: str, telemetry: "_TurnTelemetry | None" = None):
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event):
        if telemetry is not None:
            telemetry.observe(event)
        if event.type == SessionEventType.SESSION_IDLE:
            queue.put_nowait(None)
        elif event.type == SessionEventType.SESSION_ERROR:
            queue.put_nowait(RuntimeError(getattr(event.data, "message", "error")))
        elif getattr(event.type, "value", "") == "assistant.message":
            queue.put_nowait(getattr(getattr(event, "data", None), "content", None) or "")

    unsubscribe = session.on(on_event)
    final = ""
    try:
        if telemetry is not None:
            telemetry.sent()
        await session.send(input_text)
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield b"\n"
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                final = f"Error: {item}"
                break
            if item.strip():
                final = item
        yield (final or "Error: the agent returned no answer.").encode("utf-8")
    finally:
        unsubscribe()


async def _read_invocation(request: Request) -> tuple[dict[str, Any], str]:
    """The request body as ``(options, input_text)``.

    Accepted: a JSON object with ``input`` (or ``message``), a JSON string, or
    plain UTF-8 text. ``azd ai agent invoke <agent> "<text>"`` sends the text as
    is; it returned HTTP 400 when only JSON objects were accepted.
    """
    raw = await request.body()
    text = raw.decode("utf-8").strip()  # UnicodeDecodeError is a ValueError
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        data = text
    if not isinstance(data, dict):
        # A JSON string is the input; any other non-object JSON (42, true,
        # null, [1, 2]) is plain text that happens to parse as JSON.
        data = {"input": data if isinstance(data, str) else text}
    input_text = data.get("input") if data.get("input") is not None else data.get("message")
    if not isinstance(input_text, str) or not input_text.strip():
        raise ValueError('missing or empty "input" field')
    return data, input_text


@app.invoke_handler
async def handle_invoke(request: Request) -> Response:
    try:
        data, input_text = await _read_invocation(request)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "message": (
                    'Request body must be plain text or a JSON object with a non-empty "input" string, '
                    'e.g. {"input": "What can you help me with?"}'
                ),
            },
        )
    # "new_conversation": true (sent per row by evals/run_evals.py) starts a fresh
    # conversation even when the caller reuses one agent_session_id: the
    # Foundry eval sent all rows with one session id in the 2026-11-07 live run.
    # The platform forwards invocations bodies untouched, so this never affects
    # sandbox routing, and the caller's ongoing session is left as it was.
    session_key = "" if data.get("new_conversation") is True else _session_key(request)
    # The toolbox call id is per request: a toolbox turn always uses a fresh
    # session so no session keeps a previous request's caller context.
    call_id = str(getattr(request.state, "call_id", "") or "")
    if _toolbox_enabled():
        session_key = ""
    if data.get("stream") is False:
        return StreamingResponse(
            _final_answer_response(input_text, session_key, call_id),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    return StreamingResponse(
        _stream_response(request.state.invocation_id, input_text, session_key, call_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# Voice Live Bridge Protocol 1.0 (opt-in Foundry Hosted Voice front-end)
# ---------------------------------------------------------------------------
# https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent
# The managed `kind: voice` wrapper (Voice Live) owns microphone audio, speech
# recognition, turn detection, synthesis and playback interruption. It sends
# finalised caller text here as `user.message`; the reply is the final answer
# of one turn of the SAME Copilot session logic as /invocations (tools, MCP,
# skills and any governance stay in this text agent).
#
# * One voice call (one Bridge WebSocket) = one Copilot conversation, keyed by
#   a per-call id; never the container-wide FOUNDRY_AGENT_SESSION_ID. The call
#   transcript is deleted at session.end or disconnect. A reconnect on a new
#   WebSocket starts a new conversation.
# * Only the final assistant message is spoken (intermediate "Let me check"
#   messages and tool events are not), as response.output_text.done, with
#   markdown markup removed (the agent's instructions are written for text).
# * Voice Live fails a response with `response_timeout:first_output` when no
#   output arrives before the deadlines it announces in session.start
#   (first_output_ms / idle_ms; 30 s each in the 2026-11-10 live run). A turn
#   with MCP tool calls easily takes longer, so while the turn runs the
#   container speaks a short hold message (THREADLIGHT_VOICE_HOLD_TEXT) as its
#   own output item before each deadline, then the final answer.
# * Turns in a call run in arrival order. user.barge_in, response.cancelled
#   and response.timeout cancel the active turn and abort the Copilot turn; no
#   further output is sent for that response. heard_text is not written back:
#   the Copilot transcript keeps the full answer.

_VOICE_MAX_INPUT_CHARS = 16_000
_VOICE_MAX_PENDING_TURNS = 4
_VOICE_ABORT_WAIT_SECONDS = 5
_VOICE_HOLD_SECONDS = float(os.environ.get("THREADLIGHT_VOICE_HOLD_SECONDS", "") or "4")
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
# Speech recognition drops separators from spoken identifiers ("RMA2026104418" for a spoken
# RMA-2026-104418, live voice eval 2026-11-11), and long spoken answers cost audio time.
_VOICE_NOTE = ("Host note: this is a spoken call; the caller's words come from speech recognition. "
               "Answer in the same language as the caller, every word including labels and decision names "
               "(say \"approvare il rimborso\", not \"approve_refund\", to an Italian caller). "
               "Identifiers and numbers may have lost their separators or spacing (ABC-2026-0042 heard as "
               "ABC20260042 or ABC 2026 0042): restore the format your tools expect before calling them, and "
               "if a lookup still finds nothing, ask the caller to spell the identifier. Keep the spoken answer "
               "short: the outcome, the main reason and the next step, without reading long identifiers aloud.")


def _voice_prompt(utterance: str) -> str:
    return _VOICE_NOTE + "\n\nCaller: " + utterance


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|`)")
_MD_LINE_PREFIX = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|>[ \t]?)", re.M)


def _speakable(text: str) -> str:
    """Drop markdown markup (links, emphasis, code ticks, headings, bullets) before speech; keep the words."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_EMPHASIS.sub("", text)
    text = _MD_LINE_PREFIX.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _voice_hold_schedule(*, first_output_ms: int, idle_ms: int, hold_seconds: float) -> tuple[float, float]:
    """(first hold delay, repeat interval) in seconds, inside Voice Live's announced deadlines."""
    first = min(hold_seconds, first_output_ms / 2000)
    idle = idle_ms / 1000
    repeat = min(20.0, idle - 10 if idle >= 20 else idle / 2)
    return first, repeat


class _VoiceCall:
    def __init__(self, bridge_session) -> None:
        self.bridge_session = bridge_session  # held so id() is never reused while registered
        self.key = f"voice-{uuid.uuid4().hex}"
        self.lock = asyncio.Lock()
        self.tasks: dict[str, asyncio.Task] = {}
        self.current_item: str | None = None
        self.current_response: str | None = None
        self.hold_first, self.hold_repeat = _VOICE_HOLD_SECONDS, 20.0


_voice_calls: dict[int, _VoiceCall] = {}
_voice_cleanups: set[asyncio.Task] = set()


async def _final_answer(session, input_text: str) -> str:
    """Run one Copilot turn and return its final assistant message."""
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event):
        if event.type == SessionEventType.SESSION_IDLE:
            queue.put_nowait(None)
        elif event.type == SessionEventType.SESSION_ERROR:
            queue.put_nowait(RuntimeError(getattr(event.data, "message", "error")))
        elif getattr(event.type, "value", "") == "assistant.message":
            queue.put_nowait(getattr(getattr(event, "data", None), "content", None) or "")

    unsubscribe = session.on(on_event)
    final = ""
    try:
        await session.send(input_text)
        while True:
            item = await queue.get()
            if item is None:
                return final
            if isinstance(item, Exception):
                raise item
            if item.strip():
                final = item
    finally:
        unsubscribe()


async def _abort_turn(session) -> None:
    """Abort an interrupted Copilot turn and wait for it to settle (best-effort)."""
    abort = getattr(session, "abort", None)
    if abort is None:
        return
    idle = asyncio.Event()

    def on_event(event):
        # github-copilot-sdk 1.0.x emits `abort`, then the aborted turn's `session.idle`.
        # Wait for that idle: if it arrived after we unsubscribed, it would end the NEXT turn.
        if event.type == SessionEventType.SESSION_IDLE:
            idle.set()

    unsubscribe = session.on(on_event)
    try:
        await abort()
        await asyncio.wait_for(idle.wait(), timeout=_VOICE_ABORT_WAIT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - an interrupted turn must not fail the call
        logger.warning("Copilot turn abort did not settle: %s", type(exc).__name__)
    finally:
        unsubscribe()


async def _end_conversation(key: str) -> None:
    """Drop a finished call's Copilot session from the cache and delete its transcript."""
    async with _sessions_lock:
        lock = _session_locks.get(key)
    if lock is not None:
        await lock.acquire()
    try:
        async with _sessions_lock:
            entry = _sessions.pop(key, None)
            _session_locks.pop(key, None)
            _dormant.pop(key, None)
    finally:
        if lock is not None:
            lock.release()
    if entry is not None:
        await _close_session(entry[0], delete=True)
    else:
        await _delete_transcript(key)


async def _voice_turn(bridge_session, call: _VoiceCall, item_id: str, input_text: str) -> None:
    try:
        async with call.lock:
            response_id = new_response_id()
            call.current_item, call.current_response = item_id, response_id
            await bridge_session.send(ResponseCreated(response_id=response_id, in_reply_to=(item_id,)))
            try:
                async with _conversation(call.key) as copilot_session:
                    work = asyncio.ensure_future(_final_answer(copilot_session, _voice_prompt(input_text)))
                    try:
                        wait = call.hold_first
                        while True:
                            done, _ = await asyncio.wait({work}, timeout=wait)
                            if done:
                                break
                            await bridge_session.send(ResponseOutputTextDone(
                                response_id=response_id, item_id=new_item_id(), text=_voice_hold_text(input_text)))
                            wait = call.hold_repeat
                        answer = work.result()
                    except asyncio.CancelledError:
                        work.cancel()

                        async def settle() -> None:
                            await asyncio.gather(work, return_exceptions=True)
                            await _abort_turn(copilot_session)

                        # A second cancel (barge-in then response.cancelled) must not cut the
                        # abort short: the next turn would then see this turn's late idle.
                        cleanup = asyncio.ensure_future(settle())
                        while not cleanup.done():
                            try:
                                await asyncio.shield(cleanup)
                            except asyncio.CancelledError:
                                continue
                        raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported to Voice Live as a response error
                logger.warning("Voice turn failed: %s", type(exc).__name__)
                await bridge_session.send(AgentError(
                    code="generation_failed", message="The agent could not answer this turn.",
                    response_id=response_id))
                return
            if not answer.strip():
                await bridge_session.send(AgentError(
                    code="empty_answer", message="The agent returned no answer.", response_id=response_id))
                return
            await bridge_session.send(ResponseOutputTextDone(
                response_id=response_id, item_id=new_item_id(), text=_speakable(answer)))
            await bridge_session.send(ResponseDone(response_id=response_id))
    except asyncio.CancelledError:
        logger.info("Voice turn cancelled")
    except Exception as exc:  # noqa: BLE001 - transport closed while sending
        logger.warning("Voice turn could not be delivered: %s", type(exc).__name__)
    finally:
        if call.current_item == item_id:
            call.current_item = call.current_response = None
        call.tasks.pop(item_id, None)


def _cancel_response(call: _VoiceCall, response_id: str | None, item_ids=()) -> None:
    for item_id, task in list(call.tasks.items()):
        if task.cancelling():
            continue
        if item_id in (item_ids or ()) or (response_id and call.current_item == item_id
                                           and call.current_response == response_id):
            task.cancel()


def _finish_call(bridge_session) -> None:
    """Cancel a call's turns and delete its conversation once they settle."""
    call = _voice_calls.pop(id(bridge_session), None)
    if call is None:
        return
    tasks = list(call.tasks.values())
    for task in tasks:
        task.cancel()

    async def cleanup() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)
        await _end_conversation(call.key)

    task = asyncio.get_running_loop().create_task(cleanup())
    _voice_cleanups.add(task)
    task.add_done_callback(_voice_cleanups.discard)


if VOICE_BRIDGE:

    @app.on_session_start
    async def _voice_session_start(bridge_session, event) -> None:
        if id(bridge_session) in _voice_calls:
            logger.warning("Ignoring duplicate session.start")
            return
        if event.protocol_version != "1.0":
            await bridge_session.send(SessionRejected(code="protocol_mismatch", retriable=False))
            return
        try:
            await _ensure_client()
        except Exception as exc:  # noqa: BLE001
            logger.error("Voice session start failed: %s", type(exc).__name__)
            await bridge_session.send(SessionRejected(code="startup_failed", retriable=True))
            return
        call = _VoiceCall(bridge_session)
        timeouts = getattr(event, "response_timeouts", None)
        if timeouts is not None:
            call.hold_first, call.hold_repeat = _voice_hold_schedule(
                first_output_ms=timeouts.first_output_ms, idle_ms=timeouts.idle_ms,
                hold_seconds=_VOICE_HOLD_SECONDS)
        _voice_calls[id(bridge_session)] = call
        await bridge_session.send(SessionReady())

    @app.on_user_message
    async def _voice_user_message(bridge_session, event) -> None:
        call = _voice_calls.get(id(bridge_session))
        if call is None:
            logger.warning("user.message before session.ready; ignored")
            return
        text = "".join(getattr(part, "text", "") or "" for part in event.content).strip()
        reason = None
        if not text:
            reason = "empty_input"
        elif len(text) > _VOICE_MAX_INPUT_CHARS:
            reason = "input_too_large"
        elif len(call.tasks) >= _VOICE_MAX_PENDING_TURNS:
            reason = "capacity_exceeded"
        if reason:
            await bridge_session.send(ResponseNone(in_reply_to=(event.item_id,), reason=reason))
            return
        call.tasks[event.item_id] = asyncio.get_running_loop().create_task(
            _voice_turn(bridge_session, call, event.item_id, text))

    @app.on_user_barge_in
    async def _voice_barge_in(bridge_session, event) -> None:
        call = _voice_calls.get(id(bridge_session))
        if call is not None:
            _cancel_response(call, event.response_id)

    @app.on_response_cancelled
    async def _voice_response_cancelled(bridge_session, event) -> None:
        call = _voice_calls.get(id(bridge_session))
        if call is not None:
            _cancel_response(call, event.response_id)

    @app.on_response_timeout
    async def _voice_response_timeout(bridge_session, event) -> None:
        call = _voice_calls.get(id(bridge_session))
        if call is not None:
            _cancel_response(call, event.response_id, event.item_ids or ())

    @app.on_session_end
    async def _voice_session_end(bridge_session, event) -> None:
        _finish_call(bridge_session)

    @app.on_connection_terminating
    def _voice_connection_terminating(bridge_session) -> None:
        _finish_call(bridge_session)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    has_token = bool(os.environ.get("GITHUB_TOKEN"))
    has_byok = _init_byok()
    if not has_token and not has_byok:
        sys.exit(
            "Error: Set GITHUB_TOKEN (Copilot model) or "
            "FOUNDRY_PROJECT_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT_NAME "
            "(BYOK Foundry model)"
        )
    logger.info("GHCP SDK + Invocations starting (BYOK=%s, GITHUB_TOKEN=%s)", has_byok, has_token)
    app.run()
