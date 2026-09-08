"""Real LOCAL-14 evidence must survive the same strict consumer boundary."""
from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.governance_runtime

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills/threadlight-governed-actions/scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="module")
def executed_native(tmp_path_factory):
    native = importlib.import_module("native_local")
    inventory = importlib.import_module("inventory")
    assessor = importlib.import_module("governed_actions")
    contracts = importlib.import_module("contracts")
    render = importlib.import_module("render")
    example = ROOT / "examples/returns-triage-governed"
    declaration = native.contract(example)
    events = native.execute(example, declaration)
    inv = inventory.build_action_inventory(example)
    paths, probes, _ = native.evaluate(events, declaration, inv.actions)
    project = tmp_path_factory.mktemp("native-ledger") / "project"
    shutil.copytree(example, project, ignore=shutil.ignore_patterns("archive", "__pycache__"))
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "remote", "add", "origin",
                    "https://github.com/octo-org/native-fixture.git"], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid", "commit",
                    "--allow-empty", "-qm", "fixture"], check=True)
    head = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
    source = contracts.SourceRef("octo-org/native-fixture", head, False)
    now = datetime.now(timezone.utc).isoformat()
    options = contracts.AssessmentOptions(root=project, phase="pre-deploy", now=now)
    policy_hashes = tuple(importlib.import_module("canonical").hash_files(
        project, inv.policy_paths)["files"])
    probes, findings, evidence = assessor._bind_probe_evidence(probes, source, options, policy_hashes)
    evidence += tuple(assessor._bind_static_source_evidence(
        project, (), source, options, policy_hashes,
        already_collected=frozenset(ref.evidence_id for ref in evidence), paths=paths))
    findings += importlib.import_module("probes").findings_from_probes(probes, phase="pre-deploy")
    result = contracts.AssessmentResult(
        source=source, actions=inv.actions, paths=paths, probes=probes,
        findings=findings, evidence=evidence, policy_hashes=policy_hashes,
        captured_at=now, phase="pre-deploy",
        native_local=native.native_local_evidence.build_envelope(
            events, source={"repository": source.repository, "commit": source.commit, "dirty": source.dirty},
            captured_at=now, policy_hashes=policy_hashes))
    return project, result, events, declaration, render


def test_real_native_controls_render_fresh_and_are_consumed(executed_native):
    project, result, _, _, render = executed_native
    manifest = render.build_manifest(result)
    assert manifest["freshness"]["status"] == "fresh"
    assert manifest["phase"] == "pre-deploy"
    assert all(not ref["live_verified"] for ref in manifest["evidence"])
    import jsonschema
    schema = json.loads((ROOT / "skills/threadlight-governed-actions/references/governed-actions-manifest.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(manifest)
    path = project / "tests/governed-actions-manifest.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(manifest))
    sys.path.insert(0, str(ROOT / "skills/threadlight-production-ready/scripts"))
    readiness = importlib.import_module("production_ready")
    loaded = readiness.load_governed_actions_manifest(project)
    assert readiness._validate_governed_actions_manifest(
        loaded, result.source.commit, datetime.now(timezone.utc)) is None
    sys.path.insert(0, str(ROOT / "skills/threadlight-auto/references"))
    auto = importlib.import_module("orchestrator")
    assert auto.summarize_governed_actions_manifest(
        path, result.source.commit)["trusted"] is True


@pytest.mark.parametrize("fault", ["action-scope", "bool-effect", "approval-consume", "payload-field",
                                  "python-version", "import-names"])
def test_native_recorder_rejects_incomplete_or_malformed_evidence(executed_native, fault):
    _, result, original, declaration, _ = executed_native
    events = deepcopy(original)
    if fault == "action-scope":
        next(e for e in events if e["event"] == "invocation")["action_id"] = "other_action"
    elif fault == "bool-effect":
        next(e for e in events if e["event"] == "terminal" and e["effects"] == 1)["effects"] = True
    elif fault == "approval-consume":
        events = [e for e in events if e["event"] != "approval_consume"]
        for counter, event in enumerate(events, 1):
            event["counter"] = counter
    elif fault == "payload-field":
        next(e for e in events if e["event"] == "invocation")["arguments"] = {"payload": "forbidden"}
    elif fault == "python-version":
        events[0]["observation"]["python_version"] = 3.12
    else:
        events[0]["observation"]["imports"] = {str(n): "/fake/__init__.py" for n in range(6)}
    with pytest.raises(Exception):
        importlib.import_module("native_local").evaluate(events, declaration, result.actions)


@pytest.mark.parametrize("fault", [
    "missing", "empty", "schema", "phase", "source", "capture", "policy",
    "events-empty", "event-scope", "event-order", "receipt-payload", "receipt-replay",
    "ack-after-effect", "consumption-missing", "nonce-reuse", "approval-replay-accepted",
    "approval-changed-accepted", "approval-expired-accepted", "audit-failure-missing",
    "source-digest", "live-evidence", "output-promoted",
])
def test_each_consumer_rejects_corrupt_native_evidence(executed_native, fault):
    project, original, _, _, render = executed_native
    from skills._shared.native_local_evidence import CONTROLS, digest
    envelope = deepcopy(original.native_local)
    evidence = list(original.evidence)
    probes = list(original.probes)
    events = envelope["events"]
    if fault == "missing":
        envelope = None
    elif fault == "empty":
        envelope = {}
    elif fault in {"schema", "phase", "capture", "policy"}:
        field = {"schema": "schema", "phase": "phase", "capture": "captured_at",
                 "policy": "policy_set_sha256"}[fault]
        envelope[field] = "invalid"
    elif fault == "source":
        envelope["source"]["commit"] = "f" * 40
    elif fault == "events-empty":
        events.clear()
    elif fault == "event-scope":
        next(e for e in events if e["event"] == "invocation")["action_id"] = "other_action"
    elif fault == "event-order":
        events[2]["counter"] = events[1]["counter"]
    elif fault == "receipt-payload":
        next(e for e in events if e["event"] == "pre_action_decision")["receipt"]["arguments"] = "forbidden"
    elif fault == "receipt-replay":
        acks = [e for e in events if e["event"] == "pre_action_decision"]
        acks[1]["receipt_id"] = acks[1]["receipt"]["receipt_id"] = acks[0]["receipt_id"]
        acks[1]["receipt_hash"] = digest(acks[1]["receipt"])
    elif fault == "ack-after-effect":
        index = next(i for i, e in enumerate(events) if e["event"] == "invocation")
        events[index - 1], events[index] = events[index], events[index - 1]
        for n, e in enumerate(events, 1):
            e["counter"] = n
    elif fault in {"consumption-missing", "audit-failure-missing"}:
        name = "approval_consume" if fault == "consumption-missing" else "audit_failure"
        events[:] = [e for e in events if e["event"] != name]
        for n, e in enumerate(events, 1):
            e["counter"] = n
    elif fault == "nonce-reuse":
        consumes = [e for e in events if e["event"] == "approval_consume" and e["case"] == "approved"]
        consumes[1]["nonce_hash"] = consumes[0]["nonce_hash"]
        next(e for e in events if e["event"] == "approval_request"
             and e["intent_hash"] == consumes[1]["intent_hash"])["nonce_hash"] = consumes[0]["nonce_hash"]
    elif fault.startswith("approval-"):
        name = "approval_" + fault.split("-")[1]
        next(e for e in events if e["event"] == name)["status"] = 200
    elif fault == "source-digest":
        events[0]["source_hashes"]["project:src/agent/copilot-instructions.md"] = "f" * 64
    elif fault == "live-evidence":
        evidence = [replace(e, live_verified=True) if e.kind == "native-approval" else e for e in evidence]
    elif fault == "output-promoted":
        probes = [replace(p, status="pass") if p.probe_id == "output-mediation" else p for p in probes]
    # Rehash tampered observations: content hashes or kind labels alone cannot
    # satisfy the native sequence/identity contract.
    evidence = [replace(e, sha256=digest(events)) if e.kind in CONTROLS.values() else e for e in evidence]
    result = replace(original, native_local=envelope, evidence=tuple(evidence), probes=tuple(probes))
    manifest = render.build_manifest(result)
    assert manifest["freshness"]["status"] != "fresh"
    # Optimistic metadata must not mask the invalid native envelope downstream.
    manifest["freshness"] = render.build_manifest(original)["freshness"]
    path = project / "tests/governed-actions-manifest.json"
    path.write_text(json.dumps(manifest))
    sys.path.insert(0, str(ROOT / "skills/threadlight-production-ready/scripts"))
    readiness = importlib.import_module("production_ready")
    assert readiness._validate_governed_actions_manifest(
        readiness.load_governed_actions_manifest(project), original.source.commit,
        datetime.now(timezone.utc)) is not None
    sys.path.insert(0, str(ROOT / "skills/threadlight-auto/references"))
    assert importlib.import_module("orchestrator").summarize_governed_actions_manifest(
        path, original.source.commit)["trusted"] is False


def test_real_materialized_assessment_emits_consumable_native_envelope(tmp_path):
    project = tmp_path / "portable"
    materialized = subprocess.run([
        sys.executable, str(ROOT / "examples/returns-triage-governed/scripts/materialize.py"),
        "--output", str(project)], capture_output=True, text=True, timeout=120)
    assert materialized.returncode == 0, materialized.stderr
    assert (project / ".governance-tools/skills/_shared/native_local_evidence.py").read_bytes() == (
        ROOT / "skills/_shared/native_local_evidence.py").read_bytes()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "remote", "add", "origin",
                    "https://github.com/octo-org/native-fixture.git"], check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    tools = project / ".governance-tools"
    cache = tools / ".governance-validation"
    cache.mkdir()
    for name in ("linux-venv", "wheels", "opa-linux-amd64"):
        (cache / name).symlink_to(ROOT / ".governance-validation" / name)
    completed = subprocess.run([
        sys.executable, str(tools / "skills/threadlight-governed-actions/scripts/governed_actions.py"),
        "--target", str(project), "--phase", "pre-deploy", "--emit"],
        cwd=project, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    path = project / "tests/governed-actions-manifest.json"
    manifest = json.loads(path.read_text())
    assert manifest["source"]["dirty"] is False
    assert manifest["native_local"]["schema"] == "threadlight-native-local-evidence/v1"
    assert manifest["freshness"]["status"] == "fresh"
    sys.path.insert(0, str(ROOT / "skills/threadlight-production-ready/scripts"))
    readiness = importlib.import_module("production_ready")
    assert readiness._validate_governed_actions_manifest(
        readiness.load_governed_actions_manifest(project), manifest["source"]["commit"],
        datetime.now(timezone.utc)) is None
    sys.path.insert(0, str(ROOT / "skills/threadlight-auto/references"))
    assert importlib.import_module("orchestrator").summarize_governed_actions_manifest(
        path, manifest["source"]["commit"])["trusted"] is True


@pytest.mark.parametrize("assessor", [
    None, {"name": "foreign", "version": "2.0.0", "adapter": "maf/v1"},
    {"name": "threadlight-governed-actions", "version": "9.9.9", "adapter": "maf/v1"},
    {"name": "threadlight-governed-actions", "version": "0.1.0", "adapter": "maf/v1"},
])
def test_native_contract_requires_its_reviewed_assessor(executed_native, assessor):
    project, result, _, _, render = executed_native
    manifest = render.build_manifest(result)
    manifest["assessor"] = assessor
    path = project / "tests/governed-actions-manifest.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(manifest))
    sys.path.insert(0, str(ROOT / "skills/threadlight-auto/references"))
    assert importlib.import_module("orchestrator").summarize_governed_actions_manifest(
        path, result.source.commit)["trusted"] is False
