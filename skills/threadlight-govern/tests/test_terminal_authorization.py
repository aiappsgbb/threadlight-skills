"""Opt-in terminal host boundary on actual native invocations (no hook doubles)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_runtime_provider import runtime, provider, native_model_client, tool_responses, contract

pytestmark = pytest.mark.governance_runtime


@pytest.mark.parametrize("ending", ["return", "cancel"])
def test_terminal_lease_cannot_escape_actual_tool_lifetime(tmp_path, ending):
    from agent_framework import FunctionMiddleware, tool
    api = runtime()
    receipts, effects, tasks = [], [], []

    class Audit:
        def append(self, **record):
            receipts.append(record)
            return "receipt"

    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}}, audit=Audit())

    async def scenario():
        release, started = asyncio.Event(), asyncio.Event()

        @tool
        async def act():
            check = api.require_effect_authorization("act", {})

            async def late_write():
                await release.wait()
                try:
                    check()
                except api.GovernedToolUnavailable:
                    return
                effects.append("late-write")
            tasks.append(asyncio.create_task(late_write()))
            started.set()
            if ending == "cancel":
                await asyncio.Event().wait()
            return "done"

        class AfterReturn(FunctionMiddleware):
            async def process(self, context, call_next):
                await call_next()
                release.set()
                await tasks[0]

        agent = api.create_governed_agent(
            p, client=native_model_client(tool_responses()), tools=[act],
            middleware=[AfterReturn()] if ending == "return" else [])
        task = asyncio.create_task(agent.run("act"))
        if ending == "cancel":
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            await tasks[0]
        else:
            await task

    asyncio.run(scenario())
    assert effects == [], "copied context retained authority after actual application call closed"
    assert any(r["reason_code"] == "threadlight:effect_authorization_unavailable" for r in receipts)


@pytest.mark.parametrize("fault", ["valid", "arguments", "policy-version", "policy-deadline",
                                  "principal", "tenant", "invalid-arguments"])
def test_terminal_guard_uses_native_target_without_revalidating(tmp_path, fault):
    from agent_framework import tool
    from pydantic import BaseModel, field_validator
    api = runtime()
    effects, validations, receipts = [], [], []

    class Audit:
        def append(self, **record):
            receipts.append(record)
            return "receipt"

    p, authority, bundle = provider(
        tmp_path, decisions={"pre_tool_call": {"decision": "allow"}}, audit=Audit())

    class Input(BaseModel):
        amount: int

        @field_validator("amount")
        @classmethod
        def count(cls, value):
            validations.append(value)
            return value + 1

    @tool(schema=Input)
    async def act(amount):
        assert amount == 2
        check = api.require_effect_authorization("act", {"amount": amount})
        await asyncio.sleep(0)
        if fault == "arguments":
            api.require_effect_authorization("act", {"amount": True})
        if fault == "invalid-arguments":
            api.require_effect_authorization("act", {"amount": object()})
        if fault == "policy-version":
            # Own test bundle, not installed SDK or a foreign runtime bundle.
            path = bundle.root / "bundle-metadata.json"
            metadata = json.loads(path.read_text())
            metadata["version"] = "2"
            path.write_text(json.dumps(metadata))
        if fault == "policy-deadline":
            authority.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if fault == "principal":
            p.principal = "different-principal"
        if fault == "tenant":
            p.tenant = "different-tenant"
        check()
        check()
        effects.append(amount)

    agent = api.create_governed_agent(
        p, client=native_model_client(tool_responses(args={"amount": 1})), tools=[act])
    asyncio.run(agent.run("act"))
    assert validations == [1]
    assert effects == ([2] if fault == "valid" else [])
    if fault != "valid":
        assert any(r["decision"] == "deny" for r in receipts)


def test_terminal_guard_without_native_call_is_explicit(tmp_path):
    api = runtime()
    with pytest.raises(api.GovernedToolUnavailable, match="effect_authorization_unavailable"):
        api.require_effect_authorization("act", {})


def test_terminal_guard_requires_native_pretool_authorization(tmp_path):
    from agent_framework import tool
    api = runtime()
    effects = []
    p, _, _ = provider(tmp_path, document=contract(points=("post_tool_call",)),
                        decisions={"post_tool_call": {"decision": "allow"}})

    @tool
    async def act():
        api.require_effect_authorization("act", {})
        effects.append("not-preauthorized")

    agent = api.create_governed_agent(
        p, client=native_model_client(tool_responses()), tools=[act])
    asyncio.run(agent.run("act"))
    assert effects == []


def test_terminal_lease_closes_before_native_result_processing(tmp_path):
    from agent_framework import FunctionTool
    api = runtime()
    effects, checks = [], []
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})

    async def act():
        checks.append(api.require_effect_authorization("act", {}))
        return "done"

    def parse(value):
        try:
            checks[0]()
        except api.GovernedToolUnavailable:
            return value
        effects.append("result-parser-write")
        return value

    agent = api.create_governed_agent(
        p, client=native_model_client(tool_responses()),
        tools=[FunctionTool(name="act", func=act, result_parser=parse)])
    asyncio.run(agent.run("act"))
    assert checks and effects == []


def test_expired_call_lease_denial_keeps_its_original_native_action(tmp_path):
    from agent_framework import tool
    api = runtime()
    checks, receipts = [], []

    class Audit:
        def append(self, **record):
            receipts.append(record)
            return "receipt"

    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}}, audit=Audit())

    @tool
    async def act(amount: int):
        if amount == 1:
            checks.append(api.require_effect_authorization("act", {"amount": amount}))
        else:
            checks[0]()
        return amount

    first = tool_responses(args={"amount": 1})[0]
    second, final = tool_responses(args={"amount": 2})
    second.messages[0].contents[0].call_id = "second-call"
    agent = api.create_governed_agent(
        p, client=native_model_client([first, second, final]), tools=[act])
    asyncio.run(agent.run("act twice"))
    assert [r["decision"] for r in receipts] == ["allow", "allow", "deny"]
    assert receipts[2]["action_hash"] == receipts[0]["action_hash"]
    assert receipts[2]["action_hash"] != receipts[1]["action_hash"]
