"""Task14: execute workflow script strings and real fail-closed input validation.

Command recording proves orchestration/order only, never deployed enforcement.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/threadlight-e2e-foundry.yml"
SCRIPT = ROOT / "scripts/ci/runtime_readiness.py"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def helper():
    assert SCRIPT.exists(), "Missing executable readiness workflow producer"
    spec = importlib.util.spec_from_file_location("runtime_readiness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_separate_local_and_live_jobs_do_not_use_legacy_smoke_path():
    jobs = workflow()["jobs"]
    assert "local-native-contract" in jobs, "No independent native/CTK contract job"
    assert "readiness-proof" in jobs, "No protected real runtime readiness job"
    assert "inputs.mode != 'readiness-proof'" in jobs["e2e"]["if"]
    local = "\n".join(s.get("run", "") for s in jobs["local-native-contract"]["steps"])
    assert "run-governance-pin-tests.py" in local and "--deployment" in local
    live = jobs["readiness-proof"]
    assert live["environment"] == "governance-preproduction"
    assert live["needs"] == "local-native-contract"
    assert all(not s.get("continue-on-error") for s in live["steps"])
    scripts = "\n".join(s.get("run", "") for s in live["steps"])
    for stage in ("validate-inputs", "prepare", "predeploy", "deploy", "postdeploy"):
        assert f"runtime_readiness.py {stage}" in scripts
    assert scripts.index(" prepare ") < scripts.index(" predeploy ") < scripts.index(" deploy ") < scripts.index(" postdeploy ")
    for skip in ("hashFiles(", "continue-on-error", "|| true", "--accept-stale-safe-check"):
        assert skip not in scripts


def test_workflow_required_inputs_script_really_refuses_no_probe_opt_in(tmp_path):
    jobs = workflow()["jobs"]
    assert "readiness-proof" in jobs, "Missing live job"
    step = next(s for s in jobs["readiness-proof"]["steps"] if s.get("id") == "governance-inputs")
    (tmp_path / "python").symlink_to(sys.executable)
    env = {**os.environ, "GOVERNANCE_SAFE_PROBE": "false",
           "GOVERNANCE_CI_CONFIG": "", "GITHUB_WORKSPACE": str(ROOT),
           "GOV_PROJECT": str(tmp_path / "pilot"),
           "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=ROOT,
                            env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "explicit preproduction safe-probe opt-in required" in result.stderr


@pytest.mark.parametrize("value", ["", "{}", '{"environment":"production"}'])
def test_inputs_fail_descriptively_without_protected_configuration(value, tmp_path):
    mod = helper()
    config = tmp_path / "config.json"
    config.write_text(value)
    with pytest.raises(ValueError, match="protected configuration"):
        mod.load_inputs(config)


def test_source_configuration_requires_concrete_files_not_an_acceptance_boolean(tmp_path):
    mod = helper()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"safe": True, "approved": True, "environment": "preproduction"}))
    with pytest.raises(ValueError, match="protected configuration"):
        mod.load_inputs(config)


def test_postdeploy_uses_current_manifest_and_refuses_legacy_green(tmp_path):
    mod = helper()
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/governance-manifest.json").write_text(json.dumps({
        "schema": "threadlight-govern-manifest/v2", "verdict": "governed", "gaps": [],
    }))
    with pytest.raises(ValueError):
        mod.validate_collected(tmp_path, {
            "tenant": "tenant", "subscription": "subscription", "resource_group": "rg",
        }, "2026-09-06T00:00:00Z")


def test_generated_commands_use_actual_shared_apis():
    mod = helper()
    text = SCRIPT.read_text()
    for term in ("build_bundle(", "verify_bundle(", "validate_native_manifest(",
                 '"generate"', '"agent-image"', '"bind"',
                 '"--phase", "pre-deploy", "--emit", "--gate"',
                 '"azd", "provision"', '"azd", "deploy"',
                 '"--phase", "post-deploy"', '"--subscription"',
                 "validate_governance_manifest(", "assess(",
                 "started_at", "governance-live.json"):
        assert term in text, term
    assert callable(mod.validate_collected)


def test_missing_current_deployment_attempt_is_not_reusable(tmp_path):
    mod = helper()
    with pytest.raises(ValueError, match="deployment attempt"):
        mod.attempt_start(tmp_path)


def test_safe_check_result_is_published_without_a_second_host_invocation(tmp_path):
    mod = helper()
    assert hasattr(mod, "publish_postdeploy"), "safe-check returns collection inside postdeploy-manifest.json"
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    manifest, *_ = live_fixture()
    # Local protocol fixture only: validation/publication is not hosted proof.
    report = {"governance_manifest": manifest, "governance_gaps": [], "gaps": ["existing resource gap"]}
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/postdeploy-manifest.json").write_text(json.dumps(report))
    mod.publish_postdeploy(tmp_path)
    assert json.loads((tmp_path / ".threadlight/governance-live.json").read_text()) == report
    assert json.loads((tmp_path / "specs/governance-manifest.json").read_text()) == manifest


def test_repeated_collection_cannot_borrow_an_old_report(tmp_path):
    mod = helper()
    assert hasattr(mod, "publish_postdeploy")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/postdeploy-manifest.json").write_text('{"gaps":[]}')
    with pytest.raises(ValueError, match="collector"):
        mod.publish_postdeploy(tmp_path)


def test_predeploy_is_an_executed_gated_command_not_report_only(tmp_path, monkeypatch):
    mod = helper()
    commands = []
    monkeypatch.setattr(mod, "run", lambda args, **kwargs: commands.append(list(map(str, args))))
    mod.predeploy(tmp_path, {})
    assert commands[-1][-6:] == ["--target", str(tmp_path), "--phase", "pre-deploy", "--emit", "--gate"]
    assert any("run-governance-pin-tests.py" in c[1] for c in commands)
    assert any("--prepare-local" in c for c in commands)


def test_parent_scope_mismatch_prevents_provisioning(tmp_path, monkeypatch):
    mod = helper()
    calls = []
    def wrong_parent(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"id":"wrong","tenantId":"wrong"}')
    monkeypatch.setattr(mod.subprocess, "run", wrong_parent)
    with pytest.raises(ValueError, match="observed Azure parent"):
        mod.deploy(tmp_path, {"expected_target": {"subscription": "expected", "tenant": "expected"}})
    assert len(calls) == 1


def test_each_workflow_rerun_requires_its_own_completed_attempt(tmp_path, monkeypatch):
    mod = helper()
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    mod.write(tmp_path / mod.ATTEMPT, {
        "run_id": "100", "run_attempt": "1", "completed_at": "2026-09-06T00:00:00Z",
    })
    with pytest.raises(ValueError, match="deployment attempt"):
        mod.attempt_start(tmp_path)


def test_official_probe_opt_in_is_not_replaced_by_an_invented_shape():
    text = SCRIPT.read_text()
    assert '"/mnt/governance-probe/config.json"' in text


def input_fixture(tmp_path):
    """Test-only configuration: never presented as signed/live deployment evidence."""
    from skills._shared.tests.governance_consumer_fixtures import contract
    source = tmp_path / "source"
    (source / "specs").mkdir(parents=True)
    (source / "specs/governance-contract.json").write_text(json.dumps(contract(selected=True)))
    (tmp_path / "policy").mkdir()
    (tmp_path / "envelope.json").write_text("{}")
    (tmp_path / "fixture.json").write_text("{}")
    target = {"tenant": "11111111-1111-4111-8111-111111111111",
              "subscription": "22222222-2222-4222-8222-222222222222", "resource_group": "rg-test"}
    import hashlib
    config = {
        "schema": "threadlight-readiness-input/v1", "environment": "preproduction",
        "expected_target": target, "source_project": "source", "policy_source": "policy",
        "package": {"tenant_id": target["tenant"], "environment": "preproduction",
                    "signed_envelope": "envelope.json", "probe_observability": {
                        "enabled": True, "configuration_file": "/mnt/governance-probe/config.json"}},
        "agent_image": {"agent_image": "registry.azurecr.io/agent@sha256:" + "a" * 64},
        "deployment": {"infrastructure": {"environment": "preproduction"},
                       "images": {"agent": "test"}, "bindings": {"agent_version": "test"},
                       "observations": {"foundation": "test"}},
        "probe_input": {"selection": {"subscription": target["subscription"], "resource_group": target["resource_group"]}},
        "probe_files": {"fixtures/config.json": {"path": "fixture.json",
                         "sha256": hashlib.sha256(b"{}").hexdigest()}},
        "source_digests": {"source_project": "sha256:" + "a" * 64},
        "azd_environment": "test", "location": "test",
    }
    path = tmp_path / "protected.json"
    path.write_text(json.dumps(config))
    return path, config


@pytest.mark.parametrize("mutation", ["off", "bad-selected", "tenant", "subscription", "rg", "fixture", "escape"])
def test_selected_inputs_fail_closed_without_changing_scope(mutation, tmp_path):
    mod = helper()
    path, config = input_fixture(tmp_path)
    if mutation in {"off", "bad-selected"}:
        from skills._shared.tests.governance_consumer_fixtures import contract
        document = contract() if mutation == "off" else {"governance": {"mode": "selective"}}
        (tmp_path / "source/specs/governance-contract.json").write_text(json.dumps(document))
    elif mutation == "tenant":
        config["package"]["tenant_id"] = "33333333-3333-4333-8333-333333333333"
    elif mutation in {"subscription", "rg"}:
        config["probe_input"]["selection"]["resource_group" if mutation == "rg" else mutation] = "wrong"
    elif mutation == "fixture":
        (tmp_path / "fixture.json").write_text('{"changed":true}')
    else:
        config["probe_files"]["../escape.json"] = config["probe_files"].pop("fixtures/config.json")
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="protected configuration"):
        mod.load_inputs(path)


def test_complete_input_transport_is_accepted_but_is_not_an_enforcement_claim(tmp_path):
    mod = helper()
    path, config = input_fixture(tmp_path)
    loaded = mod.load_inputs(path)
    assert loaded["expected_target"] == config["expected_target"]
    assert "live" not in loaded and "signature_verified" not in loaded


def test_workflow_input_script_rejects_modified_protected_bytes(tmp_path):
    path, _ = input_fixture(tmp_path)
    step = next(s for s in workflow()["jobs"]["readiness-proof"]["steps"] if s.get("id") == "governance-inputs")
    (tmp_path / "python").symlink_to(sys.executable)
    env = {**os.environ, "GOVERNANCE_SAFE_PROBE": "true", "GOVERNANCE_CI_CONFIG": str(path),
           "GOVERNANCE_CI_CONFIG_SHA256": "0" * 64, "GOV_PROJECT": str(tmp_path / "pilot"),
           "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "SHA256 required/mismatched" in result.stderr


def test_deploy_uses_actual_generated_agent_path_and_completes_only_after_azd(tmp_path, monkeypatch):
    mod = helper()
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    agent = tmp_path / "src/custom-agent"
    for directory in (agent, tmp_path / "src/govern-control-plane", tmp_path / "src/govern-gateway"):
        directory.mkdir(parents=True)
        (directory / "entry.py").write_text("# reviewed source\n")
    monkeypatch.setattr(generator, "frozen_configuration", lambda *args: (agent, {}))
    mod.write(tmp_path / ".threadlight/governance-package.json", {"configuration": {}})
    config = {"expected_target": {"tenant": "test", "subscription": "test", "resource_group": "rg-test"},
              "agent_image": {}, "deployment": {}, "azd_environment": "test", "location": "test",
              "package": {"agent_service": "actual-agent"},
              "source_digests": {name: generator.tree_digest(directory) for name, directory in (
                  ("agent", agent), ("govern-control-plane", tmp_path / "src/govern-control-plane"),
                  ("govern-gateway", tmp_path / "src/govern-gateway"))}}
    commands = []
    monkeypatch.setattr(mod, "account_matches", lambda *args: None)
    monkeypatch.setattr(mod, "generate", lambda stage, *args: commands.append([stage]))
    monkeypatch.setattr(mod, "run", lambda args, **kwargs: commands.append(list(map(str, args))))
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    mod.deploy(tmp_path, config)
    assert commands[:2] == [["agent-image"], ["bind"]]
    assert commands[-2:] == [["azd", "provision", "--no-prompt"],
                            ["azd", "deploy", "actual-agent", "--no-prompt"]]
    assert mod.attempt_start(tmp_path)


@pytest.mark.parametrize("mutation", ["none", "business", "prior-deployment", "signed-envelope", "failed-parent"])
def test_current_shared_readiness_consumer_is_used_not_just_schema(mutation, tmp_path, monkeypatch):
    """Real v1 validator/consumer with local wire fixtures and no remote transports."""
    from copy import deepcopy
    from datetime import timedelta
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    import skills._shared.governance_readiness as readiness
    mod = helper()
    value, document, current, now = live_fixture()
    target = {k: current["expected_target"][k] for k in ("tenant", "subscription", "resource_group")}
    after = now - timedelta(seconds=5)
    if mutation == "business":
        document["tools"][0]["id"] = "returns_apply_decision"
    elif mutation == "prior-deployment":
        after = now + timedelta(seconds=5)
    elif mutation == "signed-envelope":
        current = deepcopy(current)
        current["policy_bindings"]["policy"]["signature"] = "changed"
    mod.write(tmp_path / "specs/governance-contract.json", document)
    mod.write(tmp_path / "specs/governance-manifest.json", value)
    mod.write(tmp_path / ".threadlight/governance-live.json", {
        "governance_manifest": value, "governance_gaps": ["failure"] if mutation == "failed-parent" else [],
    })
    monkeypatch.setattr(readiness, "current_context", lambda *args: current)
    if mutation == "none":
        mod.validate_collected(tmp_path, target, after.isoformat())
    else:
        with pytest.raises(ValueError):
            mod.validate_collected(tmp_path, target, after.isoformat())
