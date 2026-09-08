"""Native fixture coverage belongs to a zero-skip native job, never a missing-SDK unit run."""
import ast
import importlib.util
from pathlib import Path
import shlex
import tomllib
import xml.etree.ElementTree as ET

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SAFE = "skills/threadlight-safe-check/tests/"
DEPLOY = "skills/threadlight-deploy/tests/"
LOCAL = "skills/threadlight-governed-actions/tests/test_native_local_evidence.py"
NATIVE_FAMILIES = {
    SAFE + "test_governance_gates.py": (
        "test_selected_binding_missing_runtime_adapter_is_gap",
        "test_intentionally_unbound_read_tool_is_not_gap",
        "test_real_gateway_staging_static_gate",
        "test_gateway_without_final_binding_cannot_collect_live_evidence",
        "test_native_package_digest_cannot_be_replaced_by_deployment_digest",
        "test_real_gateway_staging_static_rejects_tamper",
        "test_final_gateway_signed_digest_does_not_override_frozen_association",
        "test_static_tamper_is_actionable_gap",
        "test_predeploy_keeps_existing_non_governance_gaps",
        "test_postdeploy_no_contract_never_automatically_invokes_and_keeps_gaps",
        "test_collect_project_has_no_automatic_probe_without_configuration",
        "test_invalid_governance_is_a_gap_not_an_uncaught_exception",
    ),
    SAFE + "test_governance_parent_scope.py": (
        "test_parent_account_mismatch_blocks_real_collector_before_registration",
        "test_parent_missing_context_is_gap_without_registration",
        "test_parent_matching_account_passes_and_pins_all_reads",
        "test_parent_manifest_selectors_must_match_observed_account",
        "test_parent_manifest_display_name_canonicalizes_without_selecting_global_account",
    ),
    SAFE + "test_governance_review_blockers.py": (
        "test_real_generator_preserved_maf_wrapper_gate",
        "test_real_platform_noop_static_association",
    ),
    SAFE + "test_governance_scope_config.py": (
        "test_generated_host_wiring_must_match_before_image",
        "test_normal_environment_and_unknown_governance_secret_are_not_configuration",
        "test_actual_foundry_configuration_wrong_before_or_drifting_after",
        "test_missing_or_unresolved_required_host_configuration_is_unverified",
        "test_parent_target_scope_refuses_without_invocation_and_preserves_inputs",
        "test_reusable_evidence_rejects_target_transplant",
        "test_actual_arm_service_configuration_binds_roles_policy_and_endpoints",
        "test_real_generated_service_environment_overrides_cannot_change_frozen_configuration",
        "test_known_direct_target_mismatch_is_refused_before_invocation",
        "test_declared_file_digest_input_cannot_export_payloads",
        "test_unbound_service_config_override_is_not_silently_accepted",
        "test_full_generated_bound_preflight_and_parameter_drift",
        "test_actual_native_flow_ignores_unselected_environment_changes_and_labels_file_visibility",
        "test_shared_configuration_evidence_rejects_unbounded_payloads",
    ),
    SAFE + "test_governance_probe.py": None,
    SAFE + "test_task12_envelope_binding.py": None,
    DEPLOY + "test_ghcp_relay_transport.py": None,
    LOCAL: None,
}


def functions(path):
    tree = ast.parse((ROOT / path).read_text())
    module_marked = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets)
        and "pytest.mark.governance_runtime" in ast.unparse(node.value)
        for node in tree.body
    )
    return {
        node.name: module_marked or any(
            ast.unparse(decorator) == "pytest.mark.governance_runtime"
            for decorator in node.decorator_list
        )
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }


@pytest.mark.parametrize("path,names", NATIVE_FAMILIES.items())
def test_actual_native_fixture_families_are_explicitly_classified(path, names):
    marked = functions(path)
    assert marked
    assert all(marked[name] for name in names or marked), path


def test_off_and_standalone_stdlib_controls_remain_in_unit_job():
    for path, names in (
        (SAFE + "test_governance_gates.py", ("test_governance_off_and_absent_are_true_noops",)),
        (SAFE + "test_governance_review_blockers.py", (
            "test_valid_off_and_absent_need_no_acs_or_credentials",
            "test_copied_legacy_cli_remains_stdlib_only",
            "test_off_selector_cannot_hide_invalid_or_conflicting_sources",
        )),
    ):
        marked = functions(path)
        assert all(not marked[name] for name in names)
    assert not any(functions(SAFE + "test_safe_check.py").values())


def test_unit_job_partitions_only_native_markers_and_installs_real_collector_sdks():
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-pytest.yml").read_text())
    steps = workflow["jobs"]["pytest"]["steps"]
    for directory in (SAFE.rstrip("/"), DEPLOY.rstrip("/"), "skills/threadlight-governed-actions/tests"):
        command = next(shlex.split(step["run"]) for step in steps if directory in step.get("run", ""))
        assert any(command[i:i + 2] == ["-m", "not governance_runtime"] for i in range(len(command)))
    setup = "\n".join(step.get("run", "") for step in steps if "pip install" in step.get("run", ""))
    project = tomllib.loads((ROOT / "skills/threadlight-safe-check/pyproject.toml").read_text())
    for requirement in project["project"]["dependencies"]:
        if requirement.startswith(("azure-ai-projects==", "openai==")):
            assert requirement in setup
    native = workflow["jobs"]["native-generation"]["steps"]
    local = next(step for step in native if "--native-local" in step.get("run", ""))
    assert "continue-on-error" not in local and "if" not in local


def test_every_partitioned_family_has_a_full_native_runner_owner():
    tree = ast.parse((ROOT / "scripts/ci/run-governance-pin-tests.py").read_text())
    owners = {
        node.name: {
            item.value for item in ast.walk(node)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
            and item.value.startswith("skills/") and "/tests" in item.value
        }
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in ("deployment_runtime", "native_local_runtime")
    }
    assert "native_local_runtime" in owners
    paths = {
        str(path.relative_to(ROOT))
        for directory in (SAFE, DEPLOY, "skills/threadlight-governed-actions/tests")
        for path in (ROOT / directory).glob("test_*.py")
        if any(functions(str(path.relative_to(ROOT))).values())
    }
    for path in paths:
        selected = owners["native_local_runtime" if path == LOCAL else "deployment_runtime"]
        assert any(path == item or path.startswith(item.rstrip("/") + "/") for item in selected), path
    source = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    assert "python:3.12-bookworm" in source, "native ledger fixtures require real Git, not main-checkout metadata mounts"
    assert "native-local-tests.xml" in source


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("partition_runner", ROOT / "scripts/ci/run-governance-pin-tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_report(runner, path, fault=None):
    root = ET.Element("testsuite")
    for name in sorted(runner.native_local_cases()):
        ET.SubElement(root, "testcase", name=name)
    if fault == "missing":
        root.remove(root[0])
    elif fault:
        ET.SubElement(root[0], fault)
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize("fault", [None, "missing", "skipped", "failure", "error"])
def test_native_local_ci_rejects_every_nonpassing_or_missing_case(runner, tmp_path, fault):
    report = tmp_path / "report.xml"
    native_report(runner, report, fault)
    if fault:
        with pytest.raises(RuntimeError):
            runner.verify_native_local_junit(report)
    else:
        assert runner.verify_native_local_junit(report) == 34


def test_native_local_host_accepts_linux_interpreter_symlink_and_mounts_read_only(runner, tmp_path, monkeypatch):
    venv = tmp_path / "linux-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to("/missing-on-the-host/python3.12")
    (venv / "pyvenv.cfg").write_text("version = 3.12\n")
    (tmp_path / "wheels").mkdir()
    monkeypatch.setattr(runner, "SCRATCH", tmp_path)
    monkeypatch.setattr(runner, "VENV", venv)
    commands = []
    def execute(command):
        commands.append(command)
        native_report(runner, tmp_path / "native-local-tests.xml")
    monkeypatch.setattr(runner, "run", execute)
    runner.native_local_runtime()
    assert len(commands) == 1
    assert f"{ROOT}:/work:ro" in commands[0]
    assert f"{venv}:/work/.governance-validation/linux-venv:ro" in commands[0]
    assert "--network" in commands[0] and "none" in commands[0]
    assert not any(".git:" in str(value) for value in commands[0])
