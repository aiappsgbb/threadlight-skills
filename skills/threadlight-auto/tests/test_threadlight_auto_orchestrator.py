#!/usr/bin/env python3
"""Smoke test for `threadlight-auto` orchestrator decisions.

Runs the orchestrator against each fixture under `tests/fixtures/` and asserts
the `next_action.type` matches expectations.

Run locally: `python3 skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
Exit codes: 0 = all green; N = failures.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_orchestrator():
    """Load orchestrator.py as a module (with the sys.modules registration the
    Python 3.14 dataclass + importlib combination requires)."""
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("threadlight_auto_orchestrator", str(ORCH))
    mod = _ilu.module_from_spec(spec)
    sys.modules["threadlight_auto_orchestrator"] = mod
    spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator()


def test_agentops_no_opt_in_skips_even_after_cascade(tmp_path):
    decision = orch._check_agentops(tmp_path, {})
    assert decision.decision == "skip"
    assert decision.reason == "AgentOps not opted in."
    decisions = orch._cascade_invalidations([
        orch.StageDecision("deploy", "run", "fixture"), decision,
    ])
    assert decisions[-1].decision == "skip"


def test_agentops_opt_in_needs_normalized_manifest(tmp_path):
    (tmp_path / "agentops.yaml").write_text("target: demo:1\n")
    decision = orch._check_agentops(tmp_path, {})
    assert decision.decision == "run"
    assert decision.artifacts_missing == ["specs/agentops-manifest.json"]


def test_agentops_stage_is_after_governance_and_does_not_replace_gates(tmp_path):
    from skills._shared.tests.governance_consumer_fixtures import contract, seed_inventory
    seed_inventory(tmp_path, contract(selected=True))
    stages = orch._stages_for(tmp_path)
    assert stages.index("invoke") < stages.index("agentops") < stages.index("evals")
    assert stages.index("govern") < stages.index("governed_actions_gate") < stages.index("deploy")
    assert stages.index("deploy") < stages.index("governance_probe") < stages.index("agentops")


@pytest.mark.parametrize("verdict", ["operational", "partial", "blocked"])
def test_agentops_reuses_valid_nonpassing_observation(tmp_path, monkeypatch, verdict):
    from skills._shared import agentops
    (tmp_path / "agentops.yaml").write_text("target: demo:1\n")
    monkeypatch.setattr(agentops, "load_manifest", lambda repo: {"verdict": verdict})
    decision = orch._check_agentops(tmp_path, {})
    assert decision.decision == "skip"
    assert f"verdict={verdict}" in decision.reason


def test_agentops_stale_or_malformed_manifest_requires_normalization(tmp_path):
    (tmp_path / "agentops.yaml").write_text("target: demo:1\n")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/agentops-manifest.json").write_text('{"verdict":"operational"}')
    assert orch._check_agentops(tmp_path, {}).decision == "run"


def test_agentops_worker_exit_zero_cannot_replace_emitted_evidence(tmp_path, monkeypatch):
    (tmp_path / "agentops.yaml").write_text("target: demo:1\n")
    for name in orch.STAGE_PROBES:
        if name != "agentops":
            monkeypatch.setitem(
                orch.STAGE_PROBES, name,
                lambda workspace, state, name=name: orch.StageDecision(name, "skip", "fixture"),
            )
    result = orch.execute(tmp_path, lambda stage: 0)
    assert result["status"] == "blocked"
    assert result["stage"] == "agentops"
    assert result["executed"] == ["agentops"]


def test_selected_bindings_require_actual_ordered_gates(tmp_path):
    from skills._shared.tests.governance_consumer_fixtures import contract, seed_inventory
    seed_inventory(tmp_path, contract(selected=True))
    report = orch.decide(tmp_path)
    stages = report["stages"]
    assert stages.index("govern") < stages.index("governed_actions_gate") < stages.index("deploy")
    assert stages.index("deploy") < stages.index("governance_probe") < stages.index("invoke")
    assert report["dependencies"]["deploy"] == ["governed_actions_gate"]
    assert report["dependencies"]["governance_probe"] == ["deploy"]


def test_mandatory_gate_failure_stops_actual_execution_and_resume(tmp_path, monkeypatch):
    from skills._shared.tests.governance_consumer_fixtures import contract, seed_inventory
    seed_inventory(tmp_path, contract(selected=True))
    executed = []
    def worker(stage):
        executed.append(stage)
        return 1 if stage == "governed_actions_gate" else 0
    # Independent upstream stages already complete; producer remains offline only.
    for name in ("preflight", "design"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    for _ in range(2):
        result = orch.execute(tmp_path, worker)
        assert result["status"] == "blocked"
        assert "deploy" not in executed and "invoke" not in executed
    assert executed.count("governed_actions_gate") == 2


def test_auto_skill_documents_mandatory_selected_binding_gates():
    text = (REPO / "skills/threadlight-auto/SKILL.md").read_text()
    assert "governed_actions_gate" in text and "governance_probe" in text
    assert "specs/governance-manifest.json" in text


def test_auto_skill_requires_deploy_attempt_records():
    text = (REPO / "skills/threadlight-auto/SKILL.md").read_text()
    assert "--start-stage deploy" in text
    assert "--complete-stage deploy" in text
    assert "governance-execution-state.json" in text


def test_actual_dag_validates_gates_before_deploy_and_invoke(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "skills/threadlight-production-ready/tests"))
    from test_governed_actions_manifest import make_committed_target, _golden
    from skills._shared.tests.governance_consumer_fixtures import live_fixture, seed_inventory, write
    from skills._shared import governance_readiness
    root = make_committed_target(tmp_path, _golden())
    clock = [datetime.now(timezone.utc) - timedelta(seconds=30)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(orch, "datetime", Clock)
    value, document, current, _ = live_fixture()
    seed_inventory(root, document)
    (root / "specs/governance-manifest.json").unlink()
    monkeypatch.setattr(governance_readiness, "current_context", lambda root: current)
    for name in ("preflight", "design"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    executed = []
    def worker(stage):
        executed.append(stage)
        clock[0] += timedelta(seconds=5)
        if stage == "govern":
            seed_inventory(root, document)
        if stage == "governance_probe":
            fresh, _, _, _ = live_fixture(now=clock[0], target=current["expected_target"])
            fresh["policy_bundle"] = value["policy_bundle"]
            fresh["collection_evidence"]["verified_policies"] = value["collection_evidence"]["verified_policies"]
            write(root, ".threadlight/governance-live.json", {
                "governance_manifest": fresh, "governance_gaps": []})
        return 0
    result = orch.execute(root, worker)
    assert result["status"] == "complete", result
    assert executed[:4] == ["govern", "governed_actions_gate", "deploy", "governance_probe"]
    assert executed.index("governance_probe") < executed.index("invoke")
    assert (root / ".threadlight/governance-gate-state.json").exists()
    # The gate checkpoint is not reusable for a different image, contract or policy.
    write(root, ".threadlight/governance-deployment.json", {"images": {"agent": "different"}})
    assert orch._check_governed_actions_gate(root, {}).decision == "run"
    _write_json(tmp_path / "actual-dag.json", {
        "result": result, "executed": executed, "changed_image_invalidated_gate": True,
        "scope": "local stage-worker and strict protocol fixtures; no live Azure execution",
    })


def test_probe_failure_blocks_invoke_even_after_successful_local_assessment(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "skills/threadlight-production-ready/tests"))
    from test_governed_actions_manifest import make_committed_target, _golden
    from skills._shared.tests.governance_consumer_fixtures import contract, seed_inventory
    root = make_committed_target(tmp_path, _golden())
    seed_inventory(root, contract(selected=True))
    for name in ("preflight", "design"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    executed = []
    result = orch.execute(root, lambda stage: executed.append(stage) or 0)
    assert result["status"] == "blocked" and result["stage"] == "governance_probe"
    assert "deploy" in executed and "invoke" not in executed
    assert orch._check_governance_probe(root, {"governance_probe": "complete"}).decision == "run"


def test_new_selection_after_design_cannot_skip_mandatory_gate(tmp_path, monkeypatch):
    from skills._shared.tests.governance_consumer_fixtures import contract, seed_inventory
    monkeypatch.setitem(orch.STAGE_PROBES, "preflight", lambda w, s: orch.StageDecision("preflight", "skip", "fixture"))
    executed = []
    def worker(stage):
        executed.append(stage)
        if stage == "design":
            seed_inventory(tmp_path, contract(selected=True))
        return 0
    assert orch.execute(tmp_path, worker)["status"] == "blocked"
    assert "governed_actions_gate" in executed and "deploy" not in executed


def test_malformed_declaration_cannot_optimize_away_gates(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/manifest.json").write_text("{")
    assert "governed_actions_gate" in orch.decide(tmp_path)["stages"]


@pytest.mark.parametrize("ingress", ["spec", "manifest", "contract"])
@pytest.mark.parametrize("mutation", ["enforc", "enforced", "disabled", "bool", "list", "null", "mode-alias"])
def test_invalid_selected_config_blocks_planner_and_worker(tmp_path, monkeypatch, ingress, mutation):
    from skills._shared.tests.governance_consumer_fixtures import contract, write
    document = contract(selected=True)
    if mutation in ("enforc", "enforced", "bool", "list", "null"):
        document["governance"]["environment_modes"]["preproduction"] = {
            "enforc": "enforc", "enforced": "enforced", "bool": False, "list": [], "null": None,
        }[mutation]
    elif mutation == "disabled":
        document["governance"]["mode"] = "disabled"
    else:
        document["governance_mode"] = document.pop("governance")["mode"]
    if ingress == "spec":
        (tmp_path / "specs").mkdir()
        (tmp_path / "specs/SPEC.md").write_text("```yaml\n" + json.dumps(document) + "\n```\n")
    else:
        write(tmp_path, "specs/" + ("manifest.json" if ingress == "manifest" else "governance-contract.json"), document)
    for name in ("preflight", "design"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    report = orch.decide(tmp_path)
    assert report["next_action"]["type"] == "hard_stop"
    assert report["next_action"]["signature"] == "invalid-governance-configuration"
    assert "governed_actions_gate" in report["stages"]
    assert "governance_probe" in report["stages"]
    calls = []
    result = orch.execute(tmp_path, lambda stage: calls.append(stage) or 0)
    assert result["status"] == "blocked" and calls == []


@pytest.mark.parametrize("text", [
    "governance:\n  mode: selective\n",
    "**Governance mode**: `selective`\n",
    "```yaml\ngovernance: [\n```\n",
    "```yaml\ngovernance: {mode: selective}\n```\n",
])
def test_incomplete_spec_selection_is_invalid_not_legacy(tmp_path, text):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/SPEC.md").write_text(text)
    assert orch.decide(tmp_path)["next_action"].get("signature") == "invalid-governance-configuration"


def test_invalid_parent_json_cannot_be_treated_as_legacy_off(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/manifest.json").write_text('{"governance":')
    assert orch.decide(tmp_path)["next_action"].get("signature") == "invalid-governance-configuration"


def test_spec_only_selection_uses_shared_contract_validator_and_blocks_missing_gate(tmp_path, monkeypatch):
    from skills._shared.tests.governance_consumer_fixtures import contract
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/SPEC.md").write_text("```yaml\n" + json.dumps(contract(selected=True)) + "\n```\n")
    for name in ("preflight", "design"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "fixture"))
    calls = []
    report = orch.decide(tmp_path)
    assert report["stages"] == orch.GOVERNANCE_STAGES
    result = orch.execute(tmp_path, lambda stage: calls.append(stage) or 0)
    assert result["status"] == "blocked" and "deploy" not in calls


@pytest.mark.parametrize("text", ["# Legacy SPEC\n", "```yaml\nframework: github-copilot-sdk\ntools: []\n```\n"])
def test_valid_nongovernance_spec_retains_legacy_pipeline(tmp_path, text):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/SPEC.md").write_text(text)
    report = orch.decide(tmp_path)
    assert report["stages"] == orch.STAGES
    assert report["next_action"]["type"] == "run"


def test_live_probe_resume_invalidates_changed_image_policy_and_manifest(tmp_path, monkeypatch):
    from copy import deepcopy
    from skills._shared.tests.governance_consumer_fixtures import live_fixture, write
    from skills._shared import governance_readiness
    value, document, current, _ = live_fixture()
    write(tmp_path, "specs/governance-manifest.json", value)
    write(tmp_path, "specs/governance-contract.json", document)
    original = deepcopy(current)
    monkeypatch.setattr(governance_readiness, "current_context", lambda root: current)
    assert orch._check_governance_probe(tmp_path, {}).decision == "skip"
    for part, key in (("expected_target", "image_digest"), ("policy_bundle", "digest")):
        current = deepcopy(original)
        current[part][key] = "sha256:" + "b" * 64
        assert orch._check_governance_probe(tmp_path, {"governance_probe": {"status": "complete"}}).decision == "run"
    current = original
    value["coverage"]["tools_total"] = 0
    write(tmp_path, "specs/governance-manifest.json", value)
    assert orch._check_governance_probe(tmp_path, {}).decision == "run"


def test_failed_latest_collection_cannot_reuse_previous_green(tmp_path, monkeypatch):
    from skills._shared.tests.governance_consumer_fixtures import live_fixture, write
    from skills._shared import governance_readiness
    value, document, current, _ = live_fixture()
    write(tmp_path, "specs/governance-manifest.json", value)
    write(tmp_path, "specs/governance-contract.json", document)
    monkeypatch.setattr(governance_readiness, "current_context", lambda root: current)
    assert orch._check_governance_probe(tmp_path, {}).decision == "skip"
    write(tmp_path, ".threadlight/governance-live.json", {
        "governance_gaps": ["producer-timeout"], "governance_probes": []})
    assert orch._check_governance_probe(tmp_path, {}).decision == "run"


@pytest.mark.parametrize("probe_result", ["fresh", "old-timestamps", "worker-failed", "invalidated-after-probe"])
def test_real_redeploy_replan_requires_new_attempt_proof(tmp_path, monkeypatch, probe_result):
    sys.path.insert(0, str(REPO / "skills/threadlight-production-ready/tests"))
    from test_governed_actions_manifest import make_committed_target, _golden
    from skills._shared.tests.governance_consumer_fixtures import live_fixture, write
    from skills._shared import governance_readiness
    root = make_committed_target(tmp_path, _golden())
    clock = [datetime.now(timezone.utc) - timedelta(seconds=30)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(orch, "datetime", Clock)
    value, document, current, _ = live_fixture(now=clock[0])
    write(root, "specs/governance-contract.json", document)
    write(root, "specs/governance-manifest.json", value)
    write(root, ".threadlight/governance-live.json", {"governance_manifest": value, "governance_gaps": []})
    monkeypatch.setattr(governance_readiness, "current_context", lambda root: current)
    assert orch.record_governed_actions_gate(root)
    assert orch._check_governance_probe(root, {}).decision == "skip"
    for name in ("preflight", "design", "safe_check", "cost_projection", "invoke", "evals", "redteam"):
        monkeypatch.setitem(orch.STAGE_PROBES, name,
                            lambda w, s, name=name: orch.StageDecision(name, "skip", "independent fixture"))
    if probe_result == "invalidated-after-probe":
        for name in ("safe_check", "cost_projection", "invoke", "evals", "redteam"):
            monkeypatch.setitem(orch.STAGE_PROBES, name,
                                lambda w, s, name=name: orch.StageDecision(name, "run", "independent fixture"))
    reports = []
    real_decide = orch.decide
    def plan(*args):
        report = real_decide(*args)
        reports.append(report)
        return report
    monkeypatch.setattr(orch, "decide", plan)
    attempted = []
    prior_collection = (root / ".threadlight/governance-live.json").read_bytes()
    # A pre-deploy gate assesses source/infra inputs that already exist.
    for path in ("infra/main.bicep", "azure.yaml"):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("local deployment artifact\n")
    # Select the environment before authorization, without pre-seeding any outputs.
    (root / ".azure/demo").mkdir(parents=True)
    assert not (root / ".azure/demo/.env").exists()
    assert orch.record_governed_actions_gate(root)
    def worker(stage):
        attempted.append(stage)
        clock[0] += timedelta(seconds=5)
        if stage == "deploy":
            for path in ("infra/main.bicep", "azure.yaml"):
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("local deployment artifact\n")
            dest = root / ".azure/demo/.env"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("AGENT_FQDN=local.example\n")
            # Old artifact timestamps cannot stand in for the actual attempt.
            os.utime(root / "specs/governance-manifest.json", (1, 1))
            assert (root / ".threadlight/governance-live.json").read_bytes() == prior_collection
        elif stage == "governance_probe":
            if probe_result == "worker-failed":
                return 1
            fresh, _, _, _ = live_fixture(
                now=clock[0] if probe_result != "old-timestamps" else clock[0] - timedelta(seconds=15),
                target=current["expected_target"])
            fresh["policy_bundle"] = value["policy_bundle"]
            fresh["collection_evidence"]["verified_policies"] = value["collection_evidence"]["verified_policies"]
            write(root, ".threadlight/governance-live.json",
                  {"governance_manifest": fresh, "governance_gaps": []})
            clock[0] += timedelta(seconds=1)
        elif stage == "redteam":
            write(root, ".threadlight/governance-live.json", {"governance_gaps": ["later-collection-failed"]})
        elif stage in ("safe_check", "cost_projection", "invoke", "evals"):
            pass
        else:
            pytest.fail("Unexpected stage: " + stage)
        return 0
    result = orch.execute(root, worker)
    assert attempted == ["deploy", "governance_probe"] + (
        ["safe_check", "cost_projection", "invoke", "evals", "redteam"]
        if probe_result == "invalidated-after-probe" else [])
    assert "governance_probe" in reports[1]["next_action"]["stages_to_run"]
    assert result["status"] == ("complete" if probe_result == "fresh" else "blocked")
    if probe_result == "fresh":
        state_path = root / orch.GOVERNANCE_EXECUTION_STATE
        state = json.loads(state_path.read_text())
        collection = json.loads((root / ".threadlight/governance-live.json").read_text())
        evidence = collection["governance_manifest"]["collection_evidence"]
        assert state["deploy"]["started_at"] <= state["deploy"]["finished_at"] < evidence["started_at"]
        assert state["probe"]["attempt_id"] == state["deploy"]["attempt_id"]
        assert state["probe"]["fingerprint"] == state["deploy"]["fingerprint"]
        assert state["probe"]["collection_sha256"] == orch._sha256(root / ".threadlight/governance-live.json")
        for key in ("attempt_id", "fingerprint", "collection_sha256"):
            write(root, orch.GOVERNANCE_EXECUTION_STATE,
                  {**state, "probe": {**state["probe"], key: "previous-attempt"}})
            assert orch._check_governance_probe(root, {}).decision == "run"
        write(root, orch.GOVERNANCE_EXECUTION_STATE, state)
        assert orch._check_governance_probe(root, {}).decision == "skip"
        assert orch.execute(root, lambda stage: pytest.fail("unexpected resume " + stage))["executed"] == []
        # Same image/version, another successful deployment: a distinct proof is mandatory.
        (root / ".azure/demo/.env").unlink()
        # Resetting recorded azd observations invalidates proof, not declared inputs.
        assert orch._check_governance_probe(root, {}).decision == "run"
        assert orch.record_governed_actions_gate(root)
        prior_collection = (root / ".threadlight/governance-live.json").read_bytes()
        assert orch.execute(root, worker)["status"] == "complete"
        assert attempted == ["deploy", "governance_probe", "deploy", "governance_probe"]
        assert json.loads(state_path.read_text())["deploy"]["attempt_id"] != state["deploy"]["attempt_id"]
    else:
        assert orch._check_governance_probe(root, {}).decision == "run"
        assert "governance_probe" in real_decide(root)["next_action"]["stages_to_run"]
        if probe_result == "old-timestamps":
            assert orch.main(["--workspace", str(root), "--complete-stage", "governance_probe",
                              "--output", "json"]) == 1


def test_cli_records_actual_deploy_attempt_without_changing_cloud_artifacts(tmp_path):
    for flag in ("--start-stage", "--complete-stage"):
        result = subprocess.run([sys.executable, str(ORCH), "--workspace", str(tmp_path),
                                 flag, "deploy", "--output", "json"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / orch.GOVERNANCE_EXECUTION_STATE).read_text())
    assert state["deploy"]["status"] == "succeeded"
    assert state["deploy"]["attempt_id"]
    assert state["deploy"]["started_at"] <= state["deploy"]["finished_at"]
    assert state["probe"] is None
    assert not (tmp_path / "specs/manifest.json").exists()
    assert not (tmp_path / ".threadlight/governance-live.json").exists()


def test_attempt_must_start_before_completion_and_interruption_invalidates(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "skills/threadlight-production-ready/tests"))
    from test_governed_actions_manifest import make_committed_target, _golden
    tmp_path = make_committed_target(tmp_path, _golden())
    from skills._shared.tests.governance_consumer_fixtures import live_fixture, write
    from skills._shared import governance_readiness
    value, document, current, _ = live_fixture()
    write(tmp_path, "specs/governance-contract.json", document)
    write(tmp_path, "specs/governance-manifest.json", value)
    write(tmp_path, ".threadlight/governance-live.json", {"governance_manifest": value, "governance_gaps": []})
    monkeypatch.setattr(governance_readiness, "current_context", lambda root: current)
    assert orch.record_governed_actions_gate(tmp_path)
    assert not orch.record_deploy_completed(tmp_path)
    orch.record_deploy_started(tmp_path)
    assert orch._check_governance_probe(tmp_path, {}).decision == "run"
    assert not orch.record_governance_probe(tmp_path)
    assert orch.record_deploy_completed(tmp_path)
    assert not orch.record_deploy_completed(tmp_path)
    assert not orch.record_governance_probe(tmp_path)


def test_duplicate_spec_governance_cannot_replace_selection_with_off(tmp_path):
    import yaml
    from skills._shared.tests.governance_consumer_fixtures import contract
    (tmp_path / "specs").mkdir()
    block = "governance: {mode: selective}\n" + yaml.safe_dump(contract())
    (tmp_path / "specs/SPEC.md").write_text("```yaml\n" + block + "```\n")
    assert orch.decide(tmp_path)["next_action"].get("signature") == "invalid-governance-configuration"


def _leg_envelope(schema: str, status: str) -> str:
    return json.dumps({
        "schema": schema,
        "tool_version": "0.1.0",
        "generated_at": "2026-08-18T10:00:00Z",
        "freshness": {"valid_for_hours": 24, "source_oldest_at": None},
        "status": status,
        "findings": [],
    })


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_postdeploy_fixture(tmp_path: Path, payload: dict) -> None:
    deployment_manifest = {
        "subscription_id": "sub-1",
        "resource_group": "rg-pilot",
    }
    _write_json(tmp_path / "specs" / "manifest.json", {"deployment_manifest": deployment_manifest})
    merged = {
        "deployment_manifest": deployment_manifest,
        **payload,
    }
    _write_json(tmp_path / "tests" / "postdeploy-manifest.json", merged)


# ---------------------------------------------------------------------------
# Manual-handoff projection (Task 7): the four live legs are advisory only.
# pytest-collected; also exercised from main() below for the standalone runner.
# ---------------------------------------------------------------------------

def test_new_live_legs_are_manual_handoffs_not_auto_stages(tmp_path):
    decision = orch.decide(tmp_path)
    handoffs = decision["manual_handoffs"]
    # Ordered exactly connect -> ground -> loadtest -> upgrade.
    assert [h["skill"] for h in handoffs] == [
        "threadlight-connect",
        "threadlight-ground",
        "threadlight-loadtest",
        "threadlight-upgrade",
    ]
    # An empty workspace has no leg manifests -> every handoff is 'ready'.
    assert all(h["status"] == "ready" for h in handoffs)
    # Each handoff names its skill for a manual, advisory chat invocation.
    for h in handoffs:
        assert h["skill"] in h["next_intent"]
        assert h["manifest"].startswith("specs/")
    # The live legs are NEVER auto-stages.
    assert not {"connect", "ground", "loadtest", "upgrade"}.intersection(decision["stages"])
    assert not {"connect", "ground", "loadtest", "upgrade"}.intersection(orch.STAGES)
    # `stages` echoes the automatic stage runner exactly.
    assert decision["stages"] == list(orch.STAGES)


def test_manual_handoff_status_reflects_validated_envelope(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "connect-manifest.json").write_text(
        _leg_envelope("threadlight-connect-manifest/v1", "complete"), encoding="utf-8")
    (specs / "ground-manifest.json").write_text(
        _leg_envelope("threadlight.ground/v1", "partial"), encoding="utf-8")
    (specs / "load-manifest.json").write_text(
        _leg_envelope("threadlight.load/v1", "aborted"), encoding="utf-8")
    # An unrecognized / malformed manifest must degrade to 'partial', never 'complete'.
    (specs / "upgrade-manifest.json").write_text("{ not valid json", encoding="utf-8")

    decision = orch.decide(tmp_path)
    by_skill = {h["skill"]: h["status"] for h in decision["manual_handoffs"]}
    assert by_skill == {
        "threadlight-connect": "complete",
        "threadlight-ground": "partial",
        "threadlight-loadtest": "aborted",
        "threadlight-upgrade": "partial",
    }


def test_govern_reruns_when_required_capabilities_are_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "govern-manifest.json",
        {
            "schema": "threadlight-govern-manifest/v2",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "governed",
            "capabilities": {
                "policy_artefact_present": {"status": "pass"},
                "policy_schema_valid": {"status": "pass"},
            },
        },
    )

    decision = orch._check_govern(tmp_path, {})

    assert decision.decision == "run"
    assert "legacy capabilities are not evidence" in decision.reason


def test_invalid_envelope_never_reports_complete(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    # A dict with status 'complete' but missing required envelope keys is not a
    # valid envelope -> partial, not complete.
    (specs / "connect-manifest.json").write_text(
        json.dumps({"status": "complete"}), encoding="utf-8")
    decision = orch.decide(tmp_path)
    connect = next(h for h in decision["manual_handoffs"] if h["skill"] == "threadlight-connect")
    assert connect["status"] == "partial"


def test_deploy_requires_a_real_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "# AGENT_FQDN=commented-out.example.com\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "AGENT_FQDN" not in decision.reason or "hasn't completed" in decision.reason


def test_deploy_requires_a_non_empty_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"


def test_deploy_rejects_a_quoted_empty_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        'AGENT_FQDN=""\n',
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"


def test_deploy_rejects_an_inline_comment_after_empty_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=   # placeholder until deployed\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"


def test_deploy_requires_an_unambiguous_single_azd_env(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=threadlight-dev.example.com\n",
        encoding="utf-8",
    )
    (tmp_path / ".azure" / "prod").mkdir(parents=True)
    (tmp_path / ".azure" / "prod" / ".env").write_text(
        "AGENT_FQDN=threadlight-prod.example.com\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "multiple azd envs" in decision.reason


def test_deploy_treats_an_azd_env_without_dot_env_as_incomplete_evidence(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "AGENT_FQDN" in decision.reason


def test_deploy_rejects_a_symlinked_azd_root(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / "shadow-azure" / "dev").mkdir(parents=True)
    (tmp_path / "shadow-azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=shadow.example.com\n",
        encoding="utf-8",
    )
    os.symlink(tmp_path / "shadow-azure", tmp_path / ".azure", target_is_directory=True)

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "symlinked" in decision.reason


def test_deploy_rejects_a_symlinked_azd_env_directory(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure").mkdir(parents=True)
    (tmp_path / "shadow-env").mkdir(parents=True)
    (tmp_path / "shadow-env" / ".env").write_text(
        "AGENT_FQDN=shadow.example.com\n",
        encoding="utf-8",
    )
    os.symlink(tmp_path / "shadow-env", tmp_path / ".azure" / "dev", target_is_directory=True)

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "symlinked" in decision.reason


def test_deploy_rejects_a_broken_symlinked_dot_env(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    os.symlink(tmp_path / "missing.env", tmp_path / ".azure" / "dev" / ".env")

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "symlinked" in decision.reason


def test_deploy_treats_a_regular_file_dot_azure_as_missing_evidence(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure").write_text("not a directory\n", encoding="utf-8")

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "AGENT_FQDN" in decision.reason


def test_live_legs_never_added_to_stage_runner():
    assert set(orch.MANUAL_HANDOFFS) == {
        "threadlight-connect", "threadlight-ground",
        "threadlight-loadtest", "threadlight-upgrade",
    }
    assert "connect" not in orch.STAGES
    assert "ground" not in orch.STAGES
    assert "loadtest" not in orch.STAGES
    assert "upgrade" not in orch.STAGES
    assert orch.STAGE_PROBES.keys() == set(orch.STAGES)


def test_safe_check_requires_green_postdeploy_manifest_even_with_fresh_doc(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "safe-check-post.md").write_text("# green\n", encoding="utf-8")

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "tests/postdeploy-manifest.json" in decision.artifacts_missing


def test_safe_check_non_green_postdeploy_manifest_with_fresh_doc_runs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "safe-check-post.md").write_text("# green\n", encoding="utf-8")
    # Create a postdeploy manifest that reports unresolved gaps
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "postdeploy-manifest.json").write_text(
        json.dumps({"phase": "post-deploy", "gaps": [{"id": "g1", "reason": "issue"}]}),
        encoding="utf-8",
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    # Reason should mention gaps and that we need to re-run the safe-check
    assert "gaps" in decision.reason


def test_safe_check_requires_postdeploy_manifest_to_match_current_deployment_manifest(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "safe-check-post.md").write_text("# green\n", encoding="utf-8")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "manifest.json").write_text(
        json.dumps(
            {
                "deployment_manifest": {
                    "subscription_id": "sub-1",
                    "resource_group": "rg-current",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "postdeploy-manifest.json").write_text(
        json.dumps(
            {
                "phase": "post-deploy",
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "gaps": [],
                "deployment_manifest": {
                    "subscription_id": "sub-1",
                    "resource_group": "rg-old",
                },
            }
        ),
        encoding="utf-8",
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "no longer matches" in decision.reason.lower()
    assert "re-running" in decision.reason.lower()


def test_safe_check_green_postdeploy_manifest_skips_without_doc(tmp_path):
    _write_postdeploy_fixture(tmp_path, {"checked_at": _iso_now(), "phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "skip"
    assert "tests/postdeploy-manifest.json" in decision.artifacts_seen


def test_safe_check_manifest_without_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(tmp_path, {"phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_timezone_less_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-08-06T08:00:00", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_space_separated_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-08-06 08:00:00+00:00", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_future_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2099-01-01T00:00:00Z", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "future" in decision.reason.lower()


def test_safe_check_manifest_with_invalid_rfc3339_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-08-05T24:00:00Z", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_impossible_calendar_date_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-02-30T08:00:00Z", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_lowercase_z_skips(tmp_path):
    checked_at = _iso_now().replace("Z", "z")
    _write_postdeploy_fixture(tmp_path, {"checked_at": checked_at, "phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "skip"
    assert "tests/postdeploy-manifest.json" in decision.artifacts_seen


def test_safe_check_manifest_exactly_24_hours_old_runs(tmp_path):
    checked_at = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write_postdeploy_fixture(tmp_path, {"checked_at": checked_at, "phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert ">= 24 h" in decision.reason


def test_leg_manifest_requires_expected_schema_captured_at_and_known_verdict(tmp_path):
    manifest = tmp_path / "specs" / "evals-manifest.json"
    bad_payloads = (
        {"schema": "threadlight-evals-manifest/v999", "captured_at": _iso_now(), "verdict": "comprehensive"},
        {"schema": "threadlight-evals-manifest/v1", "verdict": "comprehensive"},
        {"schema": "threadlight-evals-manifest/v1", "captured_at": _iso_now(), "verdict": "mystery"},
    )

    for payload in bad_payloads:
        _write_json(manifest, payload)
        decision = orch._check_evals(tmp_path, {})
        assert decision.decision == "run"


def test_leg_manifest_reruns_when_capabilities_are_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "redteam-manifest.json",
        {
            "schema": "threadlight-redteam-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "hardened",
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "run"
    assert "capabilities" in decision.reason


def test_leg_manifest_skips_fresh_non_passing_known_verdict_with_reason(tmp_path):
    manifest = tmp_path / "specs" / "redteam-manifest.json"
    _write_json(
        manifest,
        {
            "schema": "threadlight-redteam-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "vulnerable",
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["redteam"]["required_capabilities"]
            },
            "asr": {
                "jailbreak": 0.0,
                "prompt_injection": 0.0,
                "indirect_attack": 0.0,
                "exfiltration": 0.0,
                "harmful_content": 0.0,
            },
            "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "skip"
    assert "verdict=vulnerable" in decision.reason


def test_leg_manifest_reruns_when_govern_capability_status_is_invalid(tmp_path):
    _write_json(
        tmp_path / "specs" / "govern-manifest.json",
        {
            "schema": "threadlight-govern-manifest/v2",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "governed",
            "capabilities": {
                capability: {"status": "pass"}
                for capability in ("policy_artefact_present", "policy_schema_valid")
            }
            | {"policy_schema_valid": {"status": "bogus"}},
        },
    )

    decision = orch._check_govern(tmp_path, {})

    assert decision.decision == "run"
    assert "legacy capabilities are not evidence" in decision.reason


def test_leg_manifest_reruns_when_evals_check_id_is_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "evals-manifest.json",
        {
            "schema": "threadlight-evals-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "comprehensive",
            "capabilities": {
                capability: {"status": "pass", "check_id": f"eval-{index:03d}"}
                for index, capability in enumerate(
                    sorted(orch.LEG_CONTRACTS["evals"]["required_capabilities"]),
                    start=1,
                )
            }
            | {"eval_scenarios_present": {"status": "pass"}},
        },
    )

    decision = orch._check_evals(tmp_path, {})

    assert decision.decision == "run"
    assert "check_id" in decision.reason


def test_leg_manifest_reruns_when_redteam_capability_has_unsupported_fields(tmp_path):
    _write_json(
        tmp_path / "specs" / "redteam-manifest.json",
        {
            "schema": "threadlight-redteam-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "vulnerable",
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["redteam"]["required_capabilities"]
            }
            | {"scan_present": {"status": "pass", "bogus": 123}},
            "asr": {
                "jailbreak": 0.0,
                "prompt_injection": 0.0,
                "indirect_attack": 0.0,
                "exfiltration": 0.0,
                "harmful_content": 0.0,
            },
            "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "run"
    assert "unsupported fields" in decision.reason


def test_leg_manifest_reruns_when_redteam_tool_version_is_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "redteam-manifest.json",
        {
            "schema": "threadlight-redteam-manifest/v1",
            "captured_at": _iso_now(),
            "verdict": "hardened",
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["redteam"]["required_capabilities"]
            },
            "asr": {
                "jailbreak": 0.0,
                "prompt_injection": 0.0,
                "indirect_attack": 0.0,
                "exfiltration": 0.0,
                "harmful_content": 0.0,
            },
            "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "run"
    assert "tool_version" in decision.reason


def test_cost_projection_requires_1x_schema_before_trusting_generated_at(tmp_path):
    spec = tmp_path / "specs" / "SPEC.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "\n".join(
            [
                "load_profile:",
                "  workload_class: steady",
                "  peak_concurrent_sessions: 10",
                "  avg_requests_per_session: 4",
                "  avg_tokens_per_request: 800",
                "  peak_requests_per_second: 3",
                "  business_hours_only: true",
                "  cosmos_gb_year_one: 1",
                "  storage_gb_year_one: 1",
                "  ai_search_documents: 10",
                "  monthly_growth_rate: 0.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "specs" / "cost-manifest.json",
        {
            "schema_version": "2.0",
            "generated_at": _iso_now(),
        },
    )

    decision = orch._check_cost_projection(
        tmp_path,
        {"cost_projection": {"last_deploy_at": "2026-01-01T00:00:00Z"}},
    )

    assert decision.decision == "run"


def test_cost_projection_resumability_requires_strictly_newer_than_last_deploy(tmp_path):
    """Regression: resume check should require manifest.generated_at > last_deploy_at.

    If generated_at equals the recorded last deploy instant, the planner must
    re-run cost-projection (decision "run").
    """
    spec = tmp_path / "specs" / "SPEC.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "\n".join(
            [
                "load_profile:",
                "  workload_class: steady",
                "  peak_concurrent_sessions: 10",
                "  avg_requests_per_session: 4",
                "  avg_tokens_per_request: 800",
                "  peak_requests_per_second: 3",
                "  business_hours_only: true",
                "  cosmos_gb_year_one: 1",
                "  storage_gb_year_one: 1",
                "  ai_search_documents: 10",
                "  monthly_growth_rate: 0.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Use a trusted 1.x schema_version and set generated_at exactly equal to last_deploy
    last_deploy = "2026-08-20T12:34:56Z"
    _write_json(
        tmp_path / "specs" / "cost-manifest.json",
        {
            "schema_version": "1.0",
            "generated_at": last_deploy,
        },
    )

    decision = orch._check_cost_projection(
        tmp_path,
        {"cost_projection": {"last_deploy_at": last_deploy}},
    )

    # Expect RUN because generated_at must be strictly newer than last_deploy to reuse
    assert decision.decision == "run"


# ---------------------------------------------------------------------------
# Governed-actions lifecycle (Task 14): recommendation-only, never a stage.
#
# `threadlight-governed-actions` owns consequential-action assessment,
# enforcement scaffolding, and every high-impact change. `threadlight-auto`
# may only *recommend* the explicit lifecycle steps and summarise an already
# committed manifest. It never runs the skill, never adds it to the stage
# runner, and never emits a scaffold / policy-application / canary / merge /
# deploy command of its own.
# ---------------------------------------------------------------------------

decide = orch.decide
STAGES = orch.STAGES
LEG_CONTRACTS = orch.LEG_CONTRACTS

GOVERNED_ACTIONS_MANIFEST_REL = "tests/governed-actions-manifest.json"
GOLDEN_GOVERNED_ACTIONS_MANIFEST = (
    REPO
    / "skills"
    / "threadlight-governed-actions"
    / "tests"
    / "golden"
    / "conformant-manifest.json"
)
GOLDEN_COMMIT = "0123456789abcdef0123456789abcdef01234567"
# Inside the golden's own freshness window (2026-09-01T12:00Z .. 2026-09-02T12:00Z).
GOLDEN_FRESH_NOW = datetime(2026, 9, 1, 18, 0, 0, tzinfo=timezone.utc)

EXPECTED_HANDOFF = {
    "execution": "manual-explicit",
    "design": (
        "run threadlight-governed-actions --phase design "
        "after threadlight-design"
    ),
    "pre_deploy": (
        "run threadlight-governed-actions --phase pre-deploy before deploy"
    ),
    "post_deploy": (
        "run threadlight-governed-actions --phase post-deploy "
        "against staging only"
    ),
    "manifest": "tests/governed-actions-manifest.json",
}

# Privileged verbs auto must never emit. `deploy`/`design` on their own are
# deliberately absent: the approved lifecycle wording contains them, and this
# guard is about *commands* auto could be read as authorising.
FORBIDDEN_COMMAND_TOKENS = (
    "--scaffold",
    "scaffold",
    "--apply",
    "apply-plan",
    "apply the policy",
    "policy apply",
    "canary",
    "rollout",
    "merge",
    "azd up",
    "azd deploy",
    "az deployment",
    "gh pr",
    "git push",
)


def _golden_governed_actions_manifest() -> dict:
    return json.loads(GOLDEN_GOVERNED_ACTIONS_MANIFEST.read_text(encoding="utf-8"))


def make_context(
    tmp_path: Path,
    *,
    consequential_actions: bool = False,
    manifest: dict | None = None,
) -> Path:
    """Build a workspace and return it (the orchestrator's only input).

    `consequential_actions=True` writes the smallest honest signal auto can
    read on its own: a root tool registry declaring a non-`read` action.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    if consequential_actions:
        _write_json(
            tmp_path / "tool-registry.json",
            {
                "actions": [
                    {"id": "issue_refund", "consequence": "financial"},
                    {"id": "read_claim", "consequence": "read"},
                ]
            },
        )
    if manifest is not None:
        _write_json(tmp_path / "tests" / "governed-actions-manifest.json", manifest)
    return tmp_path


def test_auto_recommends_explicit_governed_actions_lifecycle_handoffs(tmp_path):
    decision = decide(make_context(tmp_path, consequential_actions=True))
    assert decision["governed_actions"] == EXPECTED_HANDOFF
    assert orch.GOVERNED_ACTIONS_HANDOFF == EXPECTED_HANDOFF


def test_auto_never_schedules_governed_actions_or_rollout():
    assert "governed_actions" not in STAGES
    assert all(
        leg.get("skill") != "threadlight-governed-actions"
        for leg in LEG_CONTRACTS.values()
    )
    # Not a stage probe, and not one of the four advisory live legs either.
    assert "governed_actions" not in orch.STAGE_PROBES
    assert "threadlight-governed-actions" not in orch.MANUAL_HANDOFFS


def test_auto_omits_governed_actions_handoff_without_signal(tmp_path):
    decision = decide(make_context(tmp_path))
    assert decision["governed_actions"] is None
    assert decision["governed_actions_manifest"] is None


def test_auto_recommends_handoff_when_only_a_manifest_exists(tmp_path):
    workspace = make_context(tmp_path, manifest=_golden_governed_actions_manifest())
    decision = decide(workspace)
    assert decision["governed_actions"] == EXPECTED_HANDOFF


def test_auto_summarizes_a_schema_valid_governed_actions_manifest(tmp_path):
    workspace = make_context(
        tmp_path,
        consequential_actions=True,
        manifest=_golden_governed_actions_manifest(),
    )
    summary = orch.summarize_governed_actions_manifest(
        workspace / GOVERNED_ACTIONS_MANIFEST_REL,
        GOLDEN_COMMIT,
        now=GOLDEN_FRESH_NOW,
    )
    assert summary["status"] == "summarized"
    assert summary["trusted"] is True
    assert summary["phase"] == "pre-deploy"
    assert summary["verdict"] == "governed"
    assert summary["counts"] == {
        "pass": 1,
        "must_fix": 0,
        "should_fix": 0,
        "not_verified": 0,
        "not_applicable": 0,
    }
    assert summary["recommendation"] == []


def test_auto_recommends_rerun_for_a_stale_governed_actions_manifest(tmp_path):
    workspace = make_context(tmp_path, manifest=_golden_governed_actions_manifest())
    summary = orch.summarize_governed_actions_manifest(
        workspace / GOVERNED_ACTIONS_MANIFEST_REL,
        GOLDEN_COMMIT,
        now=GOLDEN_FRESH_NOW + timedelta(days=3),
    )
    assert summary["status"] == "rerun-recommended"
    assert summary["trusted"] is False
    assert "expired" in summary["reason"]
    assert summary["counts"] == {
        "pass": 0,
        "must_fix": 0,
        "should_fix": 0,
        "not_verified": 0,
        "not_applicable": 0,
    }
    # Rerun wording is exactly the approved lifecycle recommendation.
    assert summary["recommendation"] == [EXPECTED_HANDOFF["pre_deploy"]]


def test_auto_rejects_unsupported_or_missing_governance_evidence(tmp_path):
    legacy = _golden_governed_actions_manifest()
    legacy.pop("evidence_contract")
    stripped = _golden_governed_actions_manifest()
    removed = {e["evidence_id"] for e in stripped["evidence"]
               if e["kind"].endswith("-ledger-records")}
    stripped["evidence"] = [e for e in stripped["evidence"] if e["evidence_id"] not in removed]
    for probe in stripped["conformance"]["application_probes"]:
        probe["evidence_refs"] = [ref for ref in probe["evidence_refs"] if ref not in removed]
    for index, manifest in enumerate((legacy, stripped)):
        workspace = make_context(tmp_path / str(index), manifest=manifest)
        summary = orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL, GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW,
        )
        assert summary["trusted"] is False
        assert summary["status"] == "rerun-recommended"


def test_auto_rejects_partial_or_hidden_governance_probe_outcomes(tmp_path):
    partial = _golden_governed_actions_manifest()
    probes = partial["conformance"]["application_probes"]
    probes.remove(next(p for p in probes if p["probe_id"] == "approval-anti-replay"))
    missing = _golden_governed_actions_manifest()
    missing["conformance"]["application_probes"] = []
    hidden = _golden_governed_actions_manifest()
    next(p for p in hidden["conformance"]["application_probes"]
         if p["probe_id"] == "output-mediation")["status"] = "not-verified"
    summaries = []
    for index, manifest in enumerate((partial, missing, hidden)):
        workspace = make_context(tmp_path / str(index), manifest=manifest)
        summaries.append(orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL, GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW,
        ))
    assert [s["trusted"] for s in summaries] == [False, False, False]


def _assert_missing_approval_action_id_is_untrusted(tmp_path, monkeypatch, consumer):
    manifest = _golden_governed_actions_manifest()
    probe = next(p for p in manifest["conformance"]["application_probes"]
                 if p["probe_id"] == "approval-anti-replay")
    del probe["action_id"]
    workspace = make_context(tmp_path, manifest=manifest)
    if consumer == "summarize":
        summary = orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL, GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW,
        )
    else:
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return GOLDEN_FRESH_NOW
        monkeypatch.setattr(orch, "datetime", FrozenDatetime)
        monkeypatch.setattr(orch, "_git_head_commit", lambda _: GOLDEN_COMMIT)
        summary = decide(workspace)["governed_actions_manifest"]
    assert summary["trusted"] is False
    assert summary["status"] == "rerun-recommended"
    assert summary["verdict"] is None
    assert summary["recommendation"]


def test_auto_summarize_missing_approval_action_id_is_untrusted(tmp_path, monkeypatch):
    _assert_missing_approval_action_id_is_untrusted(tmp_path, monkeypatch, "summarize")


def test_auto_decide_missing_approval_action_id_is_untrusted(tmp_path, monkeypatch):
    _assert_missing_approval_action_id_is_untrusted(tmp_path, monkeypatch, "decide")


def test_auto_recommends_rerun_for_an_invalid_governed_actions_manifest(tmp_path):
    cases: dict[str, dict] = {}

    wrong_schema = _golden_governed_actions_manifest()
    wrong_schema["schema"] = "threadlight-governed-actions-manifest/v2"
    cases["schema"] = wrong_schema

    wrong_commit = _golden_governed_actions_manifest()
    wrong_commit["source"] = dict(wrong_commit["source"], commit="f" * 40)
    cases["commit"] = wrong_commit

    dirty = _golden_governed_actions_manifest()
    dirty["source"] = dict(dirty["source"], dirty=True)
    cases["dirty"] = dirty

    miscounted = _golden_governed_actions_manifest()
    miscounted["summary"] = {
        **miscounted["summary"],
        "pass": [],
        "must_fix": ["OPS-001"],
    }
    cases["summary"] = miscounted

    for label, manifest in cases.items():
        workspace = make_context(tmp_path / label, manifest=manifest)
        summary = orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL,
            GOLDEN_COMMIT,
            now=GOLDEN_FRESH_NOW,
        )
        assert summary["status"] == "rerun-recommended", label
        assert summary["trusted"] is False, label
        assert summary["verdict"] is None, label
        assert summary["recommendation"], label
        assert all(
            rec in EXPECTED_HANDOFF.values() for rec in summary["recommendation"]
        ), label

    # Unparseable JSON is untrusted too, and never crashes the orchestrator.
    broken = tmp_path / "broken"
    (broken / "tests").mkdir(parents=True)
    (broken / "tests" / "governed-actions-manifest.json").write_text(
        "{ not json", encoding="utf-8"
    )
    summary = orch.summarize_governed_actions_manifest(
        broken / GOVERNED_ACTIONS_MANIFEST_REL, GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW
    )
    assert summary["status"] == "rerun-recommended"
    assert summary["recommendation"] == [
        EXPECTED_HANDOFF["design"],
        EXPECTED_HANDOFF["pre_deploy"],
        EXPECTED_HANDOFF["post_deploy"],
    ]


def test_auto_recommends_rerun_for_a_non_string_finding_status(tmp_path):
    # An otherwise schema-valid, commit-bound, fresh manifest must still be
    # untrusted when a finding's status is untrusted JSON that isn't a
    # string (e.g. an array or object) rather than raising inside the
    # findings loop's `status not in observed` membership check.
    non_string_statuses: dict[str, object] = {
        "list": ["pass"],
        "object": {"value": "pass"},
    }

    for label, status in non_string_statuses.items():
        manifest = _golden_governed_actions_manifest()
        manifest["findings"][0]["status"] = status
        workspace = make_context(tmp_path / label, manifest=manifest)
        summary = orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL,
            GOLDEN_COMMIT,
            now=GOLDEN_FRESH_NOW,
        )
        assert summary["status"] == "rerun-recommended", label
        assert summary["trusted"] is False, label
        assert "unknown id or status" in summary["reason"], label
        assert summary["verdict"] is None, label
        assert summary["recommendation"] == [EXPECTED_HANDOFF["pre_deploy"]], label


def test_auto_decision_surfaces_the_manifest_summary(tmp_path):
    manifest = _golden_governed_actions_manifest()
    workspace = make_context(tmp_path, consequential_actions=True, manifest=manifest)
    decision = decide(workspace)
    summary = decision["governed_actions_manifest"]
    assert summary is not None
    # The committed golden is long expired against wall-clock now, so auto must
    # recommend a rerun rather than believe it.
    assert summary["status"] == "rerun-recommended"
    assert summary["manifest"] == GOVERNED_ACTIONS_MANIFEST_REL


def test_auto_output_carries_no_privileged_commands(tmp_path):
    """The governed-actions surface authors recommendations, never commands.

    Scoped to that surface on purpose: auto legitimately drives `azd up` at its
    own deploy stage, and this guard is about what the governed-actions handoff
    hands an agent, not about the pilot driver's existing stage prose.
    """
    workspace = make_context(
        tmp_path,
        consequential_actions=True,
        manifest=_golden_governed_actions_manifest(),
    )
    decision = decide(workspace)
    blob = json.dumps(
        {
            "governed_actions": decision["governed_actions"],
            "governed_actions_manifest": decision["governed_actions_manifest"],
        }
    )
    for approved in EXPECTED_HANDOFF.values():
        blob = blob.replace(approved, "")
    low = blob.casefold()
    for token in FORBIDDEN_COMMAND_TOKENS:
        assert token not in low, token


def test_auto_never_imports_or_executes_the_governed_actions_skill():
    source = Path(orch.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "threadlight-governed-actions/scripts",
        '"threadlight-governed-actions"',
        "threadlight_governed_actions",
        "import probes",
        "import render",
        "import scaffold",
        "import governed_actions",
    ):
        assert forbidden not in source, forbidden
    # The summary is a pure read of committed JSON: no producer module is
    # imported as a side effect of running it.
    before = set(sys.modules)
    orch.summarize_governed_actions_manifest(
        Path("does-not-exist.json"), GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW
    )
    assert not {
        name
        for name in set(sys.modules) - before
        if name in {"probes", "render", "scaffold", "governed_actions", "mediation"}
    }


def test_auto_human_output_prints_recommendations_only(tmp_path, capsys):
    workspace = make_context(
        tmp_path,
        consequential_actions=True,
        manifest=_golden_governed_actions_manifest(),
    )
    orch._print_human(decide(workspace))
    printed = capsys.readouterr().out
    for key in ("design", "pre_deploy", "post_deploy"):
        assert EXPECTED_HANDOFF[key] in printed
    assert "execution: manual-explicit" in printed
    # Scoped to the governed-actions block: the pilot driver's own deploy
    # stage legitimately says it will run `azd up`.
    block = printed[printed.index("Governed actions") :]
    for approved in EXPECTED_HANDOFF.values():
        block = block.replace(approved, "")
    low = block.casefold()
    for token in ("--scaffold", "--apply", "--canary", "azd up", "gh pr merge"):
        assert token not in low, token


def test_auto_human_output_stays_silent_without_a_signal(tmp_path, capsys):
    orch._print_human(decide(make_context(tmp_path)))
    assert "governed actions" not in capsys.readouterr().out.casefold()


def run(workspace: Path) -> dict:
    out = subprocess.run(
        [sys.executable, str(ORCH), "--workspace", str(workspace), "--dry-run", "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if not out.stdout.strip():
        raise RuntimeError(f"orchestrator emitted no JSON for {workspace}; stderr={out.stderr!r}")
    return json.loads(out.stdout)


def _standalone_manual_handoff_checks() -> int:
    """Manual-handoff assertions for the standalone runner (no pytest fixtures)."""
    failures = 0
    with tempfile.TemporaryDirectory(prefix="threadlight-handoffs-") as tmp:
        ws = Path(tmp)
        decision = orch.decide(ws)
        order = [h["skill"] for h in decision["manual_handoffs"]]
        expected = [
            "threadlight-connect", "threadlight-ground",
            "threadlight-loadtest", "threadlight-upgrade",
        ]
        if order != expected:
            print(f"❌ manual_handoffs order: expected {expected}, got {order}")
            failures += 1
        elif {"connect", "ground", "loadtest", "upgrade"}.intersection(decision["stages"]):
            print("❌ live legs leaked into stages")
            failures += 1
        else:
            print("✅ manual handoffs ordered + excluded from stages")

        specs = ws / "specs"
        specs.mkdir()
        (specs / "connect-manifest.json").write_text(
            _leg_envelope("threadlight-connect-manifest/v1", "complete"), encoding="utf-8")
        (specs / "load-manifest.json").write_text(
            _leg_envelope("threadlight.load/v1", "aborted"), encoding="utf-8")
        by_skill = {h["skill"]: h["status"] for h in orch.decide(ws)["manual_handoffs"]}
        if by_skill["threadlight-connect"] != "complete" or by_skill["threadlight-loadtest"] != "aborted":
            print(f"❌ manual handoff status mismatch: {by_skill}")
            failures += 1
        else:
            print("✅ manual handoff status reflects envelope")
    return failures


def main() -> int:
    cases = [
        ("blank",        "run",       {"preflight", "design", "deploy", "safe_check", "cost_projection", "invoke", "evals", "redteam", "govern"}, set()),
        # NOTE: all-complete fixture predates cost_projection + the discover/protect
        # legs; no cost-manifest.json → cost_projection runs, and the cascade plus
        # absent leg manifests make evals/redteam/govern run too.
        ("all-complete", "run",       {"cost_projection", "invoke", "evals", "redteam", "govern"},                   {"preflight", "design", "deploy", "safe_check"}),
        ("hard-stop",    "hard_stop", None,                                                                         None),
        ("spec-edited",  "run",       None,                                                                         None),
    ]
    failures = 0
    for fixture_name, expected_type, expected_run, expected_skip in cases:
        fixture = FIXTURES / fixture_name
        if not fixture.exists():
            print(f"❌ {fixture_name}: fixture dir missing")
            failures += 1
            continue
        try:
            fixture_to_run = fixture
            with tempfile.TemporaryDirectory(prefix=f"threadlight-{fixture_name}-") as tmp:
                if fixture_name == "all-complete":
                    fixture_to_run = Path(tmp) / fixture_name
                    shutil.copytree(fixture, fixture_to_run)
                    for rel in (
                        ".threadlight/preflight-passed.json",
                        "docs/invoke-results.md",
                    ):
                        os.utime(fixture_to_run / rel)
                    postdeploy = fixture_to_run / "tests" / "postdeploy-manifest.json"
                    postdeploy_data = json.loads(postdeploy.read_text(encoding="utf-8"))
                    postdeploy_data["checked_at"] = _iso_now()
                    postdeploy.write_text(json.dumps(postdeploy_data), encoding="utf-8")
                report = run(fixture_to_run)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ {fixture_name}: orchestrator crashed: {exc!r}")
            failures += 1
            continue
        actual_type = report["next_action"]["type"]
        if actual_type != expected_type:
            print(f"❌ {fixture_name}: expected next_action.type={expected_type!r}, got {actual_type!r}")
            failures += 1
            continue
        if expected_run is not None:
            actual_run = set(report["next_action"].get("stages_to_run", []))
            if actual_run != expected_run:
                print(f"❌ {fixture_name}: stages_to_run mismatch; expected={sorted(expected_run)} actual={sorted(actual_run)}")
                failures += 1
                continue
        if expected_skip is not None:
            actual_skip = set(report["next_action"].get("stages_to_skip", []))
            if actual_skip != expected_skip:
                print(f"❌ {fixture_name}: stages_to_skip mismatch; expected={sorted(expected_skip)} actual={sorted(actual_skip)}")
                failures += 1
                continue
        if fixture_name == "spec-edited":
            if "design" not in set(report["next_action"].get("stages_to_run", [])):
                print(f"❌ spec-edited: expected 'design' in stages_to_run after hash mismatch; got {report['next_action'].get('stages_to_run')}")
                failures += 1
                continue
        print(f"✅ {fixture_name}: next_action.type={actual_type}")

    # --- extra: assert cost_projection is in STAGES between safe_check and invoke ---
    import importlib.util as _ilu, sys as _sys
    _s = _ilu.spec_from_file_location("_orch_check", str(ORCH))
    _m = _ilu.module_from_spec(_s)
    _sys.modules["_orch_check"] = _m
    _s.loader.exec_module(_m)
    stages = _m.STAGES
    if "cost_projection" not in stages:
        print("❌ STAGES: cost_projection not in STAGES list")
        failures += 1
    else:
        cp_idx = stages.index("cost_projection")
        sc_idx = stages.index("safe_check")
        inv_idx = stages.index("invoke")
        if not (sc_idx < cp_idx < inv_idx):
            print(f"❌ STAGES: cost_projection at index {cp_idx} not between safe_check ({sc_idx}) and invoke ({inv_idx})")
            failures += 1
        else:
            print(f"✅ STAGES order: safe_check({sc_idx}) < cost_projection({cp_idx}) < invoke({inv_idx})")

    # A fresh marker is reusable only while it remains bound to the exact
    # Foundation that passed runtime-policy validation.
    with tempfile.TemporaryDirectory(prefix="threadlight-foundation-created-") as tmp:
        workspace = Path(tmp)
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": "1.0.0", "foundation_sha256": None}), encoding="utf-8")
        foundation = workspace / "specs" / "foundation.md"
        foundation.parent.mkdir(parents=True)
        foundation.write_text("# Foundation\n", encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ foundation-created-after-preflight: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-created-after-preflight: preflight invalidated")

    with tempfile.TemporaryDirectory(prefix="threadlight-legacy-marker-") as tmp:
        workspace = Path(tmp)
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ legacy-marker-without-foundation-hash: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ legacy-marker-without-foundation-hash: preflight invalidated")

    with tempfile.TemporaryDirectory(prefix="threadlight-foundation-matching-") as tmp:
        workspace = Path(tmp)
        foundation = workspace / "specs" / "foundation.md"
        foundation.parent.mkdir(parents=True)
        foundation.write_text("# Foundation\n", encoding="utf-8")
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps({"version": "1.0.0", "foundation_sha256": _m._sha256(foundation)}),
            encoding="utf-8",
        )
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "skip":
            print(f"❌ foundation-hash-matches: expected skip, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-hash-matches: fresh preflight reused")

        foundation.write_text("# Foundation\n\nedited: true\n", encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ foundation-edited-after-preflight: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-edited-after-preflight: preflight invalidated")

    # --- extra: assert the discover/protect legs follow invoke in STAGES ---
    for leg in ("evals", "redteam", "govern"):
        if leg not in stages:
            print(f"❌ STAGES: {leg} not in STAGES list")
            failures += 1
        elif stages.index(leg) <= stages.index("invoke"):
            print(f"❌ STAGES: {leg} at index {stages.index(leg)} not after invoke ({stages.index('invoke')})")
            failures += 1
        else:
            print(f"✅ STAGES order: invoke({stages.index('invoke')}) < {leg}({stages.index(leg)})")

    # --- extra: manual-handoff projection (Task 7) ---
    failures += _standalone_manual_handoff_checks()

    print(f"\n=== {len(cases) + 1 - failures}/{len(cases) + 1} passed ===")
    return failures


if __name__ == "__main__":
    sys.exit(main())
