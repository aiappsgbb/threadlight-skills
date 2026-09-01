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

Two faults are deliberately dishonest about that discipline, on purpose:
``stub`` never writes to the ledger at all (not even a "start" record)
while still returning a clean, well-formed self-report, proving the
harness must never trust a self-report that the ledger does not
corroborate. ``slow_correct_transform`` correctly applies the same
transform policy as ``transform`` and reaches the tool with the right
arguments, but then hangs past ``timeout_ms`` instead of ever reporting a
decision, proving the harness must not fabricate a fail-open finding
merely because a transform-family fault's own contract expects the tool
to be reached.

Every real decision (every fault except ``stub``) durably mirrors its
audit event to the observation ledger, not only to the in-memory
``AUDIT_EVENTS`` sink, via ``_record_audit_with_ledger``: the sink is
only ever drained by the parent *after* ``dispatch_probe`` returns
normally, so a crashed or killed child's audit trail would otherwise
never surface at all. ``raise`` and ``sleep`` record that ledger audit
event immediately before crashing/hanging, proving a visible operational
signal was recorded even though the process never gets to complete —
this is what lets a fail-closed crash/timeout probe pass on real
evidence rather than on silence. ``silent_crash`` deliberately omits
that audit record (crashes right after only a "start" record) to prove
the opposite: a "start" record alone is never enough evidence for a
pass. ``double_invoke_transform`` and ``crash_before_transform_invoke``
are further deliberately negative transform-family regressions: the
former invokes the tool twice (once correctly transformed, once with
the raw/untransformed arguments) while still self-reporting a single
clean invocation, proving the harness counts the ledger's own
invocation records rather than trusting a self-reported count; the
latter crashes before ever invoking the tool at all, proving an
incomplete transform-family run is truthfully unverified, never a
fabricated policy-violation finding. ``raw_passthrough_transform`` is a
further deliberately negative regression, fully self-report/ledger
consistent yet still wrong: it self-reports a clean "transform"
decision, corroborated by every ledger record the harness checks, but
never actually applies the transform policy — the tool receives the
raw arguments completely unchanged. Proves the harness must not treat
self-report/ledger *consistency* alone as proof a transform happened:
it must also prove the ledger shows the tool actually received
something different from what the case started with.

Every ledger "invocation" record carries both the hash of the
arguments the synthetic tool actually received and a hash of the
case's original, pre-transform arguments — still payload-free, never
the raw values — so the harness can prove a transform actually changed
something (the two hashes differ) without ever needing the payload
itself.
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


def _record_audit_with_ledger(ledger_path: str, action_id: str, decision: str) -> str:
    """Record an audit event both in-memory and durably in the ledger.

    ``AUDIT_EVENTS`` is only ever drained by the parent harness *after*
    ``dispatch_probe`` returns normally — a crashed or killed child's
    in-memory audit trail never surfaces at all. Mirroring the same
    event to the observation ledger (flushed and fsynced immediately, by
    ``_append_ledger``) is what lets a parent that has to kill this
    process still prove a visible operational audit signal was recorded
    before it died. Every real decision uses this instead of the bare
    ``_record_audit`` — except ``stub``, which deliberately keeps using
    the ledger-free version to prove a self-report is never trusted
    without a corroborating ledger record.
    """
    audit_id = _record_audit(action_id, decision)
    _append_ledger(
        ledger_path, "audit", action_id=action_id, decision=decision, audit_id=audit_id
    )
    return audit_id


def _transform_arguments(arguments: Mapping[str, object]) -> dict:
    """Apply the same authorized-policy transform every transform fault uses.

    Refunds above the synthetic $5 pre-authorized ceiling are capped
    before the tool is ever called — never passed through verbatim.
    Shared by ``transform`` and ``slow_correct_transform`` so both apply
    the identical, correct policy.
    """
    transformed = dict(arguments)
    if isinstance(transformed.get("amount"), (int, float)) and not isinstance(
        transformed.get("amount"), bool
    ):
        transformed["amount"] = min(transformed["amount"], 5)
    return transformed


def _synthetic_tool_invoke(
    ledger_path: str,
    action_id: str,
    arguments: Mapping[str, object],
    original_arguments: Mapping[str, object],
) -> str:
    """The synthetic (never real) side-effecting tool service.

    Records only that it was reached, a hash of what it actually
    received, and a hash of the case's original (pre-transform)
    arguments — never the arguments themselves — so the parent can
    prove reach, and prove a transform actually changed something (the
    two hashes differ), without ever seeing or needing the payload.
    For a fault that invokes with the raw arguments directly (no
    transform), *arguments* and *original_arguments* are the same
    mapping, so both hashes are identical.
    """
    argument_hash = _canonical_hash(dict(arguments))
    original_argument_hash = _canonical_hash(dict(original_arguments))
    _append_ledger(
        ledger_path,
        "invocation",
        action_id=action_id,
        argument_hash=argument_hash,
        original_argument_hash=original_argument_hash,
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

    if fault == "stub":
        # Deliberately buggy seam: never writes anything to the
        # observation ledger at all — not even a "start" record — yet
        # still returns a well-formed, self-reported "deny" decision
        # backed by a real audit event. Proves a self-report is never
        # trusted merely because it looks clean and carries an audit
        # id: without a correlated ledger start/decision record the
        # harness must treat this as unobservable, never a pass.
        _record_audit(action_id, "deny")
        return {
            "decision": "deny",
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }

    _append_ledger(ledger_path, "start", action_id=action_id, fault=fault)

    if fault == "deny":
        _record_audit_with_ledger(ledger_path, action_id, "deny")
        _append_ledger(ledger_path, "decision", action_id=action_id, decision="deny")
        return {
            "decision": "deny",
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }

    if fault == "transform":
        transformed = _transform_arguments(arguments)
        argument_hash = _synthetic_tool_invoke(
            ledger_path, action_id, transformed, arguments
        )
        _record_audit_with_ledger(ledger_path, action_id, "transform")
        _append_ledger(
            ledger_path, "decision", action_id=action_id, decision="transform"
        )
        return {
            "decision": "transform",
            "invocation_count": 1,
            "argument_hash": argument_hash,
            "exception_class": None,
        }

    if fault == "slow_correct_transform":
        # Correctly applies the *same* transform policy as "transform"
        # (caps the refund amount at the synthetic $5 ceiling) and
        # reaches the tool with those correctly transformed arguments —
        # then hangs well past timeout_ms before it can ever report a
        # decision. Proves the harness must not fabricate a fail-open
        # ENF-002 finding merely because a transform-family fault's own
        # contract expects the tool to be reached on a normal
        # completion: reaching the tool is not itself forbidden here,
        # so a timeout afterwards is unverified, never a manufactured
        # violation.
        transformed = _transform_arguments(arguments)
        _synthetic_tool_invoke(ledger_path, action_id, transformed, arguments)
        time.sleep(10.0)
        raise AssertionError("unreachable: the probe harness should time out first")

    if fault == "double_invoke_transform":
        # Deliberately buggy seam: reaches the tool *twice* — once with
        # the raw, untransformed arguments (a policy-bypass leak), then
        # again with the correctly transformed arguments — yet still
        # self-reports a single clean "transform" decision with the
        # correctly transformed hash and a (lying) invocation_count of
        # 1. Proves the harness counts the ledger's own recorded
        # invocation events for the action rather than trusting a
        # self-reported count or only the first/last recorded hash: an
        # extra raw invocation this fixture never admits to must still
        # never be laundered into a clean pass.
        _synthetic_tool_invoke(ledger_path, action_id, arguments, arguments)
        transformed = _transform_arguments(arguments)
        argument_hash = _synthetic_tool_invoke(
            ledger_path, action_id, transformed, arguments
        )
        _record_audit_with_ledger(ledger_path, action_id, "transform")
        _append_ledger(
            ledger_path, "decision", action_id=action_id, decision="transform"
        )
        return {
            "decision": "transform",
            "invocation_count": 1,
            "argument_hash": argument_hash,
            "exception_class": None,
        }

    if fault == "crash_before_transform_invoke":
        # A transform-family regression that crashes *before* ever
        # reaching the tool — never applies the transform, never
        # invokes anything, never reports a decision. A transform-
        # family fault's own contract expects the tool to be reached
        # on a normal completion, so an incomplete run that never even
        # got that far proves nothing either way: not a pass (the
        # transform never demonstrably happened), and never a
        # fabricated ENF-001/ENF-002 finding either, since no ledger
        # evidence contradicts anything this fault ever claimed to do.
        raise RuntimeError("synthetic interceptor crash before transform invocation")

    if fault == "raw_passthrough_transform":
        # Deliberately buggy seam: self-reports a clean, well-formed
        # "transform" decision, fully corroborated by matching ledger
        # start/decision records and a real audit id, yet never
        # actually applies the transform policy at all — it invokes
        # the tool with the raw, completely unchanged arguments (both
        # the transformed and original hashes it records are
        # identical). Proves the harness must not treat self-report/
        # ledger *consistency* alone as proof of a transform: it must
        # also prove the ledger shows the tool actually received
        # something different from the case's original arguments.
        argument_hash = _synthetic_tool_invoke(
            ledger_path, action_id, arguments, arguments
        )
        _record_audit_with_ledger(ledger_path, action_id, "transform")
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
        # Records a durable audit event first — the in-memory
        # AUDIT_EVENTS sink is only ever drained after a normal return,
        # so without this the crash would leave no visible operational
        # signal at all for the harness to corroborate a pass against.
        _record_audit_with_ledger(ledger_path, action_id, "crash-detected")
        raise RuntimeError("synthetic interceptor crash (fail-closed)")

    if fault == "silent_crash":
        # Deliberately minimal-evidence regression: crashes immediately
        # after the unconditional "start" record above, recording no
        # audit signal at all — unlike "raise", which durably records
        # one before crashing. Proves the harness never grants a
        # fail-closed pass on silence alone: a "start" record by itself
        # is not a visible operational audit signal, so this must raise
        # ProbeToolingError rather than fabricate a pass.
        raise RuntimeError("synthetic interceptor crash with no audit signal")

    if fault == "sleep":
        # Interceptor hangs well past the probe contract's timeout_ms, so
        # the parent must kill this process to observe anything at all.
        # Records a durable audit event first, for the same reason
        # "raise" does: a killed process's in-memory audit trail would
        # otherwise never surface to the parent at all.
        _record_audit_with_ledger(ledger_path, action_id, "timeout-detected")
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
        _record_audit_with_ledger(ledger_path, action_id, decision)
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
        _synthetic_tool_invoke(ledger_path, action_id, arguments, arguments)
        raise RuntimeError("synthetic interceptor fail-open after invocation")

    if fault == "timeout_fail_open":
        # Fail-open variant of "sleep": the tool is reached *first*, then
        # the interceptor hangs well past timeout_ms. Proves a timed-out
        # dispatch is still ENF-002 whenever the ledger proves fail-open,
        # not only when it happens to also crash outright.
        _synthetic_tool_invoke(ledger_path, action_id, arguments, arguments)
        time.sleep(10.0)
        raise AssertionError("unreachable: the probe harness should time out first")

    if fault == "malformed_fail_open":
        # Fail-open variant of "invalid": the tool is reached *first*,
        # and only then does the interceptor report a malformed verdict.
        # Proves a malformed verdict is still ENF-002 whenever the
        # ledger proves fail-open, not only when it also happens to
        # block the tool.
        argument_hash = _synthetic_tool_invoke(
            ledger_path, action_id, arguments, arguments
        )
        decision = "not-a-real-verdict"
        _record_audit_with_ledger(ledger_path, action_id, decision)
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
        _synthetic_tool_invoke(ledger_path, action_id, arguments, arguments)
        _record_audit_with_ledger(ledger_path, action_id, "deny")
        _append_ledger(ledger_path, "decision", action_id=action_id, decision="deny")
        return {
            "decision": "deny",
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }

    raise ValueError(f"unknown probe fault: {fault!r}")
