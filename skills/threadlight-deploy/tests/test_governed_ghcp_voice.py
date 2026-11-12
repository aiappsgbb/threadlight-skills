"""Governed GHCP host as the conversation engine of a Foundry Hosted Voice wrapper.

One pilot, text + voice + the governed write: with THREADLIGHT_VOICE_BRIDGE=1 the
generated governed GHCP container (references/governance/ghcp-container.py) also
serves Voice Live Bridge Protocol 1.0 on /invocations_ws. Every voice turn runs the
SAME governed invocation as the text path (relay, bootstrap gate, gateway), so the
gateway still decides allow / deny / pending and a central ACK precedes any effect.

Voice-specific rules tested here, with the real azure-ai-agentserver-invocations
Voice host and a fake governed turn (no Copilot, gateway or Azure):
* the host, not the model, keeps the call's `governance_pending` record and gives
  the exact proposed arguments + operation id back on the next turn (resume);
* barge-in silences the reply but never cancels a governed turn in flight (that
  would turn a write into an unknown outcome); the unheard result is carried over.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
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

GOVERNED_GHCP = Path(__file__).resolve().parents[1] / "references" / "governance" / "ghcp-container.py"
TS = "2026-11-10T00:00:00Z"
PENDING = {
    "status": "pending_approval",
    "approval_intent": {"opaque": "host-owned intent the Approver's tool validates"},
    "review_context": {"action": "returns_apply_decision"},
    "operation_id": "op-" + "a" * 61,
    "proposed_arguments": {"case_id": "RMA-W05-C", "expected_etag": "\"0a00\"",
                           "decision": "escalate_to_supervisor", "reason": "750 EUR above limit"},
}


def _load(monkeypatch, voice=True):
    if voice:
        monkeypatch.setenv("THREADLIGHT_VOICE_BRIDGE", "1")
    else:
        monkeypatch.delenv("THREADLIGHT_VOICE_BRIDGE", raising=False)
    spec = importlib.util.spec_from_file_location(f"governed_ghcp_voice_{voice}", GOVERNED_GHCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _message(text):
    return b"data: " + json.dumps({"type": "assistant.message", "data": {"content": text}}).encode() + b"\n\n"


class FakeGovernedTurns:
    """Stands in for build_host's governed ``stream(invocation_id, prompt)``."""

    def __init__(self):
        self.prompts: list[str] = []
        self.finished: list[str] = []
        self.release = threading.Event()

    async def __call__(self, invocation_id, prompt):
        self.prompts.append(prompt)
        caller = prompt.rsplit("Caller: ", 1)[-1]
        if "slow" in caller:
            await asyncio.sleep(1.2)
        if "write" in caller:
            yield _message("Let me read the case.")
            while not self.release.is_set():
                await asyncio.sleep(0.02)
            yield _message("Recorded **approve_refund**, audit_id `aud-1`.")
            self.finished.append("write")
        elif "escalate" in caller:
            if "slowly" in caller:
                while not self.release.is_set():
                    await asyncio.sleep(0.02)
            yield b"event: governance_pending\ndata: " + json.dumps(PENDING).encode() + b"\n\n"
            yield _message("Nothing is recorded yet: a human Approver must decide operation " + PENDING["operation_id"])
        elif "fail" in caller:
            yield b'data: {"type":"error","message":"agent_unavailable"}\n\n'
            return
        else:
            yield _message("Answer to: " + caller)
        yield b"event: done\ndata: " + json.dumps({"invocation_id": invocation_id}).encode() + b"\n\n"


@pytest.fixture
def bridge(monkeypatch):
    module = _load(monkeypatch)
    turns = FakeGovernedTurns()
    app = voice_sdk.VoiceAgentServerHost()
    module.attach_voice_bridge(app, turns)
    return module, app, turns


def _start(ws, version="1.0", idle_ms=30000):
    ws.send_json({"type": "session.start", "id": "m_start", "ts": TS, "protocol_version": version,
                  "reconnect": False,
                  "response_timeouts": {"first_output_ms": 30000, "idle_ms": idle_ms, "max_duration_ms": 120000}})
    return ws.receive_json()


def _say(ws, text, n):
    ws.send_json({"type": "user.message", "id": f"m_u{n}", "ts": TS, "item_id": f"in_u{n}",
                  "content": [{"type": "input_text", "text": text}]})


def _turn(ws):
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("response.done", "error", "response.none"):
            return frames


def _end(ws):
    ws.send_json({"type": "session.end", "id": "m_end", "ts": TS, "reason": "done"})


def test_voice_bridge_is_off_unless_opted_in(monkeypatch):
    assert _load(monkeypatch, voice=False).VOICE_BRIDGE is False
    source = GOVERNED_GHCP.read_text(encoding="utf-8")
    assert "VoiceAgentServerHost" in source and "attach_voice_bridge(host, stream)" in source


def test_a_voice_turn_is_one_governed_invocation_and_speaks_only_its_final_answer(bridge):
    _, app, turns = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        assert _start(ws)["type"] == "session.ready"
        _say(ws, "hello", 1)
        frames = _turn(ws)
        _end(ws)
    assert [f["type"] for f in frames] == ["response.created", "response.output_text.done", "response.done"]
    assert frames[1]["text"] == "Answer to: hello"
    assert len(turns.prompts) == 1 and turns.prompts[0].endswith("\n\nCaller: hello")
    voice_note = turns.prompts[0].split("\n\n")[0]
    assert "spoken" in voice_note and "operation_id" in voice_note and "audit_id" in voice_note


def test_protocol_mismatch_is_rejected(bridge):
    _, app, _ = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        rejected = _start(ws, version="2.0")
    assert rejected["type"] == "session.rejected" and rejected["code"] == "protocol_mismatch"


def test_pending_is_kept_by_the_host_and_given_back_exactly_for_the_resume(bridge):
    _, app, turns = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "please escalate RMA-W05-C", 1)
        first = _turn(ws)
        _say(ws, "The supervisor approved it, please complete it", 2)
        _turn(ws)
        _end(ws)
    assert "human Approver" in first[-2]["text"]
    resume = turns.prompts[1]
    assert resume.endswith("Caller: The supervisor approved it, please complete it")
    record = json.loads(resume.split("Exact arguments: ", 1)[1].split("\n", 1)[0])
    assert record == dict(PENDING["proposed_arguments"], governance_operation_id=PENDING["operation_id"])
    assert "host record" in resume.lower() and "not an approval" in resume.lower()
    assert "returns_apply_decision" in resume and "returns_get_case" in resume


def test_pending_records_never_cross_calls(bridge):
    _, app, turns = bridge
    with TestClient(app) as client:
        with client.websocket_connect("/invocations_ws") as ws:
            _start(ws)
            _say(ws, "please escalate RMA-W05-C", 1)
            _turn(ws)
            _end(ws)
        with client.websocket_connect("/invocations_ws") as ws:
            _start(ws)
            _say(ws, "complete it", 1)
            _turn(ws)
            _end(ws)
    assert "Host record" not in turns.prompts[1] and turns.prompts[1].endswith("Caller: complete it")


def test_barge_in_silences_but_never_cancels_a_governed_turn(bridge):
    _, app, turns = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "record the write", 1)
        created = ws.receive_json()
        assert created["type"] == "response.created"
        ws.send_json({"type": "user.barge_in", "id": "m_b", "ts": TS,
                      "response_id": created["response_id"], "heard_text": ""})
        time.sleep(0.2)
        turns.release.set()
        _say(ws, "what happened?", 2)
        frames = _turn(ws)
        _end(ws)
    assert turns.finished == ["write"], "the governed turn must run to completion"
    assert all(f.get("response_id") != created["response_id"] for f in frames)
    nxt = turns.prompts[1]
    assert "interrupted" in nxt and "aud-1" in nxt and nxt.endswith("Caller: what happened?")


def test_a_failed_governed_turn_is_a_response_scoped_error(bridge):
    _, app, _ = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "fail please", 1)
        frames = _turn(ws)
        _end(ws)
    assert [f["type"] for f in frames] == ["response.created", "error"]
    assert frames[1]["response_id"] == frames[0]["response_id"]


def test_slow_governed_turns_speak_hold_messages(monkeypatch):
    monkeypatch.setenv("THREADLIGHT_VOICE_HOLD_SECONDS", "0.3")
    monkeypatch.setenv("THREADLIGHT_VOICE_HOLD_TEXT", "Un momento.")
    module = _load(monkeypatch)
    turns = FakeGovernedTurns()
    app = voice_sdk.VoiceAgentServerHost()
    module.attach_voice_bridge(app, turns)
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws, idle_ms=1000)
        _say(ws, "slow one", 1)
        frames = _turn(ws)
        _end(ws)
    spoken = [f["text"] for f in frames if f["type"] == "response.output_text.done"]
    assert len(spoken) >= 3 and set(spoken[:-1]) == {"Un momento."} and spoken[-1] == "Answer to: slow one"


def test_governed_hold_message_and_note_follow_the_caller_language(monkeypatch):
    monkeypatch.setenv("THREADLIGHT_VOICE_HOLD_SECONDS", "0.3")
    monkeypatch.delenv("THREADLIGHT_VOICE_HOLD_TEXT", raising=False)
    module = _load(monkeypatch)
    turns = FakeGovernedTurns()
    app = voice_sdk.VoiceAgentServerHost()
    module.attach_voice_bridge(app, turns)
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws, idle_ms=1000)
        _say(ws, "per favore valuti il reso, slow", 1)
        frames = _turn(ws)
        _end(ws)
    spoken = [f["text"] for f in frames if f["type"] == "response.output_text.done"]
    assert len(spoken) >= 3 and set(spoken[:-1]) == {"Un momento, sto verificando."}
    assert "same language as the caller" in turns.prompts[0]


# --- generator wiring: governance packaging keeps the opted-in voice front-end -------

GENERATE = GOVERNED_GHCP.parent / "generate.py"


def _generate_module():
    spec = importlib.util.spec_from_file_location("governance_generate_voice", GENERATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _voiced_azure():
    return {"name": "p", "services": {
        "ai-project": {"host": "azure.ai.project"},
        "returns-governed": {
            "host": "azure.ai.agent", "project": "src/agent", "kind": "hosted", "name": "returns-governed",
            "protocols": [{"protocol": "invocations", "version": "2.0.0"},
                          {"protocol": "invocations_ws", "version": "1.0.0"}],
            "metadata": {"voiceLiveCompatible": "true", "bridgeProtocolVersion": "1.0"},
            "environmentVariables": [{"name": "THREADLIGHT_VOICE_BRIDGE", "value": "1"}]},
        "returns-governed-voice": {
            "host": "azure.ai.agent", "kind": "voice", "name": "returns-governed-voice",
            "uses": ["ai-project", "returns-governed"],
            "conversationEngine": {"type": "hosted_agent", "name": "returns-governed"}}}}


CONFIG = {"agent_service": "returns-governed", "control_plane_url": "https://cp.invalid",
          "gateway_url": "https://gw.invalid/mcp"}


def test_governance_compose_keeps_the_voice_bridge_protocol_and_wrapper(tmp_path):
    yaml = pytest.importorskip("yaml")
    generate = _generate_module()
    generate.compose(tmp_path, _voiced_azure(), CONFIG, "github-copilot-sdk")
    services = yaml.safe_load((tmp_path / "azure.yaml").read_text())["services"]
    agent = services["returns-governed"]
    assert {(p["protocol"], p["version"]) for p in agent["protocols"]} == {
        ("invocations", "2.0.0"), ("invocations_ws", "1.0.0")}
    assert agent["metadata"] == {"voiceLiveCompatible": "true", "bridgeProtocolVersion": "1.0"}
    env = {e["name"]: e["value"] for e in agent["environmentVariables"]}
    assert env["THREADLIGHT_VOICE_BRIDGE"] == "1" and "GOVERNED_TOOL_GATEWAY_URL" in env
    assert services["returns-governed-voice"]["conversationEngine"]["name"] == "returns-governed"


def test_governance_compose_without_voice_keeps_one_protocol(tmp_path):
    yaml = pytest.importorskip("yaml")
    azure = _voiced_azure()
    agent = azure["services"]["returns-governed"]
    del azure["services"]["returns-governed-voice"], agent["metadata"]
    agent["environmentVariables"] = []
    _generate_module().compose(tmp_path, azure, CONFIG, "github-copilot-sdk")
    agent = yaml.safe_load((tmp_path / "azure.yaml").read_text())["services"]["returns-governed"]
    assert agent["protocols"] == [{"protocol": "invocations", "version": "2.0.0"}]


def test_voiced_governed_ghcp_agent_pins_the_voice_capable_agentserver(tmp_path):
    generate = _generate_module()
    template = (GENERATE.parent / "pyproject-maf.toml").read_text()
    voiced = generate.ghcp_pyproject(template, voice=True)
    plain = generate.ghcp_pyproject(template, voice=False)
    assert '"azure-ai-agentserver-invocations==1.3.0"' in voiced and '"azure-ai-agentserver-core==2.3.0"' in voiced
    assert '"azure-ai-agentserver-invocations==1.2.0b1"' in plain and '"azure-ai-agentserver-core==2.1.0"' in plain
    # responses 2.2.0b1 requires core < 2.2.0 (live 2026-11-10); the GHCP Invocations host never imports it.
    assert "azure-ai-agentserver-responses" not in voiced
    for text in (voiced, plain):
        assert '"github-copilot-sdk==1.0.1"' in text and "agent-framework" not in text


def test_the_exact_pending_record_reaches_the_caller_as_unspoken_application_data(bridge):
    """Voice analogue of the host-owned `event: governance_pending` SSE frame: the
    Approver's threadlight-review-action needs the exact record (approval_intent)."""
    _, app, _ = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "please escalate RMA-W05-C", 1)
        frames = _turn(ws)
        _end(ws)
    kinds = [f["type"] for f in frames]
    assert kinds == ["response.created", "response.output_data.done", "response.output_text.done", "response.done"]
    data = frames[1]
    assert data["data"] == PENDING and data["response_id"] == frames[0]["response_id"]
    assert "approval_intent" not in frames[2]["text"]


def test_a_barged_in_escalation_still_delivers_its_pending_record_on_the_next_response(bridge):
    _, app, turns = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "please escalate slowly", 1)
        created = ws.receive_json()
        ws.send_json({"type": "user.barge_in", "id": "m_b", "ts": TS,
                      "response_id": created["response_id"], "heard_text": ""})
        time.sleep(0.2)
        turns.release.set()
        _say(ws, "what happened?", 2)
        frames = _turn(ws)
        _end(ws)
    data = [f for f in frames if f["type"] == "response.output_data.done"]
    assert len(data) == 1 and data[0]["data"] == PENDING
    assert data[0]["response_id"] != created["response_id"]


def test_the_host_record_never_claims_the_operation_is_still_pending(bridge):
    _, app, turns = bridge
    with TestClient(app) as client, client.websocket_connect("/invocations_ws") as ws:
        _start(ws)
        _say(ws, "please escalate RMA-W05-C", 1)
        _turn(ws)
        _say(ws, "thanks, anything else?", 2)
        _turn(ws)
        _end(ws)
    note = turns.prompts[1]
    assert "is pending" not in note
    assert "current state is not known to the host" in note and "never repeats an effect" in note
