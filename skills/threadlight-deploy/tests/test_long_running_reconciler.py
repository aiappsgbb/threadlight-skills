"""Pilot reconciler for non-terminal background responses, against real local agents.

The local agent restart stands in for the platform waking a stopped session; the
hosted wake is separate live evidence. No Azure resource is used.
"""
import importlib.util
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PY = ROOT / "skills/threadlight-deploy/references/hosted-agent/maf/references/python"
HARNESS = ROOT / "skills/threadlight-local-test/scripts/crash_recovery.py"
BACKGROUND_ONLY = ROOT / "skills/threadlight-local-test/tests/fixtures/background_only_agent.py"

if os.environ.get("THREADLIGHT_REQUIRE_AGENTSERVER") == "1":
    import azure.ai.agentserver.responses  # noqa: F401
else:
    pytest.importorskip("azure.ai.agentserver.responses")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def crashed_job(tmp_path, agent_path):
    """Start a background job, kill the agent after its first checkpoint, track the job."""
    harness, client, reconciler = load(HARNESS, "crash_recovery"), load(PY / "long_running_client.py", "lrc"), \
        load(PY / "reconciler.py", "reconciler")
    agent = harness.Agent(agent_path.resolve(), tmp_path, True, 1.0)
    agent.start()
    state = client.start(agent.base + "/responses", "reconciler test", tmp_path / "client.json")
    assert harness._wait(lambda: any(e["event"] == "checkpointed" for e in agent.lifecycle()), 60)
    agent.kill()  # session stopped mid-run
    return harness, reconciler, agent, state.data


def test_stopped_session_job_completes_with_no_user_action(tmp_path):
    harness, reconciler, agent, job = crashed_job(tmp_path, PY / "long_running.py")
    jobs = tmp_path / "jobs.json"
    try:
        reconciler.track(jobs, endpoint=job["endpoint"], response_id=job["response_id"],
                         agent_session_id=job.get("agent_session_id"), deadline_seconds=90)
        first = reconciler.reconcile_once(jobs)[0]
        assert first["state"] == "unreachable" and not first["final"]
        agent.start()  # locally the test plays the platform: the next request wakes the session
        final = reconciler.run(jobs, interval=0.5, max_seconds=90)[0]
        assert final["state"] == "completed" and final["final"] and final["ui_state"] == "completed"
        assert final["recovering_seen"] is True  # shown as "recovering" while it resumed
        assert final["effects"] == "not-touched"
    finally:
        agent.stop()


def test_ghost_without_the_opt_in_fails_at_its_deadline_with_an_alert(tmp_path):
    harness, reconciler, agent, job = crashed_job(tmp_path, BACKGROUND_ONLY)
    jobs, alerts = tmp_path / "jobs.json", []
    try:
        agent.start()
        reconciler.track(jobs, endpoint=job["endpoint"], response_id=job["response_id"],
                         agent_session_id=job.get("agent_session_id"), deadline_seconds=8)
        started = time.monotonic()
        final = reconciler.run(jobs, interval=0.5, max_seconds=30, alert=alerts.append)[0]
        assert final["state"] == "deadline_exceeded" and final["final"]
        assert final["effects"] == "UNKNOWN"  # reconcile the business effect; never replay it
        assert time.monotonic() - started < 8 + 5
        assert [a["response_id"] for a in alerts] == [job["response_id"]]
    finally:
        agent.stop()


def test_reconciler_only_observes(tmp_path, monkeypatch):
    harness, reconciler, agent, job = crashed_job(tmp_path, PY / "long_running.py")
    methods, original = [], urllib.request.urlopen

    def recording(request, *args, **kwargs):
        methods.append(request.get_method() if isinstance(request, urllib.request.Request) else "GET")
        return original(request, *args, **kwargs)

    jobs = tmp_path / "jobs.json"
    try:
        agent.start()
        reconciler.track(jobs, endpoint=job["endpoint"], response_id=job["response_id"], deadline_seconds=90)
        monkeypatch.setattr(urllib.request, "urlopen", recording)
        assert reconciler.run(jobs, interval=0.5, max_seconds=90)[0]["state"] == "completed"
        assert methods and set(methods) == {"GET"}
        assert sum(1 for e in agent.lifecycle() if e["event"] == "entry") == 2  # no new run was started
        assert json.loads(jobs.read_text())["jobs"][job["response_id"]]["polls"] >= 1
    finally:
        agent.stop()
