"""GHCP hosted runtime: one Copilot session per Foundry session, never one per container.

The 2026-11-07 live re-proof (workshop-e2e3) kept ONE Copilot session for the
whole container: every Foundry evaluation row joined the same conversation,
response time grew from 21 s to 135 s and earlier rows could leak into later
answers. These tests load the real ``container.py`` with a fake Copilot client
(no network, no SDK) and drive its invoke handler.
"""
from __future__ import annotations

import asyncio
import importlib.util
import itertools
import json
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("starlette")

CONTAINER = (Path(__file__).resolve().parents[1] / "references" / "hosted-agent" / "ghcp" / "references"
             / "container.py")


class _Type:
    def __init__(self, value):
        self.value = value


IDLE, ERROR, MESSAGE, USAGE = (_Type(v) for v in ("session.idle", "session.error", "assistant.message",
                                                    "assistant.usage"))


class _Event:
    def __init__(self, kind, content=None, serialisable=True):
        self.type = kind
        self.data = types.SimpleNamespace(content=content, message=content)
        self._serialisable = serialisable

    def to_dict(self):
        assert self._serialisable
        return {"type": self.type.value, "data": {"content": self.data.content}}


class FakeSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.history: list[str] = []
        self.handlers = []
        self.closed = False

    def on(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    async def send(self, text):
        assert not self.closed, "send on a disconnected session"
        self.history.append(text)
        await asyncio.sleep(0)
        answer = f"{self.session_id}|turn {len(self.history)}|seen: {' / '.join(self.history)}"
        for event in (_Event(USAGE, serialisable=False), _Event(MESSAGE, answer), _Event(IDLE)):
            for handler in list(self.handlers):
                handler(event)

    async def disconnect(self):
        self.closed = True


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.ids = itertools.count(1)
        self.created: list[FakeSession] = []
        self.started = False

    async def start(self):
        self.started = True

    async def create_session(self, **kwargs):
        session = FakeSession(f"copilot-{next(self.ids)}")
        self.created.append(session)
        return session

    async def resume_session(self, session_id, **kwargs):
        raise RuntimeError("unknown session")


@pytest.fixture
def runtime(monkeypatch):
    host = types.ModuleType("azure.ai.agentserver.invocations")

    class Host:
        def invoke_handler(self, fn):
            return fn

        def run(self):
            raise AssertionError("never served in tests")

    host.InvocationAgentServerHost = Host
    copilot = types.ModuleType("copilot")
    copilot.CopilotClient = FakeClient
    copilot.PermissionHandler = types.SimpleNamespace(approve_all=lambda *a, **k: None)
    copilot.ProviderConfig = lambda **kw: kw
    events = types.ModuleType("copilot.session_events")
    events.SessionEventType = types.SimpleNamespace(SESSION_IDLE=IDLE, SESSION_ERROR=ERROR)
    for name, module in {"azure.ai.agentserver.invocations": host, "copilot": copilot,
                         "copilot.session_events": events}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-not-a-token")
    monkeypatch.setenv("COPILOT_MAX_SESSIONS", "2")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "container-wide-session")
    spec = importlib.util.spec_from_file_location("ghcp_container_under_test", CONTAINER)
    module = importlib.util.module_from_spec(spec)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class _Request:
    def __init__(self, body, session=None, invocation="inv-1"):
        self._body = body
        self.query_params = {"agent_session_id": session} if session else {}
        # The host falls back to the container-wide FOUNDRY_AGENT_SESSION_ID here.
        self.state = types.SimpleNamespace(invocation_id=invocation, session_id=session or "container-wide-session")

    async def json(self):
        if isinstance(self._body, bytes):
            return json.loads(self._body)
        return self._body

    async def body(self):
        return self._body if isinstance(self._body, bytes) else json.dumps(self._body).encode()


def _invoke(module, text, *, session=None, stream=True):
    body = {"input": text} if stream else {"input": text, "stream": False}

    async def run():
        response = await module.handle_invoke(_Request(body, session))
        return b"".join([chunk async for chunk in response.body_iterator]).decode()

    return asyncio.run(run())


def _answers(sse: str) -> list[str]:
    out = []
    for line in sse.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if data.get("type") == "assistant.message":
                out.append(data["data"]["content"])
    return out


def test_different_foundry_sessions_get_isolated_conversations(runtime):
    first = _invoke(runtime, "Triage RMA-1", session="foundry-a", stream=False).strip()
    second = _invoke(runtime, "Triage RMA-2", session="foundry-b", stream=False).strip()
    assert first.endswith("turn 1|seen: Triage RMA-1")
    assert second.endswith("turn 1|seen: Triage RMA-2")
    assert first.split("|")[0] != second.split("|")[0]


def test_same_foundry_session_reuses_its_conversation(runtime):
    _invoke(runtime, "Triage RMA-1", session="foundry-a")
    again = _answers(_invoke(runtime, "And the refund?", session="foundry-a"))
    assert again and again[-1].endswith("turn 2|seen: Triage RMA-1 / And the refund?")


def test_no_session_id_gets_a_fresh_session_per_invocation_and_closes_it(runtime):
    one = _invoke(runtime, "Row 1", stream=False).strip()
    two = _invoke(runtime, "Row 2", stream=False).strip()
    assert one.endswith("turn 1|seen: Row 1") and two.endswith("turn 1|seen: Row 2")
    # The container-wide FOUNDRY_AGENT_SESSION_ID never pools unrelated callers.
    client = runtime._client
    assert len(client.created) == 2 and all(s.closed for s in client.created)


def test_session_cache_is_bounded_and_evicted_sessions_are_closed(runtime):
    for key in ("s1", "s2", "s3"):
        _invoke(runtime, f"hello {key}", session=key, stream=False)
    created = {s.session_id: s for s in runtime._client.created}
    assert list(runtime._sessions) == ["s2", "s3"]
    assert created["copilot-1"].closed and not created["copilot-2"].closed and not created["copilot-3"].closed
    again = _invoke(runtime, "back again", session="s1", stream=False).strip()
    assert again.endswith("turn 1|seen: back again")


def test_streaming_still_degrades_unserialisable_events(runtime):
    sse = _invoke(runtime, "Triage RMA-9", session="foundry-z")
    assert _answers(sse) and "event: done" in sse
    assert "assistant.usage" not in sse


def test_concurrent_turns_in_one_session_are_serialised(runtime):
    async def both():
        async def call(text):
            response = await runtime.handle_invoke(_Request({"input": text, "stream": False}, "foundry-c"))
            return b"".join([c async for c in response.body_iterator]).decode().strip()
        return await asyncio.gather(call("first"), call("second"))

    results = sorted(asyncio.run(both()), key=lambda r: r.split("|")[1])
    assert results[0].split("|")[1] == "turn 1" and results[1].split("|")[1] == "turn 2"


# --- Foundry evaluation: every row is its own conversation --------------------------
# The 2026-11-07 e4 live run: the eval sent all 28 rows with ONE agent_session_id
# (latency 31 s -> 140 s). Learn documents no per-row session control for
# azure_ai_target_completions; the freeform invocations body is forwarded to the
# container untouched, so run_evals sends "new_conversation": true per row.

def _invoke_body(module, body, *, session=None):
    async def run():
        response = await module.handle_invoke(_Request(body, session))
        return b"".join([chunk async for chunk in response.body_iterator]).decode()
    return asyncio.run(run())


def test_new_conversation_isolates_rows_that_share_one_foundry_session(runtime):
    rows = [_invoke_body(runtime, {"input": f"Row {n}", "stream": False, "new_conversation": True},
                         session="eval-shared").strip() for n in (1, 2, 3)]
    assert all(r.endswith(f"turn 1|seen: Row {n}") for n, r in zip((1, 2, 3), rows)), rows
    assert "eval-shared" not in runtime._sessions
    assert all(s.closed for s in runtime._client.created)


def test_new_conversation_does_not_disturb_the_real_multi_turn_session(runtime):
    _invoke_body(runtime, {"input": "Triage RMA-1", "stream": False}, session="user-chat")
    _invoke_body(runtime, {"input": "eval row", "stream": False, "new_conversation": True}, session="user-chat")
    follow = _invoke_body(runtime, {"input": "and the refund?", "stream": False}, session="user-chat").strip()
    assert follow.endswith("turn 2|seen: Triage RMA-1 / and the refund?")


def test_generated_eval_config_starts_a_new_conversation_per_row():
    gen = (Path(__file__).resolve().parents[1] / "scripts" / "generate_only.py").read_text()
    assert '"new_conversation": True' in gen


# --- `azd ai agent invoke <agent> "<text>"` sends a plain-text body -----------------
# e4 live run: the positional form returned HTTP 400 (the container wanted JSON).

@pytest.mark.parametrize("raw", [b"Triage RMA-7 please", "Triage RMA-7 please".encode("utf-8")])
def test_plain_text_body_is_the_input(runtime, raw):
    answers = _answers(_invoke_body(runtime, raw))
    assert answers and answers[-1].endswith("turn 1|seen: Triage RMA-7 please")


@pytest.mark.parametrize("raw", [b'"Triage RMA-7 please"', b'{"message": "Triage RMA-7 please"}'])
def test_json_string_or_message_field_is_the_input(runtime, raw):
    answers = _answers(_invoke_body(runtime, raw))
    assert answers and answers[-1].endswith("turn 1|seen: Triage RMA-7 please")


@pytest.mark.parametrize("raw", [b"", b"   ", b"{}", b'{"input": ""}', b"[1, 2]", b"\xff\xfe"])
def test_empty_or_unusable_body_is_still_rejected(runtime, raw):
    async def run():
        return await runtime.handle_invoke(_Request(raw))
    response = asyncio.run(run())
    assert response.status_code == 400
