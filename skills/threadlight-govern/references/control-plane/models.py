"""Bounded, payload-free wire models. Task7 field names are preserved exactly."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints, TypeAdapter,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(
    min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*$")]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ObjectId = Annotated[str, StringConstraints(
    pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
Nonce = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
KeyId = Annotated[str, StringConstraints(
    max_length=256,
    pattern=r"^https://[a-z0-9-]+\.vault\.azure\.net/keys/[A-Za-z0-9-]+/[0-9a-f]{32}$")]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC required")
    return value.astimezone(timezone.utc)


Timestamp = Annotated[datetime, AfterValidator(utc),
                      PlainSerializer(lambda value: value.isoformat(), return_type=str, when_used="json")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ApprovalRequest(StrictModel):
    action_hash: Digest
    policy_hash: Digest
    principal: ObjectId
    agent_id: Identifier
    session_id: Identifier
    context_identity: Identifier
    nonce: Nonce
    expires_at: Timestamp
    tenant: ObjectId
    allowed_roles: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    policy_expires_at: Timestamp

    @model_validator(mode="after")
    def unique_roles(self):
        if len(set(self.allowed_roles)) != len(self.allowed_roles):
            raise ValueError("duplicate role")
        return self


class ApprovalGrant(StrictModel):
    intent: ApprovalRequest
    approved: bool
    approver: ObjectId
    approver_tenant: ObjectId
    approver_role: Identifier
    provenance: Nonce


class RequestOperation(StrictModel):
    operation: Literal["request", "resolve"]
    intent: ApprovalRequest


class DecideOperation(StrictModel):
    operation: Literal["decide"]
    intent: ApprovalRequest
    approved: bool
    approving_role: Identifier


class ConsumeOperation(StrictModel):
    operation: Literal["consume"]
    intent: ApprovalRequest
    grant: ApprovalGrant


ApprovalOperation = Annotated[
    RequestOperation | DecideOperation | ConsumeOperation, Field(discriminator="operation")]


class DecisionReceipt(StrictModel):
    receipt_id: Nonce
    correlation_id: Identifier
    action_id: Identifier
    action_hash: Digest
    policy_digest: Digest
    # "error" preserves Task7's failed-native-operation evidence; never permits an effect.
    decision: Literal["allow", "deny", "escalate", "transform", "error"]
    reason_code: Identifier
    agent_version: Identifier
    image_digest: Digest
    recorded_at: Timestamp


class BundleEnvelope(StrictModel):
    policy_id: Identifier
    version: Identifier
    content_digest: Digest
    expires_at: Timestamp
    tenant_id: ObjectId
    key_id: KeyId


class SignedBundle(StrictModel):
    envelope: BundleEnvelope
    signature: Annotated[str, StringConstraints(
        min_length=1, max_length=1024, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]


def canonical(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def envelope_digest(envelope: BundleEnvelope) -> bytes:
    return hashlib.sha256(canonical(envelope)).digest()


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("nonfinite number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def parse(model, raw: bytes):
    strict_json(raw)
    return TypeAdapter(model).validate_json(raw)
