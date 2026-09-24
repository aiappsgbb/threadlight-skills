"""Integration regressions for authority identity, lifetime and trusted notification."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import jwt
import pytest

from test_control_plane import APP, HUMAN, OTHER, TENANT, UI, WORKLOAD, Harness, module
from test_gateway import Credential, gateway, registry
from test_confirmation_gateway import harness, call, decide
from test_confirmation_providers import protected_policy
from test_user_confirmation import USER, configure_confirmation, pending_intent, user_headers


class Clock:
    def __init__(self):
        self.value = datetime.now(timezone.utc)

    def now(self, tz=None):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)

    def install(self, monkeypatch, *modules):
        clock = self
        class ControlledDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now(tz)
        for target in modules:
            monkeypatch.setattr(target, "datetime", ControlledDatetime)


def signed_profile(h):
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return {"kind": "customer-signed", "result_verifier": {
        "issuer": "https://customer.example", "audience": "confirmation-provider",
        "client_id": "customer-web", "scope": "confirm", "key_id": "customer-key",
        "public_key": h.key.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()}}


async def signed_decide(h, intent, *, lifetime=60):
    c = module("confirmation")
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"iss": "https://customer.example", "sub": USER, "aud": "confirmation-provider",
        "azp": "customer-web", "scp": "confirm", "iat": now - 1, "nbf": now - 1,
        "exp": now + lifetime, "requester_issuer": h.settings.issuer,
        "intent_digest": c.digest(intent), "confirmation_id": intent.confirmation_id,
        "approved": True, "capability": "authenticated-consent"}
    token = jwt.encode(claims, h.key, algorithm="RS256", headers={"kid": "customer-key"})
    response = await h.client.post("/confirmation/" + intent.confirmation_id,
        headers=user_headers(h), json={"intent_digest": c.digest(intent),
                                       "approved": True, "provider_result": token})
    assert response.status_code == 200, response.text
    return datetime.fromtimestamp(claims["exp"], timezone.utc)


LOGIC_URL = ("https://prod-1.westeurope.logic.azure.com/workflows/" + "a" * 32
             + "/triggers/User_confirmation_requested/paths/invoke?api-version=2016-10-01")
ARM_SCOPE = "https://management.azure.com//.default"


def test_companion_notification_configuration_accepts_exact_sas_free_logic_app():
    from test_user_confirmation import confirmation_config
    h = Harness()
    profile = confirmation_config(h)["profiles"]["email-basic"]
    selected = module("confirmation").ProviderProfile.model_validate({
        **profile, "notification_url": LOGIC_URL, "notification_scope": ARM_SCOPE})
    assert selected.notification_url == LOGIC_URL and selected.notification_scope == ARM_SCOPE


@pytest.mark.parametrize("mutation", [
    "&sig=secret", "&sp=%2Ftriggers%2Fmanual%2Frun", "&sv=1.0",
    "&unknown=1", "&api-version=2016-10-01", "&api-version=", "#fragment",
])
def test_companion_notification_rejects_sas_unknown_duplicate_and_fragments(mutation):
    from test_user_confirmation import confirmation_config
    h = Harness()
    profile = confirmation_config(h)["profiles"]["email-basic"]
    with pytest.raises(ValueError):
        module("confirmation").ProviderProfile.model_validate({
            **profile, "notification_url": LOGIC_URL + mutation, "notification_scope": ARM_SCOPE})


@pytest.mark.parametrize("url,scope", [
    (LOGIC_URL.replace(".logic.azure.com", ".logic.azure.com.evil.example"), ARM_SCOPE),
    (LOGIC_URL.replace(".logic.azure.com/", ".logic.azure.com:443/"), ARM_SCOPE),
    (LOGIC_URL.replace("User_confirmation_requested", "Review_notification_requested"), ARM_SCOPE),
    (LOGIC_URL.replace("api-version=2016-10-01", "%61pi-version=2016-10-01"), ARM_SCOPE),
    (LOGIC_URL, "https://management.azure.com/.default"),
    (LOGIC_URL, "api://notification/.default"),
    ("https://notification.example/send", ARM_SCOPE),
])
def test_notification_authority_cannot_be_retargeted(url, scope):
    from test_user_confirmation import confirmation_config
    h = Harness()
    profile = confirmation_config(h)["profiles"]["email-basic"]
    with pytest.raises(ValueError):
        module("confirmation").ProviderProfile.model_validate({
            **profile, "notification_url": url, "notification_scope": scope})
    with pytest.raises(ValueError):
        module("confirmation").https(LOGIC_URL)


def test_companion_notification_executes_with_arm_scope_without_sas():
    async def run():
        h = await configure_confirmation(await Harness().initialize(), profile={
            "notification_url": LOGIC_URL, "notification_scope": ARM_SCOPE})
        try:
            await pending_intent(h)
            assert h.service.confirmation.credential.scopes == [ARM_SCOPE]
            assert len(h.notifications) == 1
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("same_human", [True, False])
def test_native_outlook_mapped_alias_compares_actual_human_not_only_grant(tmp_path, same_human):
    async def run():
        from test_outlook_approval import NativeHarness, HOME_USER
        doc = registry()
        doc["actions"][0]["input_schema"]["properties"]["case_id"] = {"type": "string", "maxLength": 64}
        doc["actions"][0]["input_schema"]["required"].append("case_id")
        h = await harness(tmp_path, review=True, user=USER, document=doc)
        native = await NativeHarness().initialize(control=h.cp, actions=("refund",))
        actual_subject = USER if same_human else HOME_USER
        outlook = h.cp.service.outlook
        outlook.config = outlook.config.model_copy(update={"responders": (
            module("outlook").OutlookResponder(
                home_tenant=TENANT, home_subject=actual_subject, approver=HUMAN, role="Approver"),)})
        h.approval = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client, review_enabled=True)
        from test_confirmation_gateway import new_dispatcher
        h.dispatcher = new_dispatcher(h)
        args = {"amount": 5, "case_id": "RMA-1"}
        try:
            pending = await call(h, arguments=args)
            await decide(h, pending)
            review = await call(h, arguments=args)
            assert review["status"] == "pending_approval", review
            native.state = "Succeeded"
            native.mutation = lambda value: value["properties"]["outputs"]["outlook_decision"]["value"][
                "response"].update(SelectedOption="Approve", UserTenantId=TENANT, UserId=actual_subject)
            result = await call(h, arguments=args)
            assert (result["status"] == "completed") is not same_human, result
            assert len(h.calls) == int(not same_human)
        finally:
            await native.http.aclose()
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("wait", ["policy", "store"])
def test_customer_result_expiry_rechecked_after_control_plane_await(monkeypatch, wait):
    async def run():
        h = await Harness().initialize()
        await configure_confirmation(h, profile=signed_profile(h))
        try:
            intent = await pending_intent(h)
            expires = await signed_decide(h, intent)
            clock = Clock()
            clock.install(monkeypatch, module("confirmation"))
            if wait == "policy":
                fresh = h.service.confirmation.fresh
                calls = 0
                async def waited(*args):
                    nonlocal calls
                    calls += 1
                    result = await fresh(*args)
                    if calls == 2:
                        clock.value = expires + timedelta(seconds=1)
                    return result
                h.service.confirmation.fresh = waited
            else:
                replace = h.confirmation_store.replace
                async def waited(scope, key, body, etag):
                    await replace(scope, key, body, etag)
                    if body.get("state") == "consumed":
                        clock.value = expires + timedelta(seconds=1)
                h.confirmation_store.replace = waited
            response = await h.client.post("/confirmation/resolve", headers=h.headers(),
                json={"operation": "consume", "intent": intent.model_dump(mode="json")})
            assert response.status_code == 409, response.text
            assert not h.store.docs
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_control_plane_returns_bound_effective_authority_expiry():
    async def run():
        h = await Harness().initialize()
        await configure_confirmation(h, profile=signed_profile(h))
        try:
            intent = await pending_intent(h)
            expires = await signed_decide(h, intent)
            for operation in ("resolve", "consume", "validate"):
                response = await h.client.post("/confirmation/resolve", headers=h.headers(), json={
                    "operation": operation, "intent": intent.model_dump(mode="json")})
                assert response.status_code == 200, response.text
                assert datetime.fromisoformat(response.json()["effective_authority_expires_at"]) == expires
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_customer_result_expiry_after_terminal_native_policy_wait_blocks_wire(tmp_path, monkeypatch):
    async def run():
        h = await harness(tmp_path)
        old_http = h.cp.confirmation_http
        await configure_confirmation(h.cp, profile=signed_profile(h.cp))
        await old_http.aclose()
        from test_user_confirmation import register_context
        h.context_ref = (await register_context(h.cp)).json()["context_ref"]
        try:
            pending = await call(h)
            view = await h.cp.client.get("/confirmation/" + pending["confirmation_id"], headers=user_headers(h.cp))
            c = module("confirmation")
            intent = c.parse(c.ConfirmationIntent, c.canonical(view.json()["intent"]))
            expires = await signed_decide(h.cp, intent)
            clock = Clock()
            clock.install(monkeypatch, c, gateway("dispatcher"))
            evaluate = h.policy.evaluate
            count = 0
            async def waited(*args, **kwargs):
                nonlocal count
                result = await evaluate(*args, **kwargs)
                count += 1
                # Resume pre-policy, transport check, header trace, then body trace:
                # the last native await must still precede the actual effect.
                if count == 4:
                    clock.value = expires + timedelta(seconds=1)
                return result
            h.policy.evaluate = waited
            response = await call(h)
            assert response["status"] != "completed", response
            assert h.calls == []
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_customer_expiry_after_effect_keeps_durable_outcome_and_never_reexecutes(tmp_path, monkeypatch):
    async def run():
        h = await harness(tmp_path)
        old_http = h.cp.confirmation_http
        await configure_confirmation(h.cp, profile=signed_profile(h.cp))
        await old_http.aclose()
        from test_user_confirmation import register_context
        h.context_ref = (await register_context(h.cp)).json()["context_ref"]
        try:
            pending = await call(h)
            view = await h.cp.client.get("/confirmation/" + pending["confirmation_id"], headers=user_headers(h.cp))
            c = module("confirmation")
            intent = c.parse(c.ConfirmationIntent, c.canonical(view.json()["intent"]))
            expires = await signed_decide(h.cp, intent)
            clock = Clock()
            clock.install(monkeypatch, c, gateway("dispatcher"))
            transport = h.downstream.http._transport.inner
            original = transport.handle_async_request
            async def late_response(request):
                response = await original(request)
                clock.value = expires + timedelta(seconds=1)
                return response
            transport.handle_async_request = late_response
            result = await call(h)
            assert result["status"] == "completed", result
            assert await call(h) == result
            assert len(h.calls) == 1
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_entra_verified_token_preserves_iat_without_claiming_mfa_time():
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            now = int(datetime.now(timezone.utc).timestamp())
            authorization = user_headers(h, changes={"iat": now - 17})["Authorization"]
            identity = await h.auth.authenticate(authorization)
            assert identity.issued_at == now - 17
            user = await h.service.confirmation.authenticate_user(h.service, h.auth, authorization,
                h.service.confirmation.profile("email-basic"))
            assert user.issued_at == now - 17
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_entra_pre_protection_token_rejected_and_new_generation_invalidates(monkeypatch):
    async def run():
        c, ca = module("confirmation"), module("confirmation_entra")
        h = await Harness().initialize()
        await configure_confirmation(h, profile={"kind": "entra-ca", "authentication_context": "c1",
            "conditional_access_policy_id": OTHER, "capability": "ca-mfa"})
        profile = h.service.confirmation.profile("email-basic")
        clock = Clock()
        clock.install(monkeypatch, ca, c)
        policy = {**protected_policy(), "modifiedDateTime": (clock.value - timedelta(minutes=1)).isoformat()}
        async def graph(request):
            return httpx.Response(200, json=(
                {"id": "c1", "isAvailable": True} if "authenticationContextClassReferences" in str(request.url)
                else policy))
        http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
        verifier = ca.EntraConditionalAccess(tenant=TENANT, issuer=h.settings.issuer, credential=Credential(), http=http)
        original = c.RequestingUser(issuer=h.settings.issuer, subject=USER, client=UI,
            expires_at=clock.value + timedelta(minutes=5), auth_contexts=("c1",))
        original = original.model_copy(update={"issued_at": int(clock.value.timestamp()) - 60})
        try:
            policy["state"] = "disabled"
            with pytest.raises(c.ConfirmationUnavailable):
                await verifier.verify(profile, original)
            policy["state"] = "enabled"
            policy["modifiedDateTime"] = clock.value.isoformat()
            with pytest.raises(ca.ClaimsChallenge):
                await verifier.verify(profile, original)
            clock.advance(2)
            fresh = original.model_copy(update={"issued_at": int(clock.value.timestamp())})
            first_generation = await verifier.verify(profile, fresh)
            assert first_generation
            assert await verifier.verify(profile, fresh) == first_generation
            # A modification can revert content; its Graph generation still changes.
            clock.advance(2)
            policy["modifiedDateTime"] = clock.value.isoformat()
            with pytest.raises(ca.ClaimsChallenge):
                await verifier.verify(profile, fresh)
            clock.advance(2)
            latest = original.model_copy(update={"issued_at": int(clock.value.timestamp())})
            assert await verifier.verify(profile, latest) != first_generation
            restarted = ca.EntraConditionalAccess(
                tenant=TENANT, issuer=h.settings.issuer, credential=Credential(), http=http)
            with pytest.raises(ca.ClaimsChallenge):
                await restarted.verify(profile, fresh)
        finally:
            await http.aclose()
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("lost_history", ["unavailable", "missing-modified", "mapping", "long-interaction"])
def test_entra_epoch_invalidation_and_stable_challenge_generation(monkeypatch, lost_history):
    async def run():
        c, ca = module("confirmation"), module("confirmation_entra")
        h = await configure_confirmation(await Harness().initialize(), profile={
            "kind": "entra-ca", "authentication_context": "c1",
            "conditional_access_policy_id": OTHER, "capability": "ca-mfa"})
        profile = h.service.confirmation.profile("email-basic")
        clock = Clock()
        clock.install(monkeypatch, c, ca)
        policy = protected_policy()
        unavailable = False
        async def graph(request):
            if unavailable:
                return httpx.Response(503)
            return httpx.Response(200, json=(
                {"id": "c1", "isAvailable": True} if "authenticationContextClassReferences" in str(request.url)
                else policy))
        http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
        verifier = ca.EntraConditionalAccess(tenant=TENANT, issuer=h.settings.issuer,
                                            credential=Credential(), http=http)
        try:
            await verifier.health(profile)
            clock.advance(2)
            user = c.RequestingUser(issuer=h.settings.issuer, subject=USER, client=UI,
                expires_at=clock.value + timedelta(minutes=5), auth_contexts=("c1",),
                issued_at=int(clock.value.timestamp()))
            generation = await verifier.verify(profile, user)
            if lost_history == "long-interaction":
                clock.advance(60)
                # Fresh Graph reads with unchanged modifiedDateTime preserve the
                # epoch across the interactive challenge, but not stale assurance.
                with pytest.raises(ca.ClaimsChallenge):
                    verifier.fresh(profile, user, generation)
                assert await verifier.verify(profile, user) == generation
                return
            if lost_history == "unavailable":
                unavailable = True
                with pytest.raises(c.ConfirmationUnavailable):
                    await verifier.verify(profile, user)
                unavailable = False
            elif lost_history == "missing-modified":
                old = policy.pop("modifiedDateTime")
                with pytest.raises(c.ConfirmationUnavailable, match="history_unavailable"):
                    await verifier.verify(profile, user)
                policy["modifiedDateTime"] = old
            else:
                profile = profile.model_copy(update={"users": [
                    profile.users[0].model_copy(update={"delivery_ref": "changed-mapping"})]})
            clock.advance(2)
            with pytest.raises(ca.ClaimsChallenge):
                await verifier.verify(profile, user)
            with pytest.raises(ca.ClaimsChallenge):
                verifier.fresh(profile, user, generation)
        finally:
            await http.aclose()
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_protection_epoch_invalidated_during_final_policy_wait_cannot_ack_consent(monkeypatch):
    async def run():
        c, ca = module("confirmation"), module("confirmation_entra")
        h = await configure_confirmation(await Harness().initialize(), profile={
            "kind": "entra-ca", "authentication_context": "c1",
            "conditional_access_policy_id": OTHER, "capability": "ca-mfa"})
        clock = Clock()
        clock.advance(-5)
        clock.install(monkeypatch, c, ca)
        async def graph(request):
            return httpx.Response(200, json=(
                {"id": "c1", "isAvailable": True} if "authenticationContextClassReferences" in str(request.url)
                else protected_policy()))
        http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
        verifier = ca.EntraConditionalAccess(tenant=TENANT, issuer=h.settings.issuer, credential=Credential(), http=http)
        h.service.confirmation.provider = c.ExplicitConsentProvider(verifier)
        try:
            await verifier.health(h.service.confirmation.profile("email-basic"))
            clock.advance(5)
            intent = await pending_intent(h)
            fresh = h.service.confirmation.fresh
            calls = 0
            async def waited(*args):
                nonlocal calls
                value = await fresh(*args)
                calls += 1
                if calls == 2:
                    verifier.epochs.clear()
                return value
            h.service.confirmation.fresh = waited
            result = await h.client.post("/confirmation/" + intent.confirmation_id,
                headers=user_headers(h, changes={
                    "acrs": ["c1"], "iat": int(datetime.now(timezone.utc).timestamp())}),
                json={"approved": True, "intent_digest": c.digest(intent)})
            assert result.status_code == 401, result.text
            record, _ = await h.confirmation_store.read(TENANT, "confirmation:" + intent.confirmation_id)
            assert record["state"] == "pending"
        finally:
            await http.aclose()
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_reference_documents_protection_generation_and_effective_deadline():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "references"
    control = (base / "control-plane/README.md").read_text()
    gateway = (base / "gateway/README.md").read_text()
    assert ARM_SCOPE in control
    assert "requester_home_tenant" in control
    assert "protection epoch" in control and "modifiedDateTime" in control
    assert "effective_authority_expires_at" in control and "effective_authority_expires_at" in gateway
