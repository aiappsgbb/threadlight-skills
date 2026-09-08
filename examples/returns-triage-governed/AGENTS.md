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
| `returns_get_case` | R | Cosmos case container (operator-seeded synthetic cases) | intake-validation, disposition-decision |
| `returns_list_open` | R | Cosmos case container | intake-validation |
| `customer_get_profile` | R | customer-profile (mock — `customers.json`) | fraud-escalation, policy-eligibility |
| `returns_apply_decision` | W | Cosmos conditional case + audit transaction | disposition-decision |

Knowledge: **Contoso Retail Return Policy** via **Foundry IQ** (citations mandatory).

## Orchestration (behavioral guidelines)

The agent orchestrates skills in order — there is no "orchestrator" skill:

1. Always start with **intake-validation**. Read the selected case, then its order,
   then its customer in the same invocation. Do not stop on missing information:
   check known authoritative risk before choosing a disposition. BR-003 requires
   supervisor escalation even when the reason or photos are missing.
2. Run **policy-eligibility** to get an `approve_candidate` / `deny_candidate`
   verdict with citations.
3. Run **fraud-escalation**. If any gate fires, the decision becomes
   `escalate_to_supervisor` regardless of eligibility or completeness (BR-003
   overrides BR-001 and BR-004). Ordinary incomplete cases with no known risk use
   `request_more_info` with a citation; unknown risk never authorizes a refund.
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

The native served agent uses the shared AGT 5 / ACS / Agent Hooks runtime. Only
`returns_apply_decision` is bound to `returns-write-v1` at `pre_tool_call`; read
tools remain unbound. SAFE Rego consumes ordered, host-owned backend read receipts
and a fresh Cosmos revision, never model-supplied evidence flags or roles.
Selected escalation requires the real Task8 authenticated supervisor flow.
No configured signature, identity, durable audit ACK, or fresh revision means no
write. See README for materialization and deployment inputs; the committed
manifest is an offline inventory, not production certification.

## Spec reference

This agent implements `specs/SPEC.md`. Every skill traces to a BR-XXX; every tool
maps to a § 5 / § 6 contract; mocked systems have sample data in
`specs/sample-data/`.
