"""Synthetic post-action output mediator fixture (Task 6).

Models a real service's protected-output release path under three
verdicts:

- ``deny``: the complete protected output must be buffered until the
  final verdict is known, and a denied verdict releases zero bytes —
  the reference "buffer until verdict" posture.
- ``allow``: the complete protected output is likewise buffered and
  only ever released, as a single block, once the final verdict is
  known (never before).
- ``stream``: incremental (chunked) release. ``probes.run_output_probe``
  only ever trusts this as fully mediated when the probe contract
  itself declares an explicit, nonzero ``exposure_bound_bytes`` and
  ``chunk_mediation: true`` *and* every chunk this fixture actually
  released is independently proven, from the ledger, to have been
  mediated and within that declared bound.

Every event this fixture records to the ledger is a byte *count*
(``bytes``) and a boolean mediation flag, never actual output content,
so it stays payload-free by construction.
"""
from __future__ import annotations

import json


AUDIT_EVENTS = []

_TOTAL_OUTPUT_BYTES = 96
_CHUNK_SIZE_BYTES = 32


def emit_output(verdict: str, ledger_path: str) -> None:
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "verdict_received", "verdict": verdict}) + "\n"
        )
        handle.flush()

        if verdict == "deny":
            # Buffered posture: a denied verdict releases nothing at all.
            handle.write(json.dumps({"event": "egress", "bytes": 0}) + "\n")
        elif verdict == "allow":
            # Buffered posture: the complete output is released only
            # now, after the verdict is already known.
            handle.write(
                json.dumps({"event": "egress", "bytes": _TOTAL_OUTPUT_BYTES}) + "\n"
            )
        elif verdict == "stream":
            remaining = _TOTAL_OUTPUT_BYTES
            while remaining > 0:
                chunk_bytes = min(_CHUNK_SIZE_BYTES, remaining)
                remaining -= chunk_bytes
                handle.write(
                    json.dumps(
                        {"event": "chunk", "bytes": chunk_bytes, "mediated": True}
                    )
                    + "\n"
                )
            handle.write(
                json.dumps({"event": "egress", "bytes": _TOTAL_OUTPUT_BYTES}) + "\n"
            )
        else:
            raise ValueError(f"unknown output verdict: {verdict!r}")
        handle.flush()

    AUDIT_EVENTS.append(
        {
            "audit_id": f"audit-output-{verdict}",
            "event": "output_mediation_decision",
            "verdict": verdict,
        }
    )
