"""Native MAF Responses host; selected business actions execute only through MCP."""
from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from agent_framework import SkillsProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.projects.aio import AIProjectClient
import httpx
from starlette.responses import JSONResponse

from govern_control_plane.client import ServiceTransport
from govern_control_plane.bootstrap import DEPENDENCY_ERRORS
from govern_control_plane.models import SignedBundle, canonical, envelope_digest, parse
from maf_gateway import create_gateway_agent

BASE = Path(__file__).resolve().parent


def listen_host(env):
    return "0.0.0.0" if env.get("FOUNDRY_AGENT_NAME") and env.get("FOUNDRY_AGENT_VERSION") else "127.0.0.1"


class GatewayAuthority:
    def __init__(self, config, *, credential, signer, http, bootstrap_gate=None):
        self.config, self.credential, self.signer, self.http = config, credential, signer, http
        self.bootstrap_gate = bootstrap_gate
        self.snapshot = None
        self.service = ServiceTransport(
            base_url=config["control_plane_url"], scope=config["control_plane_scope"],
            credential=credential, http=http)

    async def authorize(self):
        async with asyncio.timeout(5):
            config = self.config
            if self.bootstrap_gate is not None:
                await self.bootstrap_gate.authorize()
            _, document = await self.service.request(
                "GET", f"/bundles/{config['policy_id']}/{config['policy_version']}")
            signed = parse(SignedBundle, canonical(document))
            envelope = signed.envelope
            await self.signer.health()
            if (envelope.policy_id != config["policy_id"] or envelope.version != config["policy_version"]
                    or envelope.tenant_id != config["tenant_id"] or envelope.key_id != config["key_id"]
                    or envelope.expires_at <= datetime.now(timezone.utc)
                    or config.get("policy_digest", envelope.content_digest) != envelope.content_digest
                    or not await self.signer.verify(envelope_digest(envelope),
                        base64.b64decode(signed.signature, validate=True))):
                raise RuntimeError("gateway_policy_unavailable")
            snapshot = canonical(signed)
            if self.snapshot is not None and snapshot != self.snapshot:
                raise RuntimeError("gateway_policy_changed")
            self.snapshot = snapshot
            if self.bootstrap_gate is not None:
                self.bootstrap_gate.check()
            if envelope.expires_at <= datetime.now(timezone.utc):
                raise RuntimeError("gateway_policy_expired")
            return envelope.content_digest

    async def health(self):
        digest = await self.authorize()
        token = await self.credential.get_token(self.config["gateway_scope"])
        await self.authorize()
        gateway = urlsplit(self.config["gateway_url"])
        response = await self.http.get(
            f"{gateway.scheme}://{gateway.netloc}/health",
            headers={"Authorization": "Bearer " + token.token})
        body = response.json()
        selected = [tool["id"] for tool in self.config["contract"]["tools"]
                    if tool["policy_binding"] not in (None, "none")]
        if (response.status_code != 200 or body.get("policy_digest") != digest
                or not all(body.get("bindings", {}).get(name, {}).get("healthy") is True for name in selected)):
            raise RuntimeError("gateway_dependencies_unavailable")
        await self.authorize()
        return {"status": "ready", "scope": "selected-gateway-dependencies", "tools": selected}


async def build_host(config, *, credential, signer, stack, bootstrap_gate=None,
                     application=None, client=None, http=None, transport_factory=None, **host_options):
    if application is None:
        import governance_application as application
    if getattr(application, "middleware", []):
        raise ValueError("maf_gateway_application_middleware_unsupported")
    if http is None:
        http = await stack.enter_async_context(httpx.AsyncClient(
            timeout=5, trust_env=False, follow_redirects=False))
    authority = GatewayAuthority(
        config, credential=credential, signer=signer, http=http, bootstrap_gate=bootstrap_gate)
    await authority.health()
    if callable(getattr(application, "initialize", None)):
        await application.initialize(config, credential, stack)
    if client is None:
        project = await stack.enter_async_context(AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=credential,
            allow_preview=True, retry_total=0))
        client = FoundryChatClient(
            model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], project_client=project)
        stack.push_async_callback(client.client.close)
    skills = BASE / "skills"
    contexts = [SkillsProvider.from_paths(skills)] if any(skills.glob("*/SKILL.md")) else []
    agent = await create_gateway_agent(
        config=config, client=client, local_tools=application.tools,
        instructions=(BASE / "copilot-instructions.md").read_text(),
        credential=credential, authorize=authority.authorize,
        transport_factory=transport_factory, context_providers=contexts)

    class GatewayHost(ResponsesHostServer):
        async def _readiness_endpoint(self, request):
            try:
                async with asyncio.timeout(10):
                    return JSONResponse(await authority.health())
            except DEPENDENCY_ERRORS:
                return JSONResponse({"status": "unavailable"}, status_code=503)

    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    return GatewayHost(agent, **host_options)


def remote_host(config, *, credential, signer, http=None, env=None, **host_options):
    from govern_control_plane.bootstrap import runtime_gate

    async def initialize(binding, stack):
        resolved = {**config, "policy_digest": binding.policy_digest,
                    "policy_version": binding.policy_version,
                    **{key: getattr(binding, key) for key in (
                        "principal", "agent_version", "image_digest", "subscription", "resource_group")}}
        host = await build_host(
            resolved, credential=credential, signer=signer, stack=stack,
            bootstrap_gate=gate, http=http, **host_options)
        await stack.enter_async_context(host.router.lifespan_context(host))
        return host

    gate = runtime_gate(config, env=env or os.environ, credential=credential,
                        signer=signer, initialize=initialize, http=http)
    return gate


async def main():
    from govern_control_plane.bootstrap import serve_remote
    from govern_control_plane.storage import KeyVaultSigner
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    config = json.loads((BASE / "governance-config.json").read_text())
    if "remote_bootstrap" in config:
        await serve_remote(config, remote_host)
        return
    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential(
            exclude_environment_credential=True, exclude_shared_token_cache_credential=True,
            exclude_visual_studio_code_credential=True, exclude_cli_credential=True,
            exclude_powershell_credential=True, exclude_developer_cli_credential=True,
            exclude_interactive_browser_credential=True, exclude_broker_credential=True))
        crypto = await stack.enter_async_context(CryptographyClient(config["key_id"], credential, retry_total=0))
        keys = await stack.enter_async_context(KeyClient(
            config["key_id"].split("/keys/")[0], credential, retry_total=0))
        host = await build_host(config, credential=credential,
                                signer=KeyVaultSigner(crypto, key_client=keys), stack=stack)
        await host.run_async(host=listen_host(os.environ))


if __name__ == "__main__":
    asyncio.run(main())
