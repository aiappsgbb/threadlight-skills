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
    client = model_client(tool_responses())
    if stage == "preparation":
        client.compaction_strategy = object()
        async def prepare(messages, **kwargs):
            await asyncio.sleep(0)
            clock.current = authority.expires_at
            return messages
        monkeypatch.setattr(client, "_prepare_messages_for_model_call", prepare)
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
    client = model_client(tool_responses())
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
    client = model_client(tool_responses())
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
    client = model_client(tool_responses("read"))
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
    client = model_client(tool_responses())
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
