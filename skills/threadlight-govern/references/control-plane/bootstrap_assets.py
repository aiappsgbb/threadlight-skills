"""Bounded data-only assets selected by an immutable signed bootstrap binding."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
import shutil
from typing import Annotated
import uuid

from pydantic import Field, StringConstraints

from .models import Digest, Identifier, StrictModel, canonical, parse
from .storage import Conflict

CHUNK_BYTES = 8192
MAX_TOTAL_BYTES = 524288
REQUIRED = frozenset({
    "config.json", "envelope.json", "policy/manifest.yaml",
    "policy/bundle-metadata.json", "policy/gateway-registry.json",
})
AssetPath = Annotated[str, StringConstraints(max_length=160, pattern=(
    r"^(config\.json|envelope\.json|policy/(manifest\.yaml|bundle-metadata\.json|"
    r"gateway-registry\.json|[A-Za-z0-9_-]+\.(rego|json)))$"))]


class BootstrapAsset(StrictModel):
    path: AssetPath
    digest: Digest
    size: Annotated[int, Field(ge=1, le=65536)]


class NativeProbeConstraints(StrictModel):
    audience: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    cosmos_url: Annotated[str, StringConstraints(pattern=r"^https://[a-z0-9-]+\.documents\.azure\.com:443/$")]
    cosmos_database: Identifier
    gateway_url: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    allowed_endpoints: Annotated[tuple[Annotated[str, StringConstraints(max_length=512)], ...],
                                 Field(min_length=2, max_length=2)]
    fixture_scope: Annotated[str, StringConstraints(pattern=r"^api://[A-Za-z0-9._/-]+/\.default$")]
    policy_id: Identifier
    policy_version: Identifier
    controller_digest: Digest


def validate_descriptors(items):
    paths = [item.path for item in items]
    if (not 5 <= len(paths) <= 32 or len(paths) != len(set(paths)) or not REQUIRED <= set(paths)
            or sum(item.size for item in items) > MAX_TOTAL_BYTES):
        raise ValueError("native_bootstrap_asset_inventory_invalid")


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def descriptors(files):
    items = tuple(BootstrapAsset(path=path, digest=digest(raw), size=len(raw))
                  for path, raw in sorted(files.items()))
    validate_descriptors(items)
    return items


def read_directory(root):
    root = Path(root)
    if any(path.is_symlink() for path in (root, *root.parents)) or not root.is_dir():
        raise ValueError("protected_native_asset_directory_required")
    files = {}
    for count, file in enumerate(root.rglob("*")):
        if count >= 64 or file.is_symlink():
            raise ValueError("native_asset_inventory_invalid")
        if file.is_dir():
            if file != root / "policy":
                raise ValueError("native_asset_directory_invalid")
            continue
        path = parse(AssetPath, canonical(file.relative_to(root).as_posix()))
        if not file.is_file():
            raise ValueError("native_asset_file_required")
        with file.open("rb") as source:
            raw = source.read(65537)
        if not 0 < len(raw) <= 65536:
            raise ValueError("native_asset_size_invalid")
        files[path] = raw
    descriptors(files)
    return files


def blob_name(binding, path, chunk):
    return f"{binding.tenant_id}/bootstrap/{binding.reference}/assets/{path}.{chunk}"


def validate_content(binding, assets):
    expected = binding.native_probe_assets
    if expected is None:
        if assets is not None:
            raise Conflict()
        return
    if not isinstance(assets, dict) or set(assets) != {item.path for item in expected}:
        raise Conflict()
    for item in expected:
        raw = assets[item.path]
        if type(raw) is not bytes or len(raw) != item.size or digest(raw) != item.digest:
            raise Conflict()


async def publish(service, binding, assets):
    validate_content(binding, assets)
    for item in binding.native_probe_assets or ():
        raw = assets[item.path]
        for index, offset in enumerate(range(0, item.size, CHUNK_BYTES)):
            chunk = raw[offset:offset + CHUNK_BYTES]
            name = blob_name(binding, item.path, index)
            try:
                await service.store.blob_create(name, chunk)
            except Conflict:
                # An identical acknowledged chunk is reusable after a partial
                # publication; a different object is never overwritten.
                if await service.store.blob_read(name) != chunk:
                    raise


async def read(service, identity, reference, path, chunk):
    from .bootstrap import read as read_binding
    path = parse(AssetPath, canonical(path))
    signed = await read_binding(service, identity, reference)
    item = next((item for item in signed.binding.native_probe_assets or () if item.path == path), None)
    if item is None or chunk < 0 or chunk >= (item.size + CHUNK_BYTES - 1) // CHUNK_BYTES:
        raise ValueError("invalid_bootstrap_asset_chunk")
    raw = await service.store.blob_read(blob_name(signed.binding, path, chunk))
    if len(raw) != min(CHUNK_BYTES, item.size - chunk * CHUNK_BYTES):
        raise Conflict()
    return {"path": path, "chunk": chunk, "content": base64.b64encode(raw).decode()}


async def download(signed, client):
    from .bootstrap import fresh
    expected = signed.binding.native_probe_assets
    if expected is None:
        raise ValueError("native_bootstrap_assets_required")
    files = {}
    async with asyncio.timeout(120):
        for item in expected:
            raw = bytearray()
            for index in range((item.size + CHUNK_BYTES - 1) // CHUNK_BYTES):
                fresh(signed.binding)
                _, response = await client.request(
                    "GET", f"/bootstrap/{signed.binding.reference}/assets/{item.path}?chunk={index}")
                if (set(response) != {"path", "chunk", "content"}
                        or response["path"] != item.path or type(response["chunk"]) is not int
                        or response["chunk"] != index):
                    raise ValueError("invalid_bootstrap_asset_response")
                chunk = base64.b64decode(response["content"], validate=True)
                if len(chunk) != min(CHUNK_BYTES, item.size - index * CHUNK_BYTES):
                    raise ValueError("invalid_bootstrap_asset_size")
                raw.extend(chunk)
            if len(raw) != item.size or digest(raw) != item.digest:
                raise ValueError("bootstrap_asset_digest_mismatch")
            files[item.path] = bytes(raw)
        fresh(signed.binding)
    return files


async def materialize(signed, client, *, destination):
    """Return a private complete directory, never a mutable mounted/latest alias."""
    files = await download(signed, client)
    destination = Path(destination)
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError("host_owned_bootstrap_directory_required")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = destination.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise ValueError("host_owned_bootstrap_directory_required")
    root = destination / ("binding-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    try:
        (root / "policy").mkdir(mode=0o700)
        for name, raw in files.items():
            descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        return root
    except BaseException:
        shutil.rmtree(root)
        raise
