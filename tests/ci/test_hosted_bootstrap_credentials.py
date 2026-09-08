"""Real Azure Identity constructors/token protocol against a loopback MSI fixture."""
import asyncio
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]
TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
AMBIENT = "33333333-3333-3333-3333-333333333333"


def cli():
    spec = importlib.util.spec_from_file_location("bootstrap_identity_cli", ROOT / "scripts/ci/hosted_bootstrap.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BASE = ["observe", "--creation", "not-read.json", "--attempt", "not-read-attempt.json"]


@pytest.mark.parametrize("options", [
    ["--credential-mode", "managed-identity"],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", ""],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", "not-a-guid"],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", "00000000-0000-0000-0000-000000000000"],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", "/subscriptions/resource"],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", "ABCDEFAB-1234-1234-1234-ABCDEFABCDEF"],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", " " + CLIENT],
    ["--credential-mode", "azure-cli", "--managed-identity-client-id", CLIENT],
    ["--managed-identity-client-id", CLIENT],
    ["--credential-mode", "default"],
    ["--credential-mode", "managed-identity", "--credential-mode", "azure-cli", "--managed-identity-client-id", CLIENT],
    ["--credential-mode", "managed-identity", "--managed-identity-client-id", CLIENT,
     "--managed-identity-client-id", AMBIENT],
])
def test_identity_arguments_fail_before_reading_protected_files(options, monkeypatch):
    app = cli()
    monkeypatch.setattr(app, "read", lambda *_: pytest.fail("invalid identity reached protected file access"))
    with pytest.raises(SystemExit) as failure:
        app.main(BASE + options)
    assert failure.value.code == 2


@pytest.mark.parametrize("command", ["create", "observe", "publish", "wait"])
def test_parser_retains_cli_default_and_accepts_explicit_managed_identity(command):
    app = cli()
    assert callable(getattr(app, "parse_args", None)), "independent credential parsing missing"
    base = [command, "--creation", "input.json", "--attempt", "attempt.json"]
    default = app.parse_args(base)
    assert default.credential_mode == "azure-cli" and default.managed_identity_client_id is None
    explicit = app.parse_args(base + ["--credential-mode", "azure-cli"])
    assert explicit.credential_mode == "azure-cli"
    managed = app.parse_args(base + ["--credential-mode", "managed-identity",
                                    "--managed-identity-client-id", CLIENT])
    assert managed.credential_mode == "managed-identity" and managed.managed_identity_client_id == CLIENT


@contextmanager
def msi_server(monkeypatch, *, deny=False):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((parse_qs(urlsplit(self.path).query), dict(self.headers)))
            body = ({"error": "invalid_client", "error_description": "local fixture rejects identity"} if deny else {
                "access_token": "local-managed-token", "expires_on": str(int(time.time()) + 300),
                "resource": "https://vault.azure.net", "token_type": "Bearer"})
            raw = json.dumps(body).encode()
            self.send_response(400 if deny else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    for name in ("MSI_ENDPOINT", "MSI_SECRET", "IDENTITY_SERVER_THUMBPRINT", "IMDS_ENDPOINT",
                 "AZURE_FEDERATED_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IDENTITY_ENDPOINT", f"http://127.0.0.1:{server.server_port}/token")
    monkeypatch.setenv("IDENTITY_HEADER", "local-fixture-header")
    monkeypatch.setenv("AZURE_CLIENT_ID", AMBIENT)
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("deny", [False, True])
def test_native_managed_identity_sdk_uses_only_explicit_client(monkeypatch, asynchronous, deny):
    import azure.identity
    import azure.identity.aio
    from azure.core.exceptions import ClientAuthenticationError
    app = cli()
    assert callable(getattr(app, "make_credential", None)), "explicit sync/aio credential factory missing"
    args = SimpleNamespace(credential_mode="managed-identity", managed_identity_client_id=CLIENT)
    def forbidden(*args, **kwargs):
        pytest.fail("managed identity must not construct CLI/default credentials")
    monkeypatch.setattr(azure.identity, "AzureCliCredential", forbidden)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", forbidden)
    monkeypatch.setattr(azure.identity.aio, "AzureCliCredential", forbidden)
    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", forbidden)
    with msi_server(monkeypatch, deny=deny) as requests:
        async def run_async():
            credential = app.make_credential(args, TENANT, asynchronous=True)
            assert isinstance(credential, azure.identity.aio.ManagedIdentityCredential)
            async with credential:
                if deny:
                    with pytest.raises(ClientAuthenticationError):
                        await credential.get_token("https://vault.azure.net/.default")
                else:
                    assert (await credential.get_token("https://vault.azure.net/.default")).token == "local-managed-token"
        if asynchronous:
            asyncio.run(run_async())
        else:
            credential = app.make_credential(args, TENANT)
            assert isinstance(credential, azure.identity.ManagedIdentityCredential)
            with credential:
                if deny:
                    with pytest.raises(ClientAuthenticationError):
                        credential.get_token("https://vault.azure.net/.default")
                else:
                    assert credential.get_token("https://vault.azure.net/.default").token == "local-managed-token"
        assert requests
        assert all(query.get("client_id") == [CLIENT] for query, _ in requests)
        assert all(query.get("resource") == ["https://vault.azure.net"] for query, _ in requests)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_azure_cli_credential_remains_tenant_bound(monkeypatch, asynchronous):
    import azure.identity
    import azure.identity.aio
    app = cli()
    assert callable(getattr(app, "make_credential", None)), "explicit sync/aio credential factory missing"
    namespace = azure.identity.aio if asynchronous else azure.identity
    real = namespace.AzureCliCredential
    observed = []
    def constructor(**kwargs):
        observed.append(kwargs)
        return real(**kwargs)
    monkeypatch.setattr(namespace, "AzureCliCredential", constructor)
    credential = app.make_credential(SimpleNamespace(credential_mode="azure-cli", managed_identity_client_id=None),
                                     TENANT, asynchronous=asynchronous)
    assert isinstance(credential, real)
    assert observed == [{"tenant_id": TENANT}]
    if asynchronous:
        asyncio.run(credential.close())
    else:
        credential.close()


def test_operator_docs_explain_explicit_identity_without_remote_state_claims():
    text = (ROOT / "skills/threadlight-deploy/references/governance/README.md").read_text()
    assert "--credential-mode managed-identity" in text
    assert "--managed-identity-client-id" in text
    assert "external durable create-intent guard" in text
