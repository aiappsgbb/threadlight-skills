"""Reusable Task11/Task12 proof evaluator. A saved file is not an attestation.

Authority is established by the collector's authenticated point reads. This
module checks scope, order and consistency; it cannot authenticate an uploaded
JSON file or attest a hostile service host.
"""
from datetime import datetime, timedelta, timezone


class ProbeEvidenceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ProbeEvidenceError(reason)


def state(raw, *, run, registration, tenant, binding, fixture_id, producer):
    from govern_control_plane.models import canonical, parse
    from govern_control_plane.probes import State
    try:
        value = parse(State, canonical(raw))
    except Exception:
        raise ProbeEvidenceError("invalid-authoritative-state") from None
    require(value.probe_run_id == run and value.producer == producer
            and value.tenant == tenant and value.binding == binding
            and value.fixture_id == fixture_id
            and value.registration.model_dump(mode="json") == registration, "probe-scope-mismatch")
    return value


def fresh(value, started_at, now):
    require(started_at - timedelta(seconds=2) <= value.registered_at <= now + timedelta(seconds=2)
            and value.expires_at == value.registered_at + timedelta(minutes=10)
            and now < value.expires_at, "probe-registration-not-fresh")
    require(not any(value.counts.values()) and value.events == [] and value.context is None
            and value.terminal is None and value.action_hash is None and value.effect_key is None,
            "probe-baseline-not-empty")


def advance(before, after):
    require(all(after.counts[key] >= count for key, count in before.counts.items())
            and after.events[:len(before.events)] == before.events, "probe-state-reset-or-rewritten")
    require(all(getattr(before, key) == getattr(after, key) for key in (
        "probe_run_id", "producer", "tenant", "binding", "fixture_id", "registration",
        "registered_at", "expires_at")), "probe-registration-changed")
    if before.context is not None:
        require(before.context == after.context, "probe-context-changed")
    if before.terminal is not None:
        require(before == after, "terminal-state-changed")


def evaluate_pair(records, *, target, registration_scope, started_at, finished_at):
    """Only an explicit noop binding at pre_tool_call can consume this evidence."""
    from govern_control_plane.models import DecisionReceipt, canonical, parse
    from govern_gateway.dispatcher import digest
    require(len(records) == 2 and {r["variant"] for r in records} == {"allow", "deny"},
            "positive-and-deny-required")
    require(len({r["run_id"] for r in records}) == 2, "fresh-distinct-nonces-required")
    summaries = []
    for record in records:
        variant, run = record["variant"], record["run_id"]
        registration = {**registration_scope["registration"], "variant": variant}
        kwargs = {key: registration_scope[key] for key in ("tenant", "binding", "fixture_id")}
        kwargs.update(run=run, registration=registration)
        producer = state(record["producer"], **kwargs, producer=registration_scope["producer"])
        fixture = state(record["fixture"], **kwargs, producer="fixture")
        for name, after in (("producer", producer), ("fixture", fixture)):
            before = state(record["before_" + name], **kwargs, producer=after.producer)
            fresh(before, started_at, finished_at)
            advance(before, after)
            times = [e.recorded_at for e in after.events]
            require(times == sorted(times) and all(
                before.registered_at <= t <= finished_at + timedelta(seconds=2) for t in times),
                "probe-event-time-invalid")
        allowed = variant == "allow"
        expected = {"received": 1, "intercepted": 1, "dispatch": int(allowed), "effect": 0, "completed": 1}
        require(producer.counts == expected
                and producer.terminal == ("completed" if allowed else "denied"), "producer-not-terminal")
        require(fixture.counts == {"received": int(allowed), "intercepted": 0, "dispatch": 0,
                                   "effect": int(allowed), "completed": int(allowed)}
                and fixture.terminal == ("completed" if allowed else None), "fixture-effect-mismatch")
        require([e.phase for e in producer.events] == (
            ["received", "intercepted", "dispatch", "completed"] if allowed
            else ["received", "intercepted", "completed"]), "producer-event-order-invalid")
        try:
            receipt = parse(DecisionReceipt, canonical(record["receipt"]))
        except Exception:
            raise ProbeEvidenceError("receipt-invalid") from None
        event = producer.events[1]
        context = producer.context
        require(context is not None and receipt.probe == context
                and event.receipt_id == receipt.receipt_id and event.decision == variant
                and receipt.decision == variant
                and receipt.action_id == "governance_probe_noop"
                and receipt.policy_digest == registration["policy_digest"]
                and receipt.agent_version == target["agent_version"]
                and receipt.image_digest == target["image_digest"]
                and producer.registered_at <= receipt.recorded_at <= event.recorded_at,
                "receipt-scope-decision-or-time-mismatch")
        facts = {"tenant": target["tenant"], "subject": target["subject"], "client": target["client_id"],
                 "action": receipt.action_id, "scope": "governance-probe", "policy": receipt.policy_digest,
                 "deployment": registration["deployment"]}
        action_hash = digest({"facts": facts, "arguments": {"probe_run_id": run, "variant": variant}})
        if producer.producer == "native":
            # Native action_hash hashes the native hook context (including raw
            # session/call identities). Those identities MUST NOT be disclosed
            # merely to rebuild a hash. Compare its durable native record hash,
            # not the distinct Task9 wire-action hash.
            require(producer.action_hash is not None and receipt.action_hash == producer.action_hash,
                    "native-record-hash-mismatch")
        else:
            require(receipt.action_hash == action_hash, "gateway-action-hash-mismatch")
        if allowed:
            require(fixture.action_hash == action_hash
                    and all(e.receipt_id == receipt.receipt_id for e in fixture.events),
                    "fixture-receipt-or-action-hash-mismatch")
        summaries.append({
            "probe_id": run, "binding_id": receipt.action_id,
            "environment": registration["deployment"]["environment"],
            "agent_version": target["agent_version"], "decision": variant,
            "downstream_effect_delta": int(allowed),
            "decision_receipt_ref": "EV-receipt-" + receipt.receipt_id,
            "service_oracle_ref": "EV-fixture-" + run, "status": "pass"})
    return summaries
