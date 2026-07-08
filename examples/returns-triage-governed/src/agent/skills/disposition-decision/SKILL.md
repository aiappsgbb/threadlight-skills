---
name: disposition-decision
description: Emit the final triage decision (approve_refund / deny_refund / escalate_to_supervisor / request_more_info), recommend a warehouse disposition, and write the audit record. USE FOR producing and persisting the final outcome. DO NOT USE FOR gathering data (intake-validation) or the raw eligibility/risk checks.
---

# Disposition & Decision

> Implements the terminal decision + BR-005 (cite + audit). Consumes verdicts from
> policy-eligibility and fraud-escalation.

## Operational contract
- **Inputs**: eligibility verdict + escalation verdict + the case object.
- **Outputs**: one of the four decisions + recommended disposition + audit record;
  persisted via `returns_apply_decision`.
- **Deps**: tool `returns_apply_decision` (idempotent on `rma_id`).
- **Idempotency**: writing the same decision for the same `rma_id` is a no-op.
- **Failure behavior**: if the write fails after retries → surface to the CS agent;
  do not silently drop.

## Procedure
1. Resolve the terminal decision:
   - completeness verdict `request_more_info` → **`request_more_info`** (BR-004)
   - else escalation `escalate` → **`escalate_to_supervisor`** (BR-003)
   - else eligibility `approve_candidate` → **`approve_refund`** (BR-001)
   - else eligibility `deny_candidate` → **`deny_refund`** (BR-002)
2. Recommend disposition:
   - approve + `defective` → `liquidation`; approve + resellable → `restock_a`;
     approve + opened/used-but-ok → `restock_b`; deny → `return_to_customer`.
3. **BR-005** — assemble the audit record: `decision.outcome`,
   `decision.business_rules_fired`, `answer.citations[]`, actor, timestamp,
   `escalation.reason` (if any), `disposition.recommended`.
4. Persist with `returns_apply_decision(rma_id, decision, disposition, citations, rationale)`.
5. On `escalate_to_supervisor`, hand the case to the supervisor gate (§ 8) — the
   agent does not finalize an escalated refund itself.

## Output schema
```json
{ "decision": "approve_refund",
  "disposition": "restock_a",
  "business_rules_fired": ["BR-001", "BR-005"],
  "citations": ["policy#return-window"],
  "rationale": "In-window (6 days), not final-sale, unworn — approve.",
  "audit_id": "AUD-..." }
```
