"""Task13 Step6 is executed local evidence, never a deployed-image attestation."""
import importlib
import hashlib
import json
from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples/returns-triage-governed"
SCRIPTS = ROOT / "skills/threadlight-governed-actions/scripts"
sys.path.insert(0, str(SCRIPTS))


def test_selected_native_probe_contract_is_runnable_not_a_synthetic_dispatch():
    path = EXAMPLE / "governance/probe-contract.json"
    assert path.is_file(), "Task13 has no actual local probe entrypoint"
    contract = json.loads(path.read_text())
    assert contract["protocol"] == "native-local/v1"
    assert contract["actions"] == ["returns_apply_decision"]
    assert contract["entrypoint"] == "scripts/local_probe.py"
    assert contract["side_effect_mode"] == "local-sdk-storage"
    assert set(contract["execution_modes"]) == {
        "interactive", "batch", "background", "subagent", "direct-tool"}


def test_local_probe_entrypoint_really_runs_the_gate():
    completed = subprocess.run([sys.executable, str(EXAMPLE / "scripts/local_probe.py")],
                               cwd=EXAMPLE, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr
    assert "LOCAL selected proof; live bindings unverified" in completed.stdout


def test_intake_contract_cannot_finalize_before_known_risk():
    skills = EXAMPLE / "src/agent/skills"
    assert '"verdict": "complete | incomplete"' in (skills / "intake-validation/SKILL.md").read_text()
    assert "completeness verdict `incomplete`" in (skills / "disposition-decision/SKILL.md").read_text()


def test_served_instructions_require_known_risk_before_incomplete_disposition():
    prompt = (EXAMPLE / "src/agent/copilot-instructions.md").read_text()
    assert "Do not stop on missing information" in prompt
    assert "regardless of eligibility or completeness" in prompt
    assert "unknown risk never authorizes a refund" in prompt
    assert "before stopping" not in prompt
    fraud = (EXAMPLE / "src/agent/skills/fraud-escalation/SKILL.md").read_text()
    assert "incomplete" in fraud and "request_more_info" in fraud


def test_assessor_fingerprints_cover_actual_served_prompt_and_runtime_skills():
    native = importlib.import_module("native_local")
    hashes = native.source_fingerprints(ROOT, EXAMPLE)
    for path in [EXAMPLE / "src/agent/copilot-instructions.md",
                 *sorted((EXAMPLE / "src/agent/skills").glob("*/SKILL.md"))]:
        assert hashes.get("project:" + path.relative_to(EXAMPLE).as_posix()) == hashlib.sha256(
            path.read_bytes()).hexdigest()


def test_native_pin_uses_shared_runtime_and_observed_wheels_not_old_maf_claim():
    pin = json.loads((ROOT / "skills/threadlight-governed-actions/references/upstream-pin.json").read_text())
    shared = json.loads((ROOT / "skills/_shared/governance-upstream-pin.json").read_text())
    assert pin["maf"]["version"] == shared["maf"]["agent-framework-core"]
    assert pin["acs"]["policy_schema"] == "0.3.1-beta"
    assert pin["runtime_pin"] == "../../_shared/governance-upstream-pin.json"
    adapter = importlib.import_module("maf_adapter")
    assert hasattr(adapter, "compare_native_observation")


def test_assessor_policy_hashes_include_executed_rego_and_bundle():
    inventory = importlib.import_module("inventory").build_action_inventory(EXAMPLE)
    assert Path("src/agent/governance/policy/returns.rego") in inventory.policy_paths
    assert Path("src/agent/governance/bundle/bundle-metadata.json") in inventory.policy_paths


def test_runtime_contract_accepts_validated_assessor_tool_metadata():
    from skills._shared.governance import validate_governance_contract
    import yaml
    document = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    metadata = {"id": "returns_apply_decision", "approval_required": True,
                "execution_modes": ["interactive"], "input_schema": {}, "output_schema": {}}
    selected = next(t for t in document["tools"] if t["id"] == metadata["id"])
    selected.update({k: metadata[k] for k in
                     ("approval_required", "execution_modes", "input_schema", "output_schema")})
    validate_governance_contract({k: document[k] for k in ("framework", "governance", "tools")},
                                 deployment_target="customer-pilot")


def test_change_plane_root_is_observed_worktree_not_arbitrary_ancestor():
    ghcp = importlib.import_module("ghcp")
    assert hasattr(ghcp, "resolve_change_plane_root")
    root, prefix = ghcp.resolve_change_plane_root(EXAMPLE)
    assert root == ROOT
    assert prefix == "examples/returns-triage-governed"
    result = ghcp.assess_change_plane(EXAMPLE)
    assert not any(f.reason_code in {"codeowners-missing", "codeowners-incomplete-coverage",
                                     "ci-missing-ctk-or-application-probes"}
                   for f in result.findings)


def test_standalone_owner_metadata_requires_its_own_git_root(tmp_path):
    ghcp = importlib.import_module("ghcp")
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance/change-plane.json").write_text(json.dumps({
        "scope": "standalone", "workflows": [".github/workflows/native-local.yml"]}))
    with pytest.raises(ghcp.ChangePlaneError, match="standalone"):
        ghcp.resolve_change_plane_root(tmp_path)


def test_worktree_owner_metadata_rejects_arbitrary_parent(tmp_path):
    ghcp = importlib.import_module("ghcp")
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance/change-plane.json").write_text(json.dumps({
        "scope": "worktree", "root": "..", "project": tmp_path.name,
        "workflows": [".github/workflows/native-local.yml"]}))
    with pytest.raises(ghcp.ChangePlaneError, match="current-worktree"):
        ghcp.resolve_change_plane_root(tmp_path)


def test_native_local_gate_selected_proof_not_live(tmp_path):
    native = importlib.import_module("native_local")
    inventory = importlib.import_module("inventory")
    mediation = importlib.import_module("mediation")
    contract = native.contract(EXAMPLE)
    events = native.execute(EXAMPLE, contract)
    paths, results, pins = native.evaluate(events, contract, inventory.build_action_inventory(EXAMPLE).actions)
    assert events[0]["observation"]["deployed_image"] == "not-verified"
    prompt_hash = hashlib.sha256((EXAMPLE / "src/agent/copilot-instructions.md").read_bytes()).hexdigest()
    requests = [e for e in events if e["event"] == "model_instructions"]
    assert requests and all(e["served_sha256"] == prompt_hash for e in requests)
    assert any(e["case"] == "risk-incomplete" for e in requests)
    profile_cases = {"missing-profile-low", "missing-profile-risk", "missing-profile-absent",
                     "missing-profile-wrong-info"}
    assert profile_cases <= {e["case"] for e in events if e["event"] == "terminal"}
    paths, findings = mediation.apply_execution_receipts(paths, results)
    assert not findings
    selected = [p for p in results if p.action_id == "returns_apply_decision"]
    assert next(p for p in selected if p.probe_id == "output-mediation").status == "not-applicable"
    assert selected and all(p.status == "pass" for p in selected if p.probe_id != "output-mediation")
    assert {p.mode for p in selected if p.path_id} == {
        "interactive", "batch", "background", "subagent", "direct-tool"}
    from copy import deepcopy
    for remove in ("native_evaluation", "pre_action_decision", "invocation", "approval_replay",
                   "approval_changed", "approval_expired", "approval_fresh", "terminal",
                   "audit_failure", "schema_output", "model_instructions"):
        corrupt = [e for e in deepcopy(events) if e["event"] != remove]
        with pytest.raises(Exception):
            native.evaluate(corrupt, contract, inventory.build_action_inventory(EXAMPLE).actions)
    for case in profile_cases:
        with pytest.raises(Exception):
            native.evaluate([e for e in events if e.get("case") != case], contract,
                            inventory.build_action_inventory(EXAMPLE).actions)
    corrupt = deepcopy(events)
    corrupt[0]["observation"]["packages"][0]["sha256"] = "f" * 64
    with pytest.raises(Exception):
        native.evaluate(corrupt, contract, inventory.build_action_inventory(EXAMPLE).actions)
    from dataclasses import replace
    required = tuple(replace(a, binding_requires_output=True)
                     if a.action_id == "returns_apply_decision" else a
                     for a in inventory.build_action_inventory(EXAMPLE).actions)
    _, extra_results, _ = native.evaluate(events, contract, required)
    assert next(p for p in extra_results if p.probe_id == "output-mediation").status == "not-verified"
