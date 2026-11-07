# SpecKit: Returns Triage (synthetic fixture)

## 1. Process Overview

Fictional retailer return triage: recommend approve, deny or escalate with a policy citation.

## 3. Business Rules

### BR-001: Approve an eligible in-window return
- **Condition**: Delivered within 30 days and not final sale.
- **Action**: approve_refund, cite RP-2.1.

### BR-002: Escalate high-value cases
- **Condition**: Refund amount above EUR 250.
- **Action**: escalate_to_supervisor, cite RP-4.1.

## 6. Tool Contracts

### `returns_get_case`
- **Description**: fetch one return case by RMA id.
- **Inputs**:
  | Parameter | Type | Required | Description |
  |-----------|------|----------|-------------|
  | rma_id | string | yes | `RMA-NNN` |
- **Side Effects**: none
- **Backed by**: Returns case store (mock)

### `returns_list_open`
- **Description**: list open cases awaiting triage.
- **Inputs**:
  | Parameter | Type | Required | Description |
  |-----------|------|----------|-------------|
  | offset | int | no | default 0 |
  | limit | int | no | default 20 |
- **Side Effects**: none
- **Backed by**: Returns case store (mock)

### `customer_get_profile`
- **Description**: fetch loyalty tier and account status (email masked).
- **Inputs**:
  | Parameter | Type | Required | Description |
  |-----------|------|----------|-------------|
  | customer_id | string | yes | `CUST-NN` |
- **Side Effects**: none
- **Backed by**: Customer-profile service (mock)

### `returns_record_recommendation`
- **Description**: store the triage recommendation on the case. Never settles a refund.
- **Inputs**:
  | Parameter | Type | Required | Description |
  |-----------|------|----------|-------------|
  | rma_id | string | yes | Case id |
  | decision | string | yes | approve_refund, deny_refund or escalate_to_supervisor |
  | citations | array | yes | at least one RP clause id |
  | rationale | string | yes | short reason |
- **Side Effects**: sets the recommendation on the case
- **Backed by**: Returns case store (mock)

### `return_policy_search`
- **Description**: retrieve policy clauses with ids for citation.
- **Inputs**:
  | Parameter | Type | Required | Description |
  |-----------|------|----------|-------------|
  | query | string | yes | Natural-language question |
- **Side Effects**: none
- **Backed by**: Return-policy knowledge base (Foundry IQ)

## 9. Success Criteria

### Evaluation Scenarios

| ID | Scenario | Input | Expected Output | Business Rules | Category |
|----|----------|-------|-----------------|----------------|----------|
| S-001 | In-window jacket | Triage RMA-001 | approve_refund | BR-001 | happy-path |
| S-002 | Coat above EUR 250 | Triage RMA-002 | escalate_to_supervisor | BR-002 | approval |

## 11a. Runtime Governance Contract

```yaml
tools:
  - id: returns_record_recommendation
    consequence: write
```

## 11c. Tech Stack (Module selectors)

| Module | Selected? | Purpose in this process |
|--------|-----------|-------------------------|
| `cosmos-db` | no | Mock store is in memory |
| `ai-search` | yes | Foundry IQ backing for the return policy |
| `storage-blob` | yes | Policy corpus for the knowledge base |
| `app-insights` | yes | Telemetry |
| `aca-mcp` | yes | Mock returns server |
| `foundry-iq-index` | yes | Return-policy knowledge base |

## 11e. Workflow Model

```yaml
agent_type: hosted
workflow_model: agent
```
