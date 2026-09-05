"""Official FastMCP Streamable HTTP service, stateless and authenticated on every request."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
import logging
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolResult, ClientNotification, ClientRequest, JSONRPCMessage, JSONRPCRequest, TextContent,
)
from pydantic import Field, model_validator
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount

from govern_control_plane.app import configure_logging
from govern_control_plane.auth import EntraAuth, Settings
from govern_control_plane.client import ServiceTransport
from govern_control_plane.models import (
    Digest, Identifier, ObjectId, canonical, parse, strict_json,
)
from govern_control_plane.storage import AzureStore, KeyVaultSigner

from .dispatcher import (
    GovernedDispatcher, NativePolicy, DownstreamClient, _request_identity, authenticate, https_endpoint,
)
from .receipts import HTTPControlPlaneApprovalService, ReceiptClient

_idempotency_key = ContextVar("gateway_idempotency_key", default=None)
HEALTH_TIMEOUT = 5.0


def tool_result(body):
    return CallToolResult(
        content=[TextContent(type="text", text=canonical(body).decode())],
        structuredContent=body, isError=body["status"] != "completed")


class RegisteredTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        return tool_result(await self.fn(arguments))


class GatewayMCP(FastMCP):
    async def call_tool(self, name, arguments):
        if name not in {t.name for t in self._tool_manager.list_tools()}:
            return tool_result({"status": "blocked", "reason_code": "unknown_action"})
        return await super().call_tool(name, arguments)


class AuthBoundary:
    def __init__(self, app, dispatcher):
        self.app, self.dispatcher = app, dispatcher

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        auth = [v.decode("latin1") for k, v in headers if k.lower() == b"authorization"]
        keys = [v.decode("latin1") for k, v in headers if k.lower() == b"idempotency-key"]
        try:
            if len(auth) != 1 or len(keys) > 1:
                raise ValueError()
            async with asyncio.timeout(5):
                identity = await authenticate(self.dispatcher.auth, auth[0])
        except Exception:
            return await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
        # Bound and parse here so SDK/Pydantic errors cannot echo private input.
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    body.extend(event.get("body", b""))
                    if len(body) > 32768:
                        raise ValueError()
                    if not event.get("more_body", False):
                        break
            if body:
                document = strict_json(bytes(body))
                if not isinstance(document, dict) or not isinstance(document.get("method"), str):
                    raise ValueError()
                envelope = JSONRPCMessage.model_validate_json(bytes(body))
                model = ClientRequest if isinstance(envelope.root, JSONRPCRequest) else ClientNotification
                model.model_validate(document)
                if document["method"] == "tools/call":
                    params = document.get("params")
                    if (not isinstance(params, dict) or not isinstance(params.get("name"), str)
                            or len(params["name"]) > 64 or not isinstance(params.get("arguments", {}), dict)
                            or not keys):
                        raise ValueError()
                # No caller-controlled resources, prompts, logging levels or subscriptions.
                if document["method"] not in (
                    "initialize", "notifications/initialized", "ping", "tools/list", "tools/call"):
                    raise ValueError()
        except Exception:
            return await JSONResponse({"error": "invalid_request"}, 400)(scope, receive, send)
        first = True
        async def replay():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        identity_token = _request_identity.set(identity)
        key_token = _idempotency_key.set(keys[0] if keys else None)
        try:
            return await self.app(scope, replay, send)
        finally:
            _idempotency_key.reset(key_token)
            _request_identity.reset(identity_token)


def create_app(dispatcher):
    tools = []
    for action in dispatcher.policy.registry.actions:
        def registered(name):
            async def invoke(arguments):
                return await dispatcher.dispatch(
                    authorization=None, action=name, arguments=arguments,
                    idempotency_key=_idempotency_key.get())
            return invoke
        async def empty():
            pass
        base = Tool.from_function(empty, name=action.name)
        tools.append(RegisteredTool(
            fn=registered(action.name), name=action.name, description=f"Governed action: {action.name}",
            parameters=action.input_schema, fn_metadata=base.fn_metadata, is_async=True))
    url = urlsplit(dispatcher.policy.registry.gateway_url)
    mcp = GatewayMCP(
        "Threadlight governed actions", tools=tools, stateless_http=True, json_response=True,
        streamable_http_path=url.path, max_request_body_size=32768,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[url.netloc], allowed_origins=[f"https://{url.netloc}"]))

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        async def policy_health():
            dispatcher.policy.fresh()

        async def auth_health():
            await dispatcher.auth.health()

        async def store_health():
            await dispatcher.store.health()

        async def receipt_health():
            if (not callable(dispatcher.receipts.append)
                    or await dispatcher.receipts.health() is not True):
                raise ValueError()

        async def approval_health(action):
            context = dispatcher.approval_context(action, tenant=dispatcher.policy.registry.tenant_id)
            if (not callable(dispatcher.approvals.resolve)
                    or not callable(dispatcher.approvals.verify)
                    or await dispatcher.approvals.health(approval_context=context) is not True):
                raise ValueError()

        async def check(probe, reason, *args):
            try:
                async with asyncio.timeout(HEALTH_TIMEOUT):
                    await probe(*args)
                return {"healthy": True, "reason_code": None}
            except Exception:
                return {"healthy": False, "reason_code": reason}

        probes = {
            "policy": (policy_health, "policy_unavailable"),
            "authentication": (auth_health, "auth_unavailable"),
            "idempotency_store": (store_health, "idempotency_unavailable"),
            "receipts": (receipt_health, "receipt_unavailable"),
        }
        approval_actions = [action for action in dispatcher.policy.registry.actions if action.approval_roles]
        results = await asyncio.gather(
            *(check(probe, reason) for probe, reason in probes.values()),
            *(check(approval_health, "approval_unavailable", action) for action in approval_actions))
        dependencies = dict(zip(probes, results[:len(probes)]))
        approvals = dict(zip((action.name for action in approval_actions), results[len(probes):]))
        if approvals:
            healthy = all(result["healthy"] for result in approvals.values())
            dependencies["approvals"] = {
                "healthy": healthy, "reason_code": None if healthy else "approval_unavailable"}
        # A policy may expire while the remote dependencies are being checked.
        dependencies["policy"] = await check(policy_health, "policy_unavailable")
        bindings = {}
        for action in dispatcher.policy.registry.actions:
            required = ["policy", "authentication", "idempotency_store", "receipts"]
            reasons = [dependencies[name]["reason_code"] for name in required
                       if not dependencies[name]["healthy"]]
            if action.name in approvals and not approvals[action.name]["healthy"]:
                reasons.append(approvals[action.name]["reason_code"])
            bindings[action.name] = {"healthy": not reasons, "reason_codes": reasons}
        ready = all(binding["healthy"] for binding in bindings.values())
        return JSONResponse({
            "status": "ready" if ready else "unavailable",
            "policy_digest": dispatcher.policy.digest, "registry_loaded": True,
            "scope": "declared-local-controls-not-live-proof",
            "dependencies": dependencies, "bindings": bindings}, 200 if ready else 503)
    app = mcp.streamable_http_app()
    app.add_middleware(AuthBoundary, dispatcher=dispatcher)
    return app


class Configuration(Settings):
    gateway_url: str
    control_plane_url: str
    control_plane_scope: str
    service_client_id: ObjectId
    service_principal: ObjectId
    service_agent_id: Identifier
    downstream_client_id: ObjectId
    cosmos_url: Annotated[str, Field(pattern=r"^https://[a-z0-9-]+\.documents\.azure\.com:443/$")]
    cosmos_database: Identifier
    cosmos_container: Identifier
    bundle_path: Annotated[str, Field(min_length=1, max_length=1024)]
    policy_id: Identifier
    policy_version: Identifier
    policy_digest: Digest
    allowed_endpoints: Annotated[list[str], Field(min_length=2, max_length=128)]

    @model_validator(mode="after")
    def separate_credentials(self):
        https_endpoint(self.gateway_url)
        if (self.service_client_id == self.downstream_client_id
                or self.downstream_client_id in {w.client_id for w in self.workloads.values()}):
            raise ValueError("distinct_downstream_identity_required")
        return self


class GatewayStore(AzureStore):
    async def health(self):
        await self.write_safety()


def host_evidence(identity):
    """Only facts established by this PEP; business evidence is NOT invented."""
    return {"scope": identity["scope"], "identity": {
        "tenant": identity["tenant"], "principal": identity["subject"],
        "client": identity["client"], "authenticated": True},
        "policy_digest": identity["policy"], "deployment": identity["deployment"]}


@asynccontextmanager
async def production():
    from azure.identity.aio import DefaultAzureCredential
    from azure.cosmos.aio import CosmosClient
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient

    configure_logging()
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
    path = os.environ.get("GATEWAY_CONFIG_FILE")
    if not path:
        raise ValueError("configuration_required")
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("configuration_invalid")
    config = parse(Configuration, raw)
    async with AsyncExitStack() as stack:
        async def managed(client):
            async def close():
                async with asyncio.timeout(config.request_timeout):
                    await client.close()
            stack.push_async_callback(close)
            await client.__aenter__()
            return client
        async def identity(client_id):
            return await managed(DefaultAzureCredential(
                managed_identity_client_id=client_id, exclude_environment_credential=True,
                exclude_workload_identity_credential=True, exclude_shared_token_cache_credential=True,
                exclude_visual_studio_code_credential=True, exclude_cli_credential=True,
                exclude_powershell_credential=True, exclude_developer_cli_credential=True,
                exclude_interactive_browser_credential=True, exclude_broker_credential=True))
        async with asyncio.timeout(30):
            credential = await identity(config.service_client_id)
            downstream_credential = await identity(config.downstream_client_id)
            cosmos = await managed(CosmosClient(config.cosmos_url, credential=credential,
                retry_total=0, connection_timeout=5, read_timeout=5))
            crypto = await managed(CryptographyClient(config.key_id, credential=credential, retry_total=0))
            keys = await managed(KeyClient(
                config.key_id.split("/keys/")[0], credential=credential, retry_total=0))
            http = await stack.enter_async_context(httpx.AsyncClient(
                timeout=5, trust_env=False, follow_redirects=False))
            store = GatewayStore(None,
                cosmos.get_database_client(config.cosmos_database).get_container_client(config.cosmos_container),
                account_reader=cosmos._get_database_account)
            signer = KeyVaultSigner(crypto, key_client=keys)
            auth = EntraAuth(config, http)
            service = dict(base_url=config.control_plane_url, scope=config.control_plane_scope,
                           credential=credential, timeout=config.request_timeout)
            bundles = await stack.enter_async_context(ServiceTransport(**service))
            status, signed = await bundles.request(
                "GET", f"/bundles/{config.policy_id}/{config.policy_version}")
            if status != 200:
                raise ValueError("policy_unavailable")
            policy = await NativePolicy.load(
                bundle_path=Path(config.bundle_path), signed=signed, signer=signer,
                tenant=config.tenant_id, key_id=config.key_id, policy_id=config.policy_id,
                version=config.policy_version, expected_digest=config.policy_digest,
                allowed_endpoints=config.allowed_endpoints, gateway_url=config.gateway_url)
            receipts = await stack.enter_async_context(ReceiptClient(**service))
            approvals = await stack.enter_async_context(HTTPControlPlaneApprovalService(**service))
            downstream = DownstreamClient(credential=downstream_credential)
            stack.push_async_callback(downstream.aclose)
            await store.health()
            await auth.health()
        yield GovernedDispatcher(
            policy=policy, auth=auth, store=store, receipts=receipts, downstream=downstream,
            approvals=approvals, safe_provider=host_evidence,
            approval_principal=config.service_principal, approval_agent_id=config.service_agent_id)


def production_app():
    active = None
    @asynccontextmanager
    async def lifespan(app):
        nonlocal active
        async with production() as dispatcher:
            active = create_app(dispatcher)
            async with active.router.lifespan_context(active):
                yield
        active = None
    async def forward(scope, receive, send):
        await active(scope, receive, send)
    return Starlette(routes=[Mount("/", app=forward)], lifespan=lifespan)


def main():
    import uvicorn
    uvicorn.run(production_app(), host="0.0.0.0", port=8000, access_log=False, log_level="critical")


if __name__ == "__main__":
    main()
