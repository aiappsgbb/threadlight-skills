"""MAF 1.14 / Responses: native hooks own the actual hosted agent."""
from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from agent_framework import SkillsProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from starlette.responses import JSONResponse

from govern_bundle.policy_bundle import verify_bundle
from govern_control_plane.client import ApprovalClient, PolicySnapshot, ServiceTransport
from govern_control_plane.models import SignedBundle, envelope_digest, parse
from govern_control_plane.storage import KeyVaultSigner
from skills._shared.governance import validate_governance_contract
from audit_delivery import AuditDelivery
from runtime import (
    AcsGovernanceProvider, ApprovalGrant, ApprovalIntent, DurableSpool, VerifiedPolicy,
    create_governed_agent,
)

BASE = Path(__file__).resolve().parent


class UnavailablePolicy:
    def verify(self, bundle):
        raise ValueError("policy_unavailable")


async def build_provider(config, *, signer, credential):
    """Authenticate the immutable envelope with the trusted, versioned Key Vault key."""
    signature = UnavailablePolicy()
    try:
        signed = parse(SignedBundle, (BASE / "policy-envelope.json").read_bytes())
        envelope = signed.envelope
        await signer.health()
        if (envelope.tenant_id != config["tenant_id"] or envelope.key_id != config["key_id"]
                or envelope.policy_id != config["policy_id"] or envelope.version != config["policy_version"]
                or envelope.content_digest != config["policy_digest"]
                or envelope.expires_at <= datetime.now(timezone.utc)
                or not await signer.verify(envelope_digest(envelope),
                    base64.b64decode(signed.signature, validate=True))):
            raise ValueError("policy_unavailable")
        signature = PolicySnapshot(VerifiedPolicy(envelope.content_digest, envelope.expires_at))
    except Exception:
        # Keep unbound tools usable, but selected tools deny and readiness is 503.
        pass
    approval = ApprovalClient(
        base_url=config["control_plane_url"], scope=config["control_plane_scope"],
        credential=credential, intent_type=ApprovalIntent, grant_type=ApprovalGrant)
    import governance_application as application
    if not callable(application.safe_evidence):
        raise ValueError("host_owned_safe_evidence_required")

    def safe_evidence(identity):
        facts = application.safe_evidence(identity)
        if not isinstance(facts, dict) or not facts:
            raise ValueError("safe_evidence_unavailable")
        return facts

    from runtime.governance_provider import AUDIT
    required_audit = any(
        set(binding.get("requires", [])) & AUDIT for binding in (
            config["contract"]["tools"] + config["contract"]["governance"]["lifecycle_bindings"]))
    required_audit = required_audit or "probe_observability" in config
    if config.get("audit_delivery") != "remote-ack":
        raise ValueError("hosted_audit_requires_remote_ack")
    audit = AuditDelivery(
        config["spool_dir"], base_url=config["control_plane_url"],
        scope=config["control_plane_scope"], required=required_audit)
    provider = AcsGovernanceProvider(
        contract=config["contract"], bundle_path=BASE / "policy",
        expected_digest=config["policy_digest"], bundle_verifier=verify_bundle,
        contract_validator=validate_governance_contract, signature_verifier=signature,
        safe_provider=safe_evidence, approval_resolver=approval,
        principal=config["principal"], tenant=config["tenant_id"],
        allowed_approval_roles=config["approver_roles"],
        audit=audit, agent_version=config["agent_version"],
        image_digest=config["image_digest"], environment=config.get("environment", "production"),
    )
    provider.deployment_agent_id = config["agent_id"]
    return provider


def readiness(provider):
    provider._refresh()
    health = provider.health()
    ready = bool(health["bindings"]) and all(b["healthy"] for b in health["bindings"].values())
    return JSONResponse(health, status_code=200 if ready else 503)


async def dependency_readiness(provider):
    from runtime.governance_provider import APPROVAL, AUDIT
    response = readiness(provider)
    body = json.loads(response.body)
    for key, binding in provider._bindings.items():
        if set(binding["requires"]) & AUDIT:
            if not await provider.audit.health():
                body["bindings"][key].update(healthy=False, reason="threadlight:audit_unavailable")
            elif binding["ready"] and binding["last_failure"] == "threadlight:audit_unavailable":
                # An authenticated dependency check plus completed replay clears
                # only the audit outage, never a policy/native enforcement failure.
                body["bindings"][key].update(healthy=True, reason=None)
        if set(binding["requires"]) & APPROVAL:
            if not await provider.approval_resolver.health(approval_context={
                "agent_id": provider.deployment_agent_id, "principal": provider.principal,
                "tenant": provider.tenant, "allowed_roles": list(provider.allowed_approval_roles),
            }):
                body["bindings"][key].update(healthy=False, reason="threadlight:approval_unavailable")
    return JSONResponse(body, status_code=200 if (
        body["bindings"] and all(b["healthy"] for b in body["bindings"].values())) else 503)


async def resolve_identity(config, credential, *, http=None):
    """Confirm a credential-token subject with Task8 before using it as an approval principal."""
    from govern_control_plane.models import ObjectId, canonical, strict_json
    token = await credential.get_token(config["control_plane_scope"])
    payload = token.token.split(".")[1]
    candidate = strict_json(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    principal = parse(ObjectId, canonical(candidate["oid"]))
    if candidate["tid"] != config["tenant_id"]:
        raise ValueError("workload_tenant_mismatch")
    # The decoded claims are NOT authentication. The configured TLS service
    # verifies the JWT and exact context before this subject becomes trusted.
    async with ServiceTransport(
            base_url=config["control_plane_url"], scope=config["control_plane_scope"],
            credential=credential, http=http) as service:
        if not await service.health(approval_context={
                "principal": principal, "agent_id": config["agent_id"], "tenant": config["tenant_id"],
                "allowed_roles": config["approver_roles"]}):
            raise ValueError("workload_identity_unavailable")
    return principal


def build_host(provider, *, client, **host_options):
    import governance_application as application
    skills = BASE / "skills"
    # Shared validator Python modules are not agent skills; retain progressive disclosure.
    contexts = [SkillsProvider.from_paths(skills)] if any(skills.glob("*/SKILL.md")) else []
    tools = application.tools
    probes = getattr(provider, "probes", None)
    if probes is not None:
        if any(getattr(tool, "name", getattr(tool, "__name__", None)) == probes.action.name for tool in tools):
            raise ValueError("probe_must_not_replace_application_tool")
        tools = [*tools, probes.tool()]
    agent = create_governed_agent(
        provider, client=client, middleware=application.middleware,
        tools=tools, context_providers=contexts,
        id=provider.deployment_agent_id, name=provider.deployment_agent_id,
        instructions=(BASE / "copilot-instructions.md").read_text(),
        default_options={"store": False})

    class GovernedHost(ResponsesHostServer):
        async def _readiness_endpoint(self, request):
            native = await super()._readiness_endpoint(request)
            return await dependency_readiness(provider) if native.status_code == 200 else native

    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    host = GovernedHost(agent, **host_options)
    if probes is not None:
        from govern_control_plane.probes import control_app
        host.router.routes.extend(control_app(probes.service, probes.auth).routes)
    return host


async def install_probe_runtime(config, provider, stack):
    from govern_control_plane.probes import ProbeOptIn
    from govern_control_plane.models import canonical
    from govern_gateway.probe_runtime import open_runtime, read_configuration
    from runtime import NativeProbeTelemetry
    option = parse(ProbeOptIn, canonical(config["probe_observability"]))
    probe_config = read_configuration(option.configuration_file)
    actual = {name: config[name] for name in (
        "agent_id", "agent_version", "image_digest", "environment", "subscription", "resource_group")}
    if (probe_config.producer != "native" or probe_config.tenant_id != provider.tenant
            or probe_config.expected_deployment.model_dump(mode="json") != actual):
        raise ValueError("native_probe_deployment_mismatch")
    workload = probe_config.workloads.get(provider.principal)
    if workload is None or workload.client_id != probe_config.service_client_id:
        raise ValueError("native_probe_identity_mismatch")
    service, auth, downstream = await stack.enter_async_context(open_runtime(probe_config))
    if service.registry.native_policy_digest != provider.policy_digest():
        raise ValueError("native_probe_policy_association_mismatch")
    telemetry = NativeProbeTelemetry(
        provider=provider, service=service, downstream=downstream, client_id=workload.client_id)
    telemetry.auth = auth


async def main():
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    config = json.loads((BASE / "governance-config.json").read_text())
    for key, variable in {
        "agent_version": "FOUNDRY_AGENT_VERSION",
        "image_digest": "TL_GOV_IMAGE_DIGEST",
        "spool_dir": "TL_GOV_SPOOL_DIR",
    }.items():
        config[key] = os.environ[variable]
    if os.environ["GOV_CONTROL_PLANE_URL"] != config["control_plane_url"]:
        raise ValueError("service_auth_binding_mismatch")
    from govern_control_plane.models import Digest, Identifier, ObjectId, canonical
    for name, schema in (("agent_version", Identifier), ("image_digest", Digest)):
        parse(schema, canonical(config[name]))
    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())
        config["principal"] = await resolve_identity(config, credential)
        crypto = await stack.enter_async_context(CryptographyClient(config["key_id"], credential=credential))
        keys = await stack.enter_async_context(KeyClient(
            config["key_id"].split("/keys/")[0], credential=credential))
        provider = await build_provider(
            config, signer=KeyVaultSigner(crypto, key_client=keys), credential=credential)
        stack.push_async_callback(provider.approval_resolver.aclose)
        await stack.enter_async_context(provider.audit)
        if "probe_observability" in config:
            await install_probe_runtime(config, provider, stack)
        client = FoundryChatClient(
            project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=credential)
        host = build_host(provider, client=client)
        await host.run_async()


if __name__ == "__main__":
    asyncio.run(main())
