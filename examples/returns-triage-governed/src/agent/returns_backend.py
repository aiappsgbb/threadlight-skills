"""Cosmos is the case/audit authority; immutable JSON represents mock OMS/CRM reads.

The container must be provisioned with partition key /case_id and seeded by an
operator. Runtime never seeds, resets, settles money, or writes local case files.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from runtime import TrustedContextSnapshot, record_trusted_read, trusted_effect_snapshot
from runtime.evidence import digest


class ReturnsBackend:
    def __init__(self, *, container, samples):
        self.container = container
        self.samples = Path(samples)
        self.orders = self._samples("orders")
        self.customers = self._samples("customers")
        self.case_scope = tuple(self._samples("returns"))

    def _samples(self, entity):
        records = json.loads((self.samples / f"{entity}.json").read_text())["records"]
        return {record["id"]: record for record in records}

    async def get_case(self, rma_id):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        try:
            result = await self.container.read_item(item=rma_id, partition_key=rma_id)
        except CosmosResourceNotFoundError:
            result = {"not_found": rma_id}
        record_trusted_read("returns_get_case", result)
        return deepcopy(result)

    def get_order(self, order_id):
        result = deepcopy(self.orders.get(order_id, {"not_found": order_id}))
        record_trusted_read("oms_get_order", result)
        return result

    def get_customer(self, customer_id):
        result = deepcopy(self.customers.get(customer_id, {"not_found": customer_id}))
        record_trusted_read("customer_get_profile", result)
        return result

    async def list_open(self, offset=0, limit=20):
        query = ("SELECT * FROM c WHERE c.kind = 'case' AND c.status = 'in_triage' "
                 "ORDER BY c.id OFFSET @offset LIMIT @limit")
        return [row async for row in self.container.query_items(
            query=query, parameters=[{"name": "@offset", "value": offset},
                                     {"name": "@limit", "value": min(limit, 100)}])]

    async def trusted_context(self, identity, tool_call, reads):
        # No arguments other than the selected identifier select the backend
        # snapshot; all model copies/booleans/roles are excluded from authority.
        rma_id = tool_call["args"].get("rma_id")
        facts = {"allowed_cases": list(self.case_scope)}
        if rma_id in self.case_scope:
            case = await self.container.read_item(item=rma_id, partition_key=rma_id)
            order = deepcopy(self.orders.get(case["order_id"], {"not_found": case["order_id"]}))
            customer = deepcopy(self.customers.get(case["customer_id"], {"not_found": case["customer_id"]}))
            days = None
            if order.get("delivery_date"):
                days = (date.fromisoformat(case["requested_at"]) -
                        date.fromisoformat(order["delivery_date"])).days
            facts.update(case=case, order=order, customer=customer, days_since_delivery=days,
                         evidence_digest=digest([case, order, customer]))
            if case.get("audit_id"):
                facts["audit"] = await self.container.read_item(
                    item=case["audit_id"], partition_key=rma_id)
        return TrustedContextSnapshot(
            facts=facts, expires_at=datetime.now(timezone.utc) + timedelta(seconds=20))

    async def apply_decision(self, **arguments):
        authorized = trusted_effect_snapshot()
        facts, identity = authorized["facts"], authorized["identity"]
        # This is a consistency boundary, not an alternative policy decision.
        if authorized["tool_call"]["args"] != arguments:
            raise ValueError("authorized_target_changed")
        case = deepcopy(facts["case"])
        existing = facts.get("audit", {})
        if all(existing.get(key) == value for key, value in arguments.items()):
            return {"ok": True, "audit_id": existing["id"]}
        if datetime.now(timezone.utc) >= datetime.fromisoformat(authorized["expires_at"]):
            raise ValueError("backend_snapshot_expired")
        etag = case.pop("_etag")
        if not isinstance(etag, str) or not etag:
            raise ValueError("backend_revision_required")
        for key in list(case):
            if key.startswith("_"):
                del case[key]
        audit_id = "decision-" + digest([case["id"], arguments])[7:]
        case.update(decision=arguments["decision"], disposition=arguments["disposition"],
                    status="escalated" if arguments["decision"] == "escalate_to_supervisor"
                    else "in_triage" if arguments["decision"] == "request_more_info" else "closed",
                    audit_id=audit_id)
        if arguments["decision"] == "request_more_info":
            case["info_requests"] = case.get("info_requests", 0) + 1
        rule = {"approve_refund": "BR-001", "deny_refund": "BR-002",
                "escalate_to_supervisor": "BR-003", "request_more_info": "BR-004"}[arguments["decision"]]
        audit = {
            "id": audit_id, "case_id": case["id"], "kind": "decision-audit",
            **arguments, **identity, "evidence_digest": facts["evidence_digest"],
            "case_revision": etag, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "decision_trace": {"outcome": arguments["decision"], "business_rules_fired": [rule, "BR-005"]},
            "answer": {"citations": arguments["citations"]}, "actor": identity["principal"],
        }
        await self.container.execute_item_batch(
            batch_operations=[
                ("replace", (case["id"], case), {"if_match_etag": etag}),
                ("create", (audit,), {}),
            ], partition_key=case["id"])
        return {"ok": True, "audit_id": audit_id}
