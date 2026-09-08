"""Azure durable adapters. A successful CAS acknowledgement is the consume boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.keyvault.keys.crypto import SignatureAlgorithm


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


class Store(Protocol):
    async def health(self): ...
    async def blob_create(self, name: str, body: bytes): ...
    async def blob_read(self, name: str) -> bytes: ...
    async def read(self, scope: str, key: str) -> tuple[dict, str]: ...
    async def create(self, scope: str, key: str, body: dict): ...
    async def replace(self, scope: str, key: str, body: dict, etag: str): ...


class BundleSigner(Protocol):
    async def sign(self, digest: bytes) -> bytes: ...
    async def verify(self, digest: bytes, signature: bytes) -> bool: ...
    async def health(self): ...


async def sdk_call(awaitable):
    try:
        return await awaitable
    except HttpResponseError as error:
        if error.status_code in (409, 412):
            raise Conflict() from None
        if error.status_code == 404:
            raise Missing() from None
        raise RuntimeError("storage_unavailable") from None


class AzureStore:
    def __init__(self, blobs, documents, *, account_reader=None):
        self.blobs, self.documents = blobs, documents
        self.account_reader = account_reader

    async def health(self):
        await sdk_call(self.blobs.get_container_properties())
        await self.write_safety()

    async def write_safety(self):
        properties = await sdk_call(self.documents.read())
        if properties.get("partitionKey", {}).get("paths") != ["/scope"]:
            raise RuntimeError("invalid_partition")
        # TTL would eventually reopen an approval nonce for reuse.
        if properties.get("defaultTtl") is not None:
            raise RuntimeError("ttl_not_supported")
        if self.account_reader is None:
            raise RuntimeError("account_verification_required")
        account = await sdk_call(self.account_reader())
        if len(account.WritableLocations) != 1:
            raise RuntimeError("multiple_writers_not_supported")

    async def blob_create(self, name, body):
        await sdk_call(self.blobs.upload_blob(name=name, data=body, overwrite=False))

    async def blob_read(self, name):
        download = await sdk_call(self.blobs.download_blob(name))
        if download.size > 16384:
            raise Conflict()
        body = await sdk_call(download.readall())
        if len(body) > 16384:
            raise Conflict()
        return body

    async def read(self, scope, key):
        item = await sdk_call(self.documents.read_item(item=key, partition_key=scope))
        if item["id"] != key or item["scope"] != scope or not item.get("_etag"):
            raise Conflict()
        return item["body"], item["_etag"]

    async def create(self, scope, key, body):
        await self.write_safety()
        await sdk_call(self.documents.create_item(
            body={"id": key, "scope": scope, "body": body}))

    async def replace(self, scope, key, body, etag):
        await self.write_safety()
        await sdk_call(self.documents.replace_item(
            item=key, body={"id": key, "scope": scope, "body": body},
            etag=etag, match_condition=MatchConditions.IfNotModified))


class KeyVaultSigner:
    def __init__(self, crypto, *, key_client=None):
        self.crypto = crypto
        self.key_client = key_client

    async def sign(self, digest):
        if len(digest) != 32:
            raise ValueError("digest_required")
        return (await self.crypto.sign(SignatureAlgorithm.rs256, digest)).signature

    async def verify(self, digest, signature):
        if len(digest) != 32:
            return False
        return (await self.crypto.verify(SignatureAlgorithm.rs256, digest, signature)).is_valid

    async def health(self):
        if self.key_client is None:
            raise RuntimeError("key_verification_required")
        name, version = self.crypto.key_id.split("/")[-2:]
        key = await self.key_client.get_key(name, version)
        now = datetime.now(timezone.utc)
        if (key.id != self.crypto.key_id or key.properties.enabled is not True
                or (key.properties.expires_on is not None and key.properties.expires_on <= now)
                or (key.properties.not_before is not None and key.properties.not_before > now)):
            raise RuntimeError("key_unavailable")
