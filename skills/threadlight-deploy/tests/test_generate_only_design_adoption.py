"""design -> deploy chain: generate_only adopts the MCP server threadlight-design wrote.

threadlight-design owns ``src/mcp/server.py`` (tool logic and its safeguards).
threadlight-deploy generate_only owns the packaging around it (Dockerfile,
hashed requirements.lock, the pinned-SDK entry point) and everything else it
renders. The documented workshop chain must work without ``--force`` and must
never discard design-owned code.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from test_generate_only import GENERATOR, HARNESS, PILOT, SELFCHECK, _run

DEPLOY = Path(__file__).resolve().parents[1]
FIXTURE = DEPLOY / "tests" / "fixtures" / "generate-only" / "design-owned-mcp"
ORDER_RETURNS = DEPLOY / "tests" / "fixtures" / "generate-only" / "order-returns"
DESIGN_TOOLS = ("returns_get_case", "returns_list_open", "customer_get_profile", "returns_record_recommendation")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def adopted(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("adopt")
    project = tmp / "returns-triage"
    shutil.copytree(FIXTURE, project)
    result = _run([str(GENERATOR), "--project", str(project)], tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    return project


def _load(path: Path, name: str, data: Path):
    import os
    os.environ["SAMPLE_DATA_DIR"] = str(data / "sample-data")
    os.environ["KNOWLEDGE_DIR"] = str(data / "knowledge")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


# --- ownership: no --force, design code byte-identical ---------------------------

def test_design_server_is_adopted_without_force_and_kept_byte_identical(adopted: Path):
    assert _sha(adopted / "src" / "mcp" / "server.py") == _sha(FIXTURE / "src" / "mcp" / "server.py")
    record = json.loads((adopted / "specs" / "mcp-adoption.json").read_text())
    assert record["server"]["owner"] == "threadlight-design"
    assert record["server"]["sha256"] == _sha(FIXTURE / "src" / "mcp" / "server.py")
    assert sorted(record["server"]["tools"]) == sorted(DESIGN_TOOLS)
    assert record["knowledge_standin_tools"] == ["return_policy_search"]


def test_legacy_design_packaging_is_replaced_by_pinned_packaging_but_preserved(adopted: Path):
    docker = (adopted / "src" / "mcp" / "Dockerfile").read_text()
    assert "--require-hashes" in docker and ">=" not in docker
    assert "threadlight_mcp_entry.py" in docker
    assert "specs/knowledge/" in docker
    # The root .dockerignore is an allowlist: the corpus must be let through.
    assert "!specs/knowledge/**" in (adopted / ".dockerignore").read_text()
    assert not (adopted / "src" / "mcp" / "pyproject.toml").exists(), "unpinned fastmcp must not ship"
    lock = (adopted / "src" / "mcp" / "requirements.lock").read_text()
    assert lock == (PILOT / "mcp" / "requirements.lock").read_text()
    record = json.loads((adopted / "specs" / "mcp-adoption.json").read_text())
    replaced = {r["path"]: r for r in record["replaced_design_packaging"]}
    assert set(replaced) == {"src/mcp/Dockerfile", "src/mcp/pyproject.toml"}
    original = (FIXTURE / "src" / "mcp" / "pyproject.toml").read_text()
    assert replaced["src/mcp/pyproject.toml"]["original_text"] == original
    assert "unpinned" in replaced["src/mcp/pyproject.toml"]["reason"]


def test_adopted_entry_serves_design_safeguards(adopted: Path):
    pytest.importorskip("mcp.server.fastmcp")
    entry = _load(adopted / "src" / "mcp" / "threadlight_mcp_entry.py", "adopted_entry", adopted / "specs")
    profile = entry.customer_get_profile(customer_id="CUST-01")
    assert profile["email_masked"].startswith("a***") and "email" not in profile
    assert entry.returns_record_recommendation(
        rma_id="RMA-001", decision="settle_now", citations=["RP-2.1"], rationale="x")["error"] == "invalid_decision"
    assert entry.returns_record_recommendation(
        rma_id="RMA-001", decision="approve_refund", citations=[], rationale="x")["error"] == "missing_citation"
    hit = entry.return_policy_search(query="RP-4.1 value ceiling")
    assert hit["citations"][0]["clause_id"] == "RP-4.1"
    assert "local stand-in" in hit["backing"]
    # Paragraph-level clause ids under a section heading are cited precisely.
    fault = entry.return_policy_search(query="RP-3.2 photo evidence")
    assert fault["citations"][0]["clause_id"] == "RP-3.2"
    assert "photo" in fault["citations"][0]["text"]
    names = {t.name for t in asyncio.run(entry.mcp.list_tools())}
    assert names == {*DESIGN_TOOLS, "return_policy_search"}


def _set_level_ok(server) -> bool:
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    async def probe():
        async with connect(server._mcp_server) as session:
            await session.set_logging_level("info")
            return True
    return asyncio.run(probe())


def test_adopted_entry_answers_logging_set_level(adopted: Path):
    pytest.importorskip("mcp.server.fastmcp")
    entry = _load(adopted / "src" / "mcp" / "threadlight_mcp_entry.py", "adopted_entry_log", adopted / "specs")
    assert _set_level_ok(entry.mcp)


def test_generated_server_answers_logging_set_level(tmp_path: Path):
    pytest.importorskip("mcp.server.fastmcp")
    project = tmp_path / "order-returns"
    shutil.copytree(ORDER_RETURNS, project)
    assert _run([str(GENERATOR), "--project", str(project)], tmp_path).returncode == 0
    server = _load(project / "src" / "mcp" / "server.py", "generated_server_log", project / "specs")
    assert _set_level_ok(server.mcp)


def test_rerun_keeps_design_server_and_force_only_touches_generated_files(adopted: Path, tmp_path: Path):
    again = _run([str(GENERATOR), "--project", str(adopted)], tmp_path)
    assert again.returncode == 5, again.stdout + again.stderr
    assert "src/mcp/server.py" not in again.stderr
    before = json.loads((adopted / "specs" / "mcp-adoption.json").read_text())["replaced_design_packaging"]
    assert before
    forced = _run([str(GENERATOR), "--project", str(adopted), "--force"], tmp_path)
    assert forced.returncode == 0, forced.stderr
    assert _sha(adopted / "src" / "mcp" / "server.py") == _sha(FIXTURE / "src" / "mcp" / "server.py")
    # The preserved original design packaging survives a --force re-run.
    after = json.loads((adopted / "specs" / "mcp-adoption.json").read_text())["replaced_design_packaging"]
    assert after == before


def test_generated_tests_selfcheck_and_harness_pass_on_adopted_repo(adopted: Path, tmp_path: Path):
    tests = adopted / "src" / "agent" / "tests"
    result = _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests)], tmp_path, cwd=adopted)
    assert result.returncode == 0, result.stdout + result.stderr
    report = tmp_path / "sc.json"
    check = _run([str(SELFCHECK), "--project", str(adopted), "--report", str(report),
                  "--skip-docker", "--skip-bicep"], tmp_path)
    assert check.returncode == 0, check.stdout + check.stderr
    pytest.importorskip("mcp.server.fastmcp")
    out = tmp_path / "pt.json"
    harness = _run([str(HARNESS), "--project", str(adopted), "--report", str(out)], tmp_path)
    calls = {c["tool"]: c for c in json.loads(out.read_text())["calls"]}
    assert set(calls) == {*DESIGN_TOOLS, "return_policy_search"}
    assert all(c["ok"] for c in calls.values()), calls
    # Only optional arguments: an empty call is a valid call, not a degenerate one.
    assert calls["returns_list_open"]["arguments"] == {}
    # The design's decision validation rejected the literal fallback first; the
    # harness retried with the first value the server itself declares valid.
    assert calls["returns_record_recommendation"]["arguments"]["decision"] == "approve_refund"
    assert harness.returncode == 0, harness.stdout + harness.stderr


# --- contract violations are refused with a clear message, nothing written ------

def test_design_server_missing_a_spec_tool_is_refused(tmp_path: Path):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    server = project / "src" / "mcp" / "server.py"
    server.write_text(server.read_text().replace("def customer_get_profile(", "def customer_profile_get("))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "customer_get_profile" in result.stderr and "threadlight-design" in result.stderr
    assert not (project / "azure.yaml").exists()


def test_design_server_with_unpinned_top_level_import_is_refused(tmp_path: Path):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    server = project / "src" / "mcp" / "server.py"
    server.write_text(server.read_text().replace("import copy\n", "import copy\nimport fastmcp\n", 1))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "fastmcp" in result.stderr and "requirements.lock" in result.stderr
    assert not (project / "azure.yaml").exists()


@pytest.mark.parametrize("wrapper", [
    "class _Boot:\n    import fastmcp\n",
    "for _ in range(1):\n    import fastmcp\n",
    "while False:\n    import fastmcp\n",
    "match 1:\n    case 1:\n        import fastmcp\n",
    "try:\n    pass\nexcept* ValueError:\n    import fastmcp\n",
    "if True:\n    class _Inner:\n        from fastmcp import FastMCP\n",
])
def test_unpinned_import_in_any_import_time_block_is_refused(tmp_path: Path, wrapper: str):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    server = project / "src" / "mcp" / "server.py"
    server.write_text(server.read_text() + "\n\n" + wrapper)
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "fastmcp" in result.stderr
    assert not (project / "azure.yaml").exists()


@pytest.mark.parametrize("extra", [
    "from pydantic import Field\nimport anyio\nimport httpx\nfrom starlette.requests import Request\nimport jwt\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import requests\n",
    "import typing\nif typing.TYPE_CHECKING:\n    from fastmcp import FastMCP\n",
    "try:\n    import orjson as _json\nexcept ImportError:\n    import json as _json\n",
    "try:\n    from fastmcp import FastMCP\nexcept (ModuleNotFoundError, ValueError):\n    from mcp.server.fastmcp import FastMCP\n",
])
def test_pinned_lock_packages_and_guarded_imports_are_adopted(tmp_path: Path, extra: str):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    server = project / "src" / "mcp" / "server.py"
    server.write_text(server.read_text().replace("import copy\n", "import copy\n" + extra, 1))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("extra", [
    "import requests\n",
    # The fallback branch of an optional import is still checked.
    "try:\n    import orjson\nexcept ImportError:\n    import requests\n",
    # A try that does not guard ImportError is not an optional import.
    "try:\n    import requests\nexcept ValueError:\n    pass\n",
])
def test_unpinned_imports_outside_guards_are_still_refused(tmp_path: Path, extra: str):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    server = project / "src" / "mcp" / "server.py"
    server.write_text(server.read_text().replace("import copy\n", "import copy\n" + extra, 1))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "requests" in result.stderr
    # The message names what the image really installs.
    assert "pydantic" in result.stderr and "mcp" in result.stderr and "starlette" in result.stderr


def test_lazy_imports_in_functions_and_lambdas_stay_allowed(tmp_path: Path):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    server = project / "src" / "mcp" / "server.py"
    server.write_text(server.read_text() + "\n\nclass _Lazy:\n    def boot(self):\n        import fastmcp\n"
                      "\n_lazy = lambda: __import__('fastmcp')\n")
    assert _run([str(GENERATOR), "--project", str(project)], tmp_path).returncode == 0


# --- claim (c): selected knowledge modules are flagged, not silently dropped ------

def test_selected_but_unprovisioned_modules_are_flagged(adopted: Path):
    deferred = (adopted / "specs" / "deferred-steps.md").read_text()
    deploy_md = (adopted / "DEPLOY.md").read_text()
    for module in ("ai-search", "storage-blob", "foundry-iq-index"):
        assert module in deferred, module
        assert module in deploy_md, module
    assert "threadlight-ground" in deferred
    assert "return_policy_search" in deferred and "stand-in" in deferred
    gaps = " ".join(json.loads((adopted / "tests" / "postdeploy-manifest.json").read_text())["gaps"])
    assert "foundry-iq-index" in gaps
    bicep = (adopted / "infra" / "main.bicep").read_text()
    assert "Microsoft.Search/searchServices" not in bicep


def test_no_unprovisioned_flag_when_spec_selects_none(tmp_path: Path):
    project = tmp_path / "order-returns"
    shutil.copytree(ORDER_RETURNS, project)
    assert _run([str(GENERATOR), "--project", str(project)], tmp_path).returncode == 0
    assert "not provisioned" not in (project / "specs" / "deferred-steps.md").read_text()


# --- claim (d): the design's model, not the template default ----------------------

def test_model_follows_foundation(adopted: Path):
    params = json.loads((adopted / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["modelName"]["value"] == "${AZURE_AI_MODEL_NAME=gpt-5.4}"
    assert params["modelVersion"]["value"] == "${AZURE_AI_MODEL_VERSION=2026-03-05}"


def test_model_falls_back_to_spec_7b(tmp_path: Path):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    (project / "specs" / "foundation.md").unlink()
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text() + "\n## 7b. AI Services & Model Selection\n\n"
                    "- **Model + version**: `gpt-5.4-nano` (2026-03-17) — fixture\n")
    assert _run([str(GENERATOR), "--project", str(project)], tmp_path).returncode == 0
    params = json.loads((project / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["modelName"]["value"] == "${AZURE_AI_MODEL_NAME=gpt-5.4-nano}"
    assert params["modelVersion"]["value"] == "${AZURE_AI_MODEL_VERSION=2026-03-17}"


# --- contradiction 1: hosted agents no longer create capability hosts -------------

def test_pilot_bicep_creates_no_capability_hosts():
    text = (PILOT / "infra" / "main.bicep").read_text()
    assert "capabilityHosts" not in text
    assert re.search(r"migrate-hosted-agent-preview", text)
