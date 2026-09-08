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
    assert "--native-probe-assets" in result.stdout
    assert "--observation-output" in result.stdout and "--expected-observation" in result.stdout


def test_public_docs_distinguish_signed_bootstrap_from_live_acceptance():
    text = (ROOT / "docs/production-readiness.md").read_text()
    assert "threadlight-hosted-bootstrap/v1" in text
    assert "hosted_bootstrap.py" in text
    assert "create-once" in text
    assert "hosted-native-probe remains unverified" in text


def test_operator_lifetime_cannot_outlive_any_policy_dependency():
    from datetime import datetime, timedelta, timezone
    lib = lifecycle()
    assert callable(getattr(lib, "bounded_lifetime", None)), "bounded policy lease selection missing"
    now = datetime.now(timezone.utc)
    assert lib.bounded_lifetime(3600, [now + timedelta(minutes=10), now + timedelta(minutes=5)], now=now) == 295
    assert lib.bounded_lifetime(120, [now + timedelta(minutes=10)], now=now) == 120
    with pytest.raises(ValueError):
        lib.bounded_lifetime(3600, [now + timedelta(seconds=30)], now=now)


def test_resumed_observation_file_is_idempotent_but_never_overwritten(tmp_path):
    lib = lifecycle()
    assert callable(getattr(lib, "record_observation", None)), "safe repeated observation handoff missing"
    path = tmp_path / "observation.json"
    value = {"agent_version": "17", "principal": "22222222-2222-2222-2222-222222222222"}
    lib.record_observation(path, value)
    before = path.read_bytes()
    lib.record_observation(path, value)
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        lib.record_observation(path, {**value, "agent_version": "18"})
    assert path.read_bytes() == before


def endpoint_fixture(tmp_path, *, protocol="invocations", etag=True, sdk_endpoint=None, sdk_api_version="v1"):
    """Exercise the published SDK and its real transport against an HTTP fixture."""
    from contextlib import contextmanager
    from copy import deepcopy
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import ContainerConfiguration, HostedAgentDefinition, ProtocolVersionRecord
    from azure.core.credentials import AccessToken
    from azure.core.pipeline.transport import RequestsTransport
    from requests import Response, Session
    from requests.adapters import BaseAdapter
    from urllib3.response import HTTPResponse

    @contextmanager
    def opened():
        lib, config = lifecycle(), inputs()
        config["protocol"] = protocol
        attempt = tmp_path / "attempt.json"
        state = lib.creation_state(config, attempt)
        state.update(state="created", version="17", version_id="fixture-version-17")
        lib.persist(attempt, state, exclusive=True)
        identity = {"principal_id": "22222222-2222-2222-2222-222222222222",
                    "client_id": "33333333-3333-3333-3333-333333333333"}
        definition = HostedAgentDefinition(
            cpu=config["cpu"], memory=config["memory"],
            container_configuration=ContainerConfiguration(image=config["image"]),
            environment_variables=config["environment_variables"],
            protocol_versions=[ProtocolVersionRecord(protocol=protocol, version="2.0.0")],
        ).as_dict()
        version_record = dict(id=state["version_id"], object="agent.version", name=config["agent_name"],
                              version="17", created_at=int(time.time()), metadata={}, status="active",
                              definition=deepcopy(definition), instance_identity=identity)
        details = dict(id="fixture-agent-id", object="agent", name=config["agent_name"], state="enabled",
                       versions={"latest": deepcopy(version_record)}, instance_identity=deepcopy(identity),
                       agent_endpoint={
                           "version_selector": {"version_selection_rules": [
                               {"type": "FixedRatio", "agent_version": "@latest", "traffic_percentage": 100}]},
                           "protocol_configuration": {"responses": {}},
                           "protocols": ["responses"], "authorization_schemes": [{"type": "Entra"}],
                           "publish_approval_status": "not_published"})
        expected = dict(tenant_id=config["tenant_id"], reference=config["reference"],
                        agent_id=config["agent_name"], agent_version="17",
                        project_endpoint=config["project_endpoint"], subscription=config["subscription"],
                        resource_group=config["resource_group"], image_digest=config["image"].split("@")[1],
                        principal=identity["principal_id"], client_id=identity["client_id"])
        wire = dict(calls=[], version=version_record, details=details, before_send=None, lost_ack=False,
                    patch_status=200)

        class Credential:
            def get_token(self, *scopes, **kwargs):
                return AccessToken("local-endpoint-fixture-only", int(time.time()) + 3600)

        def merge(target, patch):
            for key, value in patch.items():
                if value is None:
                    target.pop(key, None)
                elif isinstance(value, dict):
                    merge(target.setdefault(key, {}), value)
                else:
                    target[key] = value

        class Foundry(BaseAdapter):
            def send(self, request, **kwargs):
                call = {"method": request.method, "url": request.url, "headers": dict(request.headers),
                        "timeout": kwargs.get("timeout"),
                        "body": json.loads(request.body) if request.body else None}
                wire["calls"].append(call)
                if wire["before_send"]:
                    wire["before_send"](call, wire)
                assert request.url.startswith(config["project_endpoint"] + "/agents/fixture")
                assert "api-version=v1" in request.url
                status = wire["patch_status"] if request.method == "PATCH" else 200
                if status != 200:
                    body = {"error": {"code": "fixture-denied", "message": "local-fixture-only"}}
                elif request.method == "PATCH":
                    assert "/versions/" not in request.url
                    assert set(call["body"]) == {"agent_endpoint"}
                    if etag:
                        assert request.headers["If-Match"] == '"fixture-etag"'
                    merge(wire["details"], call["body"])
                    wire["details"]["agent_endpoint"]["protocols"] = sorted(
                        wire["details"]["agent_endpoint"]["protocol_configuration"])
                    if wire["lost_ack"]:
                        raise RuntimeError("fixture-lost-endpoint-ack")
                    body = wire["details"]
                else:
                    assert request.method == "GET", "no new agent/version or other mutation is authorized"
                    body = wire["version"] if "/versions/17?" in request.url else wire["details"]
                response = Response()
                response.request, response.status_code = request, status
                response.headers["Content-Type"] = "application/json"
                if etag:
                    response.headers["ETag"] = '"fixture-etag"'
                response._content = json.dumps(body).encode()
                response.raw = HTTPResponse(body=BytesIO(response._content), headers=response.headers,
                                            status=status, preload_content=False)
                return response

            def close(self):
                pass

        with Session() as session:
            session.trust_env = False
            session.mount("https://", Foundry())
            with AIProjectClient(endpoint=sdk_endpoint or config["project_endpoint"], credential=Credential(),
                                 api_version=sdk_api_version,
                                 retry_total=0, transport=RequestsTransport(
                                     session=session, session_owner=False)) as client:
                yield lib, client, config, attempt, expected, wire
    return opened()


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("protocol", ["responses", "invocations"])
@pytest.mark.parametrize("etag", [False, True])
def test_real_sdk_configures_only_created_endpoint_and_reobserves_without_new_version(tmp_path, protocol, etag):
    lib = lifecycle()
    assert callable(getattr(lib, "configure_endpoint", None)), "explicit endpoint configuration missing"
    with endpoint_fixture(tmp_path, protocol=protocol, etag=etag) as (lib, client, config, attempt, expected, wire):
        original = attempt.read_bytes()
        result = lib.configure_endpoint(client, config, attempt, expected)
        assert result == {"agent_id": "fixture", "agent_version": "17", "protocol": protocol,
                          "authorization": "Entra"}
        patches = [call for call in wire["calls"] if call["method"] == "PATCH"]
        assert len(patches) == 1
        patch = patches[0]
        protocols = {protocol: {}}
        assert patch["body"] == {"agent_endpoint": {
            "version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": "17", "traffic_percentage": 100}]},
            "protocol_configuration": protocols,
            "authorization_schemes": [{"type": "Entra"}]}}
        assert patch["headers"]["Content-Type"] == "application/merge-patch+json"
        assert wire["details"]["agent_endpoint"]["protocol_configuration"] == {"responses": {}, protocol: {}}
        assert wire["calls"][-1]["method"] == "GET"
        assert all(call["timeout"] == (5, 30) for call in wire["calls"])
        assert lib.configure_endpoint(client, config, attempt, expected) == result
        assert len([call for call in wire["calls"] if call["method"] == "PATCH"]) == 1
        assert attempt.read_bytes() == original


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("protocol_order", [["invocations", "responses"], ["responses", "invocations"]])
def test_documented_combined_endpoint_is_already_configured_without_protocol_removal(tmp_path, protocol_order):
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        endpoint = wire["details"]["agent_endpoint"]
        endpoint["version_selector"]["version_selection_rules"][0]["agent_version"] = "17"
        endpoint["protocol_configuration"] = {"responses": {}, "invocations": {}}
        endpoint["protocols"] = protocol_order
        assert lib.observe_endpoint(client, config, attempt, expected)["protocol"] == "invocations"
        assert lib.configure_endpoint(client, config, attempt, expected)["protocol"] == "invocations"
        assert all(call["method"] == "GET" for call in wire["calls"])
        assert endpoint["protocol_configuration"] == {"responses": {}, "invocations": {}}


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
def test_fixed_numeric_route_does_not_infer_declared_protocol_is_exposed(tmp_path):
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        endpoint = wire["details"]["agent_endpoint"]
        endpoint["version_selector"]["version_selection_rules"][0]["agent_version"] = "17"
        with pytest.raises(ValueError, match="explicit_endpoint_configuration_required"):
            lib.observe_endpoint(client, config, attempt, expected)
        assert all(call["method"] == "GET" for call in wire["calls"])
        assert lib.configure_endpoint(client, config, attempt, expected)["protocol"] == "invocations"
        patches = [call for call in wire["calls"] if call["method"] == "PATCH"]
        assert len(patches) == 1
        assert patches[0]["body"]["agent_endpoint"]["protocol_configuration"] == {"invocations": {}}
        assert endpoint["protocol_configuration"] == {"responses": {}, "invocations": {}}


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
def test_sanitized_actual_native_endpoint_ignores_readonly_publication_metadata(tmp_path):
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        # Actual native GET shape; only its deployment-specific version is sanitized.
        endpoint = {
            "version_selector": {"version_selection_rules": [
                {"agent_version": "17", "traffic_percentage": 100, "type": "FixedRatio"}]},
            "protocols": ["invocations", "responses"],
            "protocol_configuration": {"responses": {}, "invocations": {}},
            "authorization_schemes": [{"type": "Entra"}],
            "publish_approval_status": "not_published",
        }
        wire["details"]["agent_endpoint"] = endpoint
        native = client.agents.get(agent_name=config["agent_name"])
        assert native.agent_endpoint.as_dict() == endpoint
        result = lib.observe_endpoint(client, config, attempt, expected)
        assert result["protocol"] == "invocations" and "publish_approval_status" not in result
        assert lib.configure_endpoint(client, config, attempt, expected) == result
        assert all(call["method"] == "GET" for call in wire["calls"])
        endpoint["version_selector"]["version_selection_rules"][0]["agent_version"] = "@latest"
        def publication_changes_independently(call, wire):
            if call["method"] == "GET" and "/versions/" not in call["url"]:
                endpoint["publish_approval_status"] = (
                    "pending" if endpoint["publish_approval_status"] == "not_published" else "not_published")
        wire["before_send"] = publication_changes_independently
        assert lib.configure_endpoint(client, config, attempt, expected) == result
        patches = [call for call in wire["calls"] if call["method"] == "PATCH"]
        assert len(patches) == 1
        assert "publish_approval_status" not in patches[0]["body"]["agent_endpoint"]


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("status", [None, "not_published", "pending", "approved", "rejected", "no_approval_needed"])
def test_publication_status_never_substitutes_for_declared_protocol_exposure(tmp_path, status):
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        endpoint = wire["details"]["agent_endpoint"]
        endpoint["version_selector"]["version_selection_rules"][0]["agent_version"] = "17"
        endpoint["publish_approval_status"] = status
        with pytest.raises(ValueError, match="explicit_endpoint_configuration_required"):
            lib.observe_endpoint(client, config, attempt, expected)
        assert all(call["method"] == "GET" for call in wire["calls"])
        endpoint["protocol_configuration"] = {"responses": {}, "invocations": {}}
        endpoint["protocols"] = ["invocations", "responses"]
        assert lib.observe_endpoint(client, config, attempt, expected)["protocol"] == "invocations"
        endpoint.pop("publish_approval_status")
        assert lib.observe_endpoint(client, config, attempt, expected)["protocol"] == "invocations"


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("extension", [
    {"authentication_disabled": True},
    {"publish_requires_approval": False},
    {"future_endpoint_options": {"protocols": ["activity"]}},
    {"publish_approval_status": {"authorization_schemes": []}},
    {"publish_approval_status": False},
])
def test_publication_allowance_does_not_allow_unknown_or_malformed_endpoint_extensions(tmp_path, extension):
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        wire["details"]["agent_endpoint"].update(extension)
        with pytest.raises(ValueError):
            lib.configure_endpoint(client, config, attempt, expected)
        assert all(call["method"] == "GET" for call in wire["calls"])


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("bad", [
    "no-attempt", "creating-attempt", "source-changed", "missing-expected", "expected-identity",
    "foreign-client", "foreign-agent", "foreign-latest", "foreign-version-id", "foreign-identity",
    "image", "environment", "cpu", "memory", "definition-protocol", "inactive", "draft", "draft-zero",
    "wrong-route", "split-route", "boolean-traffic", "route-kind", "missing-auth", "extra-auth",
    "missing-protocol", "extra-protocol", "protocol-alias-mismatch", "endpoint-drift",
])
def test_endpoint_configuration_rejects_ambiguous_or_foreign_state_before_mutation(tmp_path, bad):
    lib = lifecycle()
    assert callable(getattr(lib, "configure_endpoint", None)), "explicit endpoint configuration missing"
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        details, actual = wire["details"], wire["version"]
        endpoint = details["agent_endpoint"]
        rule = endpoint["version_selector"]["version_selection_rules"][0]
        if bad == "no-attempt":
            attempt.unlink()
        elif bad == "creating-attempt":
            state = json.loads(attempt.read_bytes())
            state["state"] = "creating"
            lib.persist(attempt, state)
        elif bad == "source-changed":
            config["source_commit"] = "f" * 40
        elif bad == "missing-expected":
            expected = None
        elif bad == "expected-identity":
            expected["principal"] = config["tenant_id"]
        elif bad == "foreign-client":
            config["project_endpoint"] = "https://other.services.ai.azure.com/api/projects/test"
        elif bad == "foreign-agent":
            details["name"] = "another-agent"
        elif bad == "foreign-latest":
            details["versions"]["latest"]["version"] = "18"
        elif bad == "foreign-version-id":
            actual["id"] = "other-owner-version"
        elif bad == "foreign-identity":
            details["instance_identity"]["principal_id"] = config["tenant_id"]
        elif bad == "image":
            actual["definition"]["container_configuration"]["image"] = "other.azurecr.io/agent@sha256:" + "f" * 64
        elif bad == "environment":
            actual["definition"]["environment_variables"]["GOV_CONTROL_PLANE_URL"] = "https://other.example"
        elif bad in ("cpu", "memory"):
            actual["definition"][bad] = "4" if bad == "cpu" else "4Gi"
        elif bad == "definition-protocol":
            actual["definition"]["protocol_versions"][0]["protocol"] = "responses"
        elif bad == "inactive":
            actual["status"] = "creating"
        elif bad == "draft":
            actual["draft"] = True
        elif bad == "draft-zero":
            actual["draft"] = 0
        elif bad == "wrong-route":
            rule["agent_version"] = "18"
        elif bad == "split-route":
            endpoint["version_selector"]["version_selection_rules"].append(dict(rule))
        elif bad == "boolean-traffic":
            rule["traffic_percentage"] = True
        elif bad == "route-kind":
            rule["type"] = "Unknown"
        elif bad == "missing-auth":
            endpoint.pop("authorization_schemes")
        elif bad == "extra-auth":
            endpoint["authorization_schemes"].append({"type": "BotServiceTenant"})
        elif bad == "missing-protocol":
            endpoint["protocol_configuration"] = {}
        elif bad == "extra-protocol":
            endpoint["protocol_configuration"]["activity"] = {}
        elif bad == "protocol-alias-mismatch":
            endpoint["protocols"] = ["invocations"]
        elif bad == "endpoint-drift":
            def change_on_second_details(call, wire):
                gets = [c for c in wire["calls"] if c["method"] == "GET" and "/versions/" not in c["url"]]
                if len(gets) == 2:
                    wire["details"]["id"] = "other-agent-object"
            wire["before_send"] = change_on_second_details
        with pytest.raises(ValueError):
            lib.configure_endpoint(client, config, attempt, expected)
        assert all(call["method"] == "GET" for call in wire["calls"])


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
def test_endpoint_lost_ack_and_failed_readback_are_not_success_or_retried_mutations(tmp_path):
    lib = lifecycle()
    assert callable(getattr(lib, "configure_endpoint", None)), "explicit endpoint configuration missing"
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        wire["lost_ack"] = True
        with pytest.raises(RuntimeError, match="lost-endpoint-ack"):
            lib.configure_endpoint(client, config, attempt, expected)
        assert len([c for c in wire["calls"] if c["method"] == "PATCH"]) == 1
        wire["lost_ack"] = False
        assert lib.configure_endpoint(client, config, attempt, expected)["agent_version"] == "17"
        assert len([c for c in wire["calls"] if c["method"] == "PATCH"]) == 1
        wire["details"]["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["agent_version"] = "@latest"
        def revert_after_patch(call, wire):
            if call["method"] == "GET" and len([c for c in wire["calls"] if c["method"] == "PATCH"]) == 2:
                wire["details"]["agent_endpoint"]["protocol_configuration"] = {"responses": {}}
                wire["details"]["agent_endpoint"]["protocols"] = ["responses"]
        wire["before_send"] = revert_after_patch
        with pytest.raises(ValueError):
            lib.configure_endpoint(client, config, attempt, expected)
        assert len([c for c in wire["calls"] if c["method"] == "PATCH"]) == 2


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
def test_wait_endpoint_check_is_read_only_and_requires_pinned_declared_protocol(tmp_path):
    lib = lifecycle()
    assert callable(getattr(lib, "observe_endpoint", None)), "read-only endpoint gate missing"
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        with pytest.raises(ValueError):
            lib.observe_endpoint(client, config, attempt, expected)
        assert all(call["method"] == "GET" for call in wire["calls"])
        configured = lib.configure_endpoint(client, config, attempt, expected)
        wire["calls"].clear()
        assert lib.observe_endpoint(client, config, attempt, expected) == configured
        assert all(call["method"] == "GET" for call in wire["calls"])


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("status", [403, 412, 429, 503])
def test_native_endpoint_http_failures_never_retry_or_claim_configuration(tmp_path, status):
    from azure.core.exceptions import HttpResponseError
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        wire["patch_status"] = status
        before = json.dumps(wire["details"], sort_keys=True)
        with pytest.raises(HttpResponseError):
            lib.configure_endpoint(client, config, attempt, expected)
        assert len([call for call in wire["calls"] if call["method"] == "PATCH"]) == 1
        assert json.dumps(wire["details"], sort_keys=True) == before


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
@pytest.mark.parametrize("options", [
    {"sdk_endpoint": "https://other.services.ai.azure.com/api/projects/test"},
    {"sdk_api_version": "unsupported-preview"},
])
def test_endpoint_rejects_wrong_native_client_target_or_api_before_transport(tmp_path, options):
    with endpoint_fixture(tmp_path, **options) as (lib, client, config, attempt, expected, wire):
        with pytest.raises(ValueError, match="pinned_project_client_endpoint_required"):
            lib.configure_endpoint(client, config, attempt, expected)
        assert wire["calls"] == []


@pytest.mark.skipif(os.environ.get("THREADLIGHT_READINESS_SDK") != "1", reason="pinned SDK job")
def test_cli_wait_rejects_default_endpoint_before_any_host_request(tmp_path, monkeypatch):
    from test_hosted_bootstrap_credentials import cli
    import sys
    import azure.ai.projects
    from contextlib import nullcontext
    app = cli()
    with endpoint_fixture(tmp_path) as (lib, client, config, attempt, expected, wire):
        creation = tmp_path / "creation.json"
        creation.write_text(json.dumps(config))
        expected_file = tmp_path / "observed.json"
        expected_file.write_text(json.dumps(expected))
        monkeypatch.setitem(sys.modules, "govern_control_plane.hosted_lifecycle", lib)
        monkeypatch.setattr(azure.ai.projects, "AIProjectClient", lambda **_: nullcontext(client))
        monkeypatch.setattr(app, "make_credential", lambda *_: nullcontext(None))
        monkeypatch.setattr(app, "observe_parent", lambda *_: config["project_id"])
        monkeypatch.setattr(app, "wait_ready", lambda *_: pytest.fail("unconfigured endpoint reached host request"))
        common = ["--creation", str(creation), "--attempt", str(attempt),
                  "--expected-observation", str(expected_file)]
        with pytest.raises(ValueError, match="explicit_endpoint_configuration_required"):
            app.main(["wait", *common, "--binding-output", "never-read-binding.json",
                      "--frozen-config", "never-read-frozen.json"])
        assert all(call["method"] == "GET" for call in wire["calls"])
        app.main(["configure-endpoint", *common])
        assert len([call for call in wire["calls"] if call["method"] == "PATCH"]) == 1


def test_endpoint_phase_is_explicit_protected_and_precedes_wait_in_resume_workflow():
    import subprocess
    import sys
    cli = ROOT / "scripts/ci/hosted_bootstrap.py"
    help_result = subprocess.run([sys.executable, str(cli), "--help"], text=True, capture_output=True)
    assert "configure-endpoint" in help_result.stdout, "explicit operator endpoint phase missing"
    invalid = subprocess.run([sys.executable, str(cli), "configure-endpoint", "--creation", "absent.json",
                              "--attempt", "absent.json"], text=True, capture_output=True)
    assert invalid.returncode == 2 and "--expected-observation" in invalid.stderr
    source = cli.read_text()
    assert source.index("observe_endpoint(client,") < source.index("asyncio.run(wait_ready(")
    workflow = (ROOT / "scripts/ci/runtime_readiness_remote.py").read_text()
    assert workflow.index('cli, "configure-endpoint"') < workflow.index('cli, "wait"')
    assert '"--expected-observation", observed_path' in workflow.split('cli, "configure-endpoint"', 1)[1].split("])", 1)[0]
    for relative in ("docs/production-readiness.md", "skills/threadlight-deploy/references/governance/README.md"):
        text = (ROOT / relative).read_text()
        assert "configure-endpoint" in text and "update_details" in text
        assert "Entra-only" in text and "protocol_configuration" in text
        assert "preserves the Responses default" in text
        assert "publish_approval_status" in text and "not a readiness" in text


def test_private_noop_proof_is_dated_scoped_and_discloses_operator_steps():
    text = (ROOT / "docs/production-readiness.md").read_text()
    heading = "### Private GHCP noop evidence snapshot (2026-09-08)"
    assert heading in text
    section = text.split(heading, 1)[1].split("\n### ", 1)[0]
    for term in (
        "62cb516", "governance_probe_noop", "allow: 1", "deny: 0",
        "central audit", "not whole-agent", "returns_apply_decision",
        "operator", "numeric route", "agent_session_id",
        "collect_project(http=", "default collector CLI", "timeout",
        "fresh after-deployment", "retained privately",
    ):
        assert term in section
    assert "Private GHCP noop evidence snapshot" in (
        ROOT / "skills/threadlight-safe-check/references/governance-probe.md"
    ).read_text()
