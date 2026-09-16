"""The targeted native runner cannot substitute skipped or incomplete MCP cases."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "governance_pin_runner", ROOT / "scripts/ci/run-governance-pin-tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def report(path, names, fault=None):
    suite = ET.Element("testsuite")
    for index, name in enumerate(names):
        case = ET.SubElement(suite, "testcase", name=name)
        if index == 0 and fault:
            ET.SubElement(case, fault)
    ET.ElementTree(suite).write(path)


def test_mcp_runner_requires_executed_named_contracts(tmp_path):
    assert hasattr(runner, "MCP_REQUIRED_CASES"), "Targeted MCP native runner is missing"
    path = tmp_path / "mcp.xml"
    report(path, sorted(runner.MCP_REQUIRED_CASES))
    assert runner.verify_mcp_junit(path) == len(runner.MCP_REQUIRED_CASES)


def test_mcp_gate_requires_generated_project_closure():
    assert {
        "test_maf_gateway_generate_stage_bind_preserves_responses_and_remote_policy",
        "test_generated_maf_gateway_imports_without_catalog_namespace",
        "test_maf_gateway_remote_bootstrap_stages_final_policy_without_agent_rewrite",
    } <= runner.MCP_REQUIRED_CASES


@pytest.mark.parametrize("deferred", [False, True])
def test_mcp_gate_requires_both_parameterized_native_host_modes(tmp_path, deferred):
    name = f"test_native_responses_host_uses_signed_authority_and_mcp[{deferred}]"
    assert name in runner.MCP_REQUIRED_CASES
    assert "test_native_responses_host_uses_signed_authority_and_mcp" not in runner.MCP_REQUIRED_CASES
    path = tmp_path / "missing-host.xml"
    report(path, sorted(runner.MCP_REQUIRED_CASES - {name}))
    with pytest.raises(RuntimeError, match="MCP"):
        runner.verify_mcp_junit(path)


def test_deferred_gate_requires_actual_human_resume_and_negative_cases(tmp_path):
    assert hasattr(runner, "DEFERRED_REQUIRED_CASES")
    required = runner.DEFERRED_REQUIRED_CASES
    assert "test_deferred_mcp_client_returns_pending_and_resumes_with_the_same_operation" in required
    assert "test_deferred_concurrent_resume_has_one_effect" in required
    assert "test_operator_review_uses_authenticated_human_protocol_not_workload_token[False]" in required
    path = tmp_path / "deferred.xml"
    report(path, sorted(required))
    assert runner.verify_deferred_junit(path) == len(required)
    report(path, sorted(required), "skipped")
    with pytest.raises(RuntimeError, match="deferred"):
        runner.verify_deferred_junit(path)


@pytest.mark.parametrize("fault", ["missing", "empty", "skipped", "failure", "error"])
def test_mcp_runner_rejects_incomplete_or_failed_execution(tmp_path, fault):
    assert hasattr(runner, "MCP_REQUIRED_CASES"), "Targeted MCP native runner is missing"
    names = sorted(runner.MCP_REQUIRED_CASES)
    if fault == "missing":
        names.pop()
    elif fault == "empty":
        names = []
    path = tmp_path / "mcp.xml"
    report(path, names, fault if fault in ("skipped", "failure", "error") else None)
    with pytest.raises(RuntimeError, match="MCP"):
        runner.verify_mcp_junit(path)
