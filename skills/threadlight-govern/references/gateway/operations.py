"""Operator-only recovery and leased admission; never registered as model tools."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import model_validator

from govern_control_plane.auth import Unauthorized
from govern_control_plane.models import Digest, Identifier, ObjectId, StrictModel, Timestamp, canonical, parse
from govern_control_plane.storage import Conflict, Missing

from .dispatcher import GateError, digest, validated


class Operation(StrictModel):
    operation: Literal["inspect", "reconcile", "admission"]
    action: Identifier
    requester: ObjectId
    operation_id: Identifier | None = None
    arguments: dict | None = None
    expected_record_hash: Digest | None = None
    state: Literal["open", "stopped"] | None = None
    valid_until: Timestamp | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.operation == "admission":
            if (self.state is None or self.reason_code is None
                    or self.operation_id is not None or self.arguments is not None
                    or (self.valid_until is None) != (self.state == "stopped")):
                raise ValueError("invalid_admission_request")
        else:
            if any(v is not None for v in (self.state, self.valid_until, self.reason_code)):
                raise ValueError("invalid_operation_request")
            if (self.operation_id is None) != (self.arguments is None):
                raise ValueError("original_operation_required")
            if self.operation == "reconcile" and (
                    self.operation_id is None or self.expected_record_hash is None):
                raise ValueError("recovery_precondition_required")
        return self


def coordinates(dispatcher, action, requester):
    registry = dispatcher.policy.registry
    workload = dispatcher.auth.settings.workloads.get(requester)
    selected = next((a for a in registry.actions if a.name == action), None)
    if (workload is None or selected is None or requester not in selected.workloads
            or workload.agent_id != registry.deployment.agent_id
            or dispatcher.policy.policy_id not in workload.policies):
        raise GateError("scope_denied", "blocked")
    facts = {"tenant": registry.tenant_id, "subject": requester, "client": workload.client_id,
             "action": selected.name, "scope": selected.scope, "policy": dispatcher.policy.digest,
             "deployment": registry.deployment.model_dump(mode="json")}
    return selected, facts


def admission_location(facts):
    return digest(["admission", facts]), "admission"


async def admit(dispatcher, facts):
    if not dispatcher.operations_required:
        return
    try:
        # Each read is fresh. No process-local allow cache survives a stop/rotation.
        await dispatcher.store.health()
        await dispatcher.policy.signer.health()
        record, _ = await dispatcher.store.read(*admission_location(facts))
        until = parse(Timestamp, canonical(record["valid_until"]))
        if (record["state"] != "open" or record["facts_hash"] != digest(facts)
                or until <= datetime.now(timezone.utc) or not record["audit_receipt_id"]):
            raise ValueError()
        dispatcher.policy.fresh()
    except Exception:
        raise GateError("admission_closed", "blocked") from None


def summary(record):
    return {"state": record["state"], "record_hash": digest(record),
            "audit_receipt_id": record.get("operator_audit_receipt_id", record.get("audit_receipt_id")),
            "outcome_reference": record.get("outcome_reference"), "retry_authorized": False}


async def operate(dispatcher, authorization, body):
    try:
        request = parse(Operation, canonical(body))
        async def authorize():
            identity = await dispatcher.auth.authenticate(authorization)
            controller = dispatcher.auth.settings.operation_controllers.get(identity.subject)
            if (not dispatcher.operations_required or controller is None
                    or identity.client != controller.client_id or identity.workload is not None
                    or "Governance.Operate" not in identity.roles
                    or request.requester not in controller.subjects or request.action not in controller.actions):
                raise Unauthorized()
            return identity
        identity = await authorize()
        action, facts = coordinates(dispatcher, request.action, request.requester)
        location = (admission_location(facts) if request.operation_id is None
                    else (digest([facts["tenant"], facts["subject"], action.name]),
                          digest(request.operation_id)[7:]))
        try:
            record, etag = await dispatcher.store.read(*location)
        except Missing:
            record, etag = None, None
        if request.operation_id is not None:
            arguments = validated(request.arguments, action.input_schema)
            if record is None:
                raise GateError("operation_not_found", "blocked")
            if (record["input_hash"] != digest({"facts": facts, "arguments": arguments})
                    or record["facts_hash"] != digest(facts)):
                raise GateError("idempotency_conflict", "blocked")
        if request.operation == "inspect":
            return summary(record) if record is not None else {
                "state": "missing", "record_hash": None, "retry_authorized": False}
        if (digest(record) if record is not None else None) != request.expected_record_hash:
            raise GateError("operator_conflict", "blocked")
        if request.operation == "admission":
            if request.state == "open":
                dispatcher.policy.fresh()
                if not datetime.now(timezone.utc) < request.valid_until <= min(
                        dispatcher.policy.expires_at, datetime.now(timezone.utc) + timedelta(seconds=300)):
                    raise GateError("admission_lease_invalid", "blocked")
                await dispatcher.policy.signer.health()
            audit_hash = digest({"operator": identity.subject, "client": identity.client,
                                 "request": request.model_dump(mode="json"), "facts": facts})
            receipt = await dispatcher.audit(action, audit_hash, digest(location)[7:],
                                             "allow" if request.state == "open" else "deny",
                                             "admission_" + request.state)
            await authorize()
            if request.state == "open" and request.valid_until <= datetime.now(timezone.utc):
                raise GateError("admission_lease_invalid", "blocked")
            updated = {"state": request.state, "facts_hash": digest(facts),
                       "valid_until": request.valid_until.isoformat() if request.valid_until else None,
                       "operator": identity.subject, "operator_client": identity.client,
                       "reason_code": request.reason_code, "audit_receipt_id": receipt}
        else:
            if record["state"] in {"completed", "not_executed"}:
                return summary(record)
            if record["state"] != "pending" or not record.get("receipt_id"):
                raise GateError("outcome_unknown")
            dispatcher.policy.fresh()
            safe = dispatcher.safe_provider(deepcopy(facts))
            decision, arguments = await dispatcher.policy.evaluate("pre_tool_call", action, arguments, safe)
            if (decision == "deny" or digest({"facts": facts, "arguments": validated(
                    arguments, action.input_schema)}) != record["action_hash"]):
                raise GateError("recovery_context_changed", "blocked")
            async def guard():
                await authorize()
                dispatcher.policy.fresh()
                await dispatcher.policy.signer.health()
            proof = await dispatcher.downstream.recover(
                action=action, arguments=arguments, key=location[1],
                action_hash=record["action_hash"], provenance=record["receipt_id"],
                facts=facts, guard=guard)
            if proof["state"] not in {"completed", "not_executed"}:
                raise GateError("outcome_unknown")
            receipt = await dispatcher.audit(
                action, digest({"operator": identity.subject, "client": identity.client,
                                "record_hash": digest(record), "proof": proof}),
                location[1], "allow" if proof["state"] == "completed" else "deny",
                "recovery_" + proof["state"])
            await guard()
            updated = {**record, "state": proof["state"], "outcome_reference": proof["receipt_id"],
                       "operator_audit_receipt_id": receipt, "operator": identity.subject,
                       "recovery_evidence_hash": digest(proof)}
        if etag is None:
            await dispatcher.store.create(*location, updated)
        else:
            await dispatcher.store.replace(*location, updated, etag)
        # A lost CAS ACK is not success. Inspect/reconcile, never delete or reset.
        observed, _ = await dispatcher.store.read(*location)
        if observed != updated:
            raise GateError("operator_conflict", "blocked")
        return summary(observed)
    except Unauthorized:
        return {"status": "blocked", "reason_code": "operator_unauthorized"}
    except Conflict:
        return {"status": "blocked", "reason_code": "operator_conflict"}
    except GateError as exc:
        return {"status": exc.status, "reason_code": exc.reason}
    except Exception:
        return {"status": "unavailable", "reason_code": "operator_unavailable"}
