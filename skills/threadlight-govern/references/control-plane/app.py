"""Five-route reference API with durable human decisions and atomic consumption."""
from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import json
import logging
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
from pydantic import Field, ValidationError

from .auth import EntraAuth, Identity, Settings, Unauthorized
from .models import (
    ApprovalGrant, ApprovalOperation, ApprovalRequest, BundleEnvelope, DecisionReceipt,
    Identifier, Nonce, SignedBundle, canonical, envelope_digest, parse,
)
from .storage import AzureStore, BundleSigner, Conflict, KeyVaultSigner, Missing, Store


class Forbidden(Exception):
    pass


class ControlPlane:
    def __init__(self, settings: Settings, store: Store, signer: BundleSigner):
        self.settings, self.store, self.signer = settings, store, signer

    def now(self):
        return datetime.now(timezone.utc)

    def workload(self, identity: Identity):
        if identity.workload is None:
            raise Forbidden()
        return identity.workload

    def auditor(self, identity):
        return (identity.workload is None
                and identity.subject in self.settings.auditor_subjects
                and "Governance.Auditor" in identity.roles
                and "Governance.Read" in identity.scopes)

    def policy_access(self, identity, policy_id):
        if identity.tenant != self.settings.tenant_id:
            raise Forbidden()
        if identity.workload:
            if policy_id not in identity.workload.policies:
                raise Forbidden()
        elif not self.auditor(identity):
            raise Forbidden()

    def policy_name(self, policy_id, version):
        return f"{self.settings.tenant_id}/policies/{policy_id}/{version}.json"

    def digest_name(self, digest):
        return f"{self.settings.tenant_id}/digests/{digest[7:]}.json"

    async def authenticate_bundle(self, raw):
        try:
            bundle = parse(SignedBundle, raw)
            envelope = bundle.envelope
            await self.signer.health()
            if (envelope.tenant_id != self.settings.tenant_id
                    or envelope.key_id != self.settings.key_id
                    or envelope.expires_at <= self.now()
                    or not await self.signer.verify(
                        envelope_digest(envelope), base64.b64decode(bundle.signature, validate=True))):
                raise Conflict()
            if envelope.expires_at <= self.now():
                raise Conflict()
            return bundle
        except (ValueError, ValidationError):
            raise Conflict() from None

    async def publish(self, envelope: BundleEnvelope):
        """CI/admin backend only. Never changes Task6's integrity-covered files."""
        async with asyncio.timeout(self.settings.request_timeout):
            if (envelope.tenant_id != self.settings.tenant_id
                    or envelope.key_id != self.settings.key_id or envelope.expires_at <= self.now()):
                raise Conflict()
            signature = await self.signer.sign(envelope_digest(envelope))
            signed = SignedBundle(envelope=envelope, signature=base64.b64encode(signature).decode())
            raw = canonical(signed)
            await self.authenticate_bundle(raw)
            await self.store.blob_create(self.policy_name(envelope.policy_id, envelope.version), raw)
            # Readers require both immutable objects to match. Partial publication fails closed.
            await self.store.blob_create(self.digest_name(envelope.content_digest), raw)
            return signed

    async def bundle(self, identity, policy_id, version):
        self.policy_access(identity, policy_id)
        raw = await self.store.blob_read(self.policy_name(policy_id, version))
        signed = await self.authenticate_bundle(raw)
        if signed.envelope.policy_id != policy_id or signed.envelope.version != version:
            raise Conflict()
        index = await self.store.blob_read(self.digest_name(signed.envelope.content_digest))
        if index != raw:
            raise Conflict()
        return signed

    async def intent_policy(self, intent):
        try:
            raw = await self.store.blob_read(self.digest_name(intent.policy_hash))
            signed = await self.authenticate_bundle(raw)
            envelope = signed.envelope
            if (envelope.content_digest != intent.policy_hash
                    or envelope.expires_at != intent.policy_expires_at
                    or await self.store.blob_read(
                        self.policy_name(envelope.policy_id, envelope.version)) != raw):
                raise Conflict()
            self.fresh(intent)
            return envelope
        except Missing:
            raise Conflict() from None

    def fresh(self, intent):
        if (intent.expires_at <= self.now() or intent.policy_expires_at <= self.now()
                or intent.expires_at > intent.policy_expires_at
                or intent.expires_at > self.now() + timedelta(seconds=self.settings.approval_max_seconds)):
            raise Conflict()

    def requester(self, identity, intent):
        workload = self.workload(identity)
        if (intent.tenant != identity.tenant or intent.principal != identity.subject
                or intent.agent_id != workload.agent_id
                or not set(intent.allowed_roles).issubset(self.settings.approver_roles)):
            raise Forbidden()

    async def approval(self, identity, operation):
        intent = operation.intent
        scope = identity.tenant
        if scope != self.settings.tenant_id or intent.tenant != scope:
            raise Forbidden()
        if operation.operation == "decide":
            if (identity.workload is not None or identity.subject == intent.principal
                    or identity.subject not in self.settings.approver_subjects
                    or "Governance.Approve" not in identity.scopes
                    or operation.approving_role not in identity.roles
                    or operation.approving_role not in self.settings.approver_roles
                    or operation.approving_role not in intent.allowed_roles):
                raise Forbidden()
        else:
            self.requester(identity, intent)
        policy = await self.intent_policy(intent)
        requester = self.settings.workloads.get(intent.principal)
        if (requester is None or intent.agent_id != requester.agent_id
                or policy.policy_id not in requester.policies
                or not set(intent.allowed_roles).issubset(self.settings.approver_roles)):
            raise Forbidden()
        key = f"approval:{intent.nonce}"
        wire = intent.model_dump(mode="json")
        try:
            record, etag = await self.store.read(scope, key)
        except Missing:
            if operation.operation != "request":
                raise Conflict() from None
            record = {"intent": wire, "state": "pending", "grant": None}
            try:
                await self.store.create(scope, key, record)
                self.fresh(intent)
                return 202, {"status": "pending"}
            except Conflict:
                record, etag = await self.store.read(scope, key)
        if record["intent"] != wire or record["state"] == "consumed":
            raise Conflict()
        if operation.operation in ("request", "resolve"):
            self.fresh(intent)
            if record["state"] == "pending":
                return 202, {"status": "pending"}
            if record["state"] != "decided":
                raise Conflict()
            return 200, {"grant": record["grant"]}
        if operation.operation == "decide":
            if record["state"] != "pending":
                raise Conflict()
            grant = ApprovalGrant(
                intent=intent, approved=operation.approved, approver=identity.subject,
                approver_tenant=scope, approver_role=operation.approving_role,
                provenance=uuid.uuid4().hex,
            ).model_dump(mode="json")
            replacement = {"intent": wire, "state": "decided", "grant": grant}
        else:
            grant = operation.grant.model_dump(mode="json")
            if record["state"] != "decided" or record["grant"] != grant or grant["intent"] != wire:
                raise Conflict()
            if (grant["approver"] not in self.settings.approver_subjects
                    or grant["approver_tenant"] != scope
                    or grant["approver"] == intent.principal
                    or grant["approver_role"] not in self.settings.approver_roles
                    or grant["approver_role"] not in intent.allowed_roles):
                raise Forbidden()
            replacement = {"intent": wire, "state": "consumed", "grant": grant}
        self.fresh(intent)
        await self.store.replace(scope, key, replacement, etag)
        # If expiry raced a successful write, the nonce is burned, never permitted.
        self.fresh(intent)
        return 200, {"grant": grant, **({"consumed": True} if operation.operation == "consume" else {})}

    async def record_receipt(self, identity, receipt):
        self.workload(identity)
        if receipt.recorded_at > self.now() + timedelta(seconds=30):
            raise Conflict()
        body = {"owner": identity.subject, "receipt": receipt.model_dump(mode="json")}
        key = f"receipt:{receipt.receipt_id}"
        try:
            await self.store.create(identity.tenant, key, body)
        except Conflict:
            existing, _ = await self.store.read(identity.tenant, key)
            if existing != body:
                raise Conflict() from None
        return {"receipt_id": receipt.receipt_id}

    async def receipt(self, identity, receipt_id):
        record, _ = await self.store.read(identity.tenant, f"receipt:{receipt_id}")
        if not ((identity.workload and record["owner"] == identity.subject) or self.auditor(identity)):
            raise Forbidden()
        return parse(DecisionReceipt, canonical(record["receipt"]))


class AzureConfiguration(Settings):
    blob_url: Annotated[str, Field(pattern=r"^https://[a-z0-9]+\.blob\.core\.windows\.net$")]
    blob_container: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")]
    cosmos_url: Annotated[str, Field(pattern=r"^https://[a-z0-9-]+\.documents\.azure\.com:443/$")]
    cosmos_database: Identifier
    cosmos_container: Identifier


def configure_logging():
    # SDK credential failures can include remote exception bodies even at WARNING.
    for name in ("azure", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())


@asynccontextmanager
async def production():
    """No fallback credentials or resource provisioning. All clients close on failure."""
    from azure.identity.aio import DefaultAzureCredential
    from azure.cosmos.aio import CosmosClient
    from azure.storage.blob.aio import BlobServiceClient
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient

    configure_logging()
    path = os.environ.get("GOV_CONFIG_FILE")
    if not path:
        raise ValueError("configuration_required")
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("invalid_configuration")
    settings = parse(AzureConfiguration, raw)
    async with AsyncExitStack() as stack:
        async def managed(client):
            # Register cleanup before __aenter__: Cosmos initialization can fail mid-I/O.
            async def close():
                async with asyncio.timeout(settings.request_timeout):
                    await client.close()
            stack.push_async_callback(close)
            await client.__aenter__()
            return client

        async with asyncio.timeout(settings.request_timeout):
            credential = await managed(DefaultAzureCredential(
                exclude_environment_credential=True, exclude_workload_identity_credential=True,
                exclude_shared_token_cache_credential=True, exclude_visual_studio_code_credential=True,
                exclude_cli_credential=True, exclude_powershell_credential=True,
                exclude_developer_cli_credential=True, exclude_interactive_browser_credential=True,
                exclude_broker_credential=True,
            ))
            blobs = await managed(BlobServiceClient(
                settings.blob_url, credential=credential, retry_total=0,
                connection_timeout=settings.request_timeout, read_timeout=settings.request_timeout))
            cosmos = await managed(CosmosClient(
                settings.cosmos_url, credential=credential, retry_total=0,
                connection_timeout=settings.request_timeout, read_timeout=settings.request_timeout))
            crypto = await managed(CryptographyClient(
                settings.key_id, credential=credential, retry_total=0,
                connection_timeout=settings.request_timeout, read_timeout=settings.request_timeout))
            keys = await managed(KeyClient(
                settings.key_id.split("/keys/")[0], credential=credential, retry_total=0,
                connection_timeout=settings.request_timeout, read_timeout=settings.request_timeout))
            http = await stack.enter_async_context(httpx.AsyncClient(
                timeout=settings.request_timeout, follow_redirects=False, trust_env=False))
            store = AzureStore(blobs.get_container_client(settings.blob_container),
                cosmos.get_database_client(settings.cosmos_database).get_container_client(settings.cosmos_container),
                # Pinned aio SDK exposes account metadata only through this internal method.
                account_reader=cosmos._get_database_account)
            auth = EntraAuth(settings, http)
            service = ControlPlane(settings, store, KeyVaultSigner(crypto, key_client=keys))
            await store.health()
            await service.signer.health()
            await auth.health()
        yield service, auth


class RequestBoundary:
    """Cap the actual ASGI stream, including chunked bodies, before JSON decoding."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 16384:
                        return await JSONResponse({"error": "request_too_large"}, 413)(scope, receive, send)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"error": "request_timeout"}, 408)(scope, receive, send)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(*, service=None, auth=None):
    @asynccontextmanager
    async def lifespan(app):
        if service is not None and auth is not None:
            yield
            return
        stack = AsyncExitStack()
        try:
            try:
                app.state.service, app.state.auth = await stack.enter_async_context(production())
            except Exception:
                app.state.service = app.state.auth = None
            yield
        finally:
            app.state.service = app.state.auth = None
            try:
                await stack.aclose()
            except Exception:
                # Cleanup already attempts every registered client; never log private SDK errors.
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.auth = service, auth
    app.add_middleware(RequestBoundary)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exception):
        return JSONResponse({"error": "invalid_request"}, 422)

    async def dispatch(request, action):
        current, authority = app.state.service, app.state.auth
        if current is None or authority is None:
            return JSONResponse({"error": "unavailable"}, 503)
        try:
            async with asyncio.timeout(current.settings.request_timeout):
                identity = await authority.authenticate(request.headers.get("authorization"))
                result = await action(current, identity)
                if isinstance(result, tuple):
                    status, result = result
                else:
                    status = 200
                return JSONResponse(content=json.loads(canonical(result)), status_code=status)
        except Unauthorized:
            return JSONResponse({"error": "unauthorized"}, 401)
        except Forbidden:
            return JSONResponse({"error": "forbidden"}, 403)
        except Conflict:
            return JSONResponse({"error": "conflict"}, 409)
        except Missing:
            return JSONResponse({"error": "not_found"}, 404)
        except (ValidationError, ValueError, RecursionError):
            return JSONResponse({"error": "invalid_request"}, 422)
        except Exception:
            return JSONResponse({"error": "unavailable"}, 503)

    async def check_health(current, authority):
        await current.store.health()
        await current.signer.health()
        await authority.health()

    @app.get("/health")
    async def health(request: Request):
        if "authorization" in request.headers:
            async def action(current, identity):
                current.workload(identity)
                await check_health(current, app.state.auth)
                return {"status": "healthy", "authenticated": True}
            return await dispatch(request, action)
        try:
            current, authority = app.state.service, app.state.auth
            async with asyncio.timeout(current.settings.request_timeout):
                await check_health(current, authority)
            return {"status": "healthy"}
        except Exception:
            return JSONResponse({"status": "unhealthy"}, 503)

    @app.get("/bundles/{policy_id}/{version}")
    async def bundle(request: Request, policy_id: str, version: str):
        async def action(current, identity):
            return await current.bundle(identity, parse(Identifier, canonical(policy_id)),
                                        parse(Identifier, canonical(version)))
        return await dispatch(request, action)

    @app.post("/approvals/resolve")
    async def approval(request: Request):
        async def action(current, identity):
            return await current.approval(identity, parse(ApprovalOperation, await request.body()))
        return await dispatch(request, action)

    @app.post("/receipts")
    async def receipts(request: Request):
        async def action(current, identity):
            return await current.record_receipt(identity, parse(DecisionReceipt, await request.body()))
        return await dispatch(request, action)

    @app.get("/receipts/{receipt_id}")
    async def receipt(request: Request, receipt_id: str):
        async def action(current, identity):
            return await current.receipt(identity, parse(Nonce, canonical(receipt_id)))
        return await dispatch(request, action)

    return app
