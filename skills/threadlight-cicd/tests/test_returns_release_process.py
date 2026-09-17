"""Process-emulated operators/providers: no Azure, native model, Outlook or business proof."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_returns_release import SCRIPTS, adapter, behavior, external, observation

sys.path.insert(0, str(SCRIPTS))
import generate_pipeline
import release_runner

FIXTURE = '''
import json, os, pathlib, sys
from datetime import datetime, timezone
root = pathlib.Path(".")
request = json.loads(pathlib.Path(os.environ["THREADLIGHT_RELEASE_REQUEST"]).read_text())
action = sys.argv[1]
with pathlib.Path(".threadlight-release-private/effects.log").open("a") as stream:
    stream.write(action + "\\n")
config = json.loads(pathlib.Path("specs/returns-release.json").read_text())
if action == "observe":
    phase = request["target"]["environment"]
    result = json.loads(pathlib.Path("observations.json").read_text())[phase]
    result["source_sha"] = request["ci"]["source_sha"]
    result["observed_at"] = datetime.now(timezone.utc).isoformat()
    result["application_contract"]["behavior"] = config["behavior"]
    print(json.dumps(result))
elif action in ("evals", "redteam"):
    output = "evals/runs/release.json" if action == "evals" else "redteam/runs/release.json"
    now = datetime.now(timezone.utc).isoformat()
    raw = dict(provider="local-test-process-not-native", run_id="fixture-run",
        started_at=now, finished_at=now, captured_at=now,
        release_binding={"ci":request["ci"], "candidate":request["candidate"]})
    if action == "evals":
        raw["pass_rate"] = json.loads(pathlib.Path("quality.json").read_text())["pass_rate"]
    else:
        raw.update(tool="local-test-not-scanner", num_attacks=100, strategies=["fixture"],
            attack_success_rate={name: 0 for name in
                ("jailbreak", "prompt_injection", "indirect_attack", "exfiltration", "harmful_content")})
    pathlib.Path(output).write_text(json.dumps(raw))
elif action in ("stop", "admission", "operation", "promote", "admit"):
    state_file = pathlib.Path(".threadlight-release-private/remote-emulator.json")
    state = json.loads(state_file.read_text()) if state_file.exists() else dict(
        state="absent", admission="closed", operation_id=request["operation_id"])
    if action == "stop":
        state["admission"] = "closed"
    if action == "promote":
        state.update(state="prepared", image_digest=request["candidate"]["image_digest"])
    if action == "admit":
        state.update(state="admitted", admission="open")
    state_file.write_text(json.dumps(state))
    print(json.dumps(dict(state, state=state["admission"], target_id=request["target"]["target_id"])
        if action == "admission" else state))
elif action == "postcheck":
    import hashlib
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    checks = ("native_readiness", "signed_binding", "policy_runtime", "tool_inventory",
        "role_map", "cosmos_target", "outlook_authority", "central_audit")
    print(json.dumps(dict(operation_id=request["operation_id"], target_id=request["target"]["target_id"],
        observed_at=datetime.now(timezone.utc).isoformat(),
        application_contract_sha256=digest(request["production_observation"]["application_contract"]),
        checks={name:dict(status="pass", evidence_sha256="1"*64) for name in checks})))
else:
    print("{}")
'''


@pytest.fixture
def selected(tmp_path, monkeypatch):
    generate_pipeline.generate({"platform": "github-actions", "central_env_required": False,
                                "reference_application": "returns-mcp/v1"}, tmp_path)
    for name, body in {
        "operator.py": FIXTURE,
        "quality.json": json.dumps({"pass_rate": 0.99}),
        "observations.json": json.dumps({phase: observation(phase) for phase in ("validation", "production")}),
        "evals/scenarios.json": json.dumps({"scenario_id": "fixture", "tool_calls": [], "tool_outputs": []}),
        "evals/evaluation.py": "# local assessor inventory fixture only; not executed schedule\n"
                               "# continuous evaluation schedule threshold: 0.95\n"
                               "# create_agent_evaluation(app_insights_connection_string=fixture)\n"
                               "# eval threshold breach alert; champion challenger comparison\n",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    package_root = tmp_path / "source"
    manifest = {"schema": "threadlight-returns-mcp-source/v1", "files": {}}
    module = adapter()
    for name in module.SOURCE_FILES:
        path = package_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# TEST source closure marker only, not packaged native runtime\n")
        manifest["files"][name] = release_runner.file_digest(path)
    package = package_root / "source-package.json"
    package.write_text(json.dumps(manifest))
    config = {
        "schema": "threadlight-returns-release/v1", "source_package": "source/source-package.json",
        "behavior": dict(behavior(), source_package_sha256=release_runner.file_digest(package)),
        "owners": {key: "fixture-owner" for key in (
            "application", "central_platform", "production_approver", "human_reviewer", "incident")},
        "targets": {phase: {"external": external(phase), "operators": {
            name: [sys.executable, "operator.py", name] for name in names}}
            for phase, names in module.OPS.items()},
        "producers": {domain: {"execute": [sys.executable, "operator.py", domain],
                               "raw_output": f"{domain}/runs/release.json"}
                      for domain in ("evals", "redteam")},
    }
    (tmp_path / "specs/returns-release.json").write_text(json.dumps(config))
    plan = json.loads((tmp_path / "specs/release-policy.example.json").read_text())
    plan["inputs"] = ["operator.py", "quality.json", "observations.json",
                      "evals/scenarios.json", "evals/evaluation.py"]
    for phase in ("validation", "production"):
        plan[phase].update(environment=phase, tenant_id="tenant", subscription_id="subscription",
                           client_id=phase + "-client", resource_group=phase + "-rg", target_id=phase + "-agent")
        for name in ("prepare", "promote", "observe"):
            if name in plan[phase]:
                plan[phase][name][0] = sys.executable
    for domain in ("evals", "redteam"):
        plan[domain]["producer"][0] = sys.executable
    (tmp_path / "specs/release-policy.json").write_text(json.dumps(plan))
    fake_az = tmp_path / "bin/az"
    fake_az.parent.mkdir()
    fake_az.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib
r = json.loads(pathlib.Path(os.environ["THREADLIGHT_RELEASE_REQUEST"]).read_text())["target"]
print(json.dumps({"tenantId":r["tenant_id"],"id":r["subscription_id"],
                  "user":{"type":"servicePrincipal","name":r["client_id"]}}))
''')
    fake_az.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_az.parent) + os.pathsep + os.environ["PATH"])
    (tmp_path / ".gitignore").write_text((tmp_path / ".gitignore").read_text() +
                                        "\n**/__pycache__/\n")
    for command in (["git", "init", "-q"], ["git", "config", "user.name", "Local fixture"],
                    ["git", "config", "user.email", "fixture@example.invalid"]):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    commit_fixture(tmp_path, monkeypatch)
    for key, value in {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "example/fixture", "GITHUB_RUN_ID": "4",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push",
        "THREADLIGHT_RELEASE_ENVIRONMENT": "validation",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(release_runner, "verify_identity", lambda *args: None)
    return tmp_path, plan, tmp_path / ".threadlight-release/candidate.json"


def commit_fixture(root, monkeypatch):
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "Local test fixture"], cwd=root, check=True, capture_output=True)
    monkeypatch.setenv("GITHUB_SHA", subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip())


def test_selected_reference_completes_existing_engine_with_real_subprocesses(selected, monkeypatch):
    root, plan, receipt = selected
    plan = release_runner.policy(root, "specs/release-policy.json")
    release_runner.validate(root, plan, "specs/release-policy.json", receipt)
    expected = release_runner.file_digest(receipt)
    monkeypatch.setenv("THREADLIGHT_RELEASE_ENVIRONMENT", "production")
    result = release_runner.promote(root, plan, "specs/release-policy.json", receipt, expected)
    assert result["application"]["state"] == "admitted"
    assert result["deployment"]["image_digest"] == release_runner.load(receipt)["candidate"]["image_digest"]
    assert release_runner.file_digest(receipt) == expected
    events = (root / ".threadlight-release-private/effects.log").read_text().splitlines()
    assert events.index("evals") < events.index("redteam") < events.index("promote")
    assert events.index("postcheck") < events.index("admit")


def test_selected_bad_candidate_never_calls_production(selected, monkeypatch):
    root, plan, receipt = selected
    (root / "quality.json").write_text(json.dumps({"pass_rate": 0.1}))
    commit_fixture(root, monkeypatch)
    with pytest.raises(ValueError):
        release_runner.validate(root, plan, "specs/release-policy.json", receipt)
    assert not receipt.exists()
    events = (root / ".threadlight-release-private/effects.log").read_text().splitlines()
    assert "promote" not in events and "admit" not in events


def test_selected_postdeployment_drift_never_admits(selected, monkeypatch):
    root, plan, receipt = selected
    observed = json.loads((root / "observations.json").read_text())
    observed["production"]["application_contract"]["external"]["connections"]["gateway"]["revision"] = "drift"
    (root / "observations.json").write_text(json.dumps(observed))
    commit_fixture(root, monkeypatch)
    release_runner.validate(root, plan, "specs/release-policy.json", receipt)
    monkeypatch.setenv("THREADLIGHT_RELEASE_ENVIRONMENT", "production")
    with pytest.raises(ValueError):
        release_runner.promote(root, plan, "specs/release-policy.json", receipt, release_runner.file_digest(receipt))
    events = (root / ".threadlight-release-private/effects.log").read_text().splitlines()
    assert "promote" in events and "admit" not in events
    assert json.loads((root / ".threadlight-release-private/remote-emulator.json").read_text())["admission"] == "closed"


def test_input_change_during_last_admission_read_never_opens(selected, monkeypatch):
    root, plan, receipt = selected
    # The final closed read simulates an independent transport wait during which source changes.
    fixture = FIXTURE.replace(
        'state_file.write_text(json.dumps(state))',
        '''if action == "admission":
        count = state.get("reads", 0) + 1
        state["reads"] = count
        if count == 4:
            pathlib.Path("quality.json").write_text('{"pass_rate":0.5}')
    state_file.write_text(json.dumps(state))''')
    (root / "operator.py").write_text(fixture)
    commit_fixture(root, monkeypatch)
    release_runner.validate(root, plan, "specs/release-policy.json", receipt)
    monkeypatch.setenv("THREADLIGHT_RELEASE_ENVIRONMENT", "production")
    with pytest.raises(ValueError):
        release_runner.promote(root, plan, "specs/release-policy.json", receipt, release_runner.file_digest(receipt))
    events = (root / ".threadlight-release-private/effects.log").read_text().splitlines()
    assert "admit" not in events


def test_reconcile_closes_and_reads_unknown_operation_without_retry(selected, monkeypatch):
    root, plan, _ = selected
    private = root / ".threadlight-release-private"
    private.mkdir(exist_ok=True)
    operation_id = "a" * 64
    (private / "remote-emulator.json").write_text(json.dumps({
        "operation_id": operation_id, "state": "unknown", "admission": "open"}))
    monkeypatch.setenv("THREADLIGHT_RELEASE_ENVIRONMENT", "production")
    result = release_runner.reconcile_application(root, plan, "specs/release-policy.json", operation_id)
    assert result["state"] == "unknown"
    assert (private / "effects.log").read_text().splitlines() == ["stop", "admission", "operation"]
    assert json.loads((private / "remote-emulator.json").read_text())["admission"] == "closed"
