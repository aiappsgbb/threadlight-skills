# SpecKit: Governed Returns Decision (workshop governed write)

Purpose: a GHCP hosted agent triages synthetic return cases and records ONE
governed write, the return decision, through the governed tool gateway.

## 6. Tool Contracts

### `returns_get_case`
- **Description**: read one synthetic return case (amount, currency, eligibility, risk flag, reason code, current decision) and its exact Cosmos revision `_etag`.
- **Inputs**: `case_id: string` (required)
- **Side Effects**: none (read-only lookup)
- **Backed by**: returns business data (Cosmos), read by the MCP server

### `returns_apply_decision`
- **Description**: record the decision on a return case, conditional on the revision read with `returns_get_case`.
- **Inputs**: `case_id: string` (required); `expected_etag: string` (required, the `_etag` from `returns_get_case`); `decision: string` (required; `approve_refund`, `deny_refund`, `escalate_to_supervisor` or `request_more_info`); `reason: string` (required)
- **Side Effects**: records a return decision and its decision audit in Cosmos through the returns business API; never a payment or settlement.
- **Backed by**: returns business API, called only by the governed tool gateway

## 8. Human Steps

- **Supervisor approval**: when the policy escalates (a high-value or high-risk
  case handed to `escalate_to_supervisor`), a human Approver approves or
  rejects the pending action with `threadlight-review-action` (delegated
  `Governance.Approve`) before the gateway performs the write. A rejection
  means nothing is written.

## 11. Security, Compliance & Governance

### 11a. Runtime Governance Contract

- **Action mode**: `governed-write` (one write through the governed tool
  gateway; every other tool is an unbound read)
- **Policy rules** (`returns-write`, implemented in Rego in `governance/policy/governed_write.rego`; case facts come from the signed case snapshot, never from model arguments):
  - Rule 1 deny: any case that is not in the signed case snapshot.
  - Rule 2 deny: `approve_refund` on an ineligible case, or `deny_refund` on an eligible case.
  - Rule 3 escalate or deny: on a high-value (amount > 500) or high-risk case, `escalate_to_supervisor` escalates (a human Approver approves with `threadlight-review-action` before the write); any other decision is denied.
  - Rule 4 allow: ordinary decisions on ordinary cases are recorded autonomously.
- **Downstream service**: the returns business API. `POST /decisions` is
  idempotent (`Idempotency-Key`), re-checks the `_etag`, status and the same
  invariants, and writes the case and its audit record in one Cosmos
  transactional batch; `GET /outcomes` returns the recorded outcome. It
  authorizes only the gateway's downstream identity.
- **Governance foundation**: the shared control plane, governed tool gateway,
  Key Vault signing key, Cosmos approvals/audit and Blob signed catalog exist
  before deploy (deployed once by the facilitator). Approval is selected in the
  signed gateway registry (`approval_mode: deferred`, `approval_requirement:
  policy`): an allow stays autonomous and only an escalate waits for a person;
  the gateway gets a central audit ACK before the effect.

```yaml
framework: github-copilot-sdk

governance:
  mode: selective
  environment_modes:
    development: evaluate_only
    staging: evaluate_only
    preproduction: enforce
    production: enforce
  lifecycle_bindings: []

tools:
  - id: returns_get_case
    consequence: read
    policy_binding: none
    enforcement_path: none
    intervention_points: []
    safe_principles: [least-privilege]
    requires: []
  - id: returns_apply_decision
    consequence: write
    policy_binding: returns-write
    enforcement_path: governed-tool-gateway
    intervention_points: [pre_tool_call]
    safe_principles: [scope, audit]
    requires: [authorization, signed-policy-bundle, decision-receipt, idempotency-or-transaction]
```

## 13. Assumptions

- Synthetic cases only (`governance/cases.json`): per workshop set, `RMA-W0n-A`
  is ordinary and eligible (120 EUR), `RMA-W0n-B` ineligible (80 EUR) and
  `RMA-W0n-C` high value (750 EUR). A decided case is never reset.
- This contract binds one tool. It makes no claim about governance of the
  rest of the agent, and offline validation is not runtime or live evidence.
