"""Artifact boundary tests use local fixtures; never Azure or hosted proof."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def exporter():
    assert (ROOT / "scripts/ci/readiness_evidence.py").exists(), "missing evidence-only exporter"
    return importlib.import_module("scripts.ci.readiness_evidence")


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    mod = exporter()
    runner = tmp_path / "runner"
    runner.mkdir()
    project = runner / "project"
    project.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(runner))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    state = mod.initialize(project)
    return mod, project, state


def put(project, relative, value):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def diagnostic():
    return {"status": "not-verified", "live": False,
            "reason": "PRIVATE body from a failed transport"}


def upload_files(state):
    return sorted((state.parent / "upload").rglob("*"))


def test_only_explicit_produced_sanitized_evidence_enters_upload_tree(boundary):
    mod, project, state = boundary
    mod.begin_stage(state, project, "postdeploy")
    put(project, "tests/runtime-readiness.json", diagnostic())
    put(project, "tests/operator-private-manifest.json", {"private": "PRIVATE operator file"})
    put(project, ".threadlight/private.json", {"secret": "PRIVATE"})
    put(project, "specs/manifest.json", {"deployment_manifest": {"private": "PRIVATE"}})
    (project / "report.md").write_text("PRIVATE raw stdout")
    mod.finish_stage(state, project, "postdeploy", False)
    assert mod.export(state, project)
    files = upload_files(state)
    assert {p.name for p in files} == {"runtime-readiness.json", "export-status.json"}
    assert all("PRIVATE" not in p.read_text() for p in files)
    result = json.loads((state.parent / "upload/runtime-readiness.json").read_text())
    assert result["producer_status"] == "failure"
    assert result["evidence"]["status"] == "not-verified"
    assert not (state.parent / "upload").resolve().is_relative_to(project)


def test_never_started_producer_cannot_export_checked_in_success(boundary):
    mod, project, state = boundary
    put(project, "tests/runtime-readiness.json", {"status": "pass", "live": True})
    assert mod.export(state, project)
    assert [p.name for p in upload_files(state)] == ["export-status.json"]
    assert json.loads(upload_files(state)[0].read_text())["artifacts"] == {}


def test_begin_clears_old_outputs_and_failed_empty_producer_leaves_no_success(boundary):
    mod, project, state = boundary
    old = put(project, "tests/runtime-readiness.json", {"status": "pass", "live": True})
    mod.begin_stage(state, project, "postdeploy")
    assert not old.exists()
    mod.finish_stage(state, project, "postdeploy", False)
    assert mod.export(state, project)
    assert [p.name for p in upload_files(state)] == ["export-status.json"]


@pytest.mark.parametrize("mutation", ["unknown", "nested-private", "generic", "duplicate", "nan", "oversize",
                                      "symlink", "parent-symlink", "hardlink", "changed"])
def test_invalid_outputs_fail_export_without_leaking_or_claiming_success(boundary, mutation):
    mod, project, state = boundary
    mod.begin_stage(state, project, "postdeploy")
    file = put(project, "tests/runtime-readiness.json", diagnostic())
    if mutation == "unknown":
        put(project, file.relative_to(project), {**diagnostic(), "private_material": "PRIVATE"})
    elif mutation == "nested-private":
        put(project, file.relative_to(project), {**diagnostic(), "coverage": {"private_material": "PRIVATE"}})
    elif mutation == "generic":
        file.write_text('{"private_material":"PRIVATE"}')
    elif mutation == "duplicate":
        file.write_text('{"status":"not-verified","status":"pass","live":true,"reason":"PRIVATE"}')
    elif mutation == "nan":
        file.write_text('{"status":"not-verified","live":false,"reason":NaN}')
    elif mutation == "oversize":
        file.write_text("PRIVATE" * mod.MAX_BYTES)
    elif mutation in {"symlink", "hardlink"}:
        private = put(project, "private.json", diagnostic())
        file.unlink()
        if mutation == "symlink":
            file.symlink_to(private)
        else:
            file.hardlink_to(private)
    elif mutation == "parent-symlink":
        (project / "tests").rename(project / "elsewhere")
        (project / "tests").symlink_to(project / "elsewhere", target_is_directory=True)
    mod.finish_stage(state, project, "postdeploy", False)
    if mutation == "changed":
        file.write_text('{"private_material":"PRIVATE changed after producer"}')
    assert not mod.export(state, project)
    assert [p.name for p in upload_files(state)] == ["export-status.json"]
    assert "PRIVATE" not in upload_files(state)[0].read_text()
    assert json.loads(upload_files(state)[0].read_text())["status"] == "failure"


def test_previous_run_attempt_receipts_are_rejected(boundary, monkeypatch):
    mod, project, state = boundary
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    with pytest.raises(ValueError):
        mod.export(state, project)


def test_export_rejects_project_overlapping_private_export_session(boundary):
    mod, _, state = boundary
    with pytest.raises(ValueError):
        mod.begin_stage(state, state.parent, "postdeploy")


def test_strict_governance_validator_runs_before_sanitized_export(boundary):
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    mod, project, state = boundary
    manifest, *_ = live_fixture()
    mod.begin_stage(state, project, "predeploy")
    manifest["private_material"] = "PRIVATE"
    put(project, "specs/governance-manifest.json", manifest)
    mod.finish_stage(state, project, "predeploy", True)
    assert not mod.export(state, project)
    assert [p.name for p in upload_files(state)] == ["export-status.json"]


def test_export_cli_failure_is_nonzero_and_sanitized(boundary):
    mod, project, state = boundary
    mod.begin_stage(state, project, "postdeploy")
    put(project, "tests/runtime-readiness.json", {"PRIVATE": "PRIVATE"})
    mod.finish_stage(state, project, "postdeploy", True)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ci/readiness_evidence.py"),
                             "export", "--project", str(project), "--state", str(state)],
                            text=True, capture_output=True)
    assert result.returncode == 1
    assert "PRIVATE" not in result.stdout + result.stderr


def live_outputs(boundary, monkeypatch):
    from datetime import timedelta
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    from skills._shared import governance_readiness
    mod, project, state = boundary
    manifest, document, current, now = live_fixture()
    monkeypatch.setattr(governance_readiness, "current_context", lambda *_: current)
    mod.begin_stage(state, project, "deploy")
    put(project, ".threadlight/readiness-attempt.json", {
        "run_id": "123", "run_attempt": "2",
        "started_at": (now - timedelta(seconds=20)).isoformat(),
        "completed_at": (now - timedelta(seconds=10)).isoformat(),
    })
    mod.finish_stage(state, project, "deploy", True)
    mod.begin_stage(state, project, "postdeploy")
    put(project, "specs/governance-contract.json", document)
    put(project, "specs/governance-manifest.json", manifest)
    put(project, ".threadlight/governance-live.json", {"governance_manifest": manifest, "governance_gaps": []})
    return manifest, current, now


def test_current_canonical_collector_proof_exports_only_counts(boundary, monkeypatch):
    manifest, _, now = live_outputs(boundary, monkeypatch)
    mod, project, state = boundary
    put(project, "tests/postdeploy-manifest.json", {
        "phase": "post-deploy", "checked_at": now.isoformat(),
        "governance_manifest": manifest, "gaps": ["PRIVATE error body"], "governance_gaps": [],
        "deployment_manifest": {"private_material": "PRIVATE original input"},
        "unknown_operator_field": {"body": "PRIVATE raw request"},
    })
    mod.finish_stage(state, project, "postdeploy", False)
    assert mod.export(state, project)
    files = upload_files(state)
    assert {p.name for p in files} == {"governance-summary.json", "postdeploy-summary.json",
                                      "deployment-attempt.json", "export-status.json"}
    assert all("PRIVATE" not in p.read_text() and "signature" not in p.read_text() for p in files)
    summary = json.loads((state.parent / "upload/postdeploy-summary.json").read_text())
    assert summary["evidence"]["resource_gap_count"] == 1
    assert summary["producer_status"] == "failure"


@pytest.mark.parametrize("mutation", ["stale", "prior-attempt", "changed-policy", "changed-config"])
def test_collector_freshness_and_current_binding_validation_precede_export(boundary, monkeypatch, mutation):
    manifest, current, now = live_outputs(boundary, monkeypatch)
    mod, project, state = boundary
    if mutation in {"stale", "prior-attempt"}:
        path = ".threadlight/readiness-attempt.json"
        deployment = json.loads((project / path).read_text())
        if mutation == "stale":
            deployment["completed_at"] = now.isoformat()
        else:
            deployment["run_attempt"] = "1"
        put(project, path, deployment)
    elif mutation == "changed-policy":
        current["policy_bindings"] = {}
    else:
        current["configuration"] = {}
    mod.finish_stage(state, project, "postdeploy", True)
    assert not mod.export(state, project)
    assert not (state.parent / "upload/governance-summary.json").exists()


def test_real_readiness_result_is_rechecked_without_exporting_reason_text(boundary, monkeypatch):
    live_outputs(boundary, monkeypatch)
    from skills._shared.governance_readiness import assess
    mod, project, state = boundary
    result = assess(project)
    assert result["status"] == "pass"
    put(project, "tests/runtime-readiness.json", result)
    mod.finish_stage(state, project, "postdeploy", True)
    assert mod.export(state, project)
    value = json.loads((state.parent / "upload/runtime-readiness.json").read_text())
    assert value["evidence"] == {"status": "pass", "live": True, "gap_count": 0}


@pytest.mark.parametrize("mutation", [None, "hash", "reference", "missing-start", "private-field", "missing-network"])
def test_real_remote_attempt_and_public_readiness_export_sanitized_linkage(boundary, monkeypatch, mutation):
    from datetime import timedelta
    from scripts.ci import runtime_readiness, runtime_readiness_remote
    from skills._shared import governance_readiness
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    from skills._shared.governance_configuration import public_proof_network_evidence
    from govern_control_plane.bootstrap import BootstrapBinding, SignedBootstrap
    from govern_control_plane.models import canonical, parse
    assert callable(getattr(runtime_readiness_remote, "record_completed_attempt", None)), (
        "remote attempt producer must include start time and bounded bootstrap linkage")
    mod, project, state = boundary
    manifest, document, current, now = live_fixture()
    target = current["expected_target"]
    binding = parse(BootstrapBinding, canonical({
        "schema": "threadlight-hosted-bootstrap/v1", "reference": "attempt-1",
        "tenant_id": target["tenant"], "principal": target["subject"], "client_id": target["client_id"],
        **{key: target[key] for key in ("agent_id", "agent_version", "image_digest",
                                       "environment", "subscription", "resource_group")},
        "project_endpoint": "https://fixture.services.ai.azure.com/api/projects/fixture",
        "key_id": "https://fixture.vault.azure.net/keys/policy/" + "a" * 32,
        "policy_id": "safe", "policy_version": "1", "policy_digest": current["policy_bundle"]["digest"],
        "native_policy_digest": current["policy_bundle"]["digest"], "config_digest": "sha256:" + "a" * 64,
        "issued_at": (now - timedelta(seconds=60)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat()}))
    # Wire-protocol fixture only; exporter must not claim cryptographic attestation.
    signed = SignedBootstrap(binding=binding, signature="dGVzdC1zaWduYXR1cmU=")
    current["bootstrap"] = signed.model_dump(mode="json")
    current["network_evidence"] = public_proof_network_evidence()
    manifest["collection_evidence"].update(
        bootstrap=current["bootstrap"], network_evidence=current["network_evidence"])
    monkeypatch.setattr(governance_readiness, "current_context", lambda *_: current)
    monkeypatch.setattr(runtime_readiness, "utc", lambda: (now - timedelta(seconds=10)).isoformat())
    mod.begin_stage(state, project, "deploy")
    runtime_readiness_remote.record_completed_attempt(
        project, signed, started_at=(now - timedelta(seconds=20)).isoformat())
    actual = json.loads((project / ".threadlight/readiness-attempt.json").read_text())
    assert actual["started_at"] and actual["bootstrap_reference"] == binding.reference
    if mutation == "hash":
        actual["bootstrap_sha256"] = "sha256:" + "f" * 64
    elif mutation == "reference":
        actual["bootstrap_reference"] = "other-attempt"
    elif mutation == "missing-start":
        actual.pop("started_at")
    elif mutation == "private-field":
        actual["private_configuration"] = "PRIVATE"
    put(project, ".threadlight/readiness-attempt.json", actual)
    mod.finish_stage(state, project, "deploy", True)
    mod.begin_stage(state, project, "postdeploy")
    put(project, "specs/governance-contract.json", document)
    put(project, "specs/governance-manifest.json", manifest)
    put(project, ".threadlight/governance-live.json", {"governance_manifest": manifest, "governance_gaps": []})
    result = governance_readiness.assess(project)
    assert result["status"] == "pass" and result["network_evidence"] == current["network_evidence"]
    if mutation == "missing-network":
        result.pop("network_evidence")
    put(project, "tests/runtime-readiness.json", result)
    put(project, ".threadlight/private-configuration.json", {"token": "PRIVATE"})
    (project / "raw.log").write_text("PRIVATE transport diagnostics")
    mod.finish_stage(state, project, "postdeploy", True)
    success = mod.export(state, project)
    exported = state.parent / "upload"
    if mutation is not None:
        assert not success
        assert not (exported / "runtime-readiness.json").exists()
        assert all("PRIVATE" not in file.read_text() for file in exported.iterdir() if file.is_file())
        return
    assert success
    attempt_summary = json.loads((exported / "deployment-attempt.json").read_text())["evidence"]
    readiness_summary = json.loads((exported / "runtime-readiness.json").read_text())["evidence"]
    assert attempt_summary["bootstrap_sha256"] == actual["bootstrap_sha256"]
    assert readiness_summary["network_evidence"]["network_isolation"] == "not-established"
    for file in exported.rglob("*"):
        if file.is_file():
            assert "PRIVATE" not in file.read_text()
            assert "dGVzdC1zaWduYXR1cmU=" not in file.read_text()


@pytest.mark.parametrize("poison", [None, "file", "symlink"])
def test_upload_path_is_released_only_after_sanitized_export(boundary, monkeypatch, poison):
    mod, project, state = boundary
    output = project.parent / "github-output"
    output.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    upload = state.parent / "upload"
    if poison == "file":
        (upload / "private.json").write_text("PRIVATE planted file")
    elif poison == "symlink":
        upload.rmdir()
        upload.symlink_to(project, target_is_directory=True)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ci/readiness_evidence.py"),
                             "export", "--project", str(project), "--state", str(state)],
                            text=True, capture_output=True)
    if poison is None:
        assert result.returncode == 0
        assert output.read_text().strip() == f"upload={upload}"
    else:
        assert result.returncode == 1
        assert output.read_text() == ""
    assert "PRIVATE" not in result.stdout + result.stderr


def test_journal_cannot_smuggle_private_stage_values_into_export(boundary):
    mod, project, state = boundary
    value = json.loads(state.read_text())
    value["stages"]["predeploy"] = "PRIVATE"
    state.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        mod.export(state, project)


def test_driver_records_actual_failed_producer_without_replacing_original_failure(boundary, monkeypatch):
    from scripts.ci import runtime_readiness as driver
    import hashlib
    mod, project, state = boundary
    protected = put(project.parent, "config.json", {"test": True})
    put(project, ".threadlight/ci-input.json", {"digest": "approved"})
    monkeypatch.setenv("READINESS_EVIDENCE_STATE", str(state))
    monkeypatch.setenv("GOVERNANCE_SAFE_PROBE", "true")
    monkeypatch.setenv("GOVERNANCE_CI_CONFIG_SHA256", hashlib.sha256(protected.read_bytes()).hexdigest())
    monkeypatch.setattr(driver, "load_inputs", lambda _: {"input_digest": "approved"})
    def failed_producer(project, _):
        put(project, "tests/runtime-readiness.json", diagnostic())
        raise subprocess.CalledProcessError(17, ["test-only-producer"])
    monkeypatch.setattr(driver, "postdeploy", failed_producer)
    monkeypatch.setattr(sys, "argv", ["runtime_readiness.py", "postdeploy",
                                     "--configuration", str(protected), "--project", str(project)])
    assert driver.main() == 1
    assert json.loads(state.read_text())["stages"]["postdeploy"] == "failure"
    assert mod.export(state, project)
    summary = json.loads((state.parent / "upload/runtime-readiness.json").read_text())
    assert summary["producer_status"] == "failure"


def test_local_artifact_contains_only_validated_step_outcomes(boundary, monkeypatch):
    _, project, state = boundary
    output = project.parent / "github-output"
    output.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    put(project, ".governance-validation/runtime-proof.json", {"body": "PRIVATE"})
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ci/readiness_evidence.py"),
                             "local", "--project", str(project), "--state", str(state),
                             "--native", "success", "--prepared", "failure"],
                            text=True, capture_output=True)
    assert result.returncode == 0
    assert [p.name for p in upload_files(state)] == ["local-contract-status.json"]
    assert "PRIVATE" not in upload_files(state)[0].read_text()
    assert json.loads(upload_files(state)[0].read_text())["prepared"] == "failure"
