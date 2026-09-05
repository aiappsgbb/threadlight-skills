"""Real published MAF + ACS/OPA; only external transports are synthetic."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "skills/threadlight-govern/references/runtime"
sys.path.insert(0, str(ROOT))
pytestmark = pytest.mark.governance_runtime


def runtime():
    assert (RUNTIME / "governance_provider.py").exists(), "runtime provider missing"
    return importlib.import_module("skills.threadlight-govern.references.runtime")


def contract(*, points=("pre_tool_call",), lifecycle=(), requires=(), mode="selective"):
    document = {
        "framework": "microsoft-agent-framework",
        "governance": {
            "mode": mode,
            "environment_modes": {
                "development": "evaluate_only", "staging": "evaluate_only",
                "preproduction": "enforce", "production": "enforce",
            },
            "lifecycle_bindings": [
                {"lifecycle_point": point, "policy_binding": "safe",
                 "enforcement_path": "local-agent-hooks", "safe_principles": ["Safety"],
                 "requires": list(requires)} for point in lifecycle
            ],
        },
        "tools": [
            {"id": "act", "consequence": "write", "policy_binding": "safe",
             "enforcement_path": "local-agent-hooks", "intervention_points": list(points),
             "safe_principles": ["Safety"], "requires": list(requires)},
            {"id": "read", "consequence": "read", "policy_binding": "none",
             "enforcement_path": "none", "intervention_points": [],
             "safe_principles": [], "requires": []},
        ],
    }
    if not points:
        document["tools"][0].update(
            policy_binding="none", enforcement_path="none", safe_principles=[], requires=[],
        )
    return document


def build_policy(tmp_path, decisions=None):
    import yaml
    from test_policy_bundle import bundle_module
    decisions = decisions or {"pre_tool_call": {"decision": "deny"}}
    source = tmp_path / "source"
    source.mkdir(parents=True)
    targets = {
        "pre_tool_call": "$.tool_call.args", "post_tool_call": "$.tool_result",
        "input": "$.input", "output": "$.output", "pre_model_call": "$.messages",
        "post_model_call": "$.response", "agent_startup": "$.agent_init",
        "agent_shutdown": "$.summary",
    }
    manifest = {
        "agent_control_specification_version": "0.3.1-beta",
        "policies": {"safe": {"type": "rego", "data": ["safe.rego"],
                            "query": "data.test.check_pre_tool_call"}},
        "intervention_points": {
            point: {"policy_target": targets[point],
                    "policy": {"id": "safe", "query": f"data.test.check_{point}"}}
            for point in decisions
        },
    }
    (source / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    (source / "safe.rego").write_text("package test\nimport rego.v1\n" + "\n".join(
        f"check_{point} := " + (value if isinstance(value, str) else json.dumps(value))
        for point, value in decisions.items()
    ))
    return bundle_module().build_bundle(
        source=source, destination=tmp_path / "bundle", policy_id="safe", version="1"
    )


class TestAuthority:
    """External signature authority double, not a local signing key."""
    __test__ = False

    def __init__(self, digest):
        self.digest = digest
        self.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        self.valid = True

    def verify(self, bundle):
        if not self.valid:
            raise ValueError("PRIVATE-SIGNATURE-ERROR")
        return runtime().VerifiedPolicy(self.digest, self.expires_at)


def provider(tmp_path, *, decisions=None, document=None, safe=None, **kwargs):
    from test_policy_bundle import bundle_module
    from skills._shared.governance import validate_governance_contract
    built = build_policy(tmp_path, decisions)
    authority = TestAuthority(built.bundle_digest)
    result = runtime().AcsGovernanceProvider(
        contract=document or contract(), bundle_path=built.root,
        expected_digest=built.bundle_digest, bundle_verifier=bundle_module().verify_bundle,
        signature_verifier=authority, contract_validator=validate_governance_contract,
        safe_provider=lambda identity: deepcopy(safe or {}), **kwargs,
    )
    return result, authority, built


def model_client(responses, *, chunks=None, on_chunk=None):
    from agent_framework import (
        BaseChatClient, ChatMiddlewareLayer, ChatResponse, ChatResponseUpdate,
        Content, FunctionInvocationLayer, ResponseStream,
    )

    class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
        def __init__(self):
            super().__init__()
            self.requests = []
            self.responses = responses

        def _inner_get_response(self, *, messages, stream, options, **kwargs):
            recorded = dict(options)
            if "tools" in recorded:
                recorded["tools"] = list(recorded["tools"])
            self.requests.append(([m.to_dict() for m in messages], recorded))
            response = responses.pop(0)
            if not stream:
                async def get():
                    return response
                return get()

            async def updates():
                if chunks is None:
                    for message in response.messages:
                        yield ChatResponseUpdate(role=message.role, contents=message.contents)
                    return
                for text in chunks or [response.text]:
                    if on_chunk:
                        on_chunk(text)
                    yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(text)])
            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)
    return ScriptedClient()


def native_model_client(responses, *, request_hook=None, foundry=False, chat_completions=False, auth=None):
    """Real pinned model client; only the final HTTP transport is synthetic."""
    import httpx
    from openai import AsyncOpenAI
    from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
    requests = []

    def transport(request):
        body = json.loads(request.content)
        requests.append((body["messages" if chat_completions else "input"], body))
        response = responses.pop(0)
        output = []
        for message in response.messages:
            for content in message.contents:
                if content.type == "function_call":
                    output.append({
                        "id": f"fc_{content.call_id}", "type": "function_call",
                        "call_id": content.call_id, "name": content.name,
                        "arguments": content.arguments, "status": "completed",
                    })
                elif content.type == "text":
                    output.append({
                        "id": f"msg_{len(output)}", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{
                            "type": "output_text", "text": content.text, "annotations": [],
                        }],
                    })
        wire = {"id": "resp_local", "object": "response", "created_at": 0,
                "status": "completed", "model": "local", "output": output}
        if chat_completions:
            message = {"role": "assistant", "content": response.text}
            calls = [{"id": item["call_id"], "type": "function",
                      "function": {"name": item["name"], "arguments": item["arguments"]}}
                     for item in output if item["type"] == "function_call"]
            if calls:
                message["tool_calls"] = calls
            wire = {"id": "chatcmpl_local", "object": "chat.completion", "created": 0,
                    "model": "local", "choices": [{
                        "index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop",
                    }]}
        return httpx.Response(200, json=wire)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [request_hook]} if request_hook else None,
        auth=auth,
    )
    if foundry:
        from agent_framework.foundry import FoundryChatClient
        from azure.ai.projects.aio import AIProjectClient
        from azure.core.credentials import AccessToken
        class LocalCredential:
            async def get_token(self, *scopes, **kwargs):
                return AccessToken("local-test-only", 4102444800)
        project = AIProjectClient(
            endpoint="https://local.services.ai.azure.com/api/projects/local",
            credential=LocalCredential(),
        )
        client = FoundryChatClient(project_client=project, model="local")
        # Replace only this caller-owned SDK HTTP client via its public copy API.
        client.client = client.client.with_options(http_client=http, max_retries=0)
    else:
        sdk = AsyncOpenAI(api_key="local-test-only", http_client=http, max_retries=0)
        factory = OpenAIChatCompletionClient if chat_completions else OpenAIChatClient
        client = factory(model="local", async_client=sdk)
    client.requests = requests
    client.responses = responses
    return client


def tool_responses(name="act", args=None):
    from agent_framework import ChatResponse, Content, Message
    return [
        ChatResponse(messages=[Message("assistant", contents=[
            Content.from_function_call(call_id="call-1", name=name,
                                       arguments=json.dumps(args or {})),
        ])], finish_reason="tool_calls"),
        ChatResponse(messages=[Message("assistant", ["finished"])], finish_reason="stop"),
    ]


def run_tool(p, *, name="act", args=None, result=None, middleware=(), on_effect=None):
    from agent_framework import FunctionTool
    from agent_framework._tools import SKIP_PARSING
    effects = []

    async def implementation(**values):
        if on_effect:
            on_effect()
        effects.append(values)
        return result if result is not None else "effect"

    tools = [FunctionTool(
        name=n, description="Local synthetic effect", func=implementation,
        input_model={"type": "object", "properties": {}, "additionalProperties": True},
        result_parser=SKIP_PARSING,
    ) for n in ("act", "read")]
    factory = native_model_client if any(
        b["tool"] is None and b["point"] in {"agent_startup", "input", "pre_model_call"}
        for b in p._bindings.values()
    ) else model_client
    client = factory(tool_responses(name, args))
    agent = runtime().create_governed_agent(
        p, client=client, tools=tools, middleware=list(middleware),
    )
    return asyncio.run(agent.run("perform action")), effects, client, agent


def test_spool_persists_action_and_creation_timestamp_and_rejects_async_false_exporters(tmp_path):
    spool = runtime().DurableSpool(tmp_path)
    receipt_id = spool.append(correlation_id="correlation", decision="error", action_id="act",
        action_hash="sha256:" + "a" * 64, policy_hash="sha256:" + "b" * 64)
    path = tmp_path / (receipt_id + ".json")
    original = json.loads(path.read_text())
    assert original["action_id"] == "act"
    timestamp = datetime.fromisoformat(original["recorded_at"])
    assert timestamp.tzinfo is not None
    async def async_exporter(receipt):
        return True
    assert spool.retry(async_exporter) == 0, "coroutine creation is not remote delivery"
    assert spool.retry(lambda receipt: False) == 0
    assert json.loads(path.read_text()) == original


def test_bound_deny_zero_effects(tmp_path):
    p, _, _ = provider(tmp_path)
    _, effects, client, _ = run_tool(p)
    assert effects == []
    assert "threadlight:policy_deny" in str(client.requests[-1][0])
    assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is True


def test_unbound_tool_never_evaluates_acs(tmp_path, monkeypatch):
    p, _, _ = provider(tmp_path)
    from agent_control_specification import AgentControl
    async def forbidden(*args, **kwargs):
        pytest.fail("unbound tool evaluated ACS")
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", forbidden)
    assert run_tool(p, name="read")[1] == [{}]


def test_bound_allow_positive_control(tmp_path):
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    _, effects, client, _ = run_tool(p, args={"amount": 10})
    assert effects == [{"amount": 10}]
    assert all(options["store"] is False for _, options in client.requests)


def test_native_argument_transform(tmp_path):
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {
        "decision": "transform", "transform": {"path": "$policy_target", "value": {"amount": 5}},
    }})
    assert run_tool(p, args={"amount": 999})[1] == [{"amount": 5}]


def test_native_post_tool_transform_before_model_or_history(tmp_path):
    p, _, _ = provider(tmp_path, document=contract(points=("post_tool_call",)),
                       decisions={"post_tool_call": {
                           "decision": "transform",
                           "transform": {"path": "$policy_target", "value": "redacted"},
                       }})
    response, effects, client, _ = run_tool(p, result={"private": "PRIVATE-RESULT"})
    assert effects == [{}]
    assert "redacted" in str(client.requests[-1][0])
    assert "PRIVATE-RESULT" not in str(client.requests[-1][0])
    assert "PRIVATE-RESULT" not in response.to_json()
    tool_message = next(m for m in client.requests[-1][0] if m["role"] == "tool")
    assert tool_message["contents"][0]["result"] == "redacted"


@pytest.mark.parametrize("failure", ["exception", "timeout", "malformed"])
def test_engine_failure_zero_effects(tmp_path, monkeypatch, failure):
    p, _, _ = provider(tmp_path, timeout=0.05)
    from agent_control_specification import AgentControl
    async def fail(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("PRIVATE-ERROR")
        if failure == "timeout":
            await asyncio.sleep(0.2)
        return {"decision": "allow"}
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", fail)
    _, effects, client, agent = run_tool(p)
    assert effects == []
    assert "PRIVATE-ERROR" not in str(client.requests)
    assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is False
    client.responses.extend(tool_responses("read"))
    asyncio.run(agent.run("read"))
    assert effects == [{}]


@pytest.mark.parametrize("failure", ["expired", "signature", "digest", "missing", "point"])
def test_unhealthy_binding_preserves_unbound_surface(tmp_path, failure):
    decisions = {"output": {"decision": "allow"}} if failure == "point" else None
    p, authority, built = provider(tmp_path, decisions=decisions)
    if failure == "expired":
        authority.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif failure == "signature":
        authority.valid = False
    elif failure == "digest":
        (built.root / "safe.rego").write_text("package tampered")
    elif failure == "missing":
        (built.root / "manifest.yaml").unlink()
    _, effects, client, agent = run_tool(p)
    assert effects == []
    client.responses.extend(tool_responses("read"))
    asyncio.run(agent.run("read"))
    assert effects == [{}]
    assert p.policy_digest() == built.bundle_digest
    assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is False


def test_model_cannot_forge_safe_evidence_or_approval(tmp_path):
    rule = ('{"decision": "allow"} if {input.snapshot.safe.evidence.receipt_verified == true} '
            'else := {"decision": "deny"}')
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": rule})
    assert run_tool(p, args={
        "safe": {"evidence": {"receipt_verified": True},
                 "escalations": {"refund_approved": True}}, "approved": True,
    })[1] == []


def test_host_evidence_is_used(tmp_path):
    rule = ('{"decision": "allow"} if {input.snapshot.safe.evidence.receipt_verified == true} '
            'else := {"decision": "deny"}')
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": rule},
                       safe={"evidence": {"receipt_verified": True}})
    assert run_tool(p)[1] == [{}]


@pytest.mark.parametrize("point", ["input", "pre_model_call", "post_model_call", "output"])
def test_native_lifecycle_transforms(tmp_path, point):
    from agent_framework import ChatResponse, Message
    value = {
        "input": {"role": "user", "content": "safe-input"},
        "pre_model_call": [{"role": "user", "content": "safe-input"}],
        "post_model_call": {"content": "safe-output", "tool_calls": [], "finish_reason": "stop"},
        "output": {"content": "safe-output"},
    }[point]
    p, _, _ = provider(tmp_path, document=contract(lifecycle=(point,)), decisions={
        point: {"decision": "transform", "transform": {"path": "$policy_target", "value": value}},
    })
    factory = native_model_client if point in {"input", "pre_model_call"} else model_client
    client = factory([ChatResponse(messages=[Message("assistant", ["PRIVATE-OUTPUT"])])])
    agent = runtime().create_governed_agent(p, client=client)
    response = asyncio.run(agent.run("PRIVATE-INPUT"))
    if point in ("input", "pre_model_call"):
        assert "safe-input" in str(client.requests[0][0])
        assert "PRIVATE-INPUT" not in str(client.requests[0][0])
    else:
        assert response.text == "safe-output"


@pytest.mark.parametrize("decision", ["allow", "deny", "transform", "limit"])
def test_native_buffered_stream(tmp_path, decision):
    from agent_framework import ChatResponse, Message
    from agent_hooks import InterceptionBlocked
    policy = {"decision": "allow" if decision == "limit" else decision}
    if decision == "transform":
        policy["transform"] = {"path": "$policy_target", "value": {"content": "safe"}}
    p, _, _ = provider(
        tmp_path, document=contract(lifecycle=("output",)), decisions={"output": policy},
        max_output_bytes=700 if decision == "limit" else 10000,
    )
    seen, released = [], []
    chunks = ["PRIVATE-" + "x" * 150] * 30 if decision == "limit" else ["PRIVATE-", "OUTPUT"]
    client = model_client(
        [ChatResponse(messages=[Message("assistant", ["PRIVATE-OUTPUT"])])],
        chunks=chunks, on_chunk=lambda chunk: seen.append(chunk),
    )
    agent = runtime().create_governed_agent(p, client=client)

    async def consume():
        stream = agent.run("input", stream=True, options={"store": True})
        async for update in stream:
            assert seen == chunks
            released.append(update.text)
        return await stream.get_final_response()
    if decision in ("deny", "limit"):
        with pytest.raises((InterceptionBlocked, runtime().OutputLimitExceeded)):
            asyncio.run(consume())
        assert released == []
        if decision == "limit":
            assert len(seen) < len(chunks), "limit must stop collection, not check after buffering"
    else:
        final = asyncio.run(consume())
        expected = "safe" if decision == "transform" else "PRIVATE-OUTPUT"
        assert final.text == "".join(released) == expected
    assert all(options["store"] is False for _, options in client.requests)


def test_post_tool_deny_discards_tainted_result(tmp_path):
    p, _, _ = provider(tmp_path, document=contract(points=("post_tool_call",)),
                       decisions={"post_tool_call": {"decision": "deny"}})
    response, effects, client, _ = run_tool(p, result={"secret": "TAINTED-RESULT"})
    assert effects == [{}]
    assert "TAINTED-RESULT" not in str(client.requests[-1][0]) + response.to_json()
    assert "threadlight:policy_deny" in str(client.requests[-1][0])


@pytest.mark.parametrize("point", ["startup", "shutdown"])
def test_selected_session_points_are_evaluated(tmp_path, point):
    from agent_framework import ChatResponse, Message
    from agent_hooks import InterceptionBlocked
    native = {"startup": "agent_startup", "shutdown": "agent_shutdown"}[point]
    p, _, _ = provider(tmp_path, document=contract(lifecycle=(point,)),
                       decisions={native: {"decision": "deny"}})
    factory = native_model_client if point == "startup" else model_client
    client = factory([ChatResponse(messages=[Message("assistant", ["ok"])])])
    agent = runtime().create_governed_agent(p, client=client)
    if point == "startup":
        with pytest.raises(InterceptionBlocked):
            asyncio.run(agent.run("input"))
        assert client.requests == []
    else:
        assert asyncio.run(agent.run("input")).text == "ok"
        assert p.health()["bindings"]["lifecycle:agent_shutdown"]["status"] == "observational"


@pytest.mark.parametrize("case", ["duplicate", "before", "foreign", "empty"])
def test_bundle_order_and_count(tmp_path, case):
    from agent_framework import AgentMiddleware, create_agent_hooks_middleware
    from agent_hooks import ALLOW
    p, _, _ = provider(tmp_path)
    class App(AgentMiddleware):
        async def process(self, context, call_next):
            await call_next()
    class Allow:
        def intercept(self, context):
            return ALLOW
    entries = {
        "duplicate": [p.middleware(), p.middleware()],
        "before": [App(), p.middleware()],
        "foreign": [create_agent_hooks_middleware([Allow()])],
        "empty": [],
    }[case]
    if case == "empty":
        with pytest.raises(ValueError):
            runtime().hooks_bundle([])
    else:
        with pytest.raises(ValueError):
            runtime().create_governed_agent(p, client=model_client([]), middleware=entries)


def test_nested_agents_require_independent_bundles(tmp_path):
    from agent_framework import ChatResponse, FunctionTool, Message
    child, _, _ = provider(tmp_path / "child")
    child_response, effects, _, _ = run_tool(child)
    assert effects == []
    with pytest.raises(ValueError):
        runtime().create_governed_agent(child, client=model_client([]))
    parent, _, _ = provider(tmp_path / "parent",
                            decisions={"pre_tool_call": {"decision": "allow"}})
    inner, _, _ = provider(tmp_path / "inner")
    inner_client = model_client(tool_responses())
    inner_effects = []
    async def effect():
        inner_effects.append(True)
        return "effect"
    inner_agent = runtime().create_governed_agent(
        inner, client=inner_client, tools=[FunctionTool(name="act", func=effect)],
    )
    async def nested(**kwargs):
        return (await inner_agent.run("nested")).text
    outer = runtime().create_governed_agent(
        parent, client=model_client(tool_responses()),
        tools=[FunctionTool(name="act", func=nested)],
    )
    asyncio.run(outer.run("outer"))
    assert inner_effects == []


def test_durable_payload_free_receipt_precedes_effect_and_retries(tmp_path):
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=contract(requires=("durable-audit",)),
                       decisions={"pre_tool_call": {"decision": "allow"}}, audit=spool)
    def check():
        receipt = json.loads(next((tmp_path / "spool").glob("*.json")).read_text())
        assert receipt["decision"] == "allow"
        assert receipt["delivery_status"] == "pending"
    assert run_tool(p, args={"secret": "PRIVATE-ARGS"}, on_effect=check)[1]
    files = list((tmp_path / "spool").glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text()
    assert "PRIVATE" not in raw and "perform action" not in raw
    receipt = json.loads(raw)
    assert {"audit_id", "correlation_id", "decision", "action_hash", "policy_hash",
            "delivery_status"} <= receipt.keys()
    def outage(receipt):
        raise RuntimeError("PRIVATE-EXPORTER-ERROR")
    assert spool.retry(outage) == 0
    reopened = runtime().DurableSpool(tmp_path / "spool")
    delivered = []
    assert reopened.retry(delivered.append) == 1
    assert len(delivered) == 1
    assert reopened.retry(delivered.append) == 0
    assert "PRIVATE" not in files[0].read_text()


def test_audit_disk_failure_denies_selected_tool(tmp_path, monkeypatch):
    import os
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=contract(requires=("durable-audit",)),
                       decisions={"pre_tool_call": {"decision": "allow"}}, audit=spool)
    def fail(fd):
        raise OSError("PRIVATE-FSYNC-ERROR")
    monkeypatch.setattr(os, "fsync", fail)
    _, effects, client, agent = run_tool(p)
    assert not effects
    assert "PRIVATE" not in str(client.requests)
    client.responses.extend(tool_responses("read"))
    asyncio.run(agent.run("read"))
    assert effects == [{}]


class ApprovalService:
    def __init__(self, case):
        self.case, self.requests = case, []
        self.first = None
        self.issued = {}

    async def resolve(self, intent):
        from dataclasses import replace
        self.requests.append(intent)
        if self.case == "outage":
            raise RuntimeError("PRIVATE-APPROVAL-ERROR")
        grant = runtime().ApprovalGrant(
            intent=intent, approved=self.case != "reject", approver="host:approver",
            approver_tenant=intent.tenant, approver_role="test-reviewer",
            provenance=f"receipt:{intent.nonce}",
        )
        self.issued[grant.provenance] = grant
        if self.case == "reject":
            grant = replace(grant, approved=False)
        if self.case == "changed":
            grant = replace(grant, intent=replace(intent, action_hash="wrong"))
        if self.case == "expired":
            grant = replace(grant, intent=replace(intent, expires_at=datetime.now(timezone.utc)))
        if self.case == "replay":
            self.first = self.first or grant
            grant = self.first
        return grant

    async def verify(self, grant, *, intent):
        return self.issued.get(grant.provenance) == grant


@pytest.mark.parametrize("case", ["outage", "reject", "changed", "expired", "approve", "replay"])
def test_native_approval_resolver_binding(tmp_path, case):
    service = ApprovalService(case)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=contract(requires=("approval", "durable-audit")),
                       decisions={"pre_tool_call": {"decision": "escalate"}},
                       approval_resolver=service, principal="host:user", tenant="host:tenant",
                       allowed_approval_roles=("test-reviewer",), audit=spool)
    _, effects, client, agent = run_tool(p, args={"amount": 10, "approved": True})
    assert len(effects) == (1 if case in ("approve", "replay") else 0)
    assert len(service.requests) == 1
    assert service.requests[0].principal == "host:user"
    assert service.requests[0].policy_hash == p.policy_digest()
    assert service.requests[0].context_identity
    assert "PRIVATE" not in str(client.requests)
    if case in ("outage", "changed", "expired"):
        assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is False
    if case in ("approve", "reject"):
        assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is True
    if case == "replay":
        client.responses.extend(tool_responses(args={"amount": 999}))
        asyncio.run(agent.run("different action"))
        assert len(effects) == 1


def test_required_approval_cannot_be_bypassed_by_allow_policy(tmp_path):
    p, _, _ = provider(tmp_path, document=contract(requires=("approval",)),
                       decisions={"pre_tool_call": {"decision": "allow"}})
    assert run_tool(p, args={"approved": True})[1] == []


@pytest.mark.parametrize("case", ["post-only-missing", "unsupported-tool-point"])
def test_unavailable_tool_binding_preflight_blocks_before_effect(tmp_path, case):
    point = "post_tool_call" if case == "post-only-missing" else "output"
    p, authority, _ = provider(tmp_path, document=contract(points=(point,)),
                               decisions={point: {"decision": "allow"}})
    if case == "post-only-missing":
        authority.valid = False
    assert run_tool(p)[1] == []
    assert p.health()["bindings"][f"act:{point}"]["healthy"] is False


@pytest.mark.parametrize("requirement", ["audit", "decision-receipt", "human-approval-record"])
def test_requirement_aliases_fail_closed_without_host_dependency(tmp_path, requirement):
    p, _, _ = provider(tmp_path, document=contract(requires=(requirement,)),
                       decisions={"pre_tool_call": {"decision": "allow"}})
    assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is False
    assert run_tool(p)[1] == []


def test_oversized_output_transform_is_not_released(tmp_path):
    from agent_framework import ChatResponse, Message
    from agent_hooks import InterceptionBlocked
    p, _, _ = provider(tmp_path, document=contract(lifecycle=("output",)),
                       decisions={"output": {"decision": "transform", "transform": {
                           "path": "$policy_target", "value": {"content": "x" * 10000},
                       }}}, max_output_bytes=2000)
    agent = runtime().create_governed_agent(p, client=model_client([
        ChatResponse(messages=[Message("assistant", ["ok"])])
    ]))
    with pytest.raises(InterceptionBlocked):
        asyncio.run(agent.run("input"))


def test_provider_hosted_tools_are_explicitly_unsupported(tmp_path):
    p, _, _ = provider(tmp_path)
    with pytest.raises(ValueError, match="provider-hosted"):
        runtime().create_governed_agent(
            p, client=model_client([]), tools=[{"type": "web_search_preview"}],
        )


def test_evaluate_only_is_explicit_and_production_enforces(tmp_path):
    p, _, _ = provider(tmp_path, environment="development")
    assert run_tool(p)[1] == [{}]
    assert p.health()["mode"] == "evaluate_only"
    assert all(b["status"] != "enforced" for b in p.health()["bindings"].values())
    with pytest.raises(ValueError):
        provider(tmp_path / "invalid", environment="unknown")


def test_invalid_argument_transform_is_binding_scoped(tmp_path):
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {
        "decision": "transform", "transform": {"path": "$policy_target", "value": ["invalid"]},
    }})
    _, effects, client, agent = run_tool(p)
    assert effects == []
    client.responses.extend(tool_responses("read"))
    asyncio.run(agent.run("read"))
    assert effects == [{}]


def test_other_binding_success_does_not_hide_engine_failure(tmp_path, monkeypatch):
    from agent_control_specification import AgentControl
    p, _, _ = provider(tmp_path, document=contract(lifecycle=("output",)), decisions={
        "pre_tool_call": {"decision": "allow"}, "output": {"decision": "allow"},
    })
    evaluate = AgentControl.evaluate_intervention_point
    async def fail_tool(self, point, *args, **kwargs):
        if point == "pre_tool_call":
            raise RuntimeError("PRIVATE-ENGINE-ERROR")
        return await evaluate(self, point, *args, **kwargs)
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", fail_tool)
    assert run_tool(p)[1] == []
    health = p.health()["bindings"]
    assert health["act:pre_tool_call"]["healthy"] is False
    assert health["lifecycle:output"]["healthy"] is True


@pytest.mark.parametrize("source", [
    "defaults", "run-options", "run-tools", "extra-body", "client-extra-body",
    "nested-extra-body", "web-search-options",
])
def test_effective_hosted_tools_never_reach_native_transport(tmp_path, source):
    from agent_framework import ChatResponse, Message
    p, _, _ = provider(tmp_path)
    client = model_client([ChatResponse(messages=[Message("assistant", ["ok"])])])
    hosted = [{"type": "web_search_preview"}]
    defaults, run = {}, {}
    if source == "defaults":
        defaults = {"tools": hosted}
    elif source == "run-options":
        run = {"options": {"tools": hosted}}
    elif source == "run-tools":
        run = {"tools": hosted}
    elif source == "extra-body":
        defaults = {"extra_body": {"tools": hosted}}
    elif source == "nested-extra-body":
        run = {"client_kwargs": {"extra_body": {"extra_body": {"tools": hosted}}}}
    elif source == "web-search-options":
        run = {"options": {"web_search_options": {}}}
    else:
        run = {"client_kwargs": {"extra_body": {"tools": hosted}}}
    with pytest.raises(ValueError, match="provider-hosted"):
        agent = runtime().create_governed_agent(p, client=client, default_options=defaults)
        asyncio.run(agent.run("input", **run))
    assert client.requests == []


def controlled_clock(monkeypatch):
    class Clock(datetime):
        current = datetime.now(timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    for module in ("governance_provider", "maf_agent_hooks_acs"):
        monkeypatch.setattr(importlib.import_module(
            f"skills.threadlight-govern.references.runtime.{module}"
        ), "datetime", Clock)
    return Clock


@pytest.mark.parametrize("stage", ["engine", "audit", "middleware"])
@pytest.mark.parametrize("decision", ["allow", "transform"])
def test_policy_expiring_in_flight_cannot_authorize_effect(tmp_path, monkeypatch, stage, decision):
    from agent_control_specification import AgentControl
    from agent_framework import FunctionMiddleware
    clock = controlled_clock(monkeypatch)
    spool = runtime().DurableSpool(tmp_path / "spool")
    verdict = {"decision": decision}
    if decision == "transform":
        verdict["transform"] = {"path": "$policy_target", "value": {"amount": 5}}
    p, authority, _ = provider(
        tmp_path, document=contract(requires=("durable-audit",)),
        decisions={"pre_tool_call": verdict}, audit=spool,
    )
    def expire():
        clock.current = authority.expires_at
    if stage == "engine":
        evaluate = AgentControl.evaluate_intervention_point
        async def delayed(self, *args, **kwargs):
            result = await evaluate(self, *args, **kwargs)
            expire()
            return result
        monkeypatch.setattr(AgentControl, "evaluate_intervention_point", delayed)
    if stage == "audit":
        append = spool.append
        def delayed_append(**kwargs):
            result = append(**kwargs)
            expire()
            return result
        monkeypatch.setattr(spool, "append", delayed_append)
    class Delay(FunctionMiddleware):
        async def process(self, context, call_next):
            expire()
            await asyncio.sleep(0)
            await call_next()
    _, effects, _, _ = run_tool(p, middleware=[Delay()] if stage == "middleware" else [])
    assert effects == []
    assert p.health()["bindings"]["act:pre_tool_call"]["healthy"] is False


@pytest.mark.parametrize("scope", ["lifecycle", "mixed", "tool"])
def test_post_tool_audit_requires_covering_pre_effect_control(tmp_path, monkeypatch, scope):
    import os
    document = contract(points=(), lifecycle=("post_tool_call",), requires=("durable-audit",))
    if scope == "mixed":
        document["governance"]["lifecycle_bindings"].append({
            **document["governance"]["lifecycle_bindings"][0],
            "lifecycle_point": "pre_tool_call", "requires": [],
        })
    if scope == "tool":
        document = contract(points=("pre_tool_call", "post_tool_call"))
        document["tools"][0]["requires"] = ["durable-audit"]
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=document, audit=spool, decisions={
        "pre_tool_call": {"decision": "allow"}, "post_tool_call": {"decision": "allow"},
    })
    def fail(fd):
        raise OSError("PRIVATE-FSYNC")
    monkeypatch.setattr(os, "fsync", fail)
    assert run_tool(p)[1] == []


@pytest.mark.parametrize("scope", ["tool", "lifecycle", "unbound"])
@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_selected_tool_exception_is_sanitized_before_native_serialization(
    tmp_path, scope, decision, caplog,
):
    from agent_framework import FunctionTool
    document = contract(points=("post_tool_call",)) if scope != "lifecycle" else contract(
        points=(), lifecycle=("post_tool_call",),
    )
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=document,
                       decisions={"post_tool_call": {"decision": decision}}, audit=spool)
    effects = []
    async def fail():
        effects.append(True)
        raise RuntimeError("PRIVATE-TOOL-FAILURE")
    name = "read" if scope == "unbound" else "act"
    client = model_client(tool_responses(name))
    agent = runtime().create_governed_agent(
        p, client=client, tools=[FunctionTool(name=name, func=fail)],
    )
    result = asyncio.run(agent.run("input"))
    wire = str(client.requests) + result.to_json()
    assert effects == [True]
    if scope == "unbound":
        assert "PRIVATE-TOOL-FAILURE" in wire
    else:
        assert "PRIVATE-TOOL-FAILURE" not in wire + caplog.text
        assert "threadlight:tool_unavailable" in wire
        assert p.health()["bindings"][f"{'lifecycle' if scope == 'lifecycle' else 'act'}:post_tool_call"]["healthy"]
        assert "PRIVATE" not in "".join(f.read_text() for f in spool.directory.glob("*.json"))


def test_identity_incomplete_approval_never_authorizes_effect(tmp_path):
    p, _, _ = provider(
        tmp_path, document=contract(requires=("approval",)),
        decisions={"pre_tool_call": {"decision": "escalate"}},
        principal="host:user", approval_resolver=ApprovalService("approve"),
    )
    assert run_tool(p)[1] == []


@pytest.mark.parametrize("case", [
    "approve", "reject", "missing-requester", "wrong-requester", "missing-tenant",
    "wrong-tenant", "missing-approver", "wrong-approver", "missing-role", "wrong-role",
    "wrong-approver-tenant", "missing-provenance", "forged-provenance", "unverified",
    "untrusted-service", "changed-action", "changed-policy", "changed-expiry",
    "changed-nonce", "changed-roles", "replay",
])
def test_approval_identity_and_trusted_attestation_are_exactly_bound(tmp_path, case):
    from dataclasses import fields, replace
    assert {"tenant", "allowed_roles", "policy_expires_at"} <= {
        f.name for f in fields(runtime().ApprovalIntent)
    }
    assert {"approver", "approver_tenant", "approver_role", "provenance"} <= {
        f.name for f in fields(runtime().ApprovalGrant)
    }
    class Service(ApprovalService):
        async def resolve(self, intent):
            grant = await super().resolve(intent)
            changes = {
                "missing-requester": {"principal": ""},
                "wrong-requester": {"principal": "other:user"},
                "missing-tenant": {"tenant": ""},
                "wrong-tenant": {"tenant": "other:tenant"},
                "changed-action": {"action_hash": "wrong"},
                "changed-policy": {"policy_hash": "wrong"},
                "changed-expiry": {"expires_at": intent.expires_at + timedelta(seconds=1)},
                "changed-nonce": {"nonce": "wrong"},
                "changed-roles": {"allowed_roles": ("other-role",)},
            }
            if case in changes:
                return replace(grant, intent=replace(intent, **changes[case]))
            changes = {
                "missing-approver": {"approver": ""},
                "wrong-approver": {"approver": "other:approver"},
                "missing-role": {"approver_role": ""},
                "wrong-role": {"approver_role": "unselected-role"},
                "wrong-approver-tenant": {"approver_tenant": "other:tenant"},
                "missing-provenance": {"provenance": ""},
                "forged-provenance": {"provenance": "agent-says-approved"},
            }
            return replace(grant, **changes.get(case, {}))

        async def verify(self, grant, *, intent):
            if case == "unverified":
                return False
            return await super().verify(grant, intent=intent)
    service = Service("reject" if case == "reject" else "replay" if case == "replay" else "approve")
    if case == "untrusted-service":
        service.verify = None
    p, _, _ = provider(
        tmp_path, document=contract(requires=("approval",)),
        decisions={"pre_tool_call": {"decision": "escalate"}},
        principal="host:user", tenant="host:tenant", allowed_approval_roles=("test-reviewer",),
        approval_resolver=service,
    )
    _, effects, client, agent = run_tool(p)
    assert len(effects) == (1 if case in ("approve", "replay") else 0)
    if case == "replay":
        client.responses.extend(tool_responses())
        asyncio.run(agent.run("repeat same action"))
        assert len(effects) == 1


@pytest.mark.parametrize("stage", ["resolver", "verification", "audit", "middleware"])
def test_approval_expiry_is_rechecked_at_effect(tmp_path, monkeypatch, stage):
    from dataclasses import fields
    from agent_framework import FunctionMiddleware
    assert "tenant" in {f.name for f in fields(runtime().ApprovalIntent)}
    clock = controlled_clock(monkeypatch)
    class Service(ApprovalService):
        async def resolve(self, intent):
            grant = await super().resolve(intent)
            if stage == "resolver":
                clock.current = intent.expires_at
            return grant

        async def verify(self, grant, *, intent):
            if stage == "verification":
                clock.current = intent.expires_at
            return await super().verify(grant, intent=intent)
    service = Service("approve")
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(requires=("approval", "durable-audit")),
        decisions={"pre_tool_call": {"decision": "escalate"}}, audit=spool,
        principal="host:user", tenant="host:tenant", allowed_approval_roles=("test-reviewer",),
        approval_resolver=service,
    )
    if stage == "audit":
        append = spool.append
        def delayed(**kwargs):
            result = append(**kwargs)
            clock.current = service.requests[0].expires_at
            return result
        monkeypatch.setattr(spool, "append", delayed)
    class Delay(FunctionMiddleware):
        async def process(self, context, call_next):
            clock.current = service.requests[0].expires_at
            await asyncio.sleep(0)
            await call_next()
    assert run_tool(p, middleware=[Delay()] if stage == "middleware" else [])[1] == []


@pytest.mark.parametrize("shape", ["sync", "sync-awaitable", "async"])
def test_exception_guard_preserves_original_tool_and_covers_callable_shapes(tmp_path, shape):
    from agent_framework import FunctionTool
    p, _, _ = provider(tmp_path, document=contract(points=("post_tool_call",)),
                       decisions={"post_tool_call": {"decision": "deny"}})
    async def fail_async():
        raise RuntimeError("PRIVATE-AWAITABLE")
    def fail_sync():
        raise RuntimeError("PRIVATE-SYNC")
    def returns_awaitable():
        return fail_async()
    original = {"sync": fail_sync, "sync-awaitable": returns_awaitable, "async": fail_async}[shape]
    tool = FunctionTool(name="act", func=original)
    client = model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, tools=[tool])
    response = asyncio.run(agent.run("input"))
    assert "PRIVATE" not in str(client.requests) + response.to_json()
    assert tool.func is original


def test_signed_expiry_cannot_be_renewed_during_evaluation(tmp_path, monkeypatch):
    from agent_control_specification import AgentControl
    p, authority, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    evaluate = AgentControl.evaluate_intervention_point
    async def renew(self, *args, **kwargs):
        result = await evaluate(self, *args, **kwargs)
        authority.expires_at += timedelta(hours=1)
        return result
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", renew)
    assert run_tool(p)[1] == []


@pytest.mark.parametrize("source", ["options", "client_kwargs"])
def test_nested_store_override_cannot_enable_provider_persistence(tmp_path, source):
    from agent_framework import ChatResponse, Message
    p, _, _ = provider(tmp_path)
    client = model_client([ChatResponse(messages=[Message("assistant", ["ok"])])])
    # This transport represents the OpenAI extra_body override merge.
    native = client._inner_get_response
    def capture(**kwargs):
        extra = kwargs.get("extra_body", kwargs["options"].get("extra_body", {}))
        assert extra.get("store") is False
        return native(**kwargs)
    client._inner_get_response = capture
    agent = runtime().create_governed_agent(p, client=client)
    asyncio.run(agent.run("input", **{source: {"extra_body": {"store": True}}}))


def test_missing_selected_roles_never_requests_approval(tmp_path):
    service = ApprovalService("approve")
    p, _, _ = provider(
        tmp_path, document=contract(requires=("approval",)),
        decisions={"pre_tool_call": {"decision": "escalate"}},
        principal="host:user", tenant="host:tenant", approval_resolver=service,
    )
    assert run_tool(p)[1] == []
    assert service.requests == []


def test_approval_wire_schema_matches_portable_protocol(tmp_path):
    from dataclasses import asdict
    from jsonschema import Draft7Validator, FormatChecker
    schema_path = RUNTIME / "approval.schema.json"
    assert schema_path.exists(), "portable approval protocol schema missing"
    schema = json.loads(schema_path.read_text())
    Draft7Validator.check_schema(schema)
    service = ApprovalService("approve")
    p, _, _ = provider(
        tmp_path, document=contract(requires=("approval",)),
        decisions={"pre_tool_call": {"decision": "escalate"}},
        principal="host:user", tenant="host:tenant", allowed_approval_roles=("test-reviewer",),
        approval_resolver=service,
    )
    assert run_tool(p)[1] == [{}]
    grant = next(iter(service.issued.values()))
    wire = json.loads(json.dumps(asdict(grant), default=lambda value: value.isoformat()))
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    validator.validate(wire)
    for field in ("approver", "approver_tenant", "approver_role", "provenance"):
        incomplete = deepcopy(wire)
        del incomplete[field]
        assert list(validator.iter_errors(incomplete)), field
    for field in ("tenant", "principal", "allowed_roles", "nonce", "expires_at", "policy_expires_at"):
        incomplete = deepcopy(wire)
        del incomplete["intent"][field]
        assert list(validator.iter_errors(incomplete)), field


@pytest.mark.parametrize("stream", [False, True])
def test_approval_positive_control_survives_native_run_scopes(tmp_path, stream):
    from agent_framework import FunctionTool
    p, _, _ = provider(
        tmp_path, document=contract(requires=("approval",)),
        decisions={"pre_tool_call": {"decision": "escalate"}},
        principal="host:user", tenant="host:tenant", allowed_approval_roles=("test-reviewer",),
        approval_resolver=ApprovalService("approve"),
    )
    effects = []
    async def act():
        effects.append(True)
        return "ok"
    client = model_client(tool_responses() + tool_responses())
    agent = runtime().create_governed_agent(p, client=client, tools=[FunctionTool(name="act", func=act)])
    async def runs():
        for _ in range(2):
            response = agent.run("input", stream=stream)
            if stream:
                await response.get_final_response()
            else:
                await response
    asyncio.run(runs())
    assert effects == [True, True]


def test_lifecycle_pre_tool_audit_covers_every_local_effect(tmp_path):
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_tool_call", "post_tool_call"),
                                    requires=("durable-audit",)),
        decisions={"pre_tool_call": {"decision": "allow"}, "post_tool_call": {"decision": "allow"}},
        audit=spool,
    )
    def before():
        assert list(spool.directory.glob("*.json"))
    assert run_tool(p, name="read", on_effect=before)[1] == [{}]


def test_monotonic_policy_deadline_survives_wall_clock_rollback(tmp_path, monkeypatch):
    from agent_control_specification import AgentControl
    clock = controlled_clock(monkeypatch)
    module = importlib.import_module("skills.threadlight-govern.references.runtime.governance_provider")
    # Override this module's time object, not asyncio's event-loop clock.
    from types import SimpleNamespace
    ticks = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: ticks[0]))
    p, authority, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    evaluate = AgentControl.evaluate_intervention_point
    async def delayed(self, *args, **kwargs):
        result = await evaluate(self, *args, **kwargs)
        ticks[0] += (authority.expires_at - clock.current).total_seconds() + 1
        clock.current -= timedelta(hours=1)
        return result
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", delayed)
    assert run_tool(p)[1] == []


def test_policy_expiry_between_worker_and_deferred_effect(tmp_path, monkeypatch):
    from agent_framework import FunctionTool
    clock = controlled_clock(monkeypatch)
    p, authority, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    effects = []
    async def deferred():
        effects.append(True)
        return "effect"
    def prepare():
        clock.current = authority.expires_at
        return deferred()
    agent = runtime().create_governed_agent(
        p, client=model_client(tool_responses()), tools=[FunctionTool(name="act", func=prepare)],
    )
    asyncio.run(agent.run("input"))
    assert effects == []


def test_progressive_provider_hosted_tool_never_reaches_next_transport(tmp_path):
    from agent_framework import FunctionInvocationContext, FunctionTool
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    async def expand(ctx: FunctionInvocationContext):
        ctx.add_tools([{"type": "web_search_preview"}])
        return "exposed"
    client = model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, tools=[FunctionTool(name="act", func=expand)])
    with pytest.raises(ValueError, match="provider-hosted"):
        asyncio.run(agent.run("input"))
    assert len(client.requests) == 1
    assert all(not isinstance(t, dict) for t in client.requests[0][1]["tools"])


@pytest.mark.parametrize("source", ["defaults", "options", "tools"])
def test_unbound_local_option_tools_remain_permitted(tmp_path, source, monkeypatch):
    from agent_control_specification import AgentControl
    from agent_framework import FunctionTool
    p, _, _ = provider(tmp_path)
    async def forbidden(*args, **kwargs):
        pytest.fail("unbound option tool evaluated ACS")
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", forbidden)
    effects = []
    async def read():
        effects.append(True)
        return "read"
    tools = [FunctionTool(name="read", func=read)]
    client = model_client(tool_responses("read"))
    agent = runtime().create_governed_agent(
        p, client=client, default_options={"tools": tools} if source == "defaults" else {},
    )
    run = {"options": {"tools": tools}} if source == "options" else {"tools": tools} if source == "tools" else {}
    asyncio.run(agent.run("input", **run))
    assert effects == [True]


def test_policy_refresh_cannot_outlive_its_authorization(tmp_path, monkeypatch):
    import yaml
    clock = controlled_clock(monkeypatch)
    p, authority, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    load = yaml.safe_load
    verified = [False]
    verify = authority.verify
    def signed(bundle):
        verified[0] = True
        return verify(bundle)
    def delayed(document):
        value = load(document)
        if verified[0]:
            clock.current = authority.expires_at
        return value
    monkeypatch.setattr(authority, "verify", signed)
    monkeypatch.setattr(yaml, "safe_load", delayed)
    p._refresh()
    assert not p.health()["bindings"]["act:pre_tool_call"]["healthy"]


def test_selected_native_tool_override_failure_is_still_sanitized(tmp_path):
    from agent_framework import FunctionTool
    p, _, _ = provider(tmp_path, document=contract(points=("post_tool_call",)),
                       decisions={"post_tool_call": {"decision": "deny"}})
    effects = []
    class NativeTool(FunctionTool):
        async def invoke(self, **kwargs):
            effects.append(True)
            raise RuntimeError("PRIVATE-INVOKE")
    client = model_client(tool_responses())
    agent = runtime().create_governed_agent(
        p, client=client, tools=[NativeTool(name="act", func=lambda: None)],
    )
    result = asyncio.run(agent.run("input"))
    assert effects == [True]
    assert "PRIVATE-INVOKE" not in str(client.requests) + result.to_json()


@pytest.mark.parametrize("approval", [False, True])
@pytest.mark.parametrize("mutation", [False, True])
def test_final_native_arguments_are_validated_once_and_exactly_authorized(tmp_path, monkeypatch, approval, mutation):
    from agent_framework import FunctionMiddleware, FunctionTool
    from pydantic import BaseModel, field_validator
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    observed = []
    intercept = adapter.AcsInterceptor.intercept
    async def capture(self, context):
        if context["interception_point"] == "pre_tool_call":
            observed.append(deepcopy(context))
        return await intercept(self, context)
    monkeypatch.setattr(adapter.AcsInterceptor, "intercept", capture)
    validations, effects = [], []
    class Arguments(BaseModel):
        amount: int
        @field_validator("amount")
        @classmethod
        def increment(cls, value):
            validations.append(value)
            return value + 1
    class Mutate(FunctionMiddleware):
        async def process(self, context, call_next):
            context.arguments["amount"] += 1
            await asyncio.sleep(0)
            await call_next()
    service = ApprovalService("approve")
    p, _, _ = provider(
        tmp_path, decisions={"pre_tool_call": {"decision": "allow"}},
        document=contract(requires=("approval",) if approval else ()),
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    # Observe the context entering the real ACS adapter, not a synthetic policy oracle.
    original_safe = p._safe_provider
    hashes = []
    p._safe_provider = lambda identity: (hashes.append(identity["action_hash"]) or original_safe(identity))
    async def act(amount: int):
        effects.append({"amount": amount})
        return "done"
    tool = FunctionTool(name="act", func=act, input_model=Arguments)
    schema = deepcopy(tool.parameters())
    client = model_client(tool_responses(args={"amount": 9}))
    agent = runtime().create_governed_agent(
        p, client=client, tools=[tool], middleware=[Mutate()] if mutation else [],
    )
    asyncio.run(agent.run("input"))
    assert validations == [9], "native automatic dispatch must not run a validator twice"
    assert effects == ([] if mutation else [{"amount": 10}])
    assert tool.parameters() == schema
    assert client.requests[0][1]["tools"][0].parameters() == schema
    if approval and not mutation:
        assert service.requests[0].action_hash == hashes[0]
        actual = {**observed[0], "target": effects[0],
                  "tool_call": {**observed[0]["tool_call"], "args": effects[0]}}
        assert service.requests[0].action_hash == adapter.action_hash(p, actual)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("stage", ["chat", "preparation"])
def test_lifecycle_expiry_at_actual_native_transport(tmp_path, monkeypatch, stream, stage):
    from agent_framework import ChatMiddleware
    clock = controlled_clock(monkeypatch)
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)),
        decisions={"pre_model_call": {"decision": "allow"}},
    )
    class Delay(ChatMiddleware):
        async def process(self, context, call_next):
            await asyncio.sleep(0)
            clock.current = authority.expires_at
            await call_next()
    client = native_model_client(tool_responses())
    prepared = []
    if stage == "preparation":
        # Selected pre-model compaction is unsupported. Exercise the real
        # preparation path's non-target-changing token annotation instead.
        class ExpiringTokenizer:
            def count_tokens(self, text):
                prepared.append(True)
                clock.current = authority.expires_at
                return 1
        client.tokenizer = ExpiringTokenizer()
    agent = runtime().create_governed_agent(p, client=client, middleware=[Delay()] if stage == "chat" else [])
    async def run():
        if stream:
            async for _ in agent.run("input", stream=True):
                pass
        else:
            await agent.run("input")
    try:
        asyncio.run(run())
    except Exception as exc:
        assert "threadlight:policy_unavailable" in str(exc)
    assert client.requests == [], "expired lifecycle authorization reached the transport"
    if stage == "preparation":
        assert prepared


@pytest.mark.parametrize("point", ["startup", "input"])
def test_run_lifecycle_expiry_after_application_await(tmp_path, monkeypatch, point):
    from agent_framework import AgentMiddleware
    clock = controlled_clock(monkeypatch)
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=(point,)),
        decisions={"agent_startup" if point == "startup" else point: {"decision": "allow"}},
    )
    class Delay(AgentMiddleware):
        async def process(self, context, call_next):
            await asyncio.sleep(0)
            clock.current = authority.expires_at
            await call_next()
    client = native_model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, middleware=[Delay()])
    try:
        asyncio.run(agent.run("input"))
    except Exception as exc:
        assert "threadlight:policy_unavailable" in str(exc)
    assert client.requests == []


@pytest.mark.parametrize("surface", ["function_middleware", "chat_middleware", "agent_middleware"])
def test_preconfigured_native_client_middleware_rejected_before_effect(tmp_path, surface):
    from agent_framework import AgentMiddleware, ChatMiddleware, FunctionMiddleware
    effects = []
    base = {"function_middleware": FunctionMiddleware, "chat_middleware": ChatMiddleware,
            "agent_middleware": AgentMiddleware}[surface]
    class Outer(base):
        async def process(self, context, call_next):
            effects.append("prehook")
            await call_next()
    client = model_client(tool_responses())
    setattr(client, surface, [Outer()])
    p, _, _ = provider(tmp_path)
    rejected = False
    try:
        agent = runtime().create_governed_agent(p, client=client, tools=[lambda: None])
        asyncio.run(agent.run("input"))
    except ValueError:
        rejected = True
    assert rejected, "client middleware precedes the native bundle and must be rejected"
    assert effects == []
    assert client.requests == []


@pytest.mark.parametrize("failure", ["engine", "native"])
def test_production_native_failure_sink_is_durable_and_payload_free(tmp_path, monkeypatch, failure):
    from agent_control_specification import AgentControl
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=contract(requires=("durable-audit",)), audit=spool)
    if failure == "engine":
        async def fail(*args, **kwargs):
            raise RuntimeError("PRIVATE-ENGINE-EXCEPTION")
        monkeypatch.setattr(AgentControl, "evaluate_intervention_point", fail)
    else:
        original = adapter.AcsInterceptor.intercept
        async def fail(self, context):
            if context["interception_point"] == "pre_tool_call":
                raise RuntimeError("PRIVATE-NATIVE-EXCEPTION")
            return await original(self, context)
        monkeypatch.setattr(adapter.AcsInterceptor, "intercept", fail)
    try:
        _, effects, _, _ = run_tool(p, args={"secret": "PRIVATE-ARGUMENT"})
        assert effects == []
    except Exception as exc:
        assert "PRIVATE" not in str(exc)
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    assert len(records) == 1, "one durable failure receipt, not unselected native allow records"
    assert records[0]["decision"] == "deny"
    assert records[0]["reason_code"] == (
        "threadlight:engine_failure" if failure == "engine" else "threadlight:native_failure"
    )
    assert records[0]["interception_point"] == "pre_tool_call"
    assert "PRIVATE" not in json.dumps(records)
    assert not p.health()["bindings"]["act:pre_tool_call"]["healthy"]


def test_native_function_client_ordering_is_actually_outer(tmp_path):
    from agent_framework import Agent, FunctionMiddleware, FunctionTool
    effects = []
    class Outer(FunctionMiddleware):
        async def process(self, context, call_next):
            effects.append("prehook")
            await call_next()
    p, _, _ = provider(tmp_path)
    client = model_client(tool_responses())
    client.function_middleware = [Outer()]
    native = Agent(client=client, middleware=[p.middleware()],
                   tools=[FunctionTool(name="act", func=lambda: effects.append("effect"))])
    asyncio.run(native.run("input"))
    assert effects == ["prehook"], "real native ordering, not a fake middleware harness"


def test_native_failure_receipt_disk_error_is_unhealthy(tmp_path, monkeypatch):
    from agent_control_specification import AgentControl
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=contract(requires=("durable-audit",)), audit=spool)
    attempts = []
    def broken(**kwargs):
        attempts.append(kwargs)
        raise OSError("PRIVATE-SINK-ERROR")
    async def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE-ENGINE-ERROR")
    monkeypatch.setattr(spool, "append", broken)
    monkeypatch.setattr(AgentControl, "evaluate_intervention_point", fail)
    _, effects, client, _ = run_tool(p)
    assert effects == []
    assert len(attempts) == 1
    assert p.health()["bindings"]["act:pre_tool_call"]["reason"] == "threadlight:audit_unavailable"
    assert "PRIVATE" not in str(client.requests)


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_failed_native_tool_never_receipts_a_successful_result(tmp_path, decision):
    from agent_framework import FunctionTool
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(points=("post_tool_call",)),
        decisions={"post_tool_call": {"decision": decision}}, audit=spool,
    )
    async def fail():
        raise RuntimeError("PRIVATE-TOOL")
    client = model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, tools=[FunctionTool(name="act", func=fail)])
    asyncio.run(agent.run("input"))
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    assert len(records) == 1
    assert records[0]["decision"] == "error"
    assert records[0]["reason_code"] == "threadlight:tool_unavailable"
    assert "PRIVATE" not in json.dumps(records)


@pytest.mark.parametrize("surface", ["run", "client_kwargs"])
def test_extra_run_hooks_are_rejected_before_application_effects(tmp_path, surface):
    from agent_framework import AgentMiddleware
    p, _, _ = provider(tmp_path)
    extra, _, _ = provider(tmp_path / "extra")
    effects = []
    class Effect(AgentMiddleware):
        async def process(self, context, call_next):
            effects.append(True)
            await call_next()
    client = model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, middleware=[Effect()])
    entries = {"middleware": [extra.middleware()]}
    options = entries if surface == "run" else {"client_kwargs": entries}
    with pytest.raises(ValueError):
        asyncio.run(agent.run("input", **options))
    assert effects == []
    assert client.requests == []


def test_native_additional_tools_remain_declaration_only(tmp_path):
    from agent_framework import FunctionTool
    from pydantic import BaseModel, field_validator
    calls, effects = [], []
    class Args(BaseModel):
        amount: int
        @field_validator("amount")
        @classmethod
        def increment(cls, value):
            calls.append(value)
            return value + 1
    async def act(amount):
        effects.append(amount)
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    client = model_client(tool_responses(args={"amount": 9}))
    client.function_invocation_configuration["additional_tools"] = [
        FunctionTool(name="act", func=act, input_model=Args),
    ]
    agent = runtime().create_governed_agent(p, client=client, tools=[FunctionTool(name="read", func=lambda: "read")])
    asyncio.run(agent.run("input"))
    # In this pin additional_tools are declaration-only requests, not local dispatch.
    assert calls == []
    assert effects == []


@pytest.mark.parametrize("expire", [False, True])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("mounted", [False, True])
def test_actual_native_http_transport_checks_after_async_request_hooks(tmp_path, monkeypatch, expire, selected, mounted):
    import httpx
    from openai import AsyncOpenAI
    from agent_framework.openai import OpenAIChatClient
    clock = controlled_clock(monkeypatch)
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)) if selected else contract(),
        decisions={"pre_model_call": {"decision": "allow"}},
    )
    requests, hooks = [], []
    async def request_hook(request):
        await asyncio.sleep(0)
        hooks.append(True)
        if expire:
            clock.current = authority.expires_at
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "resp_local", "object": "response", "created_at": 0,
            "status": "completed", "model": "local", "output": [{
                "id": "msg_local", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "finished", "annotations": []}],
            }],
        })
    async def run():
        wire = httpx.MockTransport(transport)
        async with httpx.AsyncClient(
            transport=wire, event_hooks={"request": [request_hook]},
            mounts={"https://api.openai.com": wire} if mounted else None,
        ) as http:
            sdk = AsyncOpenAI(api_key="local-test-only", http_client=http, max_retries=0)
            client = OpenAIChatClient(model="local", async_client=sdk)
            agent = runtime().create_governed_agent(p, client=client)
            try:
                result = await agent.run("input")
                if not (expire and selected):
                    assert result.text == "finished"
            except Exception as exc:
                assert expire and selected and "threadlight:policy_unavailable" in str(exc)
    asyncio.run(run())
    assert hooks == [True]
    assert len(requests) == (0 if expire and selected else 1), "HTTP transport ran after request hook expired authorization"


@pytest.mark.parametrize("point", ["input", "pre_model_call"])
def test_lifecycle_approval_expiry_after_application_await(tmp_path, monkeypatch, point):
    from agent_framework import AgentMiddleware, ChatMiddleware
    clock = controlled_clock(monkeypatch)
    service = ApprovalService("approve")
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=(point,), requires=("approval",)),
        decisions={point: {"decision": "allow"}},
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    class Delay(AgentMiddleware if point == "input" else ChatMiddleware):
        async def process(self, context, call_next):
            await asyncio.sleep(0)
            clock.current = service.requests[-1].expires_at
            await call_next()
    client = native_model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, middleware=[Delay()])
    try:
        asyncio.run(agent.run("input"))
    except Exception as exc:
        assert "threadlight:approval_unavailable" in str(exc)
    assert len(service.requests) == 1
    assert client.requests == []


def test_transformed_authorization_receipt_hash_matches_final_effect(tmp_path, monkeypatch):
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(requires=("durable-audit",)), audit=spool,
        decisions={"pre_tool_call": {
            "decision": "transform", "transform": {"path": "$policy_target", "value": {"amount": 5}},
        }},
    )
    contexts = []
    original = adapter.AcsInterceptor.intercept
    async def capture(self, context):
        if context["interception_point"] == "pre_tool_call":
            contexts.append(deepcopy(context))
        return await original(self, context)
    monkeypatch.setattr(adapter.AcsInterceptor, "intercept", capture)
    _, effects, _, _ = run_tool(p, args={"amount": 9})
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    assert effects == [{"amount": 5}]
    assert len(records) == 1
    actual = {**contexts[0], "target": effects[0],
              "tool_call": {**contexts[0]["tool_call"], "args": effects[0]}}
    assert records[0]["action_hash"] == adapter.action_hash(p, actual)


def test_selected_input_remains_fresh_at_otherwise_unbound_effect(tmp_path, monkeypatch):
    from agent_framework import FunctionMiddleware, FunctionTool
    clock = controlled_clock(monkeypatch)
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("input",)),
        decisions={"input": {"decision": "allow"}},
    )
    class Delay(FunctionMiddleware):
        async def process(self, context, call_next):
            await asyncio.sleep(0)
            clock.current = authority.expires_at
            await call_next()
    effects = []
    async def read():
        effects.append(True)
    client = native_model_client(tool_responses("read"))
    agent = runtime().create_governed_agent(
        p, client=client, middleware=[Delay()], tools=[FunctionTool(name="read", func=read)],
    )
    with pytest.raises(Exception, match="threadlight:policy_unavailable"):
        asyncio.run(agent.run("input"))
    assert effects == []


def test_final_expiry_denial_is_durably_recorded_not_only_earlier_allow(tmp_path, monkeypatch):
    from agent_framework import ChatMiddleware
    clock = controlled_clock(monkeypatch)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",), requires=("durable-audit",)),
        decisions={"pre_model_call": {"decision": "allow"}}, audit=spool,
    )
    class Delay(ChatMiddleware):
        async def process(self, context, call_next):
            await asyncio.sleep(0)
            clock.current = authority.expires_at
            await call_next()
    client = native_model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, middleware=[Delay()])
    with pytest.raises(Exception, match="threadlight:policy_unavailable"):
        asyncio.run(agent.run("input"))
    assert client.requests == []
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    assert sorted(r["decision"] for r in records) == ["allow", "deny"]
    denied = next(r for r in records if r["decision"] == "deny")
    assert denied["reason_code"] == "threadlight:policy_unavailable"
    assert denied["interception_point"] == "pre_model_call"


def test_late_native_run_middleware_cannot_reuse_one_tool_authorization(tmp_path):
    from agent_framework import FunctionMiddleware, FunctionTool
    effects = []
    class Twice(FunctionMiddleware):
        async def process(self, context, call_next):
            await call_next()
            await asyncio.sleep(0)
            await call_next()
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    async def act():
        effects.append(True)
    client = model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, tools=[FunctionTool(name="act", func=act)])
    asyncio.run(agent.run("input", middleware=[Twice()]))
    assert effects == [True]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("expire", [False, True])
def test_output_approval_freshness_at_release_after_native_shutdown(tmp_path, monkeypatch, stream, expire):
    from agent_framework import ChatResponse, Message
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    clock = controlled_clock(monkeypatch)
    service = ApprovalService("approve")
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("output",), requires=("approval",)),
        decisions={"output": {"decision": "allow"}},
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    original = adapter.AcsInterceptor.intercept
    async def delayed(self, context):
        if context["interception_point"] == "agent_shutdown" and expire:
            await asyncio.sleep(0)
            clock.current = service.requests[-1].expires_at
        return await original(self, context)
    monkeypatch.setattr(adapter.AcsInterceptor, "intercept", delayed)
    client = model_client([ChatResponse(messages=[Message("assistant", ["finished"])])])
    agent = runtime().create_governed_agent(p, client=client)
    async def run():
        result = agent.run("input", stream=stream)
        return await result.get_final_response() if stream else await result
    if expire:
        with pytest.raises(Exception, match="threadlight:approval_unavailable"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()).text == "finished"
    assert len(client.requests) == 1


def test_guard_preserves_native_bound_self_context_and_invocation_budget(tmp_path):
    from agent_framework import FunctionInvocationContext, tool
    effects = []
    class Service:
        @tool(name="act", max_invocations=1)
        async def act(self, amount: int, ctx: FunctionInvocationContext):
            assert ctx.arguments == {"amount": amount}
            effects.append((self, amount))
            return "done"
    service = Service()
    original = service.act
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    client = model_client(tool_responses(args={"amount": 10})[:1] + tool_responses(args={"amount": 10}))
    agent = runtime().create_governed_agent(p, client=client, tools=[original])
    asyncio.run(agent.run("input"))
    assert effects == [(service, 10)]
    assert original.invocation_count == 0


@pytest.mark.parametrize("shape", ["money", "nested"])
@pytest.mark.parametrize("decision", ["allow", "approval", "transform"])
def test_native_rich_arguments_keep_types_and_exact_wire_authorization(tmp_path, monkeypatch, shape, decision):
    from datetime import date
    from decimal import Decimal
    from enum import Enum
    from uuid import UUID
    from agent_framework import Agent, FunctionTool
    from agent_framework._agent_hooks import _ToolArgumentsCodec
    from pydantic import BaseModel, field_validator, model_serializer
    validations, serializations, effects, observed, hashes = [], [], [], [], []
    class State(str, Enum):
        READY = "ready"
    class Money(BaseModel):
        amount: Decimal
        @field_validator("amount")
        @classmethod
        def validate_amount(cls, value):
            validations.append(value)
            return value
        @model_serializer(mode="wrap")
        def serialize(self, handler):
            serializations.append(self.amount)
            return handler(self)
    class Entry(Money):
        at: datetime
        day: date
        state: State
        identifier: UUID
    class Nested(BaseModel):
        entries: list[Entry]
    original = {"amount": "10.50"} if shape == "money" else {"entries": [{
        "amount": "10.50", "at": "2026-09-05T09:00:00+00:00", "day": "2026-09-05",
        "state": "ready", "identifier": "12345678-1234-5678-1234-567812345678",
    }]}
    model = Money if shape == "money" else Nested
    expected_native = model.model_validate(original).model_dump(exclude_unset=True)
    target = _ToolArgumentsCodec.to_wire(expected_native)
    if decision == "transform":
        (target if shape == "money" else target["entries"][0])["amount"] = "20.25"
    async def act(**values):
        effects.append(values)
        return "done"
    tool = FunctionTool(name="act", func=act, input_model=model)
    unbound = native_model_client(tool_responses(args=original))
    asyncio.run(Agent(client=unbound, tools=[tool]).run("input"))
    assert len(effects) == 1, "positive control must be accepted by the actual pinned framework"
    effects.clear()
    validations.clear()
    serializations.clear()
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    intercept = adapter.AcsInterceptor.intercept
    async def capture(self, context):
        if context["interception_point"] == "pre_tool_call":
            observed.append(deepcopy(context))
        return await intercept(self, context)
    monkeypatch.setattr(adapter.AcsInterceptor, "intercept", capture)
    service = ApprovalService("approve")
    spool = runtime().DurableSpool(tmp_path / "spool")
    policy = {"decision": "allow"}
    if decision == "transform":
        policy = {"decision": "transform", "transform": {"path": "$policy_target", "value": target}}
    p, _, _ = provider(
        tmp_path, decisions={"pre_tool_call": policy},
        document=contract(requires=("durable-audit", "approval") if decision == "approval"
                          else ("durable-audit",)), audit=spool,
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    p._safe_provider = lambda identity: (hashes.append(identity["action_hash"]) or {})
    client = native_model_client(tool_responses(args=original))
    result = asyncio.run(runtime().create_governed_agent(p, client=client, tools=[tool]).run("input"))
    assert len(effects) == 1, result.to_json()
    final = effects[0] if shape == "money" else effects[0]["entries"][0]
    assert type(final["amount"]) is Decimal
    assert final["amount"] == Decimal("20.25" if decision == "transform" else "10.50")
    if shape == "nested":
        assert type(final["at"]) is datetime and type(final["day"]) is date
        assert type(final["state"]) is State and type(final["identifier"]) is UUID
    expected_validations = [Decimal("10.50")] + ([Decimal("20.25")] if decision == "transform" else [])
    assert validations == serializations == expected_validations
    wire = _ToolArgumentsCodec.to_wire(effects[0])
    final_hash = adapter.action_hash(p, observed[0], wire)
    assert hashes == [adapter.action_hash(p, observed[0])]
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    assert any(r["action_hash"] == final_hash and r["decision"] == policy["decision"] for r in records)
    if decision != "transform":
        assert final_hash == hashes[0]
    if decision == "approval":
        assert service.requests[0].action_hash == final_hash
    asyncio.run(client.client.close())
    asyncio.run(unbound.client.close())


@pytest.mark.parametrize("detailed", [False, True])
def test_native_argument_codec_failure_is_private_before_hooks(tmp_path, detailed):
    from typing import Any
    from agent_framework import FunctionTool
    from pydantic import BaseModel, field_serializer
    effects, serializations = [], []
    class Unsupported:
        __slots__ = ()
        def __str__(self):
            raise RuntimeError("PRIVATE-CODEC-DIAGNOSTIC")
    class Arguments(BaseModel):
        amount: Any
        @field_serializer("amount")
        def serialize(self, value):
            serializations.append(value)
            return Unsupported()
    async def act(amount):
        effects.append(amount)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, audit=spool, decisions={"pre_tool_call": {"decision": "allow"}})
    client = native_model_client(tool_responses(args={"amount": 10}))
    client.function_invocation_configuration["include_detailed_errors"] = detailed
    agent = runtime().create_governed_agent(
        p, client=client, tools=[FunctionTool(name="act", func=act, input_model=Arguments)],
    )
    result = asyncio.run(agent.run("input"))
    assert effects == [] and serializations == [10]
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    assert "PRIVATE" not in str(client.requests) + result.to_json() + json.dumps(records)
    results = [c for m in result.messages for c in m.contents if c.type == "function_result"]
    assert results[0].exception == "threadlight:invalid_arguments"
    assert not any(r["decision"] in {"allow", "transform"} for r in records)
    asyncio.run(client.client.close())


@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("source", ["agent", "options", "progressive"])
def test_native_invocation_budget_survives_runs(tmp_path, limit, source):
    from agent_framework import Agent, ChatMiddleware, FunctionInvocationContext, FunctionTool
    effects, executions = [], []
    async def act(ctx: FunctionInvocationContext):
        effects.append("effect")
        executions.append(ctx.function)
        return "done"
    original = FunctionTool(name="act", func=act, max_invocations=limit)
    native_tool = FunctionTool(name="act", func=act, max_invocations=limit)
    unbound = native_model_client([])
    native_agent = Agent(client=unbound, tools=[native_tool])
    async def runs(agent, client, **kwargs):
        outcomes = []
        for _ in range(limit + 1):
            client.responses.extend(tool_responses())
            outcomes.append(await agent.run("input", **kwargs))
        return outcomes
    asyncio.run(runs(native_agent, unbound))
    assert len(effects) == native_tool.invocation_count == limit
    effects.clear()
    executions.clear()
    class Expose(ChatMiddleware):
        async def process(self, context, call_next):
            context.options.setdefault("tools", [])[:] = [original]
            await call_next()
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    client = native_model_client([])
    agent = runtime().create_governed_agent(
        p, client=client, tools=[original] if source == "agent" else [],
        middleware=[Expose()] if source == "progressive" else [],
    )
    options = {"tools": [original]} if source == "options" else {}
    results = asyncio.run(runs(agent, client, options=options))
    assert len(effects) == limit, "a new run must not replenish the native tool's lifetime budget"
    assert all(tool is executions[0] for tool in executions)
    assert executions[0].invocation_count == limit
    assert original.invocation_count == 0
    assert all("threadlight:tool_unavailable" not in r.to_json() for r in results[:-1])
    assert "threadlight:tool_unavailable" in results[-1].to_json()
    if source == "options":
        assert options["tools"] == [original]
    # The caller's shared original remains usable by a separate unbound agent.
    unbound.responses.extend(tool_responses())
    asyncio.run(Agent(client=unbound, tools=[original]).run("input"))
    assert original.invocation_count == 1 and len(effects) == limit + 1
    asyncio.run(client.client.close())
    asyncio.run(unbound.client.close())


def test_native_parallel_invocation_budget_is_not_replenished(tmp_path):
    from agent_framework import Agent, FunctionTool
    effects = []
    async def act():
        await asyncio.sleep(0)
        effects.append("effect")
        return "done"
    async def exercise(agent, client):
        responses = tool_responses()
        for call_id in ("call-2", "call-3"):
            extra = deepcopy(responses[0].messages[0].contents[0])
            extra.call_id = call_id
            responses[0].messages[0].contents.append(extra)
        client.responses.extend(responses)
        first = await agent.run("input")
        assert len(effects) == 2
        client.responses.extend(tool_responses())
        second = await agent.run("input")
        return first, second
    native_tool = FunctionTool(name="act", func=act, max_invocations=2)
    unbound = native_model_client([])
    asyncio.run(exercise(Agent(client=unbound, tools=[native_tool]), unbound))
    assert len(effects) == native_tool.invocation_count == 2
    effects.clear()
    original = FunctionTool(name="act", func=act, max_invocations=2)
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    client = native_model_client([])
    agent = runtime().create_governed_agent(p, client=client, tools=[original])
    first, second = asyncio.run(exercise(agent, client))
    assert len(effects) == 2 and original.invocation_count == 0
    for result in (first, second):
        assert "threadlight:tool_unavailable" in result.to_json()
    asyncio.run(client.client.close())
    asyncio.run(unbound.client.close())


@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("kind", ["sync", "sync-loop", "async", "returns-awaitable"])
@pytest.mark.parametrize("success_between", [False, True])
def test_native_exception_budget_matches_actual_call_failures(tmp_path, limit, kind, success_between):
    from agent_framework import Agent, FunctionInvocationContext, FunctionTool
    attempts, executions = [], []
    def attempt(ctx):
        attempts.append(len(attempts))
        executions.append(ctx.function)
        if success_between and len(attempts) == 2:
            return "success"
        raise ValueError("PRIVATE-APPLICATION-FAILURE")
    def sync(ctx: FunctionInvocationContext):
        return attempt(ctx)
    async def asynchronous(ctx: FunctionInvocationContext):
        await asyncio.sleep(0)
        return attempt(ctx)
    def returns_awaitable(ctx: FunctionInvocationContext):
        return asynchronous(ctx)
    function = {"sync": sync, "sync-loop": sync, "async": asynchronous,
                "returns-awaitable": returns_awaitable}[kind]
    def tool():
        value = FunctionTool(name="act", func=function, max_invocation_exceptions=limit)
        value._invoke_sync_on_event_loop = kind == "sync-loop"
        return value
    async def runs(agent, client):
        results = []
        for _ in range(4):
            client.responses.extend(tool_responses())
            results.append(await agent.run("input"))
        return results
    native = tool()
    unbound = native_model_client([])
    asyncio.run(runs(Agent(client=unbound, tools=[native]), unbound))
    expected = (len(attempts), native.invocation_count, native.invocation_exception_count)
    assert expected == (
        (limit + int(success_between and limit > 1),) * 2 + (limit,)
        if kind.startswith("sync") else (4, 4, 0)
    )
    attempts.clear()
    executions.clear()
    original = tool()
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, decisions={"pre_tool_call": {"decision": "allow"},
                             "post_tool_call": {"decision": "allow"}},
        document=contract(points=("pre_tool_call", "post_tool_call")), audit=spool,
    )
    client = native_model_client([])
    client.function_invocation_configuration["include_detailed_errors"] = True
    agent = runtime().create_governed_agent(p, client=client, tools=[original])
    results = asyncio.run(runs(agent, client))
    assert len(attempts) == expected[0], "governance must not replenish native exception budgets"
    assert all(t is executions[0] for t in executions)
    assert (executions[0].invocation_count, executions[0].invocation_exception_count) == expected[1:]
    assert original.invocation_count == original.invocation_exception_count == 0
    assert "PRIVATE-APPLICATION-FAILURE" not in "".join(r.to_json() for r in results)
    assert "PRIVATE-APPLICATION-FAILURE" not in json.dumps(client.requests)
    assert "PRIVATE-APPLICATION-FAILURE" not in "".join(f.read_text() for f in spool.directory.glob("*.json"))
    asyncio.run(client.client.close())
    asyncio.run(unbound.client.close())


@pytest.mark.parametrize("repeat", range(3))
@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("fails", [True, False])
def test_native_queued_sync_exception_budget_at_worker_boundary(tmp_path, repeat, limit, workers, fails):
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial
    from threading import Event, Lock
    from agent_framework import Agent, FunctionInvocationContext, FunctionTool

    async def exercise(governed):
        loop = asyncio.get_running_loop()
        queued, started = asyncio.Event(), asyncio.Event()
        release, lock = Event(), Lock()
        effects, executions, timeouts, submissions = [], [], [], []

        class QueuedExecutor(ThreadPoolExecutor):
            def submit(self, fn, /, *args, **kwargs):
                future = super().submit(fn, *args, **kwargs)
                # Observe to_thread application jobs, not native ACS evaluations.
                if isinstance(fn, partial) and fn.args and getattr(fn.args[0], "__module__", "") in {
                    "agent_framework._tools",
                    "skills.threadlight-govern.references.runtime.maf_agent_hooks_acs",
                }:
                    submissions.append(future)
                    if len(submissions) == 3:
                        queued.set()
                return future

        loop.set_default_executor(QueuedExecutor(max_workers=workers))

        def act(amount: int, ctx: FunctionInvocationContext):
            with lock:
                effects.append(amount)
                executions.append(ctx.function)
                if len(effects) == workers:
                    loop.call_soon_threadsafe(started.set)
            if not release.wait(30):
                timeouts.append(amount)
            if fails:
                raise ValueError("PRIVATE-QUEUED-APPLICATION-FAILURE")
            return "success"

        original = FunctionTool(name="act", func=act, max_invocation_exceptions=limit)
        client = native_model_client([])
        client.function_invocation_configuration["include_detailed_errors"] = True
        spool = None
        if governed:
            spool = runtime().DurableSpool(tmp_path / "spool")
            p, _, _ = provider(
                tmp_path, decisions={point: {"decision": "allow"}
                                     for point in ("pre_tool_call", "post_tool_call")},
                document=contract(points=("pre_tool_call", "post_tool_call")), audit=spool,
            )
            agent = runtime().create_governed_agent(p, client=client, tools=[original])
        else:
            agent = Agent(client=client, tools=[original])
        responses = tool_responses(args={"amount": 0})
        for amount in (1, 2):
            extra = deepcopy(responses[0].messages[0].contents[0])
            extra.call_id = f"call-{amount + 1}"
            extra.arguments = json.dumps({"amount": amount})
            responses[0].messages[0].contents.append(extra)
        client.responses.extend(responses)
        task = asyncio.create_task(agent.run("parallel"))
        try:
            await asyncio.wait_for(queued.wait(), 20)
            await asyncio.wait_for(started.wait(), 20)
        finally:
            release.set()
            first = await asyncio.wait_for(task, 30)
        assert not timeouts, "application release must be event-driven, not a timeout"
        assert len(submissions) == 3
        initial_effects = sorted(effects)
        owned = executions[0]
        assert all(t is owned for t in executions)
        initial_counts = (owned.invocation_count, owned.invocation_exception_count)
        # The same execution copy must retain the exhausted budget on later runs.
        client.responses.extend(tool_responses(args={"amount": 3}))
        second = await agent.run("later")
        observations = (
            initial_effects, initial_counts, sorted(effects),
            (owned.invocation_count, owned.invocation_exception_count),
        )
        if governed:
            records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
            assert records
            if fails:
                assert any(r.get("interception_point") == "post_tool_call" for r in records)
            wire = first.to_json() + second.to_json() + json.dumps(client.requests) + json.dumps(records)
            assert "PRIVATE-QUEUED-APPLICATION-FAILURE" not in wire
            results = [c for response in (first, second) for m in response.messages
                       for c in m.contents if c.type == "function_result"]
            assert len(results) == 4
            assert all(c.exception == ("threadlight:tool_unavailable" if fails else None)
                       for c in results)
            assert original.invocation_count == original.invocation_exception_count == 0
            # The caller-owned tool still has its full native budget when unbound.
            client.responses.extend(tool_responses(args={"amount": 4}))
            await Agent(client=client, tools=[original]).run("unbound")
            assert effects[-1] == 4
            assert (original.invocation_count, original.invocation_exception_count) == (1, int(fails))
        await client.client.close()
        return observations

    native = asyncio.run(exercise(False))
    expected = min(limit, 3) if fails and workers == 1 else 3
    assert native == (
        list(range(expected)), (expected, expected if fails else 0),
        list(range(expected if fails else 4)),
        (expected if fails else 4, expected if fails else 0),
    )
    governed = asyncio.run(exercise(True))
    assert governed[0] == native[0], "queued workers must not spend an exhausted native exception budget"
    assert governed == native, "native concurrency, effects and lifetime counters must match exactly"


@pytest.mark.parametrize("denial", ["policy", "approval"])
@pytest.mark.parametrize("limit", [1, 2])
def test_native_sync_exception_budgets_are_provider_owned_and_exclude_denials(tmp_path, denial, limit):
    from agent_framework import FunctionInvocationContext, FunctionTool
    executions = []
    def act(blocked: bool, ctx: FunctionInvocationContext):
        executions.append(ctx.function)
        raise RuntimeError("PRIVATE-SYNC-FAILURE")
    original = FunctionTool(name="act", func=act, max_invocation_exceptions=limit)
    service = ApprovalService("reject")
    agents = []
    for name in ("first", "second"):
        spool = runtime().DurableSpool(tmp_path / name / "spool")
        p, _, _ = provider(
            tmp_path / name, document=contract(requires=("approval",) if denial == "approval" else ()),
            decisions={"pre_tool_call": (
                '{"decision": "deny"} if {input.policy_target.value.blocked == true} '
                'else := {"decision": "allow"}'
            )}, audit=spool, approval_resolver=service, principal="host:user", tenant="host:tenant",
            allowed_approval_roles=("test-reviewer",),
        )
        client = native_model_client([])
        agents.append((runtime().create_governed_agent(p, client=client, tools=[original]), client, spool))
    async def run(agent, client, blocked):
        client.responses.extend(tool_responses(args={"blocked": blocked}))
        return await agent.run("input")
    first, client, _ = agents[0]
    denied = [asyncio.run(run(first, client, denial == "policy")) for _ in range(2)]
    assert executions == [] and original.invocation_exception_count == 0
    assert all("PRIVATE-SYNC-FAILURE" not in r.to_json() for r in denied)
    service.case = "approve"
    all_results = []
    for agent, client, spool in agents:
        start = len(executions)
        all_results.extend(asyncio.run(run(agent, client, False)) for _ in range(limit + 1))
        owned = executions[start:]
        assert len(owned) == limit and all(t is owned[0] for t in owned)
        assert owned[0].invocation_count == owned[0].invocation_exception_count == limit
        assert "PRIVATE-SYNC-FAILURE" not in json.dumps(client.requests)
        assert "PRIVATE-SYNC-FAILURE" not in "".join(f.read_text() for f in spool.directory.glob("*.json"))
        asyncio.run(client.client.close())
    assert executions[0] is not executions[limit]
    assert original.invocation_count == original.invocation_exception_count == 0
    assert all("PRIVATE-SYNC-FAILURE" not in r.to_json() for r in all_results)


@pytest.mark.parametrize("name", ["act", "read"])
def test_native_sync_scheduler_failure_does_not_consume_budget(tmp_path, name):
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial
    from agent_framework import Agent, FunctionInvocationContext, FunctionTool

    async def exercise(governed):
        effects, executions = [], []

        class RejectingExecutor(ThreadPoolExecutor):
            reject = False
            rejected = 0

            def submit(self, fn, /, *args, **kwargs):
                if self.reject and isinstance(fn, partial):
                    self.rejected += 1
                    raise RuntimeError("PRIVATE-SCHEDULER-FAILURE")
                return super().submit(fn, *args, **kwargs)

        executor = RejectingExecutor(max_workers=1)
        asyncio.get_running_loop().set_default_executor(executor)
        def act(ctx: FunctionInvocationContext):
            effects.append("effect")
            executions.append(ctx.function)
            return "success"
        original = FunctionTool(name=name, func=act, max_invocations=2, max_invocation_exceptions=1)
        client = native_model_client([])
        client.function_invocation_configuration["include_detailed_errors"] = True
        spool = None
        if governed:
            spool = runtime().DurableSpool(tmp_path / "spool")
            p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}}, audit=spool)
            agent = runtime().create_governed_agent(p, client=client, tools=[original])
        else:
            agent = Agent(client=client, tools=[original])
        async def run():
            client.responses.extend(tool_responses(name=name))
            return await agent.run("input")
        await run()
        executor.reject = True
        denied = await run()
        assert executor.rejected == 1 and effects == ["effect"]
        after_rejection = (executions[0].invocation_count, executions[0].invocation_exception_count)
        executor.reject = False
        resumed = await run()
        final = (len(effects), executions[0].invocation_count, executions[0].invocation_exception_count)
        if governed and name == "act":
            wire = denied.to_json() + resumed.to_json() + json.dumps(client.requests)
            wire += "".join(f.read_text() for f in spool.directory.glob("*.json"))
            assert "PRIVATE-SCHEDULER-FAILURE" not in wire
            assert "threadlight:tool_unavailable" in denied.to_json()
            assert original.invocation_count == original.invocation_exception_count == 0
        else:
            assert executions[0] is original
            assert "PRIVATE-SCHEDULER-FAILURE" in denied.to_json()
        await client.client.close()
        return after_rejection, final

    native = asyncio.run(exercise(False))
    assert native == ((1, 0), (2, 2, 0))
    assert asyncio.run(exercise(True)) == native


@pytest.mark.parametrize("expiry", ["policy", "approval"])
def test_sync_effect_expiry_is_not_an_application_exception(tmp_path, monkeypatch, expiry):
    from agent_framework import FunctionInvocationContext, FunctionTool
    clock = controlled_clock(monkeypatch)
    service = ApprovalService("approve")
    p, authority, _ = provider(
        tmp_path, decisions={"pre_tool_call": {"decision": "allow"}},
        document=contract(requires=("approval",) if expiry == "approval" else ()),
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    executions = []
    def act(ctx: FunctionInvocationContext):
        executions.append(ctx.function)
        return "done"
    original = FunctionTool(name="act", func=act, max_invocation_exceptions=1)
    client = native_model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, tools=[original])
    asyncio.run(agent.run("first"))
    to_thread = asyncio.to_thread
    async def scheduled(call, *args, **kwargs):
        def at_worker():
            clock.current = authority.expires_at if expiry == "policy" else service.requests[-1].expires_at
            return call(*args, **kwargs)
        return await to_thread(at_worker)
    monkeypatch.setattr(asyncio, "to_thread", scheduled)
    client.responses.extend(tool_responses())
    asyncio.run(agent.run("expired"))
    assert len(executions) == 1
    assert executions[0].invocation_count == 1
    assert executions[0].invocation_exception_count == 0
    assert original.invocation_count == original.invocation_exception_count == 0
    asyncio.run(client.client.close())


def test_native_nested_providers_keep_independent_lifetime_budgets(tmp_path):
    from contextvars import ContextVar
    from agent_framework import FunctionInvocationContext, FunctionTool
    inside = ContextVar("inside_child", default=False)
    effects = []
    async def act(ctx: FunctionInvocationContext):
        effects.append(ctx.function)
        if not inside.get():
            token = inside.set(True)
            try:
                child_client.responses.extend(tool_responses())
                await child.run("nested")
            finally:
                inside.reset(token)
        return "done"
    original = FunctionTool(name="act", func=act, max_invocations=1)
    child_provider, _, _ = provider(tmp_path / "child", decisions={"pre_tool_call": {"decision": "allow"}})
    parent_provider, _, _ = provider(tmp_path / "parent", decisions={"pre_tool_call": {"decision": "allow"}})
    child_client, parent_client = native_model_client([]), native_model_client([])
    child = runtime().create_governed_agent(child_provider, client=child_client, tools=[original])
    parent = runtime().create_governed_agent(parent_provider, client=parent_client, tools=[original])
    async def run():
        parent_client.responses.extend(tool_responses())
        await parent.run("first")
        assert len(effects) == 2 and effects[0] is not effects[1]
        parent_client.responses.extend(tool_responses())
        parent_result = await parent.run("second")
        token = inside.set(True)
        try:
            child_client.responses.extend(tool_responses())
            child_result = await child.run("second")
        finally:
            inside.reset(token)
        return parent_result, child_result
    results = asyncio.run(run())
    assert len(effects) == 2 and all(t.invocation_count == 1 for t in effects)
    assert original.invocation_count == 0
    assert all("threadlight:tool_unavailable" in r.to_json() for r in results)
    asyncio.run(child_client.client.close())
    asyncio.run(parent_client.client.close())


def test_guard_cache_uses_original_identity_without_retaining_discarded_tools(tmp_path):
    import gc
    import weakref
    from agent_framework import FunctionTool
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    async def act():
        return "done"
    first = FunctionTool(name="act", func=act, max_invocations=1)
    second = FunctionTool(name="act", func=act, max_invocations=2)
    guarded = adapter._guard_tool(first, p)
    assert adapter._guard_tool(first, p) is guarded
    assert adapter._guard_tool(guarded, p) is guarded
    assert adapter._guard_tool(second, p) is not guarded
    reference = weakref.ref(first)
    key = id(first)
    del first
    gc.collect()
    assert reference() is None, "discarded per-call tools must not accumulate in a host's cache"
    assert key not in p._guarded_tools


@pytest.mark.parametrize("case,target,expected,validations_expected", [
    ("constraint", -1, [], [9]),
    ("valid", 5, [5], [9, 5]),
    ("coercion", "5", [], [9, 5]),
    ("nonidempotent", 5, [], [9, 5]),
    ("same-canonical", 10, [10], [9]),
    ("mutation", 5, [], [9]),
])
def test_native_transformed_target_preserves_original_constraints(
    tmp_path, case, target, expected, validations_expected,
):
    from agent_framework import FunctionMiddleware, FunctionTool
    from pydantic import BaseModel, Field, field_validator
    validations, effects = [], []
    class Arguments(BaseModel):
        amount: int = Field(gt=0)
        @field_validator("amount")
        @classmethod
        def normalize(cls, value):
            validations.append(value)
            return value + 1 if case in {"nonidempotent", "same-canonical"} else value
    class Mutate(FunctionMiddleware):
        async def process(self, context, call_next):
            context.arguments["amount"] += 1
            await asyncio.sleep(0)
            await call_next()
    async def act(amount: int):
        effects.append(amount)
        return "done"
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(requires=("durable-audit",)), audit=spool,
        decisions={"pre_tool_call": {
            "decision": "transform", "transform": {"path": "$policy_target", "value": {"amount": target}},
        }},
    )
    tool = FunctionTool(name="act", func=act, input_model=Arguments)
    original_schema = deepcopy(tool.parameters())
    client = native_model_client(tool_responses(args={"amount": 9}))
    agent = runtime().create_governed_agent(
        p, client=client, tools=[tool], middleware=[Mutate()] if case == "mutation" else [],
    )
    result = asyncio.run(agent.run("input"))
    assert effects == expected, "unvalidated or differently normalized ACS target reached the effect"
    assert validations == validations_expected
    assert tool.input_model is Arguments and tool.parameters() == original_schema
    if not expected:
        reason = "threadlight:arguments_changed" if case == "mutation" else "threadlight:invalid_transform"
        assert reason in result.to_json()
        records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
        assert any(r["decision"] == "deny" and r.get("reason_code") == reason for r in records)
    asyncio.run(client.client.close())


@pytest.mark.parametrize("case", ["validator", "structured", "runtime-validator", "serializer", "valid"])
@pytest.mark.parametrize("detailed", [False, True])
def test_native_prehook_validation_errors_are_private(tmp_path, case, detailed):
    from agent_framework import Agent, FunctionTool
    from pydantic import BaseModel, Field, field_serializer, field_validator
    effects = []
    class Arguments(BaseModel):
        amount: int = Field(gt=0)
        @field_validator("amount")
        @classmethod
        def validate_amount(cls, value):
            if value == 9:
                raise ValueError("PRIVATE-VALIDATOR-DIAGNOSTIC")
            if value == 8:
                raise RuntimeError("PRIVATE-VALIDATOR-DIAGNOSTIC")
            return value
        @field_serializer("amount")
        def serialize_amount(self, value):
            if value == 7:
                raise RuntimeError("PRIVATE-SERIALIZER-DIAGNOSTIC")
            return value
    async def act(amount: int):
        effects.append(amount)
        return "done"
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(points=("pre_tool_call", "post_tool_call")),
        decisions={point: {"decision": "allow"} for point in ("pre_tool_call", "post_tool_call")},
        audit=spool,
    )
    tool = FunctionTool(name="act", func=act, input_model=Arguments)
    schema = deepcopy(tool.parameters())
    amount = {"validator": 9, "structured": {"malformed": True},
              "runtime-validator": 8, "serializer": 7, "valid": 5}[case]
    client = native_model_client(tool_responses(args={"amount": amount}))
    client.function_invocation_configuration["include_detailed_errors"] = detailed
    agent = runtime().create_governed_agent(p, client=client, tools=[tool])
    result = asyncio.run(agent.run("input"))
    assert len(client.requests) == 2, "real native error continuation must still call the model"
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    wire = str(client.requests) + result.to_json() + json.dumps(records)
    assert "PRIVATE" not in wire
    assert effects == ([5] if case == "valid" else [])
    results = [c for m in result.messages for c in m.contents if c.type == "function_result"]
    assert len(results) == 1
    if case != "valid":
        assert results[0].exception == "threadlight:invalid_arguments"
        assert "input_value" not in wire and "input_type" not in wire
        assert not any(r["decision"] in {"allow", "transform"} for r in records)
    else:
        assert results[0].exception is None
    assert tool.input_model is Arguments and tool.parameters() == schema
    # The shared object still exposes the original native validation behavior.
    original_client = native_model_client(tool_responses(args={"amount": 9}))
    original = Agent(client=original_client, tools=[tool])
    assert "PRIVATE-VALIDATOR-DIAGNOSTIC" in asyncio.run(original.run("input")).to_json()
    asyncio.run(client.client.close())
    asyncio.run(original_client.client.close())


@pytest.mark.parametrize("point", ["startup", "input", "pre_model_call"])
@pytest.mark.parametrize("borrowed_sdk", [False, True])
def test_unsupported_async_client_rejected_before_lifecycle_effects(tmp_path, monkeypatch, point, borrowed_sdk):
    from agent_framework import AgentMiddleware
    clock = controlled_clock(monkeypatch)
    native_point = "agent_startup" if point == "startup" else point
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=(point,)),
        decisions={native_point: {"decision": "allow"}},
    )
    effects, transports = [], []
    class Effect(AgentMiddleware):
        async def process(self, context, call_next):
            effects.append(True)
            await call_next()
    client = model_client(tool_responses())
    inner = client._inner_get_response
    async def delayed(**kwargs):
        await asyncio.sleep(0)
        clock.current = authority.expires_at
        transports.append(True)
        return await inner(**kwargs)
    client._inner_get_response = delayed
    client.terminal_guard_installed = True  # A self-asserted claim is not coverage.
    if borrowed_sdk:
        client.client = native_model_client([]).client
    with pytest.raises(ValueError, match="supported.*model.*transport"):
        agent = runtime().create_governed_agent(p, client=client, middleware=[Effect()])
        asyncio.run(agent.run("input"))
    assert effects == transports == client.requests == []
    assert not p._claimed
    assert p.health()["bindings"][f"lifecycle:{native_point}"]["reason"] == "threadlight:unsupported_model_transport"
    if borrowed_sdk:
        asyncio.run(client.client.close())


@pytest.mark.parametrize("client_kind", ["openai", "chat-completions", "foundry"])
@pytest.mark.parametrize("expire", [False, True])
@pytest.mark.parametrize("authorization", ["policy", "approval"])
def test_supported_native_lifecycle_transport_remains_workable(tmp_path, monkeypatch, client_kind, expire, authorization):
    from agent_framework import ChatResponse, Message
    clock = controlled_clock(monkeypatch)
    service = ApprovalService("approve")
    p, authority, _ = provider(
        tmp_path, document=contract(
            points=(), lifecycle=("pre_model_call",),
            requires=("approval",) if authorization == "approval" else (),
        ),
        decisions={"pre_model_call": {"decision": "allow"}},
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    hooks = []
    async def request_hook(request):
        await asyncio.sleep(0)
        hooks.append(True)
        if expire:
            clock.current = authority.expires_at if authorization == "policy" else service.requests[-1].expires_at
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])],
        request_hook=request_hook, foundry=client_kind == "foundry",
        chat_completions=client_kind == "chat-completions",
    )
    original_sdk = client.client
    original_http = original_sdk._client
    agent = runtime().create_governed_agent(p, client=client)
    if expire:
        with pytest.raises(Exception, match=f"threadlight:{authorization}_unavailable"):
            asyncio.run(agent.run("input"))
    else:
        assert asyncio.run(agent.run("input")).text == "finished"
    assert hooks == [True]
    assert len(client.requests) == (0 if expire else 1)
    assert client.client is original_sdk and client.client._client is original_http
    asyncio.run(client.client.close())


def test_native_model_capability_is_rechecked_before_runtime_middleware(tmp_path):
    from agent_framework import AgentMiddleware
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)),
        decisions={"pre_model_call": {"decision": "allow"}},
    )
    effects = []
    class Effect(AgentMiddleware):
        async def process(self, context, call_next):
            effects.append(True)
            await call_next()
    client = native_model_client(tool_responses())
    agent = runtime().create_governed_agent(p, client=client, middleware=[Effect()])
    agent.client.client = None
    with pytest.raises(ValueError, match="supported.*model.*transport"):
        asyncio.run(agent.run("input"))
    assert effects == client.requests == []
    asyncio.run(client.client.close())


@pytest.mark.parametrize("governed", [False, True])
def test_native_prehook_validation_preserves_framework_termination(tmp_path, governed):
    from agent_framework import Agent, FunctionTool, MiddlewareTermination
    from pydantic import BaseModel, field_validator
    effects = []
    class Arguments(BaseModel):
        amount: int
        @field_validator("amount")
        @classmethod
        def stop(cls, value):
            raise MiddlewareTermination(result="stopped-native")
    async def act(amount):
        effects.append(amount)
    client = native_model_client(tool_responses(args={"amount": 9}))
    tool = FunctionTool(name="act", func=act, input_model=Arguments)
    if governed:
        p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
        agent = runtime().create_governed_agent(p, client=client, tools=[tool])
    else:
        agent = Agent(client=client, tools=[tool])
    result = asyncio.run(agent.run("input"))
    assert len(client.requests) == 1 and effects == []
    results = [c for m in result.messages for c in m.contents if c.type == "function_result"]
    assert len(results) == 1 and results[0].result == "stopped-native"
    assert results[0].exception is None
    asyncio.run(client.client.close())


def test_host_owned_validation_entrypoints_drop_exception_context(tmp_path):
    from agent_framework import ChatMiddleware, FunctionTool
    from pydantic import BaseModel, field_validator
    exposed = []
    class Arguments(BaseModel):
        amount: int
        @field_validator("amount")
        @classmethod
        def reject(cls, value):
            raise ValueError("PRIVATE-VALIDATOR-DIAGNOSTIC")
    class Capture(ChatMiddleware):
        async def process(self, context, call_next):
            await call_next()
            exposed.append(context.options["tools"][0])
    p, _, _ = provider(tmp_path, decisions={"pre_tool_call": {"decision": "allow"}})
    original = FunctionTool(name="act", func=lambda amount: None, input_model=Arguments)
    client = native_model_client(tool_responses(args={"amount": 9}))
    agent = runtime().create_governed_agent(p, client=client, tools=[original], middleware=[Capture()])
    asyncio.run(agent.run("input"))
    model = exposed[0].input_model
    assert model is not Arguments and issubclass(model, Arguments)
    assert model.model_json_schema() == Arguments.model_json_schema()
    for method, value in (
        ("model_validate", {"amount": 9}),
        ("model_validate_json", '{"amount": 9}'),
        ("model_validate_strings", {"amount": "9"}),
    ):
        with pytest.raises(TypeError, match="^threadlight:invalid_arguments$") as error:
            getattr(model, method)(value)
        assert error.value.__context__ is None and error.value.__cause__ is None
    assert original.input_model is Arguments
    asyncio.run(client.client.close())


@pytest.mark.parametrize("case", [
    "field-serializer", "model-serializer", "root-validator", "non-object",
    "returned-model", "returned-object", "valid-serializer", "schema-only",
])
@pytest.mark.parametrize("detailed", [False, True])
def test_native_entire_argument_pipeline_is_private(tmp_path, case, detailed):
    from typing import Literal
    from agent_framework import Agent, FunctionTool
    from pydantic import BaseModel, field_serializer, model_serializer, model_validator
    validations, serializations, effects = [], [], []
    class Arguments(BaseModel):
        state: Literal["ok", "ready"]

        @model_validator(mode="after")
        def validate_state(self):
            validations.append(self.state)
            if case == "root-validator":
                self.state = "PRIVATE"
            if case == "returned-model":
                return Arguments.model_construct(state="PRIVATE")
            if case == "returned-object":
                class Result:
                    def model_dump(self, **kwargs):
                        return {"state": "PRIVATE"}
                return Result()
            return self

        @field_serializer("state")
        def serialize_state(self, value):
            serializations.append(value)
            if case == "field-serializer":
                return "PRIVATE"
            return "ready" if case == "valid-serializer" else value

        @model_serializer(mode="wrap")
        def serialize_model(self, handler):
            value = handler(self)
            if case == "model-serializer":
                return {"state": "PRIVATE"}
            return "PRIVATE" if case == "non-object" else value

    async def act(state):
        effects.append(state)
        return "done"
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, audit=spool,
        decisions={"pre_tool_call": {"decision": "allow"}},
    )
    schema = {"type": "object", "properties": {"state": {"enum": ["ready"]}}}
    tool = FunctionTool(name="act", func=act, input_model=schema if case == "schema-only" else Arguments)
    original_schema = deepcopy(tool.parameters())
    client = native_model_client(tool_responses(args={"state": "ok"}))
    client.function_invocation_configuration["include_detailed_errors"] = detailed
    agent = runtime().create_governed_agent(p, client=client, tools=[tool])
    result = asyncio.run(agent.run("input"))
    records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
    wire = str(client.requests) + result.to_json() + json.dumps(records)
    assert "PRIVATE" not in wire
    assert len(client.requests) == 2
    results = [c for m in result.messages for c in m.contents if c.type == "function_result"]
    assert len(results) == 1
    if case == "valid-serializer":
        assert effects == ["ready"] and results[0].exception is None
    else:
        assert effects == []
        assert results[0].exception == "threadlight:invalid_arguments"
        assert not any(r["decision"] in {"allow", "transform"} for r in records)
    assert validations == ([] if case == "schema-only" else ["ok"])
    assert len(serializations) == (0 if case in {"schema-only", "returned-object"} else 1)
    assert tool.parameters() == original_schema
    assert tool.input_model is (None if case == "schema-only" else Arguments)
    # Unbound tools retain the original SDK validation/serialization semantics.
    unbound = native_model_client(tool_responses(args={"state": "ok"}))
    try:
        original_result = asyncio.run(Agent(client=unbound, tools=[tool]).run("input"))
        if case not in {"valid-serializer", "schema-only"}:
            assert "PRIVATE" in original_result.to_json()
    except ValueError:
        assert case == "non-object"
    asyncio.run(client.client.close())
    asyncio.run(unbound.client.close())


@pytest.mark.parametrize("strategy_name", ["truncation", "window"])
@pytest.mark.parametrize("source", ["client", "agent", "run", "middleware"])
def test_selected_model_scope_rejects_native_compaction(tmp_path, strategy_name, source):
    from agent_framework import (
        Agent, ChatMiddleware, ChatResponse, Message, SlidingWindowStrategy, TruncationStrategy,
    )
    def strategy():
        return (TruncationStrategy(max_n=1, compact_to=1) if strategy_name == "truncation"
                else SlidingWindowStrategy(keep_last_groups=1))
    def messages():
        return [Message("user", ["first"]), Message("assistant", ["second"]), Message("user", ["third"])]
    def responses():
        return [ChatResponse(messages=[Message("assistant", ["finished"])])]
    # Reproduce the actual built-in mutation, not a patched SDK or pretend strategy.
    unbound = native_model_client(responses())
    asyncio.run(Agent(client=unbound, compaction_strategy=strategy()).run(messages()))
    assert len(unbound.requests[0][0]) == 1
    service = ApprovalService("approve")
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",), requires=("approval",)),
        decisions={"pre_model_call": (
            '{"decision": "allow"} if {count(input.snapshot.messages) == 3} '
            'else := {"decision": "deny"}'
        )}, audit=spool, approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    from agent_framework._agent_hooks import _ModelRequestCodec
    final = _ModelRequestCodec.to_wire(messages()[-1:])
    decision = asyncio.run(p._engine.evaluate_intervention_point(
        "pre_model_call", {"messages": final}, mode="enforce",
    ))
    assert decision.verdict.decision.value == "deny", "real ACS denies the compacted target"
    class Mutate(ChatMiddleware):
        async def process(self, context, call_next):
            context.kwargs["compaction_strategy"] = strategy()
            await call_next()
    client = native_model_client(responses())
    if source == "client":
        client.compaction_strategy = strategy()
    with pytest.raises((ValueError, RuntimeError), match="threadlight:unsupported_model_compaction"):
        agent = runtime().create_governed_agent(
            p, client=client, middleware=[Mutate()] if source == "middleware" else [],
            **({"compaction_strategy": strategy()} if source == "agent" else {}),
        )
        asyncio.run(agent.run(messages(), **({"compaction_strategy": strategy()} if source == "run" else {})))
    assert client.requests == []
    assert len(service.requests) <= 1, "no reapproval loop"
    if source == "middleware":
        records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
        assert any(r.get("reason_code") == "threadlight:unsupported_model_compaction" for r in records)
    asyncio.run(client.client.close())
    asyncio.run(unbound.client.close())


@pytest.mark.parametrize("mutation", ["none", "messages", "options", "tools", "http", "http-stream"])
@pytest.mark.parametrize("chat_completions", [False, True])
def test_model_approval_is_bound_to_final_target(tmp_path, monkeypatch, mutation, chat_completions):
    from agent_framework import ChatMiddleware, ChatResponse, FunctionTool, Message
    service = ApprovalService("approve")
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    observed = []
    intercept = adapter.AcsInterceptor.intercept
    async def capture(self, context):
        if context["interception_point"] == "pre_model_call":
            observed.append(deepcopy(context))
        return await intercept(self, context)
    monkeypatch.setattr(adapter.AcsInterceptor, "intercept", capture)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",), requires=("approval",)),
        decisions={"pre_model_call": {"decision": "allow"}}, audit=spool,
        approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    class Mutate(ChatMiddleware):
        async def process(self, context, call_next):
            if mutation == "messages":
                context.messages[:] = [Message("user", ["changed"])]
            elif mutation == "options":
                context.options["instructions"] = "changed"
            elif mutation == "tools":
                context.options["tools"].append(FunctionTool(name="other", func=lambda: None))
            await call_next()
    async def request_hook(request):
        if mutation in {"http", "http-stream"}:
            body = json.loads(request.content)
            body["messages" if chat_completions else "input"] = [{"role": "user", "content": "changed"}]
            changed = json.dumps(body).encode()
            if mutation == "http":
                request._content = changed
            else:
                import httpx
                request.stream = httpx.ByteStream(changed)
        await asyncio.sleep(0)
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])],
        request_hook=request_hook, chat_completions=chat_completions,
    )
    agent = runtime().create_governed_agent(
        p, client=client, middleware=[Mutate()], instructions="host instructions",
        tools=[FunctionTool(name="read", func=lambda: None)],
    )
    if mutation == "none":
        assert asyncio.run(agent.run("input")).text == "finished"
        assert len(client.requests) == 1
    else:
        with pytest.raises(Exception, match="threadlight:model_target_changed"):
            asyncio.run(agent.run("input"))
        assert client.requests == []
        records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
        assert any(r.get("reason_code") == "threadlight:model_target_changed" for r in records)
    assert len(service.requests) == 1
    assert service.requests[0].action_hash == adapter.action_hash(p, observed[0])
    assert all("input" not in f.read_text() and "changed" not in f.read_text().replace(
        "threadlight:model_target_changed", "") for f in spool.directory.glob("*.json"))
    asyncio.run(client.client.close())


@pytest.mark.parametrize("source", ["preparer", "options", "extra-body"])
def test_native_posthook_message_replacements_are_unsupported(tmp_path, source):
    from agent_framework import AgentMiddleware, ChatResponse, Message
    effects = []
    class Effect(AgentMiddleware):
        async def process(self, context, call_next):
            effects.append(True)
            await call_next()
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)),
        decisions={"pre_model_call": {"decision": "allow"}},
    )
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])], chat_completions=True,
    )
    options = {}
    if source == "preparer":
        client.message_preparer = lambda message, prepared: [{"role": "user", "content": "changed"}]
    elif source == "options":
        options["messages"] = [{"role": "user", "content": "changed"}]
    else:
        options["extra_body"] = {"messages": [{"role": "user", "content": "changed"}]}
    reason = "preparer" if source == "preparer" else "override"
    with pytest.raises(ValueError, match=f"threadlight:unsupported_model_{reason}"):
        agent = runtime().create_governed_agent(
            p, client=client, default_options=options, middleware=[Effect()],
        )
        asyncio.run(agent.run("input"))
    assert effects == client.requests == []
    asyncio.run(client.client.close())


@pytest.mark.parametrize("chat_completions", [False, True])
@pytest.mark.parametrize("mutation", [
    "replacement", "stream", "json", "instructions", "tools", "headers", "equivalent", "expiry",
])
def test_native_http_auth_cannot_replace_authorized_body(tmp_path, monkeypatch, chat_completions, mutation):
    import httpx
    from agent_framework import ChatResponse, Message
    from agent_framework._agent_hooks import _ModelRequestCodec
    adapter = importlib.import_module("skills.threadlight-govern.references.runtime.maf_agent_hooks_acs")
    clock = controlled_clock(monkeypatch)
    service = ApprovalService("approve")
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",), requires=("approval",)),
        decisions={"pre_model_call": (
            '{"decision": "allow"} if {count(input.snapshot.messages) == 3} '
            'else := {"decision": "deny"}'
        )}, audit=spool, approval_resolver=service, principal="host:user", tenant="host:tenant",
        allowed_approval_roles=("test-reviewer",),
    )
    messages = [Message("user", ["first"]), Message("assistant", ["second"]), Message("user", ["third"])]
    final = _ModelRequestCodec.to_wire(messages[-1:])
    decision = asyncio.run(p._engine.evaluate_intervention_point(
        "pre_model_call", {"messages": final}, mode="enforce",
    ))
    assert decision.verdict.decision.value == "deny"
    original_bodies, hook_bodies = [], []
    class Auth(httpx.Auth):
        async def async_auth_flow(self, request):
            body = json.loads(request.content)
            original_bodies.append(deepcopy(body))
            await asyncio.sleep(0)
            request.headers["Authorization"] = "Bearer refreshed-local-token"
            key = "messages" if chat_completions else "input"
            if mutation in {"replacement", "stream", "json", "instructions", "tools"}:
                if mutation == "instructions":
                    body["instructions"] = "PRIVATE-CHANGED-INSTRUCTIONS"
                elif mutation == "tools":
                    body["tools"] = [{"type": "function", "name": "PRIVATE-CHANGED-TOOL"}]
                else:
                    body[key] = body[key][-1:]
                changed = json.dumps(body).encode()
                if mutation == "replacement":
                    request = httpx.Request(request.method, request.url, json=body, headers=request.headers)
                elif mutation == "stream":
                    request.stream = httpx.ByteStream(changed)
                else:
                    request._content = changed
                    request.stream = httpx.ByteStream(changed)
            elif mutation == "equivalent":
                request = httpx.Request(
                    request.method, request.url, headers=request.headers,
                    content=json.dumps(body, sort_keys=True, indent=2).encode(),
                )
            elif mutation == "expiry":
                clock.current = authority.expires_at
            yield request
    async def hook(request):
        hook_bodies.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer refreshed-local-token"
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])],
        auth=Auth(), request_hook=hook, chat_completions=chat_completions,
    )
    original_sdk, original_http = client.client, client.client._client
    agent = runtime().create_governed_agent(p, client=client)
    if mutation in {"headers", "equivalent"}:
        assert asyncio.run(agent.run(messages)).text == "finished"
        assert len(client.requests) == 1 and len(client.requests[0][0]) == 3
    else:
        reason = "threadlight:policy_unavailable" if mutation == "expiry" else "threadlight:model_target_changed"
        with pytest.raises(adapter.GovernedToolUnavailable, match=reason):
            asyncio.run(agent.run(messages))
        assert client.requests == []
        records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
        assert any(r.get("reason_code") == reason and r["decision"] == "deny" for r in records)
        assert any(r["decision"] == "allow" for r in records)
    assert len(service.requests) == len(original_bodies) == len(hook_bodies) == 1
    assert len(original_bodies[0]["messages" if chat_completions else "input"]) == 3
    assert client.client is original_sdk and client.client._client is original_http
    asyncio.run(client.client.close())


@pytest.mark.parametrize("chat_completions", [False, True])
@pytest.mark.parametrize("continuation", ["challenge", "retry", "redirect-mount"])
@pytest.mark.parametrize("mutation", ["none", "body", "expiry"])
def test_native_http_continuations_keep_original_authorization(
    tmp_path, monkeypatch, chat_completions, continuation, mutation,
):
    import httpx
    from agent_framework import ChatResponse, Message
    clock = controlled_clock(monkeypatch)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)),
        decisions={"pre_model_call": {"decision": "allow"}}, audit=spool,
    )
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])], chat_completions=chat_completions,
    )
    inner = client.client._client._transport
    wires, hooks = [], []
    async def exchange(request):
        wires.append(json.loads(request.content))
        if len(wires) == 1:
            code = {"challenge": 401, "retry": 503, "redirect-mount": 307}[continuation]
            return httpx.Response(code, headers={
                "location": "https://redirect.local/continued", "retry-after-ms": "1",
            })
        return await inner.handle_async_request(request)
    def update(request):
        if mutation == "expiry":
            clock.current = authority.expires_at
        body = json.loads(request.read())
        if mutation == "body":
            key = "messages" if chat_completions else "input"
            body[key] = body[key][-1:]
        return httpx.Request(
            request.method, request.url, headers=request.headers,
            content=json.dumps(body, sort_keys=True, indent=2).encode(),
        )
    class Auth(httpx.Auth):
        async def async_auth_flow(self, request):
            await asyncio.sleep(0)
            response = yield request
            if response.status_code == 401:
                await asyncio.sleep(0)
                yield update(request)
    async def hook(request):
        hooks.append(True)
        if len(hooks) == 2 and continuation != "challenge" and mutation != "none":
            await asyncio.sleep(0)
            replacement = update(request)
            request._content, request.stream = replacement.content, replacement.stream
    wire = httpx.MockTransport(exchange)
    http = httpx.AsyncClient(
        transport=wire, mounts={"https://redirect.local": wire}, auth=Auth(),
        event_hooks={"request": [hook]}, follow_redirects=True,
    )
    client.client = client.client.with_options(
        http_client=http, max_retries=1 if continuation == "retry" else 0,
    )
    agent = runtime().create_governed_agent(p, client=client)
    messages = [Message("user", ["first"]), Message("assistant", ["second"]), Message("user", ["third"])]
    if mutation == "none":
        assert asyncio.run(agent.run(messages)).text == "finished"
        assert len(wires) == 2
    else:
        reason = "policy_unavailable" if mutation == "expiry" else "model_target_changed"
        with pytest.raises(RuntimeError, match=f"threadlight:{reason}"):
            asyncio.run(agent.run(messages))
        assert len(wires) == 1, "only the first authorized HTTP attempt can reach transport"
        records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
        assert any(r.get("reason_code") == f"threadlight:{reason}" for r in records)
    assert all(len(body["messages" if chat_completions else "input"]) == 3 for body in wires)
    assert len(hooks) == 2
    asyncio.run(http.aclose())


@pytest.mark.parametrize("chat_completions", [False, True])
@pytest.mark.parametrize("expire", [False, True])
def test_native_token_refresh_preserves_transformed_wire(tmp_path, monkeypatch, chat_completions, expire):
    from agent_framework import ChatResponse, Message
    from openai import AsyncAzureOpenAI
    clock = controlled_clock(monkeypatch)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, authority, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)),
        decisions={"pre_model_call": {
            "decision": "transform",
            "transform": {"path": "$policy_target", "value": [{"role": "user", "content": "authorized-transform"}]},
        }}, audit=spool,
    )
    tokens = []
    async def refresh():
        await asyncio.sleep(0)
        tokens.append(True)
        if expire:
            clock.current = authority.expires_at
        return "local-refreshed-token"
    async def hook(request):
        assert request.headers["Authorization"] == "Bearer local-refreshed-token"
        assert "authorized-transform" in request.content.decode()
        assert "PRIVATE-ORIGINAL" not in request.content.decode()
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])],
        request_hook=hook, chat_completions=chat_completions,
    )
    # Native Azure credential preparation, entirely local MockTransport: no Azure service.
    client.client = AsyncAzureOpenAI(
        azure_endpoint="https://local.openai.azure.com", api_version="2025-04-01-preview",
        azure_ad_token_provider=refresh, http_client=client.client._client, max_retries=0,
    )
    agent = runtime().create_governed_agent(p, client=client)
    if expire:
        with pytest.raises(RuntimeError, match="threadlight:policy_unavailable"):
            asyncio.run(agent.run("PRIVATE-ORIGINAL"))
        assert client.requests == []
    else:
        assert asyncio.run(agent.run("PRIVATE-ORIGINAL")).text == "finished"
        assert len(client.requests) == 1
        assert "authorized-transform" in json.dumps(client.requests)
    assert tokens == [True]
    asyncio.run(client.client.close())


@pytest.mark.parametrize("chat_completions", [False, True])
@pytest.mark.parametrize("phase", ["options", "request"])
@pytest.mark.parametrize("mutation", [False, True])
def test_native_sdk_preparation_cannot_rebaseline_body(tmp_path, chat_completions, phase, mutation):
    import httpx
    from agent_framework import ChatResponse, Message
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(
        tmp_path, document=contract(points=(), lifecycle=("pre_model_call",)),
        decisions={"pre_model_call": {"decision": "allow"}}, audit=spool,
    )
    client = native_model_client(
        [ChatResponse(messages=[Message("assistant", ["finished"])])], chat_completions=chat_completions,
    )
    agent = runtime().create_governed_agent(p, client=client)
    # Inject an awaited preparation callback on this host-owned instance; the real
    # SDK request/retry/auth pipeline still executes, and no SDK class is patched.
    sdk = agent.client.client
    prepare = getattr(sdk, f"_prepare_{phase}")
    async def preparation(value):
        result = await prepare(value)
        await asyncio.sleep(0)
        if mutation:
            key = "messages" if chat_completions else "input"
            if phase == "options":
                result.json_data[key] = [{"role": "user", "content": "PRIVATE-CHANGED"}]
            else:
                body = json.loads(value.content)
                body[key] = [{"role": "user", "content": "PRIVATE-CHANGED"}]
                value._content = json.dumps(body).encode()
                value.stream = httpx.ByteStream(value.content)
        elif phase == "options":
            from openai import NotGiven
            result.headers = {
                **({} if isinstance(result.headers, NotGiven) else result.headers), "x-local-token": "refreshed",
            }
        else:
            value.headers["x-local-token"] = "refreshed"
        return result
    setattr(sdk, f"_prepare_{phase}", preparation)
    if mutation:
        with pytest.raises(RuntimeError, match="threadlight:model_target_changed"):
            asyncio.run(agent.run("input"))
        assert client.requests == []
        records = [json.loads(f.read_text()) for f in spool.directory.glob("*.json")]
        assert any(r.get("reason_code") == "threadlight:model_target_changed" for r in records)
        assert "PRIVATE-CHANGED" not in json.dumps(records)
    else:
        assert asyncio.run(agent.run("input")).text == "finished"
        assert len(client.requests) == 1
    asyncio.run(client.client.close())
