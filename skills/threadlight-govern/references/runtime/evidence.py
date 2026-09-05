"""Payload-free local audit spool and host-authenticated approval seam."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol
import uuid


def digest(value):
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(wire.encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalIntent:
    action_hash: str
    policy_hash: str
    principal: str
    agent_id: str
    session_id: str
    context_identity: str
    nonce: str
    expires_at: datetime


@dataclass(frozen=True)
class ApprovalGrant:
    """Returned ONLY by a trusted authenticated control-plane client."""
    intent: ApprovalIntent
    approved: bool


class ApprovalService(Protocol):
    async def resolve(self, intent: ApprovalIntent) -> ApprovalGrant: ...


class DurableSpool:
    """Write + flush + fsync file AND directory before permitting the effect.

    Export is at-least-once; consumers must deduplicate audit_id. Files contain
    decisions/hashes only. The spool is host-owned, not a model-writable directory.
    """
    def __init__(self, directory):
        self.directory = Path(directory)

    def _write(self, receipt, *, replace=False):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise OSError("audit directory must not be a symlink")
        name = receipt["audit_id"]
        if uuid.UUID(name).hex != name:
            raise ValueError("invalid audit id")
        path = self.directory / f"{name}.json"
        staging = self.directory / f"{uuid.uuid4().hex}.pending"
        try:
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(receipt, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            if not replace and path.exists():
                raise FileExistsError("duplicate audit id")
            os.replace(staging, path)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            staging.unlink(missing_ok=True)

    def append(self, *, correlation_id, decision, action_hash, policy_hash,
               agent_version=None, image_digest=None):
        receipt = {
            "audit_id": uuid.uuid4().hex, "correlation_id": correlation_id,
            "decision": decision, "action_hash": action_hash, "policy_hash": policy_hash,
            "delivery_status": "pending",
        }
        if agent_version is not None:
            receipt["agent_version"] = agent_version
        if image_digest is not None:
            receipt["image_digest"] = image_digest
        self._write(receipt)
        return receipt["audit_id"]

    def retry(self, exporter):
        count = 0
        for path in sorted(self.directory.glob("*.json")):
            if path.is_symlink():
                continue
            receipt = json.loads(path.read_text())
            if receipt["delivery_status"] != "pending":
                continue
            try:
                exporter(dict(receipt))
            except Exception:
                continue
            receipt["delivery_status"] = "delivered"
            self._write(receipt, replace=True)
            count += 1
        return count
