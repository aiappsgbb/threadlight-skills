"""Regressions from the 2026-11-07 live workshop deploy (generate_only -> azd up).

Each test pins a failure the live run fixed by hand-patching generated output:

1. MCP image layout: the design-owned ``server.py`` resolved repo-relative paths
   (``Path(__file__).parents[2]``) and raised IndexError at /app/server.py.
2. GHCP runtime: an SDK ``to_dict()`` AssertionError killed the SSE stream, and
   evaluations needed a ``"stream": false`` final-answer mode.
3. Evals sent the full SSE stream (judge 429, result upload 413) and one poll
   died on a transient DNS error.
4. The deployer needs Foundry User at ACCOUNT scope to run evaluations.
5. The App Insights project connection was created by hand.
6. Selected governance was not disclosed by generate_only.
8. ``specs/knowledge`` without a SPEC knowledge tool left the agent no read path.
9. Model capacity ignored ``specs/foundation.md``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import enum
import json
import logging
import re
import shutil
import types
from pathlib import Path

import pytest

from test_generate_only import GENERATOR, PILOT, _run

DEPLOY = Path(__file__).resolve().parents[1]
SKILLS = DEPLOY.parent
FIXTURES = DEPLOY / "tests" / "fixtures" / "generate-only"
CONTAINER = DEPLOY / "references" / "hosted-agent" / "ghcp" / "references" / "container.py"
RUN_EVALS = DEPLOY / "references" / "generate-only" / "templates" / "run_evals.py.tmpl"
LOCAL_KNOWLEDGE = SKILLS / "threadlight-local-test" / "references" / "quickstart" / "threadlight_quickstart" / "knowledge.py"
FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"

# The live design server resolved its data directory from a fixed parent depth at import.
PARENTS_SERVER_PREFIX = (
    "from pathlib import Path as _LivePath\n"
    "_DEFAULT_DATA = _LivePath(__file__).resolve().parents[2] / 'specs' / 'sample-data'\n"
)


def _generate(tmp_path: Path, fixture: str, mutate=None) -> Path:
    project = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, project)
    if mutate:
        mutate(project)
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return project


def _parents_server(project: Path) -> None:
    server = project / "src" / "mcp" / "server.py"
    text = server.read_text()
    head, sep, rest = text.partition("from __future__ import annotations\n")
    server.write_text(head + sep + PARENTS_SERVER_PREFIX + rest if sep else PARENTS_SERVER_PREFIX + text)


def _pytest(project: Path, tmp_path: Path):
    return _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "bt"),
                 str(project / "src" / "agent" / "tests")], tmp_path, cwd=project)


# --- 1. MCP image mirrors the repo layout ---------------------------------------

def test_pilot_mcp_dockerfile_mirrors_repo_layout():
    text = (PILOT / "mcp" / "Dockerfile").read_text()
    assert "WORKDIR /app/src/mcp" in text
    assert "COPY src/mcp/ /app/src/mcp/" in text
    assert "COPY specs/sample-data/ /app/specs/sample-data/" in text
    assert "SAMPLE_DATA_DIR=/app/specs/sample-data" in text
    assert "/app/sample-data" not in text


def test_generated_mcp_dockerfile_mirrors_repo_layout_with_knowledge(tmp_path: Path):
    project = _generate(tmp_path, "design-owned-mcp")
    text = (project / "src" / "mcp" / "Dockerfile").read_text()
    for src, dest in re.findall(r"^COPY\s+(\S+)\s+(\S+)", text, re.M):
        assert dest == "/app/" + src, (src, dest)
    assert "COPY specs/knowledge/ /app/specs/knowledge/" in text
    assert "KNOWLEDGE_DIR=/app/specs/knowledge" in text


def test_design_server_with_fixed_parent_depth_imports_under_container_layout(tmp_path: Path):
    project = _generate(tmp_path, "design-owned-mcp", _parents_server)
    result = _pytest(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "container_layout" in (project / "src" / "agent" / "tests" / "test_agent_package.py").read_text()


def test_generated_layout_test_catches_the_flat_live_layout(tmp_path: Path):
    project = _generate(tmp_path, "design-owned-mcp", _parents_server)
    docker = project / "src" / "mcp" / "Dockerfile"
    flat = (docker.read_text()
            .replace("WORKDIR /app/src/mcp", "WORKDIR /app")
            .replace("COPY src/mcp/ /app/src/mcp/", "COPY src/mcp/ /app/")
            .replace("COPY specs/sample-data/ /app/specs/sample-data/", "COPY specs/sample-data/ /app/sample-data/")
            .replace("SAMPLE_DATA_DIR=/app/specs/sample-data", "SAMPLE_DATA_DIR=/app/sample-data"))
    assert flat != docker.read_text()
    docker.write_text(flat)
    result = _pytest(project, tmp_path)
    assert result.returncode != 0
    assert "container_layout" in result.stdout + result.stderr


# --- 2. GHCP runtime: resilient events and a non-streaming final answer -------------

def _container_functions(*names: str, **namespace):
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assigns = [n for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id.startswith("_HEARTBEAT") for t in n.targets)]
    assert {n.name for n in nodes} == set(names)
    ns = {"logger": logging.getLogger("container-test"), "json": json, "asyncio": asyncio, **namespace}
    exec(compile(ast.Module(body=[*assigns, *nodes], type_ignores=[]), str(CONTAINER), "exec"), ns)
    return ns


class _Type(enum.Enum):
    USAGE = "assistant.usage"
    MESSAGE = "assistant.message"
    IDLE = "session.idle"
    ERROR = "session.error"


class _BadEvent:
    def __init__(self, kind, content=None):
        self.type = kind
        self.data = types.SimpleNamespace(content=content)

    def to_dict(self):
        assert False  # github-copilot-sdk 1.0.x timeToFirstTokenMs serialisation


def test_unserialisable_sdk_event_degrades_instead_of_killing_the_stream():
    ns = _container_functions("_event_payload")
    payload = ns["_event_payload"]
    assert payload(_BadEvent(_Type.USAGE)) is None
    assert payload(_BadEvent(_Type.MESSAGE, "Decision: approve_refund")) == {
        "type": "assistant.message", "data": {"content": "Decision: approve_refund"}}
    source = CONTAINER.read_text()
    assert "item.to_dict()" not in source


def test_stream_false_returns_only_the_final_answer_text():
    class Session:
        def __init__(self):
            self.handler = None

        def on(self, handler):
            self.handler = handler
            return lambda: None

        async def send(self, text):
            for event in (_BadEvent(_Type.USAGE), _BadEvent(_Type.MESSAGE, "thinking"),
                          _BadEvent(_Type.MESSAGE, "Decision: escalate_to_supervisor (BR-002)"),
                          types.SimpleNamespace(type=_Type.IDLE, data=None)):
                self.handler(event)

    session = Session()

    @contextlib.asynccontextmanager
    async def conversation(_key):
        yield session

    ns = _container_functions(
        "_final_answer_response",
        SessionEventType=types.SimpleNamespace(SESSION_IDLE=_Type.IDLE, SESSION_ERROR=_Type.ERROR),
        _conversation=conversation,
    )

    async def collect():
        return b"".join([chunk async for chunk in ns["_final_answer_response"]("Triage RMA-002")])

    assert asyncio.run(collect()).strip() == b"Decision: escalate_to_supervisor (BR-002)"
    handler = CONTAINER.read_text()
    assert re.search(r'data\.get\("stream"\) is False', handler)


# --- 3. Evals: non-streaming input and a poll that survives transient errors ---------

def test_eval_config_requests_non_streaming_final_answer(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    cfg = json.loads((project / "evals" / "eval-config.json").read_text())
    assert cfg["input_messages"] == {"input": "{{item.query}}", "stream": False, "new_conversation": True}
    dry = _run([str(project / "evals" / "run_evals.py"), "--dry-run"], tmp_path, cwd=project)
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["data_source"]["input_messages"]["stream"] is False


def test_eval_poll_retries_transient_connection_errors():
    namespace: dict = {"__name__": "run_evals_under_test", "__file__": str(RUN_EVALS)}
    exec(compile(RUN_EVALS.read_text(), str(RUN_EVALS), "exec"), namespace)
    poll = namespace["poll_run"]
    states = iter([ConnectionError("dns"), types.SimpleNamespace(id="r", status="in_progress"),
                   OSError("reset"), types.SimpleNamespace(id="r", status="completed")])

    class Runs:
        def retrieve(self, run_id, eval_id):
            item = next(states)
            if isinstance(item, Exception):
                raise item
            return item

    client = types.SimpleNamespace(evals=types.SimpleNamespace(runs=Runs()))
    run = poll(client, types.SimpleNamespace(id="r", status="queued"), "e", sleep=lambda _s: None)
    assert run.status == "completed"

    always = types.SimpleNamespace(evals=types.SimpleNamespace(runs=types.SimpleNamespace(
        retrieve=lambda run_id, eval_id: (_ for _ in ()).throw(ConnectionError("down")))))
    with pytest.raises(ConnectionError):
        poll(always, types.SimpleNamespace(id="r", status="queued"), "e", sleep=lambda _s: None)


# --- 4/5. Pilot infra: account-scope Foundry User and the App Insights connection ----

def test_pilot_bicep_grants_deployer_foundry_user_at_account_scope():
    text = (PILOT / "infra" / "main.bicep").read_text()
    block = re.search(r"resource deployerFoundryUserAccount '[^']+' = if \(!empty\(principalId\)\) \{(.*?)\n\}",
                      text, re.S)
    assert block, "deployer Foundry User at account scope is missing"
    assert "scope: account" in block.group(1)
    assert "roleFoundryUser" in block.group(1)
    assert "evaluation-permissions" in text
    assert FOUNDRY_USER in text


def test_pilot_bicep_connects_app_insights_to_the_project():
    text = (PILOT / "infra" / "main.bicep").read_text()
    block = re.search(r"resource projectAppInsights 'Microsoft\.CognitiveServices/accounts/projects/connections@[^']+' = \{(.*?)\n\}",
                      text, re.S)
    assert block, "App Insights project connection is missing"
    body = block.group(1)
    assert "parent: project" in body and "category: 'AppInsights'" in body and "target: appi.id" in body
    assert "configure-hosted-agent-telemetry" in text


# --- 6. Selected governance is disclosed, never silently dropped --------------------

def _bind_governance(project: Path) -> None:
    spec = project / "specs" / "SPEC.md"
    text = spec.read_text()
    start = text.index("## 11a.")
    end = text.index("## 11c.")
    contract = (
        "## 11a. Runtime Governance Contract\n\n```yaml\nframework: github-copilot-sdk\n\n"
        "governance:\n  mode: selective\n  environment_modes:\n    development: evaluate_only\n"
        "    staging: evaluate_only\n    preproduction: enforce\n    production: enforce\n"
        "  lifecycle_bindings: []\n\ntools:\n"
        "  - id: returns_get_case\n    consequence: read\n    policy_binding: none\n"
        "    enforcement_path: none\n    intervention_points: []\n    safe_principles: [least-privilege]\n"
        "    requires: []\n"
        "  - id: returns_record_recommendation\n    consequence: write\n"
        "    policy_binding: returns-recommendation-write-v1\n    enforcement_path: governed-tool-gateway\n"
        "    intervention_points: [pre_tool_call]\n    safe_principles: [scope, audit]\n"
        "    requires: [authorization, audit, idempotency]\n```\n\n")
    spec.write_text(text[:start] + contract + text[end:])



def test_selected_governance_is_disclosed_as_not_packaged(tmp_path: Path):
    project = _generate(tmp_path, "design-owned-mcp", _bind_governance)
    deferred = (project / "specs" / "deferred-steps.md").read_text()
    assert "Selected governance" in deferred
    assert "returns_record_recommendation" in deferred and "governed-tool-gateway" in deferred
    assert "not packaged" in deferred and "safe-check" in deferred
    gaps = " ".join(json.loads((project / "tests" / "postdeploy-manifest.json").read_text())["gaps"])
    assert "governance selected but not packaged" in gaps
    assert "Selected governance" in (project / "DEPLOY.md").read_text()


SAFE_CHECK = SKILLS / "threadlight-safe-check" / "scripts" / "safe_check.py"


def _governance_gaps(project: Path, tmp_path: Path) -> list[str]:
    # As in the live run: the installed catalog's script, cwd = the pilot, no PYTHONPATH.
    result = _run([str(SAFE_CHECK), "--phase", "pre-deploy", "--quiet"], tmp_path, cwd=project)
    report = json.loads((project / "tests" / "safe-check-predeploy-manifest.json").read_text())
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return [g for g in report["gaps"] if g.startswith("governance")]


def test_safe_check_names_the_missing_package_for_selected_governance(tmp_path: Path):
    project = _generate(tmp_path, "design-owned-mcp", _bind_governance)
    assert _governance_gaps(project, tmp_path) == ["governance: selected-governance-not-packaged"]


def test_invalid_governance_contract_is_disclosed(tmp_path: Path):
    project = _generate(tmp_path, "design-owned-mcp")
    deferred = (project / "specs" / "deferred-steps.md").read_text()
    assert "Governance contract" in deferred and "invalid" in deferred


def test_no_governance_row_without_a_contract(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    assert "governance" not in (project / "specs" / "deferred-steps.md").read_text().lower()


# --- 8. specs/knowledge always has a read-only search tool --------------------------

def _knowledge_tool_name() -> str:
    match = re.search(r'^TOOL_NAME\s*=\s*"([a-z_]+)"', LOCAL_KNOWLEDGE.read_text(), re.M)
    assert match, "threadlight-local-test knowledge.py must define TOOL_NAME"
    return match.group(1)


def test_corpus_without_spec_knowledge_tool_gets_a_search_tool(tmp_path: Path):
    def corpus(project: Path) -> None:
        knowledge = project / "specs" / "knowledge"
        knowledge.mkdir()
        (knowledge / "policy.md").write_text("# Policy\n\n## RP-1 Window\nReturns within 30 days.\n")

    project = _generate(tmp_path, "order-returns", corpus)
    name = _knowledge_tool_name()
    plan = {t["name"]: t for t in json.loads((project / "specs" / "project-tools.json").read_text())["tools"]}
    assert plan[name]["kind"] == "read" and plan[name].get("knowledge")
    server = (project / "src" / "mcp" / "server.py").read_text()
    assert f"def {name}(" in server
    deferred = (project / "specs" / "deferred-steps.md").read_text()
    assert f"`{name}`" in deferred and "Foundry IQ" in deferred
    docker = (project / "src" / "mcp" / "Dockerfile").read_text()
    assert "COPY specs/knowledge/ /app/specs/knowledge/" in docker
    assert "!specs/knowledge/**" in (project / ".dockerignore").read_text()
    result = _pytest(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_implicit_knowledge_tool_without_corpus(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    names = {t["name"] for t in json.loads((project / "specs" / "project-tools.json").read_text())["tools"]}
    assert _knowledge_tool_name() not in names


# --- 9. Model capacity follows specs/foundation.md ----------------------------------

def _with_capacity(value: str | None):
    def mutate(project: Path) -> None:
        foundation = project / "specs" / "foundation.md"
        text = foundation.read_text()
        if value is not None:
            text = text.replace("  capacity_type: GlobalStandard\n",
                                f"  capacity_type: GlobalStandard\n  capacity_tpm: {value}\n")
        foundation.write_text(text)
    return mutate


@pytest.mark.parametrize("value,units,raised", [("500K", 500, False), ("1M", 1000, False),
                                                 ("400000", 400, False), ("50K", 300, True), (None, 300, False)])
def test_model_capacity_derives_from_foundation_tpm(tmp_path: Path, value, units, raised):
    project = _generate(tmp_path, "design-owned-mcp", _with_capacity(value))
    params = json.loads((project / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["modelCapacity"]["value"] == f"${{AZURE_AI_MODEL_CAPACITY={units}}}"
    deferred = (project / "specs" / "deferred-steps.md").read_text()
    assert ("Model capacity" in deferred) is raised
    if raised:
        assert "50K TPM" in deferred and "1,000 TPM" in deferred and "AZURE_AI_MODEL_CAPACITY" in deferred


# --- 2b. The governed GHCP adapter has the same SDK serialisation guard -------------

GOVERNED_GHCP = DEPLOY / "references" / "governance" / "ghcp-container.py"


def test_governed_ghcp_adapter_degrades_unserialisable_message_events():
    tree = ast.parse(GOVERNED_GHCP.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_message_payload"]
    assert nodes, "governed ghcp-container.py must define module-level _message_payload"
    ns = {"LOGGER": logging.getLogger("governed-ghcp-test")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(GOVERNED_GHCP), "exec"), ns)
    payload = ns["_message_payload"]
    assert payload(_BadEvent(_Type.MESSAGE, "Decision: approve_refund")) == {
        "type": "assistant.message", "data": {"content": "Decision: approve_refund"}}

    class Good:
        type = _Type.MESSAGE

        def to_dict(self):
            return {"type": "assistant.message", "data": {"content": "ok"}}

    assert payload(Good()) == {"type": "assistant.message", "data": {"content": "ok"}}
    source = GOVERNED_GHCP.read_text(encoding="utf-8")
    assert "canonical(event.to_dict())" not in source
    assert "canonical(_message_payload(event))" in source


# --- 10. Deploy evidence is recorded by a generated helper, not by hand -------------
# e4 live run: agent_id/version, app_insights_connection_id and trace_id stayed
# null in specs/foundry-package-manifest.json until the operator filled them.

PROJECT_ID = ("/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-x/providers/"
              "Microsoft.CognitiveServices/accounts/ais-x/projects/proj-x")
APPI_ID = ("/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-x/providers/"
           "Microsoft.Insights/components/appi-x")


def _evidence_module(project: Path):
    import importlib.util
    path = project / "scripts" / "record_deploy_evidence.py"
    assert path.is_file(), "generate_only must emit scripts/record_deploy_evidence.py"
    spec = importlib.util.spec_from_file_location("record_deploy_evidence_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_cli(calls, *, show=None, rows=None):
    env = {"AZURE_AI_PROJECT_ID": PROJECT_ID,
           "AZURE_AI_APPINSIGHTS_CONNECTION_ID": PROJECT_ID + "/connections/appi-x",
           "APPLICATIONINSIGHTS_RESOURCE_ID": APPI_ID}
    show = show if show is not None else {"id": "returns-triage:3", "name": "returns-triage", "version": "3",
                                          "agent_guid": "11111111-2222-3333-4444-555555555555"}
    rows = rows if rows is not None else [["f80c03bba5aff2938ae369538df47c13"]]

    def run(cmd):
        calls.append(cmd)
        if cmd[:3] == ["azd", "env", "get-values"]:
            return json.dumps(env)
        if cmd[:4] == ["azd", "ai", "agent", "show"]:
            return json.dumps(show)
        if cmd[:4] == ["az", "monitor", "app-insights", "query"]:
            return json.dumps({"tables": [{"rows": rows}]})
        raise AssertionError(cmd)
    return run


def test_deploy_evidence_helper_fills_the_manifest(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    module = _evidence_module(project)
    calls: list = []
    module.run = _fake_cli(calls)
    assert module.main([]) == 0
    manifest = json.loads((project / "specs" / "foundry-package-manifest.json").read_text())
    assert manifest["agent"]["agent_id"] == "11111111-2222-3333-4444-555555555555"
    assert manifest["agent"]["agent_version"] == "3"
    assert manifest["telemetry"]["app_insights_connection_id"] == PROJECT_ID + "/connections/appi-x"
    assert manifest["telemetry"]["trace_id"] == "f80c03bba5aff2938ae369538df47c13"
    query = next(c for c in calls if c[:2] == ["az", "monitor"])
    assert APPI_ID in query and "invoke_agent" in " ".join(query)
    assert any(c[:4] == ["azd", "ai", "agent", "show"] and manifest["agent"]["agent_name"] in c for c in calls)


def test_deploy_evidence_helper_prefers_an_explicit_trace_id(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    module = _evidence_module(project)
    calls: list = []
    module.run = _fake_cli(calls)
    assert module.main(["--trace-id", "abcdef0123456789abcdef0123456789"]) == 0
    manifest = json.loads((project / "specs" / "foundry-package-manifest.json").read_text())
    assert manifest["telemetry"]["trace_id"] == "abcdef0123456789abcdef0123456789"
    assert not any(c[:2] == ["az", "monitor"] for c in calls)


def test_deploy_evidence_helper_never_invents_missing_values(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    module = _evidence_module(project)
    module.run = _fake_cli([], rows=[])
    before = json.loads((project / "specs" / "foundry-package-manifest.json").read_text())
    assert module.main([]) == 3
    after = json.loads((project / "specs" / "foundry-package-manifest.json").read_text())
    assert after["telemetry"]["trace_id"] is None
    assert after["agent"]["agent_id"] == "11111111-2222-3333-4444-555555555555"
    assert before["evaluation"] == after["evaluation"]


def test_deploy_evidence_helper_is_documented_and_wired(tmp_path: Path):
    project = _generate(tmp_path, "order-returns")
    assert "python scripts/record_deploy_evidence.py" in (project / "DEPLOY.md").read_text()
    bicep = (PILOT / "infra" / "main.bicep").read_text()
    assert "output AZURE_AI_APPINSIGHTS_CONNECTION_ID string = projectAppInsights.id" in bicep
    assert "output APPLICATIONINSIGHTS_RESOURCE_ID string = appi.id" in bicep


# --- 11. Structured `expected` from the design dataset stays JSON -------------------
# e4 live run: evals/eval_dataset.jsonl carried expected as a Python dict repr
# ("{'decision': 'approve_refund', ...}") because design_dataset() used str().

def test_structured_expected_is_serialised_as_json(tmp_path: Path):
    expected = {"decision": "approve_refund", "anchors": ["RMA-001", "RP-2.1"], "must_cite": True}

    def dataset(project: Path) -> None:
        tests = project / "tests"
        tests.mkdir(exist_ok=True)
        rows = [{"id": "S-001", "input": "Triage RMA-001", "expected": expected, "business_rules": ["BR-001"]},
                {"id": "S-002", "query": "Triage RMA-002", "expected": "escalate_to_supervisor"}]
        (tests / "eval_dataset.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    project = _generate(tmp_path, "order-returns", dataset)
    rows = [json.loads(line) for line in (project / "evals" / "eval_dataset.jsonl").read_text().splitlines()]
    assert isinstance(rows[0]["expected"], str)
    assert json.loads(rows[0]["expected"]) == expected
    assert "'decision'" not in rows[0]["expected"] and "True" not in rows[0]["expected"]
    assert rows[1]["expected"] == "escalate_to_supervisor"
