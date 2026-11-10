"""GHCP hosted runtime as the conversation engine of a Foundry Hosted Voice wrapper.

Official route (preview): a managed ``kind: voice`` wrapper uses an existing
hosted agent as its conversation engine through Voice Live Bridge Protocol 1.0
on ``/invocations_ws``
(https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent).
Voice Live owns audio, speech recognition, turn-taking and synthesis; the
container only maps text events onto the SAME Copilot conversation logic the
text ``/invocations`` path uses.

These tests load the real ``container.py`` on the real
``azure-ai-agentserver-invocations`` Voice host (typed wire codec) with a fake
Copilot client: no network, no model, no Azure. They prove the local Bridge
handler only, never the managed audio path.
"""
from __future__ import annotations

import asyncio
import importlib.util
import itertools
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytest.importorskip("starlette.testclient")
pytest.importorskip("httpx")
voice_sdk = pytest.importorskip("azure.ai.agentserver.invocations.voice")
from starlette.testclient import TestClient  # noqa: E402

CONTAINER = (Path(__file__).resolve().parents[1] / "references" / "hosted-agent" / "ghcp" / "references"
             / "container.py")
TS = "2026-11-10T00:00:00Z"


class _Type:
    def __init__(self, value):
        self.value = value


IDLE, ERROR, MESSAGE, ABORT = (_Type(v) for v in ("session.idle", "session.error", "assistant.message", "abort"))


class _Event:
    def __init__(self, kind, content=None):
        self.type = kind
        self.data = types.SimpleNamespace(content=content, message=content)

    def to_dict(self):
        return {"type": self.type.value, "data": {"content": self.data.content}}


class FakeSession:
    def __init__(self, client, session_id, history):
        self.client = client
        self.session_id = session_id
        self.history = history
        self.handlers = []
        self.closed = False
        self.aborted = False

    def on(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def _emit(self, *events):
        for event in events:
            for handler in list(self.handlers):
                handler(event)

    async def send(self, text):
        assert not self.closed, "send on a disconnected session"
        self.history.append(text)
        if text == "boom":
            self._emit(_Event(ERROR, "model failed"))
            return
        if text == "slow tool turn":
            await asyncio.sleep(1.2)  # e.g. MCP tool calls before the final answer
        if text == "hang":
            self.client.hanging.set()
            deadline = time.monotonic() + 10
            while not self.aborted and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            return
        await asyncio.sleep(0.5 if text == "next" else 0)  # a model turn is never instant
        answer = f"{self.session_id}|turn {len(self.history)}|seen: {' / '.join(self.history)}"
        self._emit(_Event(MESSAGE, "Let me check."), _Event(MESSAGE, answer), _Event(IDLE))

    async def abort(self):
        self.aborted = True
        self.client.aborts.append(self.session_id)
        # Real github-copilot-sdk order (probed 2026-11-10): `abort`, then `session.idle` of the
        # aborted turn shortly after. A handler that stops at `abort` lets that late idle end the
        # NEXT turn with an empty answer.
        self._emit(_Event(ABORT))
        asyncio.get_running_loop().call_later(0.2, self._emit, _Event(IDLE))

    async def disconnect(self):
        self.closed = True


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.ids = itertools.count(1)
        self.disk: dict[str, list[str]] = {}
        self.created: list[FakeSession] = []
        self.deleted: list[str] = []
        self.aborts: list[str] = []
        self.hanging = threading.Event()

    async def start(self):
        pass

    async def create_session(self, *, session_id=None, **kwargs):
        sid = session_id or f"copilot-{next(self.ids)}"
        assert sid not in self.disk
        self.disk[sid] = []
        session = FakeSession(self, sid, self.disk[sid])
        self.created.append(session)
        return session

    async def get_session_metadata(self, session_id):
        return {"sessionId": session_id} if session_id in self.disk else None

    async def resume_session(self, session_id, **kwargs):
        if session_id not in self.disk:
            raise RuntimeError(f"Session not found: {session_id}")
        return FakeSession(self, session_id, self.disk[session_id])

    async def delete_session(self, session_id):
        self.disk.pop(session_id, None)
        self.deleted.append(session_id)


def _load(monkeypatch, *, voice: bool):
    copilot = types.ModuleType("copilot")
    copilot.CopilotClient = FakeClient
    copilot.PermissionHandler = types.SimpleNamespace(approve_all=lambda *a, **k: None)
    copilot.ProviderConfig = lambda **kw: kw
    events = types.ModuleType("copilot.session_events")
    events.SessionEventType = types.SimpleNamespace(SESSION_IDLE=IDLE, SESSION_ERROR=ERROR)
    monkeypatch.setitem(sys.modules, "copilot", copilot)
    monkeypatch.setitem(sys.modules, "copilot.session_events", events)
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-not-a-token")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "container-wide-session")
    if voice:
        monkeypatch.setenv("THREADLIGHT_VOICE_BRIDGE", "1")
    else:
        monkeypatch.delenv("THREADLIGHT_VOICE_BRIDGE", raising=False)
    spec = importlib.util.spec_from_file_location(f"ghcp_voice_under_test_{voice}", CONTAINER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(monkeypatch):
    return _load(monkeypatch, voice=True)


def _start(ws, version="1.0", idle_ms=30000):
    ws.send_json({"type": "session.start", "id": "m_start", "ts": TS, "protocol_version": version,
                  "reconnect": False,
                  "response_timeouts": {"first_output_ms": 30000, "idle_ms": idle_ms, "max_duration_ms": 120000}})
    return ws.receive_json()


def _say(ws, text, n):
    ws.send_json({"type": "user.message", "id": f"m_user{n}", "ts": TS, "item_id": f"in_user{n}",
                  "content": [{"type": "input_text", "text": text}]})


def _turn(ws):
    """Frames of one response, through its terminal frame."""
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("response.done", "error", "response.none"):
            return frames


def _end(ws):
    ws.send_json({"type": "session.end", "id": "m_end", "ts": TS, "reason": "validation_complete"})


def test_voice_bridge_is_off_unless_opted_in(monkeypatch):
    module = _load(monkeypatch, voice=False)
    assert not isinstance(module.app, voice_sdk.VoiceAgentServerHost)
    assert module.VOICE_BRIDGE is False


def test_session_start_is_ready_and_a_wrong_protocol_is_rejected(runtime):
    assert isinstance(runtime.app, voice_sdk.VoiceAgentServerHost)
    with TestClient(runtime.app) as client:
        with client.websocket_connect("/invocations_ws") as ws:
            assert _start(ws)["type"] == "session.ready"
            _end(ws)
        with client.websocket_connect("/invocations_ws") as ws:
            rejected = _start(ws, version="2.0")
            assert rejected["type"] == "session.rejected"
            assert rejected["code"] == "protocol_mismatch" and rejected["retriable"] is False


def test_user_message_runs_one_copilot_turn_and_speaks_only_the_final_answer(runtime):
    with TestClient(runtime.app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "hello", 1)
        frames = _turn(ws)
        _end(ws)
    assert [f["type"] for f in frames] == ["response.created", "response.output_text.done", "response.done"]
    created, done_text, done = frames
    assert created["in_reply_to"] == ["in_user1"]
    assert created["response_id"] == done_text["response_id"] == done["response_id"]
    assert done_text["text"].endswith("|turn 1|seen: hello")
    assert "Let me check." not in done_text["text"]


def test_one_call_is_one_conversation_and_calls_never_share_one(runtime):
    with TestClient(runtime.app) as client:
        with client.websocket_connect("/invocations_ws") as ws:
            _start(ws)
            _say(ws, "my order is 42", 1)
            _turn(ws)
            _say(ws, "what is my order?", 2)
            second = _turn(ws)[-2]["text"]
            _end(ws)
        with client.websocket_connect("/invocations_ws") as ws:
            _start(ws)
            _say(ws, "who am I?", 1)
            other = _turn(ws)[-2]["text"]
            _end(ws)
    assert second.endswith("|turn 2|seen: my order is 42 / what is my order?")
    assert other.endswith("|turn 1|seen: who am I?")
    assert second.split("|")[0] != other.split("|")[0]
    # The container-wide FOUNDRY_AGENT_SESSION_ID is never the conversation key.
    assert "container-wide-session" not in second + other


def test_session_end_closes_and_deletes_the_call_transcript(runtime):
    with TestClient(runtime.app) as client:
        with client.websocket_connect("/invocations_ws") as ws:
            _start(ws)
            _say(ws, "hello", 1)
            key = _turn(ws)[-2]["text"].split("|")[0]
            _end(ws)
        deadline = time.monotonic() + 5
        while key not in runtime._client.deleted and time.monotonic() < deadline:
            time.sleep(0.01)
    assert key in runtime._client.deleted
    assert key not in runtime._sessions


def test_empty_text_is_declined_with_response_none(runtime):
    with TestClient(runtime.app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "   ", 1)
        frames = _turn(ws)
        _end(ws)
    assert [f["type"] for f in frames] == ["response.none"]
    assert frames[0]["in_reply_to"] == ["in_user1"]


def test_a_failed_turn_reports_a_response_scoped_error_not_a_spoken_answer(runtime):
    with TestClient(runtime.app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "boom", 1)
        frames = _turn(ws)
        _end(ws)
    assert [f["type"] for f in frames] == ["response.created", "error"]
    assert frames[1]["response_id"] == frames[0]["response_id"]
    assert "output_text" not in str(frames)


def test_barge_in_aborts_the_copilot_turn_and_sends_no_further_output(runtime):
    with TestClient(runtime.app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "hang", 1)
        created = ws.receive_json()
        assert created["type"] == "response.created"
        assert runtime._client.hanging.wait(5)
        ws.send_json({"type": "user.barge_in", "id": "m_barge", "ts": TS,
                      "response_id": created["response_id"], "heard_text": ""})
        deadline = time.monotonic() + 5
        while not runtime._client.aborts and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        _say(ws, "next", 2)
        frames = _turn(ws)
        elapsed = time.monotonic() - started
        _end(ws)
    assert runtime._client.aborts
    assert elapsed < 3, f"the next turn waited {elapsed:.1f}s for the aborted turn to settle"
    assert all(f.get("response_id") != created["response_id"] for f in frames)
    assert [f["type"] for f in frames] == ["response.created", "response.output_text.done", "response.done"]
    assert frames[1]["text"].endswith("seen: hang / next")


def test_text_invocations_still_work_on_the_voice_host(runtime):
    with TestClient(runtime.app) as client:
        reply = client.post("/invocations?agent_session_id=s1", json={"input": "hi", "stream": False})
    assert reply.status_code == 200
    assert reply.text.endswith("|turn 1|seen: hi")


def test_a_slow_turn_speaks_hold_messages_before_voice_live_deadlines(monkeypatch):
    # Live 2026-11-10: an MCP tool turn on the deployed agent failed with
    # `response_timeout:first_output` because only the final answer was sent.
    monkeypatch.setenv("THREADLIGHT_VOICE_HOLD_SECONDS", "0.3")
    monkeypatch.setenv("THREADLIGHT_VOICE_HOLD_TEXT", "Un momento, controllo.")
    runtime = _load(monkeypatch, voice=True)
    with TestClient(runtime.app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws, idle_ms=1000)
        _say(ws, "slow tool turn", 1)
        frames = _turn(ws)
        _end(ws)
    kinds = [f["type"] for f in frames]
    assert kinds[0] == "response.created" and kinds[-1] == "response.done"
    spoken = [f for f in frames if f["type"] == "response.output_text.done"]
    holds, final = spoken[:-1], spoken[-1]
    assert len(holds) >= 2, kinds
    assert all(h["text"] == "Un momento, controllo." for h in holds)
    assert final["text"].endswith("|turn 1|seen: slow tool turn")
    assert len({f["item_id"] for f in spoken}) == len(spoken), "each spoken message is its own output item"
    assert {f["response_id"] for f in frames} == {frames[0]["response_id"]}


def test_hold_messages_follow_the_deadlines_announced_in_session_start(runtime):
    assert runtime._voice_hold_schedule(first_output_ms=30000, idle_ms=30000, hold_seconds=4) == (4, 20)
    assert runtime._voice_hold_schedule(first_output_ms=3000, idle_ms=2000, hold_seconds=4) == (1.5, 1.0)


def test_spoken_text_drops_markdown_markup_but_keeps_the_words(runtime):
    # Live 2026-11-10: the text-first answer reached Voice Live as markdown
    # ("Proposed: **approve_refund**  \nDisposition: ...", "[policy](https://...)").
    text = ("Proposed: **approve_refund**  \nDisposition: `restock_a`\n\n## Why\n- within the **30-day** window\n"
            "* see [return policy](https://example.invalid/policy) and __RP-1__")
    assert runtime._speakable(text) == (
        "Proposed: approve_refund\nDisposition: restock_a\n\nWhy\nwithin the 30-day window\n"
        "see return policy and RP-1")
    assert runtime._speakable("approve_refund and restock_a stay as words") == "approve_refund and restock_a stay as words"


def test_a_second_cancel_for_the_same_response_does_not_interrupt_the_abort(runtime):
    # Voice Live can send user.barge_in and then response.cancelled for one response.
    with TestClient(runtime.app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "hang", 1)
        created = ws.receive_json()
        assert runtime._client.hanging.wait(5)
        ws.send_json({"type": "user.barge_in", "id": "m_barge", "ts": TS,
                      "response_id": created["response_id"], "heard_text": ""})
        ws.send_json({"type": "response.cancelled", "id": "m_cancel", "ts": TS,
                      "response_id": created["response_id"], "heard_text": ""})
        _say(ws, "next", 2)
        frames = _turn(ws)
        _end(ws)
    assert [f["type"] for f in frames] == ["response.created", "response.output_text.done", "response.done"]
    assert frames[1]["text"].endswith("seen: hang / next")
