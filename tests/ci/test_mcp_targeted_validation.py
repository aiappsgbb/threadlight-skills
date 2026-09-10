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
