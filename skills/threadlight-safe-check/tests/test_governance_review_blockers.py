"""PR127 regressions: invalid selection is not off; supported native packaging is valid."""
import asyncio
import importlib.abc
import json
import shutil
import subprocess
import sys

import pytest

from test_governance_gates import ROOT, empty_parent_azure, reference, safe_check
from test_governance_wiring import contract, module


def off_contract():
    document = contract("microsoft-agent-framework")
    document["governance"]["mode"] = "off"
    document["governance"]["lifecycle_bindings"] = []
    document["tools"] = [document["tools"][1]]
    return document


def project_manifest(project, document, *, custom=False):
    path = project / ("nested/custom.json" if custom else "specs/manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {**document, "deployment_manifest": {
        "module_selectors": {}, "services": [], "expected_resource_types": []}}
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize("fault", ["bound-tool", "lifecycle", "required", "duplicate", "symlink",
                                  "standalone", "spec", "spec-malformed", "spec-symlink",
                                  "custom", "malformed"])
def test_off_selector_cannot_hide_invalid_or_conflicting_sources(tmp_path, monkeypatch, fault):
    document = off_contract()
    if fault == "bound-tool":
        document["tools"] = contract("microsoft-agent-framework")["tools"]
    elif fault == "lifecycle":
        document["governance"]["lifecycle_bindings"] = [{"lifecycle_point": "startup"}]
    elif fault == "required":
        document = {"governance": {"mode": "off", "required": ["audit"]}}
    path, parent = project_manifest(tmp_path, document, custom=fault == "custom")
    selected = contract("microsoft-agent-framework")
    specs = tmp_path / "specs"
    specs.mkdir(exist_ok=True)
    if fault in ("standalone", "duplicate", "symlink", "malformed"):
        source = specs / "governance-contract.json"
        source.write_text(json.dumps(selected))
        if fault == "duplicate":
            source.write_text('{"governance":{"mode":"selective"},"governance":{"mode":"off"}}')
        elif fault == "malformed":
            source.write_text('{"PRIVATE configuration":')
        elif fault == "symlink":
            source.unlink()
            source.symlink_to(path)
    elif fault in ("spec", "spec-malformed", "spec-symlink"):
        import yaml
        (specs / "SPEC.md").write_text("```yaml\n" + yaml.safe_dump(selected) + "```\n")
        if fault == "spec-malformed":
            (specs / "SPEC.md").write_text("```yaml\ngovernance: [PRIVATE:\n```\n")
        elif fault == "spec-symlink":
            (specs / "SPEC.md").rename(tmp_path / "actual-spec.md")
            (specs / "SPEC.md").symlink_to(tmp_path / "actual-spec.md")
    elif fault == "custom":
        project_manifest(tmp_path, selected)
    (tmp_path / "azure.yaml").write_text("services: {}\n")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/main.bicep").write_text("")
    gate = reference("governance_static")
    assert gate.check(tmp_path, parent)["gaps"]
    assert safe_check.phase_predeploy(tmp_path, path, tmp_path / "pre.json") == 1
    monkeypatch.setattr(safe_check, "_az", empty_parent_azure)
    assert safe_check.phase_postdeploy(path, tmp_path / "post.json", "fixture", tmp_path) == 1
    post = json.loads((tmp_path / "post.json").read_text())
    assert post["governance_gaps"] and not post["governance_probes"]
    def prohibited(*args, **kwargs):
        pytest.fail("Invalid/off selection must not invoke credentials, Azure or a model")
    collected = asyncio.run(reference("governance_probe").collect_project(
        tmp_path, manifest_path=path, run=prohibited, force=True))
    assert collected["governance_gaps"] and not collected["governance_probes"]
    assert "PRIVATE" not in json.dumps((post, collected))


def test_in_memory_off_mutation_is_not_ignored(tmp_path):
    original = contract("microsoft-agent-framework")
    _, parent = project_manifest(tmp_path, original)
    parent["governance"]["mode"] = "off"
    assert safe_check._governance_enabled(parent)
    assert reference("governance_static").enabled(parent)
    assert reference("governance_static").check(tmp_path, parent)["gaps"]


@pytest.mark.parametrize("document", [{}, {"governance": {"mode": "off"}}, off_contract()])
def test_valid_off_and_absent_need_no_acs_or_credentials(tmp_path, monkeypatch, document):
    class NoRuntime(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"agent_compliance", "agent_framework", "azure", "govern_native"}:
                pytest.fail("Off governance imported " + fullname)
    monkeypatch.setattr(sys, "meta_path", [NoRuntime(), *sys.meta_path])
    path, parent = project_manifest(tmp_path, document)
    assert reference("governance_static").check(tmp_path, parent) == {}
    def prohibited(*args, **kwargs):
        pytest.fail("Off governance must not invoke external dependencies")
    assert asyncio.run(reference("governance_probe").collect_project(
        tmp_path, manifest_path=path, run=prohibited)) == {}


@pytest.mark.parametrize("document", [{}, {"governance": {"mode": "off"}}])
def test_copied_legacy_cli_remains_stdlib_only(tmp_path, document):
    script = tmp_path / "safe_check.py"
    shutil.copyfile(ROOT / "skills/threadlight-safe-check/scripts/safe_check.py", script)
    path, _ = project_manifest(tmp_path, document)
    (tmp_path / "azure.yaml").write_text("services: {}\n")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/main.bicep").write_text("")
    result = subprocess.run([sys.executable, "-I", "-S", str(script), "--phase", "pre-deploy"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_does_not_resolve_away_selected_manifest_symlink(tmp_path, monkeypatch):
    source, _ = project_manifest(tmp_path, off_contract(), custom=True)
    selected = tmp_path / "selected.json"
    selected.symlink_to(source)
    (tmp_path / "azure.yaml").write_text("services: {}\n")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/main.bicep").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["safe-check", "--phase", "pre-deploy",
                                     "--manifest", str(selected)])
    assert safe_check.main() == 1
    report = json.loads((tmp_path / "tests/safe-check-predeploy-manifest.json").read_text())
    assert report["governance_health"]["gaps"]


@pytest.mark.parametrize("consumer", ["legacy", "enabled", "selection"])
@pytest.mark.parametrize("error", [RuntimeError, AssertionError])
def test_selection_does_not_swallow_programming_failures(tmp_path, monkeypatch, consumer, error):
    from skills._shared import governance_selection
    path, parent = project_manifest(tmp_path, {})
    def broken(*args, **kwargs):
        raise error("programming-failure")
    gate = reference("governance_static")
    if consumer == "legacy":
        monkeypatch.setattr(type(path), "open", broken)
        operation = lambda: safe_check._legacy_governance_unselected(tmp_path, parent, path)
    else:
        monkeypatch.setattr(governance_selection, "load_contract", broken)
        operation = (lambda: gate.enabled(parent, tmp_path)) if consumer == "enabled" else (
            lambda: gate.check(tmp_path, parent))
    with pytest.raises(error, match="programming-failure"):
        operation()


@pytest.mark.parametrize("error", [ValueError, PermissionError, ImportError])
def test_expected_selection_failures_are_sanitized_and_fail_closed(tmp_path, monkeypatch, error):
    from skills._shared import governance_selection
    def unavailable(*args, **kwargs):
        raise error("PRIVATE configuration")
    monkeypatch.setattr(governance_selection, "load_contract", unavailable)
    gate = reference("governance_static")
    assert gate.enabled({}, tmp_path)
    result = gate.check(tmp_path, {})
    assert result["gaps"] == ["governance: governance-contract-invalid-or-conflicting"]
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("document", [None, [], False, "off", {"governance": {"mode": "selective"}}])
def test_invalid_parent_shapes_are_normalized_to_validation_errors(tmp_path, document):
    from skills._shared.governance_selection import parent_contract
    with pytest.raises(ValueError):
        parent_contract(document)
    gate = reference("governance_static")
    assert gate.enabled(document, tmp_path)
    assert gate.check(tmp_path, document)["gaps"]


@pytest.mark.parametrize("raw", ['[]', '{"governance":', '{"governance":{},"governance":{"mode":"off"}}'])
def test_legacy_fallback_refuses_parse_and_shape_failures(tmp_path, raw):
    path, parent = project_manifest(tmp_path, {})
    path.write_text(raw)
    assert not safe_check._legacy_governance_unselected(tmp_path, parent, path)


def test_looping_selected_symlink_is_a_sanitized_gap(tmp_path):
    path, parent = project_manifest(tmp_path, {"governance": {"mode": "off"}})
    source = path.parent / "governance-contract.json"
    source.symlink_to(source.name)
    result = reference("governance_static").check(tmp_path, parent)
    assert result["gaps"] == ["governance: governance-contract-invalid-or-conflicting"]
    assert not safe_check._legacy_governance_unselected(tmp_path, parent, path)


@pytest.mark.parametrize("fault", [None, "wrapper", "host", "missing-host", "wrapper-link", "host-link"])
@pytest.mark.governance_runtime
def test_real_generator_preserved_maf_wrapper_gate(tmp_path, fault):
    from test_governance_quality import inputs
    project, document, config, _, _ = inputs(tmp_path, environment="preproduction")
    gen = module("generate")
    agent = project / "src/agent"
    shutil.copyfile(gen.REFERENCE / "maf-entrypoint.py", agent / "container.py")
    gen.generate(project, document, configuration=config)
    assert (agent / "container.py").read_bytes() == (gen.REFERENCE / "maf-entrypoint.py").read_bytes()
    assert (agent / "governance_host.py").read_bytes() == (gen.REFERENCE / "maf-container.py").read_bytes()
    if fault in ("wrapper", "host"):
        (agent / ("container.py" if fault == "wrapper" else "governance_host.py")).write_text("# changed\n")
    elif fault == "missing-host":
        (agent / "governance_host.py").unlink()
    elif fault in ("wrapper-link", "host-link"):
        name = "container.py" if fault == "wrapper-link" else "governance_host.py"
        (agent / name).rename(agent / ("original-" + name))
        (agent / name).symlink_to(agent / ("original-" + name))
    result = reference("governance_static").check(project, document)
    assert bool(result["gaps"]) == (fault is not None), result
    assert all(b["status"] != "enforced" for b in result["bindings"])


@pytest.mark.parametrize("fault", [None, "separate-downstream", "service-client", "tenant", "workload",
                                  "fixture-client", "fixture-caller", "deployment", "controller",
                                  "unknown-mode", "platform-downstream", "probe-envelope",
                                  "probe-envelope-scope"])
@pytest.mark.governance_runtime
def test_real_platform_noop_static_association(tmp_path, monkeypatch, fault):
    from govern_control_plane.models import canonical, parse
    from govern_gateway.probe_runtime import ProbeConfiguration
    from test_governance_probe import (
        APP, WORKLOAD, native_collector_harness, packaged_collector_project,
    )
    async def scenario():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, options = packaged_collector_project(tmp_path, h)
            deployment_path = project / ".threadlight/governance-deployment.json"
            deployment = json.loads(deployment_path.read_text())
            binding = deployment["bindings"]
            producer = binding["native_probe_config"]
            producer.update(credential_mode="platform-noop", downstream_client_id=None)
            fixture_path = project / ".threadlight/fixture.json"
            fixture = json.loads(fixture_path.read_text())
            fixture["fixture_callers"] = {WORKLOAD: APP}
            other = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            if fault == "separate-downstream":
                producer.update(credential_mode="separate-managed-identity", downstream_client_id=other)
                fixture["fixture_callers"] = {binding["downstream_principal"]: binding["downstream_client"]}
            elif fault == "service-client":
                producer["service_client_id"] = other
            elif fault == "tenant":
                producer["tenant_id"] = other
            elif fault == "workload":
                producer["workloads"][WORKLOAD]["client_id"] = other
            elif fault == "fixture-client":
                fixture["service_client_id"] = APP
            elif fault == "fixture-caller":
                fixture["fixture_callers"] = {WORKLOAD: other}
            elif fault == "deployment":
                producer["expected_deployment"]["agent_version"] = "different"
            elif fault == "controller":
                producer["probe_controllers"] = {}
            elif fault == "unknown-mode":
                producer["credential_mode"] = "unknown"
            elif fault == "platform-downstream":
                producer["downstream_client_id"] = other
            elif fault in ("probe-envelope", "probe-envelope-scope"):
                envelope_path = project / ".threadlight/probe-envelope.json"
                signed = json.loads(envelope_path.read_text())
                if fault == "probe-envelope":
                    signed.pop("signature")
                else:
                    signed["envelope"]["tenant_id"] = other
                envelope_path.write_bytes(canonical(signed))
            deployment_path.write_bytes(canonical(deployment))
            fixture_path.write_bytes(canonical(fixture))
            probe = reference("governance_probe")
            if fault is None:
                real = parse(ProbeConfiguration, canonical(producer))
                assert real.credential_mode == "platform-noop" and real.downstream_client_id is None
                loaded = probe.load_configuration(project, options)
                _, registered, _ = probe.preflight(loaded)
                assert registered.native_policy_digest == binding["policy_digest"]
                assert registered.deployment.model_dump(mode="json") == producer["expected_deployment"]
                assert loaded["policy"]["signed"] == h.config["policy"]["signed"]
            elif fault in ("unknown-mode", "platform-downstream"):
                with pytest.raises(ValueError):
                    parse(ProbeConfiguration, canonical(producer))
            result = reference("governance_static").check(project, h.config["contract"])
            assert bool(result["gaps"]) == (fault is not None), result
            assert all(b["status"] != "enforced" for b in result["bindings"])
    asyncio.run(scenario())
