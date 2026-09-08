---
name: fraud-escalation
description: Apply the value ceiling and serial-returner / flagged-account risk gates and route cases to a human supervisor. USE FOR deciding whether a return must be escalated to the returns supervisor. DO NOT USE FOR the base eligibility check (policy-eligibility).
---

# Fraud & Escalation Gate

> Implements BR-003 (escalate high-value or high-risk). Overrides an
> `approve_candidate` from policy-eligibility when a gate fires.

## Operational contract
- **Inputs**: the case object, completeness verdict, and eligibility verdict if available.
- **Outputs**: `escalate | proceed` + a risk rationale.
- **Deps**: `customer_get_profile` fields (`lifetime_return_rate`, `account_status`).
- **Idempotency**: pure function of inputs.
- **Failure behavior**: unknown risk data is incomplete, never permission to finalize
  a refund or denial. Preserve known risk from the case/order even without a profile.

## Procedure
1. **BR-003 gates** — escalate if ANY:
   - `refund_amount > 250` (auto-approve ceiling), OR
   - `lifetime_return_rate ≥ 0.40` (serial-returner risk), OR
   - `account_status == review_flagged`, OR
   - `days_since_delivery > 30` AND a plausible override reason needs human judgement.
2. If a gate fires → `escalate_to_supervisor`, even with incomplete reason/photos or
   a missing customer profile; require the authenticated supervisor gate.
3. If no gate fires and evidence is incomplete → `request_more_info` (BR-004).
   After an earlier unanswered information request, use the supervisor gate.
   Otherwise → `proceed` (carry the eligibility verdict forward).
4. Emit the escalation reason for the audit record (BR-005).

## Output schema
```json
{ "decision": "escalate | proceed",
  "gates_fired": ["value_ceiling", "serial_returner"],
  "escalation": { "reason": "Refund $1,180 > $250 ceiling", "queue": "returns-supervisors" } }
```
