"""Synthetic service-side approval nonce store fixture (Task 6).

Models the two pieces of "approval anti-replay" that must genuinely be
service-side and stateful: atomic, one-time nonce redemption, *and*
the fact that expiry is a decision this same service-side store makes
for itself — never something ``probes.run_approval_probe`` merely
self-grades in Python before ever asking the target anything. Every
other check — canonical-binding comparison against the redeemed
digest — is still performed by ``run_approval_probe`` itself, never
invented here: this fixture is only the synthetic backend a real
approval service would delegate the atomic redemption-and-expiry
primitive, and the actual protected tool invocation that a granted
approval unlocks, to.

``redeem`` never decides replay-vs-mismatch on its own — that binding
comparison is ``run_approval_probe``'s job — but it *does* directly
and durably record, per attempt, whether this specific redemption was
accepted, and *only if accepted* durably records that the protected
tool was actually invoked as a result. Every call appends exactly one
``{"event": "decision", "nonce": ..., "digest": ..., "accepted": bool}``
record, under an exclusive OS file lock spanning the whole
read-check-decide-append critical section, atomically granting
``accepted: true`` only when *both* the binding is not yet expired
(``now < expires_at``) *and* this nonce has no prior accepted decision
— regardless of how many callers race for the same nonce concurrently.
Only when that decision is ``accepted: true`` does it then append a
second, distinct ``{"event": "invocation", "nonce": ...}`` record —
strictly after its own decision record — modeling the protected tool
actually being reached; a rejected decision (replay, mutated binding,
or expiry) never appends an invocation record at all, so no attempt
ever reaches the tool before its decision is durably recorded, and no
rejected attempt ever reaches it at all.

``run_approval_probe`` reads this same ledger both before and after
calling ``redeem`` and trusts *only* the newly appended records' own
explicit ``event``/``accepted`` fields as the directly observed
outcome of this call — never an inference drawn merely from how the
record count changed, which a fail-open store that silently
overwrote a prior record in place (rather than truly appending) could
otherwise fool, and never an expiry judgment made independently of
what this store itself decided.

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
                # decision has been durably recorded as accepted —
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
