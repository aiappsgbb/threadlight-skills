"""Ephemeral, requesting-user consent. Independent reviewer grants are never consent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit
import uuid

from azure.core import MatchConditions
import jwt
from pydantic import Field, StringConstraints, model_validator

from .auth import Unauthorized
from .models import (
    ApprovalGrant, Digest, Identifier, Nonce, ObjectId, StrictModel, Timestamp,
    canonical, parse, strict_json,
)
from .storage import AzureStore, Conflict, Missing, sdk_call

CONTEXT_ARGUMENT = "governance_request_context"
CONTEXT_META = CONTEXT_ARGUMENT
Subject = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"^[^\s\x00-\x1f\x7f]+$")]


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def https(value):
    url = urlsplit(value)
    if (len(value) > 512 or not value.isascii() or any(ord(c) <= 32 for c in value)
            or url.scheme != "https" or not url.hostname or url.port not in (None, 443)
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("invalid_confirmation_endpoint")
    return value


class ConfirmationUnavailable(RuntimeError):
    def __init__(self, reason="confirmation_unavailable"):
        self.reason = reason
        super().__init__(reason)


class ConfirmationRequirement(StrictModel):
    trigger: Literal["always", "policy"]
    provider_profile: Identifier
    max_age_seconds: Annotated[int, Field(gt=0, le=3600)]


class RequestingUser(StrictModel):
    issuer: str
    subject: Subject
    client: Subject
    expires_at: Timestamp
    auth_contexts: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def valid_issuer(self):
        https(self.issuer)
        return self

    def fresh(self):
        if self.expires_at <= datetime.now(timezone.utc):
            raise Unauthorized()


class UserBinding(StrictModel):
    issuer: str
    subject: Subject
    client: Subject
    delivery_ref: Identifier
    # Explicit alias for cross-issuer independent-review separation, never inferred.
    reviewer_tenant: ObjectId | None = None
    reviewer_subject: ObjectId | None = None

    @model_validator(mode="after")
    def valid(self):
        https(self.issuer)
        if (self.reviewer_tenant is None) != (self.reviewer_subject is None):
            raise ValueError("reviewer_alias_pair_required")
        return self


class WorkloadBinding(StrictModel):
    workload: ObjectId
    client: ObjectId
    agent_id: Identifier
    actions: Annotated[list[Identifier], Field(min_length=1, max_length=64)]


class CustomerIdentityConfiguration(StrictModel):
    issuer: str
    audience: Subject
    client_id: Subject
    scope: Identifier
    public_key: Annotated[str, Field(min_length=64, max_length=8192)]
    key_id: Identifier

    @model_validator(mode="after")
    def valid(self):
        https(self.issuer)
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
        key = load_pem_public_key(self.public_key.encode())
        if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
            raise ValueError("rsa_public_key_required")
        return self


class ProviderProfile(StrictModel):
    kind: Literal["email-basic", "entra-ca", "customer-signed"]
    users: Annotated[list[UserBinding], Field(min_length=1, max_length=256)]
    workloads: Annotated[list[WorkloadBinding], Field(min_length=1, max_length=128)]
    notification_url: str
    notification_scope: Annotated[str, Field(pattern=r"^api://[A-Za-z0-9._/-]+/\.default$")]
    customer_identity: CustomerIdentityConfiguration | None = None
    result_verifier: CustomerIdentityConfiguration | None = None
    authentication_context: Annotated[str, Field(pattern=r"^c[1-9][0-9]?$")] | None = None
    conditional_access_policy_id: ObjectId | None = None
    capability: Literal["authenticated-consent", "ca-mfa"] = "authenticated-consent"

    @model_validator(mode="after")
    def valid(self):
        https(self.notification_url)
        if len({(u.issuer, u.subject, u.client) for u in self.users}) != len(self.users):
            raise ValueError("duplicate_confirmation_user")
        if self.kind == "entra-ca":
            if (self.authentication_context is None or self.conditional_access_policy_id is None
                    or self.capability != "ca-mfa" or self.customer_identity):
                raise ValueError("verified_entra_context_required")
        elif (self.authentication_context is not None or self.conditional_access_policy_id is not None
                or self.capability != "authenticated-consent"):
            raise ValueError("unsupported_confirmation_capability")
        if (self.kind == "customer-signed") != (self.result_verifier is not None):
            raise ValueError("signed_provider_verifier_required")
        return self


class ConfirmationConfiguration(StrictModel):
    cosmos_container: Identifier
    public_url: str
    gateway_principals: Annotated[list[ObjectId], Field(min_length=1, max_length=128)]
    profiles: Annotated[dict[Identifier, ProviderProfile], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def valid(self):
        https(self.public_url)
        if urlsplit(self.public_url).path not in ("", "/"):
            raise ValueError("confirmation_origin_required")
        return self


class ContextRequest(StrictModel):
    provider_profile: Identifier
    workload: ObjectId
    client: ObjectId
    agent_id: Identifier
    action: Identifier
    operation_id: Identifier
    expires_at: Timestamp


class ContextLookup(StrictModel):
    context_ref: Nonce
    operation_id: Identifier
    provider_profile: Identifier
    workload: ObjectId
    client: ObjectId
    agent_id: Identifier
    action: Identifier


class ConfirmationIntent(StrictModel):
    confirmation_id: Nonce
    context_ref: Nonce
    operation_id: Identifier
    principal: ObjectId
    tenant: ObjectId
    agent_id: Identifier
    action: Identifier
    action_hash: Digest
    facts_hash: Digest
    safe_hash: Digest
    policy_hash: Digest
    policy_expires_at: Timestamp
    expires_at: Timestamp
    requirement: ConfirmationRequirement
    reviewer_required: bool = False


class ConfirmationDecision(StrictModel):
    intent_digest: Digest
    approved: bool
    provider_result: Annotated[str, Field(min_length=1, max_length=12000)] | None = None


class ConfirmationOperation(StrictModel):
    operation: Literal["request", "resolve", "consume", "validate"]
    intent: ConfirmationIntent
    facts: dict | None = None
    arguments: dict | None = None
    approval_grant: ApprovalGrant | None = None


class ConfirmationProvider(Protocol):
    async def verify(self, profile, user, intent, decision): ...
    async def recheck(self, profile, user, authority): ...


class CustomerIdentityAdapter:
    """Host-pinned issuer/audience/key: no forwarded claims or discovery from tokens."""
    def __init__(self, config):
        self.config = config

    def claims(self, token):
        try:
            header = jwt.get_unverified_header(token)
            if (set(header) - {"typ", "alg", "kid"} or header.get("alg") != "RS256"
                    or header.get("kid") != self.config.key_id):
                raise Unauthorized()
            claims = jwt.decode(token, self.config.public_key, algorithms=["RS256"],
                issuer=self.config.issuer, audience=self.config.audience,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nbf", "azp", "scp"],
                         "strict_aud": True})
            if (any(type(claims[k]) is not int for k in ("exp", "iat", "nbf"))
                    or claims.get("idtyp") == "app" or claims["azp"] != self.config.client_id
                    or not isinstance(claims["scp"], str)
                    or self.config.scope not in claims["scp"].split()):
                raise Unauthorized()
            return claims
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise Unauthorized() from None

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 16384:
            raise Unauthorized()
        claims = self.claims(authorization[7:])
        return RequestingUser(issuer=claims["iss"], subject=claims["sub"], client=claims["azp"],
                              expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc))


class ExplicitConsentProvider:
    """Only called after user authentication and a transaction-specific POST."""
    def __init__(self, ca=None):
        self.ca = ca

    async def verify(self, profile, user, intent, decision):
        user.fresh()
        if profile.kind == "customer-signed":
            if decision.provider_result is None:
                raise Unauthorized()
            claims = CustomerIdentityAdapter(profile.result_verifier).claims(decision.provider_result)
            if (claims.get("requester_issuer") != user.issuer or claims["sub"] != user.subject
                    or claims.get("intent_digest") != digest(intent)
                    or claims.get("confirmation_id") != intent.confirmation_id
                    or type(claims.get("approved")) is not bool
                    or claims["approved"] != decision.approved
                    or claims.get("capability") != profile.capability):
                raise Unauthorized()
        elif decision.provider_result is not None:
            raise Unauthorized()
        if profile.kind == "entra-ca":
            if self.ca is None:
                raise ConfirmationUnavailable("confirmation_ca_unavailable")
            await self.ca.verify(profile, user)
        user.fresh()
        return {"kind": profile.kind, "capability": profile.capability,
                "decided_at": datetime.now(timezone.utc).isoformat()}

    async def recheck(self, profile, user, authority):
        user.fresh()
        if authority["kind"] != profile.kind or authority["capability"] != profile.capability:
            raise Conflict()
        if profile.kind == "entra-ca":
            if self.ca is None:
                raise ConfirmationUnavailable("confirmation_ca_unavailable")
            await self.ca.verify(profile, user)
        user.fresh()


class EphemeralConfirmationStore(AzureStore):
    """Dedicated TTL container; permanent approval/receipt containers stay TTL-free."""
    async def health(self):
        await self.write_safety()

    async def write_safety(self):
        properties = await sdk_call(self.documents.read())
        if (properties.get("partitionKey", {}).get("paths") != ["/scope"]
                or type(properties.get("defaultTtl")) is not int
                or not 0 < properties["defaultTtl"] <= 3600 or self.account_reader is None):
            raise RuntimeError("confirmation_ephemeral_container_required")
        account = await sdk_call(self.account_reader())
        if len(account.WritableLocations) != 1:
            raise RuntimeError("multiple_writers_not_supported")

    def item(self, scope, key, body):
        expiry = datetime.fromisoformat(body["expires_at"])
        ttl = math.ceil((expiry - datetime.now(timezone.utc)).total_seconds())
        if not 0 < ttl <= 3600:
            raise Conflict()
        return {"id": key, "scope": scope, "body": body, "ttl": ttl}

    async def create(self, scope, key, body):
        await self.write_safety()
        await sdk_call(self.documents.create_item(body=self.item(scope, key, body)))

    async def replace(self, scope, key, body, etag):
        await self.write_safety()
        await sdk_call(self.documents.replace_item(item=key, body=self.item(scope, key, body),
            etag=etag, match_condition=MatchConditions.IfNotModified))


class ConfirmationService:
    def __init__(self, config, store, *, credential, http, provider=None):
        self.config, self.store = config, store
        self.credential, self.http = credential, http
        self.provider = provider or ExplicitConsentProvider()

    def profile(self, name):
        try:
            return self.config.profiles[name]
        except KeyError:
            raise Unauthorized() from None

    def user_binding(self, profile, user):
        user.fresh()
        found = next((u for u in profile.users
                      if (u.issuer, u.subject, u.client) == (user.issuer, user.subject, user.client)), None)
        if found is None:
            raise Unauthorized()
        return found

    async def authenticate_user(self, control, auth, authorization, profile):
        if profile.customer_identity is not None:
            user = CustomerIdentityAdapter(profile.customer_identity).authenticate(authorization)
        else:
            identity = await auth.authenticate(authorization)
            if (identity.workload is not None or identity.subject not in control.settings.confirmation_subjects
                    or "Governance.Confirm" not in identity.scopes or not identity.expires_at):
                raise Unauthorized()
            user = RequestingUser(issuer=identity.issuer, subject=identity.subject, client=identity.client,
                expires_at=datetime.fromtimestamp(identity.expires_at, timezone.utc),
                auth_contexts=identity.auth_contexts)
        self.user_binding(profile, user)
        return user

    def context_fresh(self, record):
        if datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc):
            raise Conflict()

    async def register(self, control, user, request):
        profile = self.profile(request.provider_profile)
        self.user_binding(profile, user)
        if (request.expires_at <= control.now()
                or request.expires_at > min(user.expires_at, control.now() + timedelta(hours=1))
                or not any((b.workload, b.client, b.agent_id) ==
                    (request.workload, request.client, request.agent_id) and request.action in b.actions
                    for b in profile.workloads)):
            raise Unauthorized()
        workload = control.settings.workloads.get(request.workload)
        if workload is None or (workload.client_id, workload.agent_id) != (request.client, request.agent_id):
            raise Unauthorized()
        ref = uuid.uuid4().hex
        record = {"request": request.model_dump(mode="json"), "user": user.model_dump(mode="json"),
                  "expires_at": request.expires_at.isoformat(), "confirmation_id": None}
        await self.store.create(control.settings.tenant_id, "context:" + ref, record)
        user.fresh()
        self.context_fresh(record)
        return {"context_ref": ref, "operation_id": request.operation_id}

    async def lookup(self, control, identity, lookup):
        if identity.workload is None or identity.subject not in self.config.gateway_principals:
            raise Unauthorized()
        record, _ = await self.store.read(control.settings.tenant_id, "context:" + lookup.context_ref)
        self.context_fresh(record)
        request = parse(ContextRequest, canonical(record["request"]))
        if any(getattr(lookup, key) != getattr(request, key) for key in (
                "operation_id", "provider_profile", "workload", "client", "agent_id", "action")):
            raise Unauthorized()
        self.user_binding(self.profile(request.provider_profile),
                          parse(RequestingUser, canonical(record["user"])))
        return {"context_ref": lookup.context_ref, "expires_at": request.expires_at.isoformat()}

    async def context(self, control, intent):
        record, etag = await self.store.read(intent.tenant, "context:" + intent.context_ref)
        self.context_fresh(record)
        request = parse(ContextRequest, canonical(record["request"]))
        user = parse(RequestingUser, canonical(record["user"]))
        profile = self.profile(request.provider_profile)
        self.user_binding(profile, user)
        if (intent.tenant != control.settings.tenant_id or intent.agent_id != request.agent_id
                or intent.action != request.action or intent.operation_id != request.operation_id
                or intent.requirement.provider_profile != request.provider_profile
                or intent.expires_at > request.expires_at
                or not any((b.workload, b.client, b.agent_id) ==
                    (request.workload, request.client, request.agent_id) and request.action in b.actions
                    for b in profile.workloads)):
            raise Unauthorized()
        return record, etag, request, user, profile

    async def fresh(self, control, intent):
        now = control.now()
        if (intent.expires_at <= now or intent.expires_at > intent.policy_expires_at
                or intent.expires_at > now + timedelta(seconds=intent.requirement.max_age_seconds)):
            raise Conflict()
        # Unlike reviewer limits, confirmation lifetime is independently bounded.
        raw = await control.store.blob_read(control.digest_name(intent.policy_hash))
        signed = await control.authenticate_bundle(raw)
        if (signed.envelope.content_digest != intent.policy_hash
                or signed.envelope.expires_at != intent.policy_expires_at
                or raw != await control.store.blob_read(control.policy_name(
                    signed.envelope.policy_id, signed.envelope.version))):
            raise Conflict()
        if intent.expires_at <= control.now():
            raise Conflict()
        return signed.envelope

    async def health(self, control, identity, profile_name):
        if identity.workload is None or identity.subject not in self.config.gateway_principals:
            raise Unauthorized()
        self.profile(profile_name)
        await self.store.health()
        return {"status": "healthy", "confirmation_ready": True, "provider_profile": profile_name}

    async def notify(self, control, intent, record, etag, profile, user):
        if record["notification"] != "prepared":
            if record["notification"] != "sent":
                raise ConfirmationUnavailable("confirmation_notification_unknown")
            return
        sending = {**record, "notification": "sending"}
        await self.store.replace(intent.tenant, "confirmation:" + intent.confirmation_id, sending, etag)
        # A lost ACK remains sending; never retry a possibly delivered message.
        token = await self.credential.get_token(profile.notification_scope)
        await self.fresh(control, intent)
        self.user_binding(profile, user)
        if token.expires_on <= control.now().timestamp():
            raise Unauthorized()
        response = await self.http.post(profile.notification_url, headers={
            "Authorization": "Bearer " + token.token,
            "Idempotency-Key": intent.confirmation_id,
        }, json={"confirmation_id": intent.confirmation_id,
                 "confirmation_url": self.config.public_url.rstrip("/") + "/confirmation/" + intent.confirmation_id,
                 "delivery_ref": self.user_binding(profile, user).delivery_ref,
                 "expires_at": intent.expires_at.isoformat()}, follow_redirects=False)
        if (response.status_code != 200 or len(response.content) > 1024
                or strict_json(response.content) != {"notification_id": intent.confirmation_id}):
            raise ConfirmationUnavailable("confirmation_notification_unknown")
        await self.fresh(control, intent)
        current, etag = await self.store.read(intent.tenant, "confirmation:" + intent.confirmation_id)
        if current != sending:
            raise Conflict()
        await self.store.replace(intent.tenant, "confirmation:" + intent.confirmation_id,
                                 {**sending, "notification": "sent"}, etag)

    async def resolve(self, control, identity, operation):
        intent = operation.intent
        if (identity.workload is None or identity.subject not in self.config.gateway_principals
                or identity.subject != intent.principal or identity.tenant != intent.tenant):
            raise Unauthorized()
        policy = await self.fresh(control, intent)
        if policy.policy_id not in identity.workload.policies:
            raise Unauthorized()
        context, context_etag, request, user, profile = await self.context(control, intent)
        if context["confirmation_id"] not in (None, intent.confirmation_id):
            raise Conflict()
        key = "confirmation:" + intent.confirmation_id
        try:
            record, etag = await self.store.read(intent.tenant, key)
        except Missing:
            if operation.operation != "request" or operation.facts is None or operation.arguments is None:
                raise Conflict() from None
            facts = operation.facts
            if (facts.get("tenant") != intent.tenant or facts.get("subject") != request.workload
                    or facts.get("client") != request.client or facts.get("action") != request.action
                    or facts.get("deployment", {}).get("agent_id") != request.agent_id
                    or facts.get("policy") != intent.policy_hash or digest(facts) != intent.facts_hash
                    or digest({"facts": facts, "arguments": operation.arguments}) != intent.action_hash
                    or len(canonical(operation)) > 16384):
                raise Conflict()
            if context["confirmation_id"] is None:
                await self.store.replace(intent.tenant, "context:" + intent.context_ref,
                    {**context, "confirmation_id": intent.confirmation_id}, context_etag)
            record = {"intent": intent.model_dump(mode="json"), "user": user.model_dump(mode="json"),
                      "facts": facts, "arguments": operation.arguments, "expires_at": intent.expires_at.isoformat(),
                      "notification": "prepared", "state": "pending", "authority": None}
            await self.store.create(intent.tenant, key, record)
            record, etag = await self.store.read(intent.tenant, key)
        if record["intent"] != intent.model_dump(mode="json"):
            raise Conflict()
        if operation.operation in ("request", "resolve"):
            if record["state"] == "consumed":
                raise Conflict()
            await self.notify(control, intent, record, etag, profile, user)
            await self.fresh(control, intent)
            if record["state"] == "pending":
                return {"status": "pending_confirmation", "confirmation_id": intent.confirmation_id,
                        "operation_id": intent.operation_id}
            await self.provider.recheck(profile, parse(RequestingUser, canonical(record["user"])), record["authority"])
            return {"status": "confirmed" if record["approved"] else "rejected",
                    "confirmation_id": intent.confirmation_id, "intent_digest": digest(intent)}
        expected_state = "consumed" if operation.operation == "validate" else "decided"
        if record["state"] != expected_state or not record.get("approved"):
            raise Conflict()
        await self.provider.recheck(profile, parse(RequestingUser, canonical(record["user"])), record["authority"])
        if intent.reviewer_required and operation.approval_grant is None:
            raise Unauthorized()
        if operation.approval_grant is not None:
            grant = operation.approval_grant
            binding = self.user_binding(profile, user)
            same_entra = user.issuer == control.settings.issuer and grant.approver == user.subject
            alias = (grant.approver_tenant, grant.approver) == (
                binding.reviewer_tenant, binding.reviewer_subject)
            if user.issuer != control.settings.issuer and binding.reviewer_subject is None:
                raise Unauthorized()
            if (same_entra or alias or not grant.approved or grant.intent.action_hash != intent.action_hash
                    or grant.intent.policy_hash != intent.policy_hash
                    or grant.intent.context_identity != intent.facts_hash):
                raise Unauthorized()
            # A caller-supplied grant is never independent-review authority.
            review, _ = await control.store.read(intent.tenant, "approval:" + grant.intent.nonce)
            if review["state"] != "consumed" or review["grant"] != grant.model_dump(mode="json"):
                raise Unauthorized()
        await self.fresh(control, intent)
        if operation.operation == "consume":
            await self.store.replace(intent.tenant, key, {**record, "state": "consumed"}, etag)
        await self.fresh(control, intent)
        return {"status": expected_state if operation.operation == "validate" else "consumed",
                "confirmation_id": intent.confirmation_id, "intent_digest": digest(intent)}

    async def read_for_user(self, control, confirmation_id, auth, authorization):
        record, etag = await self.store.read(control.settings.tenant_id, "confirmation:" + confirmation_id)
        intent = parse(ConfirmationIntent, canonical(record["intent"]))
        profile = self.profile(intent.requirement.provider_profile)
        user = await self.authenticate_user(control, auth, authorization, profile)
        original = parse(RequestingUser, canonical(record["user"]))
        if (user.issuer, user.subject) != (original.issuer, original.subject):
            raise Unauthorized()
        await self.fresh(control, intent)
        await self.context(control, intent)
        if (digest(record["facts"]) != intent.facts_hash
                or digest({"facts": record["facts"], "arguments": record["arguments"]}) != intent.action_hash):
            raise Conflict()
        return record, etag, intent, profile, user

    async def view(self, control, confirmation_id, auth, authorization):
        record, _, intent, _, _ = await self.read_for_user(control, confirmation_id, auth, authorization)
        return {"intent": intent.model_dump(mode="json"), "intent_digest": digest(intent),
                "facts": record["facts"], "arguments": record["arguments"], "state": record["state"]}

    async def decide(self, control, confirmation_id, auth, authorization, decision):
        record, etag, intent, profile, user = await self.read_for_user(control, confirmation_id, auth, authorization)
        if (record["state"] != "pending" or record["notification"] != "sent"
                or decision.intent_digest != digest(intent)):
            raise Conflict()
        authority = await self.provider.verify(profile, user, intent, decision)
        await self.fresh(control, intent)
        user.fresh()
        await self.store.replace(intent.tenant, "confirmation:" + confirmation_id,
            {**record, "state": "decided", "approved": decision.approved,
             "user": user.model_dump(mode="json"), "authority": authority}, etag)
        await self.fresh(control, intent)
        return {"confirmation_id": confirmation_id, "status": "confirmed" if decision.approved else "rejected"}
