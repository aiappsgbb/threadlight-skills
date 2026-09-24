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
