# SpecKit: Equipment Booking Agent

## 1. Process Overview

Fabrikam Makerspace equipment booking check: verify a member, check equipment
availability for the requested window and summarise the booking request.

## 3. Business Rules

### BR-001: Active membership
- **Condition**: Member status is not `active`.
- **Action**: Reject the booking request.

### BR-002: Slot availability
- **Condition**: The requested equipment slot is not `available`.
- **Action**: Propose the next available slot.

### BR-003: Request ownership
- **Condition**: The booking request belongs to a different member.
- **Action**: Escalate to a human coordinator.

## 5. Integration Architecture

### 5b. Systems

### System: Synthetic booking system

| Tool name | Read/Write | Latency budget | Notes |
|---|---|---|---|
| `get_member` | R | 500 ms | member lookup |
| `verify_equipment_availability` | R | 800 ms | slot lookup |
| `get_booking_request` | R | 500 ms | request lookup |

## 6. Tool Contracts

| Tool | Input contract | Output contract | Business Rules |
|---|---|---|---|
| `get_member` | none / see below | member record | BR-001 |
| `verify_equipment_availability` | none / see below | slot record | BR-002 |
| `get_booking_request` | none / see below | request record | BR-003 |

### `get_member` — BR-001

- **Description**: Look up a makerspace member by id.
- **Inputs**: `member_id: string` required, exact `MEM-###`.
- **Outputs**: member record with status and tier.

### `verify_equipment_availability` — BR-002

- **Description**: Check whether an equipment type is free in a time window.
- **Inputs**:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `equipment_type` | string | yes | Equipment category, e.g. `laser_cutter` |
| `start_at` | datetime | yes | ISO-8601 window start |
| `end_at` | datetime | yes | ISO-8601 window end |

- **Outputs**: slot record with availability.

### `get_booking_request` — BR-003

- **Description**: Fetch a booking request by id.
- **Inputs**: `request_id: string` required.
- **Outputs**: booking request with member and equipment.

## 9. Success Criteria

### Evaluation Scenarios

| ID | Scenario | Input | Expected Output | Business Rules | Category |
|----|----------|-------|-----------------|----------------|----------|
| S-001 | Active member, free slot | Check request REQ-001 for MEM-001 | APPROVE | BR-001, BR-002 | happy-path |
| S-002 | Suspended member | Check request REQ-002 for MEM-002 | REJECT | BR-001 | edge-case |

## 11. Agent Design

### 11a. Tool consequence classification

```yaml
tools:
  - id: get_member
    consequence: read
  - id: verify_equipment_availability
    consequence: read
  - id: get_booking_request
    consequence: read
```

## 11e. Workflow Model

```yaml
agent_type: hosted
trivial_justification: null
```
