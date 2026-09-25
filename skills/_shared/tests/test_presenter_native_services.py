"""Synthetic native inventory fixtures, not ACA deployment or runtime evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from skills._shared import presenter
from skills._shared.tests.test_presenter import pilot, write, evidence


def native_pilot(root, process="returns", protocol="responses"):
    contract = pilot(root, process)
    (root / "azure.yaml").unlink()
    contract["deployment"].update(
        consumer="native-sdk", manifest="deploy/agent.json",
        create_entrypoint="deploy/create.py", service=f"{process}-agent", protocol=protocol,
    )
    write(root, "deploy/create.py", "retained native agent creator")
    write(root, "deploy/agent.json", {
        "kind": "hosted", "protocol_versions": [{"protocol": protocol, "version": "2.0.0"}],
        "environment_variables": {"AZURE_AI_MODEL_DEPLOYMENT_NAME": "fixture-model"},
    })
    write(root, "src/agent/Dockerfile", "FROM fixture-agent\n")
    inventory = {"services": [
        {"name": f"{process}-agent", "host": "azure.ai.agent", "src": "./src/agent"},
    ]}
    services = []
    for role in ("mcp", "workspace"):
        name = f"{process}-{role}"
        source = f"src/{role}"
        item = {
            "name": name, "resource_name": f"fixture-{name}", "host": "containerapp", "role": role,
            "src": ".", "consumer": "containerapp-arm",
            "manifest": f"deploy/{role}.json", "create_entrypoint": f"deploy/{role}-create.py",
            "lockfile": f"{source}/requirements.txt", "adapter": f"{source}/adapter.py",
            "dockerfile": f"deploy/{role}.Dockerfile", "inputs": [source],
        }
        services.append(item)
        inventory["services"].append({"name": name, "host": "containerapp", "src": "."})
        for key in ("create_entrypoint", "lockfile", "adapter", "dockerfile"):
            write(root, item[key], f"{process} {role} {key}")
        write(root, item["manifest"], {
            "type": "Microsoft.App/containerApps", "apiVersion": "2025-01-01",
            "name": item["resource_name"], "location": "fixture-region",
            "identity": {"type": "SystemAssigned"},
            "properties": {
                "managedEnvironmentId": "/fixture/environment",
                "configuration": {"ingress": {"external": False, "targetPort": 8080}},
                "template": {"containers": [
                    {"name": role, "image": "example.invalid/app@sha256:" + "a" * 64,
                     "resources": {"cpu": 0.5, "memory": "1Gi"}},
                ]},
            },
        })
    contract["deployment"]["ancillary_services"] = services
    write(root, presenter.CONTRACT, contract)
    write(root, "specs/manifest.json", {
        "delivery_profile": "presenter-ready", "deployment_manifest": inventory,
    })
    return contract, inventory


def assess(root):
    return presenter.assess(root, now=datetime(2026, 9, 24, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("process,protocol", [("returns", "responses"), ("maintenance", "invocations")])
def test_complete_native_inventory_keeps_incumbent(tmp_path, process, protocol):
    contract, inventory = native_pilot(tmp_path, process, protocol)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory, packaged=True) == []
    report = assess(tmp_path)
    assert report["states"]["source-ready"] == "verified"
    assert not report["ready"]
    assert report["checks"]["package"]["status"] == "pending"
    assert report["checks"]["deployment"]["status"] == "pending"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("fault", ["omitted", "extra", "duplicate", "host", "source",
                                  "jobs", "no-inventory", "agent-name", "unsupported-consumer"])
def test_inventory_rejects_omission_and_drift(tmp_path, fault):
    contract, inventory = native_pilot(tmp_path)
    if fault == "omitted":
        inventory["services"].pop()
    elif fault == "extra":
        inventory["services"].append({"name": "unknown", "host": "containerapp", "src": "."})
    elif fault == "duplicate":
        inventory["services"].append(deepcopy(inventory["services"][-1]))
    elif fault in ("host", "source"):
        inventory["services"][-1]["host" if fault == "host" else "src"] = "wrong"
    elif fault == "jobs":
        inventory["scheduled_jobs"] = [{"name": "unvalidated-job"}]
    elif fault == "no-inventory":
        inventory = None
    elif fault == "agent-name":
        contract["deployment"]["service"] = "another-agent"
    else:
        contract["deployment"]["ancillary_services"][0]["consumer"] = "unchecked"
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory, packaged=True)


@pytest.mark.parametrize("key", ["manifest", "create_entrypoint", "lockfile", "adapter", "dockerfile"])
def test_each_service_artifact_is_mandatory(tmp_path, key):
    contract, inventory = native_pilot(tmp_path)
    (tmp_path / contract["deployment"]["ancillary_services"][0][key]).unlink()
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory, packaged=True)


@pytest.mark.parametrize("key", ["manifest", "create_entrypoint", "lockfile", "adapter", "dockerfile", "inputs"])
def test_service_runtime_hash_covers_full_sources(tmp_path, key):
    contract, _ = native_pilot(tmp_path)
    before = presenter.fingerprints(tmp_path, contract)
    item = contract["deployment"]["ancillary_services"][0]
    path = item["inputs"][0] + "/new_module.py" if key == "inputs" else item[key]
    if key == "manifest":
        value = json.loads((tmp_path / path).read_text())
        value["properties"]["configuration"]["ingress"]["targetPort"] = 9000
        write(tmp_path, path, value)
    else:
        write(tmp_path, path, "changed")
    assert presenter.fingerprints(tmp_path, contract)["runtime"] != before["runtime"]


@pytest.mark.parametrize("fault", ["wrong-type", "wrong-name", "mutable-image", "sidecar", "no-identity"])
def test_native_aca_definition_shape_is_checked(tmp_path, fault):
    contract, inventory = native_pilot(tmp_path)
    item = contract["deployment"]["ancillary_services"][0]
    path = item["manifest"]
    definition = json.loads((tmp_path / path).read_text())
    if fault == "wrong-type":
        definition["type"] = "Microsoft.App/jobs"
    elif fault == "wrong-name":
        definition["name"] = "different"
    elif fault == "mutable-image":
        definition["properties"]["template"]["containers"][0]["image"] = "example.invalid/app:latest"
    elif fault == "sidecar":
        definition["properties"]["template"]["containers"].append(
            {"name": "unverified", "image": "example.invalid/extra:latest"})
    else:
        definition.pop("identity")
    write(tmp_path, path, definition)
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory)


def test_legacy_receipts_do_not_cover_new_services(tmp_path):
    contract, _ = native_pilot(tmp_path)
    evidence(tmp_path, contract)
    report = assess(tmp_path)
    assert report["states"]["source-ready"] == "verified"
    assert report["checks"]["package"]["status"] == "blocked"
    assert report["checks"]["deployment"]["status"] == "blocked"
    assert not report["ready"]


def service_evidence(root, contract):
    refs = evidence(root, contract)
    target = json.loads((root / presenter.TARGET).read_text())
    target["services"] = {}
    for item in contract["deployment"]["ancillary_services"]:
        target["services"][item["name"]] = {
            "attempt": f"{item['name']}-attempt-1", "environment": "fixture",
            "version": "revision-1", "identity": f"{item['name']}-identity",
            "image": "example.invalid/app@sha256:" + "a" * 64,
            "resource_name": item["resource_name"],
            "definition_sha256": presenter.sha256((root / item["manifest"]).read_bytes()),
        }
    write(root, presenter.TARGET, target)
    for check, ref in refs.items():
        value = json.loads((root / ref["path"]).read_text())
        if check != "package":
            value["target"] = deepcopy(target)
        if check in ("package", "deployment"):
            value["facts"]["services"] = {}
            for item in contract["deployment"]["ancillary_services"]:
                output = write(root, f"evidence/{check}-{item['name']}.txt",
                               f"Synthetic {check} output, not executed image/hosted proof: {item['name']}")
                value["facts"]["services"][item["name"]] = {
                    **{k: True for k in presenter.SERVICE_PACKAGE_CASES},
                    "observed": True,
                    "manifest_sha256": presenter.sha256((root / item["manifest"]).read_bytes()),
                    "adapter_sha256": presenter.sha256((root / item["adapter"]).read_bytes()),
                    "evidence": [{"path": output.relative_to(root).as_posix(),
                                  "sha256": presenter.sha256(output.read_bytes())}],
                }
        value["predecessors"] = {k: refs[k]["sha256"] for k in presenter.PREDECESSORS[check]}
        ref["sha256"] = presenter.sha256(write(root, ref["path"], value).read_bytes())
    write(root, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    return refs, target


@pytest.mark.parametrize("process,protocol", [("returns", "responses"), ("maintenance", "invocations")])
def test_recorded_per_service_proof_uses_existing_levels(tmp_path, process, protocol):
    contract, _ = native_pilot(tmp_path, process, protocol)
    service_evidence(tmp_path, contract)
    report = assess(tmp_path)
    assert report["ready"]
    assert report["evidence_authority"] == "recorded-not-independently-attested"
    assert set(report["checks"]) == set(presenter.CHECKS)


@pytest.mark.parametrize("field", ["attempt", "environment", "version", "identity", "image",
                                  "resource_name", "definition_sha256"])
def test_changed_ancillary_target_invalidates_hosted_proof(tmp_path, field):
    contract, _ = native_pilot(tmp_path)
    _, target = service_evidence(tmp_path, contract)
    target["services"]["returns-mcp"][field] = (
        "example.invalid/app@sha256:" + "b" * 64 if field == "image"
        else "b" * 64 if field == "definition_sha256" else "changed")
    write(tmp_path, presenter.TARGET, target)
    report = assess(tmp_path)
    assert report["checks"]["package"]["status"] == "verified"
    assert report["checks"]["deployment"]["status"] in ("stale", "blocked")
    assert not report["ready"]


@pytest.mark.parametrize("check", ["package", "deployment"])
@pytest.mark.parametrize("fault", ["omitted", "extra", "false", "binding", "no-output", "tampered"])
def test_each_service_requires_bound_producer_outputs(tmp_path, check, fault):
    contract, _ = native_pilot(tmp_path)
    refs, _ = service_evidence(tmp_path, contract)
    ref = refs[check]
    value = json.loads((tmp_path / ref["path"]).read_text())
    proofs = value["facts"]["services"]
    proof = proofs["returns-mcp"]
    if fault == "omitted":
        proofs.pop("returns-mcp")
    elif fault == "extra":
        proofs["unknown"] = deepcopy(proof)
    elif fault == "false":
        proof["schema_validated" if check == "package" else "observed"] = False
    elif fault == "binding":
        proof["adapter_sha256"] = "b" * 64
    elif fault == "no-output":
        proof["evidence"] = []
    else:
        write(tmp_path, proof["evidence"][0]["path"], "tampered")
    ref["sha256"] = presenter.sha256(write(tmp_path, ref["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert assess(tmp_path)["checks"][check]["status"] == "blocked"


def test_service_input_change_stales_all_runtime_dependents(tmp_path):
    contract, _ = native_pilot(tmp_path)
    service_evidence(tmp_path, contract)
    write(tmp_path, "src/mcp/new_import.py", "changed")
    report = assess(tmp_path)
    assert all(c["status"] == "stale" for c in report["checks"].values())


@pytest.mark.parametrize("case", ["schema_validated", "image_runtime", "startup", "adapter"])
def test_static_or_partial_package_output_is_not_native_service_proof(tmp_path, case):
    contract, _ = native_pilot(tmp_path)
    refs, _ = service_evidence(tmp_path, contract)
    ref = refs["package"]
    value = json.loads((tmp_path / ref["path"]).read_text())
    value["facts"]["services"]["returns-workspace"].pop(case)
    ref["sha256"] = presenter.sha256(write(tmp_path, ref["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert assess(tmp_path)["checks"]["package"]["status"] == "blocked"


@pytest.mark.parametrize("fault", ["omitted", "extra"])
def test_observed_target_must_include_exact_services(tmp_path, fault):
    contract, _ = native_pilot(tmp_path)
    _, target = service_evidence(tmp_path, contract)
    if fault == "omitted":
        target["services"].pop("returns-workspace")
    else:
        target["services"]["unbound"] = deepcopy(target["services"]["returns-mcp"])
    write(tmp_path, presenter.TARGET, target)
    assert assess(tmp_path)["checks"]["deployment"]["status"] == "blocked"


@pytest.mark.parametrize("latest", [True, False])
def test_single_revision_routing_is_bound_to_observed_revision(tmp_path, latest):
    contract, inventory = native_pilot(tmp_path)
    item = contract["deployment"]["ancillary_services"][0]
    definition = json.loads((tmp_path / item["manifest"]).read_text())
    route = {"weight": 100, **({"latestRevision": True} if latest else {"revisionName": "revision-1"})}
    definition["properties"]["configuration"]["ingress"]["traffic"] = [route]
    write(tmp_path, item["manifest"], definition)
    assert not presenter.deployment_gaps(tmp_path, contract, inventory=inventory)
    _, target = service_evidence(tmp_path, contract)
    assert assess(tmp_path)["ready"]
    if not latest:
        target["services"][item["name"]]["version"] = "revision-not-routed"
        write(tmp_path, presenter.TARGET, target)
        assert assess(tmp_path)["checks"]["deployment"]["status"] == "blocked"


def test_traffic_split_remains_unsupported(tmp_path):
    contract, inventory = native_pilot(tmp_path)
    item = contract["deployment"]["ancillary_services"][0]
    definition = json.loads((tmp_path / item["manifest"]).read_text())
    definition["properties"]["configuration"]["ingress"]["traffic"] = [
        {"weight": 50, "revisionName": "one"}, {"weight": 50, "revisionName": "two"}]
    write(tmp_path, item["manifest"], definition)
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory)


def test_old_profile_defaults_and_unchecked_ancillary_remain_fail_closed(tmp_path):
    contract, inventory = native_pilot(tmp_path)
    del contract["deployment"]["ancillary_services"]
    assert "native-ancillary-consumer-required" in presenter.deployment_gaps(
        tmp_path, contract, inventory=inventory)[0]
    write(tmp_path, "specs/manifest.json", {"deployment_manifest": inventory})
    assert assess(tmp_path) == {"enabled": False}


@pytest.mark.parametrize("path", ["azure.yaml", "agent.yaml", "src/agent/agent.manifest.yaml"])
def test_extension_does_not_allow_competing_native_definitions(tmp_path, path):
    contract, inventory = native_pilot(tmp_path)
    write(tmp_path, path, "competing")
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory)


def test_service_output_cannot_be_borrowed_between_processes(tmp_path):
    first, second = tmp_path / "returns", tmp_path / "maintenance"
    a, _ = native_pilot(first)
    b, _ = native_pilot(second, "maintenance", "invocations")
    refs, _ = service_evidence(first, a)
    service_evidence(second, b)
    import shutil
    shutil.copytree(first / "evidence", second / "evidence", dirs_exist_ok=True)
    write(second, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert not assess(second)["ready"]
    assert assess(second)["checks"]["package"]["status"] == "blocked"


@pytest.mark.parametrize("fault", ["service-duplicate", "resource-duplicate", "shape", "empty",
                                  "escape", "symlink", "root-input", "unified"])
def test_malformed_extension_fails_closed(tmp_path, fault):
    contract, inventory = native_pilot(tmp_path)
    services = contract["deployment"]["ancillary_services"]
    if fault == "service-duplicate":
        services[1]["name"] = services[0]["name"]
    elif fault == "resource-duplicate":
        services[1]["resource_name"] = services[0]["resource_name"]
    elif fault == "shape":
        services[0]["unchecked"] = True
    elif fault == "empty":
        services.clear()
    elif fault == "escape":
        services[0]["inputs"] = ["../outside"]
    elif fault == "symlink":
        (tmp_path / "src/mcp/link.py").symlink_to(tmp_path / "src/agent/main.py")
    elif fault == "root-input":
        services[0]["inputs"] = ["."]
    else:
        contract["deployment"]["consumer"] = "unified-azd"
    write(tmp_path, presenter.CONTRACT, contract)
    assert not assess(tmp_path)["ready"]
    assert assess(tmp_path)["states"]["source-ready"] == "blocked"


def test_build_context_ignore_files_and_root_imports_are_fingerprinted(tmp_path):
    contract, inventory = native_pilot(tmp_path)
    item = contract["deployment"]["ancillary_services"][0]
    write(tmp_path, "shared.py", "root import")
    item["inputs"].append("shared.py")
    assert not presenter.deployment_gaps(tmp_path, contract, inventory=inventory)
    before = presenter.fingerprints(tmp_path, contract)
    write(tmp_path, ".dockerignore", "data.json")
    assert presenter.fingerprints(tmp_path, contract)["runtime"] != before["runtime"]
    before = presenter.fingerprints(tmp_path, contract)
    write(tmp_path, item["dockerfile"] + ".dockerignore", "src/mcp")
    assert presenter.fingerprints(tmp_path, contract)["runtime"] != before["runtime"]


def test_safecheck_uses_same_complete_native_consumer(tmp_path):
    contract, inventory = native_pilot(tmp_path)
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "native_service_safecheck", root / "skills/threadlight-safe-check/scripts/safe_check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    data = {"delivery_profile": "presenter-ready", "deployment_manifest": inventory}
    assert checker._presenter_deployment_gaps(tmp_path, data, packaged=True) == []
    data["deployment_manifest"]["services"].pop()
    assert checker._presenter_deployment_gaps(tmp_path, data, packaged=True)


def test_expiry_preserves_recorded_history_but_prevents_dispatch(tmp_path, monkeypatch):
    from skills._shared.tests.test_presenter import orchestrator, skip_legacy_probes
    contract, _ = native_pilot(tmp_path)
    contract["availability"]["expires_at"] = "2026-09-24T11:00:00Z"
    write(tmp_path, presenter.CONTRACT, contract)
    service_evidence(tmp_path, contract)
    report = assess(tmp_path)
    assert report["checks"]["backend"]["status"] == "verified"
    assert not report["source_usable"] and not report["ready"]
    orch = orchestrator()
    skip_legacy_probes(monkeypatch, orch)
    monkeypatch.setattr(presenter, "assess", lambda root: report)
    calls = []
    result = orch.execute(tmp_path, lambda stage: calls.append(stage) or 0)
    assert result["status"] == "blocked"
    assert calls == ["presenter_ready"]


def test_guide_describes_checked_extension_and_proof_boundary():
    root = Path(__file__).resolve().parents[3]
    guide = (root / "docs/presenter-ready.md").read_text()
    for marker in ("ancillary_services", "containerapp-arm", "resource_name",
                   "SERVICE_PACKAGE_CASES", "definition_sha256", "source directories",
                   "scheduled jobs remain unsupported"):
        assert marker in guide
    assert "this bounded\nprofile rejects them" not in guide
