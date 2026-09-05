"""Portable Task7 protocol adapter; runtime types are injected, never repo-imported."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel

from .models import (
    ApprovalContext, ApprovalGrant, ApprovalRequest, DecisionReceipt, Identifier, SignedBundle,
    canonical, parse, strict_json,
)


class ApprovalUnavailable(Exception):
    def __init__(self):
        super().__init__("approval_unavailable")


def wire_intent(intent):
    if isinstance(intent, BaseModel):
        return parse(ApprovalRequest, canonical(intent))
    body = asdict(intent)
    body["allowed_roles"] = list(body["allowed_roles"])
    for field in ("expires_at", "policy_expires_at"):
        body[field] = body[field].isoformat()
    return parse(ApprovalRequest, canonical(body))


def wire_grant(grant):
    if isinstance(grant, BaseModel):
        return parse(ApprovalGrant, canonical(grant))
    body = asdict(grant)
    body["intent"] = wire_intent(grant.intent).model_dump(mode="json")
    return parse(ApprovalGrant, canonical(body))


class ServiceTransport:
    def __init__(self, *, base_url, scope, credential, http=None, timeout=5.0):
        self.validate_configuration(base_url, scope, timeout)
        self.url = base_url.rstrip("/")
        self.scope, self.credential = scope, credential
        self.timeout = timeout
        self.owned_http = http is None
        self.http = http or httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)

    @staticmethod
    def validate_configuration(base_url, scope, timeout):
        url = urlsplit(base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")
                or not scope.endswith("/.default")
                or not 0 < timeout <= 30):
            raise ValueError("invalid_client_configuration")

    async def health(self, *, approval_context: ApprovalContext | dict | None = None) -> bool:
        """Authenticated, read-only readiness; never create or consume governance records."""
        try:
            self.validate_configuration(self.url, self.scope, self.timeout)
            async with asyncio.timeout(self.timeout):
                if not callable(self.credential.get_token):
                    return False
                path = "/health"
                expected = {"status", "authenticated"}
                if approval_context is not None:
                    context = parse(ApprovalContext, canonical(approval_context))
                    path += "?" + urlencode({"approval_context": canonical(context).decode()})
                    expected.add("approval_context_validated")
                status, result = await self.request("GET", path)
                return (status == 200 and set(result) == expected
                        and result["status"] == "healthy" and result["authenticated"] is True
                        and (approval_context is None or result["approval_context_validated"] is True))
        except Exception:
            return False

    async def aclose(self):
        if self.owned_http:
            await self.http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def request(self, method, path, body=None):
        token = await self.credential.get_token(self.scope)
        async with self.http.stream(method, self.url + path,
                content=canonical(body) if body is not None else None,
                headers={"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"},
                follow_redirects=False) as response:
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 16384:
                    raise ApprovalUnavailable()
            if response.status_code not in (200, 202):
                raise ApprovalUnavailable()
            return response.status_code, strict_json(bytes(raw))


class ReceiptClient(ServiceTransport):
    async def append(self, receipt):
        async with asyncio.timeout(self.timeout):
            receipt = parse(DecisionReceipt, canonical(receipt))
            status, response = await self.request("POST", "/receipts", receipt.model_dump(mode="json"))
            if status != 200 or response != {"receipt_id": receipt.receipt_id}:
                raise RuntimeError("audit_unavailable")
            return receipt.receipt_id


class ApprovalClient(ServiceTransport):
    def __init__(self, *, intent_type, grant_type, poll_interval=0.25, **kwargs):
        super().__init__(**kwargs)
        if not 0 < poll_interval <= self.timeout:
            raise ValueError("invalid_client_configuration")
        self.intent_type, self.grant_type = intent_type, grant_type
        self.poll_interval = poll_interval

    async def post(self, body):
        return await self.request("POST", "/approvals/resolve", body)

    def fresh(self, intent):
        now = datetime.now(timezone.utc)
        if intent.expires_at <= now or intent.policy_expires_at <= now:
            raise ApprovalUnavailable()

    def native_grant(self, grant):
        body = grant.intent.model_dump()
        native_intent = self.intent_type(**body)
        return self.grant_type(
            intent=native_intent, approved=grant.approved, approver=grant.approver,
            approver_tenant=grant.approver_tenant, approver_role=grant.approver_role,
            provenance=grant.provenance)

    async def resolve(self, intent):
        try:
            async with asyncio.timeout(self.timeout):
                expected = wire_intent(intent)
                self.fresh(expected)
                operation = "request"
                while True:
                    status, result = await self.post(
                        {"operation": operation, "intent": expected.model_dump(mode="json")})
                    self.fresh(expected)
                    if status == 200:
                        if set(result) != {"grant"}:
                            raise ApprovalUnavailable()
                        grant = parse(ApprovalGrant, canonical(result["grant"]))
                        if grant.intent != expected:
                            raise ApprovalUnavailable()
                        return self.native_grant(grant)
                    if result != {"status": "pending"}:
                        raise ApprovalUnavailable()
                    await asyncio.sleep(self.poll_interval)
                    operation = "resolve"
        except Exception:
            raise ApprovalUnavailable() from None

    async def verify(self, grant, *, intent):
        try:
            async with asyncio.timeout(self.timeout):
                expected, presented = wire_intent(intent), wire_grant(grant)
                self.fresh(expected)
                if presented.intent != expected:
                    return False
                status, result = await self.post({
                    "operation": "consume", "intent": expected.model_dump(mode="json"),
                    "grant": presented.model_dump(mode="json"),
                })
                self.fresh(expected)
                return (status == 200 and set(result) == {"consumed", "grant"}
                        and result["consumed"] is True
                        and parse(ApprovalGrant, canonical(result["grant"])) == presented)
        except Exception:
            return False


class PolicySnapshot:
    """Host-owned synchronous Task7 verifier populated by a verified HTTPS lookup."""
    def __init__(self, trust):
        self.trust = trust

    def now(self):
        return datetime.now(timezone.utc)

    def verify(self, bundle):
        if bundle.bundle_digest != self.trust.digest or self.trust.expires_at <= self.now():
            raise ValueError("policy_unavailable")
        return self.trust


class PolicyClient(ServiceTransport):
    def __init__(self, *, tenant, key_id, verified_policy_type, **kwargs):
        super().__init__(**kwargs)
        self.tenant, self.key_id = tenant, key_id
        self.verified_policy_type = verified_policy_type

    async def load(self, policy_id, version, *, expected_digest):
        try:
            async with asyncio.timeout(self.timeout):
                parse(Identifier, canonical(policy_id))
                parse(Identifier, canonical(version))
                status, result = await self.request("GET", f"/bundles/{policy_id}/{version}")
                signed = parse(SignedBundle, canonical(result))
                envelope = signed.envelope
                if (status != 200 or envelope.tenant_id != self.tenant
                        or envelope.key_id != self.key_id
                        or envelope.policy_id != policy_id or envelope.version != version
                        or envelope.content_digest != expected_digest
                        or envelope.expires_at <= datetime.now(timezone.utc)):
                    raise ApprovalUnavailable()
                return PolicySnapshot(self.verified_policy_type(
                    envelope.content_digest, envelope.expires_at))
        except Exception:
            raise ValueError("policy_unavailable") from None
