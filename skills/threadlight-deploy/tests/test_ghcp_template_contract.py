"""GHCP hosted-agent template contracts found by the OS1 live deploy.

(C) github-copilot-sdk 1.0.x converts ``mcp_servers`` with ``servers.items()``;
    a list fails with ``AttributeError`` and the Invocations call returns 502.
(A) ``invoke_agent.py`` imports ``operation_evidence`` from the MAF reference.
(B) ``INVOCATION_EVIDENCE_FILE`` is required and write-once.
(D) The deployer needs a Foundry data-plane role at project scope before
    ``azd deploy``; subscription Owner/Contributor alone returns 403 UserError.
"""
import ast
import importlib.util
import os
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GHCP = ROOT / "skills/threadlight-deploy/references/hosted-agent/ghcp"
CONTAINER = GHCP / "references/container.py"
INVOKER = GHCP / "references/invoke_agent.py"
README = GHCP / "README.md"
SKILL = ROOT / "skills/threadlight-deploy/SKILL.md"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"


def _sdk_mcp_servers_to_wire(servers):
    # Mirrors copilot.client._mcp_servers_to_wire in github-copilot-sdk 1.0.15.
    wire = {}
    for name, config in servers.items():
        if "working_directory" in config:
            config = {**config, "cwd": config["working_directory"]}
            del config["working_directory"]
        wire[name] = config
    return wire


def _load_mcp_servers_function():
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_load_mcp_servers"
    )
    namespace = {"os": os}
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(CONTAINER), "exec"), namespace)
    return namespace["_load_mcp_servers"], ast.get_docstring(node) or ""


def test_load_mcp_servers_returns_sdk_dict_shape(monkeypatch):
    load, _ = _load_mcp_servers_function()
    monkeypatch.setenv("MCP_SERVER_FQDN", "mcp.example.azurecontainerapps.io")
    servers = load()
    assert isinstance(servers, dict), "SDK 1.0.x calls servers.items(); a list fails at session creation"
    wire = _sdk_mcp_servers_to_wire(servers)
    assert wire == {
        "mcp": {
            "type": "http",
            "url": "https://mcp.example.azurecontainerapps.io/mcp",
            "tools": ["*"],
        }
    }


def test_load_mcp_servers_none_without_endpoint(monkeypatch):
    load, _ = _load_mcp_servers_function()
    monkeypatch.delenv("MCP_SERVER_FQDN", raising=False)
    assert load() is None


def test_mcp_server_docs_do_not_claim_list_is_accepted():
    _, docstring = _load_mcp_servers_function()
    assert "list[dict]" not in docstring
    readme = README.read_text(encoding="utf-8")
    assert "list[dict] or dict[str, dict]" not in readme
    assert re.search(r"mcp_servers=\{", readme), "README session example must use the dict form"
    assert "mcp_servers=[...]" not in SKILL.read_text(encoding="utf-8")


@pytest.fixture
def invoker(monkeypatch):
    """Import invoke_agent.py as shipped: no PYTHONPATH, stubbed third-party deps."""
    requests_stub = types.ModuleType("requests")
    azure_stub = types.ModuleType("azure")
    identity_stub = types.ModuleType("azure.identity")

    class _NoCredential:
        def get_token(self, *_):
            raise AssertionError("token must not be acquired before evidence-file validation")

    identity_stub.DefaultAzureCredential = _NoCredential
    azure_stub.identity = identity_stub
    monkeypatch.setitem(sys.modules, "requests", requests_stub)
    monkeypatch.setitem(sys.modules, "azure", azure_stub)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_stub)
    monkeypatch.delitem(sys.modules, "operation_evidence", raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "maf/references/python" not in p])
    monkeypatch.delenv("PYTHONPATH", raising=False)
    spec = importlib.util.spec_from_file_location("ghcp_invoke_agent_under_test", INVOKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invoker_imports_operation_evidence_from_skill_layout(invoker):
    assert invoker.SSEFrames.__module__ == "operation_evidence"


def test_invoker_requires_evidence_file(invoker, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://a.services.ai.azure.com/api/projects/p")
    monkeypatch.delenv("INVOCATION_EVIDENCE_FILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["invoke_agent.py", "hi"])
    with pytest.raises(SystemExit) as exc:
        invoker.main()
    assert exc.value.code == 1
    assert "INVOCATION_EVIDENCE_FILE" in capsys.readouterr().out


def test_invoker_rejects_reused_evidence_file(invoker, monkeypatch, capsys, tmp_path):
    existing = tmp_path / "evidence.jsonl"
    existing.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://a.services.ai.azure.com/api/projects/p")
    monkeypatch.setenv("INVOCATION_EVIDENCE_FILE", str(existing))
    monkeypatch.setattr(sys, "argv", ["invoke_agent.py", "hi"])
    with pytest.raises(SystemExit) as exc:
        invoker.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "write-once" in out and str(existing) in out
    assert existing.read_text(encoding="utf-8") == "{}\n"


def test_invoker_usage_documents_pythonpath_and_evidence_file():
    usage = INVOKER.read_text(encoding="utf-8").split('"""')[1]
    readme = README.read_text(encoding="utf-8")
    block = readme.split("### Via curl or the reference script", 1)[1].split("---", 1)[0]
    for text in (usage, block):
        assert "INVOCATION_EVIDENCE_FILE" in text
        assert "maf/references/python" in text
        assert "write-once" in text


def test_deployer_project_role_is_in_the_deploy_flow():
    readme = README.read_text(encoding="utf-8")
    deploying = readme.split("### Deploying user", 1)[1].split("\n## ", 1)[0]
    assert "Foundry User" in deploying and FOUNDRY_USER_ROLE_ID in deploying
    assert "AZURE_AI_PROJECT_ID" in deploying
    assert "403" in deploying and "UserError" in deploying
    assert "learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent" in deploying
    troubleshooting = readme.split("## Troubleshooting", 1)[1].split("\n## ", 1)[0]
    assert any(
        "azd deploy" in row and "403" in row and FOUNDRY_USER_ROLE_ID in row
        for row in troubleshooting.splitlines()
    ), "troubleshooting must map the deploy-time 403 UserError to the project-scope role"

    skill = SKILL.read_text(encoding="utf-8")
    steps = skill.split("## Deploy Steps", 1)[1].split("\n## ", 1)[0]
    deploy_cmd = steps.index("azd up")
    role_step = steps.find(FOUNDRY_USER_ROLE_ID)
    assert 0 <= role_step < deploy_cmd, "deployer project-scope role must precede the deploy command"
    assert "403" in steps


def test_generated_pilot_smoke_copies_helper_and_sets_evidence_file():
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("#### `tests/invoke_agent.py`")
    section = text[start:text.index("\n---", start)]
    assert "operation_evidence.py" in section
    assert "INVOCATION_EVIDENCE_FILE" in section
    assert "write-once" in section
    assert "- [ ] `tests/operation_evidence.py`" in text
    for line in text.splitlines():
        if "python tests/invoke_agent.py" in line:
            assert "INVOCATION_EVIDENCE_FILE" in line, line
