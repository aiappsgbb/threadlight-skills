"""Operator-only, pinned Foundry v1 create-once / independently observe primitive."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import re

from .bootstrap import ProjectEndpoint
from .models import Digest, Identifier, ObjectId, canonical, parse


ENVIRONMENT = frozenset({
    "TL_GOV_IMAGE_DIGEST", "GOV_CONTROL_PLANE_URL", "GOVERNED_TOOL_GATEWAY_URL",
    "AZURE_AI_MODEL_DEPLOYMENT_NAME", "TL_GOV_SPOOL_DIR",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
})


def validate(config):
    required = {
        "schema", "reference", "tenant_id", "subscription", "resource_group", "project_id",
        "project_endpoint", "agent_name", "image", "cpu", "memory", "protocol",
        "source_commit", "source_digest", "environment_variables",
    }
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("explicit_hosted_creation_inputs_required")
    if config["schema"] != "threadlight-hosted-create/v1":
        raise ValueError("unsupported_hosted_creation_schema")
    for field in ("tenant_id", "subscription"):
        parse(ObjectId, canonical(config[field]))
    for field in ("resource_group", "agent_name", "reference"):
        parse(Identifier, canonical(config[field]))
    parse(ProjectEndpoint, canonical(config["project_endpoint"]))
    parent = (f"/subscriptions/{config['subscription']}/resourceGroups/{config['resource_group']}"
              "/providers/Microsoft.CognitiveServices/accounts/")
    if (not config["project_id"].startswith(parent)
            or not re.fullmatch(r"[A-Za-z0-9_-]+/projects/[A-Za-z0-9_-]+",
                                config["project_id"][len(parent):])
            or config["project_id"].rsplit("/", 1)[-1] != config["project_endpoint"].rsplit("/", 1)[-1]):
        raise ValueError("explicit_project_parent_mismatch")
    if not re.fullmatch(r"[a-z0-9]+\.azurecr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}", config["image"]):
        raise ValueError("immutable_agent_image_required")
    if not re.fullmatch(r"[0-9a-f]{40}", config["source_commit"]):
        raise ValueError("source_commit_required")
    parse(Digest, canonical(config["source_digest"]))
    if (config["cpu"] not in ("0.5", "1", "2", "4")
            or config["memory"] not in ("1Gi", "2Gi", "4Gi", "8Gi")
            or config["protocol"] not in ("responses", "invocations")):
        raise ValueError("unsupported_hosted_size_or_protocol")
    environment = config["environment_variables"]
    if (not isinstance(environment, dict) or set(environment) - ENVIRONMENT
            or not {"TL_GOV_IMAGE_DIGEST", "GOV_CONTROL_PLANE_URL",
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME", "TL_GOV_SPOOL_DIR"} <= environment.keys()
            or any(not isinstance(value, str) or not 0 < len(value) <= 1024
                   for value in environment.values())
            or environment["TL_GOV_IMAGE_DIGEST"] != config["image"].split("@")[1]):
        raise ValueError("reserved_or_invalid_environment")
    from .client import ServiceTransport
    ServiceTransport.validate_configuration(environment["GOV_CONTROL_PLANE_URL"], "api://check/.default", 5)
    if not re.fullmatch(r"/home/[a-z0-9_/-]+", environment["TL_GOV_SPOOL_DIR"]):
        raise ValueError("host_owned_writable_spool_required")
    return config


def persist(path, body, *, exclusive=False):
    """Persist the create intent before transport; a lost ACK never triggers another create."""
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ValueError("protected_attempt_directory_required")
    destination = path if exclusive else path.with_name(path.name + ".next")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical(body))
            output.flush()
            os.fsync(output.fileno())
        if not exclusive:
            os.replace(destination, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not exclusive and destination.exists():
            destination.unlink()


def creation_state(config, path):
    digest = "sha256:" + hashlib.sha256(canonical(config)).hexdigest()
    path = Path(path)
    if path.is_symlink():
        raise ValueError("unsafe_attempt_state")
    if path.exists():
        with path.open("rb") as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError("invalid_attempt_state")
        from .models import strict_json
        state = strict_json(raw)
        if state.get("input_digest") != digest:
            raise ValueError("attempt_inputs_changed")
        if state.get("state") != "created" or not state.get("version"):
            raise ValueError("ambiguous_create_requires_operator_reconciliation")
        return state
    return {"schema": "threadlight-hosted-attempt/v1", "state": "creating",
            "reference": config["reference"], "input_digest": digest}


def create_once(client, config, attempt):
    config = validate(config)
    state = creation_state(config, attempt)
    if state["state"] == "created":
        return state
    if version("azure-ai-projects") != "2.3.0":
        raise ValueError("pinned_projects_sdk_required")
    from azure.ai.projects.models import ContainerConfiguration, HostedAgentDefinition, ProtocolVersionRecord
    definition = HostedAgentDefinition(
        cpu=config["cpu"], memory=config["memory"],
        container_configuration=ContainerConfiguration(image=config["image"]),
        protocol_versions=[ProtocolVersionRecord(
            protocol=config["protocol"], version="2.0.0")],
        environment_variables=config["environment_variables"],
    )
    persist(attempt, state, exclusive=True)
    created = client.agents.create_version(agent_name=config["agent_name"], definition=definition,
                                           retry_total=0)
    assigned = parse(Identifier, canonical(created.version))
    if created.name != config["agent_name"]:
        raise ValueError("created_agent_mismatch")
    state.update(state="created", version=assigned, version_id=created.id)
    persist(attempt, state)
    return state


def observe(client, config, attempt):
    config = validate(config)
    state = creation_state(config, attempt)
    if state["state"] != "created":
        raise ValueError("created_attempt_required")
    return _observe_version(client, config, state)[1]


def _observe_version(client, config, state):
    actual = client.agents.get_version(
        agent_name=config["agent_name"], agent_version=state["version"],
        retry_total=0, connection_timeout=5, read_timeout=30)
    if (actual.name != config["agent_name"] or actual.version != state["version"]
            or actual.id != state["version_id"] or actual.instance_identity is None
            or actual.definition.container_configuration.image != config["image"]
            or dict(actual.definition.environment_variables) != config["environment_variables"]):
        raise ValueError("observed_immutable_version_mismatch")
    return actual, {
        "agent_id": actual.name, "agent_version": actual.version,
        "image_digest": actual.definition.container_configuration.image.split("@")[1],
        "principal": parse(ObjectId, canonical(actual.instance_identity.principal_id)),
        "client_id": parse(ObjectId, canonical(actual.instance_identity.client_id)),
        "project_endpoint": config["project_endpoint"],
        "subscription": config["subscription"], "resource_group": config["resource_group"],
        "tenant_id": config["tenant_id"], "reference": config["reference"],
    }


def _endpoint_inputs(client, config, attempt, expected):
    config = validate(config)
    if (version("azure-ai-projects") != "2.3.0"
            or getattr(getattr(client, "_config", None), "endpoint", None) != config["project_endpoint"]
            or getattr(getattr(client, "_config", None), "api_version", None) != "v1"):
        raise ValueError("pinned_project_client_endpoint_required")
    state = creation_state(config, attempt)
    if (state.get("schema") != "threadlight-hosted-attempt/v1" or state["state"] != "created"
            or state.get("reference") != config["reference"]
            or not isinstance(state.get("version_id"), str) or not 0 < len(state["version_id"]) <= 512
            or not isinstance(state.get("version"), str) or not re.fullmatch(r"[1-9][0-9]*", state["version"])
            or not isinstance(expected, dict)):
        raise ValueError("created_attempt_and_protected_observation_required")
    return config, state


def _endpoint_snapshot(client, config, state, expected):
    actual, observed = _observe_version(client, config, state)
    if observed != expected:
        raise ValueError("independently_observed_binding_changed")
    definition = actual.definition
    if (actual.object != "agent.version" or actual.status != "active" or actual.get("draft", False) is not False
            or definition.kind != "hosted" or definition.cpu != config["cpu"] or definition.memory != config["memory"]
            or [item.as_dict() for item in definition.protocol_versions or []]
            != [{"protocol": config["protocol"], "version": "2.0.0"}]):
        raise ValueError("observed_hosted_definition_mismatch")
    details, etag = client.agents.get(
        agent_name=config["agent_name"], retry_total=0, connection_timeout=5, read_timeout=30,
        cls=lambda response, model, _: (model, response.http_response.headers.get("ETag")))
    latest = details.versions.latest if details.versions else None
    if (details.object != "agent" or details.name != config["agent_name"] or details.state != "enabled"
            or not isinstance(details.id, str) or not 0 < len(details.id) <= 512
            or latest is None or latest.name != actual.name or latest.version != actual.version
            or latest.id != actual.id or details.agent_endpoint is None):
        raise ValueError("observed_endpoint_owner_or_version_mismatch")
    for identity in (details.instance_identity, latest.instance_identity):
        if identity is not None and (
                identity.principal_id != observed["principal"] or identity.client_id != observed["client_id"]):
            raise ValueError("observed_endpoint_identity_mismatch")
    endpoint = details.agent_endpoint.as_dict()
    # Projects 2.3 retains this unmodeled server field; store review is not invocation readiness.
    publication = endpoint.pop("publish_approval_status", None)
    if publication is not None and (not isinstance(publication, str) or not 0 < len(publication) <= 128):
        raise ValueError("invalid_endpoint_publication_metadata")
    allowed = {"version_selector", "protocol_configuration", "authorization_schemes", "protocols"}
    if (set(endpoint) - allowed or endpoint.get("authorization_schemes") != [{"type": "Entra"}]
            or not isinstance(endpoint.get("version_selector"), dict)
            or set(endpoint["version_selector"]) != {"version_selection_rules"}):
        raise ValueError("explicit_entra_only_endpoint_required")
    rules = endpoint["version_selector"]["version_selection_rules"]
    if (not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict)
            or set(rules[0]) != {"type", "agent_version", "traffic_percentage"}
            or rules[0]["type"] != "FixedRatio" or type(rules[0]["traffic_percentage"]) is not int
            or rules[0]["traffic_percentage"] != 100
            or rules[0]["agent_version"] not in (actual.version, "@latest", "latest")):
        raise ValueError("unambiguous_owned_endpoint_route_required")
    protocols = endpoint.get("protocol_configuration")
    if protocols not in ({"responses": {}}, {config["protocol"]: {}}, {"responses": {}, config["protocol"]: {}}):
        raise ValueError("unexpected_endpoint_protocol_configuration")
    if "protocols" in endpoint and (
            not isinstance(endpoint["protocols"], list)
            or any(not isinstance(item, str) for item in endpoint["protocols"])
            or len(endpoint["protocols"]) != len(protocols) or set(endpoint["protocols"]) != set(protocols)):
        raise ValueError("observed_endpoint_protocols_mismatch")
    if "protocols" in endpoint:
        endpoint["protocols"] = sorted(endpoint["protocols"])
    if etag is not None and (not isinstance(etag, str) or not re.fullmatch(r'"[^"\\\r\n]{1,256}"', etag)):
        raise ValueError("unsupported_endpoint_etag")
    return details.id, endpoint, etag


def _endpoint_result(config, state, endpoint):
    rule = endpoint["version_selector"]["version_selection_rules"][0]
    if (rule["agent_version"] != state["version"]
            or config["protocol"] not in endpoint["protocol_configuration"]):
        raise ValueError("explicit_endpoint_configuration_required")
    return {"agent_id": config["agent_name"], "agent_version": state["version"],
            "protocol": config["protocol"], "authorization": "Entra"}


def observe_endpoint(client, config, attempt, expected):
    """Read-only pre-invocation gate; a declaration or PATCH acknowledgement is not readiness."""
    config, state = _endpoint_inputs(client, config, attempt, expected)
    _, endpoint, _ = _endpoint_snapshot(client, config, state, expected)
    return _endpoint_result(config, state, endpoint)


def configure_endpoint(client, config, attempt, expected):
    """Configure only the preserved create-once target; never create/enable/replace a version."""
    config, state = _endpoint_inputs(client, config, attempt, expected)
    before = _endpoint_snapshot(client, config, state, expected)
    current = _endpoint_snapshot(client, config, state, expected)
    if current != before:
        raise ValueError("observed_endpoint_changed_before_configuration")
    owner, endpoint, etag = current
    rule = endpoint["version_selector"]["version_selection_rules"][0]
    if rule["agent_version"] == state["version"] and config["protocol"] in endpoint["protocol_configuration"]:
        return _endpoint_result(config, state, endpoint)
    from azure.ai.projects.models import (
        AgentEndpointConfig, EntraAuthorizationScheme, FixedRatioVersionSelectionRule,
        InvocationsProtocolConfiguration, ProtocolConfiguration, ResponsesProtocolConfiguration, VersionSelector,
    )
    protocol_type = (InvocationsProtocolConfiguration if config["protocol"] == "invocations"
                     else ResponsesProtocolConfiguration)
    client.agents.update_details(
        agent_name=config["agent_name"],
        agent_endpoint=AgentEndpointConfig(
            version_selector=VersionSelector(version_selection_rules=[
                FixedRatioVersionSelectionRule(agent_version=state["version"], traffic_percentage=100)]),
            protocol_configuration=ProtocolConfiguration(**{config["protocol"]: protocol_type()}),
            authorization_schemes=[EntraAuthorizationScheme()]),
        headers={"If-Match": etag} if etag is not None else {},
        retry_total=0, connection_timeout=5, read_timeout=30)
    after_owner, after_endpoint, _ = _endpoint_snapshot(client, config, state, expected)
    if after_owner != owner:
        raise ValueError("observed_endpoint_changed_after_configuration")
    return _endpoint_result(config, state, after_endpoint)


def binding_from_observation(config, frozen, observed, *, policy_digest, policy_version=None,
                             lifetime_seconds=3600, assets=None):
    from .bootstrap import BootstrapBinding, BootstrapReference
    config = validate(config)
    reference = parse(BootstrapReference, canonical(frozen["remote_bootstrap"]))
    selected_version = reference.final_policy_version or frozen["policy_version"]
    if (reference.native_probe is None) != (assets is None):
        raise ValueError("native_bootstrap_asset_selection_mismatch")
    if reference.native_probe is not None and (
            config["protocol"] != "responses" or policy_digest != reference.native_policy_digest):
        raise ValueError("native_probe_requires_frozen_native_runtime")
    expected = {
        "tenant_id": config["tenant_id"], "reference": config["reference"],
        "agent_id": config["agent_name"], "project_endpoint": config["project_endpoint"],
        "subscription": config["subscription"], "resource_group": config["resource_group"],
        "image_digest": config["image"].split("@")[1],
    }
    if (set(observed) != set(expected) | {"agent_version", "principal", "client_id"}
            or any(observed[key] != value for key, value in expected.items())
            or any(getattr(reference, key) != config[key] for key in (
                "reference", "project_endpoint", "subscription", "resource_group"))
            or frozen["tenant_id"] != config["tenant_id"] or frozen["agent_id"] != config["agent_name"]
            or frozen["control_plane_url"] != config["environment_variables"]["GOV_CONTROL_PLANE_URL"]
            or policy_version is not None and policy_version != selected_version
            or type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= 86400):
        raise ValueError("bootstrap_observation_mismatch")
    now = datetime.now(timezone.utc)
    from .bootstrap_assets import descriptors
    return parse(BootstrapBinding, canonical({
        **observed, "schema": "threadlight-hosted-bootstrap/v1", "key_id": frozen["key_id"],
        "policy_id": frozen["policy_id"], "environment": frozen["environment"],
        "policy_version": selected_version,
        "native_policy_digest": reference.native_policy_digest, "policy_digest": policy_digest,
        "config_digest": "sha256:" + hashlib.sha256(canonical(frozen)).hexdigest(),
        "issued_at": now.isoformat(), "expires_at": (now + timedelta(seconds=lifetime_seconds)).isoformat(),
        **({"native_probe_assets": [item.model_dump(mode="json") for item in descriptors(assets)]}
           if assets is not None else {}),
    }))


async def publish_or_recover(service, binding, *, assets=None):
    """Recover only the same immutable signed record; never renew its lifetime."""
    from .bootstrap import BootstrapBinding, SignedBootstrap, blob_name, verify, policy_chain, fresh
    from .bootstrap_assets import validate_content
    from .storage import Missing
    binding = parse(BootstrapBinding, canonical(binding))
    fresh(binding)
    validate_content(binding, assets)
    try:
        raw = await service.store.blob_read(blob_name(binding.tenant_id, binding.reference))
    except Missing:
        return await service.publish_bootstrap(binding, assets=assets)
    existing = parse(SignedBootstrap, raw)
    fields = {"issued_at", "expires_at"}
    if (existing.binding.model_dump(mode="json", exclude=fields)
            != binding.model_dump(mode="json", exclude=fields)):
        raise ValueError("immutable_bootstrap_reference_conflict")
    await verify(existing, service.signer, tenant_id=service.settings.tenant_id, key_id=service.settings.key_id)
    await policy_chain(service, existing.binding)
    return existing


def bounded_lifetime(requested, expiries, *, now=None):
    now = now or datetime.now(timezone.utc)
    if type(requested) is not int or not 60 <= requested <= 86400 or not expiries:
        raise ValueError("bounded_bootstrap_lifetime_required")
    remaining = min(int((expiry - now).total_seconds()) for expiry in expiries) - 5
    lifetime = min(requested, remaining)
    if lifetime < 60:
        raise ValueError("fresh_policy_lifetime_required")
    return lifetime


def record_observation(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("protected_observation_path_required")
    if path.exists():
        from .models import strict_json
        with path.open("rb") as source:
            raw = source.read(16385)
        if len(raw) > 16384 or strict_json(raw) != value:
            raise ValueError("immutable_observation_changed")
        return
    persist(path, value, exclusive=True)
