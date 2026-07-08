# Sample Data — Returns Triage (mock systems)

These JSON files stand in for three upstream systems that are **not accessible in
the PoC**. Each file mocks one system so the agent can be developed, run locally
(Pattern 0 `threadlight_quickstart`), and demoed without live credentials.

| File | Mocks (system of record) | SPEC § 5 system | Swap target |
|------|--------------------------|-----------------|-------------|
| `orders.json` | Order-management system (OMS) | Order-management system | `oms_get_order` MCP tool |
| `returns.json` | Returns database | Returns-database | `returns_get_case` / `returns_list_open` MCP tool |
| `customers.json` | Customer-profile service | Customer-profile service | `customer_get_profile` MCP tool |

## Wrapper shape

Every file uses the mandatory `{ "_meta": {...}, "records": [...] }` wrapper that
`threadlight-demo-data-factory` and `threadlight-workspace-ui` depend on. Do **not**
move `_meta` to be a sibling of individual records.

## Schema

Field-level schemas are the source of truth in **SPEC.md § 4 (Data Models)**. Keep
the JSON in step with that section — the mock MCP server validates against it.

- **orders** — `id, customer_id, status, channel, order_date, delivery_date, currency, order_total, items[]`
- **returns** — `id, order_id, customer_id, status, reason_code, requested_at, refund_amount, item_condition, photos_provided, final_sale, disposition, decision`
- **customers** — `id, name, email_masked, region, loyalty_tier, account_status, lifetime_orders, lifetime_return_rate, tenure_months`

## Golden cases (drive the demo + eval dataset)

| Return `case_slug` | Exercises branch | Why |
|--------------------|------------------|-----|
| `rma-glacier-outpost-fit-approve` | `approve_refund` | In-window, not final-sale, unworn — clean auto-approve |
| `rma-solstice-finalsale-deny` | `deny_refund` | Final-sale item, changed-mind reason |
| `rma-cardinal-cashmere-escalate` | `escalate_to_supervisor` | Refund $1,180 > $250 auto-approve ceiling |
| `rma-glacier-window-lapsed-escalate` | `escalate_to_supervisor` | 86 days after delivery — window lapsed, no override reason |
| `rma-northwind-damaged-needinfo` | `request_more_info` | Damage claim with no photos attached |
| `rma-pinnacle-serial-returner-escalate` | `escalate_to_supervisor` | Customer lifetime return rate 63% — serial-returner risk |

## Replacing mock data with real systems

When a real backend becomes available, the schema stays the same — only the source
of truth changes:

1. Stand up (or point at) the real MCP server for that system.
2. In `src/agent/mcp-config.json`, replace the mock server entry with the real endpoint.
3. Delete the corresponding `*.json` seed (or keep it as an eval fixture).

Example: when the OMS is reachable, replace `orders.json` with an MCP tool call to
`oms_get_order(order_id)` — the returned object must match SPEC § 4 `orders`.
