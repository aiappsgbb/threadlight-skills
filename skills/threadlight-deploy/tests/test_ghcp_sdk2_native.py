"""GHCP template on the real installed Copilot SDK and runtime; only the model HTTP is synthetic.

Runs in the governance pin-test lanes (THREADLIGHT_GOVERNANCE_RUNTIME=1) against
the published wheel of each SDK line: microsoft-copilot-sdk 2.x and the
github-copilot-sdk 1.0.x ``sdk_line: v1`` escape hatch. It loads the unmodified
``container.py``, points BYOK at a local Responses endpoint and checks one custom
tool call and two-turn memory in a keyed (Foundry session) conversation. This is
local native evidence, not a hosted-agent proof.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import sys
import types
from pathlib import Path

import pytest

pytestmark = [pytest.mark.governance_runtime,
              pytest.mark.skipif(os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1",
                                 reason="requires the exact published SDK and native runtime")]

CONTAINER = (Path(__file__).resolve().parents[1] / "references" / "hosted-agent" / "ghcp" / "references"
             / "container.py")


def _sse(output):
    wire = {"id": "resp_local", "object": "response", "created_at": 0, "status": "completed",
            "model": "local", "output": output,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
    events = [{"type": "response.created", "response": {**wire, "output": [], "status": "in_progress"}}]
    for index, item in enumerate(output):
        events.append({"type": "response.output_item.added", "output_index": index,
                       "item": {**item, "arguments": ""} if item["type"] == "function_call"
                       else {**item, "content": []}})
        if item["type"] == "function_call":
            events += [{"type": "response.function_call_arguments.delta", "output_index": index,
                        "item_id": item["id"], "delta": item["arguments"]},
                       {"type": "response.function_call_arguments.done", "output_index": index,
                        "item_id": item["id"], "arguments": item["arguments"]}]
        else:
            text = item["content"][0]["text"]
            events += [{"type": "response.content_part.added", "output_index": index, "content_index": 0,
                        "item_id": item["id"], "part": {"type": "output_text", "text": "", "annotations": []}},
                       {"type": "response.output_text.delta", "output_index": index, "content_index": 0,
                        "item_id": item["id"], "delta": text},
                       {"type": "response.output_text.done", "output_index": index, "content_index": 0,
                        "item_id": item["id"], "text": text},
                       {"type": "response.content_part.done", "output_index": index, "content_index": 0,
                        "item_id": item["id"], "part": item["content"][0]}]
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append({"type": "response.completed", "response": wire})
    return "".join(f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': i})}\n\n"
                   for i, e in enumerate(events))


def _message(text):
    return {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _model_app(calls):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def responses(request):
        body = await request.json()
        calls.append({"path": request.url.path, "auth": request.headers.get("authorization"), "body": body})
        items = body.get("input") or []
        text = json.dumps(items)
        tools = [t.get("name") for t in body.get("tools") or []]
        outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
        if outputs:
            output = [_message("order status: " + str(outputs[-1].get("output")))]
        elif "get_order" in tools and "Look up order" in text:
            output = [{"id": "fc_1", "type": "function_call", "call_id": "call-1", "name": "get_order",
                       "arguments": json.dumps({"order_id": "RMA-1"}), "status": "completed"}]
        elif "What is my name" in text:
            output = [_message("Your name is Ada." if "My name is Ada" in text else "I do not know.")]
        else:
            output = [_message("noted")]
        if body.get("stream"):
            return Response(_sse(output), media_type="text/event-stream")
        return JSONResponse({"id": "resp_local", "object": "response", "status": "completed", "output": output})

    return Starlette(routes=[Route("/{path:path}", responses, methods=["POST"])])


class _Credential:
    def get_token(self, scope):
        assert scope == "https://ai.azure.com/.default"
        return types.SimpleNamespace(token="local-entra-token", expires_on=4102444800)


def _answer(sse: str) -> str:
    texts = []
    for line in sse.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if data.get("type") == "assistant.message":
                texts.append(data["data"]["content"])
    return texts[-1] if texts else ""


def test_template_real_sdk_tool_call_and_multi_turn_memory(tmp_path, monkeypatch):
    import uvicorn
    import azure.identity
    from copilot.tools import Tool, ToolResult

    async def case():
        calls = []
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(_model_app(calls), access_log=False, log_level="critical"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", f"http://127.0.0.1:{sock.getsockname()[1]}")
        monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "local-model")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("MODEL_GATEWAY_URL", raising=False)
        monkeypatch.setattr(azure.identity, "DefaultAzureCredential", _Credential)
        spec = importlib.util.spec_from_file_location("ghcp_native_under_test", CONTAINER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module._init_byok()
        invoked = []

        async def get_order(invocation):
            invoked.append(invocation.arguments)
            return ToolResult(text_result_for_llm="refund approved")

        tool = Tool(name="get_order", description="Look up an order", handler=get_order,
                    parameters={"type": "object", "properties": {"order_id": {"type": "string"}}})

        async def toolbox(call_id):
            return [tool]
        monkeypatch.setattr(module, "_toolbox_enabled", lambda: True)
        monkeypatch.setattr(module, "_toolbox_tools", toolbox)

        async def turn(text, session=""):
            sse = []
            async for chunk in module._stream_response("inv", text, session):
                sse.append(chunk if isinstance(chunk, str) else chunk.decode())
            return _answer("".join(sse))

        try:
            assert "refund approved" in await turn("Look up order RMA-1")
            assert invoked == [{"order_id": "RMA-1"}]
            await turn("My name is Ada.", "foundry-session-1")
            assert await turn("What is my name?", "foundry-session-1") == "Your name is Ada."
            assert await turn("What is my name?", "foundry-session-2") == "I do not know."
        finally:
            if module._client is not None:
                await module._client.stop()
            server.should_exit = True
            await task
        assert calls and all(c["auth"] == "Bearer local-entra-token" for c in calls)
        assert all(c["body"]["model"] == "local-model" for c in calls)
        if module.SDK_V2:
            assert {c["path"] for c in calls} == {"/openai/v1/responses"}
        return calls

    asyncio.run(case())
