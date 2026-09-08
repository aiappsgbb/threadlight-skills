"""Remote bootstrap trust tests: real Entra/RSA, ASGI and production Azure adapters."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from test_control_plane import (
    APP, DIGEST, KEY, OTHER, TENANT, WORKLOAD, Harness, module, run,
)


def bootstrap():
    path = Path(__file__).parents[1] / "references/control-plane/bootstrap.py"
    assert path.exists(), "signed remote bootstrap implementation missing"
    return module("bootstrap")


def binding():
    now = datetime.now(timezone.utc)
    return dict(
        schema="threadlight-hosted-bootstrap/v1", reference="attempt-1",
        tenant_id=TENANT, key_id=KEY, policy_id="safe", policy_version="1", native_policy_digest=DIGEST,
        config_digest=DIGEST, policy_digest=DIGEST,
        project_endpoint="https://test.services.ai.azure.com/api/projects/test",
        agent_id="agent-1", agent_version="17", image_digest=DIGEST,
        subscription=TENANT, resource_group="test-rg", environment="preproduction",
        principal=WORKLOAD, client_id=APP, issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
    )


async def harness():
    h = await Harness().initialize()
    # Only the external Blob and Key Vault transports are fixtures.
    storage = module("storage")
    memory = h.store

    class Blobs:
        async def upload_blob(self, *, name, data, overwrite):
            assert overwrite is False
            await memory.blob_create(name, data)

        async def download_blob(self, name):
            raw = await memory.blob_read(name)

            async def readall():
                return raw
            return SimpleNamespace(size=len(raw), readall=readall)

    signing = h.signer

    class Crypto:
        key_id = KEY

        async def sign(self, algorithm, digest):
            return SimpleNamespace(signature=await signing.sign(digest))

        async def verify(self, algorithm, digest, signature):
            return SimpleNamespace(is_valid=await signing.verify(digest, signature))

    class Keys:
        revoked = False

        async def get_key(self, name, version):
            assert KEY.endswith(f"/{name}/{version}")
            return SimpleNamespace(id=KEY, properties=SimpleNamespace(
                enabled=not self.revoked, expires_on=None, not_before=None))

    h.keys = Keys()
    h.service.store = storage.AzureStore(Blobs(), None)
    h.service.signer = storage.KeyVaultSigner(Crypto(), key_client=h.keys)
    return h


def test_publish_is_immutable_authorized_read_and_real_signature():
    b = bootstrap()

    async def scenario():
        h = await harness()
        try:
            envelope = module("models").parse(b.BootstrapBinding, module("models").canonical(binding()))
            signed = await h.service.publish_bootstrap(envelope)
            assert await h.signer.verify(
                hashlib.sha256(module("models").canonical(envelope)).digest(),
                base64.b64decode(signed.signature))
            with pytest.raises(module("storage").Conflict):
                await h.service.publish_bootstrap(envelope)
            response = await h.client.get("/bootstrap/attempt-1", headers=h.headers())
            assert response.status_code == 200
            assert response.json()["binding"]["agent_version"] == "17"
            assert (await h.client.get("/bootstrap/attempt-1")).status_code == 401
            assert (await h.client.get("/bootstrap/attempt-1",
                headers=h.headers(changes={"oid": OTHER}))).status_code == 403
            assert (await h.client.post("/bootstrap/attempt-1",
                headers=h.headers(), json=binding())).status_code == 405
            h.keys.revoked = True
            assert (await h.client.get("/bootstrap/attempt-1", headers=h.headers())).status_code == 503
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("field,value", [
    ("reference", "attempt-2"), ("agent_version", "18"), ("image_digest", "sha256:" + "b" * 64),
    ("native_policy_digest", "sha256:" + "b" * 64), ("config_digest", "sha256:" + "b" * 64),
    ("principal", OTHER), ("client_id", OTHER), ("key_id", KEY[:-1] + "b"),
    ("project_endpoint", "https://other.services.ai.azure.com/api/projects/test"),
    ("expires_at", "2020-01-01T00:00:00+00:00"),
])
def test_wrong_binding_never_initializes(field, value):
    b = bootstrap()

    async def scenario():
        h = await harness()
        try:
            expected = binding()
            envelope = dict(expected, **{field: value})
            wire = module("models").parse(b.BootstrapBinding, module("models").canonical(envelope))
            signature = await h.signer.sign(hashlib.sha256(module("models").canonical(wire)).digest())
            signed = b.SignedBootstrap(binding=wire, signature=base64.b64encode(signature).decode())
            effects = []

            async def fetch():
                return signed

            async def initialize(binding, stack):
                effects.append("application")
                return lambda scope, receive, send: None

            gate = b.BootstrapGate(expected=expected, fetch=fetch, signer=h.service.signer,
                                   initialize=initialize)
            with pytest.raises(b.BootstrapUnavailable):
                await gate.activate()
            assert effects == []
            await gate.aclose()
        finally:
            await h.close()
    run(scenario())


def test_authenticated_client_concurrent_activation_expiry_and_cleanup():
    b = bootstrap()

    async def scenario():
        h = await harness()
        try:
            expected = binding()
            await h.service.publish_bootstrap(module("models").parse(
                b.BootstrapBinding, module("models").canonical(expected)))

            class Credential:
                async def get_token(self, scope):
                    assert scope == "api://governance/.default"
                    return SimpleNamespace(token=h.token())

            client = b.BootstrapClient(
                base_url="https://control.example", scope="api://governance/.default",
                credential=Credential(), http=h.client)
            effects = []

            async def initialize(binding, stack):
                effects.append("start")
                stack.callback(effects.append, "close")
                from starlette.responses import JSONResponse
                return JSONResponse({"application": "ready"})

            async def fetch():
                return await client.load("attempt-1")

            gate = b.BootstrapGate(expected=expected, fetch=fetch, signer=h.service.signer,
                                   initialize=initialize)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                        base_url="http://local") as http:
                assert (await http.post("/responses")).status_code == 503
                assert (await http.get("/liveness")).status_code == 200
                await asyncio.gather(*(gate.activate() for _ in range(8)))
                assert effects == ["start"]
                assert (await http.post("/responses")).status_code == 200
                checked = await http.post("/invocations", json={"input": "", "bootstrap_reference": "attempt-1"})
                assert checked.status_code == 200
                assert checked.json()["bootstrap"]["binding"]["agent_version"] == "17"
                wrong = await http.post("/invocations", json={"input": "", "bootstrap_reference": "wrong"})
                assert wrong.status_code == 503
                h.keys.revoked = True
                assert (await http.post("/responses")).status_code == 503
                assert effects == ["start"]
            await gate.aclose()
            await gate.aclose()
            assert effects == ["start", "close"]
        finally:
            await h.close()
    run(scenario())


def test_invalid_signature_missing_publication_and_expiry_after_await():
    b = bootstrap()

    async def scenario():
        h = await harness()
        try:
            expected = binding()
            wire = module("models").parse(b.BootstrapBinding, module("models").canonical(expected))
            signed = b.SignedBootstrap(binding=wire, signature=base64.b64encode(b"bad").decode())
            effects = []

            async def fetch():
                return signed

            async def initialize(binding, stack):
                effects.append("initialize")
                stack.callback(effects.append, "close")
                return lambda scope, receive, send: None

            gate = b.BootstrapGate(expected=expected, fetch=fetch, signer=h.service.signer,
                                   initialize=initialize)
            with pytest.raises(b.BootstrapUnavailable):
                await gate.activate()
            assert not effects
            signed = await h.service.publish_bootstrap(wire)
            await gate.activate()
            signed.binding.__dict__["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            with pytest.raises(b.BootstrapUnavailable):
                gate.check()
            await gate.aclose()
            assert effects == ["initialize", "close"]
        finally:
            await h.close()
    run(scenario())


def test_runtime_gate_requires_frozen_configuration_and_platform_identity(monkeypatch):
    b = bootstrap()
    assert callable(getattr(b, "runtime_gate", None)), "generated runtime gate factory missing"
    config = dict(
        tenant_id=TENANT, key_id=KEY, agent_id="agent-1", policy_id="safe",
        environment="preproduction", control_plane_url="https://control.example",
        control_plane_scope="api://governance/.default", remote_bootstrap={
            "reference": "attempt-1",
            "project_endpoint": "https://test.services.ai.azure.com/api/projects/test",
            "subscription": TENANT, "resource_group": "test-rg", "native_policy_digest": DIGEST,
        })
    env = dict(FOUNDRY_AGENT_VERSION="17", FOUNDRY_AGENT_NAME="agent-1",
               FOUNDRY_PROJECT_ENDPOINT=config["remote_bootstrap"]["project_endpoint"],
               TL_GOV_IMAGE_DIGEST=DIGEST, GOV_CONTROL_PLANE_URL=config["control_plane_url"])
    gate = b.runtime_gate(config, env=env, credential=None, signer=None,
                          initialize=None)
    assert gate.expected["agent_version"] == "17"
    assert gate.expected["config_digest"] == "sha256:" + hashlib.sha256(
        module("models").canonical(config)).hexdigest()
    for key in ("FOUNDRY_AGENT_VERSION", "FOUNDRY_AGENT_NAME", "FOUNDRY_PROJECT_ENDPOINT"):
        changed = dict(env)
        changed.pop(key)
        with pytest.raises((ValueError, KeyError)):
            b.runtime_gate(config, env=changed, credential=None, signer=None, initialize=None)
    with pytest.raises(ValueError):
        b.runtime_gate(config, env=dict(env, FOUNDRY_AGENT_NAME="wrong"),
                       credential=None, signer=None, initialize=None)


def test_native_probe_platform_credential_is_explicit_not_an_attached_uami():
    from test_probe_telemetry import gateway, probe_registry, controller_config
    h = Harness()
    try:
        runtime = gateway("probe_runtime")
        config = {
            **h.settings.model_dump(), "enabled": True, "producer": "native",
            "credential_mode": "platform-noop", "service_client_id": APP,
            "cosmos_url": "https://probe.documents.azure.com:443/",
            "cosmos_database": "governance", "cosmos_container": "probe-native",
            "bundle_path": "/config/probe-policy", "signed_envelope_path": "/config/envelope.json",
            "policy_id": "safe", "policy_version": "1", "policy_digest": DIGEST,
            "gateway_url": "https://gateway.example/mcp",
            "allowed_endpoints": ["https://fixture.example/governance/noop",
                                  "https://fixture.example/governance/outcomes"],
            "expected_deployment": probe_registry()["deployment"],
            "probe_controllers": controller_config(),
        }
        parsed = runtime.ProbeConfiguration.model_validate(config)
        assert parsed.downstream_client_id is None
        for change in (
            {"producer": "fixture", "cosmos_container": "probe-fixture", "fixture_callers": {WORKLOAD: APP}},
            {"downstream_client_id": OTHER},
            {"fixture_callers": {WORKLOAD: APP}},
        ):
            with pytest.raises(ValueError):
                runtime.ProbeConfiguration.model_validate({**config, **change})
    finally:
        run(h.close())


def test_publication_requires_current_exact_policy_chain():
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            for change in (
                {"policy_digest": "sha256:" + "b" * 64},
                {"native_policy_digest": "sha256:" + "b" * 64},
                {"policy_version": "99"},
                {"expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()},
            ):
                with pytest.raises((module("storage").Conflict, module("storage").Missing,
                                    b.BootstrapUnavailable)):
                    await h.service.publish_bootstrap(module("models").parse(
                        b.BootstrapBinding, module("models").canonical({**binding(), **change})))
        finally:
            await h.close()
    run(scenario())


@pytest.mark.parametrize("failure", [TimeoutError, OSError])
def test_active_bootstrap_dependency_errors_remain_bounded_503(failure):
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(module("models").parse(
                b.BootstrapBinding, module("models").canonical(binding())))
            unavailable = False
            async def fetch():
                if unavailable:
                    raise failure("private external diagnostic")
                return signed
            async def initialize(binding, stack):
                from starlette.responses import JSONResponse
                return JSONResponse({"unexpected": "effect"})
            gate = b.BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                   initialize=initialize)
            await gate.activate()
            unavailable = True
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                        base_url="http://local") as client:
                response = await client.get("/readiness")
                assert response.status_code == 503
                assert "private" not in response.text
            await gate.aclose()
        finally:
            await h.close()
    run(scenario())


def test_request_cannot_observe_partially_initialized_host():
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(module("models").parse(
                b.BootstrapBinding, module("models").canonical(binding())))
            entered, release = asyncio.Event(), asyncio.Event()
            async def fetch():
                return signed
            async def initialize(binding, stack):
                entered.set()
                await release.wait()
                from starlette.responses import JSONResponse
                return JSONResponse({"ready": True})
            gate = b.BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                   initialize=initialize)
            task = asyncio.create_task(gate.activate())
            try:
                await entered.wait()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                            base_url="http://local") as client:
                    assert (await client.post("/responses")).status_code == 503
                    release.set()
                    await task
                    assert (await client.post("/responses")).status_code == 200
            finally:
                release.set()
                await task
                await gate.aclose()
        finally:
            await h.close()
    run(scenario())


def test_key_health_wait_is_bounded_before_application_activation():
    b = bootstrap()
    async def scenario():
        h = await harness()
        try:
            signed = await h.service.publish_bootstrap(module("models").parse(
                b.BootstrapBinding, module("models").canonical(binding())))
            async def fetch():
                return signed
            async def initialize(binding, stack):
                pytest.fail("application initialized during unavailable key verification")
            async def unavailable(*args):
                await asyncio.sleep(60)
            h.keys.get_key = unavailable
            gate = b.BootstrapGate(expected=binding(), fetch=fetch, signer=h.service.signer,
                                   initialize=initialize)
            task = asyncio.create_task(gate.activate())
            done, pending = await asyncio.wait({task}, timeout=6)
            if pending:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert done, "bootstrap key verification has no bounded deadline"
            assert isinstance(task.exception(), TimeoutError)
            await gate.aclose()
        finally:
            await h.close()
    run(scenario())
