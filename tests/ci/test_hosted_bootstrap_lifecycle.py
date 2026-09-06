"""Pinned SDK create-once bootstrap lifecycle, with an external HTTP fixture."""
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def lifecycle():
    path = ROOT / "skills/threadlight-govern/references/control-plane/hosted_lifecycle.py"
    assert path.exists(), "create-once hosted bootstrap lifecycle missing"
    spec = importlib.util.spec_from_file_location("govern_control_plane.hosted_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs():
    return {
        "schema": "threadlight-hosted-create/v1", "reference": "attempt-1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "subscription": "11111111-1111-1111-1111-111111111111", "resource_group": "fixture",
        "project_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/fixture/providers/Microsoft.CognitiveServices/accounts/test/projects/test",
        "project_endpoint": "https://test.services.ai.azure.com/api/projects/test",
        "agent_name": "fixture", "image": "fixture.azurecr.io/agent@sha256:" + "a" * 64,
        "cpu": "1", "memory": "2Gi", "protocol": "invocations",
        "source_commit": "a" * 40, "source_digest": "sha256:" + "b" * 64,
        "environment_variables": {"TL_GOV_IMAGE_DIGEST": "sha256:" + "a" * 64,
                                  "GOV_CONTROL_PLANE_URL": "https://control.example",
                                  "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test",
                                  "GOVERNED_TOOL_GATEWAY_URL": "https://gateway.example/mcp",
                                  "TL_GOV_SPOOL_DIR": "/home/app/audit"},
    }


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("lost", [False, True])
def test_real_sdk_version17_created_once_and_lost_reply_never_retries(tmp_path, lost):
    lib = lifecycle()
    from azure.ai.projects import AIProjectClient
    from azure.core.credentials import AccessToken
    from azure.core.pipeline.transport import RequestsTransport
    from requests import Response, Session
    from requests.adapters import BaseAdapter
    from urllib3.response import HTTPResponse

    config = inputs()
    calls = []
    record = None

    class Credential:
        def get_token(self, *scopes, **kwargs):
            return AccessToken("local-fixture-only", int(time.time()) + 3600)

    class Foundry(BaseAdapter):
        def send(self, request, **kwargs):
            nonlocal record
            calls.append(request.method)
            if request.method == "POST":
                assert calls == ["POST"]
                definition = json.loads(request.body)["definition"]
                assert definition["protocol_versions"] == [{"protocol": "invocations", "version": "2.0.0"}]
                assert not any(key.startswith("FOUNDRY_") for key in definition["environment_variables"])
                record = dict(id="fixture-id", object="agent.version", name="fixture", version="17",
                              created_at=int(time.time()), metadata={}, status="active",
                              definition=definition, instance_identity={
                                  "principal_id": "22222222-2222-2222-2222-222222222222",
                                  "client_id": "33333333-3333-3333-3333-333333333333"})
                if lost:
                    raise RuntimeError("ambiguous post-create connection loss")
            else:
                assert request.method == "GET" and "/versions/17?" in request.url
            response = Response()
            response.request = request
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(record).encode()
            response.raw = HTTPResponse(body=BytesIO(response._content), headers=response.headers,
                                        status=200, preload_content=False)
            return response

        def close(self):
            pass

    attempt = tmp_path / "attempt.json"
    with Session() as session:
        session.trust_env = False
        session.mount("https://", Foundry())
        with AIProjectClient(endpoint=config["project_endpoint"], credential=Credential(), api_version="v1",
                             retry_total=0, transport=RequestsTransport(
                                 session=session, session_owner=False)) as client:
            if lost:
                with pytest.raises(RuntimeError):
                    lib.create_once(client, config, attempt)
                with pytest.raises(ValueError, match="ambiguous"):
                    lib.create_once(client, config, attempt)
                assert calls == ["POST"]
            else:
                assert lib.create_once(client, config, attempt)["version"] == "17"
                assert lib.create_once(client, config, attempt)["version"] == "17"
                observed = lib.observe(client, config, attempt)
                assert observed["agent_version"] == "17"
                assert observed["principal"] == record["instance_identity"]["principal_id"]
                assert calls == ["POST", "GET"]


@pytest.mark.parametrize("key,value", [
    ("FOUNDRY_AGENT_VERSION", "1"), ("FOUNDRY_AGENT_NAME", "fixture"),
    ("FOUNDRY_PROJECT_ENDPOINT", "https://test.services.ai.azure.com/api/projects/test"),
    ("AZURE_CLIENT_SECRET", "not-a-real-secret"),
])
def test_reserved_or_credential_environment_rejected_before_create(tmp_path, key, value):
    lib = lifecycle()
    config = inputs()
    config["environment_variables"][key] = value
    with pytest.raises(ValueError):
        lib.create_once(None, config, tmp_path / "attempt.json")
    assert not (tmp_path / "attempt.json").exists()


def test_operator_binding_uses_observation_and_frozen_config_not_claim_flags():
    lib = lifecycle()
    assert callable(getattr(lib, "binding_from_observation", None)), "operator binding construction missing"
    config = inputs()
    frozen = {
        "tenant_id": config["tenant_id"], "key_id": "https://test.vault.azure.net/keys/bundle/" + "a" * 32,
        "agent_id": config["agent_name"], "policy_id": "safe", "policy_version": "1",
        "environment": "preproduction",
        "control_plane_url": config["environment_variables"]["GOV_CONTROL_PLANE_URL"],
        "remote_bootstrap": {key: config[key] for key in (
            "reference", "project_endpoint", "subscription", "resource_group")},
    }
    frozen["remote_bootstrap"]["native_policy_digest"] = "sha256:" + "b" * 64
    observed = dict(
        tenant_id=config["tenant_id"], reference=config["reference"],
        agent_id=config["agent_name"], agent_version="17", project_endpoint=config["project_endpoint"],
        subscription=config["subscription"], resource_group=config["resource_group"],
        image_digest=config["image"].split("@")[1],
        principal="22222222-2222-2222-2222-222222222222",
        client_id="33333333-3333-3333-3333-333333333333")
    binding = lib.binding_from_observation(config, frozen, observed, policy_digest="sha256:" + "c" * 64)
    assert binding.agent_version == "17"
    assert binding.principal == observed["principal"]
    assert binding.native_policy_digest != binding.policy_digest
    with pytest.raises(ValueError):
        lib.binding_from_observation(config, frozen, dict(observed, image_digest="sha256:" + "d" * 64),
                                     policy_digest="sha256:" + "c" * 64)


def test_operator_cli_exposes_create_observe_publish_wait_without_azd():
    import subprocess
    import sys
    path = ROOT / "scripts/ci/hosted_bootstrap.py"
    assert path.exists(), "supported operator lifecycle CLI missing"
    result = subprocess.run([sys.executable, str(path), "--help"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ("create", "observe", "publish", "wait"))


def test_public_docs_distinguish_signed_bootstrap_from_live_acceptance():
    text = (ROOT / "docs/production-readiness.md").read_text()
    assert "threadlight-hosted-bootstrap/v1" in text
    assert "hosted_bootstrap.py" in text
    assert "create-once" in text
    assert "hosted-native-probe remains unsupported" in text
