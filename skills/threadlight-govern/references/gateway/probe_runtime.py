"""Explicit, mounted deployment binding for fixture/native producer services."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import Field, model_validator

from govern_control_plane.auth import EntraAuth, Settings
from govern_control_plane.models import Digest, Identifier, ObjectId, ProbeDeployment, parse
from govern_control_plane.probes import ProbeService, ProbeStore
from govern_control_plane.storage import KeyVaultSigner
from .dispatcher import DownstreamClient, NativePolicy


class ProbeConfiguration(Settings):
    enabled: bool
    producer: Literal["native", "fixture"]
    credential_mode: Literal["separate-managed-identity", "platform-noop"] = "separate-managed-identity"
    service_client_id: ObjectId
    downstream_client_id: ObjectId | None = None
    cosmos_url: Annotated[str, Field(pattern=r"^https://[a-z0-9-]+\.documents\.azure\.com:443/$")]
    cosmos_database: Identifier
    cosmos_container: Identifier
    bundle_path: Annotated[str, Field(pattern=r"^/(mnt|config)/[A-Za-z0-9_/-]+$", max_length=512)]
    signed_envelope_path: Annotated[str, Field(pattern=r"^/(mnt|config)/[A-Za-z0-9_/-]+\.json$", max_length=512)]
    policy_id: Identifier
    policy_version: Identifier
    policy_digest: Digest
    gateway_url: str
    allowed_endpoints: Annotated[list[str], Field(min_length=2, max_length=128)]
    expected_deployment: ProbeDeployment
    fixture_callers: Annotated[dict[ObjectId, ObjectId], Field(max_length=32)] = {}

    @model_validator(mode="after")
    def scope(self):
        if (self.enabled is not True or self.expected_deployment.environment not in ("staging", "preproduction")
                or self.cosmos_container != "probe-" + self.producer or not self.probe_controllers):
            raise ValueError("explicit_staging_probe_configuration_required")
        if self.producer == "native":
            if self.credential_mode == "platform-noop":
                if self.downstream_client_id is not None or self.fixture_callers:
                    raise ValueError("platform_probe_must_not_attach_downstream_identity")
            elif (self.downstream_client_id is None or self.downstream_client_id == self.service_client_id
                  or self.fixture_callers):
                raise ValueError("native_separate_downstream_identity_required")
        elif (not self.fixture_callers or self.downstream_client_id is not None
              or self.credential_mode != "separate-managed-identity"):
            raise ValueError("fixture_caller_allowlist_required")
        return self


def read_configuration(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("probe_configuration_invalid")
    return parse(ProbeConfiguration, raw)


@asynccontextmanager
async def open_runtime(config, *, platform_credential=None):
    from azure.identity.aio import DefaultAzureCredential
    from azure.cosmos.aio import CosmosClient
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    from govern_control_plane.app import configure_logging
    configure_logging()
    async with AsyncExitStack() as stack:
        async def managed(client):
            async def close():
                async with asyncio.timeout(5):
                    await client.close()
            stack.push_async_callback(close)
            return await client.__aenter__()
        async def identity(client_id):
            return await managed(DefaultAzureCredential(
                managed_identity_client_id=client_id, exclude_environment_credential=True,
                exclude_workload_identity_credential=True, exclude_shared_token_cache_credential=True,
                exclude_visual_studio_code_credential=True, exclude_cli_credential=True,
                exclude_powershell_credential=True, exclude_developer_cli_credential=True,
                exclude_interactive_browser_credential=True, exclude_broker_credential=True))
        async with asyncio.timeout(30):
            if config.credential_mode == "platform-noop":
                if platform_credential is None:
                    raise ValueError("authenticated_platform_credential_required")
                credential = platform_credential
            else:
                credential = await identity(config.service_client_id)
            http = await stack.enter_async_context(httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False))
            auth = EntraAuth(config, http)
            if config.credential_mode == "platform-noop":
                audience = config.audience if config.audience.startswith("api://") else "api://" + config.audience
                token = await credential.get_token(audience + "/.default")
                authenticated = await auth.authenticate("Bearer " + token.token)
                if (authenticated.workload is None or authenticated.client != config.service_client_id
                        or authenticated.workload.agent_id != config.expected_deployment.agent_id):
                    raise ValueError("platform_probe_identity_mismatch")
            cosmos = await managed(CosmosClient(config.cosmos_url, credential=credential,
                consistency_level="Strong", retry_total=0, connection_timeout=5, read_timeout=5))
            store = ProbeStore(None, cosmos.get_database_client(config.cosmos_database)
                               .get_container_client(config.cosmos_container),
                               account_reader=cosmos._get_database_account)
            crypto = await managed(CryptographyClient(config.key_id, credential=credential, retry_total=0))
            keys = await managed(KeyClient(config.key_id.split("/keys/")[0], credential=credential, retry_total=0))
            from govern_control_plane.models import SignedBundle
            with Path(config.signed_envelope_path).open("rb") as stream:
                signed = parse(SignedBundle, stream.read(16385))
            policy = await NativePolicy.load(
                bundle_path=Path(config.bundle_path), signed=signed.model_dump(mode="json"),
                signer=KeyVaultSigner(crypto, key_client=keys),
                tenant=config.tenant_id, key_id=config.key_id, policy_id=config.policy_id,
                version=config.policy_version, expected_digest=config.policy_digest,
                allowed_endpoints=config.allowed_endpoints, gateway_url=config.gateway_url)
            if policy.registry.deployment.model_dump() != config.expected_deployment.model_dump():
                raise ValueError("signed_probe_deployment_mismatch")
            native_digest = policy.registry.native_policy_digest
            if config.producer == "native" and native_digest is None:
                raise ValueError("separately_signed_native_policy_association_required")
            if config.credential_mode == "platform-noop" and (
                    len(policy.registry.actions) != 1
                    or policy.registry.actions[0].name != "governance_probe_noop"
                    or not policy.registry.actions[0].probe_safe
                    or policy.registry.actions[0].workloads != [authenticated.subject]):
                raise ValueError("dedicated_platform_noop_registration_required")
            service = ProbeService(store=store, registry=policy.registry,
                policy_digest=native_digest or policy.digest, producer=config.producer, fresh=policy.fresh)
            downstream = None
            if config.producer == "native":
                downstream = DownstreamClient(credential=credential if config.credential_mode == "platform-noop"
                                              else await identity(config.downstream_client_id))
                stack.push_async_callback(downstream.aclose)
            await store.health()
            await auth.health()
        yield service, auth, downstream
