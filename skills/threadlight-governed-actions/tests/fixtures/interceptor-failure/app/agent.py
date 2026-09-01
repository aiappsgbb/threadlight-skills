"""Synthetic application fixture for Task 5 hermetic enforcement probes.

Models a minimal application dispatch seam for the ``payments.refund``
action, a synthetic (never real) tool service, and a set of deliberately
faulty "interceptor" behaviors selected by ``fault``. Every fault is
exercised in an isolated subprocess by ``probes.run_application_probe`` —
this module never runs in-process with the assessor, never performs a real
refund, and never talks to a real payment system.

Ledger discipline: every observable step (interceptor entry, tool
invocation, final decision) is appended to the caller-supplied observation
ledger as a single flushed-and-fsynced JSON line *before* whatever comes
next (sleeping past the timeout, raising, or invoking the tool) — so a
parent that kills this process mid-fault can still read back exactly how
far it got. Ledger records and the ``dispatch_probe`` return value never
carry the raw ``arguments`` payload, only a canonical SHA-256 hash of
whatever the synthetic tool actually received.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Mapping, MutableMapping


# Payload-free audit sink named by the probe contract's ``audit_sink``
# field. Only ever holds ``audit_id``/``action_id``/``decision`` — no
# prompt, argument, output, or secret content.
AUDIT_EVENTS: "list[dict[str, object]]" = []
_AUDIT_SEQ = 0


def _canonical_hash(value: object) -> str:
    """Deterministic ``sha256:<hex>`` over *value*'s canonical JSON form."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _append_ledger(ledger_path: str, event: str, **fields: object) -> None:
    """Append one payload-free JSON record to *ledger_path* and fsync it.

    Flushing and fsyncing before returning is what lets a parent process
    prove this fixture reached a given step even after killing it for a
    timeout: the record is durable the instant it is written, not only
    once the process later exits cleanly.
    """
    record = {"event": event, **fields}
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _record_audit(action_id: str, decision: str) -> str:
    global _AUDIT_SEQ
    _AUDIT_SEQ += 1
    audit_id = f"audit-{_AUDIT_SEQ:04d}"
    AUDIT_EVENTS.append(
        {"audit_id": audit_id, "action_id": action_id, "decision": decision}
    )
    return audit_id


def _synthetic_tool_invoke(
    ledger_path: str, action_id: str, arguments: Mapping[str, object]
) -> str:
    """The synthetic (never real) side-effecting tool service.

    Records only that it was reached and a hash of what it received —
    never the arguments themselves — so the parent can prove reach
    without ever seeing (or needing) the payload.
    """
    argument_hash = _canonical_hash(dict(arguments))
    _append_ledger(
        ledger_path, "invocation", action_id=action_id, argument_hash=argument_hash
    )
    return argument_hash


def dispatch_probe(
    case: Mapping[str, object], ledger_path: str
) -> MutableMapping[str, object]:
    """Application dispatch seam exercised by the probe harness.

    ``case`` mirrors ``probes.ProbeCase`` as plain JSON: ``probe_id``,
    ``action_id``, ``fault``, ``arguments``. Returns only invocation
    count, argument hash, decision, and exception class — the caller
    (the isolated child driver in ``probes.py``) separately drains
    ``AUDIT_EVENTS`` for audit IDs; this function never returns the raw
    arguments or any tool output.
    """
    action_id = str(case["action_id"])
    fault = str(case["fault"])
    arguments = case["arguments"]

    _append_ledger(ledger_path, "start", action_id=action_id, fault=fault)

    if fault == "deny":
        _record_audit(action_id, "deny")
        _append_ledger(ledger_path, "decision", action_id=action_id, decision="deny")
        return {
            "decision": "deny",
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }

    if fault == "transform":
        transformed = dict(arguments)
        # Authorized policy: refunds above the synthetic $5 pre-authorized
        # ceiling are capped before the tool is ever called — never
        # passed through verbatim.
        if isinstance(transformed.get("amount"), (int, float)) and not isinstance(
            transformed.get("amount"), bool
        ):
            transformed["amount"] = min(transformed["amount"], 5)
        argument_hash = _synthetic_tool_invoke(ledger_path, action_id, transformed)
        _record_audit(action_id, "transform")
        _append_ledger(
            ledger_path, "decision", action_id=action_id, decision="transform"
        )
        return {
            "decision": "transform",
            "invocation_count": 1,
            "argument_hash": argument_hash,
            "exception_class": None,
        }

    if fault == "raise":
        # Interceptor crashes before ever reaching the tool: fail-closed.
        raise RuntimeError("synthetic interceptor crash (fail-closed)")

    if fault == "sleep":
        # Interceptor hangs well past the probe contract's timeout_ms, so
        # the parent must kill this process to observe anything at all.
        time.sleep(10.0)
        raise AssertionError("unreachable: the probe harness should time out first")

    if fault == "invalid":
        # An unknown/schema-invalid verdict from a (synthetic) policy
        # engine, reported exactly as received — this fixture never
        # normalizes it to a clean "deny" itself. Whether an
        # out-of-schema verdict is ever trustworthy is a governance
        # decision for the harness (see ``probes._build_probe_result``),
        # not something this dispatch seam gets to decide quietly on its
        # own. The tool is still never invoked for this fault.
        decision = "not-a-real-verdict"
        _record_audit(action_id, decision)
        _append_ledger(
            ledger_path,
            "decision",
            action_id=action_id,
            decision=decision,
            note="malformed-verdict",
        )
        return {
            "decision": decision,
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }

    if fault == "fail_open":
        # Deliberately faulty control: bypasses the deny/transform seam,
        # reaches the tool with the raw arguments, and only then crashes.
        # This proves the harness can detect fail-open behavior from the
        # ledger even though this process never gets to report anything.
        _synthetic_tool_invoke(ledger_path, action_id, arguments)
        raise RuntimeError("synthetic interceptor fail-open after invocation")

    if fault == "timeout_fail_open":
        # Fail-open variant of "sleep": the tool is reached *first*, then
        # the interceptor hangs well past timeout_ms. Proves a timed-out
        # dispatch is still ENF-002 whenever the ledger proves fail-open,
        # not only when it happens to also crash outright.
        _synthetic_tool_invoke(ledger_path, action_id, arguments)
        time.sleep(10.0)
        raise AssertionError("unreachable: the probe harness should time out first")

    if fault == "malformed_fail_open":
        # Fail-open variant of "invalid": the tool is reached *first*,
        # and only then does the interceptor report a malformed verdict.
        # Proves a malformed verdict is still ENF-002 whenever the
        # ledger proves fail-open, not only when it also happens to
        # block the tool.
        argument_hash = _synthetic_tool_invoke(ledger_path, action_id, arguments)
        decision = "not-a-real-verdict"
        _record_audit(action_id, decision)
        _append_ledger(
            ledger_path,
            "decision",
            action_id=action_id,
            decision=decision,
            note="malformed-verdict-fail-open",
        )
        return {
            "decision": decision,
            "invocation_count": 1,
            "argument_hash": argument_hash,
            "exception_class": None,
        }

    if fault == "mismatch":
        # Deliberately buggy seam: it invokes the tool anyway but still
        # (incorrectly) self-reports a clean deny. Proves the harness
        # trusts the ledger over the seam's own self-report.
        _synthetic_tool_invoke(ledger_path, action_id, arguments)
        _record_audit(action_id, "deny")
        _append_ledger(ledger_path, "decision", action_id=action_id, decision="deny")
        return {
            "decision": "deny",
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }

    raise ValueError(f"unknown probe fault: {fault!r}")
