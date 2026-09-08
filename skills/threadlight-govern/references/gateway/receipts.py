"""Task8 wire-compatible audit and approval clients; no local approval authority."""
from typing import Protocol

from govern_control_plane.client import ApprovalClient, ReceiptClient
from govern_control_plane.models import (
    ApprovalContext, ApprovalGrant, ApprovalRequest, DecisionReceipt,
)


class ReceiptService(Protocol):
    async def health(self) -> bool: ...
    async def append(self, receipt: DecisionReceipt) -> str: ...


class ApprovalService(Protocol):
    async def health(self, *, approval_context: ApprovalContext) -> bool: ...
    async def resolve(self, intent: ApprovalRequest) -> ApprovalGrant: ...
    async def verify(self, grant: ApprovalGrant, *, intent: ApprovalRequest) -> bool: ...


class HTTPControlPlaneApprovalService(ApprovalClient):
    def __init__(self, **kwargs):
        super().__init__(intent_type=ApprovalRequest, grant_type=ApprovalGrant, **kwargs)
