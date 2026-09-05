"""Actual planner/executor regressions; local proof fixtures are not attestations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_threadlight_auto_orchestrator import orch, REPO
from skills._shared import governance_readiness as readiness
from skills._shared.tests.governance_consumer_fixtures import (
    contract, inventory, live_fixture, seed_inventory, write,
)


def artifacts(root):
    for name, text in {
        "infra/main.bicep": "// fixture\n", "azure.yaml": "name: fixture\n",
        ".azure/demo/.env": "AGENT_FQDN=local.example\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def governed(root, monkeypatch):
    sys.path.insert(0, str(REPO / "skills/threadlight-production-ready/tests"))
    from test_governed_actions_manifest import make_committed_target, _golden
    root = make_committed_target(root, _golden())
    clock = [datetime.now(timezone.utc) - timedelta(seconds=45)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(orch, "datetime", Clock)
    value, document, current, _ = live_fixture(now=clock[0])
    seed_inventory(root, document)
    artifacts(root)
    (root / ".azure/demo/.env").unlink()
    write(root, "specs/manifest.json", {**document, "deployment_manifest": {"resource_group": "rg-staging"}})
    monkeypatch.setattr(readiness, "current_context", lambda _: current)
    for name in ("preflight", "design", "safe_check", "cost_projection", "invoke", "evals", "redteam"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "independent fixture"))
    assert orch.record_governed_actions_gate(root)
    def fresh():
        clock[0] += timedelta(seconds=5)
        proof, _, _, _ = live_fixture(now=clock[0], target=current["expected_target"])
        proof["policy_bundle"] = value["policy_bundle"]
        proof["collection_evidence"]["verified_policies"] = deepcopy(value["collection_evidence"]["verified_policies"])
        write(root, ".threadlight/governance-live.json", {"governance_manifest": proof, "governance_gaps": []})
    return root, clock, fresh


@pytest.mark.parametrize("change", ["outputs", "contract", "policy", "source", "scope"])
def test_execute_binds_authorized_inputs_not_deploy_outputs(tmp_path, monkeypatch, change):
    root, clock, fresh = governed(tmp_path, monkeypatch)
    # Source and policy inputs exist before the gate and must remain immutable.
    for name in ("src/agent/governance_application.py", "policies/safe.rego"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# approved\n")
    assert orch.record_governed_actions_gate(root)
    calls = []
    def worker(stage):
        calls.append(stage)
        clock[0] += timedelta(seconds=5)
        if stage == "deploy":
            artifacts(root)
            parent = json.loads((root / "specs/manifest.json").read_text())
            parent["deployment_manifest"].update(
                agent_fqdn="local.example", agent_version="1", image_digest="sha256:" + "a" * 64)
            if change == "scope":
                parent["deployment_manifest"]["resource_group"] = "other"
            write(root, "specs/manifest.json", parent)
            if change == "contract":
                parent["tools"][0]["safe_principles"] = ["transparency"]
                write(root, "specs/manifest.json", parent)
            if change in ("policy", "source"):
                path = "policies/safe.rego" if change == "policy" else "src/agent/governance_application.py"
                (root / path).write_text("# changed after authorization\n")
        elif stage == "governance_probe":
            fresh()
        elif stage == "governed_actions_gate":
            return 1
        else:
            pytest.fail("unexpected worker " + stage)
        return 0
    result = orch.execute(root, worker)
    assert result["status"] == ("complete" if change == "outputs" else "blocked"), result
    if change == "outputs":
        assert calls == ["deploy", "governance_probe"]
        assert readiness.assess(root)["live"]
        state = json.loads((root / orch.GOVERNANCE_EXECUTION_STATE).read_text())
        gate = json.loads((root / orch.GOVERNANCE_GATE_STATE).read_text())
        assert state["deploy"]["authorized_fingerprint"] == gate["fingerprint"]
    else:
        assert "governance_probe" not in calls


@pytest.mark.parametrize("status", ["started", "failed", "malformed"])
def test_interrupted_existing_fqdn_forces_actual_retry_before_probe(tmp_path, monkeypatch, status):
    root, clock, fresh = governed(tmp_path, monkeypatch)
    artifacts(root)
    assert orch.record_governed_actions_gate(root)
    orch.record_deploy_started(root)
    state = json.loads((root / orch.GOVERNANCE_EXECUTION_STATE).read_text())
    state["deploy"]["status"] = status
    write(root, orch.GOVERNANCE_EXECUTION_STATE, state)
    for fails in (True, False):
        report = orch.decide(root)
        assert report["dependencies"]["governance_probe"] == ["deploy"]
        assert report["next_action"]["stages_to_run"][:2] == ["deploy", "governance_probe"]
        calls = []
        def worker(stage):
            calls.append(stage)
            clock[0] += timedelta(seconds=5)
            if stage == "deploy":
                return int(fails)
            if stage == "governance_probe":
                attempt = json.loads((root / orch.GOVERNANCE_EXECUTION_STATE).read_text())["deploy"]
                assert attempt["status"] == "succeeded" and attempt["finished_at"]
                fresh()
            return 0
        result = orch.execute(root, worker)
        assert calls == (["deploy"] if fails else ["deploy", "governance_probe"])
        assert result["status"] == ("blocked" if fails else "complete")
    assert orch.execute(root, lambda s: pytest.fail("unexpected redeploy " + s))["executed"] == []


@pytest.mark.parametrize("selected", [False, True])
def test_actual_producer_parent_selection_matches_inventory(tmp_path, selected):
    document = contract(selected=selected)
    document["tools"].append(contract(tool_id="other")["tools"][0])
    write(tmp_path, "specs/manifest.json", {**document, "deployment_manifest": {"agent_version": "1"}})
    result = subprocess.run([sys.executable, str(REPO / "skills/threadlight-govern/scripts/govern_check.py"),
                             "--target", str(tmp_path), "--emit", "--json"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert len(value["bindings"]) == 2
    readiness.inventory_matches(value, readiness.load_contract(tmp_path))
    assert orch._check_govern(tmp_path, {}).decision == "skip"
    assert readiness.assess(tmp_path)["live"] is False


def test_no_selection_actual_successful_govern_worker_remains_advisory(tmp_path, monkeypatch):
    for name in orch.STAGES:
        if name != "govern":
            monkeypatch.setitem(orch.STAGE_PROBES, name,
                                lambda w, s, name=name: orch.StageDecision(name, "skip", "independent fixture"))
    calls = []
    def worker(stage):
        calls.append(stage)
        result = subprocess.run([sys.executable, str(REPO / "skills/threadlight-govern/scripts/govern_check.py"),
                                 "--target", str(tmp_path), "--emit", "--json"], capture_output=True, text=True)
        return result.returncode
    result = orch.execute(tmp_path, worker)
    assert result["status"] == "complete", result
    assert calls == ["govern"]
    assert readiness.assess(tmp_path)["live"] is False
    assert readiness.assess(tmp_path)["status"] == "not-verified"


@pytest.mark.parametrize("invalid", ["missing", "legacy"])
def test_selected_success_exit_cannot_replace_inventory(tmp_path, monkeypatch, invalid):
    write(tmp_path, "specs/governance-contract.json", contract(selected=True))
    if invalid == "legacy":
        write(tmp_path, "specs/governance-manifest.json", {
            "schema": "threadlight-govern-manifest/v2", "verdict": "governed"})
    for name in ("preflight", "design"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    result = orch.execute(tmp_path, lambda _: 0)
    assert result["status"] == "blocked" and result["stage"] == "govern"


@pytest.mark.parametrize("source", [
    "src/agent/agent.yaml", "infra/main.parameters.json", "custom-agent/tool.py", "custom/fixture.json",
])
def test_gate_closes_over_actual_host_and_config_inputs(tmp_path, monkeypatch, source):
    root, _, _ = governed(tmp_path, monkeypatch)
    (root / "azure.yaml").write_text(
        "services:\n  agent:\n    project: ./custom-agent\n    host: azure.ai.agent\n")
    write(root, ".threadlight/governance-probe.json", {"fixture_configuration_file": "custom/fixture.json"})
    path = root / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"tools": ["approved"]}')
    assert orch.record_governed_actions_gate(root)
    path.write_text('{"tools": ["unapproved"]}')
    assert orch._check_governed_actions_gate(root, {}).decision == "run"


@pytest.mark.parametrize("field", ["resource_group", "agent_version"])
def test_fresh_gate_can_authorize_retry_after_successful_old_contract(tmp_path, monkeypatch, field):
    root, clock, _ = governed(tmp_path, monkeypatch)
    artifacts(root)
    assert orch.record_governed_actions_gate(root)
    orch.record_deploy_started(root)
    clock[0] += timedelta(seconds=1)
    assert orch.record_deploy_completed(root)
    parent = json.loads((root / "specs/manifest.json").read_text())
    parent["deployment_manifest"][field] = "new-value"
    write(root, "specs/manifest.json", parent)
    assert orch.record_governed_actions_gate(root)
    assert orch._check_governed_actions_gate(root, {}).decision == "skip"
    assert orch._check_deploy(root, {}).decision == "run"


def test_prebuild_image_absent_outputs_are_bound_only_after_success(tmp_path, monkeypatch):
    root, clock, _ = governed(tmp_path, monkeypatch)
    (root / "azure.yaml").write_text("services:\n  agent:\n    project: ./src/agent\n")
    initial = orch._gate_fingerprint(root)
    assert orch.record_governed_actions_gate(root)
    orch.record_deploy_started(root)
    (root / "azure.yaml").write_text(
        "services:\n  agent:\n    project: ./src/agent\n    image: registry.example/agent@sha256:" + "a"*64
        + "\n    env:\n      TL_GOV_IMAGE_DIGEST: sha256:" + "a"*64 + "\n")
    write(root, ".threadlight/governance-deployment.json", {"images": {"agent": "sha256:" + "a"*64}})
    clock[0] += timedelta(seconds=1)
    assert orch._gate_fingerprint(root) == initial
    assert orch.record_deploy_completed(root)
    write(root, ".threadlight/governance-deployment.json", {"images": {"agent": "sha256:" + "b"*64}})
    assert orch._check_governed_actions_gate(root, {}).decision == "run"


def test_deploy_start_cannot_acquire_authorization_after_the_attempt(tmp_path, monkeypatch):
    root, clock, _ = governed(tmp_path, monkeypatch)
    (root / orch.GOVERNANCE_GATE_STATE).unlink()
    orch.record_deploy_started(root)
    clock[0] += timedelta(seconds=1)
    assert not orch.record_deploy_completed(root)
    assert orch.record_governed_actions_gate(root)
    assert not orch.record_deploy_completed(root)
    assert orch.record_deploy_started(root)
    clock[0] += timedelta(seconds=1)
    assert orch.record_deploy_completed(root)


def test_unselected_deploy_can_scaffold_without_governance_gate(tmp_path, monkeypatch):
    for name in orch.STAGES:
        if name not in ("deploy", "govern"):
            monkeypatch.setitem(orch.STAGE_PROBES, name,
                                lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    calls = []
    def worker(stage):
        calls.append(stage)
        if stage == "deploy":
            artifacts(tmp_path)
            (tmp_path / "src").mkdir()
            (tmp_path / "src/agent.py").write_text("# legacy application\n")
        return 0
    result = orch.execute(tmp_path, worker)
    assert result["status"] == "complete", result
    assert calls == ["deploy", "govern"]
    assert not (tmp_path / orch.GOVERNANCE_EXECUTION_STATE).exists()


@pytest.mark.parametrize("text", ["services: [", "services:\n  agent:\n    env: [null]\n"])
def test_malformed_host_inputs_fail_closed_without_planner_crash(tmp_path, monkeypatch, text):
    root, _, _ = governed(tmp_path, monkeypatch)
    (root / "azure.yaml").write_text(text)
    report = orch.decide(root)
    assert next(d for d in report["decisions"] if d["stage"] == "governed_actions_gate")["decision"] == "run"
