"""Task8 wire-compatible audit and approval clients; no local approval authority."""
from typing import Protocol

import asyncio

from govern_control_plane.client import ApprovalClient, ApprovalUnavailable, ReceiptClient
from govern_control_plane.models import (
    ApprovalContext, ApprovalGrant, ApprovalRequest, DecisionReceipt, ReviewMetadata, canonical, parse,
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
    def __init__(self, *, review_enabled: bool = False, **kwargs):
        if type(review_enabled) is not bool:
            raise ValueError("invalid_review_configuration")
        super().__init__(intent_type=ApprovalRequest, grant_type=ApprovalGrant, **kwargs)
        self.review_enabled = review_enabled

    async def health(self, *, approval_context=None) -> bool:
        return await super().health(
            approval_context=approval_context, require_review_metadata=self.review_enabled)

    async def request_pending(self, intent: ApprovalRequest) -> ApprovalGrant | None:
        return await self._request_pending(intent)

    async def request_review(self, intent: ApprovalRequest, review: dict) -> ApprovalGrant | None:
        return await self._request_pending(intent, parse(ReviewMetadata, canonical(review)))

    async def _request_pending(self, intent: ApprovalRequest, review=None) -> ApprovalGrant | None:
        """One request/resolve exchange; no human wait and no inferred consent."""
        async with asyncio.timeout(self.timeout):
            self.fresh(intent)
            status, body = await self.post(
                {"operation": "request", "intent": intent.model_dump(mode="json"),
                 **({"review": review.model_dump(mode="json")} if review is not None else {})})
            self.fresh(intent)
            if status == 202 and body == {"status": "pending"}:
                return None
            if status != 200 or set(body) != {"grant"}:
                raise ApprovalUnavailable()
            grant = parse(ApprovalGrant, canonical(body["grant"]))
            if grant.intent != intent:
                raise ApprovalUnavailable()
            return grant
