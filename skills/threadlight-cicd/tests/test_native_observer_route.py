"""The CI entry point uses the real packaged native observer, without new PKI."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cicd_native_entry", ROOT / "scripts/agentops_runtime.py")
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)


def test_contract_import_cannot_resolve_to_native_agentops_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "agentops", SimpleNamespace(native_package=True))
    contract = runtime._contract()
    assert callable(contract.load_manifest)
    assert Path(contract.__file__).resolve() == ROOT.parent / "_shared/agentops.py"


def test_packaged_modules_retain_relative_import_context():
    manifest = runtime._packaged("_shared/manifest.py")
    assert manifest.__package__ == "skills._shared"
    assert runtime._contract().__package__ == "skills._shared"


def test_native_observer_is_wired_to_the_single_packaged_implementation():
    assert callable(runtime.observe)
    assert runtime.observe.__module__ == runtime.__name__
    assert Path(runtime._observer().__file__).resolve() == ROOT.parent / "threadlight-agentops/scripts/native_observer.py"


def test_no_opt_in_does_not_load_or_invoke_native_observer(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "observe", lambda *a, **k: pytest.fail("unexpected native execution"))
    assert runtime.main(["--repo", str(tmp_path), "--run-eval"]) == 0


def test_actual_command_capture_is_private_and_preserves_native_exit_two(tmp_path, monkeypatch, capsys):
    observer = runtime._observer()
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    code, output, error = observer.contract.bounded_command(
        [sys.executable, "-c", "import os; assert 'GITHUB_STEP_SUMMARY' not in os.environ; "
         "print('private native output'); raise SystemExit(2)"],
        cwd=tmp_path, timeout=5, max_bytes=1024, env=observer._native_environment(),
    )
    assert code == 2
    assert b"private native output" in output
    assert error == b""
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "summary.md").exists()


def test_actual_command_capture_rejects_oversized_output(tmp_path):
    with pytest.raises(ValueError, match="output"):
        runtime._observer().contract.bounded_command(
            [sys.executable, "-c", "print('private' * 1024)"],
            cwd=tmp_path, timeout=5, max_bytes=1024, env=dict(os.environ),
        )


def test_doctor_is_not_implicit():
    assert runtime.parse_args(["--run-eval"]).refresh_doctor is False
    with pytest.raises(SystemExit):
        runtime.parse_args([])


def evidence(*, operation="eval", quality_failure=False, missing=False, operational_blocker=False):
    caps = {key: {"status": "verified"} for key in (
        "config", "pin", "binding", "integrity", "doctor_freshness", "release_consistency", "workflow",
    )}
    if operation == "eval":
        caps["doctor_freshness"]["status"] = "not-verified"
        caps["release_consistency"]["status"] = "not-verified"
    if missing:
        caps["integrity"]["status"] = "not-verified"
    findings = []
    if quality_failure:
        findings.append({"code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix"})
    if operational_blocker:
        findings.append({"code": "AOPS-DOCTOR-BLOCKED", "owner": "agentops", "severity": "must-fix"})
    return {"agents": [{
        "capabilities": caps, "findings": findings,
        "domains": {"evals": {"status": "verified", "verdict": "fail" if quality_failure else "pass",
                              "summary": {"items_total": 1, "items_passed_all": 1}}},
    }]}


def test_eval_execution_does_not_require_or_invent_doctor_evidence():
    assert runtime.operation_exit_code(evidence(), "eval") == 0


def test_quality_failure_reaches_canonical_gate_but_incomplete_proof_does_not():
    assert runtime.operation_exit_code(evidence(quality_failure=True), "eval") == 2
    assert runtime.operation_exit_code(evidence(missing=True), "eval") == 1
    assert runtime.operation_exit_code(evidence(operational_blocker=True), "eval") == 1
    broken = evidence(quality_failure=True)
    broken["agents"][0]["domains"]["evals"]["summary"]["items_passed_all"] = 0
    assert runtime.operation_exit_code(broken, "eval") == 1


def test_doctor_execution_requires_current_operational_proof():
    assert runtime.operation_exit_code(evidence(operation="doctor"), "doctor") == 0
    assert runtime.operation_exit_code(evidence(), "doctor") == 1


def test_doctor_gate_two_cannot_hide_another_agents_execution_failure():
    document = evidence(operation="doctor", operational_blocker=True)
    document["agents"].extend(evidence(operation="doctor", missing=True)["agents"])
    assert runtime.operation_exit_code(document, "doctor") == 1


def test_no_external_observer_or_signer_contract_in_ci_entry():
    text = (ROOT / "scripts/agentops_runtime.py").read_text()
    assert "observer_script" not in text
    assert "trust_key" not in text
    assert '"openssl"' not in text
    assert '["az", "account", "show"' not in text


def test_runtime_documentation_describes_local_observation_not_new_pki():
    text = (ROOT / "references/agentops-runtime.md").read_text()
    assert "same-process" in text
    assert "observer_script" not in text
    assert "RSA/SHA-256 detached signature" not in text
    assert "not remote attestation" in text


def test_runtime_documentation_matches_consolidated_approval_contract():
    text = (ROOT / "references/agentops-runtime.md").read_text()
    assert "threadlight-agentops-runtime-approval/v1" in text
    assert "threadlight-agentops-execution-approval/v1" not in text
    assert '"context_sha256"' in text
    assert "`artifact_paths`" in text
    assert "threadlight-agentops/scripts/native_observer.py" in text


@pytest.fixture
def native_execution(tmp_path, monkeypatch):
    """Real bounded subprocesses emit native-shaped data; no SDK or Azure call."""
    path = Path(__file__).with_name("native_observer_fixture.py")
    source = importlib.util.spec_from_file_location("cicd_observed_cli_fixture", path)
    fixtures = importlib.util.module_from_spec(source)
    source.loader.exec_module(fixtures)
    observer = runtime._observer()
    fixture = fixtures.NativeExecutionFixture(tmp_path, observer)
    monkeypatch.setattr(observer, "_native_executable", lambda selected: fixture.executable)
    commands = []
    actual = observer.contract.bounded_command
    def record(argv, **kwargs):
        if argv[0] == str(fixture.executable):
            commands.append(list(argv[1:]))
        return actual(argv, **kwargs)
    monkeypatch.setattr(observer.contract, "bounded_command", record)
    try:
        yield fixture, observer, commands
    finally:
        fixture.close()


def _emit_canonical(repo):
    for skill, script in (("threadlight-evals", "evals_check"), ("threadlight-redteam", "redteam_check")):
        module = runtime._packaged(f"{skill}/scripts/{script}.py")
        assert module.main(["--target", str(repo), "--emit"]) == 0


def test_real_runtime_receipt_assessor_and_canonical_roundtrip_without_pki(native_execution, capsys):
    fixture, observer, commands = native_execution
    before = runtime._contract().assess_repository(fixture.repo)
    assert before["agents"][0]["domains"]["evals"]["status"] != "verified"
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 0
    evaluated = runtime._contract().load_manifest(fixture.repo)
    assert evaluated["agents"][0]["domains"]["evals"]["status"] == "verified"
    assert evaluated["agents"][0]["capabilities"]["doctor_freshness"]["status"] == "not-verified"
    assert not any(command[0] == "doctor" for command in commands)
    _emit_canonical(fixture.repo)
    runtime._contract().load_manifest(fixture.repo)
    fixture.approve("doctor")
    assert runtime.main(["--repo", str(fixture.repo), "--refresh-doctor"]) == 0
    _emit_canonical(fixture.repo)
    final = runtime._contract().load_manifest(fixture.repo)
    assert final["verdict"] == "operational"
    canonical = observer.contract.read_json(fixture.repo, "specs/evals-manifest.json")
    assert canonical["agentops"]["source_manifest_sha256"] == observer.contract.canonical_hash(final)
    assert canonical["capabilities"]["latest_pass_rate_ok"]["status"] == "pass"
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1
    assert sum(command[0] == "doctor" for command in commands) == 1
    assert not (fixture.repo / ".private-key.pem").exists()
    assert not (fixture.repo / ".agentops/threadlight/receipt.sig").exists()
    assert not (fixture.repo / "MUST-NOT-WRITE").exists()
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err


def test_real_runtime_quality_exit_two_remains_canonical_failure(native_execution):
    fixture, observer, commands = native_execution
    result = fixture.fixture.native
    result["aggregate_metrics"]["relevance"] = 4.0
    result["rows"][0]["metrics"][0]["value"] = 4.0
    result["thresholds"][0].update(actual="4", passed=False)
    result["summary"].update(thresholds_passed=0, threshold_pass_rate=0.0, overall_passed=False)
    for role in ("result", "latest"):
        fixture.fixture.write(fixture.fixture.roles[role], result)
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 2
    _emit_canonical(fixture.repo)
    receipt = observer.contract.read_json(fixture.repo, ".agentops/threadlight/receipt.json")
    assert receipt["observation"]["exit_code"] == 2
    canonical = observer.contract.read_json(fixture.repo, "specs/evals-manifest.json")
    assert canonical["capabilities"]["latest_pass_rate_ok"]["status"] == "must-fix"
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1


def test_runtime_refuses_missing_or_replayed_approval_without_extra_commands(native_execution):
    fixture, _, commands = native_execution
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert commands == []
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 0
    count = len(commands)
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert len(commands) == count


def test_runtime_refuses_changed_approved_context_without_native_execution(native_execution):
    fixture, _, commands = native_execution
    fixture.approve("eval")
    os.environ["AGENTOPS_OTLP_ENDPOINT"] = "https://unapproved.invalid"
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert commands == []


def test_existing_runner_credential_directories_need_not_be_inside_checkout(native_execution, tmp_path):
    fixture, _, commands = native_execution
    cli = tmp_path / "runner-cli"
    azd = tmp_path / "runner-azd"
    cli.mkdir()
    azd.mkdir()
    os.environ["AZURE_CONFIG_DIR"] = str(cli)
    os.environ["AZD_CONFIG_DIR"] = str(azd)
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 0
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1


def test_existing_unapproved_baseline_cannot_be_silently_omitted(native_execution):
    fixture, _, commands = native_execution
    fixture.fixture.write(".agentops/baseline/results.json", fixture.fixture.native)
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert commands == []


def test_native_exit_zero_without_supported_result_contract_is_not_success(native_execution):
    fixture, _, commands = native_execution
    fixture.fixture.native["version"] = 99
    fixture.fixture.write(".agentops/results/latest/results.json", fixture.fixture.native)
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1


def _approve_baseline(fixture, observer, wrong=None):
    path = ".agentops/baseline/results.json"
    fixture.fixture.write(path, fixture.fixture.native)
    policy = fixture.fixture.policy
    policy.update(
        baseline_sha256=observer.contract.sha256(observer.contract.read_bytes(fixture.repo, path)),
        baseline_dataset_sha256=observer.contract.sha256(observer.contract.read_bytes(fixture.repo, "data.jsonl")),
        baseline_target_sha256=observer.contract.canonical_hash(fixture.fixture.native["target"]),
    )
    policy["artifact_paths"]["baseline"] = path
    if wrong:
        policy[wrong] = "d" * 64
    fixture.fixture.write(".threadlight/agentops-binding.json", policy)
    fixture.fixture.git("add", ".threadlight/agentops-binding.json")
    fixture.fixture.git("commit", "-qm", "Synthetic independent baseline approval")
    fixture.approve("eval")
    return path


@pytest.mark.parametrize("wrong", ["baseline_dataset_sha256", "baseline_target_sha256"])
def test_baseline_must_be_independently_bound_before_native_execution(native_execution, wrong):
    fixture, observer, commands = native_execution
    _approve_baseline(fixture, observer, wrong)
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert commands == []


def test_bound_baseline_is_an_explicit_approved_runtime_input(native_execution):
    fixture, observer, commands = native_execution
    path = _approve_baseline(fixture, observer)
    identity = observer.contract.discover_opted_in_agents(fixture.repo)[0]
    plan = observer._preflight(fixture.repo, identity, "eval", None)
    assert plan["paths"]["baseline"] == path
    assert commands == []


def test_actual_bound_baseline_roundtrip_is_verified_and_never_promoted(native_execution):
    fixture, observer, commands = native_execution
    path = _approve_baseline(fixture, observer)
    before = observer.contract.read_bytes(fixture.repo, path)
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 0
    document = runtime._contract().load_manifest(fixture.repo)
    assert document["agents"][0]["domains"]["evals"]["summary"]["comparison"]["status"] == "verified"
    assert observer.contract.read_bytes(fixture.repo, path) == before
    assert any(len(command) >= 2 and command[-2] == "--baseline"
               and Path(command[-1]).as_posix() == path for command in commands)
    assert all("promote" not in command for command in commands)


def test_requested_comparison_missing_from_native_output_is_not_success(native_execution):
    fixture, observer, commands = native_execution
    _approve_baseline(fixture, observer)
    fixture.fixture.write(".agentops/omit-comparison", "synthetic fault")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 1
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1


def test_doctor_can_reuse_bound_baseline_without_refreshing_its_age(native_execution, monkeypatch):
    from datetime import datetime, timezone
    fixture, observer, commands = native_execution
    path = _approve_baseline(fixture, observer)
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 0
    fixture.approve("doctor")
    baseline_time = fixture.fixture.native["finished_at"]
    actual_freshness = observer.contract._fresh
    monkeypatch.setattr(observer.contract, "_fresh",
                        lambda value, now, hours: False if value == baseline_time else actual_freshness(value, now, hours))
    identity = observer.contract.discover_opted_in_agents(fixture.repo)[0]
    plan = observer._preflight(fixture.repo, identity, "doctor", None)
    assert plan["operation"] == "doctor"
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1


def test_nested_agent_context_binds_repository_deployment_environment(native_execution):
    fixture, observer, _ = native_execution
    root = fixture.repo / "src/agent"
    root.mkdir(parents=True)
    policy = {**fixture.fixture.policy, "artifact_paths": {}}
    fixture.fixture.write("src/agent/.threadlight/agentops-binding.json", policy)
    env = fixture.repo / ".azure/prod/.env"
    env.parent.mkdir(parents=True)
    env.write_text("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://first.invalid\n")
    before = observer.execution_context_hash(fixture.repo, root, dict(os.environ))
    env.write_text("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://changed.invalid\n")
    assert observer.execution_context_hash(fixture.repo, root, dict(os.environ)) != before


def test_agentops_refresh_cli_executes_same_owned_observer_and_emits_one_json(native_execution, capsys):
    fixture, _, commands = native_execution
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval"]) == 0
    fixture.approve("doctor")
    capsys.readouterr()
    assessor = runtime._packaged("threadlight-agentops/scripts/agentops_check.py")
    assert assessor.main(["--target", str(fixture.repo), "--refresh-doctor", "--emit", "--json",
                          "--agentops-bin", str(fixture.executable)]) == 0
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["agents"][0]["capabilities"]["doctor_freshness"]["status"] == "verified"
    assert "PRIVATE" not in output.out + output.err
    assert sum(command[:2] == ["eval", "run"] for command in commands) == 1
    assert sum(command[0] == "doctor" for command in commands) == 1


@pytest.mark.parametrize("floor", ["warning", "info"])
def test_observed_noncritical_doctor_exit_two_is_not_reconstructed_from_must_fix(native_execution, floor):
    fixture, observer, commands = native_execution
    selected = runtime._observer()
    fixture.approve("eval")
    assert runtime.main(["--repo", str(fixture.repo), "--run-eval",
                         "--agentops-bin", str(fixture.executable)]) == 0
    if floor == "info":
        program = fixture.executable.read_text()
        condition = 'sys.argv[sys.argv.index("--severity-fail") + 1] == "warning"'
        assert condition in program
        fixture.executable.write_text(program.replace(
            condition, 'sys.argv[sys.argv.index("--severity-fail") + 1] in {"warning", "info"}'))
    approval = fixture.approve("doctor")
    approval["doctor_severity"] = floor
    fixture.fixture.write(".agentops/threadlight/approvals/doctor.json", approval)
    assert runtime.main(["--repo", str(fixture.repo), "--refresh-doctor",
                         "--agentops-bin", str(fixture.executable)]) == 2
    document = runtime._contract().load_manifest(fixture.repo)
    assert not any(item["severity"] == "must-fix" for item in document["agents"][0]["findings"])
    receipt = observer.contract.read_json(fixture.repo, ".agentops/threadlight/receipt.json")
    assert receipt["observation"]["exit_code"] == 2
    assert runtime._observer() is selected
    assert sum(command[0] == "doctor" for command in commands) == 1
