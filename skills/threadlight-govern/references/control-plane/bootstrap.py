"""Create-only hosted binding and fail-closed, one-time ASGI activation.

This protocol carries deployment facts, never executable configuration or URLs
to load. The independent publisher observes the platform version before signing.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import uuid
from typing import Annotated, Literal

import httpx
from azure.core.exceptions import AzureError
from pydantic import Field, StringConstraints, model_validator
from starlette.responses import JSONResponse

from .client import ApprovalUnavailable, ServiceTransport
from .models import (
    Digest, Identifier, KeyId, ObjectId, StrictModel, Timestamp, canonical, parse, strict_json,
)
from .storage import Conflict
from .bootstrap_assets import BootstrapAsset, NativeProbeConstraints, validate_descriptors

DEPENDENCY_ERRORS = (ApprovalUnavailable, ValueError, RuntimeError, OSError, httpx.HTTPError, AzureError)

ProjectEndpoint = Annotated[str, StringConstraints(
    pattern=r"^https://[a-z0-9-]+\.services\.ai\.azure\.com/api/projects/[A-Za-z0-9_-]+$",
    max_length=512)]


class BootstrapBinding(StrictModel):
    schema_version: Literal["threadlight-hosted-bootstrap/v1"] = Field(alias="schema")
    reference: Identifier
    tenant_id: ObjectId
    key_id: KeyId
    policy_id: Identifier
    policy_version: Identifier
    native_policy_digest: Digest
    policy_digest: Digest
    config_digest: Digest
    project_endpoint: ProjectEndpoint
    agent_id: Identifier
    agent_version: Identifier
    image_digest: Digest
    subscription: ObjectId
    resource_group: Identifier
    environment: Literal["staging", "preproduction", "production"]
    principal: ObjectId
    client_id: ObjectId
    issued_at: Timestamp
    expires_at: Timestamp
    native_probe_assets: Annotated[tuple[BootstrapAsset, ...], Field(min_length=5, max_length=32)] | None = Field(
        default=None, exclude_if=lambda value: value is None)

    model_config = {**StrictModel.model_config, "serialize_by_alias": True}

    @model_validator(mode="after")
    def asset_inventory(self):
        if self.native_probe_assets is not None:
            validate_descriptors(self.native_probe_assets)
        return self


class SignedBootstrap(StrictModel):
    binding: BootstrapBinding
    signature: Annotated[str, StringConstraints(
        min_length=1, max_length=1024, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]


class BootstrapReference(StrictModel):
    reference: Identifier
    project_endpoint: ProjectEndpoint
    subscription: ObjectId
    resource_group: Identifier
    native_policy_digest: Digest
    final_policy_version: Identifier | None = None
    native_probe: NativeProbeConstraints | None = None


class BootstrapUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("bootstrap_unavailable")


class BootstrapNotReady(BootstrapUnavailable):
    pass


def binding_digest(binding):
    return hashlib.sha256(canonical(binding)).digest()


def fresh(binding):
    now = datetime.now(timezone.utc)
    if (binding.issued_at > now or binding.expires_at <= now
            or binding.expires_at <= binding.issued_at
            or binding.expires_at - binding.issued_at > timedelta(hours=24)):
        raise BootstrapUnavailable()


async def verify(signed, signer, *, tenant_id, key_id):
    binding = signed.binding
    fresh(binding)
    if binding.tenant_id != tenant_id or binding.key_id != key_id:
        raise BootstrapUnavailable()
    async with asyncio.timeout(5):
        await signer.health()
        if not await signer.verify(binding_digest(binding),
                                   base64.b64decode(signed.signature, validate=True)):
            raise BootstrapUnavailable()
    fresh(binding)
    return binding


def blob_name(tenant, reference):
    return f"{tenant}/bootstrap/{reference}.json"


async def policy_chain(service, binding):
    for digest in {binding.policy_digest, binding.native_policy_digest}:
        raw = await service.store.blob_read(service.digest_name(digest))
        signed = await service.authenticate_bundle(raw)
        envelope = signed.envelope
        if (envelope.content_digest != digest or envelope.expires_at < binding.expires_at
                or await service.store.blob_read(service.policy_name(
                    envelope.policy_id, envelope.version)) != raw
                or digest == binding.policy_digest and (
                    envelope.policy_id != binding.policy_id or envelope.version != binding.policy_version)):
            raise Conflict()


async def publish(service, binding, *, assets=None):
    """Operator backend only; API identity must not have sign or Blob write rights."""
    binding = parse(BootstrapBinding, canonical(binding))
    from . import bootstrap_assets
    bootstrap_assets.validate_content(binding, assets)
    async with asyncio.timeout(120 if assets is not None else service.settings.request_timeout):
        if (binding.tenant_id != service.settings.tenant_id
                or binding.key_id != service.settings.key_id):
            raise Conflict()
        fresh(binding)
        await policy_chain(service, binding)
        signed = SignedBootstrap(binding=binding, signature=base64.b64encode(
            await service.signer.sign(binding_digest(binding))).decode())
        await verify(signed, service.signer, tenant_id=service.settings.tenant_id,
                     key_id=service.settings.key_id)
        await bootstrap_assets.publish(service, binding, assets)
        await verify(signed, service.signer, tenant_id=service.settings.tenant_id, key_id=service.settings.key_id)
        await policy_chain(service, binding)
        await service.store.blob_create(blob_name(binding.tenant_id, binding.reference), canonical(signed))
        fresh(binding)
        return signed


async def read(service, identity, reference):
    from .app import Forbidden
    service.workload(identity)
    signed = parse(SignedBootstrap, await service.store.blob_read(
        blob_name(service.settings.tenant_id, reference)))
    binding = signed.binding
    service.policy_access(identity, binding.policy_id)
    if (binding.reference != reference or binding.tenant_id != identity.tenant
            or binding.principal != identity.subject or binding.client_id != identity.client
            or binding.agent_id != identity.workload.agent_id):
        raise Forbidden()
    await verify(signed, service.signer, tenant_id=service.settings.tenant_id,
                 key_id=service.settings.key_id)
    await policy_chain(service, binding)
    return signed


class BootstrapClient(ServiceTransport):
    async def load(self, reference):
        reference = parse(Identifier, canonical(reference))
        async with asyncio.timeout(self.timeout):
            _, body = await self.request("GET", f"/bootstrap/{reference}")
            signed = parse(SignedBootstrap, canonical(body))
            if signed.binding.reference != reference:
                raise BootstrapUnavailable()
            return signed


class BootstrapGate:
    """Liveness alone is available pending binding; initialization never repeats."""
    def __init__(self, *, expected, fetch, signer, initialize, prepare=None):
        self.expected = dict(expected)
        self.fetch, self.signer, self.initialize = fetch, signer, initialize
        self.prepare = prepare
        self.lock = asyncio.Lock()
        self.resources = AsyncExitStack()
        self.signed = self.app = None
        self.active = False
        self.invalid = self.closed = False

    def check(self):
        if self.closed or self.invalid or self.signed is None:
            raise BootstrapUnavailable()
        fresh(self.signed.binding)

    async def authenticate(self):
        signed = await self.fetch()
        binding = await verify(signed, self.signer, tenant_id=self.expected["tenant_id"],
                               key_id=self.expected["key_id"])
        actual = binding.model_dump(mode="json")
        for key, value in self.expected.items():
            if key not in ("issued_at", "expires_at") and actual.get(key) != value:
                raise BootstrapUnavailable()
        if self.signed is not None and signed != self.signed:
            raise BootstrapUnavailable()
        return signed

    async def activate(self):
        async with self.lock:
            if self.closed or self.invalid:
                raise BootstrapUnavailable()
            if self.app is not None:
                await self.authorize()
                return
            # A missing publication is retryable. No app code has run yet.
            signed = await self.authenticate()
            if self.prepare is not None:
                await self.prepare(signed, self.resources)
            self.signed = signed
            try:
                self.app = await self.initialize(signed.binding, self.resources)
                await self.authorize()
                self.active = True
            except BaseException:
                self.invalid = True
                self.app = None
                await self.resources.aclose()
                raise

    async def authorize(self):
        self.check()
        try:
            await self.authenticate()
            self.check()
        except DEPENDENCY_ERRORS:
            self.invalid = True
            self.active = False
            raise BootstrapUnavailable() from None

    async def aclose(self):
        async with self.lock:
            self.closed = True
            self.active = False
            self.app = None
            await self.resources.aclose()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.lifespan(receive, send)
        if scope["type"] != "http":
            return await send({"type": "websocket.close", "code": 1013})
        if scope["path"] == "/liveness":
            return await JSONResponse({"status": "alive"})(scope, receive, send)
        if not self.active:
            return await JSONResponse({"status": "bootstrap_unavailable"}, 503)(scope, receive, send)
        try:
            await self.authorize()
        except DEPENDENCY_ERRORS:
            return await JSONResponse({"status": "bootstrap_unavailable"}, 503)(scope, receive, send)
        if scope["path"] in ("/invocations", "/responses") and scope["method"] == "POST":
            raw = bytearray()
            try:
                async with asyncio.timeout(5):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        raw.extend(message.get("body", b""))
                        limit = 32768 if scope["path"] == "/invocations" else 16777216
                        if len(raw) > limit:
                            raise ValueError("bounded_request_required")
                        if not message.get("more_body", False):
                            break
                body = strict_json(bytes(raw)) if raw else None
            except (TimeoutError, ValueError):
                return await JSONResponse({"error": "invalid_request"}, 400)(scope, receive, send)
            if (scope["path"] == "/responses" and isinstance(body, dict)
                    and isinstance(body.get("metadata"), dict)
                    and "threadlight_bootstrap_reference" in body["metadata"]):
                expected = {"threadlight_bootstrap_reference": self.signed.binding.reference}
                selection = {"type": "agent_reference", "name": self.signed.binding.agent_id,
                             "version": self.signed.binding.agent_version}
                if (body.get("input") != [] or body.get("store") is not False
                        or body["metadata"] != expected
                        or set(body) - {"input", "metadata", "store", "agent_reference", "model"}
                        or body.get("agent_reference", selection) != selection):
                    return await JSONResponse({"status": "bootstrap_unavailable"}, 503)(scope, receive, send)
                try:
                    await self.authorize()
                except DEPENDENCY_ERRORS:
                    return await JSONResponse({"status": "bootstrap_unavailable"}, 503)(scope, receive, send)
                from .bootstrap_assets import digest
                return await JSONResponse({
                    "id": "resp_bootstrap_" + uuid.uuid4().hex, "object": "response",
                    "created_at": int(datetime.now(timezone.utc).timestamp()), "status": "completed",
                    "model": "threadlight-bootstrap-control", "output": [], "tools": [], "store": False,
                    "parallel_tool_calls": False, "tool_choice": "none",
                    "metadata": {"threadlight_bootstrap_sha256": digest(canonical(self.signed))},
                })(scope, receive, send)
            if scope["path"] == "/invocations" and isinstance(body, dict) and "bootstrap_reference" in body:
                if body != {"input": "", "bootstrap_reference": self.signed.binding.reference}:
                    return await JSONResponse({"status": "bootstrap_unavailable"}, 503)(scope, receive, send)
                try:
                    await self.authorize()
                except DEPENDENCY_ERRORS:
                    return await JSONResponse({"status": "bootstrap_unavailable"}, 503)(scope, receive, send)
                return await JSONResponse({"bootstrap": self.signed.model_dump(mode="json")})(scope, receive, send)
            consumed = False
            original_receive = receive

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(raw), "more_body": False}
                return await original_receive()
            receive = replay
        await self.app(scope, receive, send)

    async def lifespan(self, receive, send):
        async def poll():
            while not self.closed and not self.invalid:
                try:
                    await self.activate()
                    return
                except DEPENDENCY_ERRORS:
                    await asyncio.sleep(1)

        task = None
        try:
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    task = asyncio.create_task(poll())
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    return
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await self.aclose()
            await send({"type": "lifespan.shutdown.complete"})


async def read_host_binding(target, signed, *, credential, http):
    """A read-only control exchange on the existing native protocol, never inference."""
    from .bootstrap_assets import digest
    fresh(signed.binding)
    if any(getattr(signed.binding, key) != target[key] for key in (
            "agent_id", "agent_version", "project_endpoint")):
        raise BootstrapUnavailable()
    async with asyncio.timeout(30):
        if target["protocol"] == "responses":
            from azure.ai.projects.aio import AIProjectClient
            from openai import APIStatusError
            async with AIProjectClient(endpoint=target["project_endpoint"], credential=credential,
                                       allow_preview=True, retry_total=0) as project:
                client = project.get_openai_client(
                    agent_name=target["agent_id"], http_client=http, max_retries=0)
                try:
                    response = await client.responses.create(
                        input=[], store=False,
                        metadata={"threadlight_bootstrap_reference": signed.binding.reference},
                        extra_body={"agent_reference": {"type": "agent_reference", "name": target["agent_id"],
                                                         "version": target["agent_version"]}})
                except APIStatusError as error:
                    if error.status_code in (404, 409, 429, 503):
                        raise BootstrapNotReady() from None
                    raise BootstrapUnavailable() from None
                if (response.output != [] or response.metadata != {
                        "threadlight_bootstrap_sha256": digest(canonical(signed))}):
                    raise BootstrapUnavailable()
        elif target["protocol"] == "invocations":
            token = await credential.get_token("https://ai.azure.com/.default")
            url = target["project_endpoint"] + "/agents/" + target["agent_id"] + "/endpoint/protocols/invocations?api-version=v1"
            async with http.stream("POST", url, headers={"Authorization": "Bearer " + token.token},
                    json={"input": "", "bootstrap_reference": signed.binding.reference},
                    follow_redirects=False) as response:
                if response.status_code != 200:
                    if response.status_code in (404, 409, 429, 503):
                        raise BootstrapNotReady()
                    raise BootstrapUnavailable()
                raw = bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw) > 16384:
                        raise BootstrapUnavailable()
            body = strict_json(bytes(raw))
            if set(body) != {"bootstrap"} or parse(SignedBootstrap, canonical(body["bootstrap"])) != signed:
                raise BootstrapUnavailable()
        else:
            raise BootstrapUnavailable()
    fresh(signed.binding)


def runtime_gate(config, *, env, credential, signer, initialize, http=None, prepare=None):
    """Only frozen image configuration and platform-injected identity are selectors."""
    reference = parse(BootstrapReference, canonical(config["remote_bootstrap"]))
    if (env["FOUNDRY_PROJECT_ENDPOINT"] != reference.project_endpoint
            or env["FOUNDRY_AGENT_NAME"] != config["agent_id"]
            or env["GOV_CONTROL_PLANE_URL"] != config["control_plane_url"]):
        raise ValueError("bootstrap_platform_mismatch")
    reference_fields = reference.model_dump(exclude={"final_policy_version", "native_probe"})
    expected = {
        **reference_fields, "tenant_id": config["tenant_id"], "key_id": config["key_id"],
        "policy_id": config["policy_id"], "environment": config["environment"],
        "agent_id": config["agent_id"],
        "agent_version": parse(Identifier, canonical(env["FOUNDRY_AGENT_VERSION"])),
        "image_digest": parse(Digest, canonical(env["TL_GOV_IMAGE_DIGEST"])),
        "config_digest": "sha256:" + hashlib.sha256(canonical(config)).hexdigest(),
    }
    if reference.final_policy_version is not None:
        expected["policy_version"] = reference.final_policy_version
    client = BootstrapClient(base_url=config["control_plane_url"],
        scope=config["control_plane_scope"], credential=credential, http=http)

    async def fetch():
        # The CP's EntraAuth proves exact subject/client authorization for this
        # record. No locally decoded token claims are trusted by this receiver.
        return await client.load(reference.reference)

    gate = BootstrapGate(expected=expected, fetch=fetch, signer=signer, initialize=initialize, prepare=prepare)
    gate.bootstrap_client = client
    gate.resources.push_async_callback(client.aclose)
    return gate


async def serve_remote(config, factory):
    """Start a pending HTTP server without importing an application or model client."""
    import os
    import uvicorn
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    from .storage import KeyVaultSigner
    async with AsyncExitStack() as stack:
        # Foundry's native credential flow, not an invented attachable agent UAMI.
        credential = await stack.enter_async_context(DefaultAzureCredential(
            exclude_environment_credential=True, exclude_shared_token_cache_credential=True,
            exclude_visual_studio_code_credential=True, exclude_cli_credential=True,
            exclude_powershell_credential=True, exclude_developer_cli_credential=True,
            exclude_interactive_browser_credential=True, exclude_broker_credential=True))
        crypto = await stack.enter_async_context(CryptographyClient(
            config["key_id"], credential=credential, retry_total=0))
        keys = await stack.enter_async_context(KeyClient(
            config["key_id"].split("/keys/")[0], credential=credential, retry_total=0))
        gate = factory(config, credential=credential,
                       signer=KeyVaultSigner(crypto, key_client=keys))
        stack.push_async_callback(gate.aclose)
        await uvicorn.Server(uvicorn.Config(
            gate, host="0.0.0.0", port=int(os.environ.get("PORT", "8088")),
            access_log=False, log_level="warning")).serve()
