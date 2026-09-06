#!/usr/bin/env python3
"""Explicit operator lifecycle. Never deploy a second version to activate a binding."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    from govern_control_plane.models import strict_json
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("protected_input_symlink")
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("protected_input_too_large")
    return strict_json(raw)


def observe_parent(config, credential):
    """Authenticated ARM read establishes actual parent before any mutation."""
    import requests
    token = credential.get_token("https://management.azure.com/.default")
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            "https://management.azure.com" + config["project_id"] + "?api-version=2025-06-01",
            headers={"Authorization": "Bearer " + token.token}, timeout=30, allow_redirects=False)
        if response.status_code != 200 or len(response.content) > 65536:
            raise ValueError("project_parent_observation_unavailable")
        document = response.json()
    endpoints = document.get("properties", {}).get("endpoints", {})
    if (document.get("id", "").lower() != config["project_id"].lower()
            or config["project_endpoint"] not in endpoints.values()):
        raise ValueError("observed_project_parent_or_endpoint_mismatch")
    return document["id"]


async def publish(config, args, observed):
    from azure.identity.aio import AzureCliCredential
    from azure.storage.blob.aio import BlobServiceClient
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    from govern_control_plane.app import AzureConfiguration, ControlPlane
    from govern_control_plane.models import SignedBundle, canonical, parse
    from govern_control_plane.storage import AzureStore, KeyVaultSigner
    from govern_control_plane.hosted_lifecycle import binding_from_observation, persist
    frozen = read(args.frozen_config)
    final = parse(SignedBundle, canonical(read(args.policy_envelope)))
    publisher = parse(AzureConfiguration, canonical(read(args.publisher_config)))
    if publisher.tenant_id != config["tenant_id"] or publisher.key_id != frozen["key_id"]:
        raise ValueError("publisher_scope_mismatch")
    binding = binding_from_observation(
        config, frozen, observed, policy_digest=final.envelope.content_digest,
        policy_version=final.envelope.version, lifetime_seconds=args.lifetime_seconds)
    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(AzureCliCredential(tenant_id=config["tenant_id"]))
        blobs = await stack.enter_async_context(BlobServiceClient(
            publisher.blob_url, credential=credential, retry_total=0))
        crypto = await stack.enter_async_context(CryptographyClient(
            publisher.key_id, credential=credential, retry_total=0))
        keys = await stack.enter_async_context(KeyClient(
            publisher.key_id.split("/keys/")[0], credential=credential, retry_total=0))
        service = ControlPlane(publisher, AzureStore(blobs.get_container_client(publisher.blob_container), None),
                               KeyVaultSigner(crypto, key_client=keys))
        signed = await service.publish_bootstrap(binding)
        persist(args.binding_output, json.loads(canonical(signed)), exclusive=True)


async def wait_ready(config, args, observed):
    import httpx
    from azure.identity.aio import AzureCliCredential
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    from govern_control_plane.bootstrap import SignedBootstrap, verify
    from govern_control_plane.models import canonical, parse
    from govern_control_plane.storage import KeyVaultSigner
    signed = parse(SignedBootstrap, canonical(read(args.binding_output)))
    if config["protocol"] != "invocations":
        raise ValueError("native_responses_bootstrap_check_not_yet_supported")
    if any(getattr(signed.binding, key) != value for key, value in observed.items()):
        raise ValueError("published_binding_observation_mismatch")
    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(AzureCliCredential(tenant_id=config["tenant_id"]))
        crypto = await stack.enter_async_context(CryptographyClient(
            signed.binding.key_id, credential=credential, retry_total=0))
        keys = await stack.enter_async_context(KeyClient(
            signed.binding.key_id.split("/keys/")[0], credential=credential, retry_total=0))
        signer = KeyVaultSigner(crypto, key_client=keys)
        http = await stack.enter_async_context(httpx.AsyncClient(
            timeout=10, follow_redirects=False, trust_env=False))
        async with asyncio.timeout(args.wait_seconds):
            while True:
                await verify(signed, signer, tenant_id=config["tenant_id"], key_id=signed.binding.key_id)
                token = await credential.get_token("https://ai.azure.com/.default")
                response = await http.post(
                    config["project_endpoint"] + f"/agents/{config['agent_name']}"
                    + "/endpoint/protocols/invocations?api-version=v1",
                    headers={"Authorization": "Bearer " + token.token},
                    json={"input": "", "bootstrap_reference": signed.binding.reference})
                if response.status_code == 200:
                    if len(response.content) > 16384 or parse(
                            SignedBootstrap, canonical(response.json().get("bootstrap"))) != signed:
                        raise ValueError("hosted_bootstrap_check_mismatch")
                    return
                if response.status_code not in (404, 409, 429, 503):
                    raise ValueError("hosted_readiness_unavailable")
                await asyncio.sleep(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "observe", "publish", "wait"))
    parser.add_argument("--creation", required=True, type=Path)
    parser.add_argument("--attempt", required=True, type=Path)
    parser.add_argument("--credential-mode", required=True, choices=("azure-cli",),
                        help="Explicit tenant-bound CLI credential; no DefaultAzureCredential fallbacks")
    parser.add_argument("--frozen-config", type=Path)
    parser.add_argument("--policy-envelope", type=Path)
    parser.add_argument("--publisher-config", type=Path)
    parser.add_argument("--binding-output", type=Path)
    parser.add_argument("--lifetime-seconds", type=int, default=3600)
    parser.add_argument("--wait-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    from azure.identity import AzureCliCredential
    from azure.ai.projects import AIProjectClient
    from govern_control_plane.hosted_lifecycle import create_once, observe, validate
    from govern_control_plane.app import configure_logging
    configure_logging()
    config = validate(read(args.creation))
    if args.command == "publish" and not all((
            args.frozen_config, args.policy_envelope, args.publisher_config, args.binding_output)):
        parser.error("publish requires --frozen-config, --policy-envelope, --publisher-config, --binding-output")
    if args.command == "wait" and (not args.binding_output or not 1 <= args.wait_seconds <= 3600):
        parser.error("wait requires --binding-output and bounded --wait-seconds")
    with AzureCliCredential(tenant_id=config["tenant_id"]) as credential:
        observe_parent(config, credential)
        with AIProjectClient(endpoint=config["project_endpoint"], credential=credential,
                             api_version="v1", retry_total=0) as client:
            if args.command == "create":
                state = create_once(client, config, args.attempt)
                print(json.dumps({"status": "created-pending-binding", "version": state["version"]}))
                return
            observed = observe(client, config, args.attempt)
            if args.command == "publish":
                asyncio.run(publish(config, args, observed))
            elif args.command == "wait":
                asyncio.run(wait_ready(config, args, observed))
            print(json.dumps({"status": args.command + "-complete-not-live-proof", "observation": observed}))


if __name__ == "__main__":
    main()
