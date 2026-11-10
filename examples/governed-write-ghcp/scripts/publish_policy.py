#!/usr/bin/env python3
"""Sign (and optionally publish) a Task6 bundle digest under the PUBLISHER identity.

Run ONLY as a one-off job/container under the separate publisher managed identity
(``AZURE_CLIENT_ID``); never with a human or agent credential, never with a local
private key. It uses the portable control plane's own ``ControlPlane.publish`` and
``KeyVaultSigner``; there is no other signing code here.

* ``--mode publish``: immutable Blob publication (``overwrite=False``) of
  ``{tenant}/policies/{id}/{version}.json`` and the digest index. Prints the
  signed envelope.
* ``--mode sign-only``: prints a signed envelope WITHOUT publishing it. Used for
  the bootstrap package of a gateway pilot: the agent image does not embed the
  bundle, and the policy name/version stays reserved for the final registry
  bundle, which is published exactly once. A sign-only envelope is never live
  authority: runtime readiness reads the published final envelope.

All inputs come from environment variables so the job definition holds no files:
TL_TENANT_ID, TL_KEY_ID (versioned), TL_POLICY_ID, TL_POLICY_VERSION,
TL_CONTENT_DIGEST, TL_EXPIRES_AT (ISO UTC), TL_BLOB_URL, TL_BLOB_CONTAINER.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime
import json
import os
from types import SimpleNamespace


async def run(mode):
    from azure.identity.aio import ManagedIdentityCredential
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    from azure.storage.blob.aio import ContainerClient
    from govern_control_plane.app import ControlPlane
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest, parse
    from govern_control_plane.storage import KeyVaultSigner

    env = os.environ
    envelope = parse(BundleEnvelope, canonical({
        "policy_id": env["TL_POLICY_ID"], "version": env["TL_POLICY_VERSION"],
        "content_digest": env["TL_CONTENT_DIGEST"],
        "expires_at": datetime.fromisoformat(env["TL_EXPIRES_AT"]).isoformat(),
        "tenant_id": env["TL_TENANT_ID"], "key_id": env["TL_KEY_ID"],
    }))
    async with ManagedIdentityCredential(client_id=env["AZURE_CLIENT_ID"]) as credential, \
            CryptographyClient(envelope.key_id, credential) as crypto, \
            KeyClient(envelope.key_id.split("/keys/")[0], credential) as keys:
        signer = KeyVaultSigner(crypto, key_client=keys)
        await signer.health()
        if mode == "sign-only":
            signature = await signer.sign(envelope_digest(envelope))
            signed = SignedBundle(envelope=envelope, signature=base64.b64encode(signature).decode())
            if not await signer.verify(envelope_digest(envelope), signature):
                raise RuntimeError("signature_verification_failed")
        else:
            async with ContainerClient.from_container_url(
                    env["TL_BLOB_URL"].rstrip("/") + "/" + env["TL_BLOB_CONTAINER"], credential=credential) as blobs:
                settings = SimpleNamespace(tenant_id=envelope.tenant_id, key_id=envelope.key_id,
                                           request_timeout=30)
                store = SimpleNamespace(blob_create=lambda name, body: blobs.upload_blob(
                    name=name, data=body, overwrite=False))
                signed = await ControlPlane(settings, store, signer).publish(envelope)
    print("THREADLIGHT_SIGNED_ENVELOPE=" + json.dumps({"mode": mode, "signed": json.loads(canonical(signed))},
                                                       separators=(",", ":")), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("publish", "sign-only"), required=True)
    asyncio.run(run(parser.parse_args().mode))


if __name__ == "__main__":
    main()
