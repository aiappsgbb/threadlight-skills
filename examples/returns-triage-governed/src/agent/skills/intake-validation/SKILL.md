---
name: intake-validation
description: Correlate a return case with its order and customer profile, and validate completeness. USE FOR intake of a new return, matching an RMA to an order, checking required fields/photos. DO NOT USE FOR the eligibility or refund decision (that is policy-eligibility / disposition-decision).
---

# Intake & Validation

> Implements BR-004 (partial: completeness gate) and feeds BR-001/002/003.

## Operational contract
- **Inputs**: an RMA id or an order id.
- **Outputs**: a consolidated case object `{ return, order, customer }`, or a
  `request_more_info` verdict with the list of missing items.
- **Deps**: tools `returns_get_case`, `oms_get_order`, `customer_get_profile`.
- **Idempotency**: read-only; safe to re-run.
- **Failure behavior**: if any of the three fetches fails after retries → mark the
  case `system_error` and hand to fraud-escalation for escalation.

## Procedure
1. Resolve the case: if given an RMA, call `returns_get_case`; if given an order id,
   call `returns_list_open` and match.
2. Fetch the originating order via `oms_get_order(return.order_id)`.
   - If `not_found` → emit `request_more_info` ("order could not be matched").
3. Fetch the customer via `customer_get_profile(return.customer_id)`.
4. Completeness check (BR-004):
   - `reason_code` present?
   - if `reason_code == arrived_damaged` → `photos_provided == true`?
   - order line matched to the returned SKU?
   - Any failure → emit `request_more_info` with the exact missing field(s).
5. Otherwise emit the consolidated case object and pass to policy-eligibility.

## Output schema
```json
{ "verdict": "complete | request_more_info",
  "missing": ["reason_code"],
  "case": { "return": {}, "order": {}, "customer": {} } }
```
