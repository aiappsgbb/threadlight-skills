# SpecKit: Returns Triage

> Generated: 2026-07-07
> Status: draft
> Mode: **Fast-PoC** (neutral demo defaults — see § 13)

## 1. Process Overview

**Name**: Returns Triage
**Domain**: Retail / CPG (reverse logistics)
**Description**: When a customer requests a return, the AI assistant pulls the
originating order, the return record, and the customer profile, then triages the
case against Contoso Retail's return policy. It recommends one of four outcomes —
approve refund, deny refund, escalate to a returns supervisor, or request more
information — with a cited policy rationale and an audit record. A customer-service
agent runs the assistant; a returns supervisor handles escalations.
**Target Persona**: Mixed (COO / customer-service ops lead + CISO for audit).

### Audience & Customer Context

- **`audience_mode`**: `external-demo` *(defaulted-after-skip — Fast-PoC; see § 13)*

### Customer / Org

- **`customer.name`**: Contoso Retail
- **`customer.region`**: EU
- **`customer.brand_palette`**:
  - **primary**: Threadlight neutral *(defaulted-after-skip)*
  - **secondary**: (empty)
  - **logo_url**: (none)

### Goals
- **Primary**: Cut return-decision cycle time from hours of manual lookup to a
  sub-minute, policy-cited recommendation the CS agent can action or escalate.
- **Secondary**: Make every decision auditable (policy citation + rationale +
  who/what decided), and surface fraud/serial-returner risk automatically.

### Scope
- **In scope**: Triage of a single return case; the four decisions
  (`approve_refund`, `deny_refund`, `escalate_to_supervisor`, `request_more_info`);
  reads against OMS, returns-DB, and customer-profile (all mocked); the
  supervisor escalation gate.
- **Out of scope**: Executing the actual refund/payment settlement, generating
  shipping labels/RMAs downstream, warehouse disposition beyond a recommendation,
  and chargeback/dispute handling.

### Participants
| Role | Type | Description |
|------|------|-------------|
| Customer-service agent | human | Runs the assistant on a live return case; actions the recommendation or forwards escalations |
| Returns supervisor | human | Reviews escalated cases (high-value, window-lapsed, fraud-flagged) and makes the final call |
| Returns Triage assistant | agent | The AI agent that gathers data, applies policy, and recommends a decision |
| Order-management system | system | Source of order + delivery data (mock) |
| Returns database | system | Source of return-case data; receives the applied decision (mock) |
| Customer-profile service | system | Source of loyalty tier + lifetime return-rate (mock) |

---

## 2. Process Flow

### Steps

#### Step 1: Intake & correlation
- **Actor**: agent
- **Input**: a return id (RMA) or an order id from the CS agent
- **Action**: fetch the return case (returns-DB), the originating order (OMS), and
  the customer profile (customer-profile service); correlate them into one view.
- **Output**: a consolidated case object; a `request_more_info` branch if the
  order can't be matched or a required field is missing.
- **Decision branch**: missing order match / reason code / required photos →
  **BR-004** → `request_more_info`.

#### Step 2: Eligibility check
- **Actor**: agent
- **Input**: consolidated case object + return policy
- **Action**: compute days-since-delivery vs the 30-day window; check final-sale
  flag and item condition.
- **Output**: eligibility verdict + the policy clause cited.
- **Decision branch**: in-window + not final-sale + acceptable condition →
  candidate **approve** (BR-001); final-sale or window-lapsed with no override →
  candidate **deny/escalate** (BR-002).

#### Step 3: Risk & value gate
- **Actor**: agent
- **Input**: refund amount + customer lifetime return rate
- **Action**: apply the $250 auto-approve ceiling and the serial-returner threshold.
- **Output**: whether the case must go to a human.
- **Decision branch**: refund > $250 OR lifetime return rate ≥ 0.40 → **BR-003** →
  `escalate_to_supervisor`.

#### Step 4: Decision & audit
- **Actor**: agent → human (on escalation)
- **Input**: the verdicts from steps 2–3
- **Action**: emit one of the four decisions with a cited rationale, recommended
  disposition, and an audit record; on escalation, present the case to the
  returns supervisor for signoff (BR-005 always fires).
- **Output**: `decision`, `disposition`, `citations[]`, `audit` record written
  back to the returns-DB.

---

## 3. Business Rules

### BR-001: Approve in-window eligible return
- **Condition**: `days_since_delivery ≤ 30` AND `final_sale == false` AND
  `item_condition ∈ {unworn_tags_attached, unopened, defective}` AND
  `refund_amount ≤ 250` AND `lifetime_return_rate < 0.40`.
- **Action**: `approve_refund`; recommend disposition `restock_a` (or
  `liquidation` if defective).
- **Exception**: any risk/value gate in BR-003 overrides to escalation.
- **KPI**: `auto_approval_rate`, `return_cycle_time_business_days_p50`.

### BR-002: Deny ineligible return
- **Condition**: `final_sale == true` (changed-mind reason) OR
  (`days_since_delivery > 30` AND no qualifying override such as `arrived_damaged`
  or `defective`).
- **Action**: `deny_refund` with the cited policy clause; recommend disposition
  `return_to_customer`.
- **Exception**: a damaged/defective item within statutory rights routes to
  BR-004 (request evidence) rather than an outright deny.
- **KPI**: `deny_rate`, `policy_citation_rate`.

### BR-003: Escalate high-value or high-risk cases
- **Condition**: `refund_amount > 250` OR `lifetime_return_rate ≥ 0.40` OR
  `account_status == review_flagged` OR (`days_since_delivery > 30` AND a plausible
  override reason exists that needs human judgement).
- **Action**: `escalate_to_supervisor` with a summarized risk rationale.
- **Exception**: none — the human gate is mandatory when this fires.
- **KPI**: `human_escalation_rate`.

### BR-004: Request more information on incomplete cases
- **Condition**: order cannot be matched OR `reason_code` missing OR
  (`reason_code == arrived_damaged` AND `photos_provided == false`).
- **Action**: `request_more_info` with a templated list of exactly what's needed.
- **Exception**: if info is still missing after one request cycle → escalate.
- **KPI**: `first_touch_resolution_pct`, `info_request_rate`.

### BR-005: Cite policy and write an audit record on every decision
- **Condition**: always (every case, every branch).
- **Action**: attach ≥ 1 policy citation + a plain-language rationale + a
  structured audit record (`decision.outcome`, `decision.business_rules_fired`,
  `answer.citations[]`, actor, timestamp).
- **Exception**: none.
- **KPI**: `policy_citation_rate` (target 100%).

---

## 4. Data Models

### orders
| Field | Type | Notes | System of record |
|-------|------|-------|------------------|
| id | string | `ORD-YYYY-NNNNNN` | OMS (mock) |
| customer_id | string | FK → customers.id | OMS |
| status | enum | `shipped` \| `delivered` \| `cancelled` | OMS |
| channel | enum | `online` \| `store` | OMS |
| order_date | date | ISO-8601 | OMS |
| delivery_date | date\|null | null until delivered | OMS |
| currency | string | ISO-4217 | OMS |
| order_total | number | order subtotal | OMS |
| items | array | `{sku, brand, name, category, qty, unit_price, final_sale}` | OMS |

### returns
| Field | Type | Notes | System of record |
|-------|------|-------|------------------|
| id | string | `RMA-YYYY-NNNNNN` | returns-DB (mock) |
| order_id | string | FK → orders.id | returns-DB |
| customer_id | string | FK → customers.id | returns-DB |
| status | enum | `in_triage` \| `escalated` \| `closed` | returns-DB |
| reason_code | enum | `fit_too_small` \| `wrong_size` \| `changed_mind` \| `arrived_damaged` \| `not_as_described` \| `defective` | returns-DB |
| requested_at | date | ISO-8601 | returns-DB |
| refund_amount | number | requested refund | returns-DB |
| currency | string | ISO-4217 | returns-DB |
| item_condition | enum | `unworn_tags_attached` \| `unopened` \| `opened` \| `used` \| `damaged` \| `defective` | returns-DB |
| photos_provided | bool | evidence attached? | returns-DB |
| final_sale | bool | denormalized from order line | returns-DB |
| disposition | enum\|null | `restock_a` \| `restock_b` \| `liquidation` \| `return_to_customer` \| `destroy` | returns-DB |
| decision | enum\|null | `approve_refund` \| `deny_refund` \| `escalate_to_supervisor` \| `request_more_info` | returns-DB |

### customers
| Field | Type | Notes | System of record |
|-------|------|-------|------------------|
| id | string | `CR-CUST-NNNNN` | customer-profile (mock) |
| name | string | synthetic | customer-profile |
| email_masked | string | masked PII | customer-profile |
| region | enum | `EU` (single-region PoC) | customer-profile |
| loyalty_tier | enum | `bronze` \| `silver` \| `gold` \| `platinum` | customer-profile |
| account_status | enum | `active` \| `review_flagged` \| `closed` | customer-profile |
| lifetime_orders | int | count | customer-profile |
| lifetime_return_rate | number | 0–1; ≥ 0.40 flags serial-returner | customer-profile |
| tenure_months | int | account age | customer-profile |

---

## 5. System Integrations

### Order-management system (OMS)
- **Direction**: read
- **Auth**: managed identity (real) / none (mock)
- **Availability**: **mock** — no PoC access; backed by `specs/sample-data/orders.json`

### Returns database
- **Direction**: read + write (applies the decision)
- **Auth**: managed identity (real) / none (mock)
- **Availability**: **mock** — backed by `specs/sample-data/returns.json`

### Customer-profile service
- **Direction**: read
- **Auth**: managed identity (real) / none (mock)
- **Availability**: **mock** — backed by `specs/sample-data/customers.json`

---

## 5b. External Systems & Mocks (MCP contract)

### Returns Triage MCP server
- **MCP server**: `mock` (FastMCP backed by `specs/sample-data/*.json`)
- **Endpoint shape**:
  - Style: `request-response`
  - Pagination: `offset-limit` (for `returns_list_open`)
  - Idempotency key field: `returns.id` (for `returns_apply_decision`)
- **Tools exposed**:
  | Tool name | Read/Write | Latency budget | Notes |
  |-----------|-----------|----------------|-------|
  | `oms_get_order` | R | <100ms | Backed by `orders.json` |
  | `returns_get_case` | R | <100ms | Backed by `returns.json` |
  | `returns_list_open` | R | <150ms | Filters `status == in_triage` |
  | `customer_get_profile` | R | <100ms | Backed by `customers.json` |
  | `returns_apply_decision` | W | <150ms | Writes `decision`+`disposition`+audit; idempotent on `returns.id` |
- **Mock data scale**: `orders: 10, returns: 8, customers: 8` (narrative scale;
  `threadlight-demo-data-factory` scales to executive volume on demand).
- **Reset semantics**: `idempotent` (re-seed wipes + repopulates from JSON in <30s).
- **Demo state machine**: `pristine (all in_triage) → decisions-applied → reset`.
- **Real-system swap notes**: replace each mock tool with the customer's OMS /
  returns / CRM endpoint; schema in § 4 is the contract, auth becomes managed
  identity, base URLs move to `mcp-config.json`.

---

## 6. Tool Contracts

### oms_get_order
- **Description**: Fetch an order + delivery date + line items by order id.
- **Used by**: intake-validation
- **Inputs**: `order_id: string (required)`
- **Output Schema**: `orders` object (§ 4)
- **Side Effects**: none
- **Error Cases**: `not_found` → drives BR-004 `request_more_info`
- **Backed by**: OMS (mock)

### returns_get_case / returns_list_open
- **Description**: Fetch a single return case, or list open (`in_triage`) cases.
- **Used by**: intake-validation, disposition-decision
- **Inputs**: `rma_id: string (required)` / `offset,limit`
- **Output Schema**: `returns` object(s) (§ 4)
- **Error Cases**: `not_found` → `request_more_info`
- **Backed by**: returns-DB (mock)

### customer_get_profile
- **Description**: Fetch loyalty tier + lifetime return rate + account status.
- **Used by**: fraud-escalation, policy-eligibility
- **Inputs**: `customer_id: string (required)`
- **Output Schema**: `customers` object (§ 4)
- **Backed by**: customer-profile (mock)

### returns_apply_decision
- **Description**: Persist the triage outcome + disposition + audit record.
- **Used by**: disposition-decision
- **Inputs**: `rma_id, decision, disposition, citations[], rationale`
- **Output Schema**: `{ ok: bool, audit_id: string }`
- **Side Effects**: writes to returns-DB; **idempotent** on `rma_id`
- **Backed by**: returns-DB (mock)

---

## 7. Knowledge Sources

### Contoso Retail Return Policy
- **Type**: document
- **Content**: return window (30 days), final-sale rules, condition grades,
  damaged/defective statutory rights, auto-approve ceiling, escalation triggers,
  disposition matrix.
- **Format**: PDF/Markdown (synthesized for the PoC)
- **Update Frequency**: quarterly
- **Backing service**: `foundry-iq` (agentic retrieval + citations)
- **Reasoning effort**: `low`
- **Citation requirement**: `mandatory` — BR-005 requires ≥ 1 clause per decision.

---

## 7b. AI Services & Model Selection

### Chat reasoning (triage)
- **Capability type**: `chat-reasoning`
- **Service**: Azure OpenAI
- **Model + version**: `gpt-5.4` (2026-03-05) — house default for multi-skill pilots
- **Capacity (TPM)**: `50K` GlobalStandard
- **Region**: Sweden Central (primary) / West Europe (fallback) — EU boundary
- **Reasoning effort**: `medium`

*(Source for all rows: `defaulted-after-skip` — Fast-PoC, see § 13.)*

---

## 8. Human Interaction Points

### Supervisor escalation review
- **Trigger**: BR-003 fires (refund > $250, serial-returner ≥ 0.40, flagged
  account, or window-lapsed with a judgement-call override).
- **Actor**: Returns supervisor
- **Channel**: Teams adaptive card / workspace queue
- **Data Presented**: consolidated case, agent's risk rationale, policy citations,
  refund amount, customer return history.
- **Options**: approve refund, deny refund, request more info, uphold escalation.
- **Timeout/SLA**: manual review < 24h.
- **Action gate**: `escalate` → then `edit-and-approve` on the supervisor's side.
- **Linked business rules**: BR-003, BR-005.

### Info request to customer
- **Trigger**: BR-004 fires.
- **Actor**: Customer-service agent (sends), customer (responds).
- **Channel**: templated email / chat composer.
- **Data Presented**: exactly which fields/evidence are missing.
- **Options**: `request-info`.
- **Timeout/SLA**: 5 business days to respond before auto-escalation.
- **Action gate**: `request-info`.
- **Linked business rules**: BR-004, BR-005.

---

## 8b. Human Interaction (Workspace UX)

- **Workspace shape**: `case-list` (queue of open returns, click into detail)
- **Primary filters**: status, refund-amount band, age (days since request),
  risk flag (serial-returner), reason_code
- **Detail pane sections**: summary card → correlated order/customer → agent
  reasoning trace → policy citations → decision + disposition → action toolbar →
  audit viewer
- **Action toolbar**: `approve` · `deny` · `escalate` · `request-info`
- **Audit viewer**: `drawer` (open on demand)
- **Bulk operations**: assign-to-me, batch-escalate

---

## 9. Success Criteria

### Functional
- [ ] In-window, eligible, sub-ceiling returns are recommended `approve_refund` with a citation (BR-001)
- [ ] Final-sale / window-lapsed returns are recommended `deny_refund` with a citation (BR-002)
- [ ] Refunds > $250 or serial-returner cases are `escalate_to_supervisor` (BR-003)
- [ ] Incomplete / unmatched / photo-less damage cases are `request_more_info` (BR-004)
- [ ] Every decision carries ≥ 1 policy citation + audit record (BR-005)

### Performance
- Automated disposition decision **< 60s** per case; open-queue list **< 2s**.

### Quality
- `policy_citation_rate` = 100%; escalation precision high enough that supervisors
  overturn < 15% of auto-approvals.

### Evaluation Scenarios

| ID | Scenario | Input | Expected Output | Business Rules | Category |
|----|----------|-------|-----------------|----------------|----------|
| S-001 | In-window fit return | RMA-2026-004410 | `approve_refund`, cite 30-day window | BR-001, BR-005 | happy-path |
| S-002 | Boundary — refund exactly at ceiling | refund = $250, in-window | `approve_refund` (≤ ceiling) | BR-001, BR-003 | edge-case |
| S-003 | Final-sale changed-mind | RMA-2026-004418 | `deny_refund`, cite final-sale clause | BR-002, BR-005 | happy-path |
| S-004 | High-value refund | RMA-2026-004425 ($1,180) | `escalate_to_supervisor` | BR-003, BR-005 | approval |
| S-005 | Window lapsed, no override | RMA-2026-004431 (86 days) | `escalate_to_supervisor` | BR-002, BR-003 | edge-case |
| S-006 | Damage claim, no photos | RMA-2026-004440 | `request_more_info` (ask for photos) | BR-004, BR-005 | error |
| S-007 | Serial returner | RMA-2026-004452 (return rate 0.63) | `escalate_to_supervisor` | BR-003, BR-005 | approval |
| S-008 | Unmatched order | unknown order id | `request_more_info` | BR-004 | error |
| S-009 | Out-of-scope ask | "process the payment now" | decline — settlement is out of scope | — | error |

### Business KPIs (BR → KPI mapping)

| BR | KPI Name | Formula (computable from traces) | Target | Alert threshold |
|----|----------|-----------------------------------|--------|-----------------|
| BR-001 | `return_cycle_time_business_days_p50` | `p50(refund_ts − request_ts)` | < 5 business days | > 5 |
| BR-001 | `restock_a_pct` | `count(disposition=restock_a)/count(approved)` | 60–80% (apparel) | < 55% |
| BR-002 | `deny_rate` | `count(deny)/count(decisions)` | industry-realistic band | off-baseline |
| BR-003 | `human_escalation_rate` | `count(escalated)/count(decisions)` | 8–15% | > 25% |
| BR-005 | `policy_citation_rate` | `count(answers where citations≥1)/count(answers)` | 100% | < 95% |

> Trace fields the agent must emit: `decision.outcome`,
> `decision.business_rules_fired`, `answer.citations[]`, `escalation.reason`,
> `disposition.recommended`.

---

## 10. Trigger & Run Model

- **Trigger**: on-demand (CS agent opens a case) + optional scheduled sweep of the
  open-returns queue.
- **Schedule**: optional `*/15 * * * *` open-queue pre-triage.
- **Event source**: n/a for the PoC (real system would emit a `return.created` event).
- **Expected volume**: PoC demo scale; production band 10K–100K returns/week.
- **Latency/SLA**: < 60s automated decision.
- **Concurrency**: per-case, independent.

### 10b. Triggers (Receiver contract)

- **Receiver type**: on-demand invocation (chat/workspace); optional ACA cron job
  for the queue sweep.
- **Idempotency key**: `returns.id` (a case is triaged once; re-runs are idempotent).
- **Dedup window**: 24h.
- **Dead-letter rule**: cases that fail 3 tool-fetch attempts → `escalate_to_supervisor`
  with a `system_error` reason.

---

## 11. Security, Compliance & Governance

- **PII involved**: yes (customer name + email — email masked; no PAN ever shown,
  per PCI-DSS v4.0 default). GDPR applies (EU customer data).
- **Auth model**: keyless — user-assigned managed identity + `DefaultAzureCredential`
  end-to-end.
- **Data residency**: EU (Sweden Central primary, West Europe fallback).
- **Data retention**: 90 days for traces/case data in the PoC (regulated-7y is a
  deferred decision — see § 11f).
- **Regulatory**: GDPR, PCI-DSS v4.0 (payment data never surfaced), consumer
  distance-selling / statutory return rights.
- **Access control**: Entra ID groups — `returns-cs-agents` (run + view),
  `returns-supervisors` (escalation edit-and-approve).
- **Audit requirements**: decision rationale, citations, model + prompt version,
  actor, human overrides, upstream data lineage. 90-day PoC / 7-year if regulated.
- **Responsible AI posture**: `consequential` (affects a customer refund outcome);
  mandatory human gate on escalations; transparency via citations.
- **Model governance**: `gpt-5.4` pinned; dev→prod promotion gated on eval pass;
  rollback to prior pinned version.
- **Customer onboarding RACI**: R = CS ops lead; A = COO; C = security/compliance;
  I = finance. *(Placeholder — confirm with sponsor.)*
- **ROI hypothesis**: `FTE-hours saved × loaded-rate` — minutes-of-manual-lookup ×
  returns/week × agent loaded rate.
- **Change management**: shadow-mode parallel run before CS agents action
  recommendations directly.

### 11b. AI Governance Hub Posture (opt-in spoke)

> **CITADEL GOVERNANCE OVERRIDE** — this pilot targets an **existing Citadel
> governance spoke**, not a bare sandbox. It consumes the Citadel hub's existing
> `tl-returns-triage` access contract (APIM AI gateway).

```yaml
governance_hub:
  required: yes
  hub_endpoint: https://apim-citadel-hub.azure-api.net
  access_contracts:
    - tl-returns-triage
```

- **Governance hub spoke required**: `yes`
- **Reason**: The pilot routes model traffic through the Citadel hub's APIM AI
  gateway for central policy enforcement, token governance, and audit. It consumes
  the pre-provisioned `tl-returns-triage` access contract rather than calling the
  model endpoint directly.
- **Hub endpoint**: `https://apim-citadel-hub.azure-api.net`
- **Access contracts consumed**: `tl-returns-triage`
- **Spoke artifacts needed**: `Access Contract (tl-returns-triage)`, `APIM connection`,
  `Product Policy`, `JWT auth`, `network whitelist`.
- **Tenant strategy**: `shared demo tenant` (default).

---

## 11c. Tech Stack (Module selectors)

| Module | Selected? | Purpose in this process |
|--------|-----------|-------------------------|
| `cosmos-db` | yes | Persistent case state + audit log |
| `ai-search` | yes | Foundry IQ backing for the return policy |
| `doc-intel` | no | No structured-doc extraction in scope |
| `azure-vision` | no | Damage-photo review is a V2 (GPT vision) item |
| `azure-speech` | no | No voice intake |
| `event-grid` | no | On-demand PoC; real system event is future |
| `service-bus` | no | Not needed at PoC scale |
| `storage-blob` | yes | Policy corpus + dataset hosting |
| `app-insights` | yes | Telemetry — required for continuous evals |
| `aca-job` | yes | Optional scheduled open-queue sweep |
| `aca-mcp` | yes | Mock MCP server for OMS / returns / customer |
| `aca-bot` | yes | Teams supervisor escalation card |
| `foundry-iq-index` | yes | Pre-provisioned return-policy Knowledge Base |

**Implications**: `aca-bot` → implies `aca-mcp` (satisfied); `foundry-iq-index` →
implies `ai-search` + `storage-blob` (satisfied); `aca-job` receiver present for
the optional sweep.

**Keyless by mandate**: no `key-vault` — managed identity end-to-end.

---

## 11d. Demo Data (Realism rules)

- **Per-entity volume**: `orders: 10, returns: 8, customers: 8` (narrative scale).
- **Distribution**: ~50% approve-eligible, ~12% final-sale deny, ~25% escalate
  (value/risk), ~13% need-info — deliberately skewed to exercise all four branches.
- **Golden cases**: `rma-glacier-outpost-fit-approve`,
  `rma-solstice-finalsale-deny`, `rma-cardinal-cashmere-escalate`,
  `rma-glacier-window-lapsed-escalate`, `rma-northwind-damaged-needinfo`,
  `rma-pinnacle-serial-returner-escalate` (two-token-shifted brands — no real
  retailer names).
- **Reset semantics**: `idempotent`.
- **Realism rules**:
  - PII: synthetic-only (emails masked).
  - Vendor / brand names: synonym-shifted (Glacier Outpost, Solstice Beauty Co.,
    Cardinal & Stripe, Northwind Home, Pinnacle Athletics).
  - Regulatory identifiers: none surfaced (no PAN).
  - Geographic distribution: single-country (EU).

---

## 11e. Workflow Model

```yaml
workflow_model: agent  # single agent + skills + tools
```

Rationale: per-case decision with tool-gathering + rule set + one human gate fits
an agent better than a DurableWorkflow. *(Source: defaulted-after-skip.)*

---

## 11f. Deployment Posture

```yaml
deployment_posture:
  deployment_target: customer-pilot
  source: defaulted-after-skip
  overrides:
    networking: public
    replicas: single
    retention: 90d
    model_pinning: ga-pinned
  deferred_decisions:
    - waf-front-door
    - dr-runbook
    - regulated-7y-retention
```

---

## 12. Production Readiness

- **target_posture**: `citadel-spoke` (customer brings the Citadel AI Hub Gateway — see § 11b)
- **residency**: `EU` (Sweden Central / West Europe)
- **rto**: `4h`
- **rpo**: `1h`
- **sla**: `99.5`
- **incident_owner**: `returns-oncall@contoso-retail.example` *(placeholder — confirm)*
- **production_track**: `pilot-to-prod-2-week`
- **must-have pillars**: `agent-governance, identity-access, observability,
  continuous-evals, responsible-ai, hitl-audit`
- **CISO sign-off required**: `yes` (customer PII + consequential decision)
- **Waiver acceptors**: *(none named yet — every gap is a hard block until a sponsor is confirmed)*

### `load_profile{}` (consumed by `threadlight-consumption-iq`)

```yaml
load_profile:
  workload_class: chat-agent
  peak_concurrent_sessions: 10
  avg_requests_per_session: 4
  avg_tokens_per_request: 3000
  peak_requests_per_second: 2.0
  business_hours_only: true
  cosmos_gb_year_one: 5
  storage_gb_year_one: 2
  ai_search_documents: 200
  monthly_growth_rate: 0.1
  declared_constraints:
    max_p95_latency_ms: 60000
    min_redundancy: zone-redundant
    pinned_region: swedencentral
```

---

## 13. Assumptions & Open Questions

Fast-PoC mode: audience mode, customer context, brand, and production posture were not collected; using neutral demo defaults. Override later in SPEC § 1 / § 11f / § 13.

### Captured-context source table

| Field | Effective value | Source | Default used? | Follow-up needed |
|-------|-----------------|--------|---------------|------------------|
| § 1.audience_mode | external-demo | defaulted-after-skip | yes | yes |
| § 1.customer.name | Contoso Retail | provided | no | no |
| § 1.customer.region | EU | provided | no | no |
| § 1.customer.brand_palette | Threadlight neutral | defaulted-after-skip | yes | no |
| § 7b.model | gpt-5.4 @ Sweden Central | defaulted-after-skip | yes | no |
| § 11b.governance_hub.required | yes | provided | no | no |
| § 11b.hub_endpoint | https://apim-citadel-hub.azure-api.net | provided | no | no |
| § 11b.access_contract | tl-returns-triage | provided | no | no |
| § 11f.deployment_target | customer-pilot | defaulted-after-skip | yes | yes |
| § 11f.overrides.networking | public | defaulted-after-skip | yes | yes |
| § 13.runtime | local-mac (probed) | provided | no | no |

### Runtime capability probe (recorded)

```yaml
runtime:
  name: local-mac
  playwright_available: false     # visual validation is MANUAL; no @playwright/mcp
  ffmpeg_available: true
  node_available: true            # node v26
  uv_available: true              # uv 0.11.x
  docker_available: false         # daemon not running → deploy must use az acr build
workflow_model: agent
```

### Assumptions
- The three upstream systems (OMS, returns-DB, customer-profile) are **mocked**;
  the schema in § 4 is the contract for the real swap.
- Return window = 30 days, auto-approve ceiling = $250, serial-returner threshold =
  lifetime return rate ≥ 0.40. These are demo defaults — confirm with the customer's
  actual policy.
- Refund settlement, RMA/label generation, and warehouse disposition execution are
  out of scope (the agent recommends; it does not settle).
- Neutral brand + `external-demo` framing applied silently (Fast-PoC).

### Open Questions
- Real return-policy thresholds (window, ceiling, serial-returner definition)?
- Which real systems back OMS / returns-DB / customer-profile, and their auth?
- Production retention — 90 days vs regulated 7-year?
- Named business sponsor + CISO sign-off owner for the pilot?

### Dependencies
- Citadel governance hub (`https://apim-citadel-hub.azure-api.net`) and the
  pre-provisioned `tl-returns-triage` access contract must be reachable from the
  spoke (see § 11b).
- Foundry project + `gpt-5.4` capacity in the EU data boundary.
- `az acr build` (docker daemon unavailable locally — see runtime probe).
