"""Cold CI setup must support real protocol imports and caller-owned artifacts."""

import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "governance_pin_runner", ROOT / "scripts/ci/run-governance-pin-tests.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("local_only", [False, True])
def test_deployment_containers_preserve_caller_fixture_ownership(runner, tmp_path, monkeypatch, local_only):
    pins = json.loads(runner.PIN_FILE.read_text())
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(runner, "SCRATCH", scratch)
    monkeypatch.setattr(runner, "VENV", scratch / "linux-venv")
    monkeypatch.setattr(runner, "verify_wheels", lambda *_: None)
    (scratch / "opa-linux-amd64").write_bytes(b"fixture-opa")
    pins["opa"]["linux_amd64_static_sha256"] = runner.hashlib.sha256(b"fixture-opa").hexdigest()
    commands = []

    def run(command, **kwargs):
        commands.append([str(arg) for arg in command])
        if "--deployment-prepared" in command:
            fixture = scratch / "deployment-fixtures/test_generated_maf_native0/external-pilot"
            fixture.mkdir(parents=True)
            (scratch / "deployment-proof.json").write_text("{}")

    monkeypatch.setattr(runner, "run", run)
    runner.prepare_deployment(pins, local_only=local_only)
    containers = [command for command in commands if command[0] == "docker"]
    assert len(containers) == (1 if local_only else 2)
    for command in containers:
        assert "--user" in command, "root-owned pytest 0700 directories are invisible to the CI caller"
        assert command[command.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert f"{ROOT}:/work:ro" in containers[0]
    assert "HOME=/work/.governance-validation/deployment-home" in containers[0]
    assert (scratch / "deployment-home").is_dir()
    assert not any(".git:" in arg for command in containers for arg in command)


def test_prepared_runtime_installs_real_services_before_bootstrap_tests(runner, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    python = scratch / "linux-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(runner, "SCRATCH", scratch)
    monkeypatch.setattr(runner, "VENV", python.parent.parent)
    monkeypatch.setattr(runner, "verify_wheels", lambda *_: None)
    monkeypatch.setattr(sys, "argv", [str(runner.__file__), "--prepared"])
    monkeypatch.setattr(runner.subprocess, "run", lambda *_, **__: type("Result", (), {"returncode": 1})())
    commands = []
    monkeypatch.setattr(runner, "run", lambda command, **_: commands.append([str(arg) for arg in command]))

    runner.main()

    service_builds = [command for command in commands if "wheel" in command]
    assert service_builds, "cold native bootstrap must install actual source service wheels"
    installs = [command for command in commands if "install" in command]
    for requirement in runner.gateway_requirements():
        assert any(requirement in command for command in installs), requirement
    assert any("pip" in command and "check" in command for command in commands)
    runtime = next(i for i, command in enumerate(commands) if "--runtime" in command)
    assert all(commands.index(command) < runtime for command in installs)
    assert any(
        "govern_control_plane.bootstrap" in arg and "govern_control_plane.storage" in arg
        for command in commands[:runtime] for arg in command
    ), "validate real bootstrap and storage imports before native tests"


def test_default_runtime_preserves_ownership_for_following_prepare_local(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SCRATCH", tmp_path)
    monkeypatch.setattr(runner, "verify_wheels", lambda *_: None)
    monkeypatch.setattr(sys, "argv", [str(runner.__file__)])
    monkeypatch.setitem(sys.modules, "governance_ctk", SimpleNamespace(build=lambda: None))
    (tmp_path / "opa-linux-amd64").touch()
    commands = []
    monkeypatch.setattr(runner, "run", lambda command, **_: commands.append([str(arg) for arg in command]))

    runner.main()

    container = next(command for command in commands if command[0] == "docker")
    assert "--user" in container, "root runtime reinstalls would break the next caller-owned deployment preparation"
    assert container[container.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "HOME=/workspace/.governance-validation/runtime-home" in container
    assert (tmp_path / "runtime-home").is_dir()
    assert f"type=bind,source={ROOT},target=/workspace,readonly" in container


@pytest.mark.parametrize("candidates", [0, 1, 2])
def test_generated_fixture_handoff_counts_real_artifacts_not_pytest_aliases(
    runner, tmp_path, monkeypatch, candidates
):
    monkeypatch.setattr(runner, "SCRATCH", tmp_path)
    base = tmp_path / "deployment-fixtures"
    base.mkdir()
    for index in range(candidates):
        parent = base / f"test_generated_maf_native_cons{index}"
        (parent / "external-pilot").mkdir(parents=True)
    if candidates:
        (base / "test_generated_maf_native_conscurrent").symlink_to(
            base / "test_generated_maf_native_cons0", target_is_directory=True
        )
    if candidates == 1:
        assert runner.generated_deployment_fixture() == base / "test_generated_maf_native_cons0/external-pilot"
    else:
        with pytest.raises(RuntimeError, match="generated external fixture"):
            runner.generated_deployment_fixture()


def test_pytest_workflow_installs_protocol_services_before_any_suites():
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-pytest.yml").read_text())
    steps = workflow["jobs"]["pytest"]["steps"]
    first_suite = next(i for i, step in enumerate(steps) if "-m pytest " in step.get("run", ""))
    setup = "\n".join(step.get("run", "") for step in steps[:first_suite])
    assert '"jsonschema[format]==4.26.0"' in setup
    assert "pip install" in setup and "./skills/threadlight-govern/references/control-plane" in setup
    assert "pip install --no-deps ./skills/threadlight-govern/references/gateway" in setup
    gateway = tomllib.loads(
        (ROOT / "skills/threadlight-govern/references/gateway/pyproject.toml").read_text()
    )
    for requirement in gateway["project"]["dependencies"]:
        if requirement.startswith(("mcp==", "httpcore==")):
            assert requirement in setup, "nonnative gateway import tests require the real service dependencies"


def test_deployment_gate_requires_current_native_transport_matrix(runner):
    tree = ast.parse(Path(runner.__file__).read_text())
    deployment = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "deployment_runtime")
    required = next(
        ast.literal_eval(node.value) for node in ast.walk(deployment)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "required_cases" for target in node.targets)
    )
    expected = {
        f"test_real_model_pool_wait_reauthorizes_before_any_http_bytes[{state}-{secure}-{instrumented}]"
        for state in ("expiry", "revocation", "valid")
        for secure in (False, True) for instrumented in (False, True)
    } | {
        f"test_real_model_send_rechecks_after_handshake_credentials_and_caller_trace[{state}-{wait_at}-{instrumented}]"
        for state in ("expiry", "revocation", "valid")
        for wait_at in ("tls", "credential", "caller-trace") for instrumented in (False, True)
    } | {
        f"test_native_h1_checks_actual_core_wire_after_retained_trace[{mutation}-{stage}-{mode}]"
        for mutation in (
            "unchanged", "authorization", "extra-header", "traceparent", "target", "request", "stream",
            "injected-authorization", "injected-extra-header", "injected-baggage",
        )
        for stage in ("headers", "body") for mode in ("native", "bootstrap")
    }
    assert expected <= required
    assert not {
        "test_real_model_pool_wait_reauthorizes_before_any_http_bytes[expiry-False]",
        "test_real_model_pool_wait_reauthorizes_before_any_http_bytes[revocation-True]",
        "test_native_h1_checks_actual_core_wire_after_retained_trace[stream-headers]",
        "test_native_h1_checks_actual_core_wire_after_retained_trace[stream-body]",
    } & required
