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
    actual = client.agents.get_version(agent_name=config["agent_name"], agent_version=state["version"])
    if (actual.name != config["agent_name"] or actual.version != state["version"]
            or actual.id != state["version_id"] or actual.instance_identity is None
            or actual.definition.container_configuration.image != config["image"]
            or dict(actual.definition.environment_variables) != config["environment_variables"]):
        raise ValueError("observed_immutable_version_mismatch")
    return {
        "agent_id": actual.name, "agent_version": actual.version,
        "image_digest": actual.definition.container_configuration.image.split("@")[1],
        "principal": parse(ObjectId, canonical(actual.instance_identity.principal_id)),
        "client_id": parse(ObjectId, canonical(actual.instance_identity.client_id)),
        "project_endpoint": config["project_endpoint"],
        "subscription": config["subscription"], "resource_group": config["resource_group"],
        "tenant_id": config["tenant_id"], "reference": config["reference"],
    }


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
