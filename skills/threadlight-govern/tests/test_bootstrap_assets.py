"""Authenticated, signed and bounded native bootstrap data; never archive extraction."""
import asyncio
import base64
import hashlib
import importlib
from pathlib import Path

import pytest

from test_control_plane import module, run, OTHER
from test_remote_bootstrap import binding, harness, bootstrap


def assets_module():
    path = Path(__file__).parents[1] / "references/control-plane/bootstrap_assets.py"
    assert path.exists(), "bounded signed native bootstrap assets missing"
    return module("bootstrap_assets")


def content():
    return {
        "config.json": b'{"fixture":"typed producer validation occurs before activation"}',
        "envelope.json": b'{"fixture":"signed probe association"}',
        "policy/manifest.yaml": b"fixture: native manifest\n",
        "policy/bundle-metadata.json": b'{"fixture":"digest"}',
        "policy/gateway-registry.json": b'{"fixture":"registered noop only"}',
        "policy/safe.rego": b"package fixture\n" + b"# bounded fixture\n" * 900,
    }


def descriptors(files):
    return [{"path": path, "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
            for path, raw in sorted(files.items())]


def test_assets_use_authenticated_create_only_chunked_blob_transport():
    assets = assets_module()
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            files = content()
            envelope = module("models").parse(b.BootstrapBinding, module("models").canonical({
                **binding(), "native_probe_assets": descriptors(files)}))
            signed = await h.service.publish_bootstrap(envelope, assets=files)
            assert signed.binding.native_probe_assets
            assert len(h.store.blobs) > len(files), "large assets must use bounded Blob objects"
            for path, raw in files.items():
                received = bytearray()
                count = (len(raw) + assets.CHUNK_BYTES - 1) // assets.CHUNK_BYTES
                for chunk in range(count):
                    response = await h.client.get(
                        f"/bootstrap/attempt-1/assets/{path}?chunk={chunk}", headers=h.headers())
                    assert response.status_code == 200
                    received.extend(base64.b64decode(response.json()["content"], validate=True))
                assert bytes(received) == raw
            denied = await h.client.get("/bootstrap/attempt-1/assets/config.json?chunk=0",
                                       headers=h.headers(changes={"oid": OTHER}))
            assert denied.status_code == 403
            assert (await h.client.get("/bootstrap/attempt-1/assets/config.json?chunk=100",
                                      headers=h.headers())).status_code == 422
            with pytest.raises(module("storage").Conflict):
                await h.service.publish_bootstrap(envelope, assets={**files, "config.json": b"changed"})
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("path", ["../evil.py", "policy/../evil.py", "/config.json",
                                 "policy/code.py", "policy/link/manifest.yaml", "policy/safe.rego/extra"])
def test_asset_paths_cannot_be_executable_or_escape(path):
    assets_module()
    with pytest.raises(ValueError):
        module("models").parse(bootstrap().BootstrapBinding, module("models").canonical({
            **binding(), "native_probe_assets": descriptors({**content(), path: b"bad"})}))


def test_partial_or_tampered_assets_never_publish_a_binding():
    assets_module()
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            files = content()
            envelope = module("models").parse(b.BootstrapBinding, module("models").canonical({
                **binding(), "native_probe_assets": descriptors(files)}))
            for invalid in ({}, {**files, "config.json": b"tampered"},
                            {k: v for k, v in files.items() if k != "envelope.json"}):
                with pytest.raises((ValueError, module("storage").Conflict)):
                    await h.service.publish_bootstrap(envelope, assets=invalid)
                assert not any("/bootstrap/" in path for path in h.store.blobs)
        finally:
            await h.close()
    run(scenario())


def test_authenticated_client_materializes_only_verified_bytes(tmp_path):
    assets = assets_module()
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            files = content()
            envelope = module("models").parse(b.BootstrapBinding, module("models").canonical({
                **binding(), "native_probe_assets": descriptors(files)}))
            signed = await h.service.publish_bootstrap(envelope, assets=files)
            from types import SimpleNamespace
            class Credential:
                async def get_token(self, scope):
                    return SimpleNamespace(token=h.token())
            async with b.BootstrapClient(base_url="https://control.example",
                    scope="api://governance/.default", credential=Credential(), http=h.client) as client:
                path = await assets.materialize(signed, client, destination=tmp_path)
                assert {p.relative_to(path).as_posix(): p.read_bytes()
                        for p in path.rglob("*") if p.is_file()} == files
                name = next(name for name in h.store.blobs if "/assets/" in name and "config.json" in name)
                h.store.blobs[name] = b"tampered"
                before = set(tmp_path.iterdir())
                with pytest.raises((ValueError, RuntimeError, module("client").ApprovalUnavailable)):
                    await assets.materialize(signed, client, destination=tmp_path)
                assert set(tmp_path.iterdir()) == before
        finally:
            await h.close()
    run(scenario())


def test_retryable_asset_preparation_precedes_one_time_application_activation():
    assets_module()
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(module("models").parse(
                b.BootstrapBinding, module("models").canonical(binding())))
            preparations, effects = [], []
            async def fetch():
                return signed
            async def prepare(signed, stack):
                preparations.append("prepare")
                if len(preparations) == 1:
                    raise module("client").ApprovalUnavailable()
            async def initialize(binding, stack):
                from starlette.responses import JSONResponse
                effects.append("initialize")
                stack.callback(effects.append, "close")
                return JSONResponse({})
            gate = b.BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                   prepare=prepare, initialize=initialize)
            with pytest.raises(module("client").ApprovalUnavailable):
                await gate.activate()
            assert effects == [] and not gate.invalid
            await asyncio.gather(*(gate.activate() for _ in range(4)))
            assert preparations == ["prepare", "prepare"]
            assert effects == ["initialize"]
            await gate.aclose()
            assert effects == ["initialize", "close"]
        finally:
            await h.close()
    run(scenario())


def test_operator_asset_inventory_is_bounded_and_refuses_symlinks(tmp_path):
    assets = assets_module()
    assert callable(getattr(assets, "read_directory", None)), "operator native asset input reader missing"
    for path, raw in content().items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
    assert assets.read_directory(tmp_path) == content()
    (tmp_path / "config.json").unlink()
    (tmp_path / "config.json").symlink_to(tmp_path / "envelope.json")
    with pytest.raises(ValueError):
        assets.read_directory(tmp_path)


def test_asset_publication_has_a_separate_bounded_bulk_deadline():
    assets_module()
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            files = content()
            envelope = module("models").parse(b.BootstrapBinding, module("models").canonical({
                **binding(), "native_probe_assets": descriptors(files)}))
            h.service.settings = h.service.settings.model_copy(update={"request_timeout": 0.01})
            original = h.service.store.blob_create
            async def delayed(name, raw):
                await asyncio.sleep(0.02)
                return await original(name, raw)
            h.service.store.blob_create = delayed
            signed = await h.service.publish_bootstrap(envelope, assets=files)
            assert signed.binding.reference == "attempt-1"
        finally:
            await h.close()
    run(scenario())


def test_bootstrap_read_revalidates_immutable_policy_indexes():
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            await h.service.publish_bootstrap(module("models").parse(
                b.BootstrapBinding, module("models").canonical(binding())))
            h.store.blobs[h.service.digest_name(binding()["policy_digest"])] = b"{}"
            response = await h.client.get("/bootstrap/attempt-1", headers=h.headers())
            assert response.status_code != 200
        finally:
            await h.close()
    run(scenario())


def test_operator_recovery_never_extends_or_replaces_an_existing_binding():
    from datetime import datetime, timedelta, timezone
    lifecycle = module("hosted_lifecycle")
    assert callable(getattr(lifecycle, "publish_or_recover", None)), "immutable publication recovery missing"
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            original = module("models").parse(b.BootstrapBinding, module("models").canonical(binding()))
            signed = await lifecycle.publish_or_recover(h.service, original)
            before = dict(h.store.blobs)
            candidate = original.model_copy(update={
                "issued_at": datetime.now(timezone.utc), "expires_at": original.expires_at + timedelta(seconds=30)})
            recovered = await lifecycle.publish_or_recover(h.service, candidate)
            assert recovered == signed
            assert recovered.binding.expires_at == original.expires_at
            assert h.store.blobs == before
            with pytest.raises(ValueError):
                await lifecycle.publish_or_recover(h.service, candidate.model_copy(update={"client_id": OTHER}))
        finally:
            await h.close()
    run(scenario())
