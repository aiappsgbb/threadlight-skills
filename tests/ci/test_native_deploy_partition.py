"""Native deployment cases must execute in the exact-pin job, not disappear."""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("filename,name", [
    ("test_hosted_cohort.py", "test_generation_emits_complete_frozen_cohort_without_changing_shared_pins"),
    ("test_returns_mcp_backend.py", "test_native_server_uses_explicit_operator_state_directory"),
    ("test_returns_mcp_backend.py", "test_unbound_read_uses_injected_host_credential_without_policy"),
])
def test_native_deploy_case_is_partitioned_and_required(filename, name):
    path = f"skills/threadlight-deploy/tests/{filename}"
    module = ast.parse((ROOT / path).read_text())
    case = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name)
    assert "pytest.mark.governance_runtime" in {ast.unparse(item) for item in case.decorator_list}
    runner = ast.parse((ROOT / "scripts/ci/run-governance-pin-tests.py").read_text())
    deployment = next(node for node in runner.body
                      if isinstance(node, ast.FunctionDef) and node.name == "deployment_runtime")
    assert path in {node.value for node in ast.walk(deployment) if isinstance(node, ast.Constant)}
    required = next(node.value for node in ast.walk(deployment) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "required_cases"
                            for target in node.targets))
    assert name in {node.value for node in ast.walk(required) if isinstance(node, ast.Constant)}
