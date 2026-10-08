---
name: return-decision
description: Decide a return outcome for one Contoso Retail order using BR-001..BR-003. Use for return, refund or exchange requests.
---

# Return decision

1. Call `get_order` with the order id, then `get_customer` with its customer id.
2. Apply BR-001 (30-day window), BR-002 (value cap $150) and BR-003 (fraud flag).
3. Record the decision with `record_decision` and cite every BR that fired.
