# Tool Governance Fixture

## 6. Tool Contracts

### `inventory_read`
- **Description**: Read a synthetic inventory record.
- **Action class**: `read`
- **Decision**: `allow`
- **Enforcement point**: `mcp-server`
- **Policy ID**: `TG-FIXTURE-READ`
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`

### `external_notify`
- **Description**: Synthetic external notification canary; no real channel exists.
- **Action class**: `external-side-effect`
- **Decision**: `deny`
- **Enforcement point**: `mcp-server`
- **Policy ID**: `TG-FIXTURE-DENY`
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`

### `returns_apply_decision`
- **Description**: Apply a synthetic reversible return decision.
- **Action class**: `reversible-write`
- **Decision**: `conditional`
- **HITL gate ID**: `GATE-001`
- **Enforcement point**: `mcp-server`
- **Policy ID**: `TG-FIXTURE-HITL`
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`,
  `gate_id`, `approval_id`

## 7. Knowledge Sources

None.

## 8. Human Interaction Points

### Synthetic supervisor approval
- **Gate ID**: `GATE-001` (stable and unique within the SPEC)
- **Action gate**: `approve`
- **Approval propagation**: return `approval_id` with the original `correlation_id`

## 9. Success Criteria

- Fixture proves allow, deny, and conditional tool-governance outcomes.
