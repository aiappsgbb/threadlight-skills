"""Offline crash-recovery harness against the real AgentServer SDK (no Azure).

The AgentServer SDK uses file-backed stores under AGENTSERVER_STATE_ROOT in local
runs, so these runs take the same recovery code path as a hosted agent. They are
local evidence only, never hosted crash-test evidence.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "skills/threadlight-local-test/scripts/crash_recovery.py"
TEMPLATE = ROOT / "skills/threadlight-deploy/references/hosted-agent/maf/references/python/long_running.py"
BACKGROUND_ONLY = Path(__file__).parent / "fixtures/background_only_agent.py"

if os.environ.get("THREADLIGHT_REQUIRE_AGENTSERVER") == "1":
    import azure.ai.agentserver.responses  # noqa: F401  (CI: a missing SDK is a failure, not a skip)
else:
    pytest.importorskip("azure.ai.agentserver.responses")


def run_harness(agent, tmp_path, *extra):
    output = tmp_path / "evidence.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), "--agent", str(agent), "--offline-phases",
         "--output", str(output), *extra],
        capture_output=True, text=True, timeout=170)
    assert output.is_file(), result.stdout + result.stderr
    return result.returncode, json.loads(output.read_text())


def generated_agent(tmp_path):
    """Phase 2 copies the long-running template verbatim as src/agent/container.py."""
    agent = tmp_path / "src/agent/container.py"
    agent.parent.mkdir(parents=True)
    shutil.copy2(TEMPLATE, agent)
    return agent


def test_long_running_agent_resumes_after_sigkill_without_rerunning_checkpointed_phases(tmp_path):
    code, evidence = run_harness(generated_agent(tmp_path), tmp_path)
    assert code == 0, evidence
    assert evidence["evidence"] == "local" and "not hosted" in evidence["label"]
    assert evidence["verdict"] == "recovered"
    assert evidence["killed_after_checkpoint"] == 0
    entries = evidence["entries"]
    assert [e["is_recovery"] for e in entries] == [False, True]
    assert entries[1]["start_phase"] == 1
    # Side-effect counter: a checkpointed phase never runs again; every phase completes once.
    assert evidence["phase_completions"] == {"0": 1, "1": 1, "2": 1, "3": 1}
    assert evidence["phase_starts"]["0"] == 1 and evidence["phase_starts"]["1"] == 2  # interrupted phase re-runs
    # The generated client reconnects from its persisted cursor and applies the snapshot reset.
    assert evidence["client"] == {"status": "completed", "output_items": 4, "unique_item_ids": 4,
                                  "snapshot_resets": 1}
    assert evidence["final_status"] == "completed" and evidence["output_items"] == 4
    replay = evidence["replay"]
    assert replay["response.created"] == 1
    assert replay["response.in_progress"] == 2  # second one is the snapshot reset
    assert replay["response.completed"] == 1
    assert evidence["elapsed_seconds"] < 120


def test_agent_without_the_opt_in_is_red(tmp_path):
    code, evidence = run_harness(BACKGROUND_ONLY, tmp_path, "--no-recovery-wait", "20")
    assert code == 1
    assert evidence["verdict"] == "not_recovered"
    assert evidence["final_status"] in ("in_progress", "failed")
    assert [e["is_recovery"] for e in evidence["entries"]] == [False]


def test_control_run_documents_the_ghost_failure_mode(tmp_path):
    code, evidence = run_harness(BACKGROUND_ONLY, tmp_path, "--expect", "no-recovery",
                                 "--no-recovery-wait", "20")
    assert code == 0
    assert evidence["verdict"] == "not_recovered"
    assert "not crash-resilient" in evidence["label"]
