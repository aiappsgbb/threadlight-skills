# Cost Actuals Reconciliation - Design RFC

- **Status:** Draft - awaiting user review
- **Date:** 2026-08-18
- **Author:** Brainstormed with Copilot CLI
- **Decides:** how Threadlight preserves full Azure cost projection while adding
  post-deploy actuals, forecast variance, and cost per successful interaction
- **Related:** `threadlight-consumption-iq`, `threadlight-production-ready`,
  `threadlight-router-bench`, SPEC section 14 `value_model`

## 1. Decision summary

Threadlight will keep `specs/cost-manifest.json` unchanged as the complete
monthly Azure cost projection. The current calculation is not removed,
reduced to model-token cost, or replaced by a post-deploy query.

Post-deploy evidence is additive:

1. `specs/cost-actuals-manifest.json` records observed Azure spend, usage, and
   evidence quality for a declared time window.
2. `specs/cost-reconciliation-manifest.json` compares that evidence with the
   existing projection over the same window.
3. `docs/cost-reconciliation.md` explains total forecast, observed spend,
   monthly run-rate, variance, unmodeled costs, and cost per successful
   interaction without presenting estimates as invoices.

After deploy, actuals become the primary operational number only when they
satisfy a maturity policy declared in SPEC section 14. There are no tool-owned
default thresholds. Missing policy, permissions, freshness, or evidence
produces `not-verified`, never zero and never a success-shaped fallback.

## 2. Problem

`threadlight-consumption-iq` currently answers the pre-deploy and architecture
review question:

> What should the complete Azure solution cost at the declared load?

It projects model, hosted-agent, Container Apps, Cosmos DB, Storage, APIM,
AI Search, observability, and production-hardening costs. This is necessary and
must remain.

It does not yet answer the post-deploy FinOps questions:

- What did the workload actually cost over a known period?
- Is observed spend consistent with the projection?
- Which actual costs were absent from the model?
- What does one successful interaction cost when all Azure resources are
  included?
- Is the data mature enough to make any of those comparisons defensible?

`threadlight-production-ready` already reserves `COST-102` and `COST-103` for
actuals-vs-forecast and observed PAYG/PTU analysis, but both are explicitly
`not-verified`. `threadlight-router-bench` already parses Azure Monitor token
metrics for model-level attribution, but it measures one benchmark run, not
the complete deployed workload cost.

The gap is not another forecast formula. It is a reconciliation contract.

## 3. Goals

1. Preserve the current complete Azure projection and all current consumers.
2. Add observed total Azure cost at the workload scope.
3. Calculate cost per successful interaction from complete actual Azure cost,
   not model tokens alone.
4. Compare actual and projected cost over the same period and price basis.
5. Surface unmodeled and unattributed cost instead of hiding it in a total.
6. Make evidence maturity explicit, configurable, versioned, and auditable.
7. Reuse existing token-metric parsing rather than create a second
   interpretation of Azure Monitor payloads.
8. Keep every new verdict advisory and fail-closed.

## 4. Non-goals

- Removing or weakening the existing cost projection.
- Replacing Azure invoices, Cost Management exports, or a customer FinOps
  platform.
- Claiming that repriced token metrics are billed actual cost.
- Automatic Bicep or SKU mutation.
- A universal business-outcome denominator in v1.
- Real-time anomaly detection.
- Introducing a default maturity threshold chosen by Threadlight.
- Blocking the design-to-deploy path on delayed Cost Management ingestion.

## 5. Considered approaches

### A. Sidecar actuals plus reconciliation - selected

Keep the forecast contract unchanged. Add one evidence manifest and one joined
manifest.

**Why selected**

- Zero semantic change for current projection consumers.
- Forecast and actuals retain independent freshness and provenance.
- The join can fail closed without invalidating a valid projection.
- Raw observed evidence remains reusable by production-ready and future
  reporting.
- Schema evolution is isolated instead of weakening the strict v1 forecast
  parser.

### B. Extend `cost-manifest.json`

This uses fewer files, but mixes prediction and observation in a strict schema
whose consumers currently reject unknown top-level keys. It also gives one
timestamp to data with different ingestion delays. Rejected.

### C. Query actuals only inside production-ready

This minimizes new code in Consumption IQ, but produces no reusable evidence
artifact, duplicates cost logic in the assessor, and makes review results
non-reproducible. Rejected.

## 6. Ownership boundaries

| Concern | Owner |
|---|---|
| Complete Azure monthly projection | `threadlight-consumption-iq` existing projection path |
| Cost Management actuals collection | `threadlight-consumption-iq` |
| Azure Monitor token attribution | Shared pure parser used by Consumption IQ and router-bench |
| Successful-interaction count | `threadlight-consumption-iq`, from declared trace predicate |
| Reconciliation and unit economics | `threadlight-consumption-iq` |
| Budget wiring live check | `threadlight-production-ready` `COST-101` |
| Actual-vs-forecast assessment | `threadlight-production-ready` consumes reconciliation artifact |
| PAYG/PTU observed-usage assessment | `threadlight-production-ready` consumes reconciliation artifact |
| Maturity and KPI policy | SPEC section 14 `value_model` |

Production-ready must not independently re-query and recompute `COST-102` or
`COST-103`. It assesses the versioned evidence emitted by Consumption IQ. This
keeps producer and assessor separate without creating two cost engines.

## 7. Artifact contracts

### 7.1 Existing forecast remains unchanged

`specs/cost-manifest.json` remains strict schema v1 and continues to contain:

- projected resources and alternatives;
- monthly current and recommended total Azure cost;
- monthly savings potential;
- price source and price basis;
- load-profile and deployment references.

Existing `COST-005`, `COST-006`, production-ready scorecards, and
threadlight-auto resumability continue to consume it.

### 7.2 New actuals manifest

`specs/cost-actuals-manifest.json` records evidence, not verdicts:

```jsonc
{
  "schema": "threadlight-cost-actuals/v1",
  "generated_at": "2026-08-10T00:00:00Z",
  "status": "pass",
  "scope": {
    "subscription_id": "<guid>",
    "resource_group": "rg-pilot-prod",
    "dedicated_to_workload": true,
    "scope_evidence": "azd environment resource group"
  },
  "window": {
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-08T00:00:00Z",
    "complete_days": 7,
    "settlement_age_hours": 48,
    "window_end_age_days": 2
  },
  "cost": {
    "basis": "usage-pretax",
    "currency": "USD",
    "period_total_usd": 182.41,
    "resources": [],
    "unattributed_usd": 0.0,
    "attribution_coverage_pct": 1.0
  },
  "usage": {
    "total_interactions": 1240,
    "successful_interactions": 1178,
    "success_predicate_ref": "SPEC.md#section-14-value-model",
    "models": []
  },
  "provenance": {
    "cost_management": {},
    "azure_monitor": {},
    "traces": {}
  },
  "warnings": []
}
```

Allowed top-level `status` values in v1 are `pass` and `not-verified`.
Collection can be valid even when reconciliation is not mature.

The manifest must retain the raw period total. A monthly run-rate is derived
only in the reconciliation artifact.

`usage.models` is a **list**, not a keyed object. A single collection window
can observe many deployment/model combinations (primary deployment, spillover
deployment, multiple model versions), and each combination needs its own row
of input/output token counts. A list preserves every observed row without
forcing an arbitrary composite key.

### 7.3 New reconciliation manifest

`specs/cost-reconciliation-manifest.json` joins forecast, actuals, and policy:

```jsonc
{
  "schema": "threadlight-cost-reconciliation/v1",
  "generated_at": "2026-08-10T00:00:00Z",
  "status": "pass",
  "variance_status": "pass",
  "forecast_ref": {
    "path": "specs/cost-manifest.json",
    "sha256": "<hex>"
  },
  "actuals_ref": {
    "path": "specs/cost-actuals-manifest.json",
    "sha256": "<hex>"
  },
  "policy_ref": {
    "path": "specs/SPEC.md",
    "section": 14,
    "spec_sha256": "<hex>"
  },
  "policy_snapshot": {
    "target_cost_per_successful_interaction_usd": 0.18,
    "max_forecast_variance_pct": 0.20
  },
  "maturity": {
    "status": "pass",
    "checks": []
  },
  "totals": {
    "forecast_monthly_usd": 794.89,
    "forecast_window_usd": 185.47,
    "actual_window_usd": 182.41,
    "actual_monthly_run_rate_usd": 781.76,
    "variance_window_usd": -3.06,
    "variance_pct": -0.0165
  },
  "unit_economics": {
    "status": "pass",
    "successful_interactions": 1178,
    "cost_per_successful_interaction_usd": 0.1548,
    "target_usd": 0.18,
    "target_status": "pass"
  },
  "coverage": {
    "attribution_coverage_pct": 1.0,
    "unmodeled_actual_usd": 12.30,
    "forecast_not_observed_usd": 0.0
  },
  "drivers": {
    "payg_ptu": {"status": "pass", "observed_volume_variance_pct": 0.0}
  },
  "warnings": []
}
```

`status` is the overall evidence/maturity state of the reconciliation artifact
(`pass | not-verified`; it mirrors `maturity.status`). `variance_status` is a
separate, narrower verdict: `pass | should-fix | not-verified`, computed by
comparing `totals.variance_pct` against the SPEC-declared
`policy_snapshot.max_forecast_variance_pct`. It is `not-verified` whenever
`status`/`maturity.status` is `not-verified`, or when price bases mismatch
without an explicit SPEC opt-in (§9.5). `threadlight-production-ready`'s
`COST-102` consumes `variance_status` directly; it does not hardcode any
percentage threshold of its own.

`unit_economics` carries two independent verdicts, both required:

- `status` (`pass | not-verified`) is evidence maturity — whether
  `cost_per_successful_interaction_usd` could be computed at all. It is
  `pass` only when all three hold: the actuals evidence itself carries a
  verified Cost Management total (the actuals manifest's own `status` is
  `pass`, not `not-verified`); SPEC's declared `maturity_policy` (§8) is
  complete — every required field present, independent of whether its
  thresholds are actually met; and `successful_interactions` is greater
  than zero (a divide-by-zero guard, not a threshold comparison). Azure
  Monitor token metrics (§9.1, §10) are attribution evidence only and are
  optional here — missing or incomplete token metrics never prevent
  `status` from being `pass`; they only degrade `drivers.payg_ptu` and
  model-level breakdowns.
- `target_status` (`pass | should-fix | not-verified`) is a separate,
  secondary comparison, evaluated only when `status` is `pass`: it compares
  the computed `cost_per_successful_interaction_usd` against the
  SPEC-declared `target_usd`. It inherits `not-verified` from `status`, is
  `pass` when the observed cost is at or below target, and `should-fix` when
  it exceeds target.

`drivers` is an **object keyed by driver name** (for example `payg_ptu`), not
an array. Each driver's status and evidence live at a stable, addressable key
so a consumer can read `drivers.payg_ptu.status` directly instead of
searching an array for a matching entry. V1 defines a single driver,
`payg_ptu`; future drivers are added as additional keys, never as array
elements.

The two canonical manifest paths always contain the latest completed window.
Each successful collection also writes the same payloads under
`specs/cost-history/<start-date>--<end-date>/<generated-at>/`. History entries
are immutable; collecting the same window after more charges settle creates a
new timestamped snapshot rather than overwriting evidence. The canonical files
are the latest-view contract consumed by other skills.

The canonical reconciliation manifest is the **commit marker**. Publishers
write the actuals file first and reconciliation last. Consumers accept the pair
only when the reconciliation's `actuals_ref.sha256` matches the canonical
actuals JSON document and its `forecast_ref.sha256` matches the forecast JSON
document. A process
failure can therefore leave a newer actuals file next to an older
reconciliation, but that pair is necessarily `not-verified`; it can never
silently pass as a completed publish.

Consumers also compare `policy_ref.spec_sha256` with the current SPEC bytes.
Any SPEC edit invalidates the reconciliation conservatively. The snapshot keeps
the exact thresholds used to render the historical verdict auditable even
after a later SPEC revision.

### 7.4 Human report

`docs/cost-reconciliation.md` must lead with four separate numbers:

1. Complete projected monthly Azure cost.
2. Actual Azure spend in the observed period.
3. Actual monthly run-rate, when mature.
4. Actual cost per successful interaction, when mature.

It must never label a token reprice as "actual billed cost".

## 8. SPEC section 14 policy

There are no Threadlight-owned defaults. SPEC section 14 must explicitly
declare:

```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days: 7
      min_successful_interactions: 100
      min_cost_settlement_age_hours: 48
      max_window_end_age_days: 14
      min_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
    accounting:
      actual_cost_basis: usage-pretax
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```

The numbers above illustrate shape only; generated projects must not copy
them as defaults.

`usage-pretax` names the read-only Cost Management Query API contract used by
v1 (`type: Usage`, aggregate `PreTaxCost`). It is observed Azure spend, but it
is not described as a finalized invoice. The API contract is pinned to
`2025-03-01`: <https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01>.

The Query API does not expose a trustworthy per-response "data through"
timestamp. V1 therefore uses a conservative, measurable window rule:
`settlement_age_hours = generated_at - window.end`. The window must be old
enough for the operator-declared settlement buffer and recent enough for the
operator-declared operational recency. This does not claim that every provider
has finished billing; it makes the assumption explicit.

If any required policy field is absent, raw actual collection may still
succeed, but maturity and unit-economics verdicts are `not-verified`.

The success event is workload-owned. `trace_attribute` and every
`success_values` entry use a restricted identifier grammar
`^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`; the collector refuses arbitrary KQL
fragments. It builds the fixed query shape itself and treats invalid values as
an incomplete policy. V1 standardizes cost per successful interaction, not the
meaning of success. A future version may add business outcomes such as cost per
claim resolved without changing the v1 denominator.

## 9. Calculation rules

### 9.1 Authoritative total

`actual_window_usd` comes only from Cost Management at the declared workload
scope.

Azure Monitor token cost is attribution and diagnostic evidence. It is never
added to the Cost Management total. This invariant prevents double counting.

### 9.2 Window alignment

Only complete UTC days are compared.

```text
forecast_window_usd =
    forecast_monthly_usd * complete_days / 30

actual_monthly_run_rate_usd =
    actual_window_usd * 30 / complete_days

variance_window_usd =
    actual_window_usd - forecast_window_usd

variance_pct =
    variance_window_usd / forecast_window_usd
```

If `forecast_window_usd` is zero, `variance_pct` is null and the manifest
records an explicit reason. It is never infinity, zero, or omitted silently.

### 9.3 Unit economics

```text
cost_per_successful_interaction_usd =
    actual_window_usd / successful_interactions
```

This intentionally includes the complete Azure workload cost, not only model
tokens. `unit_economics.status` is `pass` only when the actuals evidence
itself is verified (Cost Management collection `status` is `pass`), SPEC's
declared `maturity_policy` is complete, and `successful_interactions` is
greater than zero; if any of those does not hold — including the simple case
of a zero-interaction window — it produces `unit_economics.status =
not-verified` and a null `cost_per_successful_interaction_usd`; division is
not attempted. Token metrics are never part of this gate (see §7.3):
incomplete or missing model attribution degrades `drivers.payg_ptu` and
model-level breakdowns, never `unit_economics.status`.
`unit_economics.target_status` compares the resulting cost
against SPEC's `baseline.target_cost_per_successful_interaction_usd` (see
§7.3) and is itself `not-verified` whenever `status` is `not-verified`.

### 9.4 Attribution and unmodeled costs

The reconciler maps Cost Management line items to projected resources by
resource ID first, then normalized Azure resource type. It emits:

- actual resources absent from the projection as `unmodeled`;
- projected resources with no actual observation as `not-observed`;
- actual cost that cannot be mapped as `unattributed`;
- coverage as mapped actual cost divided by total actual cost.

Unmodeled or unattributed cost remains inside `actual_window_usd`. It is not
dropped to make variance look better.

### 9.5 Price-basis mismatch

Forecast retail price and billed actual price can differ because of EA/MCA
discounts, reservations, credits, and amortization.

Both bases must be recorded. A mismatch is visible in the report. Unless SPEC
section 14 explicitly allows the mismatch for verdicts, the tool reports the
numeric delta but sets the variance verdict to `not-verified`.

### 9.6 Cache, spillover, and priority processing

These affect cost without changing the core contract:

- Cache-hit metrics are reported only when Azure Monitor or traces expose an
  attributable cached-token dimension. Missing data is `not-verified`, not a
  zero-percent cache rate.
- Spillover deployment/model usage is a separate attribution driver while its
  billed cost remains inside the Cost Management total.
- Priority Processing is captured from deployment configuration and observed
  billing dimensions when available. If the forecast did not model it, the
  reconciler identifies it as an unmodeled driver.

V1 does not require these optional dimensions to compute the complete actual
total or cost per successful interaction.

## 10. Maturity state machine

Actuals are operationally authoritative only when all declared checks pass:

```text
policy complete
  AND Cost Management scope readable
  AND dedicated workload scope or explicit tagged allocation established
  AND complete_days >= declared minimum
  AND successful_interactions >= declared minimum
  AND settlement_age_hours >= declared minimum
  AND window_end_age_days <= declared maximum
  AND attribution_coverage_pct >= declared minimum
  AND price bases compatible or explicitly allowed
```

| Condition | Result |
|---|---|
| All checks pass | `pass`; actual run-rate is primary operational number |
| Policy incomplete | `not-verified`; show forecast and raw actual period spend |
| Cost Management unavailable | `not-verified`; preserve forecast |
| Traces unavailable | actual total can pass collection; unit economics is `not-verified` |
| Token metrics unavailable | actual total and unit economics may still pass; model attribution is `not-verified` |
| Window too recent to settle or too old to represent current operations | `not-verified`; never extrapolate silently |
| Scope contains unrelated workloads | `not-verified` unless SPEC declares tagged allocation and coverage satisfies policy |

No broad exception handler may turn an evidence failure into an empty manifest.
The CLI returns a non-zero evidence exit code while still preserving any
valid raw artifacts it collected.

## 11. CLI shape

The existing projection commands remain unchanged. Add:

```bash
# Collect observed evidence only. --start/--end are required. --subscription
# and --resource-group are optional flags here: they default from
# AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP, and the command exits 2 if
# neither the flag nor the matching environment variable resolves one.
# --workspace-resource-id is optional with no environment fallback; omitting
# it only degrades Azure Monitor token attribution, never the Cost
# Management total.
scripts/consumption_iq.py actuals \
  --start 2026-08-01 --end 2026-08-08

# Join existing projection, actuals, and SPEC section 14 policy.
# --forecast, --actuals-manifest, and --spec all default to the same
# canonical paths (DEFAULT_OUTPUT_MANIFEST, DEFAULT_ACTUALS_MANIFEST,
# DEFAULT_SPEC_PATH) the rest of the pipeline already reads and writes.
scripts/consumption_iq.py reconcile

# Existing projection plus best-effort post-deploy evidence. --with-actuals
# requires --start/--end and resolves --subscription/--resource-group the
# same way `actuals` does.
scripts/consumption_iq.py run --all --with-actuals \
  --start 2026-08-01 --end 2026-08-08
```

`run --all` without `--with-actuals` preserves current behavior.

Suggested evidence exit codes:

| Code | Meaning |
|---:|---|
| 0 | Requested artifacts produced and mature |
| 2 | Missing prerequisite (including an unresolved `--subscription`/`--resource-group` or a missing `--start`/`--end`) |
| 3 | Required live source unavailable |
| 4 | Existing incomplete load profile |
| 5 | Actuals collected but maturity policy not satisfied |

Exit 5 is advisory in threadlight-auto and production-ready. It must not block
the base design-to-deploy path.

## 12. Downstream behavior

### threadlight-production-ready

- `COST-005` and `COST-006` remain tied to the forecast artifact.
- `COST-101` continues to check live budget wiring.
- `COST-102` reads the reconciliation manifest and assesses declared variance.
- `COST-103` reads observed model usage and the forecast recommendation.
- KPI scorecard reads actual cost per successful interaction from the
  reconciliation manifest, not from the forecast manifest.
- Missing or immature reconciliation evidence is `not-verified`.

### threadlight-auto

- Projection remains part of the normal post-deploy chain.
- Actuals are attempted only when requested and when SPEC section 14 policy is
  complete.
- Exit 5 records `cost-reconciliation: not-verified` and continues.
- No retry loop waits for Cost Management ingestion.

### threadlight-router-bench

Move the pure Azure Monitor payload parser and token-cost helpers to a shared
stdlib-only module. Both callers keep their own orchestration:

- router-bench owns run-window benchmark attribution;
- Consumption IQ owns deployment-window operational attribution.

No benchmark report becomes a source of billed actual cost.

## 13. Test strategy

All calculation and parsing tests are offline and fixture-driven.

### Required fixtures

- Cost Management response with multiple Azure resource types.
- Same response with an unmodeled resource.
- Same response with unattributed cost.
- Actual-cost and amortized-cost variants.
- Azure Monitor token metrics split by deployment and model.
- Trace results with successes, failures, and escalations.
- Stale, incomplete, and permission-denied source responses.

### Required invariants

1. Existing `cost-manifest.json` fixtures remain byte-for-byte unchanged.
2. Repriced token cost is never added to Cost Management total.
3. Unmodeled cost remains in the authoritative actual total.
4. Forecast and actual windows use the same complete-day boundary.
5. Zero successes yields `not-verified`, not zero cost per interaction.
6. Missing maturity policy yields `not-verified`.
7. Basis mismatch yields a numeric delta but no pass/fail verdict unless
   explicitly allowed.
8. Missing Monitoring Reader degrades model attribution only.
9. Missing Cost Management Reader prevents actual-total verification.
10. Production-ready consumes manifests and does not reimplement reconciliation.

### Live validation

Cost Management ingestion is delayed, so the short-lived full E2E run cannot
honestly gate on mature actuals. The full E2E must continue to validate the
projection and may record `not-verified` for actuals.

Before enabling `COST-102` or `COST-103`, run one manual read-only validation
against a persistent dedicated pilot Resource Group with mature billing data.
Capture sanitized source shapes as regression fixtures. Do not retain customer
identifiers or billing values in the repository.

## 14. PR sequence

Each PR is independently reviewable and keeps the base path stable.

1. **RFC only:** this document; no runtime behavior.
2. **SPEC section 14 value model:** schema, design template, contract checker,
   and generated example update.
3. **Actuals contracts and pure core:** schemas, source parsers, maturity
   evaluator, reconciler, and offline fixtures. No CLI wiring.
4. **Consumption IQ integration:** `actuals`, `reconcile`, and opt-in
   `--with-actuals`; existing `run --all` unchanged. This PR also updates
   `SKILL.md` operational guidance (RBAC scope, cadence, `usage-pretax`
   terminology) for the new CLI surface, and performs and ships the sanitized
   mature-pilot live-shape validation fixture produced by a real read-only run
   against that surface. That validation is folded in here, not a separate
   PR, because it exercises the exact CLI this PR adds; a live probe cannot
   validate a CLI surface that does not exist yet.
5. **Production-ready integration:** consume fresh reconciliation evidence for
   `COST-102`, `COST-103`, and KPI scorecard.

There are exactly five PRs. PR 2 is a prerequisite for PRs 3-5 because the
user selected SPEC section 14 as the policy source and explicitly rejected
tool-owned defaults.

## 15. Acceptance criteria

The design is implemented when:

- The current complete Azure projection is unchanged and its tests still pass.
- A read-only run can emit actuals for a dedicated pilot Resource Group.
- Reconciliation is deterministic from forecast, actuals, and SPEC policy.
- Complete Azure actual cost is never confused with token reprice.
- Cost per successful interaction includes all in-scope Azure actual cost.
- Missing or immature evidence is visibly `not-verified`.
- Production-ready consumes the artifact without duplicating the engine.
- The base design-to-deploy E2E remains green.

## 16. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Forecast disappears behind actuals | Keep separate artifacts and show both headline numbers |
| Token cost double counted | Cost Management is the sole authoritative total |
| Shared RG contaminates unit cost | Dedicated-scope evidence plus declared coverage threshold |
| Billing lag creates false confidence | Policy-owned maximum lag and complete-day windows |
| Teams tune thresholds after seeing results | Policy lives in versioned SPEC section 14 and is hashed into reconciliation |
| Free-form success predicate becomes KQL injection | SPEC declares restricted attribute/value identifiers; code owns the fixed KQL shape |
| Retail forecast vs negotiated actual creates fake savings | Record both bases; fail closed on mismatch |
| Generic "success" metric is meaningless | Workload declares the trace predicate |
| Live queries regress deploy | Opt-in post-deploy path; never blocks design-to-deploy |

## 17. Locked decisions

| ID | Decision |
|---|---|
| D1 | Preserve the full current Azure cost projection |
| D2 | Actuals-first only after declared maturity |
| D3 | No default maturity thresholds |
| D4 | Maturity and KPI policy lives in SPEC section 14 `value_model` |
| D5 | V1 unit economics is cost per successful interaction |
| D6 | Use sidecar actuals and reconciliation artifacts |
| D7 | Cost Management is the sole authoritative actual total |
| D8 | Token metrics are attribution only and are never added to actual total |
| D9 | Consumption IQ produces; production-ready assesses |
| D10 | Roll out through small prerequisite-ordered PRs |
