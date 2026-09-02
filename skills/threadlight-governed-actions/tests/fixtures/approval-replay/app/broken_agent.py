"""Deliberately broken, fail-open approval nonce store demonstrating the
``approval-replay`` fixture's scenario: an anti-replay control that
always grants ``accepted: true`` (and always invokes the protected
tool as a result), even to a second attempt for a nonce that already
has an accepted record.

This module is never used by ``tests/fixtures/approval-replay/app/agent.py``
(that module is the fixture's own genuinely correct, atomic nonce
store, exercised by ``tests/test_probes.py``'s existing payload-freeness
and anti-replay tests) -- it exists solely so this fixture's own
scenario-matrix entry (``approval-replay: gate_exit=1,
must_fix={APR-001}``, see ``tests/test_golden_fixtures.py``) can prove
``probes.run_approval_probe`` genuinely catches this exact anti-pattern
against a real dispatch seam rather than only a synthetic, throwaway,
non-fixture-owned one. Mirrors the identical broken-store shape
``tests/test_probes.py::test_approval_probe_reports_apr_001_when_ledger_proves_non_atomic_reuse``
already proves ``run_approval_probe`` flags as ``APR-001``/``must-fix``.
"""
from __future__ import annotations

import json


AUDIT_EVENTS = []


def redeem(nonce: str, digest: str, expires_at: str, now: str, ledger_path: str) -> None:
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "decision",
                    "nonce": nonce,
                    "digest": digest,
                    "accepted": True,
                }
            )
            + "\n"
        )
        handle.write(json.dumps({"event": "invocation", "nonce": nonce}) + "\n")

    AUDIT_EVENTS.append(
        {
            "audit_id": f"audit-approval-{nonce}",
            "event": "approval_redemption_attempt",
            "digest_hash": digest,
        }
    )
