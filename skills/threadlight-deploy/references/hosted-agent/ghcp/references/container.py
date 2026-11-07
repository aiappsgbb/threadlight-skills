"""Foundry Hosted Agent — GHCP SDK + BYOK via Invocations protocol.

Reference template for deploying a hosted agent using the GitHub Copilot SDK
with BYOK authentication and SSE streaming.

Two auth modes, selected automatically from environment variables:
  * BYOK Foundry model: ``FOUNDRY_PROJECT_ENDPOINT`` (auto-injected by the
    platform) + ``AZURE_AI_MODEL_DEPLOYMENT_NAME`` -> Managed Identity.
    No ``GITHUB_TOKEN`` required.
  * GitHub Copilot model: ``GITHUB_TOKEN`` set -> uses GitHub-hosted models.
    Fallback path; useful for local dev or quickstarts.

Usage:
    Copy this file as ``main.py`` in your agent project root.
    Adapt ``_load_mcp_servers()`` for your MCP server configuration
    (``dict[str, dict]`` keyed by server name).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import sys
import time
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
_MODEL = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "") or os.getenv("MODEL_DEPLOYMENT_NAME", "")


def _init_byok() -> bool:
    """Initialise the BYOK credential. Returns False if BYOK is not configured."""
    global _BYOK_CREDENTIAL, _BYOK_ENDPOINT
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
        token = _BYOK_CREDENTIAL.get_token("https://ai.azure.com/.default")
        logger.info("BYOK verified: %s (model=%s, token expires in %ds)",
                    endpoint, _MODEL, int(token.expires_on - time.time()))
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
            base_url=_BYOK_ENDPOINT,
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
# later ones). A body with ``"new_conversation": true`` always gets a fresh,
# closed-after-use session. Cached sessions are bounded (``COPILOT_MAX_SESSIONS``, LRU) and
# recreated after ``_SESSION_MAX_AGE_SECONDS`` so a BYOK bearer token minted at
# creation never outlives its validity. Turns in one session are serialised.

app = InvocationAgentServerHost()

_client: CopilotClient | None = None
_client_lock = asyncio.Lock()
_skills_dir = str(BASE_DIR / "skills")
_MAX_SESSIONS = max(1, int(os.environ.get("COPILOT_MAX_SESSIONS", "") or "16"))
_SESSION_MAX_AGE_SECONDS = 45 * 60
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


def _session_kwargs() -> dict[str, Any]:
    """Session options; mints a fresh BYOK provider token for every new session."""
    kwargs: dict[str, Any] = {
        "on_permission_request": PermissionHandler.approve_all,
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


async def _close_session(session) -> None:
    close = getattr(session, "disconnect", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail the caller
        logger.warning("Copilot session cleanup failed: %s", type(exc).__name__)


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
        await _close_session(stale)
    try:
        session = await client.resume_session(key, **_session_kwargs())
        logger.info("Resumed Copilot session for Foundry session %s", key)
    except Exception:
        session = await client.create_session(**_session_kwargs())
        logger.info("Created Copilot session for Foundry session %s", key)
    evicted = []
    async with _sessions_lock:
        _sessions[key] = (session, time.time())
        for old_key in list(_sessions):
            if len(_sessions) <= _MAX_SESSIONS:
                break
            lock = _session_locks.get(old_key)
            if old_key == key or (lock is not None and lock.locked()):
                continue  # never close a session in the middle of a turn
            evicted.append(_sessions.pop(old_key)[0])
            _session_locks.pop(old_key, None)
    for old in evicted:
        await _close_session(old)
    return session


@asynccontextmanager
async def _conversation(session_key: str):
    """Yield the Copilot session for one turn of ``session_key`` (fresh when empty)."""
    client = await _ensure_client()
    if not session_key:
        session = await client.create_session(**_session_kwargs())
        try:
            yield session
        finally:
            await _close_session(session)
        return
    async with _sessions_lock:
        lock = _session_locks.setdefault(session_key, asyncio.Lock())
    async with lock:
        yield await _keyed_session(client, session_key)


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


async def _stream_response(invocation_id: str, input_text: str, session_key: str = ""):
    """Stream CopilotClient events as SSE."""
    async with _conversation(session_key) as session:
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(event):
            if event.type == SessionEventType.SESSION_IDLE:
                queue.put_nowait(None)
            elif event.type == SessionEventType.SESSION_ERROR:
                queue.put_nowait(RuntimeError(getattr(event.data, "message", "error")))
            else:
                queue.put_nowait(event)

        unsubscribe = session.on(on_event)
        try:
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


async def _final_answer_response(input_text: str, session_key: str = ""):
    """Non-streaming mode (``"stream": false``): emit only the final answer text.

    Used by Foundry evaluations: the full SSE event stream is 200-300 KB per
    case, which exhausted the judge model's TPM (429) and exceeded the eval
    result size limit (413). Newline heartbeats keep the connection alive
    while the agent works; they are whitespace for the evaluators.
    """
    async with _conversation(session_key) as session:
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


@app.invoke_handler
async def handle_invoke(request: Request) -> Response:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("body is not a JSON object")
        input_text = data.get("input")
        if not isinstance(input_text, str) or not input_text.strip():
            raise ValueError('missing or empty "input" field')
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "message": (
                    'Request body must be a JSON object with a non-empty "input" string, '
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
    if data.get("stream") is False:
        return StreamingResponse(
            _final_answer_response(input_text, session_key),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    return StreamingResponse(
        _stream_response(request.state.invocation_id, input_text, session_key),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


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
