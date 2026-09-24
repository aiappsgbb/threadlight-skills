"""Real MAF dispatch with a deterministic model, without credentials or inference."""
from __future__ import annotations

import asyncio
import json
import pytest
from agent_framework import (
    Agent, BaseChatClient, ChatResponse, ChatResponseUpdate, Content,
    FunctionInvocationLayer, Message, ResponseStream, SkillsProvider, tool,
)

from threadlight_quickstart import agent_wiring
from threadlight_quickstart.discover import discover


class Model(FunctionInvocationLayer, BaseChatClient):
    """Only the model output is synthetic; dispatch and approval are native."""

    def __init__(self, outputs):
        super().__init__()
        self.outputs = iter(outputs)
        self.requests = []
        self.tools = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.requests.append(list(messages))
        self.tools = options.get("tools", [])
        contents = next(self.outputs)
        if stream:
            async def updates():
                for content in contents:
                    yield ChatResponseUpdate(role="assistant", contents=[content])
            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        async def response():
            return ChatResponse(messages=[Message("assistant", contents=contents)])
        return response()


def call(name, **arguments):
    return [Content.from_function_call(
        call_id=f"call-{name}", name=name, arguments=json.dumps(arguments))]


def outputs(*calls):
    return [*calls, [Content.from_text('{"status":"finished"}')]]


@pytest.fixture
def layout(tmp_path):
    data = tmp_path / "specs/sample-data"
    data.mkdir(parents=True)
    (data / "tickets.json").write_text('[{"id":"T-1"}]')
    skill = tmp_path / "src/agent/skills/triage"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: triage\ndescription: Ticket triage\n---\n"
        "PRIVATE-BODY: Route priority incidents to the incident owner.\n")
    (skill / "references/rules.md").write_text("PRIVATE-RESOURCE: Priority expires after 17 minutes.")
    (skill / "scripts/action.py").write_text("raise AssertionError('must not execute')\n")
    return discover(tmp_path)


async def result_for(agent, *, stream):
    if stream:
        return await agent.run("Read the triage instructions and resource.", stream=True).get_final_response()
    return await agent.run("Read the triage instructions and resource.")


@pytest.mark.parametrize("stream", [False, True])
def test_trusted_skill_body_and_resource_reach_model_and_continue(layout, stream):
    async def run():
        model = Model(outputs(
            call("load_skill", skill_name="triage"),
            call("read_skill_resource", skill_name="triage", resource_name="references/rules.md"),
        ))
        agent, _ = agent_wiring.build_agent(layout, chat_client=model)
        result = await result_for(agent, stream=stream)
        assert not result.user_input_requests
        assert result.text == '{"status":"finished"}'
        assert len(model.requests) == 3
        results = [
            content.result for message in model.requests[-1] for content in message.contents
            if content.type == "function_result"
        ]
        assert any("PRIVATE-BODY:" in str(value) for value in results)
        assert any("PRIVATE-RESOURCE:" in str(value) for value in results)
        modes = {t.name: t.approval_mode for t in model.tools}
        assert modes["load_skill"] == modes["read_skill_resource"] == "never_require"
        assert modes["run_skill_script"] == "always_require"
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("operation", ["run_skill_script", "settle_payment"])
def test_headless_remaining_approval_is_explicit_and_has_no_effect(layout, monkeypatch, stream, operation):
    effects = []

    @tool(approval_mode="always_require")
    def settle_payment() -> str:
        """Settle a payment."""
        effects.append("paid")
        return "paid"

    monkeypatch.setattr(agent_wiring, "_apply_overrides", lambda layout, tools, stores: [*tools, settle_payment])

    async def run():
        args = ({"skill_name": "triage", "script_name": "scripts/action.py"}
                if operation == "run_skill_script" else {})
        model = Model(outputs(call(operation, **args)))
        agent, _ = agent_wiring.build_agent(layout, chat_client=model)
        with pytest.raises(RuntimeError, match="approval_required"):
            await result_for(agent, stream=stream)
        assert not effects
        assert len(model.requests) == 1
    asyncio.run(run())


@pytest.mark.parametrize("name", ["load_skill", "read_skill_resource"])
def test_other_local_same_name_tool_does_not_inherit_provider_exemption(layout, name):
    effects = []

    @tool(name=name, approval_mode="always_require")
    def untrusted() -> str:
        """An unrelated local tool, not a trusted skill read."""
        effects.append(True)
        return "untrusted"

    async def run():
        # Use the exact middleware installed by the application, not a test policy.
        configured, _ = agent_wiring.build_agent(layout, chat_client=Model(outputs()))
        model = Model(outputs(call(name)))
        agent = Agent(client=model, tools=[untrusted], middleware=configured.middleware)
        with pytest.raises(RuntimeError, match="approval_required"):
            await agent.run("Run the tool")
        assert not effects
        assert untrusted.approval_mode == "always_require"
    asyncio.run(run())


@pytest.mark.parametrize("name", ["load_skill", "read_skill_resource"])
def test_untrusted_skills_provider_keeps_native_approval(layout, name):
    async def run():
        configured, _ = agent_wiring.build_agent(layout, chat_client=Model(outputs()))
        args = {"skill_name": "triage"}
        if name == "read_skill_resource":
            args["resource_name"] = "references/rules.md"
        model = Model(outputs(call(name, **args)))
        agent = Agent(client=model, context_providers=[SkillsProvider.from_paths(layout.skills_dir)],
                      middleware=configured.middleware)
        with pytest.raises(RuntimeError, match="approval_required"):
            await agent.run("Read an untrusted skill")
        assert len(model.requests) == 1
    asyncio.run(run())


@pytest.mark.parametrize("name", ["load_skill", "read_skill_resource"])
def test_hosted_same_name_approval_is_not_auto_approved(layout, name):
    async def run():
        hosted = Content.from_function_call(
            call_id="hosted", name=name, arguments="{}",
            additional_properties={"server_label": "untrusted-mcp"},
        )
        request = Content.from_function_approval_request(id="approval", function_call=hosted)
        model = Model(outputs([request]))
        agent, _ = agent_wiring.build_agent(layout, chat_client=model)
        with pytest.raises(RuntimeError, match="approval_required"):
            await agent.run("Read a remote skill")
        assert len(model.requests) == 1
        assert not SkillsProvider.read_only_tools_auto_approval_rule(hosted)
    asyncio.run(run())


def test_streamlit_reports_approval_instead_of_empty_success(layout, monkeypatch):
    monkeypatch.delenv("THREADLIGHT_QUICKSTART_ROOT", raising=False)
    from threadlight_quickstart.ui_streamlit import _stream_response

    class Placeholder:
        text = ""

        def markdown(self, text):
            self.text = text

    async def run():
        model = Model(outputs(call("run_skill_script", skill_name="triage", script_name="scripts/action.py")))
        agent, _ = agent_wiring.build_agent(layout, chat_client=model)
        placeholder = Placeholder()
        response = await _stream_response(agent, "Run the script", placeholder)
        assert "approval_required" in response
        assert "_error:" in response
        assert placeholder.text == response
        assert len(model.requests) == 1
    asyncio.run(run())


def test_streaming_partial_text_does_not_mask_later_approval(layout):
    async def run():
        request = Content.from_function_approval_request(
            id="approval", function_call=call("settle_payment")[0],
        )
        model = Model([[Content.from_text("Partial report"), request]])
        agent, _ = agent_wiring.build_agent(layout, chat_client=model)
        chunks = []
        with pytest.raises(RuntimeError, match="approval_required"):
            async for update in agent.run("Prepare a report", stream=True):
                if update.text:
                    chunks.append(update.text)
        assert chunks == ["Partial report"]
        assert len(model.requests) == 1
    asyncio.run(run())


def test_ui_terminal_failure_replaces_provisional_success(layout):
    from threadlight_quickstart.ui_streamlit import _stream_response

    class FailingModel(Model):
        def _inner_get_response(self, *, messages, stream, options, **kwargs):
            async def updates():
                yield ChatResponseUpdate(role="assistant", contents=[Content.from_text("Success: saved draft")])
                raise RuntimeError("terminal-validation-failed")
            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

    class Placeholder:
        text = ""

        def markdown(self, value):
            self.text = value

    async def run():
        agent, _ = agent_wiring.build_agent(layout, chat_client=FailingModel([]))
        placeholder = Placeholder()
        response = await _stream_response(agent, "Prepare a draft", placeholder)
        assert "terminal-validation-failed" in response
        assert "Success: saved draft" not in response
        assert placeholder.text == response
    asyncio.run(run())


@pytest.mark.parametrize("entity,record", [("tickets", "T-1"), ("workorders", "W-1")])
@pytest.mark.parametrize("stream", [False, True])
def test_native_dispatch_reuses_method_not_process_data_or_persistence(tmp_path, entity, record, stream):
    """Two distinct local PoCs exercise the packaged adapter; neither is durable."""
    data = tmp_path / "specs/sample-data"
    data.mkdir(parents=True)
    (data / f"{entity}.json").write_text(json.dumps([{"id": record, "status": "new"}]))
    layout = discover(tmp_path)

    async def run():
        model = Model(outputs(
            call(f"get_{entity}", id=record),
            call(f"update_{entity}", id=record, fields={"status": "draft"}),
            call(f"get_{entity}", id=record),
        ))
        agent, stores = agent_wiring.build_agent(layout, chat_client=model)
        result = await result_for(agent, stream=stream)
        assert result.text == '{"status":"finished"}'
        assert len(model.requests) == 4
        update = next(t for t in model.tools if t.name == f"update_{entity}")
        assert "fields" in update.parameters()["properties"]
        assert stores[entity].get(record)["status"] == "draft"
        # A new adapter instance is independent readback, disproving durable save.
        _, reopened = agent_wiring.build_agent(layout, chat_client=Model(outputs()))
        assert reopened[entity].get(record)["status"] == "new"
        assert set(reopened) == {entity}
    asyncio.run(run())


def test_native_filter_schema_does_not_silently_drop_arguments(tmp_path):
    data = tmp_path / "specs/sample-data"
    data.mkdir(parents=True)
    (data / "tickets.json").write_text('[{"id":"T-1","status":"new"},{"id":"T-2","status":"closed"}]')
    layout = discover(tmp_path)

    async def run():
        model = Model(outputs(call("list_tickets", filters={"status": "closed"})))
        agent, _ = agent_wiring.build_agent(layout, chat_client=model)
        await agent.run("Find closed tickets")
        function_results = [
            content.result for message in model.requests[-1] for content in message.contents
            if content.type == "function_result"]
        assert len(function_results) == 1
        assert "T-2" in str(function_results[0])
        assert "T-1" not in str(function_results[0])
    asyncio.run(run())
