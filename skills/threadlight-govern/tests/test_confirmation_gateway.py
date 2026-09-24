"""Native ACS/OPA confirmation paths; run only with the published exact-pin runner."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from test_control_plane import HUMAN, module
from test_gateway import GatewayHarness, Credential, gateway, registry
from test_user_confirmation import USER, configure_confirmation, register_context, selection, user_headers

pytestmark = pytest.mark.governance_runtime


async def harness(path, *, review=False, user=USER, decision=None, trigger="always", document=None):
    doc = document or registry()
    doc["actions"][0]["confirmation_requirement"] = selection()
    doc["actions"][0]["confirmation_requirement"]["trigger"] = trigger
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
            assert "amount" not in json.dumps(list(h.store.docs.values()))
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


def test_native_confirmation_and_signed_evidence_coexist_without_persisting_token(tmp_path):
    async def run():
        from test_evidence_attestations import fixture
        _, _, requirement, identity, args, _, adapter, provider, _ = fixture()
        doc = registry()
        action = doc["actions"][0]
        action["input_schema"]["properties"].update(case_id={"type": "string", "maxLength": 128},
            expected_etag={"type": "string", "maxLength": 128})
        action["input_schema"]["properties"]["amount"]["maximum"] = 2000
        action["input_schema"]["required"] += ["case_id", "expected_etag"]
        action["evidence_requirement"] = requirement.model_dump()
        h = await harness(tmp_path, document=doc,
            decision='{"decision":"allow"} if input.snapshot.safe.evidence.claims.purchase_verified == true else := {"decision":"deny"}')
        try:
            missing = await call(h, arguments=args)
            assert missing["reason_code"] == "evidence_required"
            assert not h.cp.notifications
            token = (await provider.issue(identity, args))["attestation"]
            pending = await call(h, arguments=args, evidence_token=token)
            assert pending["status"] == "pending_confirmation", pending
            await decide(h, pending)
            renewed = (await provider.issue(identity, args))["attestation"]
            result = await call(h, arguments=args, evidence_token=renewed)
            assert result["status"] == "completed", result
            assert len(h.calls) == 1
            assert h.receipt_bodies()[-1]["evidence_fingerprint"].startswith("sha256:")
            stored = json.dumps([*h.store.docs.values(), *h.receipt_bodies(), *h.cp.confirmation_store.docs.values()])
            assert token not in stored and renewed not in stored
            assert await call(h, arguments=args) == result
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("same_user", [True, False])
def test_native_outlook_reviewer_stays_distinct_from_requesting_user(tmp_path, same_user):
    async def run():
        from test_outlook_approval import NativeHarness
        doc = registry()
        doc["actions"][0]["input_schema"]["properties"]["case_id"] = {"type": "string", "maxLength": 64}
        doc["actions"][0]["input_schema"]["required"].append("case_id")
        h = await harness(tmp_path, review=True, user=HUMAN if same_user else USER, document=doc)
        # Cross-tenant native home identities require explicit requester identity
        # correlation; a resource-tenant object ID alone does not establish it.
        from test_control_plane import TENANT
        profile = h.cp.service.confirmation.profile("email-basic")
        binding = profile.users[0].model_copy(update={
            "requester_home_tenant": TENANT, "requester_home_subject": h.confirming_user})
        h.cp.service.confirmation.config.profiles["email-basic"] = profile.model_copy(update={"users": [binding]})
        native = await NativeHarness().initialize(control=h.cp, actions=("refund",))
        h.approval = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client, review_enabled=True)
        h.dispatcher = new_dispatcher(h)
        args = {"amount": 5, "case_id": "RMA-1"}
        try:
            pending = await call(h, arguments=args)
            await decide(h, pending)
            review = await call(h, arguments=args)
            assert review["status"] == "pending_approval", review
            native.state = "Succeeded"
            native.mutation = lambda value: value["properties"]["outputs"]["outlook_decision"]["value"][
                "response"].update(SelectedOption="Approve")
            result = await call(h, arguments=args)
            assert (result["status"] == "completed") is not same_user, result
            assert len(h.calls) == int(not same_user)
        finally:
            await native.http.aclose()
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("metadata", [True, False])
def test_native_mcp_confirmation_metadata_is_opaque_and_never_backend_arguments(tmp_path, metadata):
    async def run():
        h = await harness(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url="https://gateway.example") as client:
                    headers = {"Accept": "application/json, text/event-stream",
                               "Authorization": "Bearer " + h.cp.token(), "Idempotency-Key": "one"}
                    listed = await client.post("/mcp", headers=headers, json={
                        "jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                    tool = listed.json()["result"]["tools"][0]
                    assert tool["_meta"]["threadlight.confirmation"] == "governance_request_context"
                    params = {"name": "refund", "arguments": {"amount": 5}}
                    if metadata:
                        params["_meta"] = {"governance_request_context": h.context_ref}
                    else:
                        params["arguments"]["governance_request_context"] = h.context_ref
                    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params}
                    first = (await client.post("/mcp", headers=headers, json=payload)).json()["result"]
                    assert first["isError"] is False
                    pending = first["structuredContent"]
                    assert pending["status"] == "pending_confirmation"
                    params["_meta"] = {"governance_request_context": h.context_ref}
                    params["arguments"]["governance_request_context"] = h.context_ref
                    ambiguous = (await client.post("/mcp", headers=headers, json=payload)).json()["result"]
                    assert ambiguous["structuredContent"]["reason_code"] == "ambiguous_confirmation_transport"
                    assert not h.calls
                    del params["arguments"]["governance_request_context"]
                    await decide(h, pending)
                    resumed = (await client.post("/mcp", headers=headers, json=payload)).json()["result"]
                    assert resumed["structuredContent"]["status"] == "completed", resumed
                    assert json.loads(h.calls[0].content) == {"amount": 5}
                    assert h.context_ref not in str(h.calls[0].headers)
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_native_policy_confirmation_alone_is_explicit_signed_escalation_obligation(tmp_path):
    async def run():
        h = await harness(tmp_path, trigger="policy",
            decision='{"decision": "escalate"} if input.policy_target.value.amount > 100 else := {"decision": "allow"}')
        try:
            low = await h.call(key="low")
            assert low["status"] == "completed", low
            missing = await h.call(key="missing", arguments={"amount": 500})
            assert missing["reason_code"] == "confirmation_context_required", missing
            pending = await call(h, arguments={"amount": 500})
            assert pending["status"] == "pending_confirmation", pending
            assert not any(key.startswith("approval:") for scope, key in h.cp.store.docs)
            await decide(h, pending)
            result = await call(h, arguments={"amount": 500})
            assert result["status"] == "completed", result
            assert len(h.calls) == 2
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
