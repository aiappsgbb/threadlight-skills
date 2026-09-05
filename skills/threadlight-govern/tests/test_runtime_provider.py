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
    return {
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
            self.requests.append(([m.to_dict() for m in messages], dict(options)))
            response = responses.pop(0)
            if not stream:
                async def get():
                    return response
                return get()

            async def updates():
                for text in chunks or [response.text]:
                    if on_chunk:
                        on_chunk(text)
                    yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(text)])
            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)
    return ScriptedClient()


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
    client = model_client(tool_responses(name, args))
    agent = runtime().create_governed_agent(
        p, client=client, tools=tools, middleware=list(middleware),
    )
    return asyncio.run(agent.run("perform action")), effects, client, agent


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
    client = model_client([ChatResponse(messages=[Message("assistant", ["PRIVATE-OUTPUT"])])])
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
    client = model_client([ChatResponse(messages=[Message("assistant", ["ok"])])])
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

    async def resolve(self, intent):
        from dataclasses import replace
        self.requests.append(intent)
        if self.case == "outage":
            raise RuntimeError("PRIVATE-APPROVAL-ERROR")
        grant = runtime().ApprovalGrant(intent=intent, approved=True)
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


@pytest.mark.parametrize("case", ["outage", "reject", "changed", "expired", "approve", "replay"])
def test_native_approval_resolver_binding(tmp_path, case):
    service = ApprovalService(case)
    spool = runtime().DurableSpool(tmp_path / "spool")
    p, _, _ = provider(tmp_path, document=contract(requires=("approval", "durable-audit")),
                       decisions={"pre_tool_call": {"decision": "escalate"}},
                       approval_resolver=service, principal="host:user", audit=spool)
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
