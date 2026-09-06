"""Explicit resume mode: command ordering is orchestration evidence, not Azure proof."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def implementation():
    path = ROOT / "scripts/ci/runtime_readiness_remote.py"
    assert path.exists(), "protected signed-bootstrap resume driver missing"
    spec = importlib.util.spec_from_file_location("runtime_readiness_remote", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_absent_or_ambiguous_creation_cannot_reach_mutation(tmp_path):
    lib = implementation()
    from test_hosted_bootstrap_lifecycle import inputs
    from govern_control_plane.models import canonical
    creation = inputs()
    for state in (None, {"schema": "threadlight-hosted-attempt/v1", "state": "creating",
                          "reference": creation["reference"],
                          "input_digest": "sha256:" + hashlib.sha256(canonical(creation)).hexdigest()}):
        path = tmp_path / "attempt.json"
        if state is not None:
            path.write_bytes(canonical(state))
        with pytest.raises(ValueError):
            lib.require_created(creation, path)
    assert list(tmp_path.iterdir()) == [tmp_path / "attempt.json"]


def test_remote_scope_comes_from_created_image_not_ambient_azd(monkeypatch):
    lib = implementation()
    from test_hosted_bootstrap_lifecycle import inputs
    creation = inputs()
    config = {
        "expected_target": {"tenant": creation["tenant_id"], "subscription": creation["subscription"],
                            "resource_group": creation["resource_group"]},
        "package": {"agent_id": creation["agent_name"], "remote_bootstrap": {
            "reference": creation["reference"], "project_endpoint": creation["project_endpoint"]}},
        "deployment": {"images": {"agent": creation["image"]},
                       "infrastructure": {"runtime": "github-copilot-sdk"}},
        "probe_input": {"selection": {"project_resource_id": creation["project_id"]}},
    }
    monkeypatch.setenv("AZURE_AI_PROJECT_ID", "wrong-ambient-project")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://wrong-ambient.example")
    lib.validate_selection(config, creation)
    for key in ("tenant_id", "subscription", "resource_group", "project_id", "project_endpoint", "image"):
        with pytest.raises(ValueError):
            lib.validate_selection(config, {**creation, key: "wrong"})


def test_legacy_driver_dispatches_only_explicit_remote_mode(monkeypatch, tmp_path):
    lib = implementation()
    from scripts.ci import runtime_readiness
    calls = []
    monkeypatch.setattr(lib, "deploy", lambda project, config: calls.append((project, config)))
    monkeypatch.setattr(runtime_readiness, "remote_driver", lambda: lib)
    config = {"remote_bootstrap": {"mode": "resume-signed-bootstrap/v1"}}
    runtime_readiness.deploy(tmp_path, config)
    assert calls == [(tmp_path, config)]
    with pytest.raises(ValueError, match="NEEDS_CONTEXT"):
        runtime_readiness.deploy(tmp_path, {"remote_bootstrap": {"mode": "off"}})
    assert len(calls) == 1


def test_protected_workflow_has_no_azd_resume_or_ambient_parser_environment():
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/threadlight-e2e-foundry.yml").read_text())
    steps = workflow["jobs"]["readiness-proof"]["steps"]
    before_login = steps[:next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("azure/login"))]
    scripts = "\n".join(step.get("run", "") for step in before_login)
    assert "python -m venv .governance-validation/config-venv" in scripts
    assert "./skills/threadlight-govern/references/control-plane" in scripts
    assert not any("azd auth" in step.get("run", "") for step in steps)


def test_reference_reinstall_clears_shared_namespace_owners_before_install(monkeypatch):
    path = ROOT / "scripts/ci/run-governance-pin-tests.py"
    spec = importlib.util.spec_from_file_location("bootstrap_pin_runner", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert callable(getattr(runner, "install_reference_wheels", None)), "reference namespace reinstall ordering missing"
    calls = []
    monkeypatch.setattr(runner, "run", lambda args, **kwargs: calls.append([str(arg) for arg in args]))
    wheels = [Path("control.whl"), Path("gateway.whl"), Path("collector.whl")]
    runner.install_reference_wheels(Path("python"), wheels)
    assert calls[0][1:4] == ["-m", "pip", "uninstall"]
    assert set(calls[0][6:]) == {
        "threadlight-govern-control-plane", "threadlight-govern-gateway",
        "threadlight-govern-probe-fixture", "threadlight-governance-safe-check"}
    assert calls[1][1:4] == ["-m", "pip", "install"]
    assert calls[1][-3:] == [str(path) for path in wheels]


def test_protected_input_loader_accepts_only_pinned_acknowledged_resume(tmp_path):
    from test_runtime_readiness import input_fixture, helper
    from test_hosted_bootstrap_lifecycle import inputs
    from govern_control_plane.models import canonical
    from govern_control_plane.auth import Settings
    import importlib
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    path, config = input_fixture(tmp_path)
    creation = inputs()
    target = config["expected_target"]
    creation.update(tenant_id=target["tenant"], subscription=target["subscription"], resource_group=target["resource_group"],
                    project_id=f"/subscriptions/{target['subscription']}/resourceGroups/{target['resource_group']}"
                               "/providers/Microsoft.CognitiveServices/accounts/test/projects/test")
    key = "https://test.vault.azure.net/keys/policy/" + "a" * 32
    config["package"].update(agent_id=creation["agent_name"], key_id=key, remote_bootstrap={
        "reference": creation["reference"], "project_endpoint": creation["project_endpoint"]})
    config["deployment"]["images"]["agent"] = creation["image"]
    config["deployment"]["infrastructure"]["runtime"] = "github-copilot-sdk"
    config["probe_input"]["selection"]["project_resource_id"] = creation["project_id"]
    publisher = {
        "tenant_id": target["tenant"], "key_id": key, "audience": "api://test",
        "workloads": {"33333333-3333-3333-3333-333333333333": {
            "client_id": "44444444-4444-4444-4444-444444444444", "agent_id": creation["agent_name"], "policies": ["safe"]}},
        "human_clients": ["55555555-5555-5555-5555-555555555555"],
        "approver_subjects": ["66666666-6666-6666-6666-666666666666"],
        "auditor_subjects": ["66666666-6666-6666-6666-666666666666"], "approver_roles": ["Approver"],
        "blob_url": "https://test.blob.core.windows.net", "blob_container": "bundles",
        "cosmos_url": "https://test.documents.azure.com:443/", "cosmos_database": "governance",
        "cosmos_container": "records"}
    attempt = {"schema": "threadlight-hosted-attempt/v1", "state": "created", "reference": creation["reference"],
               "input_digest": "sha256:" + hashlib.sha256(canonical(creation)).hexdigest(),
               "version": "17", "version_id": "fixture-only-version"}
    remote = {"mode": "resume-signed-bootstrap/v1"}
    for name, body in (("creation", creation), ("attempt", attempt), ("publisher", publisher), ("policy_envelope", {})):
        file = tmp_path / (name + ".json")
        file.write_bytes(canonical(body))
        remote[name] = {"path": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    remote["policy_bundle"] = {"path": "policy", "tree_digest": generator.tree_digest(tmp_path / "policy")}
    config["remote_bootstrap"] = remote
    path.write_text(json.dumps(config))
    loaded = helper().load_inputs(path)
    assert loaded["remote_bootstrap"]["creation_values"]["reference"] == creation["reference"]
    assert loaded["remote_bootstrap"]["attempt"] == tmp_path / "attempt.json"
    (tmp_path / "attempt.json").write_text("{}")
    with pytest.raises(ValueError, match="protected configuration"):
        helper().load_inputs(path)


def test_native_gate_runs_bootstrap_protocol_and_real_host_regressions():
    text = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    for name in ("test_bootstrap_assets.py", "test_remote_bootstrap.py",
                 "test_native_bootstrap_assets.py", "test_remote_bootstrap_hosts.py",
                 "test_hosted_bootstrap_lifecycle.py", "test_public_authenticated_proof.py"):
        assert name in text
    assert '"THREADLIGHT_READINESS_SDK": "1"' in text
