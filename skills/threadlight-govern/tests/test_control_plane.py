"""Offline service tests: real RSA JWTs, mocked trusted JWKS and SDK protocols."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "skills/threadlight-govern/references/control-plane"
TENANT = "11111111-1111-1111-1111-111111111111"
WORKLOAD = "22222222-2222-2222-2222-222222222222"
HUMAN = "33333333-3333-3333-3333-333333333333"
APP = "44444444-4444-4444-4444-444444444444"
UI = "55555555-5555-5555-5555-555555555555"
OTHER = "66666666-6666-6666-6666-666666666666"
KEY = "https://test.vault.azure.net/keys/bundle/" + "a" * 32
DIGEST = "sha256:" + "a" * 64


def module(name):
    assert (SERVICE / "app.py").exists(), "Task8 control-plane implementation missing"
    if "govern_control_plane" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "govern_control_plane", SERVICE / "__init__.py",
            submodule_search_locations=[str(SERVICE)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
    return __import__(f"govern_control_plane.{name}", fromlist=[name])


def run(coro):
    return asyncio.run(coro)


def timestamp(seconds=120):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def intent():
    return dict(action_hash=DIGEST, policy_hash=DIGEST, principal=WORKLOAD,
                agent_id="agent-1", session_id="session-1", context_identity="context-1",
                nonce="b" * 32, tenant=TENANT, expires_at=timestamp(),
                policy_expires_at=timestamp(600), allowed_roles=["Approver"])


def receipt():
    return dict(receipt_id="c" * 32, correlation_id="correlation-1", action_id="act",
                action_hash=DIGEST, policy_digest=DIGEST, decision="allow",
                reason_code="threadlight:approved", agent_version="1.0",
                image_digest=DIGEST, recorded_at=timestamp(-1))


class MemoryStore:
    """Same read/create/CAS protocol as AzureStore; shared by independent apps."""
    def __init__(self):
        self.blobs = {}
        self.docs = {}
        self.etag = 0
        self.failed = False

    def check(self):
        if self.failed:
            raise OSError("PRIVATE BACKEND SECRET")

    async def health(self):
        self.check()

    async def blob_create(self, name, body):
        self.check()
        if name in self.blobs:
            raise module("storage").Conflict()
        self.blobs[name] = bytes(body)

    async def blob_read(self, name):
        self.check()
        if name not in self.blobs:
            raise module("storage").Missing()
        return self.blobs[name]

    async def read(self, scope, key):
        self.check()
        if (scope, key) not in self.docs:
            raise module("storage").Missing()
        body, etag = self.docs[(scope, key)]
        return deepcopy(body), etag

    async def create(self, scope, key, body):
        self.check()
        if (scope, key) in self.docs:
            raise module("storage").Conflict()
        self.etag += 1
        self.docs[(scope, key)] = deepcopy(body), str(self.etag)

    async def replace(self, scope, key, body, etag):
        self.check()
        await asyncio.sleep(0)  # Allow competing independent workers to read the same ETag.
        if self.docs[(scope, key)][1] != etag:
            raise module("storage").Conflict()
        self.etag += 1
        self.docs[(scope, key)] = deepcopy(body), str(self.etag)


class TestSigner:
    __test__ = False

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def sign(self, digest):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, utils
        return self.key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))

    async def verify(self, digest, signature):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, utils
        try:
            self.key.public_key().verify(
                signature, digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False

    async def health(self):
        pass


class Harness:
    def __init__(self):
        import httpx
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid="test-key", use="sig", alg="RS256")
        self.jwks = {"keys": [jwk]}
        self.urls = []

        def jwks_transport(request):
            self.urls.append(str(request.url))
            return httpx.Response(200, json=self.jwks)

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(jwks_transport))
        self.settings = module("auth").Settings(
            tenant_id=TENANT, audience="api://governance", key_id=KEY,
            workloads={WORKLOAD: {"client_id": APP, "agent_id": "agent-1", "policies": ["safe"]},
                       OTHER: {"client_id": APP, "agent_id": "agent-2", "policies": ["other"]}},
            human_clients=[UI], approver_subjects=[HUMAN], auditor_subjects=[HUMAN],
            approver_roles=["Approver"],
        )
        self.store = MemoryStore()
        self.signer = TestSigner()
        self.auth = module("auth").EntraAuth(self.settings, self.http)
        self.service = module("app").ControlPlane(self.settings, self.store, self.signer)
        self.app = module("app").create_app(service=self.service, auth=self.auth)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url="https://control.example")
        self.wire_intent = intent()

    async def initialize(self):
        envelope = dict(policy_id="safe", version="1", content_digest=DIGEST,
                        expires_at=self.wire_intent["policy_expires_at"],
                        tenant_id=TENANT, key_id=KEY)
        await self.service.publish(module("models").BundleEnvelope.model_validate_json(
            json.dumps(envelope)))
        return self

    async def close(self):
        await self.client.aclose()
        await self.http.aclose()

    def token(self, *, human=False, changes=None, headers=None):
        import jwt
        now = datetime.now(timezone.utc).timestamp()
        claims = dict(iss=f"https://login.microsoftonline.com/{TENANT}/v2.0",
                      aud="api://governance", tid=TENANT, ver="2.0",
                      oid=HUMAN if human else WORKLOAD, azp=UI if human else APP,
                      exp=int(now + 300), nbf=int(now - 5), iat=int(now - 5),
                      roles=["Approver", "Governance.Auditor"] if human else ["Governance.Workload"])
        claims.update({"scp": "Governance.Approve Governance.Read"} if human else {"idtyp": "app"})
        claims.update(changes or {})
        return jwt.encode(claims, self.key, algorithm="RS256",
                          headers=headers or {"kid": "test-key"})

    def headers(self, **kwargs):
        return {"Authorization": f"Bearer {self.token(**kwargs)}"}

    async def post(self, operation, *, human=False, **fields):
        return await self.client.post("/approvals/resolve", headers=self.headers(human=human),
                                      json={"operation": operation, **fields})

    async def decided(self, *, approved=True):
        assert (await self.post("request", intent=self.wire_intent)).status_code == 202
        response = await self.post("decide", human=True, intent=self.wire_intent,
                                   approved=approved, approving_role="Approver")
        assert response.status_code == 200, response.text
        return response.json()["grant"]


def test_immutable_bundle_lookup_returns_authenticated_digest():
    async def scenario():
        h = await Harness().initialize()
        try:
            response = await h.client.get("/bundles/safe/1", headers=h.headers())
            assert response.status_code == 200
            assert response.json()["envelope"]["content_digest"] == DIGEST
            with pytest.raises(module("storage").Conflict):
                envelope = response.json()["envelope"]
                await h.service.publish(module("models").BundleEnvelope.model_validate_json(
                    json.dumps(envelope)))
            assert (await h.client.get("/bundles/safe/1", headers=h.headers(
                changes={"oid": OTHER}))).status_code == 403
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("fault", ["healthy", "store", "auth", "human", "workload-role", "timeout"])
def test_authenticated_health_checks_real_store_and_workload_without_writes(fault):
    async def scenario():
        h = await Harness().initialize()
        before = deepcopy((h.store.docs, h.store.blobs, h.store.etag))
        headers, expected = h.headers(), 200
        if fault == "store":
            h.store.failed, expected = True, 503
        elif fault == "auth":
            headers, expected = h.headers(changes={"aud": "api://wrong"}), 401
        elif fault == "human":
            headers, expected = h.headers(human=True), 403
        elif fault == "workload-role":
            headers, expected = h.headers(changes={"roles": []}), 401
        elif fault == "timeout":
            async def slow_store():
                await asyncio.sleep(60)
            h.store.health = slow_store
            h.service.settings = h.settings.model_copy(update={"request_timeout": 0.05})
            expected = 503
        try:
            response = await h.client.get("/health", headers=headers)
            assert response.status_code == expected
            assert "PRIVATE" not in response.text
            assert (h.store.docs, h.store.blobs, h.store.etag) == before
            h.store.failed = False
            h.store.health = MemoryStore.health.__get__(h.store)
            assert (await h.client.get("/health", headers=h.headers())).json() == {
                "status": "healthy", "authenticated": True}
            assert (await h.client.get("/health")).status_code == 200
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("kind", ["approval", "receipt"])
@pytest.mark.parametrize("fault", ["healthy", "store", "audience", "human", "credential", "timeout"])
def test_task8_real_client_health_fails_closed_and_recovers_without_effects(kind, fault):
    async def scenario():
        from test_gateway import Credential, gateway
        h = await Harness().initialize()
        cls = (module("client").ApprovalClient if kind == "approval"
               else gateway("receipts").ReceiptClient)
        kwargs = ({"intent_type": module("models").ApprovalRequest,
                   "grant_type": module("models").ApprovalGrant, "poll_interval": 0.001}
                  if kind == "approval" else {})
        client = cls(base_url="https://control.example", scope="api://governance/.default",
                     credential=Credential(h.token()), http=h.client, timeout=0.05, **kwargs)
        before = deepcopy((h.store.docs, h.store.blobs, h.store.etag))
        requests = []
        async def observed(request):
            requests.append((request.method, request.url.path))
        h.client.event_hooks["request"].append(observed)
        if fault == "store":
            h.store.failed = True
        elif fault == "audience":
            client.credential = Credential(h.token(changes={"aud": "api://wrong"}))
        elif fault == "human":
            client.credential = Credential(h.token(human=True))
        elif fault == "credential":
            async def unavailable():
                raise OSError("PRIVATE CREDENTIAL FAILURE")
            client.credential = Credential(hook=unavailable)
        elif fault == "timeout":
            async def timeout():
                await asyncio.sleep(60)
            client.credential = Credential(hook=timeout)
        try:
            async with asyncio.timeout(2):
                assert await client.health() is (fault == "healthy")
            client.credential, h.store.failed = Credential(h.token()), False
            assert await client.health() is True
            assert requests and set(requests) == {("GET", "/health")}
            assert (h.store.docs, h.store.blobs, h.store.etag) == before
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("response", [
    {"status": "healthy"}, {"status": "healthy", "authenticated": False},
    {"status": "healthy", "authenticated": "true"}, {"status": "unhealthy", "authenticated": True},
])
def test_task8_client_health_rejects_anonymous_or_invalid_readiness_response(response):
    async def scenario():
        import httpx
        from test_gateway import Credential
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response))) as http:
            async with module("client").ServiceTransport(
                    base_url="https://control.example", scope="api://governance/.default",
                    credential=Credential(), http=http) as client:
                assert await client.health() is False
    run(scenario())


@pytest.mark.parametrize("changes,claims,expected", [
    ({}, {}, 200),
    ({"agent_id": "wrong-agent"}, {}, 403),
    ({"principal": OTHER}, {}, 403),
    ({"tenant": OTHER}, {}, 403),
    ({"allowed_roles": ["NotConfigured"]}, {}, 403),
    ({}, {"oid": OTHER}, 403),
    ({}, {"azp": OTHER}, 401),
    ({}, {"aud": "api://wrong"}, 401),
    ({}, {"tid": OTHER}, 401),
    ({}, {"scp": "Governance.Read"}, 401),
    ({}, {"roles": []}, 401),
    ({"agent_id": "bad/id"}, {}, 422),
    ({"allowed_roles": ["Approver", "Approver"]}, {}, 422),
])
def test_health_approval_context_uses_request_authority_without_effects(changes, claims, expected):
    async def scenario():
        h = await Harness().initialize()
        context = {key: h.wire_intent[key] for key in (
            "principal", "tenant", "agent_id", "allowed_roles")}
        context.update(changes)
        before = deepcopy((h.store.docs, h.store.blobs, h.store.etag))
        try:
            response = await h.client.get("/health", headers=h.headers(changes=claims),
                                          params={"approval_context": json.dumps(context)})
            assert response.status_code == expected
            assert response.json() == (
                {"status": "healthy", "authenticated": True, "approval_context_validated": True}
                if expected == 200 else {"error": {
                    401: "unauthorized", 403: "forbidden", 422: "invalid_request"}[expected]})
            assert (h.store.docs, h.store.blobs, h.store.etag) == before
            # Readiness is read-only; compare with the actual request, including its 202.
            operation = await h.client.post("/approvals/resolve",
                headers=h.headers(changes=claims),
                json={"operation": "request", "intent": {**h.wire_intent, **context}})
            assert operation.status_code == (202 if expected == 200 else expected)
            if expected != 200:
                assert (h.store.docs, h.store.blobs, h.store.etag) == before
        finally:
            await h.close()
    run(scenario())


def test_health_approval_context_requires_authentication():
    async def scenario():
        h = await Harness().initialize()
        try:
            response = await h.client.get("/health", params={"approval_context": "{}"})
            assert response.status_code == 401
            assert response.json() == {"error": "unauthorized"}
            assert not h.store.docs
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("response", [
    {"status": "healthy", "authenticated": True},
    {"status": "healthy", "authenticated": True, "approval_context_validated": False},
    {"status": "healthy", "authenticated": True, "approval_context_validated": "true"},
])
def test_task8_client_health_requires_context_validation_ack(response):
    async def scenario():
        import httpx
        from test_gateway import Credential
        context = {key: intent()[key] for key in (
            "principal", "tenant", "agent_id", "allowed_roles")}
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response))) as http:
            async with module("client").ServiceTransport(
                    base_url="https://control.example", scope="api://governance/.default",
                    credential=Credential(), http=http) as client:
                assert await client.health(approval_context=context) is False
    run(scenario())


@pytest.mark.parametrize("backend", ["blob-container", "cosmos-container", "account"])
def test_authenticated_health_adapter_missing_is_503_but_bundle_missing_remains_404(backend):
    async def scenario():
        from azure.core.exceptions import HttpResponseError
        from azure.cosmos.aio import ContainerProxy
        from azure.storage.blob.aio import ContainerClient
        h = Harness()
        documents = create_autospec(ContainerProxy, instance=True)
        documents.read.return_value = {"partitionKey": {"paths": ["/scope"]}}
        blobs = create_autospec(ContainerClient, instance=True)
        account = AsyncMock(return_value=SimpleNamespace(WritableLocations=[{"name": "one"}]))
        store = module("storage").AzureStore(blobs, documents, account_reader=account)
        h.service.store = store
        missing = HttpResponseError(message="PRIVATE SDK DETAILS", response=SimpleNamespace(
            status_code=404, reason="PRIVATE", headers={}))
        failed = {"blob-container": blobs.get_container_properties,
                  "cosmos-container": documents.read, "account": account}[backend]
        try:
            failed.side_effect = missing
            with pytest.raises(module("storage").Missing):
                await store.health()
            response = await h.client.get("/health", headers=h.headers())
            assert response.status_code == 503
            assert response.json() == {"error": "unavailable"}
            assert (await h.client.get("/health")).status_code == 503
            failed.side_effect = None
            assert (await h.client.get("/health", headers=h.headers())).json() == {
                "status": "healthy", "authenticated": True}
            blobs.download_blob.side_effect = missing
            response = await h.client.get("/bundles/safe/missing", headers=h.headers())
            assert response.status_code == 404
            assert response.json() == {"error": "not_found"}
            for method in (blobs.upload_blob, documents.create_item, documents.replace_item):
                method.assert_not_called()
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("backend", ["store", "signer", "auth"])
@pytest.mark.parametrize("failure", ["Missing", "Conflict", "Unauthorized", "ValueError"])
def test_authenticated_health_backend_errors_are_always_unavailable(backend, failure):
    async def scenario():
        h = Harness()
        errors = {"Missing": module("storage").Missing, "Conflict": module("storage").Conflict,
                  "Unauthorized": module("auth").Unauthorized, "ValueError": ValueError}
        getattr(h, backend).health = AsyncMock(side_effect=errors[failure]("PRIVATE"))
        try:
            response = await h.client.get("/health", headers=h.headers())
            assert response.status_code == 503
            assert response.json() == {"error": "unavailable"}
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("approved", [True, False])
def test_human_decision_consumes_once_and_replay_conflicts(approved):
    async def scenario():
        h = await Harness().initialize()
        try:
            grant = await h.decided(approved=approved)
            assert grant["approver"] == HUMAN and grant["approved"] is approved
            resolved = await h.post("resolve", intent=h.wire_intent)
            assert resolved.json()["grant"] == grant
            assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 200
            assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 409
            assert (await h.post("request", intent=h.wire_intent)).status_code == 409
            h.wire_intent["nonce"] = "d" * 32
            fresh = await h.decided()
            assert (await h.post("consume", intent=h.wire_intent, grant=fresh)).status_code == 200
        finally:
            await h.close()
    run(scenario())


def test_receipts_require_workload_and_exact_idempotent_owner_scope():
    async def scenario():
        h = await Harness().initialize()
        try:
            body = receipt()
            assert (await h.client.post("/receipts", json=body)).status_code == 401
            assert (await h.client.post("/receipts", json=body, headers={
                "x-ms-client-principal": "UNTRUSTED"})).status_code == 401
            for _ in range(2):
                assert (await h.client.post("/receipts", json=body, headers=h.headers())).status_code == 200
            changed = dict(body, decision="deny")
            assert (await h.client.post("/receipts", json=changed, headers=h.headers())).status_code == 409
            path = f"/receipts/{body['receipt_id']}"
            assert (await h.client.get(path, headers=h.headers())).json() == body
            assert (await h.client.get(path, headers=h.headers(changes={"oid": OTHER}))).status_code == 403
            assert (await h.client.get(path, headers=h.headers(human=True))).status_code == 200
            assert (await h.client.post("/receipts", json=body, headers=h.headers(human=True))).status_code == 403
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("field,value", [
    ("content_digest", "sha256:" + "f" * 64), ("expires_at", "2099-01-01T00:00:00+00:00"),
    ("tenant_id", OTHER), ("policy_id", "other"), ("version", "2"),
    ("key_id", KEY[:-1] + "b"),
])
def test_tampered_signed_bundle_metadata_conflicts(field, value):
    async def scenario():
        h = await Harness().initialize()
        try:
            key = next(k for k in h.store.blobs if "/policies/" in k)
            document = json.loads(h.store.blobs[key])
            document["envelope"][field] = value
            h.store.blobs[key] = json.dumps(document).encode()
            response = await h.client.get("/bundles/safe/1", headers=h.headers())
            assert response.status_code == 409
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("changes,headers", [
    ({"aud": "wrong"}, None), ({"iss": "https://attacker.example"}, None),
    ({"tid": OTHER}, None), ({"exp": 1}, None), ({"nbf": 4102444800}, None),
    ({"azp": OTHER}, None), ({"oid": HUMAN}, None), ({"roles": []}, None),
    ({"ver": "1.0"}, None), ({}, {"kid": "unknown"}),
    ({}, {"kid": "test-key", "jku": "https://attacker.example/keys"}),
])
def test_entra_rejects_invalid_claims_and_untrusted_key_headers(changes, headers):
    async def scenario():
        h = await Harness().initialize()
        try:
            response = await h.client.get("/bundles/safe/1",
                                         headers=h.headers(changes=changes, headers=headers))
            assert response.status_code == 401
            assert response.json() == {"error": "unauthorized"}
            assert all(url == f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
                       for url in h.urls)
        finally:
            await h.close()
    run(scenario())


def test_pending_no_autoapproval_and_decider_must_be_delegated_authorized_human():
    async def scenario():
        h = await Harness().initialize()
        try:
            for _ in range(2):
                assert (await h.post("request", intent=h.wire_intent)).status_code == 202
            assert (await h.post("resolve", intent=h.wire_intent)).status_code == 202
            assert (await h.post("decide", intent=h.wire_intent, approved=True,
                                approving_role="Approver")).status_code == 403
            changed = dict(h.wire_intent, action_hash="sha256:" + "1" * 64)
            assert (await h.post("request", intent=changed)).status_code == 409
            for claims in ({"scp": ""}, {"roles": []}, {"oid": OTHER}, {"idtyp": "app"}):
                response = await h.client.post("/approvals/resolve",
                    headers=h.headers(human=True, changes=claims),
                    json=dict(operation="decide", intent=h.wire_intent,
                              approved=True, approving_role="Approver"))
                assert response.status_code in (401, 403)
            granted = await h.decided()
            assert (await h.post("decide", human=True, intent=h.wire_intent, approved=False,
                                approving_role="Approver")).status_code == 409
            assert granted["approved"] is True
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("field,value", [
    ("principal", HUMAN), ("tenant", OTHER), ("agent_id", "agent-2"),
    ("allowed_roles", ["Untrusted"]), ("policy_hash", "sha256:" + "2" * 64),
    ("policy_expires_at", "2099-01-01T00:00:00Z"), ("expires_at", "2000-01-01T00:00:00Z"),
])
def test_request_cannot_claim_scope_or_unsigned_policy_expiry(field, value):
    async def scenario():
        h = await Harness().initialize()
        try:
            changed = dict(h.wire_intent, **{field: value})
            assert (await h.post("request", intent=changed)).status_code in (403, 409)
            assert not h.store.docs
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("field,value", [
    ("approved", False), ("approver", WORKLOAD), ("approver_tenant", OTHER),
    ("approver_role", "Untrusted"), ("provenance", "f" * 32),
])
def test_full_grant_mutations_cannot_consume(field, value):
    async def scenario():
        h = await Harness().initialize()
        try:
            grant = await h.decided()
            forged = dict(grant, **{field: value})
            assert (await h.post("consume", intent=h.wire_intent, grant=forged)).status_code == 409
            assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 200
        finally:
            await h.close()
    run(scenario())


def test_two_independent_apps_share_atomic_consume_and_outages_fail_closed():
    async def scenario():
        import httpx
        h = await Harness().initialize()
        second_service = module("app").ControlPlane(h.settings, h.store, h.signer)
        second_app = module("app").create_app(service=second_service, auth=h.auth)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app),
                                     base_url="https://control.example") as second:
            try:
                grant = await h.decided()
                body = dict(operation="consume", intent=h.wire_intent, grant=grant)
                responses = await asyncio.gather(*[
                    client.post("/approvals/resolve", json=body, headers=h.headers())
                    for client in (h.client, second)])
                assert sorted(r.status_code for r in responses) == [200, 409]
                h.wire_intent["nonce"] = "e" * 32
                grant = await h.decided()
                h.store.failed = True
                response = await h.post("consume", intent=h.wire_intent, grant=grant)
                assert response.status_code == 503 and "PRIVATE" not in response.text
                assert (await h.client.get("/health")).status_code == 503
                h.store.failed = False
                assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 200
            finally:
                await h.close()
    run(scenario())


def test_validation_is_payload_free_bounded_and_duplicate_json_is_rejected():
    async def scenario():
        h = await Harness().initialize()
        try:
            for body in (dict(receipt(), prompt="PRIVATE"), dict(receipt(), decision=1),
                         dict(receipt(), reason_code="PRIVATE " * 1000),
                         dict(receipt(), recorded_at="2026-01-01T00:00:00")):
                response = await h.client.post("/receipts", json=body, headers=h.headers())
                assert response.status_code == 422
                assert response.json() == {"error": "invalid_request"}
            for body in ('{"receipt_id":"PRIVATE","receipt_id":"duplicate"}', '{"secret":NaN}'):
                response = await h.client.post("/receipts", content=body, headers=h.headers())
                assert response.status_code == 422 and "PRIVATE" not in response.text
            async def chunks():
                for _ in range(20):
                    yield b"x" * 1024
            response = await h.client.post("/receipts", content=chunks(), headers=h.headers())
            assert response.status_code == 413
            assert not h.store.docs
            paths = {route.path for route in h.app.routes}
            assert paths == {"/health", "/bundles/{policy_id}/{version}",
                             "/approvals/resolve", "/receipts", "/receipts/{receipt_id}"}
        finally:
            await h.close()
    run(scenario())


def test_runtime_protocol_client_uses_bearer_and_remote_consume_not_self_echo():
    async def scenario():
        h = await Harness().initialize()
        spec = importlib.util.spec_from_file_location(
            "task8_runtime_evidence", SERVICE.parent / "runtime/evidence.py")
        evidence = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = evidence
        spec.loader.exec_module(evidence)
        credential = SimpleNamespace(get_token=AsyncMock(
            return_value=SimpleNamespace(token=h.token(), expires_on=4102444800)))
        client = module("client").ApprovalClient(
            base_url="https://control.example", scope="api://governance/.default",
            credential=credential, http=h.client, intent_type=evidence.ApprovalIntent,
            grant_type=evidence.ApprovalGrant, poll_interval=0.001, timeout=1)
        wire = h.wire_intent
        native = evidence.ApprovalIntent(**{
            **wire, "allowed_roles": tuple(wire["allowed_roles"]),
            "expires_at": datetime.fromisoformat(wire["expires_at"]),
            "policy_expires_at": datetime.fromisoformat(wire["policy_expires_at"])})
        effects = []
        try:
            resolving = asyncio.create_task(client.resolve(native))
            for _ in range(100):
                if h.store.docs:
                    break
                await asyncio.sleep(0.001)
            assert h.store.docs and not resolving.done()
            await h.post("decide", human=True, intent=wire, approved=True, approving_role="Approver")
            grant = await resolving
            assert isinstance(grant, evidence.ApprovalGrant)
            assert not await client.verify(replace(grant, approver=WORKLOAD), intent=native)
            h.store.failed = True
            if await client.verify(grant, intent=native):
                effects.append("unsafe")
            h.store.failed = False
            assert await client.verify(grant, intent=native)
            effects.append("approved")
            if await client.verify(grant, intent=native):
                effects.append("replay")
            assert effects == ["approved"]
            credential.get_token.assert_awaited_with("api://governance/.default")
        finally:
            await h.close()
    run(scenario())


def test_azure_adapters_use_sdk_etags_no_overwrite_and_digest_signatures():
    async def scenario():
        from azure.core import MatchConditions
        from azure.cosmos.aio import ContainerProxy
        from azure.storage.blob.aio import ContainerClient
        from azure.keyvault.keys.crypto.aio import CryptographyClient
        from azure.keyvault.keys.crypto import SignatureAlgorithm
        container = create_autospec(ContainerProxy, instance=True)
        container.read.return_value = {"partitionKey": {"paths": ["/scope"]}}
        blob = create_autospec(ContainerClient, instance=True)
        crypto = create_autospec(CryptographyClient, instance=True)
        store = module("storage").AzureStore(blob, container, account_reader=AsyncMock(
            return_value=SimpleNamespace(WritableLocations=[{"name": "one"}])))
        body = {"owner": WORKLOAD, "state": "decided"}
        container.read_item.return_value = {
            "id": "approval:n", "scope": TENANT, "body": body, "_etag": '"v1"'}
        got, etag = await store.read(TENANT, "approval:n")
        assert got == body and etag == '"v1"'
        container.read_item.assert_awaited_once_with(item="approval:n", partition_key=TENANT)
        await store.replace(TENANT, "approval:n", body, etag)
        call = container.replace_item.call_args
        assert call.kwargs["etag"] == '"v1"'
        assert call.kwargs["match_condition"] == MatchConditions.IfNotModified
        assert call.kwargs["body"]["scope"] == TENANT
        assert call.kwargs["item"] == "approval:n"
        await store.blob_create("tenant/policies/safe/1.json", b'{"signed":true}')
        assert blob.upload_blob.call_args.kwargs["overwrite"] is False
        assert blob.upload_blob.call_args.kwargs["data"] == b'{"signed":true}'
        signer = module("storage").KeyVaultSigner(crypto)
        digest = hashlib.sha256(b"canonical-envelope").digest()
        crypto.sign.return_value = SimpleNamespace(signature=b"signature")
        crypto.verify.return_value = SimpleNamespace(is_valid=True)
        assert await signer.sign(digest) == b"signature"
        assert await signer.verify(digest, b"signature")
        crypto.sign.assert_awaited_once_with(SignatureAlgorithm.rs256, digest)
        crypto.verify.assert_awaited_once_with(SignatureAlgorithm.rs256, digest, b"signature")
    run(scenario())


def test_signed_policy_snapshot_matches_task7_verified_policy_and_expires():
    async def scenario():
        from dataclasses import dataclass
        h = await Harness().initialize()

        @dataclass(frozen=True)
        class VerifiedPolicy:
            digest: str
            expires_at: datetime

        credential = SimpleNamespace(get_token=AsyncMock(
            return_value=SimpleNamespace(token=h.token())))
        try:
            client = module("client").PolicyClient(
                base_url="https://control.example", scope="api://governance/.default",
                credential=credential, http=h.client, tenant=TENANT, key_id=KEY,
                verified_policy_type=VerifiedPolicy)
            verifier = await client.load("safe", "1", expected_digest=DIGEST)
            bundle = SimpleNamespace(bundle_digest=DIGEST)
            trusted = verifier.verify(bundle)
            assert type(trusted) is VerifiedPolicy and trusted.digest == DIGEST
            assert trusted.expires_at == datetime.fromisoformat(h.wire_intent["policy_expires_at"])
            with pytest.raises(ValueError):
                verifier.verify(SimpleNamespace(bundle_digest="sha256:" + "f" * 64))
            verifier.now = lambda: trusted.expires_at + timedelta(seconds=1)
            with pytest.raises(ValueError):
                verifier.verify(bundle)
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("phase", ["pending", "decided", "write"])
def test_expiry_is_checked_at_server_clock_including_after_durable_write(phase):
    async def scenario():
        h = await Harness().initialize()
        try:
            if phase == "pending":
                h.service.now = lambda: datetime.fromisoformat(h.wire_intent["policy_expires_at"])
                assert (await h.post("request", intent=h.wire_intent)).status_code == 409
                assert not h.store.docs
                return
            grant = await h.decided()
            expired = datetime.fromisoformat(h.wire_intent["expires_at"]) + timedelta(seconds=1)
            if phase == "write":
                original = h.store.replace
                async def write(*args):
                    await original(*args)
                    h.service.now = lambda: expired
                h.store.replace = write
            else:
                h.service.now = lambda: expired
            assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 409
        finally:
            await h.close()
    run(scenario())


def test_tampered_signature_substitution_and_partial_publication_fail_closed():
    async def scenario():
        h = await Harness().initialize()
        try:
            key = h.service.policy_name("safe", "1")
            original = h.store.blobs[key]
            body = json.loads(original)
            body["signature"] = "A" * 344
            h.store.blobs[key] = json.dumps(body).encode()
            assert (await h.client.get("/bundles/safe/1", headers=h.headers())).status_code == 409
            h.store.blobs[key] = original
            h.store.blobs.pop(h.service.digest_name(DIGEST))
            assert (await h.post("request", intent=h.wire_intent)).status_code == 409
            assert not h.store.docs
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("kind", ["unsigned", "hmac", "wrong-key"])
def test_jwt_signature_and_algorithm_are_not_optional(kind):
    async def scenario():
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        h = await Harness().initialize()
        try:
            claims = jwt.decode(h.token(), options={"verify_signature": False})
            if kind == "unsigned":
                token = jwt.encode(claims, "", algorithm="none", headers={"kid": "test-key"})
            elif kind == "hmac":
                token = jwt.encode(claims, b"x" * 32, algorithm="HS256", headers={"kid": "test-key"})
            else:
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
            response = await h.client.get("/bundles/safe/1", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401 and response.json() == {"error": "unauthorized"}
        finally:
            await h.close()
    run(scenario())


def test_unconfigured_production_has_only_unhealthy_health_and_no_anonymous_fallback(monkeypatch):
    async def scenario():
        import httpx
        monkeypatch.delenv("GOV_CONFIG_FILE", raising=False)
        app = module("app").create_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://control.example") as client:
                assert (await client.get("/health")).status_code == 503
                assert (await client.post("/receipts", json=receipt())).status_code == 503
    run(scenario())


def test_azure_sdk_conflicts_fail_closed_and_health_rejects_unsafe_container():
    async def scenario():
        from azure.core.exceptions import HttpResponseError
        from azure.cosmos.aio import ContainerProxy
        from azure.storage.blob.aio import ContainerClient
        container = create_autospec(ContainerProxy, instance=True)
        container.read.return_value = {"partitionKey": {"paths": ["/scope"]}}
        blobs = create_autospec(ContainerClient, instance=True)
        store = module("storage").AzureStore(blobs, container, account_reader=AsyncMock(
            return_value=SimpleNamespace(WritableLocations=[{"name": "one"}])))
        for code in (409, 412, 429, 503):
            container.replace_item.side_effect = HttpResponseError(
                message="PRIVATE", response=SimpleNamespace(status_code=code, reason="PRIVATE", headers={}))
            with pytest.raises(module("storage").Conflict if code in (409, 412) else RuntimeError):
                await store.replace(TENANT, "approval:n", {}, '"v1"')
        for properties in ({"partitionKey": {"paths": ["/wrong"]}},
                           {"partitionKey": {"paths": ["/scope"]}, "defaultTtl": 60}):
            container.read.return_value = properties
            with pytest.raises(RuntimeError):
                await store.health()
    run(scenario())


def test_production_closes_all_async_clients_and_uses_only_managed_identity(tmp_path, monkeypatch):
    async def scenario():
        import azure.cosmos.aio
        import azure.identity.aio
        import azure.keyvault.keys.crypto.aio
        import azure.keyvault.keys.aio
        import azure.storage.blob.aio
        h = Harness()
        settings = {
            **h.settings.model_dump(mode="json"), "blob_url": "https://test.blob.core.windows.net",
            "blob_container": "bundles", "cosmos_url": "https://test.documents.azure.com:443/",
            "cosmos_database": "govern", "cosmos_container": "records"}
        path = tmp_path / "config.json"
        path.write_text(json.dumps(settings))
        monkeypatch.setenv("GOV_CONFIG_FILE", str(path))
        resources, constructor_calls = {}, {}
        def constructor(name):
            def create(*args, **kwargs):
                constructor_calls[name] = (args, kwargs)
                resource = AsyncMock()
                resource.__aenter__.return_value = resource
                resources[name] = resource
                return resource
            return create
        monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", constructor("identity"))
        monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", constructor("cosmos"))
        monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient", constructor("blob"))
        monkeypatch.setattr(azure.keyvault.keys.crypto.aio, "CryptographyClient", constructor("crypto"))
        monkeypatch.setattr(azure.keyvault.keys.aio, "KeyClient", constructor("keys"))
        original_store = module("app").AzureStore
        try:
            monkeypatch.setattr(module("app"), "AzureStore", lambda *args, **kwargs: h.store)
            monkeypatch.setattr(module("app"), "KeyVaultSigner", lambda *args, **kwargs: h.signer)
            monkeypatch.setattr(module("app"), "EntraAuth", lambda *args: h.auth)
            # Synchronous child-client getters have SDK-shaped mocks, not coroutine fakes.
            from unittest.mock import Mock
            def cosmos(*args, **kwargs):
                value = constructor("cosmos")(*args, **kwargs)
                value.get_database_client = Mock(return_value=SimpleNamespace(get_container_client=Mock()))
                return value
            def blobs(*args, **kwargs):
                value = constructor("blob")(*args, **kwargs)
                value.get_container_client = Mock()
                return value
            monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", cosmos)
            monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient", blobs)
            async with module("app").production():
                pass
            assert all(r.close.await_count == 1 for r in resources.values())
            flags = constructor_calls["identity"][1]
            assert flags["exclude_environment_credential"] is True
            assert flags["exclude_cli_credential"] is True
            assert constructor_calls["crypto"][0][0] == KEY
            assert constructor_calls["cosmos"][1]["credential"] is resources["identity"]
        finally:
            monkeypatch.setattr(module("app"), "AzureStore", original_store)
            await h.close()
    run(scenario())


def test_cosmos_health_rejects_multiwriter_account_and_key_health_checks_current_key():
    async def scenario():
        from azure.cosmos.aio import ContainerProxy
        from azure.storage.blob.aio import ContainerClient
        from azure.keyvault.keys.aio import KeyClient
        from azure.keyvault.keys.crypto.aio import CryptographyClient
        container = create_autospec(ContainerProxy, instance=True)
        container.read.return_value = {"partitionKey": {"paths": ["/scope"]}}
        blobs = create_autospec(ContainerClient, instance=True)
        reader = AsyncMock(return_value=SimpleNamespace(WritableLocations=[{"name": "one"}, {"name": "two"}]))
        store = module("storage").AzureStore(blobs, container, account_reader=reader)
        with pytest.raises(RuntimeError):
            await store.health()
        reader.return_value = SimpleNamespace(WritableLocations=[{"name": "one"}])
        await store.health()
        keys = create_autospec(KeyClient, instance=True)
        crypto = create_autospec(CryptographyClient, instance=True)
        crypto.key_id = KEY
        signer = module("storage").KeyVaultSigner(crypto, key_client=keys)
        keys.get_key.return_value = SimpleNamespace(id=KEY, properties=SimpleNamespace(
            enabled=False, expires_on=None, not_before=None))
        with pytest.raises(RuntimeError):
            await signer.health()
        keys.get_key.return_value.properties.enabled = True
        await signer.health()
        keys.get_key.assert_awaited_with("bundle", "a" * 32)
    run(scenario())


def test_startup_enter_failure_still_closes_already_constructed_sdk_client(tmp_path, monkeypatch):
    async def scenario():
        import azure.cosmos.aio
        import azure.identity.aio
        import azure.storage.blob.aio
        h = Harness()
        settings = {**h.settings.model_dump(mode="json"),
                    "blob_url": "https://test.blob.core.windows.net", "blob_container": "bundles",
                    "cosmos_url": "https://test.documents.azure.com:443/",
                    "cosmos_database": "govern", "cosmos_container": "records"}
        path = tmp_path / "config.json"
        path.write_text(json.dumps(settings))
        monkeypatch.setenv("GOV_CONFIG_FILE", str(path))
        identity, blobs, cosmos = AsyncMock(), AsyncMock(), AsyncMock()
        for obj in (identity, blobs, cosmos):
            obj.__aenter__.return_value = obj
        cosmos.__aenter__.side_effect = OSError("PRIVATE STARTUP FAILURE")
        monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", lambda **kw: identity)
        monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient", lambda *a, **kw: blobs)
        monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", lambda *a, **kw: cosmos)
        try:
            with pytest.raises(OSError):
                async with module("app").production():
                    pytest.fail("unavailable storage must not start")
            cosmos.close.assert_awaited_once()
            identity.close.assert_awaited_once()
            blobs.close.assert_awaited_once()
        finally:
            await h.close()
    run(scenario())


def test_decide_race_and_lost_consume_acknowledgement_never_permit_retry():
    async def scenario():
        h = await Harness().initialize()
        try:
            await h.post("request", intent=h.wire_intent)
            responses = await asyncio.gather(*[
                h.post("decide", human=True, intent=h.wire_intent,
                       approved=approved, approving_role="Approver")
                for approved in (True, False)])
            assert sorted(response.status_code for response in responses) == [200, 409]
            grant = next(response.json()["grant"] for response in responses if response.status_code == 200)
            original = h.store.replace
            async def lost_ack(*args):
                await original(*args)
                raise OSError("PRIVATE LOST ACK")
            h.store.replace = lost_ack
            response = await h.post("consume", intent=h.wire_intent, grant=grant)
            assert response.status_code == 503 and response.json() == {"error": "unavailable"}
            h.store.replace = original
            assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 409
        finally:
            await h.close()
    run(scenario())


def test_jwks_outage_and_validation_logs_do_not_leak_values(caplog):
    async def scenario():
        import httpx
        h = await Harness().initialize()
        try:
            bad = dict(receipt(), raw_arguments="PRIVATE-MODEL-PAYLOAD")
            response = await h.client.post("/receipts", json=bad, headers=h.headers())
            assert response.status_code == 422
            assert "PRIVATE" not in caplog.text + response.text
            async def unavailable(*args):
                raise httpx.ConnectError("PRIVATE-TOKEN-OR-KEY")
            h.auth.keys = {}
            h.http._transport = httpx.MockTransport(unavailable)
            h.auth.refreshed = float("-inf")
            assert (await h.client.get("/bundles/safe/1", headers=h.headers())).status_code == 401
            assert (await h.client.get("/health")).status_code == 503
            assert "PRIVATE" not in caplog.text
        finally:
            await h.close()
    run(scenario())


def test_approval_wire_contract_is_exact_task7_shape():
    definitions = json.loads((SERVICE.parent / "runtime/approval.schema.json").read_text())["definitions"]
    models = module("models")
    assert set(models.ApprovalRequest.model_fields) == set(definitions["intent"]["required"])
    assert set(models.ApprovalGrant.model_fields) == set(definitions["grant"]["required"])


def test_current_signing_key_failure_blocks_policy_and_approval_even_with_cached_crypto():
    async def scenario():
        h = await Harness().initialize()
        try:
            grant = await h.decided()
            async def unavailable():
                raise OSError("PRIVATE KEY FAILURE")
            h.signer.health = unavailable
            assert (await h.client.get("/bundles/safe/1", headers=h.headers())).status_code == 503
            assert (await h.post("consume", intent=h.wire_intent, grant=grant)).status_code == 503
            assert all(body["state"] != "consumed" for body, _ in h.store.docs.values())
        finally:
            await h.close()
    run(scenario())


def test_production_suppresses_dependency_exception_logging(caplog):
    import logging
    module("app").configure_logging()
    try:
        logging.getLogger("azure.identity").warning("PRIVATE SDK ERROR")
        logging.getLogger("httpx").warning("PRIVATE HTTP ERROR")
        assert "PRIVATE" not in caplog.text
    finally:
        for name in ("azure", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.NOTSET)
            logger.propagate = True


def test_shutdown_failure_is_private_and_clears_readiness(monkeypatch, caplog):
    async def scenario():
        from contextlib import asynccontextmanager
        h = Harness()
        @asynccontextmanager
        async def failing_shutdown():
            yield h.service, h.auth
            raise OSError("PRIVATE SHUTDOWN")
        monkeypatch.setattr(module("app"), "production", failing_shutdown)
        app = module("app").create_app()
        try:
            async with app.router.lifespan_context(app):
                assert app.state.service is h.service
            assert app.state.service is None and app.state.auth is None
            assert "PRIVATE" not in caplog.text
        finally:
            await h.close()
    run(scenario())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("case", ["approve", "deny", "pending", "tamper", "outage", "replay"])
def test_real_task7_native_runtime_uses_service_before_effects(tmp_path, case):
    """Published native MAF/ACS/OPA with in-process HTTPS service transport only."""
    async def scenario():
        from agent_framework import FunctionTool
        from test_runtime_provider import build_policy, contract, model_client, runtime, tool_responses
        from test_policy_bundle import bundle_module
        from skills._shared.governance import validate_governance_contract
        native = runtime()
        h = Harness()
        built = build_policy(tmp_path, {"pre_tool_call": {"decision": "escalate"}})
        envelope = module("models").BundleEnvelope.model_validate_json(json.dumps(dict(
            policy_id="safe", version="1", content_digest=built.bundle_digest,
            expires_at=timestamp(600), tenant_id=TENANT, key_id=KEY)))
        await h.service.publish(envelope)
        credential = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token=h.token())))
        client = module("client").ApprovalClient(
            base_url="https://control.example", scope="api://governance/.default",
            credential=credential, http=h.client, intent_type=native.ApprovalIntent,
            grant_type=native.ApprovalGrant, timeout=3, poll_interval=0.01)
        policy = module("client").PolicyClient(
            base_url="https://control.example", scope="api://governance/.default",
            credential=credential, http=h.client, tenant=TENANT, key_id=KEY,
            verified_policy_type=native.VerifiedPolicy)
        verifier = await policy.load("safe", "1", expected_digest=built.bundle_digest)
        original_resolve = client.resolve
        seen = []
        async def resolve(request):
            grant = await original_resolve(request)
            seen.append(grant)
            if case == "tamper":
                return replace(grant, approver=WORKLOAD)
            if case == "outage":
                h.store.failed = True
            if case == "replay":
                return seen[0]
            return grant
        client.resolve = resolve
        provider = native.AcsGovernanceProvider(
            contract=contract(requires=("approval",)), bundle_path=built.root,
            expected_digest=built.bundle_digest, bundle_verifier=bundle_module().verify_bundle,
            signature_verifier=verifier, contract_validator=validate_governance_contract,
            safe_provider=lambda identity: {}, approval_resolver=client, principal=WORKLOAD,
            tenant=TENANT, allowed_approval_roles=("Approver",), timeout=4.0)
        effects = []
        async def effect():
            effects.append("effect")
            return "done"
        model = model_client(tool_responses())
        agent = native.create_governed_agent(
            provider, id="agent-1", client=model, tools=[FunctionTool(name="act", func=effect)])

        async def human_decision():
            # A separate, explicitly delegated test identity represents the human.
            for _ in range(500):
                pending = [body for body, _ in h.store.docs.values() if body.get("state") == "pending"]
                if pending:
                    response = await h.post("decide", human=True, intent=pending[0]["intent"],
                                           approved=case != "deny", approving_role="Approver")
                    assert response.status_code == 200, response.text
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("native runtime did not create an approval intent")
        try:
            for number in range(2 if case == "replay" else 1):
                human = asyncio.create_task(human_decision()) if case != "pending" else None
                await agent.run("perform action")
                if human:
                    await human
                if number == 0 and case == "replay":
                    model.responses.extend(tool_responses())
            assert effects == (["effect"] if case in ("approve", "replay") else [])
            assert "PRIVATE" not in str(model.requests)
            if case == "approve":
                assert any(body["state"] == "consumed" for body, _ in h.store.docs.values())
        finally:
            await h.close()
    run(scenario())
