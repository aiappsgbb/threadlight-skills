"""The gateway PEP. Trusted registry + local native ACS precede irreversible HTTP.

Only external authority/transport/storage seams are injectable. Production uses
Task8 EntraAuth, AzureStore, KeyVaultSigner and authenticated service clients.
"""
from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from importlib.metadata import version as distribution_version
import json
import os
from pathlib import Path
import re
import time
from typing import Annotated, Literal
from urllib.parse import urlsplit
import uuid

import httpx
import jwt
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import Field, model_validator

from govern_bundle.policy_bundle import PIN_FILE, verify_bundle, validate_native_manifest
from govern_control_plane.auth import Unauthorized
from govern_control_plane.attestations import (
    EVIDENCE_ARGUMENT, EvidenceError, EvidenceRequirement, verify_attestation,
)
from govern_control_plane.confirmation import (
    CONTEXT_ARGUMENT, ConfirmationIntent, ConfirmationRequirement, ConfirmationUnavailable,
)
from govern_control_plane.models import (
    ApprovalContext, ApprovalRequest, DecisionReceipt, Digest, Identifier, ObjectId, StrictModel,
    SignedBundle, Timestamp, canonical, envelope_digest, parse, strict_json,
)
from govern_control_plane.probes import ProbeContract, PROBE_INPUT, PROBE_OUTPUT
from govern_control_plane.storage import Conflict, Missing

from .receipts import ApprovalService, ApprovalUnavailable, ReceiptService

MAX_BYTES = 16384
_request_identity = ContextVar("gateway_request_identity", default=None)
_transport_ticket = ContextVar("gateway_transport_ticket", default=None)


class GateError(Exception):
    def __init__(self, reason, status="unavailable"):
        self.reason, self.status = reason, status
        super().__init__(reason)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def https_endpoint(value):
    if (not isinstance(value, str) or len(value) > 512 or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid_endpoint")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.port not in (None, 443) or url.query or url.fragment
            or not re.fullmatch(r"[a-z0-9.-]+", url.hostname)
            or not re.fullmatch(r"/[A-Za-z0-9_/-]+", url.path)
            or "//" in url.path or any(p in (".", "..") for p in url.path.split("/"))
            or url.hostname.endswith(".")):
        raise ValueError("invalid_endpoint")
    return f"https://{url.hostname}{url.path}"


def check_schema(schema, depth=0):
    """Deliberately bounded JSON Schema subset; no remote refs, coercion or defaults."""
    if depth > 8 or not isinstance(schema, dict):
        raise ValueError("invalid_schema")
    if set(schema) - {"type", "properties", "required", "additionalProperties", "items",
                      "maxItems", "minItems", "maxLength", "minLength", "minimum", "maximum",
                      "enum", "description"}:
        raise ValueError("unsupported_schema")
    Draft202012Validator.check_schema(schema)
    kind = schema.get("type")
    if kind == "object":
        if (schema.get("additionalProperties") is not False
                or not isinstance(schema.get("properties"), dict)
                or len(schema["properties"]) > 32):
            raise ValueError("unbounded_schema")
        for name, child in schema["properties"].items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
                raise ValueError("invalid_schema")
            check_schema(child, depth + 1)
    elif kind == "array":
        if type(schema.get("maxItems")) is not int or not 0 <= schema["maxItems"] <= 128:
            raise ValueError("unbounded_schema")
        check_schema(schema.get("items"), depth + 1)
    elif kind == "string":
        if not schema.get("enum") and not 0 < schema.get("maxLength", 0) <= 4096:
            raise ValueError("unbounded_schema")
    elif kind not in ("integer", "number", "boolean", "null"):
        raise ValueError("invalid_schema")


def validated(value, schema):
    try:
        raw = canonical(value)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        value = strict_json(raw)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
        return value
    except Exception:
        raise GateError("schema_invalid", "blocked") from None


class Deployment(StrictModel):
    agent_id: Identifier
    agent_version: Identifier
    image_digest: Digest
    environment: Literal["staging", "preproduction", "production"]
    subscription: ObjectId
    resource_group: Identifier


class Action(StrictModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
    policy_binding: Identifier
    post_policy_binding: Identifier | None
    workloads: Annotated[list[ObjectId], Field(min_length=1, max_length=128)]
    scope: Identifier
    approval_roles: Annotated[list[Identifier], Field(max_length=16)]
    approval_mode: Literal["inline", "deferred"] = Field(
        default="inline", exclude_if=lambda value: value == "inline")
    approval_requirement: Literal["always", "policy"] = Field(
        default="always", exclude_if=lambda value: value == "always")
    approval_timeout_seconds: Annotated[int, Field(gt=0, le=3600)] = Field(
        default=300, exclude_if=lambda value: value == 300)
    endpoint: str
    outcome_endpoint: str
    credential_scope: Annotated[str, Field(pattern=r"^api://[A-Za-z0-9._/-]+/\.default$")]
    input_schema: dict
    output_schema: dict
    probe_safe: bool = False
    probe_contract: ProbeContract | None = None
    evidence_requirement: EvidenceRequirement | None = Field(
        default=None, exclude_if=lambda value: value is None)
    confirmation_requirement: ConfirmationRequirement | None = Field(
        default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def explicit_evidence(cls, value):
        if isinstance(value, dict) and "evidence_requirement" in value and value["evidence_requirement"] is None:
            raise ValueError("evidence_configuration_required")
        if isinstance(value, dict) and "confirmation_requirement" in value and value["confirmation_requirement"] is None:
            raise ValueError("confirmation_configuration_required")
        return value

    @model_validator(mode="after")
    def validate_action(self):
        if self.confirmation_requirement is not None:
            if self.probe_safe or self.name == "governance_probe_noop":
                raise ValueError("confirmation_not_probe_proof")
            if self.approval_roles and self.approval_mode != "deferred":
                raise ValueError("combined_confirmation_requires_deferred_review")
            if any(name in self.input_schema.get("properties", {}) for name in (
                    CONTEXT_ARGUMENT, "governance_operation_id")):
                raise ValueError("reserved_confirmation_argument")
        if self.evidence_requirement is not None:
            if self.probe_safe:
                raise ValueError("business_evidence_not_probe_proof")
            for name in (self.evidence_requirement.case_field, self.evidence_requirement.revision_field):
                if (name not in self.input_schema.get("required", [])
                        or self.input_schema.get("properties", {}).get(name, {}).get("type") != "string"):
                    raise ValueError("evidence_binding_fields_required")
        if EVIDENCE_ARGUMENT in self.input_schema.get("properties", {}):
            raise ValueError("reserved_evidence_argument")
        if ((self.approval_mode == "deferred" or self.approval_requirement == "policy")
                and not self.approval_roles):
            raise ValueError("approval_roles_required")
        if self.approval_mode != "deferred" and self.approval_timeout_seconds != 300:
            raise ValueError("deferred_approval_timeout_required")
        if not self.probe_safe and self.probe_contract is not None:
            raise ValueError("probe_contract_requires_opt_in")
        if self.probe_safe and (
                self.probe_contract is None or self.name != "governance_probe_noop"
                or self.scope != "governance-probe" or self.approval_roles
                or self.post_policy_binding is not None or self.approval_mode != "inline"
                or self.input_schema != PROBE_INPUT or self.output_schema != PROBE_OUTPUT
                or urlsplit(self.endpoint).path != "/governance/noop"
                or urlsplit(self.outcome_endpoint).path != "/governance/outcomes"
                or urlsplit(self.endpoint).netloc != urlsplit(self.outcome_endpoint).netloc):
            raise ValueError("dedicated_noop_probe_required")
        if self.policy_binding == "none" or self.post_policy_binding == "none":
            raise ValueError("unbound_gateway_action")
        for schema in (self.input_schema, self.output_schema):
            if schema.get("type") != "object":
                raise ValueError("object_schema_required")
            check_schema(schema)
        if self.approval_mode == "deferred" and "governance_operation_id" in self.input_schema.get("properties", {}):
            raise ValueError("reserved_approval_operation_field")
        if len(set(self.workloads)) != len(self.workloads):
            raise ValueError("duplicate_workload")
        if len(set(self.approval_roles)) != len(self.approval_roles):
            raise ValueError("duplicate_approval_role")
        for endpoint in (self.endpoint, self.outcome_endpoint):
            if https_endpoint(endpoint) != endpoint:
                raise ValueError("noncanonical_endpoint")
        return self


class Registry(StrictModel):
    schema_version: Literal["threadlight-gateway-registry/v1"] = Field(alias="schema")
    tenant_id: ObjectId
    gateway_url: str
    deployment: Deployment
    actions: Annotated[list[Action], Field(min_length=1, max_length=64)]
    native_policy_digest: Digest | None = None

    @model_validator(mode="after")
    def unique_actions(self):
        if self.native_policy_digest is not None and not all(a.probe_safe for a in self.actions):
            raise ValueError("native_probe_registry_only")
        if self.deployment.environment == "production" and any(a.probe_safe for a in self.actions):
            raise ValueError("staging_probe_only")
        if len({a.name for a in self.actions}) != len(self.actions):
            raise ValueError("duplicate_action")
        if https_endpoint(self.gateway_url) != self.gateway_url:
            raise ValueError("noncanonical_endpoint")
        return self


class NativePolicy:
    @classmethod
    async def load(cls, *, bundle_path, signed, signer, tenant, key_id, policy_id,
                   version, expected_digest, allowed_endpoints, gateway_url):
        from agent_control_specification import AgentControl
        import yaml
        signed = parse(SignedBundle, canonical(signed))
        envelope = signed.envelope
        await signer.health()
        if (envelope.tenant_id != tenant or envelope.key_id != key_id
                or envelope.policy_id != policy_id or envelope.version != version
                or envelope.content_digest != expected_digest
                or envelope.expires_at <= datetime.now(timezone.utc)
                or not await signer.verify(envelope_digest(envelope),
                    base64.b64decode(signed.signature, validate=True))):
            raise GateError("policy_unavailable")
        bundle = verify_bundle(bundle_path, expected_digest=expected_digest)
        validate_native_manifest(bundle.root)
        if "gateway-registry.json" not in {f["path"] for f in bundle.files}:
            raise GateError("registry_unavailable")
        raw = (bundle.root / "gateway-registry.json").read_bytes()
        if len(raw) > 256 * 1024:
            raise GateError("registry_unavailable")
        registry = parse(Registry, raw)
        metadata = strict_json((bundle.root / "bundle-metadata.json").read_bytes())
        if (metadata["policy_id"] != policy_id or metadata["version"] != version
                or registry.tenant_id != tenant or registry.gateway_url != https_endpoint(gateway_url)):
            raise GateError("registry_unavailable")
        def points(path):
            doc = yaml.safe_load(path.read_text())
            merged = {}
            for parent in doc.get("extends", []):
                merged.update(points(path.parent / parent))
            merged.update(doc.get("intervention_points", {}))
            return merged
        declared = points(bundle.manifest_path)
        for action in registry.actions:
            if not {action.endpoint, action.outcome_endpoint} <= set(allowed_endpoints):
                raise GateError("endpoint_unavailable")
            for point, binding, target in (
                ("pre_tool_call", action.policy_binding, "$.tool_call.args"),
                ("post_tool_call", action.post_policy_binding, "$.tool_result"),
            ):
                if binding and (declared.get(point, {}).get("policy", {}).get("id") != binding
                                or declared[point].get("policy_target") != target):
                    raise GateError("binding_unavailable")
        pins = json.loads(PIN_FILE.read_text())
        for key in ("agt", "acs", "agent_hooks"):
            if distribution_version(pins[key]["distribution"]) != pins[key]["version"]:
                raise GateError("pin_mismatch")
        opa = Path(os.environ.get("ACS_OPA_PATH", ""))
        if not opa.is_absolute() or opa.is_symlink() or not opa.is_file():
            raise GateError("opa_unavailable")
        with opa.open("rb") as binary:
            if hashlib.file_digest(binary, "sha256").hexdigest() != pins["opa"]["linux_amd64_static_sha256"]:
                raise GateError("opa_unavailable")
        result = cls()
        result.opa_path = str(opa)
        result.policy_id = envelope.policy_id
        result.bundle, result.registry = bundle, registry
        result.expires_at, result.digest = envelope.expires_at, expected_digest
        result.deadline = time.monotonic() + (envelope.expires_at - datetime.now(timezone.utc)).total_seconds()
        result.engine = AgentControl.from_path(str(bundle.manifest_path))
        # Detect content changes across parsing/native loading before publishing a ready instance.
        result.fresh()
        return result

    def fresh(self):
        if datetime.now(timezone.utc) >= self.expires_at or time.monotonic() >= self.deadline:
            raise GateError("policy_unavailable")
        if os.environ.get("ACS_OPA_PATH") != self.opa_path:
            raise GateError("opa_unavailable")
        verify_bundle(self.bundle.root, expected_digest=self.digest)

    async def evaluate(self, point, action, arguments, safe, result=None):
        from agent_control_specification import InterventionPointResult
        self.fresh()
        snapshot = {"tool_call": {"name": action.name, "args": deepcopy(arguments)},
                    "safe": deepcopy(safe)}
        if point == "post_tool_call":
            snapshot["tool_result"] = result
        outcome = await asyncio.wait_for(self.engine.evaluate_intervention_point(
            point, snapshot, mode="enforce"), 5)
        self.fresh()
        if (not isinstance(outcome, InterventionPointResult)
                or (outcome.verdict.reason or "").startswith(("runtime_error", "host_error"))):
            raise GateError("engine_unavailable")
        decision = outcome.verdict.decision.value
        if decision not in {"allow", "deny", "transform", "escalate"}:
            raise GateError("engine_unavailable")
        if decision == "transform":
            if not outcome.transformed_policy_target_applied:
                raise GateError("engine_unavailable")
            return decision, outcome.transformed_policy_target
        if outcome.transformed_policy_target_applied or outcome.verdict.transform is not None:
            raise GateError("engine_unavailable")
        return decision, result if point == "post_tool_call" else arguments


@dataclass(frozen=True)
class AuthenticatedRequest:
    identity: object
    expires_at: float

    def fresh(self):
        if time.time() >= self.expires_at:
            raise GateError("authentication_expired")


async def authenticate(auth, authorization):
    identity = await auth.authenticate(authorization)
    if identity.workload is None:
        raise Unauthorized()
    # Parsed only AFTER EntraAuth authenticates this exact JWT, never as authority.
    expires = jwt.decode(authorization[7:], options={"verify_signature": False})["exp"]
    request = AuthenticatedRequest(identity, expires)
    request.fresh()
    return request


class AuthorizedTransport(httpx.AsyncBaseTransport):
    """One-use wire binding after HTTPX auth/hooks, before the irreversible transport."""
    def __init__(self, inner):
        self.inner = inner

    async def handle_async_request(self, request):
        ticket = _transport_ticket.get()
        if ticket is None or ticket["sent"]:
            raise GateError("transport_not_authorized")
        async def check():
            guarded = ticket["guard"]()
            if inspect.isawaitable(guarded):
                await guarded
            if ticket.get("effect_check") is not None:
                checked = ticket["effect_check"]()
                if inspect.isawaitable(checked):
                    await checked
            if (request.method != ticket["method"] or request.url != httpx.URL(ticket["endpoint"])
                    or type(request.stream) is not httpx.ByteStream
                    or b"".join(request.stream) != ticket["body"]
                    or any(request.headers.get(name) != value for name, value in ticket["headers"].items())):
                raise GateError("wire_changed")
        async def trace(event, info):
            if event in ("http11.send_request_headers.started", "http11.send_request_body.started"):
                await check()
        await check()
        if ticket.get("on_dispatch") is not None:
            await ticket["on_dispatch"]()
            await check()
        ticket["sent"] = True
        # Replace, rather than trust, a request hook's trace callback.
        request.extensions["trace"] = trace
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


class DownstreamClient:
    def __init__(self, *, credential, transport=None, timeout=5.0):
        if not 0 < timeout <= 30:
            raise ValueError("invalid_timeout")
        self.credential, self.timeout = credential, timeout
        self.http = httpx.AsyncClient(
            transport=AuthorizedTransport(transport or httpx.AsyncHTTPTransport(retries=0, http2=False)),
            trust_env=False, follow_redirects=False, timeout=timeout)

    async def aclose(self):
        await self.http.aclose()

    async def request(self, *, action, arguments, key, action_hash, provenance, facts, guard,
                      retrieve=False, on_dispatch=None, effect_check=None):
        async with asyncio.timeout(self.timeout):
            token = await self.credential.get_token(action.credential_scope)
            content = canonical(validated(arguments, action.input_schema)) if not retrieve else None
            endpoint = action.outcome_endpoint if retrieve else action.endpoint
            method = "GET" if retrieve else "POST"
            headers = {
                "Authorization": f"Bearer {token.token}", "Content-Type": "application/json",
                "Idempotency-Key": digest([facts["tenant"], facts["subject"], facts["action"], key])[7:],
                "X-Action-Hash": action_hash,
                "X-Governance-Provenance": provenance,
                "X-Tenant-ID": facts["tenant"], "X-Requester-ID": facts["subject"],
                "X-Action-ID": facts["action"], "X-Policy-Digest": facts["policy"],
                "X-Deployment-Hash": digest(facts["deployment"]),
                "Accept-Encoding": "identity",
            }
            if "evidence_fingerprint" in facts:
                headers["X-Evidence-Fingerprint"] = facts["evidence_fingerprint"]
            if action.probe_safe:
                headers["X-Probe-Run-ID"] = arguments["probe_run_id"]
                headers["X-Requester-Client"] = facts["client"]
            async def final_check():
                checked = guard()
                if inspect.isawaitable(checked):
                    await checked
                if token.expires_on <= time.time():
                    raise GateError("downstream_authentication_expired")
            await final_check()
            marker = _transport_ticket.set({
                "method": method, "endpoint": endpoint, "body": content or b"",
                "headers": dict(headers), "guard": final_check, "sent": False,
                "on_dispatch": on_dispatch, "effect_check": effect_check})
            try:
                async with self.http.stream(method, endpoint, content=content, headers=headers,
                        follow_redirects=False) as response:
                    if (response.status_code != 200
                            or response.headers.get("content-encoding", "identity") != "identity"):
                        raise GateError("downstream_unavailable")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            raise GateError("downstream_unavailable")
            finally:
                _transport_ticket.reset(marker)
            await final_check()
            reply = strict_json(bytes(raw))
            if not isinstance(reply, dict) or set(reply) != {"receipt_id", "result"}:
                raise GateError("downstream_unavailable")
            parse(Identifier, canonical(reply["receipt_id"]))
            return reply


class GovernedDispatcher:
    def __init__(self, *, policy, auth, store, receipts: ReceiptService, downstream,
                 approvals: ApprovalService | None, safe_provider,
                 approval_principal, approval_agent_id, timeout=15.0, probes=None, confirmations=None):
        if not 0 < timeout <= 30:
            raise ValueError("invalid_timeout")
        self.policy, self.auth, self.store, self.receipts = policy, auth, store, receipts
        self.downstream, self.approvals, self.safe_provider = downstream, approvals, safe_provider
        self.approval_principal, self.approval_agent_id = approval_principal, approval_agent_id
        self.timeout = timeout
        self.probes = probes
        self.confirmations = confirmations

    def approval_context(self, action, *, tenant):
        return ApprovalContext(
            principal=self.approval_principal, agent_id=self.approval_agent_id,
            tenant=tenant, allowed_roles=tuple(action.approval_roles))

    async def dispatch(self, *, authorization, action, arguments, idempotency_key, evidence_token=None,
                       requesting_user_context=None):
        attempted = False
        probe = None
        try:
            async with asyncio.timeout(self.timeout):
                request = _request_identity.get() or await authenticate(self.auth, authorization)
                identity = request.identity
                registry = self.policy.registry
                selected = next((a for a in registry.actions if a.name == action), None)
                if (identity.tenant != registry.tenant_id or selected is None
                        or identity.subject not in selected.workloads
                        or identity.workload.agent_id != registry.deployment.agent_id):
                    raise GateError("scope_denied", "blocked")
                parse(Identifier, canonical(idempotency_key))
                if selected.confirmation_requirement is not None and requesting_user_context is not None:
                    from govern_control_plane.models import Nonce
                    parse(Nonce, canonical(requesting_user_context))
                elif selected.confirmation_requirement is None and requesting_user_context is not None:
                    raise GateError("confirmation_not_selected", "blocked")
                arguments = validated(arguments, selected.input_schema)
                facts = {
                    "tenant": identity.tenant, "subject": identity.subject, "client": identity.client,
                    "action": selected.name, "scope": selected.scope, "policy": self.policy.digest,
                    "deployment": registry.deployment.model_dump(mode="json"),
                }
                input_hash = digest({"facts": facts, "arguments": arguments})
                context_expiry = None
                if requesting_user_context is not None:
                    input_hash = digest({"input_hash": input_hash, "context_ref": requesting_user_context})
                request_facts_hash = digest(facts)
                action_hash = input_hash
                self.policy.fresh()
                if self.policy.policy_id not in identity.workload.policies:
                    raise GateError("binding_unavailable")
                if selected.probe_safe:
                    if self.probes is None:
                        raise GateError("probe_unavailable")
                    probe = await self.probes.begin(
                        identity.subject, arguments["probe_run_id"], action, arguments["variant"],
                        session_id=uuid.uuid4().hex, call_id=uuid.uuid4().hex)
                scope = digest([identity.tenant, identity.subject, selected.name])
                key = digest(idempotency_key)[7:]
                # Read-only completed-cache exception: no new evaluation/approval consumption
                # for an effect already durably completed under these exact authenticated facts.
                existing = None
                existing_etag = None
                try:
                    existing, existing_etag = await self.store.read(scope, key)
                except Missing:
                    pass
                def guard():
                    request.fresh()
                    self.policy.fresh()
                guard()
                if existing is not None:
                    if (existing["input_hash"] != input_hash
                            or existing.get("request_facts_hash", existing["facts_hash"]) != request_facts_hash):
                        raise GateError("idempotency_conflict", "blocked")
                    if existing["state"] == "rejected":
                        return {"status": "blocked", "reason_code": existing.get("reason_code", "approval_denied")}
                    if existing["state"] not in ("completed", "awaiting_approval", "awaiting_confirmation"):
                        raise GateError("outcome_unknown")
                    if existing["state"] == "awaiting_approval" and selected.approval_mode != "deferred":
                        raise GateError("outcome_unknown")
                if existing is not None and existing["state"] == "completed":
                    if selected.evidence_requirement is not None:
                        # This retrieves a durable outcome, never a new effect. Neither
                        # expired JWTs nor a provider outage can undo that completion.
                        facts["evidence_fingerprint"] = parse(
                            Digest, canonical(existing["evidence_fingerprint"]))
                        if (digest(facts) != existing["facts_hash"]
                                or digest({"facts": facts, "arguments": arguments}) != existing["action_hash"]):
                            raise GateError("outcome_unknown")
                        reply = await self.downstream.request(
                            action=selected, arguments=None, key=key, action_hash=existing["action_hash"],
                            provenance=existing["receipt_id"], facts=facts, guard=guard, retrieve=True)
                        if reply["receipt_id"] != existing["outcome_reference"]:
                            raise GateError("outcome_unknown")
                        return await self.output(selected, arguments, facts, reply, guard)
                    # Reconstruct transformed arguments without storing payloads. A completed
                    # approval is not consumed twice, but changed host evidence still closes output.
                    safe = self.safe_provider(deepcopy(facts))
                    if not isinstance(safe, dict) or safe.get("scope") != selected.scope:
                        raise GateError("safe_evidence_unavailable")
                    decision, enforced = await self.policy.evaluate(
                        "pre_tool_call", selected, arguments, safe)
                    if decision == "deny":
                        raise GateError("policy_deny", "blocked")
                    enforced = validated(enforced, selected.input_schema)
                    if digest({"facts": facts, "arguments": enforced}) != existing["action_hash"]:
                        raise GateError("outcome_unknown")
                    reply = await self.downstream.request(
                        action=selected, arguments=None, key=key, action_hash=existing["action_hash"],
                        provenance=existing["receipt_id"], facts=facts, guard=guard, retrieve=True)
                    if reply["receipt_id"] != existing["outcome_reference"]:
                        raise GateError("outcome_unknown")
                    return await self.output(selected, enforced, facts, reply, guard)
                if requesting_user_context is not None:
                    if self.confirmations is None:
                        raise GateError("confirmation_unavailable")
                    context_expiry = await self.confirmations.context(
                        requesting_user_context, facts=facts, operation_id=idempotency_key,
                        provider_profile=selected.confirmation_requirement.provider_profile)
                    guard()
                evidence = None
                if selected.evidence_requirement is not None:
                    try:
                        evidence = verify_attestation(
                            evidence_token, selected.evidence_requirement, facts, arguments)
                    except EvidenceError as error:
                        await self.audit(selected, input_hash, key, "deny", str(error))
                        raise
                    facts["evidence_fingerprint"] = evidence.fingerprint
                    if existing is not None and existing["facts_hash"] != digest(facts):
                        raise GateError("approval_context_changed", "blocked")
                elif evidence_token is not None:
                    raise GateError("evidence_not_selected", "blocked")

                def current_safe():
                    safe = self.safe_provider(deepcopy(facts))
                    if not isinstance(safe, dict) or safe.get("scope") != selected.scope:
                        raise GateError("safe_evidence_unavailable")
                    if evidence is not None:
                        evidence.fresh()
                        safe = {**safe, "evidence": deepcopy(evidence.safe)}
                    return safe

                safe = current_safe()
                safe_hash = digest(safe)
                evidence_digest = evidence.fingerprint if evidence else None
                decision, enforced = await self.policy.evaluate(
                    "pre_tool_call", selected, arguments, safe)
                if probe and (decision not in ("allow", "deny") or enforced != arguments):
                    raise GateError("probe_policy_unsupported")
                if decision == "deny":
                    receipt_id = await self.audit(
                        selected, digest({"facts": facts, "arguments": arguments}), key,
                        "deny", "policy_deny", probe, evidence_digest)
                    if probe:
                        await self.probes.intercept(probe, decision="deny", receipt_id=receipt_id)
                        await self.probes.complete(probe, terminal="denied")
                    return {"status": "blocked", "reason_code": "policy_deny",
                            **({"receipt_id": receipt_id} if probe else {})}
                enforced = validated(enforced, selected.input_schema)
                if evidence is not None and enforced != arguments:
                    raise GateError("evidence_arguments_changed", "blocked")
                action_hash = digest({"facts": facts, "arguments": enforced})
                confirmation_intent = None
                confirmation_expiry = None

                def check_confirmation_authority(response=None):
                    nonlocal confirmation_expiry
                    if response is not None:
                        expiry = parse(Timestamp, canonical(response["effective_authority_expires_at"]))
                        if expiry > min(confirmation_intent.expires_at, confirmation_intent.policy_expires_at):
                            raise GateError("confirmation_authority_invalid", "blocked")
                        confirmation_expiry = min(expiry, confirmation_expiry) if confirmation_expiry else expiry
                    if confirmation_expiry is not None and confirmation_expiry <= datetime.now(timezone.utc):
                        raise GateError("confirmation_authority_expired", "blocked")

                confirmation_active = selected.confirmation_requirement is not None and (
                    selected.confirmation_requirement.trigger == "always" or decision == "escalate"
                    or existing is not None and "confirmation_intent" in existing)
                review_required = (decision == "escalate" and bool(selected.approval_roles)
                    or bool(selected.approval_roles) and selected.approval_requirement == "always"
                    or existing is not None and existing["state"] == "awaiting_approval")
                # Only the signed confirmation opt-in supplies the new obligation.
                # Existing declarations and any configured reviewer remain independent.
                if decision == "escalate" and not (selected.approval_roles or confirmation_active):
                    raise GateError("approval_unavailable")
                if confirmation_active:
                    if requesting_user_context is None:
                        raise GateError("confirmation_context_required", "blocked")
                    if self.confirmations is None:
                        raise GateError("confirmation_unavailable")
                    if existing is None:
                        expiry = min(context_expiry, self.policy.expires_at, datetime.now(timezone.utc) + timedelta(
                            seconds=selected.confirmation_requirement.max_age_seconds))
                        confirmation_intent = ConfirmationIntent(
                            confirmation_id=uuid.uuid4().hex, context_ref=requesting_user_context,
                            operation_id=idempotency_key, principal=self.approval_principal,
                            tenant=identity.tenant, agent_id=registry.deployment.agent_id, action=selected.name,
                            action_hash=action_hash, facts_hash=digest(facts), safe_hash=safe_hash,
                            policy_hash=self.policy.digest, policy_expires_at=self.policy.expires_at,
                            expires_at=expiry, requirement=selected.confirmation_requirement,
                            reviewer_required=review_required)
                        pending = {"state": "awaiting_confirmation", "input_hash": input_hash,
                            "action_hash": action_hash, "facts_hash": digest(facts), "safe_hash": safe_hash,
                            "confirmation_intent": confirmation_intent.model_dump(mode="json"),
                            "request_facts_hash": request_facts_hash,
                            **({"evidence_fingerprint": evidence_digest} if evidence else {}),
                            "receipt_id": None, "outcome_reference": None}
                        try:
                            await self.store.create(scope, key, pending)
                        except Conflict:
                            raise GateError("outcome_unknown") from None
                        existing, existing_etag = await self.store.read(scope, key)
                        if existing != pending:
                            raise GateError("outcome_unknown")
                    confirmation_intent = parse(ConfirmationIntent, canonical(existing["confirmation_intent"]))
                    if (confirmation_intent.context_ref != requesting_user_context
                            or confirmation_intent.operation_id != idempotency_key
                            or confirmation_intent.action_hash != action_hash
                            or confirmation_intent.facts_hash != digest(facts)
                            or confirmation_intent.safe_hash != safe_hash
                            or confirmation_intent.policy_hash != self.policy.digest
                            or confirmation_intent.policy_expires_at != self.policy.expires_at
                            or confirmation_intent.requirement != selected.confirmation_requirement
                            or confirmation_intent.reviewer_required != review_required):
                        raise GateError("confirmation_context_changed", "blocked")
                    if confirmation_intent.expires_at <= datetime.now(timezone.utc):
                        raise GateError("confirmation_expired", "blocked")
                    confirmation = await self.confirmations.resolve(
                        confirmation_intent, facts=facts, arguments=enforced)
                    guard()
                    if confirmation["status"] == "pending_confirmation":
                        return confirmation
                    check_confirmation_authority(confirmation)
                    if confirmation["status"] == "rejected":
                        await self.audit(selected, action_hash, key, "deny", "confirmation_denied",
                                         evidence_fingerprint=evidence_digest)
                        await self.store.replace(scope, key, {**existing, "state": "rejected",
                            "reason_code": "confirmation_denied"}, existing_etag)
                        return {"status": "blocked", "reason_code": "confirmation_denied"}
                approval_expiry = None
                deferred = False
                grant = None
                if review_required:
                    if self.approvals is None or not selected.approval_roles:
                        raise GateError("approval_unavailable")
                    deferred = selected.approval_mode == "deferred"
                    expiry = min(self.policy.expires_at, datetime.now(timezone.utc)
                                 + timedelta(seconds=selected.approval_timeout_seconds if deferred else self.timeout))
                    intent = ApprovalRequest(
                        **self.approval_context(selected, tenant=identity.tenant).model_dump(),
                        action_hash=action_hash, policy_hash=self.policy.digest,
                        session_id=key,
                        context_identity=digest(facts), nonce=uuid.uuid4().hex,
                        expires_at=expiry, policy_expires_at=self.policy.expires_at)
                    if deferred:
                        if not callable(getattr(self.approvals, "request_pending", None)):
                            raise GateError("deferred_approval_unavailable")
                        safe_hash = digest(safe)
                        if existing is None or existing["state"] == "awaiting_confirmation":
                            pending = {
                                **(existing or {}),
                                "state": "awaiting_approval", "input_hash": input_hash, "action_hash": action_hash,
                                "facts_hash": digest(facts), "safe_hash": safe_hash,
                                **({"request_facts_hash": request_facts_hash,
                                    "evidence_fingerprint": evidence_digest} if evidence else {}),
                                "approval_intent": intent.model_dump(mode="json"),
                                "receipt_id": None, "outcome_reference": None,
                            }
                            try:
                                if existing is None:
                                    await self.store.create(scope, key, pending)
                                else:
                                    await self.store.replace(scope, key, pending, existing_etag)
                            except Conflict:
                                raise GateError("outcome_unknown") from None
                            existing, existing_etag = await self.store.read(scope, key)
                            if existing != pending:
                                raise GateError("outcome_unknown")
                        intent = parse(ApprovalRequest, canonical(existing["approval_intent"]))
                        if (existing["safe_hash"] != safe_hash or existing["action_hash"] != action_hash
                                or intent.action_hash != action_hash or intent.policy_hash != self.policy.digest
                                or intent.policy_expires_at != self.policy.expires_at
                                or intent.context_identity != digest(facts)
                                or intent.session_id != key
                                or intent.principal != self.approval_principal
                                or intent.agent_id != self.approval_agent_id or intent.tenant != identity.tenant
                                or intent.allowed_roles != tuple(selected.approval_roles)):
                            raise GateError("approval_context_changed", "blocked")
                        expiry = intent.expires_at
                        if expiry <= datetime.now(timezone.utc):
                            raise GateError("approval_expired", "blocked")
                        request_review = getattr(self.approvals, "request_review", None)
                        if getattr(self.approvals, "review_enabled", False):
                            if not callable(request_review):
                                raise ApprovalUnavailable()
                            grant = await request_review(intent, {
                                "operation_id": idempotency_key,
                                "review_context": facts, "proposed_arguments": enforced,
                            })
                        else:
                            grant = await self.approvals.request_pending(intent)
                        guard()
                        if grant is None:
                            if evidence is not None:
                                await self.audit(selected, action_hash, key, "escalate", "approval_pending",
                                                 evidence_fingerprint=evidence_digest)
                            return {
                                "status": "pending_approval", "operation_id": idempotency_key,
                                "approval_intent": intent.model_dump(mode="json"),
                                "review_context": facts, "proposed_arguments": enforced,
                            }
                    else:
                        grant = await self.approvals.resolve(intent)
                    guard()
                    if evidence is not None and digest(current_safe()) != safe_hash:
                        raise GateError("approval_context_changed", "blocked")
                    if (grant.intent != intent or grant.approver_tenant != identity.tenant
                            or grant.approver_role not in selected.approval_roles
                            or grant.approver == self.approval_principal
                            or not await self.approvals.verify(grant, intent=intent)):
                        raise GateError("approval_unavailable")
                    guard()
                    if not grant.approved:
                        await self.audit(selected, action_hash, key, "deny", "approval_denied",
                                         evidence_fingerprint=evidence_digest)
                        if deferred:
                            await self.store.replace(scope, key, {**existing, "state": "rejected"}, existing_etag)
                        return {"status": "blocked", "reason_code": "approval_denied"}
                    approval_expiry = expiry
                guard()
                record = {"state": "pending", "input_hash": input_hash, "action_hash": action_hash,
                          "facts_hash": digest(facts), "receipt_id": None, "outcome_reference": None,
                          **({"request_facts_hash": request_facts_hash,
                              "evidence_fingerprint": evidence_digest} if evidence else {}),
                          **({"request_facts_hash": request_facts_hash} if confirmation_intent else {})}
                try:
                    if deferred or confirmation_intent is not None:
                        await self.store.replace(scope, key, record, existing_etag)
                    else:
                        await self.store.create(scope, key, record)
                except Conflict:
                    raise GateError("outcome_unknown") from None
                if confirmation_intent is not None:
                    # Reserve permanently before consumption: lost consume ACK must
                    # reconcile this operation, not reopen its confirmation.
                    consumed = await self.confirmations.resolve(
                        confirmation_intent, operation="consume", approval_grant=grant)
                    check_confirmation_authority(consumed)
                    guard()
                guard()
                receipt_id = await self.audit(selected, action_hash, key,
                    "transform" if decision == "transform" else "allow", "execution_authorized",
                    probe, evidence_digest)
                if probe:
                    await self.probes.intercept(probe, decision="allow", receipt_id=receipt_id)
                guard()
                # Persist audit linkage before the HTTP boundary. Never reopen a pending key.
                current, etag = await self.store.read(scope, key)
                if current != record:
                    raise GateError("outcome_unknown")
                record = {**record, "receipt_id": receipt_id}
                await self.store.replace(scope, key, record, etag)
                guard()
                def effect_guard():
                    guard()
                    check_confirmation_authority()
                    if approval_expiry and datetime.now(timezone.utc) >= approval_expiry:
                        raise GateError("approval_expired")
                async def effect_check():
                    effect_guard()
                    if digest(current_safe()) != safe_hash:
                        raise GateError("approval_context_changed", "blocked")
                    if confirmation_intent is not None:
                        if confirmation_intent.expires_at <= datetime.now(timezone.utc):
                            raise GateError("confirmation_expired", "blocked")
                        validated_confirmation = await self.confirmations.resolve(
                            confirmation_intent, operation="validate", approval_grant=grant)
                        check_confirmation_authority(validated_confirmation)
                    elif requesting_user_context is not None:
                        expiry = await self.confirmations.context(
                            requesting_user_context, facts=facts, operation_id=idempotency_key,
                            provider_profile=selected.confirmation_requirement.provider_profile)
                        if expiry != context_expiry or expiry <= datetime.now(timezone.utc):
                            raise GateError("confirmation_context_changed", "blocked")
                    if selected.confirmation_requirement is not None:
                        fresh_decision, fresh_enforced = await self.policy.evaluate(
                            "pre_tool_call", selected, arguments, current_safe())
                        effect_guard()
                        if (fresh_decision != decision or fresh_enforced != enforced
                                or digest(current_safe()) != safe_hash
                                or confirmation_intent is not None
                                and confirmation_intent.expires_at <= datetime.now(timezone.utc)):
                            raise GateError("confirmation_context_changed", "blocked")
                attempted = True
                reply = await self.downstream.request(
                    action=selected, arguments=enforced, key=key, action_hash=action_hash,
                    provenance=receipt_id, facts=facts,
                    guard=guard if deferred or confirmation_intent is not None else effect_guard,
                    **({"on_dispatch": lambda: self.probes.dispatch(probe)} if probe else {}),
                    **({"effect_check": effect_check} if deferred or evidence is not None
                       or selected.confirmation_requirement is not None else {}))
                guard()
                current, etag = await self.store.read(scope, key)
                if current != record:
                    raise GateError("outcome_unknown")
                await self.store.replace(scope, key, {
                    **record, "state": "completed", "outcome_reference": reply["receipt_id"]}, etag)
                output = await self.output(
                    selected, enforced, facts, reply, guard,
                    evidence_safe=evidence.safe if evidence is not None else None)
                if probe:
                    await self.probes.complete(probe, terminal="completed")
                    output["receipt_id"] = receipt_id
                return output
        except Unauthorized:
            return {"status": "blocked", "reason_code": "authentication_denied"}
        except EvidenceError as error:
            return {"status": "blocked", "reason_code": str(error)}
        except ConfirmationUnavailable as error:
            return {"status": "unavailable", "reason_code": error.reason}
        except GateError as error:
            return {"status": error.status, "reason_code": error.reason,
                    **({"status_code": 409} if error.reason == "idempotency_conflict" else {})}
        except Exception:
            return {"status": "unavailable",
                    "reason_code": "outcome_unknown" if attempted else "gateway_unavailable"}

    async def audit(self, action, action_hash, correlation, decision, reason, probe=None,
                    evidence_fingerprint=None):
        deployment = self.policy.registry.deployment
        return await self.receipts.append(DecisionReceipt(
            receipt_id=uuid.uuid4().hex, correlation_id=correlation, action_id=action.name,
            action_hash=action_hash, policy_digest=self.policy.digest, decision=decision,
            reason_code=reason, agent_version=deployment.agent_version,
            image_digest=deployment.image_digest, recorded_at=datetime.now(timezone.utc), probe=probe,
            evidence_fingerprint=evidence_fingerprint))

    async def output(self, action, arguments, facts, reply, guard, *, evidence_safe=None):
        guard()
        result = reply["result"]
        if action.post_policy_binding:
            safe = self.safe_provider(deepcopy(facts))
            if evidence_safe is not None:
                safe = {**safe, "evidence": deepcopy(evidence_safe)}
            decision, result = await self.policy.evaluate("post_tool_call", action, arguments,
                safe, result)
            if decision not in ("allow", "transform"):
                return {"status": "blocked", "reason_code": "output_denied"}
        try:
            result = validated(result, action.output_schema)
        except GateError:
            raise GateError("output_unavailable") from None
        guard()
        return {"status": "completed", "result": result}
