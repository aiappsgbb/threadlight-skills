"""Validate post-registration probe data against frozen native code and endpoints."""
from __future__ import annotations

from pathlib import Path

from govern_bundle.policy_bundle import verify_bundle
from govern_control_plane.bootstrap import fresh
from govern_control_plane.bootstrap_assets import NativeProbeConstraints, digest
from govern_control_plane.models import SignedBundle, canonical, parse
from .dispatcher import NativePolicy
from .probe_runtime import ProbeConfiguration


def controllers_digest(controllers):
    return digest(canonical({key: {"client_id": value.client_id, "actions": value.actions}
                             for key, value in controllers.items()}))


def read(root, path):
    root = Path(root)
    if any(value.is_symlink() for value in (root, *root.parents, root / path)):
        raise ValueError("native_bootstrap_asset_symlink")
    with (root / path).open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("native_bootstrap_asset_size")
    return raw


async def validate_assets(root, *, native_bundle, binding, constraints, signer):
    constraints = parse(NativeProbeConstraints, canonical(constraints))
    config = parse(ProbeConfiguration, read(root, "config.json"))
    expected = {key: getattr(binding, key) for key in (
        "agent_id", "agent_version", "image_digest", "environment", "subscription", "resource_group")}
    workload = config.workloads.get(binding.principal)
    if (config.producer != "native" or config.credential_mode != "platform-noop"
            or config.downstream_client_id is not None or config.fixture_callers
            or config.service_client_id != binding.client_id or config.tenant_id != binding.tenant_id
            or config.key_id != binding.key_id or config.expected_deployment.model_dump(mode="json") != expected
            or set(config.workloads) != {binding.principal}
            or workload is None or workload.client_id != binding.client_id or workload.agent_id != binding.agent_id
            or any(getattr(config, key) != getattr(constraints, key) for key in (
                "audience", "cosmos_url", "cosmos_database", "gateway_url", "policy_id", "policy_version"))
            or set(config.allowed_endpoints) != set(constraints.allowed_endpoints)
            or controllers_digest(config.probe_controllers) != constraints.controller_digest
            or any(grant.subjects != [binding.principal] or grant.actions != ["governance_probe_noop"]
                   for grant in config.probe_controllers.values())
            or config.bundle_path != "/mnt/governance-probe/policy"
            or config.signed_envelope_path != "/mnt/governance-probe/envelope.json"):
        raise ValueError("native_bootstrap_configuration_mismatch")
    native = verify_bundle(Path(native_bundle), expected_digest=binding.native_policy_digest)
    probe = verify_bundle(Path(root) / "policy", expected_digest=config.policy_digest)
    native_files = {item["path"] for item in native.files}
    probe_files = {item["path"] for item in probe.files} - {"gateway-registry.json"}
    if native_files != probe_files or any(
            (native.root / path).read_bytes() != (probe.root / path).read_bytes() for path in native_files):
        raise ValueError("remote_probe_must_preserve_frozen_native_policy")
    signed = parse(SignedBundle, read(root, "envelope.json"))
    if signed.envelope.expires_at < binding.expires_at:
        raise ValueError("native_bootstrap_association_expires_first")
    policy = await NativePolicy.load(
        bundle_path=probe.root, signed=signed.model_dump(mode="json"), signer=signer,
        tenant=binding.tenant_id, key_id=binding.key_id, policy_id=constraints.policy_id,
        version=constraints.policy_version, expected_digest=config.policy_digest,
        allowed_endpoints=list(constraints.allowed_endpoints), gateway_url=constraints.gateway_url)
    registry = policy.registry
    if (registry.native_policy_digest != binding.native_policy_digest
            or registry.deployment.model_dump(mode="json") != expected
            or len(registry.actions) != 1 or registry.actions[0].name != "governance_probe_noop"
            or not registry.actions[0].probe_safe or registry.actions[0].workloads != [binding.principal]
            or registry.actions[0].credential_scope != constraints.fixture_scope):
        raise ValueError("native_bootstrap_registry_mismatch")
    fresh(binding)
    return config
