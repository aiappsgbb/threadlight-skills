"""Task8 wire-compatible audit and approval clients; no local approval authority."""
import asyncio
from typing import Protocol

from govern_control_plane.client import ApprovalClient, ServiceTransport
from govern_control_plane.models import ApprovalGrant, ApprovalRequest, DecisionReceipt, canonical, parse


class ReceiptService(Protocol):
    async def health(self) -> bool: ...
    async def append(self, receipt: DecisionReceipt) -> str: ...


class ApprovalService(Protocol):
    async def health(self) -> bool: ...
    async def resolve(self, intent: ApprovalRequest) -> ApprovalGrant: ...
    async def verify(self, grant: ApprovalGrant, *, intent: ApprovalRequest) -> bool: ...


class ReceiptClient(ServiceTransport):
    async def append(self, receipt):
        async with asyncio.timeout(self.timeout):
            receipt = parse(DecisionReceipt, canonical(receipt))
            status, response = await self.request("POST", "/receipts", receipt.model_dump(mode="json"))
            if status != 200 or response != {"receipt_id": receipt.receipt_id}:
                raise RuntimeError("audit_unavailable")
            return receipt.receipt_id


class HTTPControlPlaneApprovalService(ApprovalClient):
    def __init__(self, **kwargs):
        super().__init__(intent_type=ApprovalRequest, grant_type=ApprovalGrant, **kwargs)
