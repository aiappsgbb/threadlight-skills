"""Opt-in, single-binding Citadel execution profile. No remote allow tickets."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, model_validator

from govern_control_plane.auth import EntraAuth, Settings, Unauthorized
from govern_control_plane.models import (
    Digest, Identifier, KeyId, ObjectId, StrictModel, canonical, parse,
)
from .dispatcher import (
    Action, AuthenticatedRequest, Deployment, GateError, NativePolicy, Registry,
    authenticate, digest, https_endpoint,
)

CALLER_HEADER = b"x-threadlight-consumer-authorization"
RESERVED_PREFIXES = (
    b"x-enduser", b"x-governance-", b"x-citadel-", b"x-action-", b"x-policy-",
    b"x-deployment-", b"x-requester-", b"x-tenant-id", b"x-evidence-fingerprint",
)
RESERVED_ARGUMENTS = {
    "governance_operation_id", "governance_request_context", "governance_evidence",
}


class PolicySelection(StrictModel):
    policy_id: Identifier
    version: Identifier
    digest: Digest
    key_id: KeyId


class VersionedContract(StrictModel):
    model_config = ConfigDict(serialize_by_alias=True)


class Producer(VersionedContract):
    schema_version: Literal["threadlight-citadel-producer/v1"] = Field(alias="schema")
    contract_id: Identifier
    version: Identifier
    tenant_id: ObjectId
    policy: PolicySelection
    action: Action
    facts_provider: Literal["host-identity-v1"]
    effect_protocol: Literal["registered-http-v1"]


class Consumer(VersionedContract):
    schema_version: Literal["threadlight-citadel-consumer/v1"] = Field(alias="schema")
    contract_id: Identifier
    version: Identifier
    tenant_id: ObjectId
    policy: PolicySelection
    principal: ObjectId
    client: ObjectId
    audience: Annotated[str, Field(min_length=1, max_length=256)]
    producer_id: Identifier
    producer_version: Identifier
    action: Action


class Proxy(StrictModel):
    principal: ObjectId
    client: ObjectId
    audience: Annotated[str, Field(min_length=1, max_length=256)]


def effective_action(producer, consumer):
    left, right = producer.model_dump(mode="json"), consumer.model_dump(mode="json")
    obligations = {
        "approval_roles", "approval_mode", "approval_requirement", "approval_timeout_seconds",
        "confirmation_requirement", "evidence_requirement",
    }
    if ({k: v for k, v in left.items() if k not in obligations}
            != {k: v for k, v in right.items() if k not in obligations}):
        raise ValueError("citadel_action_conflict")
    if producer.approval_roles and consumer.approval_roles:
        raise ValueError("citadel_independent_reviews_unsupported")
    result = dict(left)
    for field in obligations:
        result.pop(field, None)
    reviewer = producer if producer.approval_roles else consumer
    result["approval_roles"] = reviewer.approval_roles
    if reviewer.approval_roles:
        if reviewer.approval_mode != "deferred":
            raise ValueError("citadel_inline_review_unsupported")
        result.update(approval_mode="deferred", approval_requirement=reviewer.approval_requirement,
                      approval_timeout_seconds=reviewer.approval_timeout_seconds)
    for field in ("confirmation_requirement", "evidence_requirement"):
        a, b = getattr(producer, field), getattr(consumer, field)
        if a is not None and b is not None and a != b:
            raise ValueError("citadel_obligation_conflict")
        selected = a or b
        if selected is not None:
            result[field] = selected.model_dump(mode="json")
    return parse(Action, canonical(result))


class Binding(VersionedContract):
    schema_version: Literal["threadlight-citadel-binding/v1"] = Field(alias="schema")
    generation: Identifier
    tenant_id: ObjectId
    deployment: Deployment
    gateway_url: str
    public_url: str
    proxy: Proxy
    producer: Producer
    consumer: Consumer

    @model_validator(mode="after")
    def consistent(self):
        producer, consumer = self.producer, self.consumer
        if (producer.tenant_id != self.tenant_id or consumer.tenant_id != self.tenant_id
                or consumer.producer_id != producer.contract_id
                or consumer.producer_version != producer.version
                or producer.action.workloads != [consumer.principal]
                or consumer.action.workloads != [consumer.principal]
                or self.proxy.principal == consumer.principal or self.proxy.client == consumer.client
                or self.proxy.audience == consumer.audience):
            raise ValueError("citadel_identity_binding_conflict")
        if producer.policy == consumer.policy or producer.policy.digest == consumer.policy.digest:
            raise ValueError("citadel_independent_policies_required")
        for url in (self.gateway_url, self.public_url):
            if https_endpoint(url) != url:
                raise ValueError("citadel_noncanonical_endpoint")
        if urlsplit(self.gateway_url).netloc == urlsplit(self.public_url).netloc:
            raise ValueError("citadel_distinct_public_internal_authority_required")
        for action in (producer.action, consumer.action):
            if action.probe_safe or action.post_policy_binding is not None:
                raise ValueError("citadel_probe_or_output_policy_unsupported")
            if RESERVED_ARGUMENTS.intersection(action.input_schema["properties"]):
                raise ValueError("citadel_reserved_argument")
        effective_action(producer.action, consumer.action)
        return self

    @property
    def producer_digest(self):
        return digest(self.producer)

    @property
    def consumer_digest(self):
        return digest(self.consumer)

    def registry(self):
        return Registry(
            schema="threadlight-gateway-registry/v1", tenant_id=self.tenant_id,
            deployment=self.deployment, gateway_url=self.gateway_url,
            actions=[effective_action(self.producer.action, self.consumer.action)])

    def provenance(self):
        return {
            "generation": self.generation, "binding_digest": digest(self),
            "producer_contract_digest": self.producer_digest,
            "consumer_contract_digest": self.consumer_digest,
            "producer_policy_digest": self.producer.policy.digest,
            "consumer_policy_digest": self.consumer.policy.digest,
        }


async def evaluate_pair(binding, producer, consumer, point, arguments, safe, result=None):
    outcomes = []
    for policy, contract in ((producer, binding.producer), (consumer, binding.consumer)):
        decision, value = await policy.evaluate(
            point, contract.action, deepcopy(arguments), deepcopy(safe), deepcopy(result))
        outcomes.append((decision, value, contract.action))
    if any(decision == "deny" for decision, _, _ in outcomes):
        return "deny", arguments
    for decision, value, action in outcomes:
        if decision == "transform" or value != arguments:
            raise GateError("citadel_transform_unsupported", "blocked")
        if decision == "escalate" and not (action.approval_roles or action.confirmation_requirement):
            raise GateError("citadel_unrepresented_obligation", "blocked")
    return ("escalate" if any(d == "escalate" for d, _, _ in outcomes) else "allow"), arguments


class CitadelPolicy:
    """Three signed snapshots activate together; a restart is the update boundary."""
    terminal_recheck = True

    @classmethod
    async def load(cls, *, generation, producer_path, consumer_path, envelopes, signer):
        raw = (generation.bundle.root / "citadel-binding.json").read_bytes()
        if len(raw) > 65536:
            raise GateError("citadel_binding_unavailable")
        binding = parse(Binding, raw)
        if generation.registry != binding.registry():
            raise GateError("citadel_effective_registry_mismatch")
        policies = []
        for owner, path in ((binding.producer, producer_path), (binding.consumer, consumer_path)):
            selection = owner.policy
            policy = await NativePolicy.load(
                bundle_path=Path(path), signed=envelopes[selection.policy_id, selection.version],
                signer=signer, tenant=binding.tenant_id, key_id=selection.key_id,
                policy_id=selection.policy_id, version=selection.version,
                expected_digest=selection.digest,
                allowed_endpoints=[endpoint for endpoint in (
                    owner.action.endpoint, owner.action.outcome_endpoint, owner.action.recovery_endpoint)
                    if endpoint is not None],
                gateway_url=binding.gateway_url)
            expected = binding.registry().model_copy(update={"actions": [owner.action]})
            if policy.registry != expected:
                raise GateError("citadel_owner_registry_mismatch")
            policies.append(policy)
        obj = cls()
        obj.binding, obj.generation = binding, generation
        obj.producer, obj.consumer = policies
        obj.registry, obj.digest, obj.policy_id = generation.registry, generation.digest, generation.policy_id
        obj.expires_at = min(p.expires_at for p in (generation, *policies))
        obj.fresh()
        return obj

    def fresh(self):
        for policy in (self.generation, self.producer, self.consumer):
            policy.fresh()
        if datetime.now(timezone.utc) >= self.expires_at:
            raise GateError("citadel_generation_expired")
        if parse(Binding, (self.generation.bundle.root / "citadel-binding.json").read_bytes()) != self.binding:
            raise GateError("citadel_binding_changed")

    async def authority_health(self):
        for policy in (self.generation, self.producer, self.consumer):
            await policy.authority_health()
        self.fresh()

    async def evaluate(self, point, action, arguments, safe, result=None):
        self.fresh()
        if action != self.registry.actions[0] or point != "pre_tool_call":
            raise GateError("citadel_binding_unavailable")
        outcome = await evaluate_pair(
            self.binding, self.producer, self.consumer, point, arguments, safe, result)
        self.fresh()
        return outcome


class CitadelIngress:
    def __init__(self, binding, caller_auth, proxy_auth):
        self.binding, self.caller_auth, self.proxy_auth = binding, caller_auth, proxy_auth

    @classmethod
    def configured(cls, binding, config, http):
        if (config.tenant_id != binding.tenant_id or config.token_version != "2.0"
                or config.audience != binding.consumer.audience
                or set(config.workloads) != {binding.consumer.principal}
                or config.workloads[binding.consumer.principal].client_id != binding.consumer.client
                or config.workloads[binding.consumer.principal].agent_id != binding.deployment.agent_id
                or config.gateway_url != binding.gateway_url
                or config.service_principal in {binding.consumer.principal, binding.proxy.principal}
                or config.service_client_id in {binding.consumer.client, binding.proxy.client}
                or config.downstream_client_id in {binding.consumer.client, binding.proxy.client}
                or any(k != config.key_id for k in (
                    binding.producer.policy.key_id, binding.consumer.policy.key_id))):
            raise ValueError("citadel_host_identity_mismatch")
        base = {key: value for key, value in config.model_dump(mode="json").items()
                if key in Settings.model_fields}
        base.update(audience=binding.proxy.audience, workloads={
            binding.proxy.principal: {"client_id": binding.proxy.client,
                "agent_id": "citadel-proxy", "policies": [config.policy_id]}})
        return cls(binding, EntraAuth(config, http),
                   EntraAuth(parse(Settings, canonical(base)), http))

    async def health(self):
        await self.caller_auth.health()
        await self.proxy_auth.health()

    async def authenticate(self, headers):
        def one(name):
            values = [v.decode("latin1") for k, v in headers if k.lower() == name]
            if len(values) != 1:
                raise Unauthorized()
            return values[0]
        if any(k.lower().startswith(RESERVED_PREFIXES)
               for k, _ in headers):
            raise Unauthorized()
        proxy = await authenticate(self.proxy_auth, one(b"authorization"))
        caller = await authenticate(self.caller_auth, one(CALLER_HEADER))
        b = self.binding
        if ((proxy.identity.tenant, proxy.identity.subject, proxy.identity.client)
                != (b.tenant_id, b.proxy.principal, b.proxy.client)
                or (caller.identity.tenant, caller.identity.subject, caller.identity.client)
                != (b.tenant_id, b.consumer.principal, b.consumer.client)):
            raise Unauthorized()
        request = AuthenticatedRequest(caller.identity, min(proxy.expires_at, caller.expires_at))
        request.fresh()
        return request
