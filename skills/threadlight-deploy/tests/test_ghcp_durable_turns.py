"""GHCP durable turns (THREADLIGHT_DURABLE_TURNS=1) on the real Copilot SDK and AgentServer.

The unmodified ``container.py`` runs as its own process group with the installed
Copilot SDK line and azure-ai-agentserver 2.3 / invocations 1.3 (file-backed task
store and replay streams). Only the model HTTP endpoint and the Entra credential
are local stand-ins. A client disconnect and a SIGKILL of the whole process group
during the model call must each end in exactly one terminal result retrievable by
the invocation id, and the user message must reach the Copilot session once.
Local native evidence, not a hosted proof.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = [pytest.mark.governance_runtime,
              pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                                 reason="requires the exact published SDK and native runtime")]

CONTAINER = (Path(__file__).resolve().parents[1] / "references" / "hosted-agent" / "ghcp" / "references"
             / "container.py")
QUESTION = "Plan the warehouse migration in three steps."
LAUNCHER = """
import runpy, sys, types
import azure.identity
class _Credential:
    def __init__(self, *a, **k): pass
    def get_token(self, scope):
        return types.SimpleNamespace(token="local-entra-token", expires_on=4102444800)
azure.identity.DefaultAzureCredential = _Credential
sys.argv = [sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def _port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ModelServer:
    """Local Responses endpoint; holds the first answer until released."""

    def __init__(self):
        from test_ghcp_sdk2_native import _message, _sse
        self.calls, self.release, self.held = [], threading.Event(), threading.Event()
        self._message, self._sse = _message, _sse
        self.port = _port()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("model server did not start")

    def _serve(self):
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        async def responses(request):
            body = await request.json()
            self.calls.append(body)
            if QUESTION in json.dumps(body.get("input")) and not self.held.is_set():
                self.held.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.05)
            output = [self._message("Step 1 inventory, step 2 move, step 3 verify.")]
            if body.get("stream"):
                return Response(self._sse(output), media_type="text/event-stream")
            return JSONResponse({"id": "resp_local", "object": "response", "status": "completed", "output": output})

        app = Starlette(routes=[Route("/{path:path}", responses, methods=["POST"])])
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, access_log=False,
                                      log_level="critical")).run()

    def user_texts(self):
        """User message texts as each model request saw them (history included)."""
        seen = []
        for body in self.calls:
            texts = []
            for item in body.get("input") or []:
                if isinstance(item, dict) and item.get("role") == "user":
                    content = item.get("content")
                    parts = content if isinstance(content, list) else [{"text": content}]
                    texts.append(" ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)))
            seen.append(texts)
        return seen


class Agent:
    def __init__(self, tmp_path, model):
        self.port, self.tmp = _port(), tmp_path
        self.base = f"http://127.0.0.1:{self.port}"
        (tmp_path / "launcher.py").write_text(LAUNCHER)
        self.env = {**os.environ, "THREADLIGHT_DURABLE_TURNS": "1", "PORT": str(self.port),
                    "HOME": str(tmp_path / "home"), "PROJECT_DIR": str(tmp_path),
                    "AGENTSERVER_STATE_ROOT": str(tmp_path / "agentserver"),
                    "FOUNDRY_PROJECT_ENDPOINT": f"http://127.0.0.1:{model.port}",
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": "local-model", "OTEL_SDK_DISABLED": "true"}
        for name in ("GITHUB_TOKEN", "MODEL_GATEWAY_URL", "FOUNDRY_HOSTING_ENVIRONMENT"):
            self.env.pop(name, None)
        self.process = None

    def start(self):
        log = open(self.tmp / "agent.log", "a")
        self.process = subprocess.Popen([sys.executable, str(self.tmp / "launcher.py"), str(CONTAINER)],
                                        env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert self.process.poll() is None, (self.tmp / "agent.log").read_text()[-3000:]
            try:
                urllib.request.urlopen(self.base + "/readiness", timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        raise AssertionError("agent not ready: " + (self.tmp / "agent.log").read_text()[-3000:])

    def kill_group(self):
        os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()

    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)

    def post(self, text, session=""):
        """Start a turn, read only the response headers, then drop the connection."""
        query = f"?agent_session_id={session}" if session else ""
        request = urllib.request.Request(self.base + "/invocations" + query, method="POST",
                                         data=json.dumps({"input": text}).encode(),
                                         headers={"Content-Type": "application/json"})
        response = urllib.request.urlopen(request, timeout=30)
        turn_id = response.headers.get("x-threadlight-turn-id")
        if turn_id:  # the id also arrives as the first frame, before any upstream work
            first = b""
            while b"\n\n" not in first:
                first += response.read(1)
            assert b"threadlight.turn.accepted" in first and turn_id.encode() in first, first
        response.close()
        return turn_id

    def status(self, turn_id):
        try:
            with urllib.request.urlopen(f"{self.base}/invocations/{turn_id}", timeout=10) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, OSError):
            return None

    def wait_terminal(self, turn_id, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            body = self.status(turn_id)
            if body and body["status"] in ("completed", "failed"):
                return body
            time.sleep(0.5)
        raise AssertionError(f"turn {turn_id} not terminal: {self.status(turn_id)}")

    def replay(self, turn_id):
        with urllib.request.urlopen(f"{self.base}/invocations/{turn_id}?stream=true", timeout=30) as response:
            return response.read().decode()


@pytest.fixture
def runtime(tmp_path):
    model = ModelServer()
    agent = Agent(tmp_path, model)
    yield model, agent
    model.release.set()
    agent.stop()


def test_client_disconnect_keeps_one_retrievable_turn(runtime):
    model, agent = runtime
    agent.start()
    turn_id = agent.post(QUESTION, session="conv-disconnect")
    assert turn_id
    assert model.held.wait(60)
    model.release.set()
    body = agent.wait_terminal(turn_id)
    assert body["status"] == "completed" and "step 3 verify" in body["answer"]
    assert body["native_retrievable_id"] is True
    assert agent.replay(turn_id).count("event: done") == 1
    assert sum(bool(texts) and QUESTION in texts[-1] for texts in model.user_texts()) == 1


def _recovered_state(replay):
    for line in replay.splitlines():
        if line.startswith("data:") and "threadlight.turn.recovered" in line:
            return json.loads(line[5:])["data"]["transcript"]
    return None


@pytest.mark.parametrize("warm", [False, True])
def test_crash_mid_turn_recovers_without_sending_the_user_message_twice(runtime, warm):
    model, agent = runtime
    agent.start()
    if warm:  # an earlier completed turn: the Copilot session already exists on disk
        agent.wait_terminal(agent.post("Hello.", session="conv-crash"))
    turn_id = agent.post(QUESTION, session="conv-crash")
    assert model.held.wait(60)
    agent.kill_group()  # container and Copilot runtime die during the model call
    assert agent.status(turn_id) is None
    model.release.set()
    agent.start()  # the startup scan reclaims the turn
    body = agent.wait_terminal(turn_id)
    assert body["status"] == "completed" and body["upstream_sent"] is True
    replay = agent.replay(turn_id)
    assert replay.count("event: done") == 1
    state = _recovered_state(replay)
    assert state in ("missing", "received", "answered")
    # Never twice in one session history; re-sent only when the session never recorded it.
    assert all(sum(QUESTION in text for text in texts) <= 1 for texts in model.user_texts())
    latest = sum(bool(texts) and QUESTION in texts[-1] for texts in model.user_texts())
    assert latest == (2 if state == "missing" else 1), (state, model.user_texts())
    print(f"recovered transcript state (warm={warm}): {state}")


def test_unknown_turn_id_is_404(runtime):
    _, agent = runtime
    agent.start()
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{agent.base}/invocations/does-not-exist", timeout=10)
    assert error.value.code == 404


def test_reference_invoker_records_the_durable_id_and_reconnects_after_a_crash(runtime):
    import importlib.util
    model, agent = runtime
    spec = importlib.util.spec_from_file_location("ghcp_invoke_durable", CONTAINER.with_name("invoke_agent.py"))
    invoker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(invoker)
    agent.start()
    records, result = [], {}

    def run():
        try:
            result["answer"] = invoker.invoke_invocations(
                "", "local", "local", QUESTION, timeout=180, invocations_url=agent.base + "/invocations",
                record=lambda event, data: records.append({"event": event, **data}), reconnect_backoff=0.5)
        except Exception as exc:  # surfaced by the assertion below
            result["error"] = repr(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert model.held.wait(60)
    agent.kill_group()
    model.release.set()
    agent.start()
    thread.join(240)
    assert "step 3 verify" in result.get("answer", ""), (result, records)
    events = [r["event"] for r in records]
    assert events.index("durable-id") < events.index("runtime-completed")
    assert "stream-interrupted" in events and "reconnected" in events
    durable = next(r for r in records if r["event"] == "durable-id")
    assert durable["native_retrievable_id"] is True
    completed = next(r for r in records if r["event"] == "runtime-completed")
    assert completed["native_retrievable_id"] is True and completed["runtime_invocation_id"] == durable[
        "runtime_invocation_id"]
