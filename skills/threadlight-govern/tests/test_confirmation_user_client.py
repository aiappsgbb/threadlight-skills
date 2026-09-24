import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from test_control_plane import Harness, module
from test_user_confirmation import configure_confirmation, pending_intent, user_headers


def test_confirmation_cli_refuses_unattended_context_or_decision(monkeypatch, capsys):
    client = module("confirmation_user")
    monkeypatch.setattr(client.sys.stdin, "isatty", lambda: False)
    assert client.main(["confirm", "--confirmation-id", "d" * 32,
        "--control-plane-url", "https://control.example", "--scope", "api://test/Governance.Confirm",
        "--tenant", "11111111-1111-1111-1111-111111111111",
        "--client-id", "55555555-5555-5555-5555-555555555555"]) == 2
    assert "Interactive" in capsys.readouterr().err


def test_confirmation_client_hash_verifies_protected_display():
    async def run():
        h = await configure_confirmation(await Harness().initialize())
        try:
            intent = await pending_intent(h)
            view = (await h.client.get("/confirmation/" + intent.confirmation_id,
                                      headers=user_headers(h))).json()
            client = module("confirmation_user")
            assert client.validate_view(view) == intent
            view["arguments"]["amount"] = 999
            with pytest.raises(ValueError):
                client.validate_view(view)
        finally:
            await h.confirmation_http.aclose()
            await h.close()
    asyncio.run(run())


def test_confirmation_cli_constructs_actual_installed_browser_credential(monkeypatch):
    client = module("confirmation_user")
    monkeypatch.setattr(client.sys.stdin, "isatty", lambda: True)
    # Invalid scope stops before any network/token interaction, but only after
    # constructing the installed SDK credential.
    assert client.main(["confirm", "--confirmation-id", "d" * 32,
        "--control-plane-url", "https://control.example", "--scope", "api://test/invalid",
        "--tenant", "11111111-1111-1111-1111-111111111111",
        "--client-id", "55555555-5555-5555-5555-555555555555"]) == 2


def test_pre_epoch_challenge_forces_real_credential_interaction_once():
    async def run():
        import httpx
        from types import SimpleNamespace
        from test_control_plane import TENANT
        client = module("confirmation_user")
        challenge = module("confirmation_entra").ClaimsChallenge("c1", TENANT)
        calls = []
        interacted = False
        async def get_token(scope, **kwargs):
            calls.append(("get_token", kwargs))
            return SimpleNamespace(token="fresh" if interacted else "old-acrs", expires_on=10**12)
        async def authenticate(**kwargs):
            nonlocal interacted
            calls.append(("authenticate", kwargs))
            interacted = True
        async def remote(request):
            if request.headers["authorization"] == "Bearer old-acrs":
                return httpx.Response(401, headers={
                    "WWW-Authenticate": challenge.header, "Retry-After": "1"})
            assert request.headers["authorization"] == "Bearer fresh"
            return httpx.Response(200, json={"status": "confirmed"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as http:
            user = client.ConfirmationUserClient(base_url="https://control.example",
                scope="api://control/Governance.Confirm", tenant=TENANT,
                credential=SimpleNamespace(get_token=get_token, authenticate=authenticate), http=http)
            assert await user.request("POST", "/confirmation/" + "d" * 32, {
                "approved": True, "intent_digest": "sha256:" + "a" * 64}, challenge=True) == {"status": "confirmed"}
        assert [name for name, _ in calls] == ["get_token", "authenticate", "get_token"]
        assert calls[1][1]["scopes"] == ["api://control/Governance.Confirm"]
        assert calls[1][1]["enable_cae"] is True
    asyncio.run(run())
