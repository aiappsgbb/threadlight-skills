"""Offline contract tests; supplied hosted receipts are fixtures, not live proof."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from skills._shared import presenter

ROOT = Path(__file__).resolve().parents[3]

@pytest.fixture(autouse=True)
def frozen_assessment_time(monkeypatch):
    from datetime import datetime, timezone
    original = presenter.assess
    monkeypatch.setattr(presenter, "assess", lambda root, *, now=None: original(
        root, now=now or datetime(2026, 9, 24, 12, tzinfo=timezone.utc)))


def write(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return target


def pilot(root, process="returns"):
    write(root, "specs/manifest.json", {"delivery_profile": "presenter-ready"})
    contract = {
        "schema": "threadlight-presenter-contract/v1",
        "process_id": process,
        "owner": "process-team",
        "evidence_max_age_hours": 24,
        "description": {
            "role": "Returns reviewer" if process == "returns" else "Service planner",
            "problem": "Prepare a bounded synthetic draft",
            "fictional_company": "Northstar Retail" if process == "returns" else "Harbor Service",
            "inputs": ["Case and supporting facts"],
            "deterministic_rules": (["BR-001: unopened within 30 days"] if process == "returns"
                                    else ["BR-001: service window no longer than four hours"]),
            "agent_contribution": "Explain the draft using case evidence",
            "outcome": "Saved draft, not a business authorization",
            "human_responsibility": "Review and decide outside the PoC",
            "inclusions": ["Synthetic draft"],
            "exclusions": ["Settlement", "Production certification"],
        },
        "journey": {
            "entry": "Open workspace and select a case",
            "interaction": "Prepare draft",
            "persistence": True,
            "store": "Process-owned draft store",
            "readback": "Separate authenticated store query by result ID",
            "reopen": "New browser session, open saved result",
            "download": False,
            "recovery": "Reconcile uncertain operation ID before retry",
        },
        "availability": {
            "presenter_access": "Standing role; normal authorization still required",
            "historical_read": "Independent of source validity",
            "session": "Short-lived; reauthenticate without deleting results",
            "source_revision": f"{process}-source-1",
            "effective_at": "2026-01-01T00:00:00Z",
            "expires_at": "2027-01-01T00:00:00Z",
            "preparation": "Explicit new synthetic revision with owner approval",
            "concurrency": "Compare-and-swap per result",
            "idempotency": "Process/source/action/operation scoped",
            "uncertain_effect": "Reconcile by operation ID; never replay blindly",
        },
        "deployment": {
            "guidance": presenter.DEPLOYMENT_PIN,
            "consumer": "unified-azd",
            "manifest": "azure.yaml",
            "service": "demo",
            "runtime_root": "src/agent",
            "entrypoint": "src/agent/main.py",
            "lockfile": "src/agent/requirements.txt",
            "adapter": "src/agent/adapter.py",
            "protocol": "responses",
            "protocol_version": "2.0.0",
            "model_env": "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        },
        "inputs": {
            "runtime": ["src/agent"],
            "interface": ["src/workspace"],
            "source": ["specs/data.json"],
            "script": ["specs/script.txt"],
            "sizing": ["specs/sizing.json"],
        },
        "sizing": {
            "status": "proposed",
            "model_tokens": "Input/output separately; estimate",
            "model_rounds": "Measured per interaction",
            "logical_tool_calls": "Count dispatches, not tokens",
            "resource_units": "Deployed replicas and capacity",
            "unpriced": ["Identity"],
            "comparison": "Fixed baseline receipt per comparison, never latest",
        },
        "publication": {"files": [], "bundles": []},
    }
    write(root, "azure.yaml", """services:
  demo:
    host: azure.ai.agent
    project: ./src/agent
    kind: hosted
    protocols:
      - protocol: responses
        version: 2.0.0
    environmentVariables:
      - name: AZURE_AI_MODEL_DEPLOYMENT_NAME
        value: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}
infra:
  provider: microsoft.foundry
""")
    for path in ["src/agent/main.py", "src/agent/adapter.py", "src/agent/requirements.txt",
                 "src/workspace/index.html", "specs/data.json", "specs/script.txt",
                 "specs/sizing.json"]:
        write(root, path, f"{process}: {path}")
    write(root, presenter.CONTRACT, contract)
    return contract


def target(root, attempt="attempt-1"):
    value = {"attempt": attempt, "environment": "fixture", "version": "1",
             "image": "sha256:" + "a" * 64, "identity": "fixture-identity"}
    write(root, presenter.TARGET, value)
    return value


def receipt(root, contract, check, *, level=None):
    scope = presenter.CHECKS[check]
    value = {
        "schema": "threadlight-presenter-receipt/v1",
        "process_id": contract["process_id"], "check": check,
        "level": level or scope["level"], "result": "pass",
        "observed_at": "2026-09-24T10:00:00Z",
        "inputs": {k: v for k, v in presenter.fingerprints(root, contract).items()
                   if k in scope["inputs"]},
        "evidence": [{"path": "evidence/output.txt",
                      "sha256": presenter.sha256(b"fixture retained output")}],
        "facts": {},
    }
    write(root, "evidence/output.txt", "fixture retained output")
    if check == "package":
        value["facts"] = {name: True for name in presenter.PACKAGE_CASES}
    if check in {"deployment", "backend", "script", "human"}:
        value["target"] = target(root)
    if check in {"backend", "script"}:
        value["facts"] = {
            "entry": True, "interaction": True, "terminal_success": True,
            "persisted": True, "independent_readback": True, "reopened": True,
            "operation_id": f"{contract['process_id']}-op", "result_id": "draft-1",
            "writer_session": "session-1", "reader_session": "session-2",
            "readback_method": "store-get",
        }
    if check == "human":
        value["facts"] = {"accepted_by": "fixture-reviewer", "first_time_presenter": True}
    path = f"evidence/{check}.json"
    raw = write(root, path, value).read_bytes()
    return {"path": path, "sha256": presenter.sha256(raw)}


def evidence(root, contract, checks=None):
    checks = checks or presenter.CHECKS
    refs = {}
    dependencies = {"package": [], "deployment": [], "backend": ["package", "deployment"],
                    "script": ["backend"], "human": ["script"]}
    for name in checks:
        ref = receipt(root, contract, name)
        value = json.loads((root / ref["path"]).read_text())
        value["predecessors"] = {key: refs[key]["sha256"] for key in dependencies[name]}
        ref["sha256"] = presenter.sha256(write(root, ref["path"], value).read_bytes())
        refs[name] = ref
    write(root, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    return refs


def assess(root):
    from datetime import datetime, timezone
    return presenter.assess(root, now=datetime(2026, 9, 24, 12, tzinfo=timezone.utc))


def test_no_opt_in_leaves_legacy_pilots_unchanged(tmp_path):
    assert assess(tmp_path) == {"enabled": False}
    write(tmp_path, presenter.CONTRACT, {"broken": True})
    assert assess(tmp_path) == {"enabled": False}


@pytest.mark.parametrize("selection", ["presenter_ready", None, {}, True])
def test_invalid_explicit_profile_is_not_off(tmp_path, selection):
    write(tmp_path, "specs/manifest.json", {"delivery_profile": selection})
    assert assess(tmp_path)["states"]["source-ready"] == "blocked"


def test_no_receipts_means_only_source_ready(tmp_path):
    pilot(tmp_path)
    report = assess(tmp_path)
    assert report["states"]["source-ready"] == "verified"
    assert report["states"]["deployed"] == "pending"
    assert not report["ready"]


def test_supplied_receipts_are_not_independent_attestation(tmp_path):
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    report = assess(tmp_path)
    assert report["ready"]
    assert report["evidence_authority"] == "recorded-not-independently-attested"
    assert set(report["states"]) == {
        "source-ready", "deployed", "backend-verified", "script-verified", "human-accepted"}


def test_offline_cannot_replace_hosted_and_missing_reopen_cannot_pass(tmp_path):
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    raw = json.loads((tmp_path / refs["script"]["path"]).read_text())
    raw["level"] = "offline"
    raw["facts"]["reopened"] = False
    refs["script"]["sha256"] = presenter.sha256(write(tmp_path, refs["script"]["path"], raw).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert assess(tmp_path)["checks"]["script"]["status"] == "blocked"


def test_only_relevant_changes_invalidate_receipts(tmp_path):
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    write(tmp_path, "specs/script.txt", "Changed presenter wording")
    report = assess(tmp_path)
    assert report["checks"]["package"]["status"] == "verified"
    assert report["checks"]["backend"]["status"] == "verified"
    assert report["checks"]["script"]["status"] == "stale"
    write(tmp_path, "src/agent/new_module.py", "New packaged code")
    assert assess(tmp_path)["checks"]["package"]["status"] == "stale"


def test_new_attempt_invalidates_hosted_not_native(tmp_path):
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    target(tmp_path, "attempt-2")
    report = assess(tmp_path)
    assert report["checks"]["package"]["status"] == "verified"
    assert report["checks"]["backend"]["status"] == "stale"


def test_expired_source_does_not_refresh_or_delete_history(tmp_path):
    contract = pilot(tmp_path)
    contract["availability"]["expires_at"] = "2026-09-23T10:00:00Z"
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = assess(tmp_path)
    assert not report["source_usable"]
    assert not report["ready"]
    assert report["checks"]["backend"]["status"] == "verified"
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_second_process_cannot_inherit_first_receipts(tmp_path):
    first, second = tmp_path / "returns", tmp_path / "maintenance"
    a, b = pilot(first), pilot(second, "maintenance")
    evidence(first, a)
    import shutil
    shutil.copytree(first / "evidence", second / "evidence")
    target(second)
    write(second, presenter.EVIDENCE, json.loads((first / presenter.EVIDENCE).read_text()))
    assert assess(second)["checks"]["backend"]["status"] == "blocked"
    evidence(second, b)
    assert assess(second)["checks"]["backend"]["status"] == "verified"


@pytest.mark.parametrize("path", ["agent.yaml", "src/agent/agent.manifest.yaml"])
def test_unified_consumer_rejects_competing_manifests(tmp_path, path):
    contract = pilot(tmp_path)
    write(tmp_path, path, "old: manifest")
    assert presenter.deployment_gaps(tmp_path, contract)


def test_protocol_and_environment_must_match_selected_consumer(tmp_path):
    contract = pilot(tmp_path)
    yaml = tmp_path / "azure.yaml"
    yaml.write_text(yaml.read_text().replace("protocol: responses", "protocol: invocations"))
    assert presenter.deployment_gaps(tmp_path, contract)


def test_obsolete_config_wrapper_is_not_native_unified_schema(tmp_path):
    contract = pilot(tmp_path)
    write(tmp_path, "azure.yaml", """services:
  demo:
    host: azure.ai.agent
    project: ./src/agent
    config:
      kind: hosted
      protocols: [{protocol: responses, version: 2.0.0}]
      environmentVariables: {AZURE_AI_MODEL_DEPLOYMENT_NAME: model}
""")
    assert presenter.deployment_gaps(tmp_path, contract)


def test_human_acceptance_is_bound_to_exact_script_receipt(tmp_path):
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    raw = json.loads((tmp_path / refs["script"]["path"]).read_text())
    raw["observed_at"] = "2026-09-24T11:00:00Z"
    refs["script"]["sha256"] = presenter.sha256(write(tmp_path, refs["script"]["path"], raw).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert assess(tmp_path)["checks"]["human"]["status"] == "stale"


def test_hosted_proof_expires_without_expiring_native_or_results(tmp_path):
    contract = pilot(tmp_path)
    contract["evidence_max_age_hours"] = 1
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract)
    report = assess(tmp_path)
    assert report["checks"]["package"]["status"] == "verified"
    assert report["checks"]["backend"]["status"] == "stale"


def test_native_sdk_consumer_is_explicit_and_source_bound(tmp_path):
    contract = pilot(tmp_path)
    contract["deployment"].update({
        "consumer": "native-sdk", "manifest": "deploy/definition.json",
        "create_entrypoint": "deploy/create.py",
    })
    (tmp_path / "azure.yaml").unlink()
    write(tmp_path, "deploy/create.py", "approved native creation adapter")
    write(tmp_path, "deploy/definition.json", {
        "kind": "hosted", "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
        "environment_variables": {"AZURE_AI_MODEL_DEPLOYMENT_NAME": "model"},
    })
    write(tmp_path, presenter.CONTRACT, contract)
    assert not presenter.deployment_gaps(tmp_path, contract)
    before = presenter.fingerprints(tmp_path, contract)
    write(tmp_path, "deploy/create.py", "changed adapter")
    assert presenter.fingerprints(tmp_path, contract)["runtime"] != before["runtime"]


def test_tampered_receipt_is_not_repaired_by_reader(tmp_path):
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    write(tmp_path, refs["backend"]["path"], {})
    assert assess(tmp_path)["checks"]["backend"]["status"] == "blocked"
    assert (tmp_path / refs["backend"]["path"]).read_text() == "{}"


def test_symlink_and_duplicate_json_rejected(tmp_path):
    pilot(tmp_path)
    path = tmp_path / "specs/data.json"
    path.unlink()
    path.symlink_to(tmp_path / "specs/script.txt")
    assert assess(tmp_path)["states"]["source-ready"] == "blocked"
    write(tmp_path, "specs/manifest.json", '{"delivery_profile":"presenter-ready","delivery_profile":"default"}')
    assert assess(tmp_path)["states"]["source-ready"] == "blocked"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_publication_checks_complete_immutable_tree_and_bundle(tmp_path):
    contract = pilot(tmp_path)
    contract["publication"]["files"] = ["specs/script.txt"]
    contract["publication"]["bundles"] = [{"path": "downloads/runtime.zip", "source_root": "src/agent"}]
    write(tmp_path, presenter.CONTRACT, contract)
    (tmp_path / "downloads").mkdir()
    with zipfile.ZipFile(tmp_path / "downloads/runtime.zip", "w") as archive:
        for path in sorted((tmp_path / "src/agent").iterdir()):
            archive.writestr(path.name, path.read_bytes())
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "immutable source")
    commit = git(tmp_path, "rev-parse", "HEAD")
    assert presenter.verify_publication(tmp_path, contract, commit)["status"] == "verified"
    write(tmp_path, "src/agent/new_module.py", "not packaged")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "new immutable source")
    current = git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="bundle"):
        presenter.verify_publication(tmp_path, contract, current)
    assert presenter.verify_publication(tmp_path, contract, commit)["status"] == "verified"


def orchestrator():
    spec = importlib.util.spec_from_file_location(
        "presenter_orchestrator", ROOT / "skills/threadlight-auto/references/orchestrator.py")
    orch = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = orch
    spec.loader.exec_module(orch)
    return orch


def test_auto_zero_exit_does_not_complete_presenter_profile(tmp_path, monkeypatch):
    orch = orchestrator()
    pilot(tmp_path)
    for name in orch.STAGE_PROBES:
        monkeypatch.setitem(orch.STAGE_PROBES, name, lambda workspace, state, name=name:
                            orch.StageDecision(name, "skip", "fixture"))
    result = orch.execute(tmp_path, lambda stage: 0)
    assert result["status"] == "blocked"
    assert result["stage"] == "presenter_package"


def test_auto_script_change_does_not_redeploy_or_replay_backend(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    for name in orch.STAGE_PROBES:
        monkeypatch.setitem(orch.STAGE_PROBES, name, lambda workspace, state, name=name:
                            orch.StageDecision(name, "skip", "fixture"))
    monkeypatch.setitem(orch.STAGE_PROBES, "design",
                        lambda *_: orch.StageDecision("design", "run", "changed script"))
    write(tmp_path, "specs/script.txt", "changed presentation")
    report = orch.decide(tmp_path)
    decisions = {d["stage"]: d["decision"] for d in report["decisions"]}
    assert decisions["deploy"] == decisions["invoke"] == "skip"
    assert decisions["presenter_ready"] == "run"
    assert report["stages"].index("presenter_package") < report["stages"].index("deploy")


def test_safe_check_validates_selected_auxiliary_services(tmp_path):
    contract = pilot(tmp_path)
    inventory = {"services": [{"name": "mcp", "host": "containerapp", "src": "src/mcp"}]}
    assert presenter.deployment_gaps(tmp_path, contract, inventory=inventory, packaged=True)


def test_partial_native_inventory_cannot_pass_package_proof(tmp_path):
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    value = json.loads((tmp_path / refs["package"]["path"]).read_text())
    del value["facts"]["retry_dispatch"]
    refs["package"]["sha256"] = presenter.sha256(write(tmp_path, refs["package"]["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert assess(tmp_path)["checks"]["package"]["status"] == "blocked"


def test_promised_publication_blocks_handoff_until_immutable_source_verified(tmp_path):
    contract = pilot(tmp_path)
    contract["publication"]["files"] = ["specs/script.txt"]
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract)
    assert not assess(tmp_path)["ready"]
    assert assess(tmp_path)["publication"]["status"] == "pending"
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "specs", "src", "azure.yaml")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "source")
    commit = git(tmp_path, "rev-parse", "HEAD")
    proof = presenter.verify_publication(tmp_path, contract, commit)
    write(tmp_path, presenter.PUBLICATION, proof)
    assert assess(tmp_path)["ready"]
    write(tmp_path, "specs/script.txt", "uncommitted publication change")
    assert assess(tmp_path)["publication"]["status"] == "blocked"


def test_companion_comparison_baseline_cannot_move_with_interleaved_activity(tmp_path):
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    value = {
        "schema": "threadlight-presenter-comparison/v1", "process_id": contract["process_id"],
        "baseline": refs["backend"], "candidate": refs["backend"],
        "method": "same cases and denominator; illustrative self-comparison",
    }
    write(tmp_path, "evidence/comparison.json", value)
    before = presenter.validate_comparison(tmp_path, "evidence/comparison.json")
    write(tmp_path, "evidence/unrelated.json", {"result": "new unrelated result"})
    assert presenter.validate_comparison(tmp_path, "evidence/comparison.json") == before
    write(tmp_path, refs["backend"]["path"], {"changed": True})
    with pytest.raises(ValueError, match="digest"):
        presenter.validate_comparison(tmp_path, "evidence/comparison.json")


def test_deployment_inventory_change_invalidates_runtime_proof(tmp_path):
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    write(tmp_path, "specs/manifest.json", {
        "delivery_profile": "presenter-ready",
        "deployment_manifest": {"module_selectors": {"workspace-ui": "yes"}},
    })
    assert assess(tmp_path)["checks"]["deployment"]["status"] == "stale"


def test_safe_check_alternate_manifest_cannot_disable_profile(tmp_path):
    pilot(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "presenter_safecheck", ROOT / "skills/threadlight-safe-check/scripts/safe_check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker._presenter_deployment_gaps(tmp_path, {"deployment_manifest": {}})


def test_receipt_cannot_predate_its_new_predecessor(tmp_path):
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    script = json.loads((tmp_path / refs["script"]["path"]).read_text())
    script["observed_at"] = "2026-09-24T11:00:00Z"
    refs["script"]["sha256"] = presenter.sha256(write(tmp_path, refs["script"]["path"], script).read_bytes())
    human = json.loads((tmp_path / refs["human"]["path"]).read_text())
    human["predecessors"]["script"] = refs["script"]["sha256"]
    refs["human"]["sha256"] = presenter.sha256(write(tmp_path, refs["human"]["path"], human).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert assess(tmp_path)["checks"]["human"]["status"] == "blocked"


def test_publication_path_error_never_leaves_ready_true(tmp_path):
    contract = pilot(tmp_path)
    contract["publication"]["files"] = ["specs/script.txt"]
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract)
    (tmp_path / presenter.PUBLICATION).symlink_to(tmp_path / presenter.EVIDENCE)
    report = assess(tmp_path)
    assert not report["ready"]
    assert report["gaps"]


def skip_legacy_probes(monkeypatch, orch):
    for name in orch.STAGE_PROBES:
        monkeypatch.setitem(orch.STAGE_PROBES, name, lambda workspace, state, name=name:
                            orch.StageDecision(name, "skip", "fixture"))


def test_presenter_receipt_cannot_hide_failed_governance_deployment(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    skip_legacy_probes(monkeypatch, orch)
    monkeypatch.setattr(orch, "_deploy_retry_required", lambda _: True)
    report = orch.decide(tmp_path)
    assert next(d for d in report["decisions"] if d["stage"] == "deploy")["decision"] != "skip"


def test_presenter_cannot_invoke_after_zero_exit_unverified_safe_check(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    skip_legacy_probes(monkeypatch, orch)
    monkeypatch.setitem(orch.STAGE_PROBES, "safe_check",
                        lambda *_: orch.StageDecision("safe_check", "run", "missing proof"))
    result = orch.execute(tmp_path, lambda _: 0)
    assert result["status"] == "blocked"
    assert result["stage"] == "safe_check"
    assert result["executed"] == ["safe_check"]


def test_failed_backend_receipt_is_not_automatic_business_retry(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    value = json.loads((tmp_path / refs["backend"]["path"]).read_text())
    value["result"] = "uncertain"
    refs["backend"]["sha256"] = presenter.sha256(write(tmp_path, refs["backend"]["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    skip_legacy_probes(monkeypatch, orch)
    executed = []
    result = orch.execute(tmp_path, lambda stage: executed.append(stage) or 0)
    assert result["status"] == "blocked"
    assert executed == []


def test_expired_observation_requests_refresh_not_business_replay(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    contract["evidence_max_age_hours"] = 1
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract)
    skip_legacy_probes(monkeypatch, orch)
    report = orch.decide(tmp_path)
    decisions = {d["stage"]: d["decision"] for d in report["decisions"]}
    assert decisions["deploy"] == decisions["invoke"] == "skip"
    assert decisions["presenter_ready"] == "run"


def test_all_declared_packaged_services_and_infrastructure_are_fingerprinted(tmp_path):
    contract = pilot(tmp_path)
    yaml = tmp_path / "azure.yaml"
    yaml.write_text(yaml.read_text().replace("infra:", """  mcp:
    host: containerapp
    project: ./src/mcp
infra:""").replace("provider: microsoft.foundry", "provider: bicep"))
    write(tmp_path, "src/mcp/main.py", "service v1")
    write(tmp_path, "infra/main.bicep", "// v1")
    write(tmp_path, "specs/manifest.json", {
        "delivery_profile": "presenter-ready",
        "deployment_manifest": {"services": [{"name": "mcp", "host": "containerapp", "src": "src/mcp"}]},
    })
    evidence(tmp_path, contract)
    write(tmp_path, "src/mcp/main.py", "service v2")
    assert assess(tmp_path)["checks"]["package"]["status"] == "stale"
    evidence(tmp_path, contract)
    write(tmp_path, "infra/main.bicep", "// v2")
    assert assess(tmp_path)["checks"]["deployment"]["status"] == "stale"


def test_assessor_and_safecheck_use_same_service_inventory(tmp_path):
    contract = pilot(tmp_path)
    write(tmp_path, "specs/manifest.json", {
        "delivery_profile": "presenter-ready",
        "deployment_manifest": {"services": [{"name": "missing", "host": "containerapp", "src": "src/missing"}]},
    })
    evidence(tmp_path, contract)
    assert assess(tmp_path)["states"]["source-ready"] == "blocked"


def test_expired_source_cannot_dispatch_a_new_backend_interaction(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    contract["availability"]["expires_at"] = "2026-09-23T10:00:00Z"
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract, checks=["package", "deployment"])
    skip_legacy_probes(monkeypatch, orch)
    executed = []
    result = orch.execute(tmp_path, lambda stage: executed.append(stage) or 0)
    assert result["status"] == "blocked"
    assert result["stage"] == "invoke"
    assert executed == []


def test_later_worker_cannot_invalidate_safecheck_and_still_complete(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    skip_legacy_probes(monkeypatch, orch)
    monkeypatch.setitem(orch.STAGE_PROBES, "cost_projection",
                        lambda *_: orch.StageDecision("cost_projection", "run", "missing"))

    def worker(stage):
        monkeypatch.setitem(orch.STAGE_PROBES, "safe_check",
                            lambda *_: orch.StageDecision("safe_check", "run", "invalidated"))
        return 0

    result = orch.execute(tmp_path, worker)
    assert result["status"] == "blocked"
    assert result["stage"] == "safe_check"


def test_already_executed_safecheck_invalidated_later_blocks_without_rerun(tmp_path, monkeypatch):
    orch = orchestrator()
    contract = pilot(tmp_path)
    evidence(tmp_path, contract)
    skip_legacy_probes(monkeypatch, orch)
    fresh = False
    monkeypatch.setitem(orch.STAGE_PROBES, "safe_check", lambda *_:
                        orch.StageDecision("safe_check", "skip" if fresh else "run", "current proof"))
    monkeypatch.setitem(orch.STAGE_PROBES, "cost_projection",
                        lambda *_: orch.StageDecision("cost_projection", "run", "not yet run"))
    executed = []

    def worker(stage):
        nonlocal fresh
        executed.append(stage)
        if stage == "safe_check":
            fresh = True
        elif stage == "cost_projection":
            fresh = False
        return 0

    result = orch.execute(tmp_path, worker)
    assert result == {"status": "blocked", "stage": "safe_check",
                      "executed": ["safe_check", "cost_projection"]}
    assert executed == ["safe_check", "cost_projection"]


@pytest.mark.parametrize("receipt_result", ["uncertain", "failed", "malformed"])
def test_governed_retry_cannot_override_blocked_deployment_receipt(tmp_path, monkeypatch, receipt_result):
    orch = orchestrator()
    contract = pilot(tmp_path)
    refs = evidence(tmp_path, contract)
    skip_legacy_probes(monkeypatch, orch)
    value = json.loads((tmp_path / refs["deployment"]["path"]).read_text())
    if receipt_result == "malformed":
        value = {}
    else:
        value["result"] = receipt_result
    refs["deployment"]["sha256"] = presenter.sha256(
        write(tmp_path, refs["deployment"]["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    monkeypatch.setattr(orch, "_deploy_retry_required", lambda _: True)
    report = orch.decide(tmp_path)
    deploy = next(d for d in report["decisions"] if d["stage"] == "deploy")
    assert deploy["decision"] == "hard_stop"
    assert deploy["hard_stop_signature"] == "presenter-evidence-requires-reconciliation"
    executed = []
    assert orch.execute(tmp_path, lambda stage: executed.append(stage) or 0) == {
        "status": "blocked", "stage": "deploy", "executed": []}
    assert executed == []
