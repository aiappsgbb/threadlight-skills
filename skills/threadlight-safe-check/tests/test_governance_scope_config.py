"""Task11 scope/config regressions using generated projects and real noop flows."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json

import pytest
import yaml

from test_governance_gates import empty_parent_azure, generated, reference, safe_check
from test_governance_probe import native_collector_harness, packaged_collector_project


@pytest.mark.parametrize("field", ["GOV_CONTROL_PLANE_URL", "GOVERNED_TOOL_GATEWAY_URL"])
@pytest.mark.parametrize("location", ["env", "environmentVariables", "legacy"])
@pytest.mark.governance_runtime
def test_generated_host_wiring_must_match_before_image(tmp_path, field, location):
    project, _, manifest, *_ = generated(tmp_path)
    path = project / "azure.yaml"
    azure = yaml.safe_load(path.read_text())
    service = azure["services"]["agent"]
    if location == "env":
        service["env"][field] = "https://wrong.example"
    elif location == "environmentVariables":
        service["environmentVariables"] = [{"name": field, "value": "https://wrong.example"}]
    else:
        (project / "agent.yaml").write_text(yaml.safe_dump({
            "environment_variables": {**service["env"], field: "https://wrong.example"}}))
    path.write_text(yaml.safe_dump(azure))
    before = path.read_bytes()
    assert safe_check.phase_predeploy(project, manifest, project / "gate.json") == 1
    assert path.read_bytes() == before


@pytest.mark.governance_runtime
def test_normal_environment_and_unknown_governance_secret_are_not_configuration(tmp_path):
    project, document, _, *_ = generated(tmp_path)
    path = project / "azure.yaml"
    azure = yaml.safe_load(path.read_text())
    azure["services"]["agent"]["env"].update(MY_SETTING="different", GOV_API_KEY="PRIVATE")
    path.write_text(yaml.safe_dump(azure))
    result = reference("governance_static").check(project, document)
    assert result["gaps"] == []
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("field", ["GOV_CONTROL_PLANE_URL", "GOVERNED_TOOL_GATEWAY_URL"])
@pytest.mark.governance_runtime
def test_actual_foundry_configuration_wrong_before_or_drifting_after(tmp_path, monkeypatch, when, field):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            environment = h.run.version["definition"]["environment_variables"]
            environment.update(yaml.safe_load((project / "azure.yaml").read_text())["services"]["agent"]["env"])
            calls = 0
            def run(command):
                nonlocal calls
                if any("/versions/1?" in arg for arg in command):
                    calls += 1
                    if when == "before" or calls >= 3:
                        environment[field] = "https://wrong.example/PRIVATE?token=PRIVATE"
                return h.run(command)
            probe = reference("governance_probe")
            report = await probe.collect_project(project, config, credential=h.credential,
                signer=h.signer, run=run, http=h.http, timeout=8)
            expected_gap = "observed-host-configuration-mismatch" if when == "before" else "deployment-changed-during-collection"
            assert report["governance_gaps"] == [expected_gap], report
            assert not report["governance_probes"]
            assert not any(b["status"] == "enforced" for b in report["governance_health"]["bindings"])
            assert "PRIVATE" not in json.dumps(report)
            if when == "before":
                assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
            else:
                assert any(".services.ai.azure.com" in host for _, host, _ in h.calls)
    asyncio.run(case())


@pytest.mark.parametrize("field", ["GOV_CONTROL_PLANE_URL", "TL_GOV_SPOOL_DIR"])
@pytest.mark.governance_runtime
def test_missing_or_unresolved_required_host_configuration_is_unverified(tmp_path, monkeypatch, field):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            env = yaml.safe_load((project / "azure.yaml").read_text())["services"]["agent"]["env"]
            h.run.version["definition"]["environment_variables"] = {
                **env, field: {"secretRef": "PRIVATE"}}
            report = await reference("governance_probe").collect_project(project, config,
                credential=h.credential, signer=h.signer, run=h.run, http=h.http, timeout=8)
            assert report["governance_gaps"]
            assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
            assert "PRIVATE" not in json.dumps(report)
    asyncio.run(case())


@pytest.mark.parametrize("entry", ["cli", "phase", "project", "direct"])
@pytest.mark.governance_runtime
def test_parent_target_scope_refuses_without_invocation_and_preserves_inputs(tmp_path, monkeypatch, entry):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            probe = reference("governance_probe")
            original = deepcopy(h.config)
            kwargs = dict(credential=h.credential, signer=h.signer, run=h.run, http=h.http, timeout=8)
            if entry in ("phase", "cli"):
                monkeypatch.setattr(safe_check, "_az", empty_parent_azure)
                output = project / "post.json"
                if entry == "cli":
                    import sys
                    import governance_references.governance_probe as installed
                    original_collect = installed.collect_project
                    async def local_dependencies(*args, **options):
                        return await original_collect(*args, **{**options, **kwargs})
                    monkeypatch.setattr(installed, "collect_project", local_dependencies)
                    monkeypatch.chdir(project)
                    monkeypatch.setattr(sys, "argv", ["safe-check", "--phase", "post-deploy", "--rg", "different-rg"])
                    result = await asyncio.to_thread(safe_check.main)
                    output = project / "tests/postdeploy-manifest.json"
                else:
                    result = await asyncio.to_thread(safe_check.phase_postdeploy,
                        project / "specs/manifest.json", output, "different-rg", project, kwargs)
                assert result == 1
                report = json.loads(output.read_text())
                assert "missing resource type: Microsoft.Storage/storageAccounts" in report["gaps"]
                assert report["rg"] == "different-rg"
            elif entry == "project":
                report = await probe.collect_project(project, config,
                    required_target={"resource_group": "different-rg"}, **kwargs)
            else:
                report = await probe.collect(h.config, required_target={"resource_group": "different-rg"}, **kwargs)
            assert report["governance_gaps"]
            assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
            assert h.config == original
    asyncio.run(case())


@pytest.mark.parametrize("field", ["agent_id", "agent_version", "environment", "subscription", "resource_group"])
@pytest.mark.governance_runtime
def test_reusable_evidence_rejects_target_transplant(tmp_path, monkeypatch, field):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            report = await reference("governance_probe").collect(h.config, credential=h.credential,
                signer=h.signer, run=h.run, http=h.http, timeout=8)
            assert report["governance_gaps"] == []
            from skills._shared.governance import validate_governance_manifest, GovernanceContractError
            manifest = deepcopy(report["governance_manifest"])
            # Expected scope is independent of saved producer counters and receipts.
            expected = deepcopy(h.native.registry.deployment.model_dump(mode="json"))
            expected.update(tenant=report["observed_target"]["tenant"],
                            subject=report["observed_target"]["subject"],
                            client_id=report["observed_target"]["client_id"])
            expected[field] = "production" if field == "environment" else "different"
            from skills._shared.probe_evidence import evaluate_pair, ProbeEvidenceError
            with pytest.raises(ProbeEvidenceError):
                evaluate_pair(report["probe_evidence"], target=report["observed_target"],
                    expected_target=expected, registration_scope=report["registration_scope"],
                    started_at=datetime.fromisoformat(report["started_at"]),
                    finished_at=datetime.fromisoformat(report["finished_at"]))
            manifest["collection_evidence"]["observed_target"][field] = expected[field]
            with pytest.raises(GovernanceContractError):
                validate_governance_manifest(manifest)
    asyncio.run(case())


@pytest.mark.parametrize("field", ["approver_roles", "workloads", "key_id"])
@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.governance_runtime
def test_actual_arm_service_configuration_binds_roles_policy_and_endpoints(tmp_path, monkeypatch, field, when):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            declaration = json.loads((project / ".threadlight/governance-deployment.json").read_text())
            settings = declaration["bindings"]["control_config"]
            rid = h.config["services"]["control_plane"]["resource_id"]
            container = h.run.resources[rid]["properties"]["template"]["containers"][0]
            container["env"] = [
                {"name": "TL_GOV_SERVICE", "value": "control-plane"},
                {"name": "AZURE_CLIENT_ID", "value": h.config["services"]["control_plane"]["client_id"]},
                {"name": "GOV_CONFIG_JSON", "value": json.dumps(settings)},
                {"name": "GOV_API_KEY", "value": "PRIVATE"}]
            reads = 0
            def run(command):
                nonlocal reads
                if rid in command:
                    reads += 1
                    if when == "before" or reads >= 2:
                        changed = deepcopy(settings)
                        if field == "approver_roles":
                            changed[field] = ["OtherApprover"]
                        elif field == "workloads":
                            next(iter(changed[field].values()))["policies"] = ["other-policy"]
                        else:
                            changed[field] = changed[field].replace("/keys/", "/keys/different-")
                        container["env"][2]["value"] = json.dumps(changed)
                return h.run(command)
            report = await reference("governance_probe").collect_project(project, config,
                credential=h.credential, signer=h.signer, run=run, http=h.http, timeout=8)
            expected_gap = "observed-service-configuration-mismatch" if when == "before" else "service-changed-during-collection"
            assert report["governance_gaps"] == [expected_gap], report
            assert not report["governance_probes"]
            assert "PRIVATE" not in json.dumps(report)
            if when == "before":
                assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
    asyncio.run(case())


def test_observer_closed_allowlist_retains_only_digests_and_detects_changes():
    from test_governance_observation import ARMFoundry, selection
    observer = reference("governance_observation")
    run = ARMFoundry()
    env = run.version["definition"]["environment_variables"]
    env.update(GOV_CONTROL_PLANE_URL="https://control.example/one",
               GOV_API_KEY="PRIVATE", BUSINESS_PASSWORD="PRIVATE")
    before = observer.observe(selection(), run)
    env["GOV_CONTROL_PLANE_URL"] = "https://control.example/two"
    after = observer.observe(selection(), run)
    assert before != after
    assert "PRIVATE" not in json.dumps(before)
    assert "https://control.example" not in json.dumps(before)
    env["GOV_API_KEY"] = "different-secret"
    assert after == observer.observe(selection(), run)


@pytest.mark.parametrize("change", ["roles", "policy", "scope", "endpoint"])
@pytest.mark.governance_runtime
def test_real_generated_service_environment_overrides_cannot_change_frozen_configuration(tmp_path, monkeypatch, change):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, _ = packaged_collector_project(tmp_path, h)
            deployment = json.loads((project / ".threadlight/governance-deployment.json").read_text())
            settings = deepcopy(deployment["bindings"]["control_config"])
            if change == "roles":
                settings["approver_roles"] = ["Other"]
            elif change == "policy":
                next(iter(settings["workloads"].values()))["policies"] = ["other"]
            elif change == "scope":
                next(iter(settings["workloads"].values()))["scopes"] = ["other"]
            else:
                settings["blob_url"] = "https://other.blob.core.windows.net"
            path = project / "azure.yaml"
            azure = yaml.safe_load(path.read_text())
            azure["services"]["govern-control-plane"]["env"] = {"GOV_CONFIG_JSON": json.dumps(settings)}
            path.write_text(yaml.safe_dump(azure))
            before = path.read_bytes()
            assert safe_check.phase_predeploy(project, project / "specs/manifest.json", project / "pre.json") == 1
            assert path.read_bytes() == before
    asyncio.run(case())


@pytest.mark.parametrize("field", ["agent_id", "agent_version", "environment", "subscription", "resource_group"])
@pytest.mark.governance_runtime
def test_known_direct_target_mismatch_is_refused_before_invocation(tmp_path, monkeypatch, field):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            h.config["expected_deployment"][field] = "production" if field == "environment" else "other"
            original = deepcopy(h.config)
            report = await reference("governance_probe").collect(h.config,
                credential=h.credential, signer=h.signer, run=h.run, http=h.http, timeout=8, force=True)
            assert report["governance_gaps"]
            assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
            assert h.config == original
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_declared_file_digest_input_cannot_export_payloads(tmp_path, monkeypatch):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            h.config["declared_file_digests"] = {"host": "PRIVATE", "GOV_API_KEY": "PRIVATE"}
            report = await reference("governance_probe").collect(h.config,
                credential=h.credential, signer=h.signer, run=h.run, http=h.http, timeout=8)
            assert "PRIVATE" not in json.dumps(report)
            assert report["governance_gaps"]
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_unbound_service_config_override_is_not_silently_accepted(tmp_path):
    project, document, _, *_ = generated(tmp_path)
    path = project / "azure.yaml"
    azure = yaml.safe_load(path.read_text())
    azure["services"]["govern-control-plane"]["env"] = {"GOV_CONFIG_JSON": '{"audience":"wrong"}'}
    path.write_text(yaml.safe_dump(azure))
    assert reference("governance_static").check(project, document)["gaps"]


def test_collector_reference_documents_exact_scope_and_configuration_visibility():
    from test_governance_gates import ROOT
    text = (ROOT / "skills/threadlight-safe-check/references/governance-probe.md").read_text()
    for term in ("expected_target", "required_target", "configuration_digests",
                 "GOV_CONFIG_JSON", "mounted", "allowlist"):
        assert term in text


@pytest.mark.parametrize("framework", ["microsoft-agent-framework", "github-copilot-sdk"])
@pytest.mark.governance_runtime
def test_full_generated_bound_preflight_and_parameter_drift(tmp_path, framework):
    from test_governance_quality import inputs, package
    data = inputs(tmp_path, framework=framework, environment="preproduction")
    project, document, _, deployment, _ = data
    generator = package(data)
    generator.bind(project, document, configuration=deployment)
    gate = reference("governance_static")
    assert gate.check(project, document)["gaps"] == []
    parameters = project / "infra/main.parameters.json"
    value = json.loads(parameters.read_text())
    value["parameters"]["governanceBindings"]["value"]["control_config"]["approver_roles"] = ["OtherApprover"]
    parameters.write_text(json.dumps(value))
    before = parameters.read_bytes()
    assert gate.check(project, document)["gaps"] == ["governance: generated-service-configuration-mismatch"]
    assert parameters.read_bytes() == before


@pytest.mark.governance_runtime
def test_actual_native_flow_ignores_unselected_environment_changes_and_labels_file_visibility(tmp_path, monkeypatch):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            reads = 0
            def run(command):
                nonlocal reads
                if any("/versions/1?" in arg for arg in command):
                    reads += 1
                    h.run.version["definition"]["environment_variables"].update(
                        BUSINESS_SETTING=str(reads), GOV_API_KEY="PRIVATE-" + str(reads))
                return h.run(command)
            report = await reference("governance_probe").collect_project(project, config,
                credential=h.credential, signer=h.signer, run=run, http=h.http, timeout=8)
            assert report["governance_gaps"] == []
            assert report["governance_health"]["bindings"][0]["status"] == "enforced"
            assert "PRIVATE" not in json.dumps(report)
            assert "environment" not in report["observed_target"]
            evidence = report["configuration_evidence"]
            assert evidence["file_visibility"] == "image-and-mounted-file-interiors-not-observed-by-azure"
            assert set(evidence["declared_file_digests"]) == {"host", "fixture", "native_probe"}
            assert evidence["observed"] == evidence["declared"]
    asyncio.run(case())


@pytest.mark.parametrize("field", ["declared_file_digests", "services"])
@pytest.mark.governance_runtime
def test_shared_configuration_evidence_rejects_unbounded_payloads(tmp_path, monkeypatch, field):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            report = await reference("governance_probe").collect(h.config,
                credential=h.credential, signer=h.signer, run=h.run, http=h.http, timeout=8)
            assert report["governance_gaps"] == []
            manifest = deepcopy(report["governance_manifest"])
            configuration = manifest["collection_evidence"]["configuration"]
            if field == "declared_file_digests":
                configuration[field] = {"host": "PRIVATE"}
            else:
                for part in ("declared", "observed"):
                    configuration[part][field]["control_plane"]["GOV_API_KEY"] = "PRIVATE"
            from skills._shared.governance import validate_governance_manifest, GovernanceContractError
            with pytest.raises(GovernanceContractError):
                validate_governance_manifest(manifest)
    asyncio.run(case())
