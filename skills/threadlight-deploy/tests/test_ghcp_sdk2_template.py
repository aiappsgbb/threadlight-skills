"""GHCP template on Microsoft Copilot SDK 2.x (#202), with the 1.0.x line kept as ``sdk_line: v1``.

Loads the real ``container.py`` with a fake ``copilot`` module shaped like
``microsoft-copilot-sdk`` 2.0.1 (``RuntimeConnection`` present, no
``ProviderConfig``) and checks the client and session options it sends. The
shapes follow https://github.com/microsoft/copilot-sdk/blob/main/docs/v2-breaking-changes.md
(explicit client mode, nested session options, named provider connections).
Native SDK behaviour is covered by the 2.0 lane of run-governance-pin-tests.py
and the live pilot, not here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import tomllib
import types
from pathlib import Path

import pytest

pytest.importorskip("starlette")

DEPLOY = Path(__file__).resolve().parents[1]
GHCP = DEPLOY / "references" / "hosted-agent" / "ghcp" / "references"
CONTAINER = GHCP / "container.py"


class _Type:
    def __init__(self, value):
        self.value = value


class FakeSession:
    def __init__(self, session_id):
        self.session_id = session_id

    def on(self, handler):
        return lambda: None

    async def disconnect(self):
        pass


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.created: list[dict] = []
        self.resumed: list[dict] = []
        FakeClient.instances.append(self)

    async def start(self):
        pass

    async def get_session_metadata(self, session_id):
        return None

    async def create_session(self, **kwargs):
        self.created.append(kwargs)
        return FakeSession(kwargs.get("session_id") or "s-1")

    async def resume_session(self, session_id, **kwargs):
        self.resumed.append(kwargs)
        return FakeSession(session_id)

    async def delete_session(self, session_id):
        pass


class FakeRuntimeConnection:
    @staticmethod
    def for_default(*, base_directory=None):
        return {"kind": "default", "base_directory": base_directory}


class FakeCredential:
    def get_token(self, scope):
        assert scope == "https://ai.azure.com/.default"
        return types.SimpleNamespace(token="entra-token", expires_on=10**10)


def _load(monkeypatch, *, v2=True, byok=True, gateway=""):
    FakeClient.instances.clear()
    host = types.ModuleType("azure.ai.agentserver.invocations")
    host.InvocationAgentServerHost = type("Host", (), {"invoke_handler": lambda self, fn: fn})
    copilot = types.ModuleType("copilot")
    copilot.CopilotClient = FakeClient
    copilot.PermissionHandler = types.SimpleNamespace(approve_all=lambda *a, **k: None)
    if v2:
        copilot.RuntimeConnection = FakeRuntimeConnection
    else:
        copilot.ProviderConfig = lambda **kw: kw
        copilot.RuntimeConnection = type("RC1", (), {})  # 1.0.1 has it, without for_default
    events = types.ModuleType("copilot.session_events")
    events.SessionEventType = types.SimpleNamespace(SESSION_IDLE=_Type("session.idle"),
                                                    SESSION_ERROR=_Type("session.error"))
    identity = types.ModuleType("azure.identity")
    identity.DefaultAzureCredential = FakeCredential
    for name, module in {"azure.ai.agentserver.invocations": host, "copilot": copilot,
                         "copilot.session_events": events, "azure.identity": identity}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    if byok:
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://acct.services.ai.azure.com/api/projects/p1/")
        monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-test")
    else:
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "test-only-not-a-token")
    monkeypatch.setenv("MODEL_GATEWAY_URL", gateway)
    spec = importlib.util.spec_from_file_location("ghcp_sdk2_under_test", CONTAINER)
    module = importlib.util.module_from_spec(spec)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    if byok:
        assert module._init_byok()
    return module


def _open(module, key=""):
    async def run():
        async with module._conversation(key) as session:
            return session
    return asyncio.run(run())


def test_sdk2_client_declares_empty_mode_and_a_state_directory(monkeypatch):
    module = _load(monkeypatch)
    _open(module)
    (client,) = FakeClient.instances
    assert client.kwargs["mode"] == "empty"
    assert client.kwargs["runtime"]["base_directory"].endswith(".copilot")
    assert client.kwargs["github"] == {"use_logged_in_user": False}


def test_sdk2_session_uses_nested_options_and_a_named_foundry_provider(monkeypatch):
    module = _load(monkeypatch)
    _open(module)
    (options,) = FakeClient.instances[0].created
    for flat in ("provider", "available_tools", "on_permission_request", "streaming", "mcp_servers",
                 "skill_directories"):
        assert flat not in options, flat
    assert options["model"] == "foundry/gpt-test"
    assert options["github"] == {"capi": {"enabled": False}}
    assert options["events"] == {"streaming": True}
    assert options["permissions"]["on_request"] is module.PermissionHandler.approve_all
    assert options["tools"]["available_tools"] == ["mcp:*", "custom:*", "builtin:skill"]
    (provider,) = options["inference"]["providers"]
    assert {k: provider[k] for k in ("name", "type", "wire_api", "base_url")} == {
        "name": "foundry", "type": "openai", "wire_api": "responses",
        "base_url": "https://acct.services.ai.azure.com/api/projects/p1/openai/v1"}
    assert "bearer_token" not in provider and "api_key" not in provider
    assert asyncio.run(provider["bearer_token_provider"]({"provider_name": "foundry"})) == "entra-token"
    assert options["inference"]["models"] == [
        {"provider": "foundry", "id": "gpt-test", "model_id": "gpt-test", "wire_model": "gpt-test"}]


def test_sdk2_keyed_resume_reregisters_the_token_callback(monkeypatch):
    module = _load(monkeypatch)
    _open(module, "foundry-a")
    client = FakeClient.instances[0]
    (created,) = client.created
    assert created["session_id"] == "foundry-a"
    assert callable(created["inference"]["providers"][0]["bearer_token_provider"])


def test_sdk2_model_gateway_route_is_used_as_the_openai_v1_base(monkeypatch):
    module = _load(monkeypatch, gateway="https://apim.example/foundry/openai/v1")
    _open(module)
    (options,) = FakeClient.instances[0].created
    assert options["inference"]["providers"][0]["base_url"] == "https://apim.example/foundry/openai/v1"


def test_sdk2_github_token_path_has_no_byok_provider(monkeypatch):
    module = _load(monkeypatch, byok=False)
    _open(module)
    (client,) = FakeClient.instances
    assert client.kwargs["github"] == {"token": "test-only-not-a-token"}
    (options,) = client.created
    assert "inference" not in options and "github" not in options


def test_sdk_line_v1_keeps_the_flat_provider_config_shape(monkeypatch):
    module = _load(monkeypatch, v2=False)
    _open(module)
    (client,) = FakeClient.instances
    assert client.kwargs == {}
    (options,) = client.created
    assert options["provider"]["type"] == "azure" and options["provider"]["bearer_token"] == "entra-token"
    assert options["available_tools"] == ["mcp:*", "custom:*", "builtin:skill"]


def test_template_pins_microsoft_copilot_sdk_2_and_stages_the_native_runtime():
    deps = tomllib.loads((GHCP / "pyproject.toml").read_text())["project"]["dependencies"]
    assert "microsoft-copilot-sdk==2.0.1" in deps
    assert not any(dep.startswith("github-copilot-sdk") for dep in deps)
    lock = (GHCP / "requirements.lock").read_text()
    assert "\nmicrosoft-copilot-sdk==2.0.1 " in lock and "github-copilot-sdk==" not in lock
    dockerfile = (GHCP / "Dockerfile").read_text()
    assert "python -m copilot download-runtime --in-process" in dockerfile
    assert dockerfile.index("COPILOT_CLI_EXTRACT_DIR") < dockerfile.index("download-runtime")
    assert "COPILOT_SKIP_CLI_DOWNLOAD=1" in dockerfile
    v1 = (GHCP / "sdk-v1" / "requirements.lock").read_text()
    assert "\ngithub-copilot-sdk==1.0.1 " in v1 and "microsoft-copilot-sdk" not in v1
