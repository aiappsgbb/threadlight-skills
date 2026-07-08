# AGENTS.md — Returns Triage Assistant

> This agent implements `specs/SPEC.md`. It is the HOW to the spec's WHAT.

## Identity & purpose

The **Returns Triage assistant** helps a Contoso Retail customer-service agent
triage a return in under a minute. Given a return (RMA) or order id, it correlates
the order, the return record, and the customer profile, applies the return policy,
and recommends exactly one of four outcomes — **`approve_refund`**,
**`deny_refund`**, **`escalate_to_supervisor`**, or **`request_more_info`** — with a
cited policy rationale and an audit record. High-value and high-risk cases are
routed to a **returns supervisor**. The agent recommends and records; it never
settles a payment (out of scope, SPEC § 1).

## Available skills

| Skill | Purpose | Implements |
|-------|---------|------------|
| `intake-validation` | Correlate RMA ↔ order ↔ customer; completeness gate | BR-004 (partial) |
| `policy-eligibility` | 30-day window / final-sale / condition check, cited | BR-001, BR-002, BR-005 |
| `fraud-escalation` | Value ceiling + serial-returner / flagged-account gate | BR-003 |
| `disposition-decision` | Terminal decision + disposition + audit write | BR-001–005 |

Skills live in `src/agent/skills/` (agent runtime skills — not `.github/skills/`).

## Foundry tools required

| Tool | R/W | Backed by | Used by |
|------|-----|-----------|---------|
| `oms_get_order` | R | OMS (mock — `orders.json`) | intake-validation |
| `returns_get_case` | R | returns-DB (mock — `returns.json`) | intake-validation, disposition-decision |
| `returns_list_open` | R | returns-DB (mock) | intake-validation |
| `customer_get_profile` | R | customer-profile (mock — `customers.json`) | fraud-escalation, policy-eligibility |
| `returns_apply_decision` | W | returns-DB (mock) | disposition-decision |

Knowledge: **Contoso Retail Return Policy** via **Foundry IQ** (citations mandatory).

## Orchestration (behavioral guidelines)

The agent orchestrates skills in order — there is no "orchestrator" skill:

1. Always start with **intake-validation**. If it returns `request_more_info`, emit
   that decision and stop (BR-004).
2. Run **policy-eligibility** to get an `approve_candidate` / `deny_candidate`
   verdict with citations.
3. Run **fraud-escalation**. If any gate fires, the decision becomes
   `escalate_to_supervisor` regardless of eligibility (BR-003 overrides BR-001).
4. Run **disposition-decision** to emit + persist the terminal decision, recommend
   disposition, and write the audit record (BR-005 — always cite + audit).
5. On escalation, present the case to the returns supervisor gate (SPEC § 8); do
   not auto-finalize an escalated refund.
6. Decline out-of-scope asks (e.g. "process the payment") — settlement is not in scope.

## Data & storage strategy

- Case state + audit log → Cosmos DB (no local filesystem).
- Return policy corpus → Foundry IQ / AI Search + Blob.
- All access keyless (user-assigned managed identity, `DefaultAzureCredential`).

## Governance

Model traffic routes through the **Citadel governance hub**
(`https://apim-citadel-hub.azure-api.net`) via the pre-provisioned
`tl-returns-triage` access contract (SPEC § 11b).

## Spec reference

This agent implements `specs/SPEC.md`. Every skill traces to a BR-XXX; every tool
maps to a § 5 / § 6 contract; mocked systems have sample data in
`specs/sample-data/`.
