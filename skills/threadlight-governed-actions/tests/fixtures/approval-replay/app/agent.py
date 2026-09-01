"""Synthetic service-side approval nonce store fixture (Task 6).

Models the one piece of "approval anti-replay" that must genuinely be
service-side and stateful: atomic, one-time nonce redemption. Every
other check — canonical-binding comparison against the redeemed
digest, and expiry — is performed by ``probes.run_approval_probe``
itself, never invented here: this fixture is only the synthetic
backend a real approval service would delegate that one atomic
primitive to.

``redeem`` never decides accept/replay/mismatch on its own: it only
guarantees the persistent, on-disk nonce ledger holds at *most* one
``{"nonce": ..., "digest": ...}`` record per nonce, appended exactly
once — the first time that nonce is ever seen — under an exclusive OS
file lock spanning the whole read-then-append critical section, so a
second call for an already-recorded nonce (whatever digest it
supplies this time) leaves the ledger completely untouched.
``run_approval_probe`` independently reads that same ledger both
before and after calling ``redeem`` to determine, from the ledger's
own contents rather than this function's return value, exactly what
happened: a first-time acceptance, a byte-identical replay, or a
mismatched (mutated-field) reuse of the same nonce.

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
            already_seen = any(
                json.loads(line).get("nonce") == nonce
                for line in handle.read().splitlines()
                if line.strip()
            )
            if not already_seen:
                handle.write(json.dumps({"nonce": nonce, "digest": digest}) + "\n")
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
