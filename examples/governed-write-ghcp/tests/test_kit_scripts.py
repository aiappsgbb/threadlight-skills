"""Offline checks of the governed-write kit's operator scripts (stdlib only, no Azure)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

EXAMPLE = Path(__file__).resolve().parents[1]
CATALOG = EXAMPLE.parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(f"kit_{name}", EXAMPLE / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PENDING = {
    "status": "pending_approval",
    "operation_id": "a" * 64,
    "approval_intent": {"expires_at": "2026-11-10T16:25:07+00:00"},
    "proposed_arguments": {"case_id": "RMA-W05-C", "decision": "escalate_to_supervisor",
                           "expected_etag": "\"etag\"", "reason": "Amount is 750 EUR."},
    "review_context": {"action": "returns_apply_decision", "scope": "returns"},
}
REVIEW = {"control_plane_url": "https://control.example", "scope": "api://11111111-1111-1111-1111-111111111111/Governance.Approve",
          "tenant": "22222222-2222-2222-2222-222222222222", "client_id": "33333333-3333-3333-3333-333333333333"}


def test_review_queue_builds_the_case_list_from_real_pending_records(tmp_path):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps(PENDING))
    out = tmp_path / "queue"
    script("review_queue").build([pending], out, review=REVIEW)
    shape = CATALOG / "skills/threadlight-workspace-ui/references/shapes/case-list"
    for name in ("index.html", "workspace.js", "workspace.css"):
        assert (out / name).read_bytes() == (shape / name).read_bytes()
    doc = json.loads((out / "data/cases.json").read_text())
    assert doc["schema"] == "threadlight-workspace-cases/v1"
    assert doc["config"]["writeback_url"] is None
    assert doc["config"]["review_command"] == {**REVIEW, "role": "Approver"}
    [case] = doc["cases"]
    assert case["id"] == "RMA-W05-C" and case["status"] == "pending_approval"
    assert case["pending"] == PENDING
    # No invented facts: the amount and recommendation are not in the pending record.
    assert case["amount"] is None and case["recommendation"] is None
    # The reviewer runs the command from the served folder; the exact record sits beside it.
    assert json.loads((out / case["pending_file"]).read_text()) == PENDING
    assert doc["actions"] == []


def test_review_queue_refuses_non_pending_or_overwrite(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**PENDING, "status": "completed"}))
    with pytest.raises(ValueError, match="not_a_pending_approval_record"):
        script("review_queue").build([bad], tmp_path / "q1", review=REVIEW)
    (tmp_path / "q2").mkdir()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(PENDING))
    with pytest.raises(FileExistsError):
        script("review_queue").build([good], tmp_path / "q2", review=REVIEW)


def test_scripts_parse():
    for path in sorted((EXAMPLE / "scripts").glob("*.py")):
        subprocess.run([sys.executable, "-m", "py_compile", str(path)], check=True)


class FakeAz:
    """Records az calls; update fails transiently once, replicas start after one poll."""
    def __init__(self, fail_updates=1, cold_polls=1):
        self.calls, self.fail_updates, self.cold_polls = [], fail_updates, {}
        self.cold = cold_polls

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("containerapp", "update"):
            if self.fail_updates:
                self.fail_updates -= 1
                raise subprocess.CalledProcessError(1, ["az", *args], stderr="ContainerAppOperationInProgress")
            return ""
        if args[:3] == ("containerapp", "replica", "list"):
            app = args[args.index("-n") + 1]
            seen = self.cold_polls.get(app, 0)
            self.cold_polls[app] = seen + 1
            state = "Running" if seen >= self.cold else "NotRunning"
            return json.dumps([{"properties": {"runningState": state}}] if seen else [])
        raise AssertionError(args)


def test_warm_retries_transient_updates_and_waits_for_running_replicas(monkeypatch):
    capacity = script("demo_capacity")
    fake = FakeAz()
    monkeypatch.setattr(capacity, "az", fake)
    monkeypatch.setattr(capacity.time, "sleep", lambda seconds: None)
    capacity.set_replicas("rg", ["control", "mcp"], 1, attempts=3)
    capacity.wait_running("rg", ["control", "mcp"], deadline_seconds=60)
    updates = [c for c in fake.calls if c[:2] == ("containerapp", "update")]
    assert len(updates) == 3  # one transient failure retried, then both apps
    # Every app, including the pilot MCP server, was observed with a Running replica.
    assert fake.cold_polls == {"control": 2, "mcp": 2}


def test_warm_gives_up_with_a_named_app(monkeypatch):
    capacity = script("demo_capacity")
    fake = FakeAz(fail_updates=5)
    monkeypatch.setattr(capacity, "az", fake)
    monkeypatch.setattr(capacity.time, "sleep", lambda seconds: None)
    with pytest.raises(SystemExit, match="control"):
        capacity.set_replicas("rg", ["control"], 1, attempts=3)
    never = FakeAz(fail_updates=0, cold_polls=10**6)
    monkeypatch.setattr(capacity, "az", never)
    clock = iter(range(0, 10**6, 30))
    monkeypatch.setattr(capacity.time, "monotonic", lambda: next(clock))
    with pytest.raises(SystemExit, match="mcp"):
        capacity.wait_running("rg", ["mcp"], deadline_seconds=120)


def test_warm_bounds_every_az_call_and_retries_a_hang(monkeypatch):
    capacity = script("demo_capacity")
    seen = []

    def run(command, **kwargs):
        seen.append(kwargs.get("timeout"))
        if len(seen) == 1:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(capacity.subprocess, "run", run)
    monkeypatch.setattr(capacity.time, "sleep", lambda seconds: None)
    capacity.set_replicas("rg", ["business"], 1, attempts=3)
    assert len(seen) == 2 and all(t and t <= 300 for t in seen)
