"""generate_only: Copilot CLI built-in tools stay off unless SPEC § 11e opts in by name (#192).

The GHCP container allows only MCP/custom tools and `skill`; a SPEC that really
needs a built-in (for example `web_fetch`) names it in `copilot_builtin_tools`
with a user opt-in and a reason, and the generator passes exactly those names
through THREADLIGHT_BUILTIN_TOOLS.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from test_generate_only import FIXTURE, GENERATOR, _load_yaml, _run

OPT_IN = """agent_type: hosted
copilot_builtin_tools: web_fetch, view
copilot_builtin_tools_opt_in_source: user
copilot_builtin_tools_reason: "User: the agent must read the public returns policy page"
"""


def _generate(tmp_path: Path, block: str | None):
    project = tmp_path / "order-returns"
    shutil.copytree(FIXTURE, project)
    if block is not None:
        spec = project / "specs" / "SPEC.md"
        spec.write_text(spec.read_text().replace("agent_type: hosted\n", block, 1), encoding="utf-8")
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    return project, result


def _env(project: Path) -> dict:
    target = _load_yaml(project / "azure.yaml")["services"]["order-returns-agent"]
    return {e["name"]: e["value"] for e in target["environmentVariables"]}


def test_default_pilot_enables_no_builtin_tools(tmp_path):
    project, result = _generate(tmp_path, None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "THREADLIGHT_BUILTIN_TOOLS" not in _env(project)


def test_user_opt_in_passes_exactly_the_named_builtins(tmp_path):
    project, result = _generate(tmp_path, OPT_IN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _env(project)["THREADLIGHT_BUILTIN_TOOLS"] == "web_fetch,view"


def test_agent_written_opt_in_is_ignored(tmp_path):
    project, result = _generate(tmp_path, OPT_IN.replace("_source: user", "_source: agent"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "THREADLIGHT_BUILTIN_TOOLS" not in _env(project)
    assert "copilot_builtin_tools ignored" in result.stdout + result.stderr


@pytest.mark.parametrize("value", ["*", "builtin:*", "web_fetch;rm", "shell"])
def test_unknown_or_wildcard_builtins_are_rejected(tmp_path, value):
    _, result = _generate(tmp_path, OPT_IN.replace("web_fetch, view", value))
    assert result.returncode != 0
    assert "copilot_builtin_tools" in result.stdout + result.stderr
