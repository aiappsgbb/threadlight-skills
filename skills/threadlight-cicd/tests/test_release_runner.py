"""Local process integration, not Azure deployment or model-quality proof."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from test_release_acceptance import document

SPEC = importlib.util.spec_from_file_location(
    "release_runner_under_test", Path(__file__).resolve().parents[1] / "scripts/release_runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

ADAPTER = '''
import hashlib, json, os, pathlib, subprocess, sys
from datetime import datetime, timezone
root = pathlib.Path(".")
request = json.loads(pathlib.Path(os.environ["THREADLIGHT_RELEASE_REQUEST"]).read_text())
phase = sys.argv[1]
now = datetime.now(timezone.utc).isoformat()
if phase in ("prepare", "promote"):
    with pathlib.Path("effects.log").open("a") as file:
        file.write(phase + "\\n")
elif phase == "observe":
    print(json.dumps(dict(
        schema="threadlight-deployment-observation/v1",
        target_id=request["target"]["target_id"], environment=request["target"]["environment"],
        source_sha=request["ci"]["source_sha"], image_digest="sha256:" + "a" * 64,
        version="candidate-1", observed_at=now,
    )))
else:
    report = json.loads(pathlib.Path(f"{phase}-fixture.json").read_text())
    report["captured_at"] = now
    raw_path = f"runs/{phase}.json"
    if phase == "evals":
        report["metrics"]["latest_run"] = raw_path
    else:
        report["scan_result"] = raw_path
        report["scan_captured_at"] = now
    pathlib.Path(raw_path).write_text(json.dumps({
        "finished_at": now,
        "release_binding": {"ci": request["ci"], "candidate": request["candidate"]},
        "pass_rate": 0.98,
    }))
    pathlib.Path(f"specs/{phase}-manifest.json").write_text(json.dumps(report))
'''


@pytest.fixture
def release(tmp_path, monkeypatch):
    for name in ("evals", "redteam"):
        (tmp_path / f"{name}-fixture.json").write_text(json.dumps(document(name)))
    (tmp_path / "adapter.py").write_text(ADAPTER)
    target = dict(environment="validation", tenant_id="tenant", subscription_id="subscription",
                  client_id="validation-client",
                  resource_group="rg-validation", target_id="validation-agent",
                  observe=[sys.executable, "adapter.py", "observe"])
    plan = {
        "schema": "threadlight-release-policy/v1", "max_age_seconds": 900, "timeout_seconds": 30,
        "inputs": ["adapter.py", "evals-fixture.json", "redteam-fixture.json"],
        "validation": dict(target, prepare=[sys.executable, "adapter.py", "prepare"]),
        "production": dict(target, environment="prod", resource_group="rg-prod",
                           client_id="production-client",
                           target_id="production-agent", promote=[sys.executable, "adapter.py", "promote"]),
    }
    for domain in ("evals", "redteam"):
        plan[domain] = dict(
            producer=[sys.executable, "adapter.py", domain],
            outputs=[runner.MANIFESTS[domain], f"runs/{domain}.json"], acceptance={})
    (tmp_path / "policy.json").write_text(json.dumps(plan))
    for command in (["git", "init", "-q"], ["git", "config", "user.name", "Local fixture"],
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    ["git", "add", "."], ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
                         capture_output=True, text=True).stdout.strip()
    values = dict(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="example/release", GITHUB_SHA=sha,
                  GITHUB_RUN_ID="42", GITHUB_RUN_ATTEMPT="1", GITHUB_REF="refs/heads/main",
                  GITHUB_EVENT_NAME="push", THREADLIGHT_RELEASE_ENVIRONMENT="validation")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runner, "verify_identity", lambda plan, phase: None)
    receipt = tmp_path / ".threadlight-release/candidate.json"
    return tmp_path, plan, receipt


def test_real_processes_complete_candidate_and_exact_image_promotion(release):
    root, plan, receipt = release
    runner.validate(root, runner.policy(root, "policy.json"), "policy.json", receipt)
    result = runner.promote(root, plan, "policy.json", receipt, runner.file_digest(receipt))
    assert result["status"] == "production-observed"
    assert (root / "effects.log").read_text().splitlines() == ["prepare", "promote"]


@pytest.mark.parametrize("mutation", ["empty", "partial", "below_threshold", "must_fix"])
def test_failed_quality_never_produces_promotion_receipt(release, mutation):
    root, plan, receipt = release
    fixture = root / "evals-fixture.json"
    report = json.loads(fixture.read_text())
    if mutation == "empty":
        report = {"metrics": {}}
    elif mutation == "partial":
        report["verdict"] = "partial"
    elif mutation == "below_threshold":
        report["metrics"]["pass_rate"] = 0.1
    else:
        report["must_fix"] = ["alert_wired"]
    fixture.write_text(json.dumps(report))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "negative fixture"], cwd=root, check=True)
    os.environ["GITHUB_SHA"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                             check=True, capture_output=True, text=True).stdout.strip()
    with pytest.raises(ValueError):
        runner.validate(root, plan, "policy.json", receipt)
    assert not receipt.exists()
    assert (root / "effects.log").read_text().splitlines() == ["prepare"]


def test_stale_files_cannot_substitute_for_a_producer(release):
    root, plan, receipt = release
    plan["evals"]["producer"] = [sys.executable, "-c", "pass"]
    (root / "policy.json").write_text(json.dumps(plan))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "no producer"], cwd=root, check=True)
    os.environ["GITHUB_SHA"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                             check=True, capture_output=True, text=True).stdout.strip()
    (root / "specs").mkdir()
    (root / "specs/evals-manifest.json").write_text(json.dumps(document("evals")))
    with pytest.raises((ValueError, OSError)):
        runner.validate(root, plan, "policy.json", receipt)
    assert not receipt.exists()


@pytest.mark.parametrize("field,value", [
    ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ID", "43"),
    ("GITHUB_REF", "refs/heads/another"), ("GITHUB_EVENT_NAME", "pull_request"),
])
def test_another_execution_cannot_promote_previous_evidence(release, monkeypatch, field, value):
    root, plan, receipt = release
    runner.validate(root, plan, "policy.json", receipt)
    expected = runner.file_digest(receipt)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError):
        runner.promote(root, plan, "policy.json", receipt, expected)
    assert (root / "effects.log").read_text().splitlines() == ["prepare"]


def test_receipt_tampering_is_rejected_against_ci_job_output(release):
    root, plan, receipt = release
    runner.validate(root, plan, "policy.json", receipt)
    expected = runner.file_digest(receipt)
    data = runner.load(receipt)
    data["candidate"]["image_digest"] = "sha256:" + "b" * 64
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        runner.promote(root, plan, "policy.json", receipt, expected)
    assert (root / "effects.log").read_text().splitlines() == ["prepare"]


def test_same_runner_never_retries_promotion_implicitly(release):
    root, plan, receipt = release
    runner.validate(root, plan, "policy.json", receipt)
    expected = runner.file_digest(receipt)
    runner.promote(root, plan, "policy.json", receipt, expected)
    with pytest.raises((OSError, ValueError)):
        runner.promote(root, plan, "policy.json", receipt, expected)
    assert (root / "effects.log").read_text().splitlines() == ["prepare", "promote"]


def test_preflight_rejects_unknown_acceptance_before_prepare(release):
    root, plan, _ = release
    plan["evals"]["acceptance"] = {"ignore_all_failures": True}
    (root / "policy.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        runner.policy(root, "policy.json")
    assert not (root / "effects.log").exists()


@pytest.mark.parametrize("before,after", [
    ('"finished_at": now', '"finished_at": "2000-01-01T00:00:00+00:00"'),
    ('"ci": request["ci"]', '"ci": {}'),
])
def test_fresh_assessment_cannot_relabel_an_old_or_unbound_run(release, before, after):
    root, plan, receipt = release
    (root / "adapter.py").write_text(ADAPTER.replace(before, after))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "invalid producer binding"], cwd=root, check=True)
    os.environ["GITHUB_SHA"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                             check=True, capture_output=True, text=True).stdout.strip()
    with pytest.raises(ValueError):
        runner.validate(root, plan, "policy.json", receipt)
    assert not receipt.exists()


def test_producer_outputs_cannot_overlap_source(release):
    root, plan, _ = release
    plan["evals"]["outputs"].append("adapter.py")
    (root / "policy.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        runner.policy(root, "policy.json")


def test_targets_cannot_share_the_rg_scoped_deploy_boundary(release):
    root, plan, _ = release
    plan["validation"]["resource_group"] = "rg-prod"
    (root / "policy.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        runner.policy(root, "policy.json")


@pytest.mark.parametrize("argv", [
    ["threadlight-missing-adapter"],
    [sys.executable, ".ci/missing.py"],
    [sys.executable, "-c", "print('not a reviewed entrypoint')"],
    [sys.executable, "-m", "missing_adapter"],
    ["./adapter.py", "prepare"],
])
def test_preflight_rejects_invalid_adapter_entrypoints_before_any_effect(release, argv, capsys):
    root, plan, _ = release
    plan["validation"]["prepare"] = argv
    (root / "policy.json").write_text(json.dumps(plan))
    subprocess.run(["git", "add", "policy.json"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "invalid adapter fixture"], cwd=root, check=True)
    os.environ["GITHUB_SHA"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert runner.main(["preflight", "--repo", str(root), "--policy", "policy.json"]) == 1
    assert "adapter" in capsys.readouterr().err.lower()
    assert not (root / "effects.log").exists()


@pytest.mark.parametrize("change", ["policy", "source", "receipt", "expiry", "ci_attempt"])
def test_authorization_is_rechecked_after_identity_wait_before_promotion(release, monkeypatch, change):
    root, plan, receipt = release
    runner.validate(root, plan, "policy.json", receipt)
    expected = runner.file_digest(receipt)
    original_now = datetime.now(timezone.utc)

    def credential_wait(*_):
        if change == "policy":
            (root / "policy.json").write_text("{}")
        elif change == "source":
            (root / "adapter.py").write_text(ADAPTER + "\n# changed during login\n")
        elif change == "receipt":
            receipt.write_text("{}")
        elif change == "ci_attempt":
            monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
        else:
            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return original_now + timedelta(seconds=plan["max_age_seconds"] + 1)
            monkeypatch.setattr(runner, "datetime", Later)

    monkeypatch.setattr(runner, "verify_identity", credential_wait)
    with pytest.raises(ValueError):
        runner.promote(root, plan, "policy.json", receipt, expected)
    assert (root / "effects.log").read_text().splitlines() == ["prepare"]


def test_validation_rechecks_reviewed_inputs_after_identity_wait(release, monkeypatch):
    root, plan, receipt = release
    def credential_wait(*_):
        (root / "adapter.py").write_text(ADAPTER + "\n# changed during login\n")
    monkeypatch.setattr(runner, "verify_identity", credential_wait)
    with pytest.raises(ValueError):
        runner.validate(root, plan, "policy.json", receipt)
    assert not (root / "effects.log").exists()


def test_ignored_source_cannot_be_deleted_as_a_transient_output(release):
    root, plan, _ = release
    plan["evals"]["outputs"].append("./adapter.py")
    (root / "policy.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="output|path"):
        runner.policy(root, "policy.json")


def test_production_cannot_reuse_validation_principal(release):
    root, plan, _ = release
    plan["production"]["client_id"] = plan["validation"]["client_id"]
    (root / "policy.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="identit"):
        runner.policy(root, "policy.json")


def test_identity_must_match_the_approved_principal_not_just_subscription(release, monkeypatch):
    root, plan, _ = release
    identity_spec = importlib.util.spec_from_file_location("real_identity_check", SPEC.origin)
    identity_module = importlib.util.module_from_spec(identity_spec)
    identity_spec.loader.exec_module(identity_module)
    from types import SimpleNamespace
    monkeypatch.setattr(identity_module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps({
        "tenantId": "tenant", "id": "subscription",
        "user": {"name": "unapproved-client", "type": "servicePrincipal"},
    })))
    with pytest.raises(ValueError, match="principal|identity"):
        identity_module.verify_identity(plan, "validation")
