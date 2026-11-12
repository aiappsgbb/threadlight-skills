"""Offline guards of the approver panel server (no Azure; the decision call is injected)."""
import importlib.util
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

EXAMPLE = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(f"kit_{name}", EXAMPLE / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PENDING = {
    "status": "pending_approval", "operation_id": "b" * 64,
    "approval_intent": {"expires_at": "2026-11-10T16:25:07+00:00"},
    "proposed_arguments": {"case_id": "RMA-W01-C", "decision": "escalate_to_supervisor",
                           "expected_etag": "\"e\"", "reason": "750 EUR"},
    "review_context": {"action": "returns_apply_decision"},
}


@pytest.fixture
def panel(tmp_path):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps(PENDING))
    queue = script("review_queue").build([pending], tmp_path / "queue", approval={"mode": "panel"})
    calls = []

    def decider(record, approved):
        calls.append((record, approved))
        return {"operation_id": record["operation_id"], "approved": approved, "execution": "not-started"}

    server = script("approval_panel").serve(queue, port=0, decider=decider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, queue, calls
    server.shutdown()


def post(server, body, *, token=None, origin=None):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    request = urllib.request.Request(base + "/approve", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Origin": origin or base,
                                              "X-Threadlight-Panel": token or server.panel_token})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_panel_page_carries_the_session_token_and_one_click_mode(panel):
    server, queue, _ = panel
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with urllib.request.urlopen(base + "/data/cases.json") as response:
        doc = json.loads(response.read())
    assert doc["config"]["approval"] == {"mode": "panel", "url": "/approve", "token": server.panel_token}
    assert "review_command" not in doc["config"]
    # The file on disk never holds the session token.
    assert "token" not in json.loads((queue / "data/cases.json").read_text())["config"]["approval"]


def test_panel_decides_the_exact_pending_record_once(panel):
    server, _, calls = panel
    status, body = post(server, {"operation_id": "b" * 64, "approved": True})
    assert status == 200 and body["approved"] is True and body["execution"] == "not-started"
    assert calls == [(PENDING, True)]
    assert post(server, {"operation_id": "b" * 64, "approved": True})[0] == 409
    assert len(calls) == 1


def test_panel_rejects_cross_site_unknown_or_malformed_requests(panel):
    server, _, calls = panel
    assert post(server, {"operation_id": "b" * 64, "approved": True}, origin="https://evil.example")[0] == 403
    assert post(server, {"operation_id": "b" * 64, "approved": True}, token="wrong")[0] == 403
    assert post(server, {"operation_id": "c" * 64, "approved": True})[0] == 404
    assert post(server, {"operation_id": "b" * 64, "approved": "yes"})[0] == 400
    assert calls == []


def test_review_queue_email_mode_names_the_approver_and_hides_the_command(tmp_path):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps(PENDING))
    out = script("review_queue").build([pending], tmp_path / "q",
                                       approval={"mode": "email", "recipient": "approver@contoso.example"})
    config = json.loads((out / "data/cases.json").read_text())["config"]
    assert config["approval"] == {"mode": "email", "recipient": "approver@contoso.example"}
    assert "review_command" not in config
