"""Native ACS/OPA confirmation paths; run only with the published exact-pin runner."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from test_control_plane import HUMAN, module
from test_gateway import GatewayHarness, Credential, gateway, registry
from test_user_confirmation import USER, configure_confirmation, register_context, selection, user_headers

pytestmark = pytest.mark.governance_runtime


async def harness(path, *, review=False, user=USER, decision=None):
    doc = registry()
    doc["actions"][0]["confirmation_requirement"] = selection()
    if review:
        doc["actions"][0].update(approval_roles=["Approver"], approval_mode="deferred")
    h = await GatewayHarness().initialize(path, document=doc, decision=decision)
    await configure_confirmation(h.cp, user=user)
    h.confirmations = module("confirmation_client").ConfirmationClient(
        base_url="https://control.example", scope="api://governance/.default",
        credential=Credential(h.cp.token()), http=h.cp.client)
    if review:
        h.approval = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client)
    h.dispatcher = new_dispatcher(h)
    response = await register_context(h.cp, user=user)
    assert response.status_code == 200, response.text
    h.context_ref = response.json()["context_ref"]
    h.confirming_user = user
    return h


def new_dispatcher(h):
    d = h.new_dispatcher()
    d.confirmations = h.confirmations
    return d


async def call(h, **kwargs):
    return await h.call(requesting_user_context=h.context_ref, **kwargs)


async def decide(h, pending, *, approved=True):
    url = "/confirmation/" + pending["confirmation_id"]
    view = await h.cp.client.get(url, headers=user_headers(h.cp, user=h.confirming_user))
    assert view.status_code == 200, view.text
    response = await h.cp.client.post(url, headers=user_headers(h.cp, user=h.confirming_user), json={
        "intent_digest": view.json()["intent_digest"], "approved": approved})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("approved", [True, False])
def test_native_confirmation_restart_concurrent_resume_one_effect(tmp_path, approved):
    async def run():
        h = await harness(tmp_path)
        try:
            first = await call(h)
            assert first["status"] == "pending_confirmation", first
            assert set(first) == {"status", "operation_id", "confirmation_id"}
            assert await call(h, dispatcher=new_dispatcher(h)) == first
            assert not h.calls and len(h.cp.notifications) == 1
            assert "amount" not in json.dumps(h.store.docs)
            await decide(h, first, approved=approved)
            result = await asyncio.gather(call(h, dispatcher=new_dispatcher(h)),
                                          call(h, dispatcher=new_dispatcher(h)))
            assert any(r["status"] == ("completed" if approved else "blocked") for r in result), result
            assert len(h.calls) == int(approved)
            replay = await call(h, dispatcher=new_dispatcher(h))
            assert replay["status"] == ("completed" if approved else "blocked"), replay
            assert len(h.calls) == int(approved)
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_native_deny_never_notifies_or_effects(tmp_path):
    async def run():
        h = await harness(tmp_path, decision={"decision": "deny"})
        try:
            result = await call(h)
            assert result["reason_code"] == "policy_deny"
            assert not h.calls and not h.cp.notifications
            assert not any(key.startswith("confirmation:") for scope, key in h.cp.confirmation_store.docs)
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("same_user", [True, False])
def test_native_confirmation_cannot_replace_independent_review(tmp_path, same_user):
    async def run():
        h = await harness(tmp_path, review=True, user=HUMAN if same_user else USER)
        try:
            first = await call(h)
            assert first["status"] == "pending_confirmation", first
            assert not any(key.startswith("approval:") for scope, key in h.cp.store.docs)
            await decide(h, first)
            review = await call(h)
            assert review["status"] == "pending_approval", review
            assert not h.calls
            assert (await h.cp.post("decide", human=True, intent=review["approval_intent"],
                                   approved=True, approving_role="Approver")).status_code == 200
            result = await call(h)
            assert (result["status"] == "completed") is not same_user, result
            assert len(h.calls) == int(not same_user)
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["facts", "expiry", "receipt", "outcome"])
def test_native_confirmation_terminal_waits_and_unknown_preserve_operation(tmp_path, failure):
    async def run():
        h = await harness(tmp_path)
        try:
            first = await call(h)
            await decide(h, first)
            if failure in ("facts", "expiry"):
                async def changed():
                    if failure == "facts":
                        h.dispatcher.safe_provider = lambda facts: {"scope": facts["scope"], "revision": "new"}
                    else:
                        key = next(key for key in h.cp.confirmation_store.docs if key[1].startswith("confirmation:"))
                        h.cp.confirmation_store.docs[key][0]["intent"]["expires_at"] = "2020-01-01T00:00:00+00:00"
                h.credential.hook = changed
            elif failure == "receipt":
                async def lost(receipt):
                    raise OSError("lost central ACK")
                h.receipts.append = lost
            else:
                h.downstream_status = 503
            result = await call(h)
            assert result["status"] != "completed", result
            assert len(h.calls) == int(failure == "outcome")
            replay = await call(h)
            assert replay["reason_code"] == "outcome_unknown", replay
            assert len(h.calls) == int(failure == "outcome")
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())
