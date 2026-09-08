"""Reusable Task11/Task12 proof evaluator. A saved file is not an attestation.

Authority is established by the collector's authenticated point reads. This
module checks scope, order and consistency; it cannot authenticate an uploaded
JSON file or attest a hostile service host.
"""
from datetime import datetime, timedelta, timezone


class ProbeEvidenceError(ValueError):
    pass


def policy_bindings(config, *, as_of=None):
    """Fingerprint complete current artifacts; this function does NOT verify signatures."""
    import base64
    import hashlib
    from pathlib import Path
    from govern_control_plane.models import SignedBundle, canonical, parse
    from govern_bundle.policy_bundle import verify_bundle
    as_of = as_of or datetime.now(timezone.utc)
    result = {}
    for name in ("policy", "native_policy") if config["producer"] == "native" else ("policy",):
        policy = config[name]
        signed = parse(SignedBundle, canonical(policy["signed"]))
        base64.b64decode(signed.signature, validate=True)
        envelope = signed.envelope
        require(envelope.tenant_id == config["tenant_id"]
                and envelope.key_id == policy["key_id"]
                and envelope.policy_id == policy["policy_id"]
                and envelope.version == policy["policy_version"]
                and envelope.content_digest == policy["policy_digest"]
                and envelope.expires_at > as_of, "current-signed-policy-mismatch")
        verify_bundle(Path(policy["bundle_path"]), expected_digest=envelope.content_digest)
        result[name] = {
            "signed_bundle_sha256": "sha256:" + hashlib.sha256(canonical(policy["signed"])).hexdigest(),
            "bundle_digest": envelope.content_digest,
            "expires_at": envelope.model_dump(mode="json")["expires_at"],
        }
    return result


def validate_policy_bindings(bindings, producer, policy_bundle, finished_at):
    import re
    names = {"policy", "native_policy"} if producer == "native" else {"policy"}
    require(isinstance(bindings, dict) and set(bindings) == names, "complete-policy-chain-required")
    for value in bindings.values():
        require(isinstance(value, dict) and set(value) == {
            "signed_bundle_sha256", "bundle_digest", "expires_at"}, "invalid-policy-binding")
        require(all(isinstance(value[k], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value[k])
                    for k in ("signed_bundle_sha256", "bundle_digest")), "invalid-policy-binding-digest")
        expiry = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        require(expiry.tzinfo is not None and expiry > finished_at, "verified-policy-expired")
    runtime = bindings["native_policy" if producer == "native" else "policy"]
    require(policy_bundle["signature_verified"] is True
            and policy_bundle["digest"] == runtime["bundle_digest"]
            and policy_bundle["expires_at"] == runtime["expires_at"], "verified-policy-provenance-mismatch")


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


DEPLOYMENT_FIELDS = frozenset({
    "agent_id", "agent_version", "image_digest", "environment", "subscription", "resource_group",
})
TARGET_FIELDS = DEPLOYMENT_FIELDS | {"tenant", "subject", "client_id"}


def require_target(target, expected_target, deployment=None):
    """Environment is a frozen declaration, not a field invented by ARM observation."""
    require(isinstance(expected_target, dict) and set(expected_target) == TARGET_FIELDS
            and all(isinstance(v, str) and 0 < len(v) <= 512 for v in expected_target.values()),
            "exact-expected-target-required")
    require(all(target.get(key) == value for key, value in expected_target.items() if key != "environment")
            and ("environment" not in target or target["environment"] == expected_target["environment"]),
            "observed-target-scope-mismatch")
    if deployment is not None:
        require(deployment == {key: expected_target[key] for key in DEPLOYMENT_FIELDS},
                "registered-target-scope-mismatch")


def require_selected_target(expected_target, required_target):
    require(isinstance(required_target, dict) and set(required_target) <= TARGET_FIELDS
            and all(expected_target.get(k) == v for k, v in required_target.items()),
            "selected-target-scope-mismatch")


def evaluate_pair(records, *, target, expected_target, registration_scope, started_at, finished_at):
    """Only an explicit noop binding at pre_tool_call can consume this evidence."""
    from govern_control_plane.models import DecisionReceipt, canonical, parse
    from govern_gateway.dispatcher import digest
    require_target(target, expected_target, registration_scope["registration"]["deployment"])
    require(registration_scope["tenant"] == target["tenant"]
            and registration_scope["registration"]["subject"] == target["subject"]
            and registration_scope["registration"]["action"] == "governance_probe_noop",
            "registration-target-identity-mismatch")
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
