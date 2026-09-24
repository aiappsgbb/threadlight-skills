"""Gateway-only service protocol; no polling, provider tokens or human impersonation."""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlencode

from .client import ServiceTransport
from .confirmation import ConfirmationIntent, ConfirmationUnavailable, digest
from .models import Timestamp, canonical, parse


class ConfirmationClient(ServiceTransport):
    async def context(self, ref, *, facts, operation_id, provider_profile):
        try:
            async with asyncio.timeout(self.timeout):
                _, result = await self.request("POST", "/confirmation/context", {
                    "context_ref": ref, "operation_id": operation_id, "provider_profile": provider_profile,
                    "workload": facts["subject"], "client": facts["client"],
                    "agent_id": facts["deployment"]["agent_id"], "action": facts["action"]})
            if set(result) != {"context_ref", "expires_at"} or result["context_ref"] != ref:
                raise ConfirmationUnavailable()
            return parse(Timestamp, canonical(result["expires_at"]))
        except Exception:
            raise ConfirmationUnavailable() from None

    async def health(self, *, provider_profile):
        try:
            async with asyncio.timeout(self.timeout):
                _, result = await self.request("GET", "/confirmation/health?" + urlencode(
                    {"provider_profile": provider_profile}))
                return result == {"status": "healthy", "confirmation_ready": True,
                                  "provider_profile": provider_profile}
        except Exception:
            return False

    async def resolve(self, intent, *, facts=None, arguments=None, operation="request", approval_grant=None):
        expected = parse(ConfirmationIntent, canonical(intent))
        body = {"operation": operation, "intent": expected.model_dump(mode="json")}
        if facts is not None:
            body.update(facts=facts, arguments=arguments)
        if approval_grant is not None:
            body["approval_grant"] = approval_grant.model_dump(mode="json")
        try:
            async with asyncio.timeout(self.timeout):
                _, result = await self.request("POST", "/confirmation/resolve", body)
            if result == {"status": "pending_confirmation", "confirmation_id": expected.confirmation_id,
                          "operation_id": expected.operation_id} and operation in ("request", "resolve"):
                return result
            states = ("confirmed", "rejected") if operation in ("request", "resolve") else ("consumed",)
            if (set(result) != {"status", "confirmation_id", "intent_digest", "effective_authority_expires_at"}
                    or result["status"] not in states or result["confirmation_id"] != expected.confirmation_id
                    or result["intent_digest"] != digest(expected)):
                raise ConfirmationUnavailable()
            expiry = parse(Timestamp, canonical(result["effective_authority_expires_at"]))
            if not datetime.now(timezone.utc) < expiry <= min(expected.expires_at, expected.policy_expires_at):
                raise ConfirmationUnavailable()
            return result
        except Exception:
            raise ConfirmationUnavailable() from None
