"""Real signature and conservative CA configuration verification; no cloud proof."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest

from test_control_plane import Harness, TENANT, OTHER, module
from test_gateway import Credential
from test_user_confirmation import (
    USER, configure_confirmation, confirmation_config, pending_intent, register_context, user_headers,
)


def protected_policy():
    return {
        "id": OTHER, "state": "enabled", "conditions": {
            "clientAppTypes": ["all"],
            "users": {"includeUsers": ["All"], "excludeUsers": []},
            "applications": {"includeAuthenticationContextClassReferences": ["c1"]},
            "signInRiskLevels": [], "userRiskLevels": [], "platforms": None,
        }, "grantControls": {"operator": "AND", "builtInControls": ["mfa"],
                             "customAuthenticationFactors": [], "termsOfUse": []},
    }


def test_ca_verifier_requires_actual_unconditional_mfa_policy():
    ca = module("confirmation_entra")
    profile = SimpleNamespace(authentication_context="c1", conditional_access_policy_id=OTHER)
    context = {"id": "c1", "isAvailable": True}
    ca.verify_configuration(profile, USER, context, protected_policy())
    policies = []
    for state in ("disabled", "enabledForReportingButNotEnforced"):
        policies.append({**protected_policy(), "state": state})
    for changed in (
        {"clientAppTypes": ["browser"]}, {"locations": {"includeLocations": ["All"]}},
        {"unknown_condition": True}, {"users": {"includeUsers": ["All"], "excludeUsers": [USER]}},
        {"users": {"includeUsers": ["All"], "excludeGroups": [OTHER]}},
        {"users": {"includeGroups": [OTHER]}},
        {"applications": {"includeAuthenticationContextClassReferences": ["c2"]}},
    ):
        p = protected_policy()
        p["conditions"].update(changed)
        policies.append(p)
    for changed in (
        {"operator": "OR", "builtInControls": ["mfa", "compliantDevice"]},
        {"operator": "AND", "builtInControls": ["compliantDevice"]},
        {"operator": "AND", "builtInControls": [], "authenticationStrength": {"id": OTHER}},
    ):
        policies.append({**protected_policy(), "grantControls": changed})
    for p in policies:
        with pytest.raises(module("confirmation").ConfirmationUnavailable):
            ca.verify_configuration(profile, USER, context, p)
    with pytest.raises(module("confirmation").ConfirmationUnavailable):
        ca.verify_configuration(profile, USER, {"id": "c1", "isAvailable": False}, protected_policy())


@pytest.mark.parametrize("case", ["valid", "missing-context", "unprotected", "unavailable", "drift"])
def test_entra_confirmation_verifies_claim_and_live_configuration_at_consume(case):
    async def run():
        h = await configure_confirmation(await Harness().initialize(), profile={
            "kind": "entra-ca", "authentication_context": "c1",
            "conditional_access_policy_id": OTHER, "capability": "ca-mfa"})
        policy = protected_policy()
        async def graph(request):
            if case == "unavailable":
                return httpx.Response(403, json={"error": "private_graph_details"})
            return httpx.Response(200, json=(
                {"id": "c1", "isAvailable": True} if "authenticationContextClassReferences" in str(request.url)
                else policy))
        graph_http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
        h.service.confirmation.provider = module("confirmation").ExplicitConsentProvider(
            module("confirmation_entra").EntraConditionalAccess(
                tenant=TENANT, issuer=h.settings.issuer, credential=Credential(), http=graph_http))
        try:
            health = await h.client.get("/confirmation/health?provider_profile=email-basic", headers=h.headers())
            assert health.status_code == (503 if case == "unavailable" else 200)
            intent = await pending_intent(h)
            if case == "unprotected":
                policy["grantControls"]["builtInControls"] = ["compliantDevice"]
            headers = user_headers(h, changes={"acrs": [] if case == "missing-context" else ["c1"]})
            response = await h.client.post("/confirmation/" + intent.confirmation_id, headers=headers,
                json={"intent_digest": module("confirmation").digest(intent), "approved": True})
            expected = 401 if case == "missing-context" else 503 if case in ("unprotected", "unavailable") else 200
            assert response.status_code == expected, response.text
            if case == "missing-context":
                assert 'error="insufficient_claims"' in response.headers["www-authenticate"]
            if case in ("valid", "drift"):
                if case == "drift":
                    policy["state"] = "disabled"
                consumed = await h.client.post("/confirmation/resolve", headers=h.headers(),
                    json={"operation": "consume", "intent": intent.model_dump(mode="json")})
                assert consumed.status_code == (200 if case == "valid" else 503), consumed.text
            assert "private_graph_details" not in response.text and not h.store.docs
        finally:
            await graph_http.aclose()
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("mutate", [None, "wrong-subject", "wrong-issuer", "wrong-intent", "expired", "forged"])
def test_customer_provider_requires_actual_signed_transaction_result(mutate):
    async def run():
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        h = await Harness().initialize()
        config = {"issuer": "https://customer.example", "audience": "confirmation-provider",
                  "client_id": "customer-web", "scope": "confirm",
                  "key_id": "customer-key", "public_key": h.key.public_key().public_bytes(
                      Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()}
        await configure_confirmation(h, profile={"kind": "customer-signed", "result_verifier": config})
        try:
            intent = await pending_intent(h)
            now = int(datetime.now(timezone.utc).timestamp())
            claims = {"iss": config["issuer"], "sub": USER, "aud": config["audience"],
                "azp": config["client_id"], "scp": config["scope"], "iat": now - 1,
                "nbf": now - 1, "exp": now + 90, "requester_issuer": h.settings.issuer,
                "intent_digest": module("confirmation").digest(intent),
                "confirmation_id": intent.confirmation_id, "approved": True,
                "capability": "authenticated-consent"}
            if mutate == "wrong-subject":
                claims["sub"] = OTHER
            if mutate == "wrong-issuer":
                claims["iss"] = "https://forged.example"
            if mutate == "wrong-intent":
                claims["intent_digest"] = "sha256:" + "f" * 64
            if mutate == "expired":
                claims["exp"] = now - 1
            token = jwt.encode(claims, h.key, algorithm="RS256", headers={"kid": "customer-key"})
            if mutate == "forged":
                token = "forged"
            response = await h.client.post("/confirmation/" + intent.confirmation_id,
                headers=user_headers(h), json={"intent_digest": module("confirmation").digest(intent),
                                               "approved": True, "provider_result": token})
            assert response.status_code == (200 if mutate is None else 401), response.text
            assert token not in json.dumps(list(h.confirmation_store.docs.values()))
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_ephemeral_store_enforces_bounded_ttl_and_cas_on_actual_sdk_seam():
    async def run():
        properties = {"partitionKey": {"paths": ["/scope"]}, "defaultTtl": 3600}
        docs = SimpleNamespace(read=AsyncMock(return_value=properties),
            create_item=AsyncMock(), replace_item=AsyncMock())
        store = module("confirmation").EphemeralConfirmationStore(None, docs,
            account_reader=AsyncMock(return_value=SimpleNamespace(WritableLocations=[{}])))
        body = {"expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
        await store.create("scope", "key", body)
        item = docs.create_item.call_args.kwargs["body"]
        assert 0 < item["ttl"] <= 60
        await store.replace("scope", "key", body, "etag-1")
        assert docs.replace_item.call_args.kwargs["etag"] == "etag-1"
        assert docs.replace_item.call_args.kwargs["body"]["body"] == body
        for ttl in (-1, None, 3601, True):
            properties["defaultTtl"] = ttl
            with pytest.raises(RuntimeError):
                await store.health()
    asyncio.run(run())


def test_customer_identity_registration_accepts_generic_subject_not_forwarded_identity():
    async def run():
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        h = await Harness().initialize()
        auth = {"issuer": "https://customer.example", "audience": "confirmation-api",
                "client_id": "customer-web", "scope": "confirm", "key_id": "customer-key",
                "public_key": h.key.public_key().public_bytes(
                    Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()}
        await configure_confirmation(h, profile={"customer_identity": auth,
            "users": [{"issuer": auth["issuer"], "subject": "customer:alice",
                       "client": auth["client_id"], "delivery_ref": "customer-alice"}]})
        try:
            now = int(datetime.now(timezone.utc).timestamp())
            claims = {"iss": auth["issuer"], "sub": "customer:alice", "aud": auth["audience"],
                      "azp": auth["client_id"], "scp": "confirm", "iat": now - 1,
                      "nbf": now - 1, "exp": now + 300}
            token = jwt.encode(claims, h.key, algorithm="RS256", headers={"kid": "customer-key"})
            from test_control_plane import WORKLOAD, APP
            body = {"provider_profile": "email-basic", "workload": WORKLOAD, "client": APP,
                    "agent_id": "agent-1", "action": "refund", "operation_id": "customer-operation",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()}
            response = await h.client.post("/confirmation/contexts",
                headers={"Authorization": "Bearer " + token}, json=body)
            assert response.status_code == 200, response.text
            forged = await h.client.post("/confirmation/contexts",
                headers={**h.headers(), "x-ms-client-principal": token}, json={**body, "operation_id": "forged"})
            assert forged.status_code == 401
            assert not h.notifications and not h.store.docs
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())
