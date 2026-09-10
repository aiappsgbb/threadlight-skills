"""Durable pending approval through the real Task8 protocol; no HTTP wait for a human."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from test_gateway import Credential, GatewayHarness, gateway, registry
from test_control_plane import module as control_module

pytestmark = pytest.mark.governance_runtime


async def harness(path, *, requirement="always", seconds=300, decision=None):
    document = registry()
    document["actions"][0].update(
        approval_mode="deferred", approval_requirement=requirement,
        approval_timeout_seconds=seconds, approval_roles=["Approver"])
    h = await GatewayHarness().initialize(path, document=document, decision=decision)
    h.approval = gateway("receipts").HTTPControlPlaneApprovalService(
        base_url="https://control.example", scope="api://governance/.default",
        credential=Credential(h.cp.token()), http=h.cp.client)
    h.dispatcher = h.new_dispatcher()
    return h


@pytest.mark.parametrize("approved", [True, False])
def test_deferred_approval_survives_dispatcher_restart_and_consumes_once(tmp_path, approved):
    async def run():
        h = await harness(tmp_path)
        try:
            first = await asyncio.wait_for(h.call(), 2)
            assert first["status"] == "pending_approval"
            assert first["operation_id"] == "one"
            assert first["proposed_arguments"] == {"amount": 5}
            intent = first["approval_intent"]
            assert datetime.now(timezone.utc) + timedelta(seconds=60) < datetime.fromisoformat(intent["expires_at"])
            assert datetime.fromisoformat(intent["expires_at"]) <= datetime.fromisoformat(intent["policy_expires_at"])
            assert h.calls == []
            assert await h.call(dispatcher=h.new_dispatcher()) == first
            records = list(h.store.docs.values())
            assert "amount" not in json.dumps(records)
            decided = await h.cp.post(
                "decide", human=True, intent=intent, approved=approved, approving_role="Approver")
            assert decided.status_code == 200
            resumed = await h.call(dispatcher=h.new_dispatcher())
            assert resumed["status"] == ("completed" if approved else "blocked")
            assert len(h.calls) == int(approved)
            assert await h.call(dispatcher=h.new_dispatcher()) == resumed
            assert len(h.calls) == int(approved)
            requests = [body for (_, key), (body, _) in h.cp.store.docs.items()
                        if key.startswith("approval:")]
            assert len(requests) == 1 and requests[0]["state"] == "consumed"
        finally:
            await h.close()
    asyncio.run(run())


def test_deferred_changed_arguments_or_trusted_facts_cannot_reuse_approval(tmp_path):
    async def run():
        h = await harness(tmp_path)
        try:
            first = await h.call()
            assert first["status"] == "pending_approval"
            decided = await h.cp.post(
                "decide", human=True, intent=first["approval_intent"],
                approved=True, approving_role="Approver")
            assert decided.status_code == 200
            changed = await h.call(arguments={"amount": 6})
            assert changed["reason_code"] == "idempotency_conflict"
            h.dispatcher.safe_provider = lambda facts: {"scope": facts["scope"], "revision": "changed"}
            assert (await h.call())["reason_code"] == "approval_context_changed"
            assert not h.calls
        finally:
            await h.close()
    asyncio.run(run())


def test_deferred_concurrent_resume_has_one_effect(tmp_path):
    async def run():
        h = await harness(tmp_path)
        try:
            first = await h.call()
            assert first["status"] == "pending_approval"
            assert (await h.cp.post(
                "decide", human=True, intent=first["approval_intent"],
                approved=True, approving_role="Approver")).status_code == 200
            replies = await asyncio.gather(
                h.call(dispatcher=h.new_dispatcher()), h.call(dispatcher=h.new_dispatcher()))
            assert any(reply["status"] == "completed" for reply in replies)
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


def test_deferred_expiry_does_not_extend_on_resume(tmp_path):
    async def run():
        h = await harness(tmp_path, seconds=1)
        try:
            first = await h.call()
            assert first["status"] == "pending_approval"
            await asyncio.sleep(1.1)
            assert (await h.call(dispatcher=h.new_dispatcher()))["reason_code"] == "approval_expired"
            assert not h.calls
        finally:
            await h.close()
    asyncio.run(run())


def test_policy_controlled_approval_keeps_low_risk_calls_autonomous(tmp_path):
    async def run():
        h = await harness(
            tmp_path, requirement="policy",
            decision='{"decision": "escalate"} if input.policy_target.value.amount > 100 else := {"decision": "allow"}')
        try:
            assert (await h.call(arguments={"amount": 5}))["status"] == "completed"
            assert (await h.call(key="review", arguments={"amount": 500}))["status"] == "pending_approval"
            assert len(h.calls) == 1
            assert len([key for _, key in h.cp.store.docs if key.startswith("approval:")]) == 1
        finally:
            await h.close()
    asyncio.run(run())


def test_deferred_mcp_client_returns_pending_and_resumes_with_the_same_operation(tmp_path):
    async def run():
        import httpx
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / "skills/threadlight-deploy/tests"))
        from test_maf_gateway_client import client_module
        module = client_module()
        h = await harness(tmp_path)
        app = gateway("server").create_app(h.dispatcher)

        async def authorize():
            h.policy.fresh()

        def make_client():
            return module.GovernedMCPTools(
                url="https://gateway.example/mcp", scope="api://gateway/.default",
                credential=Credential(h.cp.token()), authorize=authorize, selected_tools=["refund"],
                transport_factory=lambda: httpx.ASGITransport(app=app))

        try:
            async with app.router.lifespan_context(app):
                client = make_client()
                await client.connect()
                first = await client.functions[0].invoke(arguments={"amount": 5}, skip_parsing=True)
                assert first["status"] == "pending_approval" and h.calls == []
                assert first["resume_arguments"] == {"amount": 5}
                assert (await h.cp.post(
                    "decide", human=True, intent=first["approval_intent"],
                    approved=True, approving_role="Approver")).status_code == 200
                resumed_client = make_client()
                await resumed_client.connect()
                result = await resumed_client.functions[0].invoke(
                    arguments={"amount": 5, "governance_operation_id": first["operation_id"]},
                    skip_parsing=True)
                assert result == {"status": "refunded"}
                assert json.loads(h.calls[0].content) == {"amount": 5}
                assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


def test_deferred_health_requires_nonblocking_approval_protocol(tmp_path):
    async def run():
        h = await harness(tmp_path)
        h.approval.request_pending = None
        try:
            response = await h.health()
            assert response.status_code == 503
            assert response.json()["bindings"]["refund"]["reason_codes"] == ["approval_unavailable"]
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("human", [True, False])
def test_operator_review_uses_authenticated_human_protocol_not_workload_token(tmp_path, human):
    async def run():
        review = control_module("review")
        h = await harness(tmp_path)
        try:
            pending = await h.call()
            credential = Credential(h.cp.token(human=human))
            if not human:
                from govern_control_plane.client import ApprovalUnavailable
                with pytest.raises(ApprovalUnavailable):
                    await review.decide(
                        pending, approved=True, role="Approver", base_url="https://control.example",
                        scope="api://governance/.default", credential=credential, http=h.cp.client)
                assert not h.calls
                return
            result = await review.decide(
                pending, approved=True, role="Approver", base_url="https://control.example",
                scope="api://governance/.default", credential=credential, http=h.cp.client)
            assert result == {"operation_id": "one", "approved": True, "execution": "not-started"}
            assert not h.calls
            assert (await h.call(dispatcher=h.new_dispatcher()))["status"] == "completed"
        finally:
            await h.close()
    asyncio.run(run())


def test_operator_review_rejects_changed_display_before_authentication(tmp_path):
    async def run():
        review = control_module("review")
        h = await harness(tmp_path)
        try:
            pending = await h.call()
            pending["proposed_arguments"]["amount"] = 6
            credential = Credential(h.cp.token(human=True))
            with pytest.raises(ValueError, match="review"):
                await review.decide(
                    pending, approved=True, role="Approver", base_url="https://control.example",
                    scope="api://governance/.default", credential=credential, http=h.cp.client)
            assert not credential.scopes and not h.calls
        finally:
            await h.close()
    asyncio.run(run())


def test_operator_cli_refuses_unattended_approval_before_opening_inputs(monkeypatch):
    import io
    from test_control_plane import TENANT, UI
    review = control_module("review")
    monkeypatch.setattr(review.sys, "stdin", io.StringIO("APPROVE forged"))
    assert review.main([
        "--pending", "does-not-exist.json", "--control-plane-url", "https://control.example",
        "--scope", "api://review/.default", "--tenant", TENANT, "--client-id", UI,
        "--role", "Approver", "--decision", "approve",
    ]) == 2


@pytest.mark.parametrize("change_before_send", [True, False])
def test_deferred_facts_are_rechecked_before_send_not_after_the_effect(tmp_path, change_before_send):
    async def run():
        h = await harness(tmp_path)

        async def change():
            h.dispatcher.safe_provider = lambda facts: {"scope": facts["scope"], "revision": "changed"}

        try:
            pending = await h.call()
            assert (await h.cp.post(
                "decide", human=True, intent=pending["approval_intent"],
                approved=True, approving_role="Approver")).status_code == 200
            if change_before_send:
                h.credential.hook = change
            else:
                original = h.downstream.http._transport.inner

                class ChangeAfterEffect:
                    async def handle_async_request(self, request):
                        response = await original.handle_async_request(request)
                        await change()
                        return response

                    async def aclose(self):
                        await original.aclose()

                h.downstream.http._transport.inner = ChangeAfterEffect()
            result = await h.call()
            assert result["status"] == ("blocked" if change_before_send else "completed")
            assert len(h.calls) == int(not change_before_send)
        finally:
            await h.close()
    asyncio.run(run())


def test_deferred_approval_expiring_after_dispatch_does_not_lose_completed_effect(tmp_path):
    async def run():
        h = await harness(tmp_path, seconds=1)
        try:
            pending = await h.call()
            assert (await h.cp.post(
                "decide", human=True, intent=pending["approval_intent"],
                approved=True, approving_role="Approver")).status_code == 200
            original = h.downstream.http._transport.inner

            class SlowResponse:
                async def handle_async_request(self, request):
                    response = await original.handle_async_request(request)
                    await asyncio.sleep(1.1)
                    return response

                async def aclose(self):
                    await original.aclose()

            h.downstream.http._transport.inner = SlowResponse()
            result = await h.call()
            assert result["status"] == "completed", result
            assert len(h.calls) == 1
            assert (await h.call(dispatcher=h.new_dispatcher()))["status"] == "completed"
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"approval_roles": []},
    {"approval_timeout_seconds": True},
    {"approval_timeout_seconds": 3601},
    {"approval_mode": "automatic"},
])
def test_invalid_signed_deferred_settings_are_rejected(change):
    from govern_control_plane.models import canonical, parse
    document = registry()["actions"][0]
    document.update(approval_mode="deferred", approval_roles=["Approver"])
    document.update(change)
    with pytest.raises(ValueError):
        parse(gateway("dispatcher").Action, canonical(document))


def test_deferred_packaging_exposes_operator_review_without_an_agent_runtime():
    import tomllib
    root = Path(__file__).resolve().parents[3]
    control = tomllib.loads((root / "skills/threadlight-govern/references/control-plane/pyproject.toml").read_text())
    assert control["project"]["version"] == "0.2.0"
    assert control["project"]["scripts"]["threadlight-review-action"] == "govern_control_plane.review:main"
    assert not any("agent-framework" in dep or "agent-control-specification" in dep
                   for dep in control["project"]["dependencies"])
    gw = tomllib.loads((root / "skills/threadlight-govern/references/gateway/pyproject.toml").read_text())
    assert gw["project"]["version"] == "0.2.0"
    assert "threadlight-govern-control-plane==0.2.0" in gw["project"]["dependencies"]
    fixture = tomllib.loads(
        (root / "skills/threadlight-safe-check/references/probe-fixture/pyproject.toml").read_text())
    assert fixture["project"]["version"] == "0.2.0"
    assert fixture["project"]["dependencies"] == ["threadlight-govern-gateway==0.2.0"]
    readme = (root / "skills/threadlight-govern/references/gateway/README.md").read_text()
    assert "awaiting_approval" in readme and "governance_operation_id" in readme
    assert "not OBO" in readme
