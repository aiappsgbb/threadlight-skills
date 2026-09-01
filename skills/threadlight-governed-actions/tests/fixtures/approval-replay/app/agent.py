"""Synthetic service-side approval nonce store fixture (Task 6).

Models the one piece of "approval anti-replay" that must genuinely be
service-side and stateful: atomic, one-time nonce redemption. Every
other check — canonical-binding comparison against the redeemed
digest, and expiry — is performed by ``probes.run_approval_probe``
itself, never invented here: this fixture is only the synthetic
backend a real approval service would delegate that one atomic
primitive to.

``redeem`` never decides replay-vs-mismatch on its own — that binding
comparison is ``run_approval_probe``'s job — but it *does* directly
and durably record, per attempt, whether this specific redemption was
accepted: it appends exactly one
``{"nonce": ..., "digest": ..., "accepted": bool}`` record every
single time it is called, under an exclusive OS file lock spanning
the whole read-check-append critical section, atomically granting
``accepted: true`` to the first-ever attempt for a given nonce and
``accepted: false`` to every attempt after that nonce already has an
accepted record — regardless of how many callers race for the same
nonce concurrently. ``run_approval_probe`` reads this same ledger both
before and after calling ``redeem`` and trusts *only* the newly
appended record's own explicit ``accepted`` field as the directly
observed outcome of this call — never an inference drawn merely from
how the record count changed, which a fail-open store that silently
overwrote a prior record in place (rather than truly appending) could
otherwise fool.

Nothing here ever carries a raw approval argument payload: only the
caller-supplied canonical digest (itself a hash, never the underlying
binding fields) is ever persisted to the ledger or emitted to
``AUDIT_EVENTS``.
"""
from __future__ import annotations

import fcntl
import json
import os


AUDIT_EVENTS = []


def redeem(nonce: str, digest: str, ledger_path: str) -> None:
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
                record.get("nonce") == nonce and record.get("accepted") is True
                for record in existing
            )
            accepted = not already_accepted
            handle.write(
                json.dumps(
                    {"nonce": nonce, "digest": digest, "accepted": accepted}
                )
                + "\n"
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
