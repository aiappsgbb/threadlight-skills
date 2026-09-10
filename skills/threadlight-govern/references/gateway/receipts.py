"""Task8 wire-compatible audit and approval clients; no local approval authority."""
from typing import Protocol

import asyncio

from govern_control_plane.client import ApprovalClient, ApprovalUnavailable, ReceiptClient
from govern_control_plane.models import (
    ApprovalContext, ApprovalGrant, ApprovalRequest, DecisionReceipt, canonical, parse,
)


class ReceiptService(Protocol):
    async def health(self) -> bool: ...
    async def append(self, receipt: DecisionReceipt) -> str: ...


class ApprovalService(Protocol):
    async def health(self, *, approval_context: ApprovalContext) -> bool: ...
    async def resolve(self, intent: ApprovalRequest) -> ApprovalGrant: ...
    async def verify(self, grant: ApprovalGrant, *, intent: ApprovalRequest) -> bool: ...
    async def request_pending(self, intent: ApprovalRequest) -> ApprovalGrant | None: ...


class HTTPControlPlaneApprovalService(ApprovalClient):
    def __init__(self, **kwargs):
        super().__init__(intent_type=ApprovalRequest, grant_type=ApprovalGrant, **kwargs)

    async def request_pending(self, intent: ApprovalRequest) -> ApprovalGrant | None:
        """One request/resolve exchange; no human wait and no inferred consent."""
        async with asyncio.timeout(self.timeout):
            self.fresh(intent)
            status, body = await self.post(
                {"operation": "request", "intent": intent.model_dump(mode="json")})
            self.fresh(intent)
            if status == 202 and body == {"status": "pending"}:
                return None
            if status != 200 or set(body) != {"grant"}:
                raise ApprovalUnavailable()
            grant = parse(ApprovalGrant, canonical(body["grant"]))
            if grant.intent != intent:
                raise ApprovalUnavailable()
            return grant
