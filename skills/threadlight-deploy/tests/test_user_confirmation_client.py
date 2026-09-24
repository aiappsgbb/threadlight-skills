"""Native MAF client contract tests; service replies here are explicit local fixtures."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json

import httpx
from mcp.types import CallToolResult, ListToolsResult, Tool
import pytest

from test_maf_gateway_client import client_module
from test_gateway import Credential

pytestmark = pytest.mark.governance_runtime
CONTEXT = "a" * 32
CONFIRMATION = "b" * 32


def descriptor():
    return {
        "name": "act", "description": "Record the selected decision",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "amount": {"type": "integer"},
                "governance_request_context": {
                    "type": "string", "pattern": "^[a-f0-9]{32}$",
                },
            },
            "required": ["amount"],
        },
        "_meta": {"threadlight.confirmation": "governance_request_context"},
    }


def client():
    module = client_module()

    async def authorize():
        pass

    return module, module.GovernedMCPTools(
        url="https://gateway.example/mcp", scope="api://gateway/.default",
        credential=Credential(), authorize=authorize, selected_tools=["act"])


def test_confirmation_without_reviewer_exposes_existing_operation_selector():
    _, remote = client()
    functions = remote.deferred_functions({"act": descriptor()})
    assert "governance_operation_id" in functions[0].parameters()["properties"]


def test_confirmation_context_moves_to_metadata_and_survives_exact_resume():
    async def run():
        module, remote = client()
        definition = descriptor()
        observed = []

        class Session:
            async def list_tools(self):
                return ListToolsResult(tools=[Tool.model_validate(definition)])

            async def call_tool(self, name, *, arguments, meta=None):
                observed.append((name, deepcopy(arguments), deepcopy(meta)))
                return CallToolResult(content=[], structuredContent={
                    "status": "pending_confirmation", "confirmation_id": CONFIRMATION,
                    "operation_id": "original-operation",
                }, isError=False)

        @asynccontextmanager
        async def session(*, call=None):
            if call is not None:
                assert call["key"] == "original-operation"
                assert call["request_context"] == CONTEXT
            yield Session()

        remote._session = session
        await remote.connect()
        arguments = {"amount": 5, "governance_request_context": CONTEXT,
                     "governance_operation_id": "original-operation"}
        result = await remote.functions[0].invoke(arguments=arguments, skip_parsing=True)
        assert observed == [("act", {"amount": 5}, {"governance_request_context": CONTEXT})]
        assert result["status"] == "pending_confirmation"
        assert result["resume_arguments"] == arguments
        assert result["confirmation_id"] == CONFIRMATION
        assert "approved" not in result
    asyncio.run(run())


def test_request_context_without_registered_operation_never_opens_transport():
    async def run():
        module, remote = client()
        remote._descriptors = {"act": descriptor()}
        function = remote._function("act", descriptor())

        @asynccontextmanager
        async def no_transport(**kwargs):
            pytest.fail("Context without its registered operation cannot reach the wire")
            yield

        remote._session = no_transport
        with pytest.raises(module.GatewayToolError, match="confirmation_operation_required"):
            await function.invoke(
                arguments={"amount": 5, "governance_request_context": CONTEXT},
                skip_parsing=True)
    asyncio.run(run())


@pytest.mark.parametrize("mutation", [
    lambda body: body.update(operation_id="another-operation"),
    lambda body: body.update(confirmation_id="not-a-nonce"),
    lambda body: body.update(approved=True),
])
def test_pending_confirmation_cannot_change_operation_or_claim_consent(mutation):
    module, remote = client()
    body = {"status": "pending_confirmation", "confirmation_id": CONFIRMATION,
            "operation_id": "original-operation"}
    mutation(body)
    call = {"name": "act", "key": "original-operation", "request_context": CONTEXT}
    with pytest.raises(module.GatewayToolError, match="invalid_pending_confirmation"):
        remote._pending_confirmation_result(body, call, {"amount": 5})


def test_native_maf_confirms_and_resumes_through_actual_mcp_and_control_plane(tmp_path):
    from test_confirmation_gateway import harness, decide
    from test_gateway import gateway

    async def run():
        h = await harness(tmp_path)
        app = gateway("server").create_app(h.dispatcher)

        async def authorize():
            h.policy.fresh()

        try:
            async with app.router.lifespan_context(app):
                remote = client_module().GovernedMCPTools(
                    url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=Credential(h.cp.token()), authorize=authorize,
                    selected_tools=["refund"],
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                await remote.connect()
                first = await remote.functions[0].invoke(arguments={
                    "amount": 5, "governance_request_context": h.context_ref,
                    "governance_operation_id": "one",
                }, skip_parsing=True)
                assert first["status"] == "pending_confirmation"
                assert not h.calls and len(h.cp.notifications) == 1
                await decide(h, first)
                result = await remote.functions[0].invoke(
                    arguments=first["resume_arguments"], skip_parsing=True)
                assert result == {"status": "refunded"}
                assert len(h.calls) == 1
                assert json.loads(h.calls[0].content) == {"amount": 5}
                replay = await remote.functions[0].invoke(
                    arguments=first["resume_arguments"], skip_parsing=True)
                assert replay == result and len(h.calls) == 1
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())
