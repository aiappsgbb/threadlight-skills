"""Requesting-user consent uses a separate authority, never an ApprovalGrant."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from test_control_plane import Harness, MemoryStore, TENANT, WORKLOAD, APP, UI, HUMAN, OTHER, module
USER = "77777777-7777-7777-7777-777777777777"


def selection():
    return {"trigger": "always", "provider_profile": "email-basic", "max_age_seconds": 300}


def test_confirmation_requirement_is_strict_and_optional():
    from test_gateway import gateway, registry
    document = registry()["actions"][0]
    assert "confirmation_requirement" not in gateway("dispatcher").Action.model_validate(document).model_dump(mode="json")
    selected = gateway("dispatcher").Action.model_validate({**document, "confirmation_requirement": selection()})
    assert selected.confirmation_requirement.provider_profile == "email-basic"
    for bad in (None, {}, {**selection(), "max_age_seconds": True},
                {**selection(), "max_age_seconds": 3601}, {**selection(), "unknown": True}):
        with pytest.raises(ValueError):
            gateway("dispatcher").Action.model_validate({**document, "confirmation_requirement": bad})


def test_confirmation_only_user_does_not_become_reviewer():
    async def run():
        h = await Harness().initialize()
        try:
            settings = h.settings.model_copy(update={"confirmation_subjects": [USER]})
            auth = module("auth").EntraAuth(settings, h.http)
            token = h.token(human=True, changes={"oid": USER, "scp": "Governance.Confirm", "roles": []})
            identity = await auth.authenticate("Bearer " + token)
            assert identity.subject == USER and identity.workload is None
            assert identity.issuer == settings.issuer
            assert not h.service.auditor(identity)
            with pytest.raises(module("app").Forbidden):
                await h.service.approval(identity, module("models").parse(
                    module("models").ApprovalOperation, json.dumps({
                        "operation": "decide", "intent": h.wire_intent,
                        "approved": True, "approving_role": "Approver"}).encode()))
        finally:
            await h.close()
    asyncio.run(run())


def test_generic_user_identity_rejects_invalid_issuer_and_subject():
    confirmation = module("confirmation")
    user = confirmation.RequestingUser(
        issuer="https://identity.example/tenant", subject="customer:alice",
        client="customer-web", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert user.subject == "customer:alice"
    for changes in ({"issuer": "http://identity.example"}, {"issuer": "https://id.example/?token=secret"},
                    {"subject": ""}, {"subject": "alice\nadmin"}):
        with pytest.raises(ValueError):
            confirmation.RequestingUser(**{**user.model_dump(), **changes})


def confirmation_config(h, *, user=USER, kind="email-basic"):
    """Portable fixture: parent/native/generation tests can reuse the exact config."""
    return {
        "cosmos_container": "confirmation-ephemeral", "public_url": "https://control.example",
        "gateway_principals": [WORKLOAD],
        "profiles": {"email-basic": {
            "kind": kind,
            "users": [{"issuer": h.settings.issuer, "subject": user, "client": UI, "delivery_ref": "requester"}],
            "workloads": [{"workload": WORKLOAD, "client": APP, "agent_id": "agent-1", "actions": ["refund"]}],
            "notification_url": "https://notification.example/send",
            "notification_scope": "api://notification/.default",
        }},
    }


async def configure_confirmation(h, *, user=USER, profile=None):
    import httpx
    from test_gateway import Credential
    confirmation = module("confirmation")
    h.settings = h.settings.model_copy(update={"confirmation_subjects": [user]})
    h.auth.settings = h.service.settings = h.settings
    h.notifications = []
    h.notification_status = 200

    async def remote(request):
        body = json.loads(request.content)
        h.notifications.append(body)
        return httpx.Response(h.notification_status, json={"notification_id": body["confirmation_id"]})

    h.confirmation_http = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    h.confirmation_store = MemoryStore()
    config = confirmation_config(h, user=user)
    if profile:
        config["profiles"]["email-basic"].update(profile)
    h.confirmation_config = confirmation.parse(confirmation.ConfirmationConfiguration, json.dumps(config).encode())
    h.service.confirmation = confirmation.ConfirmationService(h.confirmation_config,
        h.confirmation_store, credential=Credential(), http=h.confirmation_http)
    return h


def user_headers(h, *, user=USER, changes=None):
    return {"Authorization": "Bearer " + h.token(human=True, changes={
        "oid": user, "scp": "Governance.Confirm", "roles": [], **(changes or {})})}


async def register_context(h, *, key="one", user=USER, changes=None):
    body = {"provider_profile": "email-basic", "workload": WORKLOAD, "client": APP,
            "agent_id": "agent-1", "action": "refund", "operation_id": key,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=240)).isoformat(),
            **(changes or {})}
    return await h.client.post("/confirmation/contexts", json=body, headers=user_headers(h, user=user))


async def pending_intent(h):
    c = module("confirmation")
    context = await register_context(h)
    assert context.status_code == 200, context.text
    facts = {"tenant": TENANT, "subject": WORKLOAD, "client": APP, "action": "refund",
             "policy": h.wire_intent["policy_hash"], "deployment": {"agent_id": "agent-1"}}
    arguments = {"amount": 5}
    intent = c.ConfirmationIntent(
        confirmation_id="d" * 32, context_ref=context.json()["context_ref"], operation_id="one",
        principal=WORKLOAD, tenant=TENANT, agent_id="agent-1", action="refund",
        action_hash=c.digest({"facts": facts, "arguments": arguments}),
        facts_hash=c.digest(facts), safe_hash=c.digest({"safe": True}),
        policy_hash=h.wire_intent["policy_hash"],
        policy_expires_at=datetime.fromisoformat(h.wire_intent["policy_expires_at"]),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
        requirement=c.ConfirmationRequirement(**selection()))
    response = await h.client.post("/confirmation/resolve", headers=h.headers(), json={
        "operation": "request", "intent": intent.model_dump(mode="json"),
        "facts": facts, "arguments": arguments})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending_confirmation"
    return intent


@pytest.mark.parametrize("approved", [True, False])
def test_real_confirmation_post_and_cas_are_separate_from_review(approved):
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            intent = await pending_intent(h)
            url = "/confirmation/" + intent.confirmation_id
            assert (await h.client.get(url)).status_code == 401
            assert (await h.client.get(url, headers=h.headers())).status_code == 401
            assert (await h.client.get(url, headers=user_headers(h, user=HUMAN))).status_code == 401
            view = await h.client.get(url, headers=user_headers(h))
            assert view.status_code == 200 and view.json()["arguments"] == {"amount": 5}
            assert view.headers["cache-control"] == "no-store"
            assert view.json()["state"] == "pending"
            body = {"intent_digest": view.json()["intent_digest"], "approved": approved}
            replies = await asyncio.gather(*[
                h.client.post(url, headers=user_headers(h), json=body) for _ in range(2)])
            assert sorted(r.status_code for r in replies) == [200, 409]
            assert not h.store.docs
            result = await h.client.post("/confirmation/resolve", headers=h.headers(), json={
                "operation": "consume", "intent": intent.model_dump(mode="json")})
            assert result.status_code == (200 if approved else 409)
            if approved:
                assert result.json()["status"] == "consumed"
            replay = await h.client.post("/confirmation/resolve", headers=h.headers(), json={
                "operation": "consume", "intent": intent.model_dump(mode="json")})
            assert replay.status_code == 409
            assert len(h.notifications) == 1
            assert set(h.notifications[0]) == {"confirmation_id", "confirmation_url", "delivery_ref", "expires_at"}
            assert "amount" not in json.dumps(h.notifications)
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"workload": HUMAN}, {"client": UI}, {"agent_id": "other"}, {"action": "other"},
    {"expires_at": "2020-01-01T00:00:00+00:00"},
])
def test_context_registration_checks_allowed_workload_binding(changes):
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            response = await register_context(h, changes=changes)
            assert response.status_code == 401
            assert not h.notifications and not h.confirmation_store.docs
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [
    ("action_hash", "sha256:" + "0" * 64), ("policy_hash", "sha256:" + "0" * 64),
    ("operation_id", "different"), ("context_ref", "f" * 32),
    ("expires_at", "2020-01-01T00:00:00+00:00"),
])
def test_changed_confirmation_intent_cannot_be_rebound(field, value):
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            intent = await pending_intent(h)
            response = await h.client.post("/confirmation/resolve", headers=h.headers(), json={
                "operation": "resolve", "intent": {**intent.model_dump(mode="json"), field: value}})
            assert response.status_code in (401, 404, 409)
            assert len(h.notifications) == 1 and not h.store.docs
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_forged_provider_and_changed_display_fail_closed():
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            intent = await pending_intent(h)
            response = await h.client.post("/confirmation/" + intent.confirmation_id, headers=user_headers(h),
                json={"intent_digest": module("confirmation").digest(intent), "approved": True,
                      "provider_result": "forged"})
            assert response.status_code == 401
            key = (TENANT, "confirmation:" + intent.confirmation_id)
            h.confirmation_store.docs[key][0]["arguments"]["amount"] = 900
            response = await h.client.get("/confirmation/" + intent.confirmation_id, headers=user_headers(h))
            assert response.status_code == 409
            assert not h.store.docs
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_notification_unknown_is_not_resent_after_restart():
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            h.notification_status = 503
            with pytest.raises(AssertionError):
                await pending_intent(h)
            assert len(h.notifications) == 1
            record = next(v[0] for (scope, key), v in h.confirmation_store.docs.items()
                          if key.startswith("confirmation:"))
            h.notification_status = 200
            old = h.service.confirmation
            h.service.confirmation = module("confirmation").ConfirmationService(
                old.config, old.store, credential=old.credential, http=old.http)
            response = await h.client.post("/confirmation/resolve", headers=h.headers(),
                json={"operation": "resolve", "intent": record["intent"]})
            assert response.status_code == 503 and len(h.notifications) == 1
            assert record["state"] == "pending"
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())
