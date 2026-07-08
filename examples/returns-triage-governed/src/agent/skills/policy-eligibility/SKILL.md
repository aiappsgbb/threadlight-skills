---
name: policy-eligibility
description: Check a return against Contoso Retail return policy — 30-day window, final-sale rules, item condition — and cite the governing clause. USE FOR deciding whether a return is eligible for refund. DO NOT USE FOR high-value/fraud gating (fraud-escalation) or writing the final decision (disposition-decision).
---

# Policy Eligibility

> Implements BR-001 (eligible-approve) and BR-002 (ineligible-deny), and BR-005
> (citation) for its verdicts.

## Operational contract
- **Inputs**: the consolidated case object from intake-validation.
- **Outputs**: an eligibility verdict `approve_candidate | deny_candidate` + the
  cited policy clause(s).
- **Deps**: Foundry IQ return-policy Knowledge Base (citations mandatory).
- **Idempotency**: pure function of inputs.
- **Failure behavior**: if the policy KB is unreachable → escalate (never guess).

## Procedure
1. Compute `days_since_delivery = requested_at − delivery_date`.
2. **BR-001** — if `days_since_delivery ≤ 30` AND `final_sale == false` AND
   `item_condition ∈ {unworn_tags_attached, unopened, defective}` →
   `approve_candidate`; cite the 30-day window + condition clause.
3. **BR-002** — if `final_sale == true` (changed-mind) OR
   (`days_since_delivery > 30` AND no qualifying override) → `deny_candidate`;
   cite the final-sale / window clause.
   - Exception: `arrived_damaged` / `defective` invokes statutory rights → route
     back to intake for evidence (BR-004) rather than a hard deny.
4. Always attach ≥ 1 citation (BR-005). Pass the verdict to fraud-escalation.

## Output schema
```json
{ "verdict": "approve_candidate | deny_candidate",
  "citations": ["policy#return-window", "policy#final-sale"],
  "rationale": "Delivered 2026-06-14, requested 2026-06-20 (6 days) — within window." }
```
