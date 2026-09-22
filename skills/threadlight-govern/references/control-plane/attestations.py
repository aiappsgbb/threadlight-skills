"""Named evidence verification, distinct from Entra authentication and human consent."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import time
from typing import Annotated, Protocol
from urllib.parse import urlsplit
import uuid

import jwt
from pydantic import Field, model_validator

from .auth import Identity
from .models import Digest, Identifier, KeyId, ObjectId, StrictModel, canonical, parse, strict_json
from .storage import BundleSigner

EVIDENCE_TYPE = "threadlight-evidence+jwt"
EVIDENCE_META = "threadlight/evidence"
EVIDENCE_ARGUMENT = "governance_evidence"
MAX_TOKEN_BYTES = 16384
Revision = Annotated[str, Field(min_length=1, max_length=128)]


class EvidenceError(Exception):
    pass


def fingerprint(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


class EvidenceKey(StrictModel):
    kid: KeyId
    public_key: Annotated[str, Field(min_length=1, max_length=4096)]

    def public(self):
        key = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256).prepare_key(self.public_key)
        if hasattr(key, "private_numbers") or not 2048 <= key.key_size <= 4096:
            raise ValueError("evidence_public_rsa_key_required")
        return key

    @model_validator(mode="after")
    def valid_public_key(self):
        self.public()
        return self


class EvidenceRequirement(StrictModel):
    issuer: Annotated[str, Field(min_length=1, max_length=256)]
    audience: Annotated[str, Field(min_length=1, max_length=256)]
    profile: Identifier
    purpose: Identifier
    keys: Annotated[list[EvidenceKey], Field(min_length=1, max_length=4)]
    subjects: Annotated[dict[Identifier, Identifier], Field(min_length=1, max_length=128)]
    case_field: Identifier
    revision_field: Identifier
    max_age_seconds: Annotated[int, Field(gt=0, le=3600)]

    @model_validator(mode="after")
    def fixed_authority(self):
        url = urlsplit(self.issuer)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.port not in (None, 443) or url.query or url.fragment
                or len({key.kid for key in self.keys}) != len(self.keys)
                or self.case_field == self.revision_field):
            raise ValueError("evidence_configuration_invalid")
        return self


class SourceReference(StrictModel):
    reference: Identifier
    revision: Revision
    digest: Digest


class VerificationResult(StrictModel):
    subject: Identifier
    case_id: Identifier
    revision: Revision
    sources: Annotated[list[SourceReference], Field(min_length=1, max_length=16)]
    claims: Annotated[dict[Identifier, bool | int], Field(min_length=1, max_length=32)]


class Attestation(StrictModel):
    iss: str
    aud: str
    iat: int
    exp: int
    jti: Identifier
    tid: ObjectId
    sub: Identifier
    holder: ObjectId
    holder_client: ObjectId
    case_id: Identifier
    revision: Revision
    action: Identifier
    purpose: Identifier
    profile: Identifier
    arguments_digest: Digest
    sources: Annotated[list[SourceReference], Field(min_length=1, max_length=16)]
    claims: Annotated[dict[Identifier, bool | int], Field(min_length=1, max_length=32)]


@dataclass(frozen=True)
class VerifiedEvidence:
    safe: dict
    fingerprint: str
    expires_at: int
    deadline: float = field(repr=False)

    def fresh(self):
        if time.time() >= self.expires_at or time.monotonic() >= self.deadline:
            raise EvidenceError("evidence_expired")


def verify_attestation(token, requirement, facts, arguments):
    """No URLs/keys from a token; only verified, request-bound claims leave this function."""
    try:
        if not isinstance(token, str) or not 1 <= len(token.encode()) <= MAX_TOKEN_BYTES:
            raise EvidenceError("evidence_required")
        header = jwt.get_unverified_header(token)
        strict_json(jwt.utils.base64url_decode(token.split(".")[0].encode("ascii")))
        if (set(header) != {"alg", "kid", "typ"} or header["alg"] != "RS256"
                or header["typ"] != EVIDENCE_TYPE):
            raise EvidenceError("evidence_invalid")
        key = next((key for key in requirement.keys if key.kid == header["kid"]), None)
        if key is None:
            raise EvidenceError("evidence_invalid")
        decoded = jwt.decode(
            token, key.public(), algorithms=["RS256"], issuer=requirement.issuer,
            audience=requirement.audience, leeway=0,
            options={"require": list(Attestation.model_fields), "strict_aud": True})
        # Reject duplicate JSON fields even if the JWT library accepts the last one.
        wire = jwt.api_jws.PyJWS().decode_complete(token, key.public(), algorithms=["RS256"])
        claims = parse(Attestation, wire["payload"])
        if decoded != strict_json(wire["payload"]):
            raise EvidenceError("evidence_invalid")
        case = arguments[requirement.case_field]
        if (claims.tid != facts["tenant"] or claims.holder != facts["subject"]
                or claims.holder_client != facts["client"] or claims.action != facts["action"]
                or claims.case_id != case or claims.sub != requirement.subjects.get(case)
                or claims.revision != arguments[requirement.revision_field]
                or claims.profile != requirement.profile or claims.purpose != requirement.purpose
                or claims.arguments_digest != fingerprint(arguments)
                or not 0 < claims.exp - claims.iat <= requirement.max_age_seconds):
            raise EvidenceError("evidence_binding_mismatch")
        semantic = claims.model_dump(exclude={"iat", "exp", "jti"})
        result = VerifiedEvidence(semantic, fingerprint(semantic), claims.exp,
                                  time.monotonic() + claims.exp - time.time())
        result.fresh()
        return result
    except jwt.ExpiredSignatureError:
        raise EvidenceError("evidence_expired") from None
    except EvidenceError:
        raise
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise EvidenceError("evidence_invalid") from None


class EvidenceGrant(StrictModel):
    principal: ObjectId
    client: ObjectId
    case_id: Identifier
    subject: Identifier


def evidence_content(requirement, facts, arguments, result: VerificationResult):
    return {
        "iss": requirement.issuer, "aud": requirement.audience,
        "tid": facts["tenant"], "sub": result.subject, "holder": facts["subject"],
        "holder_client": facts["client"], "case_id": result.case_id, "revision": result.revision,
        "action": facts["action"], "purpose": requirement.purpose, "profile": requirement.profile,
        "arguments_digest": fingerprint(arguments),
        "sources": [source.model_dump() for source in result.sources], "claims": result.claims,
    }


class VerificationAdapter(Protocol):
    async def verify(self, identity: Identity, arguments: dict) -> VerificationResult | None: ...


async def sign_attestation(claims, *, signer: BundleSigner, key_id, public_key):
    """Use PyJWT's JWS encoder and the existing async RS256 digest-signing authority.

    The per-encoder algorithm bridge only adapts sync/async I/O. RSA remains in
    KeyVaultSigner (or an explicit test fixture); no global algorithms are changed.
    """
    loop = asyncio.get_running_loop()

    class AuthorityRSA(jwt.algorithms.RSAAlgorithm):
        def prepare_key(self, key):
            if key != key_id:
                raise ValueError("fixed_signer_required")
            return super().prepare_key(public_key)

        def sign(self, msg, key):
            pending = asyncio.run_coroutine_threadsafe(signer.sign(hashlib.sha256(msg).digest()), loop)
            try:
                return pending.result(timeout=5)
            finally:
                if not pending.done():
                    pending.cancel()

    encoder = jwt.api_jws.PyJWS(algorithms=["RS256"])
    encoder.unregister_algorithm("RS256")
    encoder.register_algorithm("RS256", AuthorityRSA(jwt.algorithms.RSAAlgorithm.SHA256))
    return await asyncio.to_thread(
        encoder.encode, canonical(claims), key_id, algorithm="RS256",
        headers={"kid": key_id, "typ": EVIDENCE_TYPE})


class EvidenceProvider:
    def __init__(self, *, requirement: EvidenceRequirement, action: str, tenant: str,
                 signer: BundleSigner, key_id: str, adapter: VerificationAdapter,
                 grants: list[EvidenceGrant]):
        if key_id not in {key.kid for key in requirement.keys} or not grants:
            raise ValueError("evidence_issuer_configuration_required")
        self.requirement, self.action, self.tenant = requirement, action, tenant
        self.signer, self.key_id, self.adapter = signer, key_id, adapter
        self.grants = tuple(grants)

    async def issue(self, identity: Identity, arguments: dict):
        try:
            arguments = strict_json(canonical(arguments))
            if len(canonical(arguments)) > 16384:
                raise EvidenceError("evidence_input_invalid")
            case = arguments.get(self.requirement.case_field)
            subject = self.requirement.subjects.get(case)
            if (identity.workload is None or identity.tenant != self.tenant or subject is None
                    or not any(grant.principal == identity.subject and grant.client == identity.client
                               and grant.case_id == case and grant.subject == subject
                               for grant in self.grants)):
                raise EvidenceError("evidence_scope_denied")
            async with asyncio.timeout(10):
                result = await self.adapter.verify(identity, deepcopy(arguments))
                if result is None:
                    return {"status": "insufficient_evidence", "reason_code": "corroboration_missing",
                            "profile": self.requirement.profile}
                if (not isinstance(result, VerificationResult) or result.subject != subject
                        or result.case_id != case
                        or result.revision != arguments.get(self.requirement.revision_field)):
                    raise EvidenceError("evidence_source_mismatch")
                await self.signer.health()
                now = int(time.time())
                claims = Attestation(
                    iat=now, exp=now + self.requirement.max_age_seconds, jti=uuid.uuid4().hex,
                    **evidence_content(self.requirement, {
                        "tenant": identity.tenant, "subject": identity.subject,
                        "client": identity.client, "action": self.action}, arguments, result))
                public = next(key.public_key for key in self.requirement.keys if key.kid == self.key_id)
                token = await sign_attestation(
                    claims, signer=self.signer, key_id=self.key_id, public_key=public)
                await self.signer.health()
                verify_attestation(token, self.requirement, {
                    "tenant": identity.tenant, "subject": identity.subject,
                    "client": identity.client, "action": self.action}, arguments)
                return {"status": "verified", "attestation": token, "profile": self.requirement.profile}
        except EvidenceError:
            raise
        except Exception:
            raise EvidenceError("evidence_provider_unavailable") from None


def evidence_tool_schema(schema):
    schema = deepcopy(schema)
    if EVIDENCE_ARGUMENT in schema.get("properties", {}):
        raise ValueError("reserved_evidence_argument")
    schema["properties"][EVIDENCE_ARGUMENT] = {
        "type": "string", "minLength": 1, "maxLength": MAX_TOKEN_BYTES,
        "description": "Signed provider attestation; not business data or human consent.",
    }
    return schema
