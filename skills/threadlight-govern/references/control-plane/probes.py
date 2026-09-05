"""Payload-free, single-use producer state. No public method accepts counters or events."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import (
    Digest, Identifier, Nonce, ObjectId, ProbeContext, ProbeDeployment,
    StrictModel, Timestamp, canonical, parse,
)
from .storage import AzureStore, Conflict, Missing

PROBE_INPUT = {
    "type": "object", "properties": {
        "probe_run_id": {"type": "string", "minLength": 36, "maxLength": 36},
        "variant": {"type": "string", "enum": ["allow", "deny"]}},
    "required": ["probe_run_id", "variant"], "additionalProperties": False,
}
PROBE_OUTPUT = {
    "type": "object", "properties": {"status": {"type": "string", "enum": ["noop"]}},
    "required": ["status"], "additionalProperties": False,
}
PHASES = ("received", "intercepted", "dispatch", "effect", "completed")
Phase = Literal["received", "intercepted", "dispatch", "effect", "completed"]


def hashed(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class ProbeContract(StrictModel):
    protocol: Literal["threadlight-probe/v1"]
    fixture_id: Identifier
    effect: Literal["noop"]


class ProbeOptIn(StrictModel):
    enabled: bool
    configuration_file: Literal["/mnt/governance-probe/config.json"]

    @model_validator(mode="after")
    def explicit(self):
        if self.enabled is not True:
            raise ValueError("explicit_probe_opt_in_required")
        return self


class Registration(StrictModel):
    subject: ObjectId
    action: Literal["governance_probe_noop"]
    variant: Literal["allow", "deny"]
    deployment: ProbeDeployment
    policy_digest: Digest


class Event(StrictModel):
    phase: Phase
    event_id: Nonce
    recorded_at: Timestamp
    receipt_id: Nonce | None = None
    decision: Literal["allow", "deny"] | None = None


class State(StrictModel):
    kind: Literal["threadlight-probe/v1"] = "threadlight-probe/v1"
    producer: Literal["gateway", "native", "fixture"]
    probe_run_id: ObjectId
    tenant: ObjectId
    binding: Identifier
    fixture_id: Identifier
    registration: Registration
    registered_at: Timestamp
    expires_at: Timestamp
    context: ProbeContext | None = None
    counts: dict[Phase, Annotated[int, Field(ge=0, le=1)]]
    events: Annotated[list[Event], Field(max_length=5)]
    terminal: Literal["denied", "completed"] | None = None
    effect_key: Digest | None = None
    action_hash: Digest | None = None
    deployment_provenance: Literal["signed-registry-declaration"] = "signed-registry-declaration"
    observation_provenance: Literal["service-boundary"] = "service-boundary"

    @model_validator(mode="after")
    def consistent_counts(self):
        if set(self.counts) != set(PHASES) or any(
                self.counts[p] != sum(e.phase == p for e in self.events) for p in PHASES):
            raise ValueError("invalid_probe_state")
        if self.terminal is not None and self.counts["completed"] != 1:
            raise ValueError("invalid_probe_terminal")
        if self.context is not None:
            context, expected = self.context, self.registration
            if (context.probe_run_id != self.probe_run_id or context.tenant != self.tenant
                    or context.subject != expected.subject or context.action != expected.action
                    or context.binding != self.binding or context.fixture_id != self.fixture_id
                    or context.policy_digest != expected.policy_digest
                    or context.deployment != expected.deployment):
                raise ValueError("invalid_probe_context")
        if self.producer != "fixture" and bool(self.counts["received"]) != (self.context is not None):
            raise ValueError("invalid_probe_context")
        decisions = [e.decision for e in self.events if e.phase == "intercepted"]
        if self.terminal == "denied" and (
                decisions != ["deny"] or self.counts["dispatch"] or self.counts["effect"]):
            raise ValueError("invalid_probe_terminal")
        if self.terminal == "completed":
            if self.producer == "fixture":
                if self.counts["effect"] != 1 or self.effect_key is None or self.action_hash is None:
                    raise ValueError("invalid_fixture_terminal")
            elif decisions != ["allow"] or self.counts["dispatch"] != 1:
                raise ValueError("invalid_probe_terminal")
        return self


class ProbeStore(AzureStore):
    """Dedicated /scope container, no TTL, one writer region and strong point reads."""
    async def health(self):
        await self.write_safety()

    async def write_safety(self):
        await super().write_safety()
        account = await self.account_reader()
        if account.ConsistencyPolicy.get("defaultConsistencyLevel") != "Strong":
            raise RuntimeError("strong_probe_consistency_required")

    async def read(self, scope, key):
        await self.write_safety()
        return await super().read(scope, key)


class ProbeService:
    def __init__(self, *, store, registry, policy_digest, producer, fresh):
        if producer not in ("gateway", "native", "fixture"):
            raise ValueError("invalid_producer")
        if not any(a.probe_safe for a in registry.actions):
            raise ValueError("probe_not_enabled")
        self.store, self.registry, self.policy_digest = store, registry, policy_digest
        self.producer, self.fresh = producer, fresh

    def location(self, subject, run):
        parse(ObjectId, canonical(run))
        parse(ObjectId, canonical(subject))
        return hashed([self.registry.tenant_id, subject]), "probe:" + run

    def action(self, name, subject):
        self.fresh()
        selected = next((a for a in self.registry.actions if a.name == name and a.probe_safe), None)
        if selected is None or subject not in selected.workloads:
            raise ValueError("probe_scope_denied")
        return selected

    def validate_registration(self, registration):
        selected = self.action(registration.action, registration.subject)
        if (registration.deployment.model_dump() != self.registry.deployment.model_dump()
                or registration.policy_digest != self.policy_digest):
            raise ValueError("probe_deployment_mismatch")
        return selected

    async def register(self, run, expected):
        registration = parse(Registration, canonical(expected))
        action = self.validate_registration(registration)
        scope, key = self.location(registration.subject, run)
        now = datetime.now(timezone.utc)
        state = State(producer=self.producer, probe_run_id=run, registration=registration,
                      tenant=self.registry.tenant_id, binding=action.policy_binding,
                      fixture_id=action.probe_contract.fixture_id,
                      registered_at=now, expires_at=now + timedelta(minutes=10),
                      counts=dict.fromkeys(PHASES, 0), events=[])
        # Never reset or extend an existing nonce, even with identical input.
        await self.store.create(scope, key, state.model_dump(mode="json"))
        return state.model_dump(mode="json")

    async def load(self, subject, run):
        self.fresh()
        scope, key = self.location(subject, run)
        raw, etag = await self.store.read(scope, key)
        try:
            state = parse(State, canonical(raw))
            action = self.validate_registration(state.registration)
            if (state.tenant != self.registry.tenant_id or state.binding != action.policy_binding
                    or state.fixture_id != action.probe_contract.fixture_id
                    or state.probe_run_id != run or state.registration.subject != subject
                    or state.producer != self.producer):
                raise ValueError()
        except ValueError:
            raise RuntimeError("probe_state_unavailable") from None
        return state, etag

    async def status(self, subject, run):
        state, _ = await self.load(subject, run)
        return state.model_dump(mode="json")

    def event(self, state, phase, *, receipt_id=None, decision=None):
        if state.counts[phase]:
            raise Conflict()
        event = Event(phase=phase, event_id=hashed([state.probe_run_id, self.producer, phase])[:32],
                      recorded_at=datetime.now(timezone.utc), receipt_id=receipt_id, decision=decision)
        return {**state.model_dump(mode="json"),
                "counts": {**state.counts, phase: 1},
                "events": [*(e.model_dump(mode="json") for e in state.events), event.model_dump(mode="json")]}

    async def save(self, state, etag, body):
        validated = parse(State, canonical(body))
        scope, key = self.location(state.registration.subject, state.probe_run_id)
        self.fresh()
        await self.store.replace(scope, key, validated.model_dump(mode="json"), etag)
        return validated

    async def begin(self, subject, run, action, variant, *, session_id, call_id):
        state, etag = await self.load(subject, run)
        selected = self.action(action, subject)
        if (state.registration.action != action or state.registration.variant != variant
                or datetime.now(timezone.utc) >= state.expires_at or state.context is not None):
            raise Conflict()
        context = ProbeContext(
            probe_run_id=run, tenant=self.registry.tenant_id, subject=subject,
            action=action, binding=selected.policy_binding, fixture_id=selected.probe_contract.fixture_id,
            policy_digest=self.policy_digest, deployment=state.registration.deployment,
            session_id=session_id, call_id=call_id)
        body = self.event(state, "received")
        body["context"] = context.model_dump(mode="json")
        await self.save(state, etag, body)
        return context

    async def update(self, context, phase, *, decision=None, receipt_id=None, terminal=None, action_hash=None):
        for _ in range(8):
            state, etag = await self.load(context.subject, context.probe_run_id)
            if state.context != context or state.terminal is not None:
                raise Conflict()
            if phase == "intercepted":
                if self.producer == "fixture" or not state.counts["received"] or not receipt_id or not decision:
                    raise ValueError("probe_order")
            elif phase == "dispatch":
                if not state.counts["intercepted"] or state.events[-1].decision != "allow":
                    raise ValueError("probe_order")
            elif phase == "completed":
                if terminal == "denied":
                    if not state.counts["intercepted"] or state.events[-1].decision != "deny" or state.counts["dispatch"]:
                        raise ValueError("probe_order")
                elif terminal != "completed" or not state.counts["dispatch"]:
                    raise ValueError("probe_order")
            else:
                raise ValueError("producer_phase_forbidden")
            body = self.event(state, phase, decision=decision, receipt_id=receipt_id)
            if action_hash is not None:
                if phase != "intercepted" or self.producer != "native":
                    raise ValueError("native_interception_hash_only")
                body["action_hash"] = action_hash
            body["terminal"] = terminal
            try:
                return await self.save(state, etag, body)
            except Conflict:
                continue
        raise RuntimeError("probe_state_unavailable")

    async def intercept(self, context, *, decision, receipt_id, action_hash=None):
        return await self.update(context, "intercepted", decision=decision, receipt_id=receipt_id,
                                 action_hash=action_hash)

    async def dispatch(self, context):
        return await self.update(context, "dispatch")

    async def complete(self, context, *, terminal):
        return await self.update(context, "completed", terminal=terminal)

    async def effect(self, subject, run, *, variant, effect_key, action_hash, receipt_id):
        """The fixture's only effect IS this atomic durable noop acceptance."""
        if self.producer != "fixture":
            raise ValueError("fixture_authority_required")
        for _ in range(8):
            state, etag = await self.load(subject, run)
            if state.registration.variant != variant:
                raise ValueError("probe_variant_mismatch")
            if state.counts["effect"]:
                if state.effect_key != effect_key or state.action_hash != action_hash:
                    raise ValueError("probe_replay_mismatch")
                return state
            if datetime.now(timezone.utc) >= state.expires_at:
                raise ValueError("probe_expired")
            body = state.model_dump(mode="json")
            for phase in ("received", "effect", "completed"):
                body = self.event(parse(State, canonical(body)), phase, receipt_id=receipt_id)
            body.update(terminal="completed", effect_key=effect_key, action_hash=action_hash)
            try:
                return await self.save(state, etag, body)
            except Conflict:
                continue
        raise RuntimeError("probe_state_unavailable")


async def bounded_body(request):
    import asyncio
    raw = bytearray()
    async with asyncio.timeout(5):
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise ValueError("probe_request_too_large")
    return bytes(raw)


async def request_identity(request, auth):
    import asyncio
    from .auth import Unauthorized
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise Unauthorized()
    async with asyncio.timeout(5):
        return await auth.authenticate(values[0])


def authorize_controller(auth, identity, subject, action=None, *, write=False):
    grant = auth.settings.probe_controllers.get(identity.subject)
    required = {"Governance.Probe.Control"} if write else {"Governance.Probe.Read", "Governance.Probe.Control"}
    if (identity.tenant != auth.settings.tenant_id or identity.workload is not None
            or grant is None or identity.client != grant.client_id
            or not identity.roles.intersection(required) or identity.scopes
            or subject not in grant.subjects or (action is not None and action not in grant.actions)):
        raise PermissionError()


def failure(error):
    from starlette.responses import JSONResponse
    from .auth import Unauthorized
    code, status = "UNKNOWN", 503
    for cls, label, value in (
        (Unauthorized, "unauthorized", 401), (PermissionError, "forbidden", 403),
        (Missing, "probe_not_registered", 404), (Conflict, "probe_conflict", 409),
        (ValueError, "invalid_probe_request", 400),
    ):
        if isinstance(error, cls):
            code, status = label, value
            break
    return JSONResponse({"error": code}, status)


def control_app(service, auth):
    import asyncio
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def control(request):
        try:
            async with asyncio.timeout(10):
                identity = await request_identity(request, auth)
                run = request.path_params["run_id"]
                parse(ObjectId, canonical(run))
                if request.method == "POST":
                    if request.query_params:
                        raise ValueError()
                    registration = parse(Registration, await bounded_body(request))
                    authorize_controller(auth, identity, registration.subject, registration.action, write=True)
                    return JSONResponse(await service.register(run, registration.model_dump(mode="json")), 201)
                if set(request.query_params) != {"subject"} or len(request.query_params.getlist("subject")) != 1:
                    raise ValueError()
                subject = parse(ObjectId, canonical(request.query_params["subject"]))
                authorize_controller(auth, identity, subject)
                state = await service.status(subject, run)
                authorize_controller(auth, identity, subject, state["registration"]["action"])
                return JSONResponse(state)
        except Exception as error:
            return failure(error)

    return Starlette(routes=[Route("/governance/probes/{run_id}", control, methods=["POST", "GET"])])
