# SpecKit: Order Returns Agent

## 1. Process Overview

Contoso Retail return triage: decide refund, exchange or human review.

## 3. Business Rules

### BR-001: Return window
- **Condition**: Return requested more than 30 days after delivery.
- **Action**: Deny the return.

### BR-002: Auto-refund value cap
- **Condition**: Order value above $150.
- **Action**: Send to human review.

### BR-003: Fraud flag
- **Condition**: Customer has `fraud_flag: true`.
- **Action**: Send to human review regardless of value.

## 6. Tool Contracts

| Tool | Input contract | Output contract | Idempotency / failure behavior |
|---|---|---|---|
| `get_order` | `order_id` | order with value, delivered_days_ago, customer_id | read-only; `ORDER_NOT_FOUND` |
| `get_customer` | `customer_id` | tier, fraud_flag, returns_90d | read-only; `CUSTOMER_NOT_FOUND` |
| `list_customer_orders` | `customer_id`, `order_ids[]` | matching orders | read-only |
| `record_decision` | `order_id`, `decision`, `rationale` | decision id, status | idempotent on `order_id` |

## 9. Success Criteria

### Functional
- [ ] Every decision cites the business rule (BR-XXX) that fired.
- [ ] Orders above the value cap or with a fraud flag are never auto-refunded.

### Evaluation Scenarios

| ID | Scenario | Input | Expected Output | Business Rules | Category |
|----|----------|-------|-----------------|----------------|----------|
| S-001 | Order ORD-1001, day 10, $89.99, clean customer | Return ORD-1001, item damaged | AUTO_REFUND | BR-001, BR-002 | happy-path |
| S-002 | Order ORD-1002, day 45 | Return ORD-1002, changed mind | DENY | BR-001 | edge-case |
| S-003 | Order ORD-1003, $420, fraud-flagged customer | Return ORD-1003, not as described | HUMAN_REVIEW | BR-002, BR-003 | approval |

## 11e. Workflow Model

```yaml
agent_type: hosted
trivial_justification: null
```
