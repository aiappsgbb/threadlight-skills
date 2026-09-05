"""Static gates use the actual Task10 generator, not a claimed adapter boolean."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
for directory in ("threadlight-govern/tests", "threadlight-deploy/tests",
                  "threadlight-safe-check/scripts", "threadlight-safe-check/references"):
    sys.path.insert(0, str(ROOT / "skills" / directory))

import safe_check


def reference(name):
    path = ROOT / "skills/threadlight-safe-check/references" / (name + ".py")
    assert path.exists(), f"missing implemented {name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def empty_parent_azure(*args, **kwargs):
    if args[:2] == ("account", "show"):
        return json.dumps({"id": "11111111-1111-1111-1111-111111111111",
                           "tenantId": "11111111-1111-1111-1111-111111111111"})
    return "[]"


def generated(tmp_path):
    from test_governance_quality import inputs
    from test_governance_wiring import module
    project, contract, config, deployment, signer = inputs(tmp_path, environment="preproduction")
    module("generate").generate(project, contract, configuration=config)
    manifest = {**contract, "deployment_manifest": {
        "module_selectors": {}, "services": [
            {"name": name, "host": "containerapp", "src": "src/" + name}
            for name in ("govern-control-plane", "govern-gateway")],
        "expected_resource_types": []}}
    (project / "specs").mkdir()
    path = project / "specs/manifest.json"
    path.write_text(json.dumps(manifest))
    return project, manifest, path, config, deployment, signer


def test_selected_binding_missing_runtime_adapter_is_gap(tmp_path):
    project, _, path, *_ = generated(tmp_path)
    (project / "src/agent/runtime/maf_agent_hooks_acs.py").unlink()
    output = project / "gate.json"
    assert safe_check.phase_predeploy(project, path, output) == 1
    assert "selected binding has no runtime adapter" in " ".join(json.loads(output.read_text())["gaps"])


def test_intentionally_unbound_read_tool_is_not_gap(tmp_path):
    project, document, path, *_ = generated(tmp_path)
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == []
    assert result["stage"] == "pre-image"
    assert "image-not-built" in result["unverified"]
    assert all(binding["status"] != "enforced" for binding in result["bindings"])
    assert next(b for b in result["bindings"] if b["tool_id"] == "read")["status"] == "unbound"
    assert safe_check.phase_predeploy(project, path, project / "predeploy.json") == 0


@pytest.mark.parametrize("change", ["adapter", "bundle", "config", "service", "requires", "image", "docker", "bundle-loader"])
def test_static_tamper_is_actionable_gap(tmp_path, change):
    project, document, _, config, deployment, _ = generated(tmp_path)
    if change == "adapter":
        (project / "src/agent/container.py").write_text("# no host")
    elif change == "bundle":
        (project / "src/agent/policy/safe.rego").write_text("package bypass")
    elif change == "config":
        path = project / "src/agent/governance-config.json"
        value = json.loads(path.read_text())
        value["control_plane_url"] = "https://other.example"
        path.write_text(json.dumps(value))
    elif change == "service":
        (project / "src/govern-control-plane/vendor/control-plane/app.py").unlink()
    elif change == "requires":
        document["tools"][0]["requires"] = ["approval"]
    elif change == "docker":
        docker = project / "src/agent/Dockerfile"
        docker.write_text(docker.read_text() + "\nRUN echo bypass > /app/container.py\n")
    elif change == "bundle-loader":
        (project / "src/agent/govern_bundle/policy_bundle.py").write_text("# bypass")
    else:
        from test_governance_wiring import module
        module("generate").agent_image(project, {k: document[k] for k in ("framework", "governance", "tools")}, configuration={
            "agent_image": deployment["images"]["agent"], "spool_directory": "/mnt/audit"})
        path = project / "azure.yaml"
        path.write_text(path.read_text().replace("a" * 64, "b" * 64, 1))
    result = reference("governance_static").check(project, document)
    assert result["gaps"] and all(type(gap) is str for gap in result["gaps"])
    assert not any(b["status"] == "enforced" for b in result["bindings"])


def test_governance_off_and_absent_are_true_noops(tmp_path):
    gate = reference("governance_static")
    for document in ({}, {"governance": {"mode": "off"}}):
        assert gate.check(tmp_path, document) == {}


def test_predeploy_keeps_existing_non_governance_gaps(tmp_path):
    project, document, path, *_ = generated(tmp_path)
    document["deployment_manifest"]["module_selectors"]["aca-bot"] = "yes"
    path.write_text(json.dumps(document))
    output = project / "gate.json"
    assert safe_check.phase_predeploy(project, path, output) == 1
    value = json.loads(output.read_text())
    assert any("aca-bot" in gap for gap in value["gaps"])
    assert value["governance_health"]["scope"] == "static-declarations-not-enforcement"


def test_postdeploy_no_contract_never_automatically_invokes_and_keeps_gaps(tmp_path, monkeypatch):
    project, document, path, *_ = generated(tmp_path)
    document["deployment_manifest"]["expected_resource_types"] = ["Microsoft.Storage/storageAccounts"]
    path.write_text(json.dumps(document))
    monkeypatch.setattr(safe_check, "_az", empty_parent_azure)
    output = project / "post.json"
    assert safe_check.phase_postdeploy(path, output, "staging", repo_root=project) == 1
    report = json.loads(output.read_text())
    assert any("Microsoft.Storage/storageAccounts" in gap for gap in report["gaps"])
    assert report["governance_probes"] == []
    assert report["governance_gaps"]
    assert all("enforced" != b["status"] for b in report["governance_health"]["bindings"])


def test_collector_cli_is_runnable_and_off_is_no_network_noop(tmp_path):
    probe = reference("governance_probe")
    import asyncio
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/manifest.json").write_text(json.dumps({"governance": {"mode": "off"}}))
    def prohibited(*args, **kwargs):
        pytest.fail("off mode must not touch external dependencies")
    assert asyncio.run(probe.collect_project(tmp_path, tmp_path / "absent.json",
                                             run=prohibited)) == {}
    script = ROOT / "skills/threadlight-safe-check/references/governance_probe.py"
    result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--project" in result.stdout and "--configuration" in result.stdout


def test_collect_project_has_no_automatic_probe_without_configuration(tmp_path):
    import asyncio
    project, _, *_ = generated(tmp_path)
    probe = reference("governance_probe")
    result = asyncio.run(probe.collect_project(
        project, project / "absent.json", run=lambda *args: pytest.fail("no declared probe")))
    assert result["governance_gaps"] and result["governance_probes"] == []


def test_skill_and_ci_cover_real_governance_not_local_badges():
    skill = (ROOT / "skills/threadlight-safe-check/SKILL.md").read_text()
    assert "probe_safe: true" in skill
    assert "governance_probe_noop" in skill
    assert "cannot certify business bindings" in skill
    assert "--force" in skill
    runner = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    for selector in ("skills/threadlight-safe-check/tests",
                     "test_collector_actual_copilot_invocations_mcp_gateway_fixture",
                     "test_postdeploy_runs_real_collector_and_retains_original_gaps"):
        assert selector in runner


@pytest.mark.parametrize("malformed", [None, False, [], "selective"])
def test_invalid_governance_is_a_gap_not_an_uncaught_exception(tmp_path, malformed):
    project, document, path, *_ = generated(tmp_path)
    document["governance"] = malformed
    path.write_text(json.dumps(document))
    output = project / "bad.json"
    assert safe_check.phase_predeploy(project, path, output) == 1
    assert any("governance" in gap for gap in json.loads(output.read_text())["gaps"])
