"""Synthetic service-side approval nonce store for the ``conformant-maf``
fixture (Task 5/12).

``governed_actions._assess_pre_deploy`` never wires a real
:class:`probes.ApprovalBinding` on its own -- it has no safe,
non-fabricated source for one, so it always reports ``APR-001``
explicit not-verified in-band (see
``governed_actions._approval_not_verified_finding``). Demonstrating
this fixture's own approval-anti-replay control as genuinely
*passing* therefore requires driving ``probes.run_approval_probe``
directly against this dispatch seam, exactly the same way
``tests/test_probes.py``'s own approval-probe tests do, rather than
going through the orchestrator's CLI wiring at all.

This is the identical atomic, service-side nonce-redemption primitive
``tests/fixtures/approval-replay/app/agent.py`` implements: a first
non-expired attempt for a nonce is durably recorded ``accepted: true``
(and only then followed by a durable ``invocation`` record), and any
later attempt for the same nonce -- replay or mutated-field reuse --
is durably recorded ``accepted: false`` and never invokes the
protected tool, regardless of how many callers race for the same
nonce concurrently (the whole read-check-decide-append critical
section is guarded by an exclusive OS file lock). Nothing here ever
carries a raw approval argument payload: only the caller-supplied
canonical digest is ever persisted to the ledger or emitted to
``AUDIT_EVENTS``.
"""
from __future__ import annotations

import fcntl
import json
import os


AUDIT_EVENTS = []


def redeem(
    nonce: str, digest: str, expires_at: str, now: str, ledger_path: str
) -> None:
    with open(ledger_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = [
                json.loads(line)
                for line in handle.read().splitlines()
                if line.strip()
            ]
            already_accepted = any(
                record.get("event") == "decision"
                and record.get("nonce") == nonce
                and record.get("accepted") is True
                for record in existing
            )
            expired = now >= expires_at
            accepted = (not expired) and (not already_accepted)
            handle.write(
                json.dumps(
                    {
                        "event": "decision",
                        "nonce": nonce,
                        "digest": digest,
                        "accepted": accepted,
                    }
                )
                + "\n"
            )
            handle.flush()
            if accepted:
                # The protected tool is only ever reached once its own
                # decision has been durably recorded as accepted --
                # never before, and never at all for a rejected
                # attempt.
                handle.write(
                    json.dumps({"event": "invocation", "nonce": nonce}) + "\n"
                )
                handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    AUDIT_EVENTS.append(
        {
            "audit_id": f"audit-approval-{nonce}",
            "event": "approval_redemption_attempt",
            "digest_hash": digest,
        }
    )
