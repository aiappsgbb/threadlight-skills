"""The conformant-maf fixture's single declared application module.

Its ``payments.capture`` crash path deliberately fails open (see
``dispatch_probe``); every other seam behaves exactly as the conformant
fixture's does.

This one module is everything the fixture declares under ``app/``: the
statically-inspected action declarations and mediation graph, *and* the
one real dispatch seam every probe kind drives. ``dispatch`` below is the
single callable named by ``governance/probe-contract.json``; it routes to
the enforcement seam (``dispatch_probe``), the approval seam (``redeem``),
or the output seam (``emit_output``) purely by the shape of the arguments
the probe harness passes, so a target never has to declare -- or a
fixture tree never has to carry -- a separate module per probe kind.

Nothing here is ever imported or executed in-process by the assessor: the
static analysis paths only inspect this source, and every probe path runs
it in an isolated child subprocess. No record this module writes, to a
ledger or to ``AUDIT_EVENTS``, ever carries a raw argument payload -- only
canonical hashes and payload-free identifiers.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from typing import Any, Callable, Mapping, MutableMapping


class _Namespace:
    """Minimal stand-in for a mediation seam namespace.

    mediation.py recognizes mediation evidence by statically inspecting the
    *shape* of calls in dispatch function bodies (bare ``name.attr(...)``
    call expressions); it never imports or executes this module, so these
    stand-ins only need to exist to keep the module free of unresolved-name
    lint noise -- they are never actually invoked at runtime.
    """

    def __getattr__(self, _name: str) -> Callable[..., Any]:
        def _call(*args: Any, **kwargs: Any) -> None:
            return None

        return _call


agent_hooks = _Namespace()
tool_service = _Namespace()
output_mediator = _Namespace()
audit_sink = _Namespace()


def tool(*, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Minimal stand-in for a framework tool-registration decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__tool_name__ = name
        return func

    return decorator


@tool(name="customer.lookup")
def customer_lookup(customer_id: str) -> dict[str, Any]:
    """Look up a customer record. Read-only; no state mutation."""
    return {"customer_id": customer_id}


@tool(name="payments.refund")
def payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Issue a refund. Irreversible; requires approval per SPEC section 8."""
    return {"payment_id": payment_id, "amount": amount}


# ---------------------------------------------------------------------------
# Mediation dispatch functions.
#
# mediation.py recognizes these functions by static AST inspection using the
# convention ``_mode_action_function_name(action_id, mode)`` ==
# ``"{mode}_{action_id}"`` (hyphens and dots normalized to underscores). Every
# non-exclusively-provider-hosted action is assessed across all five
# REQUIRED_NON_PROVIDER_MODES, so both ``customer.lookup`` and
# ``payments.refund`` need one dispatch function per mode below. Each
# function is never imported or executed -- mediation.py only inspects the
# call shapes in its body: a bare ``agent_hooks.pre_tool_call(...)`` call
# that source-precedes a bare ``tool_service.*``/``provider.*`` call proves a
# pre-action seam mediates the tool-service invocation.


def interactive_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="interactive")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="interactive")
    return result


def batch_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="batch")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="batch")
    return result


def background_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="background")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="background")
    return result


def subagent_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="subagent")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="subagent")
    return result


def direct_tool_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="direct-tool")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="direct-tool")
    return result


def interactive_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="interactive")
    agent_hooks.require_approval(action_id="payments.refund", mode="interactive")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="interactive")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="interactive")
    return result


def batch_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="batch")
    agent_hooks.require_approval(action_id="payments.refund", mode="batch")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="batch")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="batch")
    return result


def background_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="background")
    agent_hooks.require_approval(action_id="payments.refund", mode="background")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="background")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="background")
    return result


def subagent_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="subagent")
    agent_hooks.require_approval(action_id="payments.refund", mode="subagent")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="subagent")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="subagent")
    return result


def direct_tool_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="direct-tool")
    agent_hooks.require_approval(action_id="payments.refund", mode="direct-tool")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="direct-tool")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="direct-tool")
    return result


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

    ``governance/probe-contract.json`` declares this same function as
    its ``dispatch`` target for every probe kind this fixture proves
    (application enforcement, and by extension the base output
    mediation coverage every fixture is checked against). The output
    probe harness calls ``dispatch`` with a bare ``(verdict, ledger_path)``
    tuple rather than a ``ProbeCase`` mapping, so this seam is never
    mediated by anything other than itself: it records a
    ``verdict_received`` event and zero released bytes, proving this
    fixture never exposes protected output regardless of which probe
    invoked it. This branch never participates in the enforcement
    fault matrix below — it only ever runs for the plain-string output
    verdict shape.
    """
    if isinstance(case, str):
        verdict = case
        _append_ledger(ledger_path, "verdict_received", verdict=verdict)
        return {"decision": verdict, "invocation_count": 0, "argument_hash": None, "exception_class": None}

    action_id = str(case["action_id"])
    fault = str(case["fault"])
    arguments = case["arguments"]

    if action_id == "payments.capture" and fault == "raise":
        # This fixture's declared defect: the ``payments.capture``
        # interceptor invokes the protected tool *before* the fault
        # reaches it, so an abnormal termination leaves a real side
        # effect behind. Routed through the module's own already-correct
        # fail-open implementation so the defect is the routing decision
        # itself, never a second, divergent copy of the seam.
        fault = "fail_open"

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

    if fault == "transform_missing_original_hash":
        # Deliberately incomplete ledger evidence: correctly applies
        # the transform policy and reaches the tool with the right
        # arguments, self-reports a clean "transform" decision fully
        # corroborated by matching ledger start/decision records and a
        # real audit id — but its own "invocation" ledger record omits
        # the mandatory "original_argument_hash" field entirely,
        # exactly as if an older or buggy ledger writer never recorded
        # it. Bypasses ``_synthetic_tool_invoke`` (which always records
        # both hashes) to construct this incomplete record directly.
        # Proves the harness must never treat a completed transform as
        # a pass — or even as a truthful no-op finding — when the
        # ledger cannot actually prove whether the arguments changed:
        # incomplete mandatory evidence always raises
        # ``ProbeToolingError``, never a fabricated pass.
        transformed = _transform_arguments(arguments)
        argument_hash = _canonical_hash(dict(transformed))
        _append_ledger(
            ledger_path,
            "invocation",
            action_id=action_id,
            argument_hash=argument_hash,
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

# ---------------------------------------------------------------------------
# Approval seam: synthetic, service-side atomic nonce redemption.
#
# A first non-expired attempt for a nonce is durably recorded
# ``accepted: true`` and only then followed by a durable ``invocation``
# record; any later attempt for the same nonce -- byte-identical replay or
# mutated-field reuse -- is durably recorded ``accepted: false`` and never
# invokes the protected tool, even under concurrent racing callers (the
# whole read-check-decide-append critical section is guarded by an
# exclusive OS file lock). Only the caller-supplied canonical digest is
# ever persisted; never a raw approval argument payload.
# ---------------------------------------------------------------------------


def redeem(nonce: str, digest: str, expires_at: str, now: str, ledger_path: str) -> None:
    with open(ledger_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = [
                json.loads(line) for line in handle.read().splitlines() if line.strip()
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
                handle.write(json.dumps({"event": "invocation", "nonce": nonce}) + "\n")
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


# ---------------------------------------------------------------------------
# Output seam: verdict-first, fully buffered mediation.
#
# The verdict is durably recorded before anything is ever released, a
# denied verdict releases exactly zero bytes, and an allowed verdict
# releases one single buffered ``egress`` record (never incremental
# ``chunk`` records) -- so no output can ever precede, or contradict, the
# mediation decision that authorized it.
# ---------------------------------------------------------------------------


def emit_output(verdict: str, ledger_path: str) -> None:
    _append_ledger(ledger_path, "verdict_received", verdict=verdict)
    if verdict == "deny":
        released = 0
    else:
        released = 32
        _append_ledger(ledger_path, "egress", bytes=released, mediated=True)
    AUDIT_EVENTS.append(
        {
            "audit_id": f"audit-output-{verdict}",
            "event": "output_mediation_decision",
            "decision": verdict,
        }
    )


# ---------------------------------------------------------------------------
# The single declared dispatch callable.
# ---------------------------------------------------------------------------


def dispatch(*args: object) -> object:
    """Route one probe call to the seam its own argument shape names.

    ``redeem`` is the only five-argument seam; ``emit_output`` is the only
    two-argument seam whose first argument is a plain verdict string;
    everything else is the enforcement seam's ``(case, ledger_path)``.
    Deliberately shape-based rather than mode-flagged: a probe harness
    never passes a "which seam" selector, and this fixture never invents
    one.
    """
    if len(args) == 5:
        return redeem(*args)  # type: ignore[arg-type]
    if len(args) == 2 and isinstance(args[0], str):
        return emit_output(*args)  # type: ignore[arg-type]
    return dispatch_probe(*args)  # type: ignore[arg-type]
