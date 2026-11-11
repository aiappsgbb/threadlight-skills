"""Offline tests for the user-scoped OBO downstream reference (no network)."""
import importlib.util
import logging
import pathlib
import time

import pytest

jwt = pytest.importorskip("jwt")
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

TENANT = "00000000-0000-0000-0000-0000000000aa"
API = "11111111-1111-1111-1111-111111111111"
SERVER = pathlib.Path(__file__).resolve().parents[1] / "references" / "obo-downstream" / "server.py"


@pytest.fixture(scope="module")
def server(monkeypatch_module):
    monkeypatch_module.setenv("OBO_TENANT_ID", TENANT)
    monkeypatch_module.setenv("OBO_API_CLIENT_ID", API)
    spec = importlib.util.spec_from_file_location("obo_downstream_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def monkeypatch_module():
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, **overrides):
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": API, "tid": TENANT,
              "exp": int(time.time()) + 600, "oid": "user-oid-1", "azp": "agent-identity-app",
              "scp": "Records.Read", "roles": ["Records.Reader"], "preferred_username": "a@contoso.test"}
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def test_allowed_user_sees_records_and_downstream_sees_user_and_actor(server, key):
    claims = server.validate(token(key), key=key.public_key())
    out = server.decide(claims)
    assert out["result"] == "ok" and out["records"]
    assert out["seen"]["user_oid"] == "user-oid-1"
    assert out["seen"]["actor_app_id"] == "agent-identity-app"
    assert out["seen"]["token_type"] == "delegated"


def test_user_without_app_role_is_denied_by_downstream(server, key):
    out = server.decide(server.validate(token(key, roles=[]), key=key.public_key()))
    assert out["result"] == "access_denied" and "records" not in out


def test_app_only_token_is_rejected(server, key):
    with pytest.raises(PermissionError) as exc:
        server.validate(token(key, scp=None, roles=["Records.Reader"]), key=key.public_key())
    assert exc.value.args == (403, "delegated_token_required")


@pytest.mark.parametrize("override,expected", [
    ({"aud": "api://someone-else"}, 401),
    ({"iss": "https://login.microsoftonline.com/other/v2.0"}, 401),
    ({"exp": int(time.time()) - 60}, 401),
    ({"scp": "Other.Scope"}, 403),
])
def test_invalid_tokens_are_rejected(server, key, override, expected):
    with pytest.raises(PermissionError) as exc:
        server.validate(token(key, **override), key=key.public_key())
    assert exc.value.args[0] == expected


def test_logs_never_contain_token_or_raw_user_id(server, key, caplog):
    raw = token(key)
    with caplog.at_level(logging.INFO, logger="obo-downstream"):
        server.decide(server.validate(raw, key=key.public_key()))
    text = caplog.text
    assert "decision=allow" in text
    assert raw not in text and "eyJ" not in text and "user-oid-1" not in text



def test_tool_declares_structured_output(server):
    # Through the Foundry toolbox the GHCP MCP client rejected a result whose
    # structuredContent was null (live, 2026-11-11): always return an object.
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    tool = next(t for t in tools if t.name == "list_my_records")
    assert tool.outputSchema and tool.outputSchema.get("type") == "object"
