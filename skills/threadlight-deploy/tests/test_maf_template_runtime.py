"""The MAF hosted-agent template is evaluable and optimizer-ready (#208).

Foundry agent-target evaluations read the tool trajectory from the Responses
output items. A host that returns only the final text gives judges
``sample.tool_calls = []`` and they score grounded answers as fabricated.
These tests drive the template's own ``container.py`` through the real
``ResponsesHostServer`` and the real pinned OpenAI client; only the model's HTTP
transport is scripted. They also load the template baseline with the real
``azure-ai-agentserver-optimization`` ``load_config()``.

Runs with the template's own pinned dependencies (CI step "MAF hosted-agent
template runtime"); the stdlib unit job skips it unless
THREADLIGHT_MAF_TEMPLATE_TESTS=1, which turns a missing package into a failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

PYTHON = Path(__file__).resolve().parents[1] / "references" / "hosted-agent" / "maf" / "references" / "python"
BASELINE = PYTHON / ".agent_configs" / "baseline"
REQUIRED = os.environ.get("THREADLIGHT_MAF_TEMPLATE_TESTS") == "1"


def _require(name):
    if REQUIRED:
        return __import__(name)
    return pytest.importorskip(name)


@pytest.fixture(scope="module")
def container(tmp_path_factory):
    for name in ("agent_framework", "agent_framework_foundry_hosting", "azure.ai.agentserver.optimization"):
        _require(name)
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    spec = importlib.util.spec_from_file_location("maf_template_container", PYTHON / "container.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scripted_client(script):
    """Real OpenAIChatClient; the Responses wire is replayed from ``script``."""
    import httpx
    from agent_framework.openai import OpenAIChatClient
    from openai import AsyncOpenAI

    requests = []

    def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        output = script.pop(0)
        wire = {"id": "resp_local", "object": "response", "created_at": 0, "status": "completed",
                "model": "local", "output": output}
        events = [{"type": "response.created", "response": {**wire, "output": [], "status": "in_progress"}}]
        for index, item in enumerate(output):
            events.append({"type": "response.output_item.added", "output_index": index,
                           "item": {**item, "arguments": ""} if item["type"] == "function_call"
                           else {**item, "content": []}})
            if item["type"] == "function_call":
                events += [
                    {"type": "response.function_call_arguments.delta", "output_index": index,
                     "item_id": item["id"], "delta": item["arguments"]},
                    {"type": "response.function_call_arguments.done", "output_index": index,
                     "item_id": item["id"], "arguments": item["arguments"]}]
            else:
                text = item["content"][0]["text"]
                events += [
                    {"type": "response.content_part.added", "output_index": index, "content_index": 0,
                     "item_id": item["id"], "part": {"type": "output_text", "text": "", "annotations": []}},
                    {"type": "response.output_text.delta", "output_index": index, "content_index": 0,
                     "item_id": item["id"], "delta": text},
                    {"type": "response.output_text.done", "output_index": index, "content_index": 0,
                     "item_id": item["id"], "text": text},
                    {"type": "response.content_part.done", "output_index": index, "content_index": 0,
                     "item_id": item["id"], "part": item["content"][0]}]
            events.append({"type": "response.output_item.done", "output_index": index, "item": item})
        events.append({"type": "response.completed", "response": wire})
        content = "".join(f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': n})}\n\n"
                          for n, e in enumerate(events))
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    sdk = AsyncOpenAI(api_key="local-test-only", max_retries=0,
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    client = OpenAIChatClient(model="local", async_client=sdk)
    client.requests = requests
    return client


def _tool_then_answer(name, arguments):
    return [
        [{"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": name,
          "arguments": json.dumps(arguments), "status": "completed"}],
        [{"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
          "content": [{"type": "output_text", "text": "Answer grounded in the tool result.",
                       "annotations": []}]}],
    ]


def _first_tool(container):
    config = container.load_baseline()
    return config, config.tool_definitions[0]["function"]["name"]


@pytest.mark.parametrize("stream", [False, True])
def test_handler_emits_function_call_then_output_then_message(container, stream):
    from agent_framework_foundry_hosting import ResponsesHostServer
    from starlette.testclient import TestClient

    _, name = _first_tool(container)
    client = _scripted_client(_tool_then_answer(name, {"query": "RMA-2026-004410"}))
    server = ResponsesHostServer(container.build_agent(client))
    with TestClient(server) as http:
        response = http.post("/responses", json={"input": "Look up RMA-2026-004410", "stream": stream})
        assert response.status_code == 200, response.text
        if stream:
            done = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
            output = [e["item"] for e in done if e.get("type") == "response.output_item.done"]
        else:
            output = response.json()["output"]
    assert [item["type"] for item in output] == ["function_call", "function_call_output", "message"]
    call, result, _ = output
    assert call["name"] == name and call["call_id"] == result["call_id"]
    assert json.loads(call["arguments"]) == {"query": "RMA-2026-004410"}
    assert result["output"]
    # The model saw the tool result before answering: the trajectory is real, not decorated.
    second = client.requests[1]["input"]
    assert any(item.get("type") == "function_call_output" for item in second)
    tools = client.requests[0]["tools"]
    assert {tool["name"] for tool in tools} >= {name}


def test_load_config_on_template_baseline_returns_tools(container):
    from azure.ai.agentserver.optimization import load_config

    metadata = (BASELINE / "metadata.yaml").read_text()
    assert "tool_file:" in metadata and "tools_file:" not in metadata
    raw = json.loads((BASELINE / "tools.json").read_text())
    assert isinstance(raw, list) and raw, "tools.json must be a top-level list"
    config = load_config(config_dir=PYTHON / ".agent_configs")
    assert config is not None and config.source.endswith("baseline") and config.candidate_id is None
    assert len(config.tool_definitions) > 0
    assert config.instructions.strip() and config.model
    registered = {tool.name for tool in container.TOOLS}
    assert {d["function"]["name"] for d in config.tool_definitions} == registered


def test_tool_definitions_convert_to_flat_responses_shape(container):
    config = container.load_baseline()
    flat = container.responses_tool_definitions(config)
    assert len(flat) == len(config.tool_definitions) > 0
    for item in flat:
        assert item["type"] == "function" and "function" not in item
        assert item["name"] and isinstance(item["parameters"], dict)


def test_baseline_descriptions_reach_the_registered_tools(container):
    config = container.load_baseline()
    container.build_agent(_scripted_client([]))
    by_name = {tool.name: tool for tool in container.TOOLS}
    for definition in config.tool_definitions:
        function = definition["function"]
        assert by_name[function["name"]].description == function["description"]


def test_unknown_tool_in_baseline_fails_closed(container, tmp_path):
    import shutil

    shutil.copytree(PYTHON / ".agent_configs", tmp_path / ".agent_configs")
    tools = tmp_path / ".agent_configs" / "baseline" / "tools.json"
    data = json.loads(tools.read_text())
    data.append({"type": "function", "function": {"name": "delete_order", "description": "x",
                                                  "parameters": {"type": "object", "properties": {}}}})
    tools.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="delete_order"):
        container.load_baseline(config_dir=tmp_path / ".agent_configs")


def test_container_image_ships_the_baseline():
    dockerfile = (PYTHON.parent / "docker" / "Dockerfile").read_text()
    assert "COPY .agent_configs/ ./.agent_configs/" in dockerfile
    pyproject = (PYTHON / "pyproject.toml").read_text()
    for pin in ("azure-ai-agentserver-optimization==", "openai", "httpx"):
        assert f'"{pin}' in pyproject
