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


def _runner():
    return ast.parse((ROOT / "scripts/ci/run-governance-pin-tests.py").read_text())


def _constant_set(tree, name):
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
    return {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)}


def test_copilot_sdk_lanes_are_isolated_and_both_required_in_ci():
    """Issue #206: github-copilot-sdk 1.x and microsoft-copilot-sdk 2.x share the `copilot`
    import package, so each lane has its own wheelhouse and venv, and CI runs both."""
    runner = _runner()
    lines = next(n for n in runner.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "COPILOT_SDK_LINES" for t in n.targets))
    assert ast.literal_eval(lines.value) == {"v1": ("github-copilot-sdk", "1.0.1"),
                                             "v2": ("microsoft-copilot-sdk", "2.0.1")}
    text = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    assert '"github-copilot-sdk==1.0.1"' not in text, "pins come from COPILOT_SDK_LINES only"
    required = _constant_set(runner, "COPILOT_V2_REQUIRED_CASES")
    assert "test_template_real_sdk_tool_call_and_multi_turn_memory" in required
    assert "test_ghcp_native_host_and_actual_hook_schema" in required
    workflow = (ROOT / ".github/workflows/python-pytest.yml").read_text()
    assert "THREADLIGHT_COPILOT_SDK_LINE: v2" in workflow
    assert workflow.count("run-governance-pin-tests.py --deployment") >= 2


def test_copilot_v2_selection_covers_every_required_case():
    runner = _runner()
    selection = next(n for n in runner.body if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", "") == "COPILOT_V2_SELECTION" for t in n.targets)).value.value
    words = [w for w in selection.split() if w != "or"]
    files = list((ROOT / "skills").glob("*/tests/test_*.py"))
    for case in (c for c in _constant_set(runner, "COPILOT_V2_REQUIRED_CASES") if c.startswith("test_")):
        base = case.split("[")[0]
        (owner,) = [f for f in files if f"def {base}(" in f.read_text()]
        # pytest -k matches the test name and its module name.
        assert any(word in case or word in owner.name for word in words), f"{case} not selected by {selection!r}"
