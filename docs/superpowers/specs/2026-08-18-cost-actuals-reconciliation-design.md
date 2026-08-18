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
    "cost_column": "PreTaxCost",
    "currency": "USD",
    "period_total_usd": 182.41,
    "resources": [],
    "unattributed_usd": 0.0,
    "resource_id_coverage_pct": 1.0
  },
  "usage": {
    "interaction_status": "pass",
    "model_attribution_status": "pass",
    "total_interactions": 1240,
    "successful_interactions": 1178,
    "success_predicate_ref": "SPEC.md#section-14-value-model",
    "models": []
  },
  "provenance": {
    "cost_management": {},
    "azure_monitor": {},
    "log_analytics": {}
  },
  "warnings": []
}
```

Allowed top-level `status` values in v1 are `pass` and `not-verified`.
Collection can be valid even when reconciliation is not mature.

**Top-level `status` has exactly one producer rule.** `status` is `pass` if and
only if the Cost Management source was read successfully, the declared scope
parsed, and the declared window parsed and validated (§9.2). It describes the
authoritative cost evidence and nothing else. Missing Azure Monitor token
metrics and missing Log Analytics interaction counts must never change it — a
window with a verified Cost Management total and no trace access is still
`status: pass`, carrying a warning.

Those two optional evidence streams therefore carry their own explicit,
addressable statuses inside `usage`, each `pass | not-verified`:

- `usage.interaction_status` — `pass` only when the interaction query ran and
  returned a parsable row set (a zero-row result is a parsable result and is
  `pass` with zero counts). It is `not-verified` when the query was skipped
  (unsafe or incomplete policy identifiers, no resolvable workspace), failed,
  or returned an unparsable shape. When it is `not-verified`,
  `total_interactions` and `successful_interactions` are `null`, never `0`.
- `usage.model_attribution_status` — `pass` only when Azure Monitor token
  metrics were read and parsed; `not-verified` otherwise, in which case
  `models` is an empty list and no zero-token claim is made.

`cost.cost_column` records which Cost Management cost column the parser
actually consumed (§9.7), so a reader can tell `PreTaxCost` evidence from an
alias without re-querying.

`cost.resource_id_coverage_pct` is a **source-quality** measure only: actual
cost rows carrying a nonblank resource ID divided by total actual cost. It says
nothing about whether the forecast modeled those resources. The separate,
policy-gated measure lives in the reconciliation artifact as
`coverage.projection_attribution_coverage_pct` (§7.3, §9.4). The two are
deliberately different numbers and must never be conflated: a window can have
100% resource-ID coverage and very low projection attribution coverage.

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
    "max_forecast_variance_pct": 0.20,
    "max_token_volume_variance_pct": 0.25,
    "min_projection_attribution_coverage_pct": 0.95,
    "actual_billing_price_basis": "retail",
    "forecast_price_basis": "retail",
    "allow_basis_mismatch_for_verdict": false
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
    "projection_attribution_coverage_pct": 1.0,
    "source_resource_id_coverage_pct": 1.0,
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
  `pass` only when all four hold: the actuals evidence itself carries a
  verified Cost Management total (the actuals manifest's own `status` is
  `pass`, not `not-verified`); SPEC's declared `maturity_policy` (§8) is
  complete — every required field present, independent of whether its
  thresholds are actually met; the actuals manifest's
  `usage.interaction_status` is `pass`, so the denominator is a measured
  count rather than an absent one; and `successful_interactions` is greater
  than zero (a divide-by-zero guard, not a threshold comparison). Azure
  Monitor token metrics (§9.1, §10) are attribution evidence only and are
  optional here — `usage.model_attribution_status = not-verified` never
  prevents `status` from being `pass`; it only degrades `drivers.payg_ptu`
  and model-level breakdowns.
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

`forecast_ref.sha256` behaves the same way, and that invalidation is
**intentional, not a defect**. Re-running the projection rewrites
`specs/cost-manifest.json`; even a pricing-only refresh changes its hash, so
the existing reconciliation stops matching and becomes `not-verified`. The
required remedy is deliberately cheap: rerun `reconcile` only. That command
reads the already-collected canonical actuals manifest and issues **no Azure
calls at all** — raw actuals never need to be recollected when the observed
window, scope, and collected evidence are unchanged. Recollection is required
only when the window or scope itself changes, or when the operator wants a
newer, more settled snapshot of the same window.

`reconcile` therefore reuses canonical raw actuals under an explicit,
checkable rule. It recollects nothing and calls no Azure API when all of the
following hold for the canonical `specs/cost-actuals-manifest.json`:

1. its `schema` is `threadlight-cost-actuals/v1`;
2. its `status` is `pass`;
3. its `window.start`/`window.end` equal the window being reconciled (when the
   caller declares one; `reconcile` with no window flags always adopts the
   manifest's own window);
4. its `scope.subscription_id`/`scope.resource_group` equal the scope being
   reconciled, when the caller declares one.

If any of those does not hold, `reconcile` fails closed with a `not-verified`
reconciliation naming the mismatch. It never silently re-queries Azure to
paper over a scope or window disagreement, and it never partially merges two
windows' evidence.

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
      min_projection_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
      max_token_volume_variance_pct: 0.25
    accounting:
      actual_cost_basis: usage-pretax
      actual_billing_price_basis: retail
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```

The numbers above illustrate shape only; generated projects must not copy
them as defaults.

`min_projection_attribution_coverage_pct` gates only the reconciliation's
`coverage.projection_attribution_coverage_pct` (§9.4). The actuals manifest's
`cost.resource_id_coverage_pct` is source-quality reporting and is never
compared against a policy threshold.

`max_forecast_variance_pct` gates **cost** variance only. The PAYG/PTU driver
compares **token volume**, a different quantity with a different acceptable
spread, so it has its own declared field `max_token_volume_variance_pct` and
must not reuse the cost tolerance. If `max_token_volume_variance_pct` is
absent, `drivers.payg_ptu` is `not-verified`; the tool never substitutes the
cost tolerance for it.

`usage-pretax` names the read-only Cost Management Query API contract used by
v1 (`type: Usage`, aggregate `PreTaxCost`). It is observed Azure spend, but it
is not described as a finalized invoice. The API contract is pinned to
`2025-03-01`: <https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01>.

`actual_cost_basis` and `actual_billing_price_basis` are two different
dimensions and both are required:

- `actual_cost_basis: usage-pretax` names the **metric and source** — which
  Query API `type` and aggregate produced the number. It says nothing about
  which price list generated those charges.
- `actual_billing_price_basis: retail | ea | mca | unknown` names the **price
  basis** the subscription is actually billed on. Only this field is
  comparable with `forecast_price_basis` (§9.5). `unknown` is a valid, honest
  declaration and is treated as a mismatch for verdict purposes.

The reference example in this RFC and in the shipped golden example
deliberately declares `actual_billing_price_basis: retail` so that it matches
`forecast_price_basis: retail` and the example's variance verdict is
`pass`-eligible; a real EA or MCA workload declares `ea`/`mca` and either
accepts `variance_status: not-verified` or explicitly sets
`allow_basis_mismatch_for_verdict: true`.

### 8.1 Window and daily-granularity contract

The Query API does not expose a trustworthy per-response "data through"
timestamp. V1 therefore uses a conservative, measurable window rule:
`settlement_age_hours = generated_at - window.end`. The window must be old
enough for the operator-declared settlement buffer and recent enough for the
operator-declared operational recency. This does not claim that every provider
has finished billing; it makes the assumption explicit.

The declared window must also be **verifiable from the response itself**, not
merely from the request the tool believes it sent. V1 therefore queries with
`dataset.granularity: "Daily"` and requires a `UsageDate` column in every
returned page. The documented Query API semantics this assumes are stated
explicitly so a future API change breaks a test rather than a customer report:
`timePeriod.from` is inclusive, `timePeriod.to` is treated as the end of the
declared range, and daily granularity emits one row per resource per UTC day.
The parser does not trust that description on its own — it re-validates every
row:

- `UsageDate` values are normalized from either the integer/string `YYYYMMDD`
  form or an ISO date string into a UTC date;
- every row must satisfy `window.start <= usage_date < window.end`
  (start-inclusive, end-exclusive);
- a row outside that range is a **fail-closed error**, never a silently
  dropped row, because silent dropping is exactly how an off-by-one boundary
  would masquerade as a clean total;
- `complete_days = (window.end - window.start).days`, computed from the
  declared boundaries, and `window.end <= window.start` is rejected outright.

The request body sends `to` at UTC midnight of the declared end date while the
parser enforces the end-exclusive rule against observed `UsageDate` values, so
the artifact's window semantics are guaranteed by validation rather than by an
assumption about the service.

If any required policy field is absent, raw actual collection may still
succeed, but maturity and unit-economics verdicts are `not-verified`.

The success event is workload-owned. `trace_attribute` and every
`success_values` entry use a restricted identifier grammar
`^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`; the collector refuses arbitrary KQL
fragments. It builds the fixed query shape itself and treats invalid values as
an incomplete policy. V1 standardizes cost per successful interaction, not the
meaning of success. A future version may add business outcomes such as cost per
claim resolved without changing the v1 denominator.

### 8.2 Interaction query surface

V1 transports the interaction query with
`az monitor log-analytics query --workspace <customerId>`. That is the
**Log Analytics workspace** query surface, so the query must use workspace
table and column names. The fixed shape is:

```kusto
AppTraces
| where TimeGenerated >= datetime(2026-08-01T00:00:00Z)
    and TimeGenerated < datetime(2026-08-08T00:00:00Z)
| where Message == "return_decision_completed"
| extend outcome = tostring(Properties["decision.outcome"])
| summarize total_interactions=count(),
            successful_interactions=countif(
                outcome in ("approved", "denied", "escalated")
            )
```

The `traces` table with its `timestamp`, `message`, and `customDimensions`
columns belongs to the **Application Insights** classic query surface
(`az monitor app-insights query`, or the App Insights resource-scoped API).
Those names must not be used here: against a Log Analytics workspace they
resolve to nothing and the query fails. V1 uses the workspace surface only;
the correct workspace equivalents are `AppTraces`, `TimeGenerated`, `Message`,
and `Properties`. Any future App Insights transport would be a separate,
explicitly named adapter, not a silent column rename.

Only validated identifiers are interpolated into the shape above, and window
bounds are reserialized from parsed `datetime` values, never passed through
from user text.

## 9. Calculation rules

### 9.1 Authoritative total

`actual_window_usd` comes only from Cost Management at the declared workload
scope.

Azure Monitor token cost is attribution and diagnostic evidence. It is never
added to the Cost Management total. This invariant prevents double counting.

### 9.2 Window alignment

Only complete UTC days are compared. `complete_days` is
`(window.end - window.start).days`, and every observed cost row is validated
against the start-inclusive/end-exclusive boundary described in §8.1 before
any total is computed.

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
declared `maturity_policy` is complete, the actuals manifest's
`usage.interaction_status` is `pass`, and `successful_interactions` is
greater than zero; if any of those does not hold — including the simple case
of a zero-interaction window, or a window where the interaction query never
ran — it produces `unit_economics.status = not-verified` and a null
`cost_per_successful_interaction_usd`; division is not attempted. Token
metrics are never part of this gate (see §7.3): a `not-verified`
`usage.model_attribution_status` degrades `drivers.payg_ptu` and model-level
breakdowns, never `unit_economics.status`.
`unit_economics.target_status` compares the resulting cost
against SPEC's `baseline.target_cost_per_successful_interaction_usd` (see
§7.3) and is itself `not-verified` whenever `status` is `not-verified`.

### 9.4 Attribution and unmodeled costs

The reconciler maps Cost Management line items to projected resources by
resource ID first, then normalized Azure resource type. It emits:

- actual resources absent from the projection as `unmodeled`;
- projected resources with no actual observation as `not-observed`;
- actual cost that cannot be mapped as `unattributed`;
- `coverage.projection_attribution_coverage_pct` as actual cost mapped to a
  projected resource divided by total actual cost.

Unmodeled or unattributed cost remains inside `actual_window_usd`. It is not
dropped to make variance look better.

**Resource-ID normalization, including AOAI parent/child roll-up.** Cost
Management bills Azure OpenAI at the **account** level: its `ResourceId` and
`ResourceType` name `.../providers/Microsoft.CognitiveServices/accounts/<name>`
and `microsoft.cognitiveservices/accounts`. The projection, however, models
individual deployments as
`.../Microsoft.CognitiveServices/accounts/<name>/deployments/<deployment>`
with kind `Microsoft.CognitiveServices/accounts/deployments`, because token
pricing is per deployment. Matched naively, every AOAI deployment would be
reported as `not-observed` and the entire real AOAI bill as `unmodeled`.

The reconciler therefore normalizes each forecast deployment resource to its
parent account ID (everything up to and including `/accounts/<name>`, with
`/deployments/...` removed) and to the parent type
`microsoft.cognitiveservices/accounts` for matching purposes only. Several
forecast deployments can roll up to one account; their forecast cost sums
into that account's matched forecast figure. The roll-up is strictly
name-scoped: a deployment under `/accounts/aoai-a` never matches an actual
row for `/accounts/aoai-b`, so a second AOAI account in the same resource
group is still correctly reported as unmodeled or not-observed.

The original deployment-level IDs, deployment names, and per-deployment
forecast token volumes are preserved unchanged in the reconciliation's
resource detail and are what `drivers.payg_ptu` and model-level diagnostics
read. Roll-up affects cost matching only; it never erases deployment
granularity from the artifact.

**Type fallback is deliberately narrow.** When no resource ID matches, the
reconciler may fall back to the normalized ARM resource type, but only when
that fallback is unambiguous: there is exactly **one** unmatched forecast
resource and exactly **one** unmatched actual resource sharing that
normalized type. If two or more unmatched resources on either side share the
type, no pairing is guessed — every one of them stays unmatched (actual cost
becomes `unmodeled`, forecast entries become `not-observed`) and the artifact
records the ambiguity. Attributing cost by coin flip between two same-type
resources would produce a confident-looking but arbitrary per-resource
variance, which is worse than an explicit non-attribution.

### 9.5 Price-basis mismatch

Forecast price and billed actual price can differ because of EA/MCA
discounts, reservations, credits, and amortization.

The comparison is between two **price bases**, and only these two fields are
comparable: SPEC's `accounting.actual_billing_price_basis`
(`retail | ea | mca | unknown`) and its `accounting.forecast_price_basis`.
`actual_cost_basis: usage-pretax` is not part of this comparison at all — it
is the metric and source (§8), not a price list, and comparing it against a
price basis is a category error.

Both bases are recorded in `policy_snapshot` and shown in the report. When
they are unequal, or when `actual_billing_price_basis` is `unknown`, and SPEC
does not set `allow_basis_mismatch_for_verdict: true`, the tool reports the
numeric variance delta but sets `variance_status` to `not-verified`. With the
explicit opt-in, the delta is verdicted normally and the mismatch is stated in
the report.

### 9.6 Cache, spillover, and priority processing

These affect cost without changing the core contract:

- Cache-hit metrics are reported only when Azure Monitor or workspace
  interaction evidence exposes an attributable cached-token dimension.
  Missing data is `not-verified`, not a zero-percent cache rate.
- Spillover deployment/model usage is a separate attribution driver while its
  billed cost remains inside the Cost Management total.
- Priority Processing is captured from deployment configuration and observed
  billing dimensions when available. If the forecast did not model it, the
  reconciler identifies it as an unmodeled driver.

V1 does not require these optional dimensions to compute the complete actual
total or cost per successful interaction.

### 9.7 Cost column selection

The official Query API `2025-03-01` `Usage` contract's primary cost column is
`PreTaxCost`, and that is what v1 requests. Some responses have historically
been observed carrying `CostUSD` or `Cost` instead. The parser therefore
accepts exactly one of the following, matched case-insensitively, in this
priority order:

1. `PreTaxCost` — the official primary;
2. `CostUSD`;
3. `Cost`.

If none of the three is present, parsing fails with an explicit error; the
total is never inferred from an unrecognized column. If more than one is
present, the highest-priority column wins and the chosen name is recorded in
the actuals manifest as `cost.cost_column`.

This alias handling is **defensive compatibility**, not a documented
guarantee: this RFC does not claim that any particular account type returns
these aliases. It exists so that an alias response degrades into a labeled,
auditable total rather than an unexplained parse failure.

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
  AND projection_attribution_coverage_pct >= declared minimum
  AND price bases compatible or explicitly allowed
```

Only `coverage.projection_attribution_coverage_pct` is gated here. The actuals
manifest's `cost.resource_id_coverage_pct` is reported for source quality and
is never compared against a policy threshold.

| Condition | Result |
|---|---|
| All checks pass | `pass`; actual run-rate is primary operational number |
| Policy incomplete | `not-verified`; show forecast and raw actual period spend |
| Cost Management unavailable | `not-verified`; preserve forecast |
| Interaction query unavailable | actuals `status` stays `pass`; `usage.interaction_status` is `not-verified` and unit economics is `not-verified` |
| Token metrics unavailable | actuals `status` and unit economics may still pass; `usage.model_attribution_status` is `not-verified` and `drivers.payg_ptu` is `not-verified` |
| Window too recent to settle or too old to represent current operations | `not-verified`; never extrapolate silently |
| Scope contains unrelated workloads | `not-verified` unless SPEC declares tagged allocation and projection attribution coverage satisfies policy |

No broad exception handler may turn an evidence failure into an empty manifest.
The CLI returns a non-zero evidence exit code **after** writing every artifact
it was able to produce, never instead of writing them. An incomplete or invalid
SPEC section 14 policy is an emitted, labeled `not-verified` result, not an
early exit that suppresses evidence.

## 11. CLI shape

The existing projection commands remain unchanged. Add:

```bash
# Collect observed evidence only. --start/--end are required. --subscription
# and --resource-group are optional flags here: they default from
# AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP, and the command exits 2 if
# neither the flag nor the matching environment variable resolves one.
# --workspace-resource-id is optional with no environment fallback; omitting
# it only degrades interaction and token attribution, never the Cost
# Management total.
scripts/consumption_iq.py actuals \
  --start 2026-08-01 --end 2026-08-08

# Join existing projection, actuals, and SPEC section 14 policy.
# --forecast, --actuals-manifest, and --spec all default to the same
# canonical paths (DEFAULT_OUTPUT_MANIFEST, DEFAULT_ACTUALS_MANIFEST,
# DEFAULT_SPEC_PATH) the rest of the pipeline already reads and writes.
# This command issues no Azure calls: it reuses the canonical actuals
# evidence under the rule in §7.3.
scripts/consumption_iq.py reconcile

# Existing projection plus best-effort post-deploy evidence. --with-actuals
# requires --start/--end and resolves --subscription/--resource-group the
# same way `actuals` does.
scripts/consumption_iq.py run --all --with-actuals \
  --start 2026-08-01 --end 2026-08-08
```

`run --all` without `--with-actuals` preserves current behavior.

**Workspace identity is resolved internally.** Operators hold ARM resource IDs,
not workspace GUIDs, so the CLI accepts only
`--workspace-resource-id <arm-resource-id>`. The collector resolves it once to
the workspace `customerId` that `az monitor log-analytics query --workspace`
requires:

```bash
az monitor log-analytics workspace show --ids <resource-id> \
  --query customerId -o tsv
```

Callers never pass a `customerId` directly; there is no `--workspace-id` flag
and no environment fallback. A blank, malformed, or non-GUID result degrades
the interaction evidence only: it produces a warning and
`usage.interaction_status: not-verified`, and the Cost Management total is
still collected and still yields `status: pass`.

Suggested evidence exit codes:

| Code | Meaning |
|---:|---|
| 0 | Requested artifacts produced and mature |
| 2 | Missing prerequisite (including an unresolved `--subscription`/`--resource-group` or a missing `--start`/`--end`) |
| 3 | Required live source unavailable (Cost Management only) |
| 4 | Existing incomplete load profile |
| 5 | Artifacts written, but the requested verdict is not verified |

Exit 5 always follows a successful write. Because the top-level actuals
`status` depends only on Cost Management (§7.2), an `actuals` run whose
interaction query failed still returns `0`: it produced exactly the cost
artifact it promised. The same evidence gap surfaces as exit `5` from
`reconcile`, because unit economics could not be verified without an
interaction count. Exit 5 is advisory in threadlight-auto and
production-ready. It must not block the base design-to-deploy path.

### 11.1 Rate limiting

Cost Management throttles. The collector retries only on an observed 429 or
5xx, using bounded exponential delays of 2, 4, then 8 seconds, and injects its
sleep function so tests never wait.

It does **not** read the `Retry-After` response header. Azure Cost Management
does publish that header, but `az rest` does not reliably surface response
headers to the caller — the CLI returns the response body on success and a
formatted error on failure, so there is no dependable channel to parse the
header from. Pretending to honor a header the transport cannot expose would be
a fiction that tests could only validate against a fake. Consuming the real
header would require calling the REST API through the Azure SDK or an
authenticated HTTP client instead of `az rest`; that is a possible future
change, explicitly not v1.

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
- Actuals are attempted only when requested. An incomplete SPEC section 14
  policy does not suppress the attempt: raw actuals are still collected and
  emitted, and the reconciliation is emitted as `not-verified`.
- Exit 5 records `cost-reconciliation: not-verified` and continues.
- No retry loop waits for Cost Management ingestion.

### threadlight-router-bench

Move the pure Azure Monitor payload parser and token-cost helpers to a shared
stdlib-only module owned by `threadlight-consumption-iq`. Both callers keep
their own orchestration:

- router-bench owns run-window benchmark attribution;
- Consumption IQ owns deployment-window operational attribution.

This cross-skill import is safe because this repository is the deployment
unit: skills ship together from one checkout, so the sibling path always
exists in a valid installation. It is still made explicit rather than assumed
— router-bench resolves the sibling module by repository-relative path and
raises a clear, named error if it is absent, instead of silently falling back
to a second copy of the parser. One source of truth, one loud failure mode.

No benchmark report becomes a source of billed actual cost.

## 13. Test strategy

All calculation and parsing tests are offline and fixture-driven.

### Required fixtures

- Cost Management response with multiple Azure resource types.
- Same response with an unmodeled resource.
- Same response with unattributed cost.
- Daily-granularity response carrying `UsageDate`, including in-window,
  boundary, and out-of-window rows.
- Alias cost-column variants: `PreTaxCost`, `CostUSD`, `Cost`, both present
  together, and none present.
- AOAI account-level rows against deployment-level forecast resources,
  including a second account that must not be matched.
- Azure Monitor token metrics split by deployment and model.
- `AppTraces` workspace query results with successes, failures, and
  escalations, in the response shape `az monitor log-analytics query` returns.
- Stale, incomplete, and permission-denied source responses.

### Required invariants

1. Existing `cost-manifest.json` fixtures remain byte-for-byte unchanged.
2. Repriced token cost is never added to Cost Management total.
3. Unmodeled cost remains in the authoritative actual total.
4. Forecast and actual windows use the same complete-day boundary, validated
   against observed `UsageDate` values.
5. Zero successes yields `not-verified`, not zero cost per interaction.
6. Missing or invalid maturity policy yields an emitted `not-verified`
   artifact, never a suppressed one.
7. Basis mismatch between `actual_billing_price_basis` and
   `forecast_price_basis` yields a numeric delta but no pass/fail verdict
   unless explicitly allowed.
8. Missing Monitoring Reader degrades model attribution only.
9. Missing Cost Management Reader prevents actual-total verification.
10. Production-ready consumes manifests and does not reimplement reconciliation.
11. Unmodeled actual cost lowers projection attribution coverage even when
    resource-ID coverage is 100%.
12. The interaction query uses the workspace surface (`AppTraces`,
    `TimeGenerated`, `Message`, `Properties`) and never the App Insights
    `traces`/`customDimensions` names.

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

### 14.1 Section 14 enforcement is opt-in first

Making section 14 unconditionally mandatory in `check_pilot_contract.py` would
retroactively invalidate every pilot SPEC authored before this design — an
immediate regression for existing callers who did nothing wrong. Enforcement
is therefore gated behind a new `--require-value-model` flag:

- **Without the flag (default):** if a `## 14. Value Model` section is
  present, its shape is validated exactly as described in §8, so a
  half-written section 14 still fails loudly. If it is absent, the pilot
  remains valid — a legacy pilot passes unchanged.
- **With the flag:** an absent section 14 is a failure
  (`design.spec.no-section-14`).

The design-only E2E and the repository's own contract gate pass
`--require-value-model`, and the shipped golden example carries a complete
section 14, so the new contract is fully exercised in CI from PR 2 onward
without breaking anyone else. The flag is the documented migration path: once
downstream pilots have adopted section 14, a later release flips the default
and the flag becomes a no-op, then is deprecated. That flip is deliberately
out of scope here.

## 15. Acceptance criteria

The design is implemented when:

- The current complete Azure projection is unchanged and its tests still pass.
- A read-only run can emit actuals for a dedicated pilot Resource Group.
- Reconciliation is deterministic from forecast, actuals, and SPEC policy.
- Complete Azure actual cost is never confused with token reprice.
- Cost per successful interaction includes all in-scope Azure actual cost.
- Missing or immature evidence is visibly `not-verified`.
- Incomplete policy still produces written artifacts before any non-zero exit.
- Existing pilots without SPEC section 14 keep passing the contract checker
  until `--require-value-model` is passed.
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
| Retail forecast vs negotiated actual creates fake savings | Record both price bases explicitly; fail closed on mismatch or `unknown` |
| Generic "success" metric is meaningless | Workload declares the trace predicate |
| Wrong query surface silently returns nothing | Workspace table/column names are pinned and tested; App Insights names are explicitly out of contract |
| Ambiguous type-based attribution invents per-resource variance | Type fallback applies only to a unique unmatched pair |
| AOAI account billing vs deployment forecast looks 100% unmodeled | Name-scoped parent roll-up for matching, deployment detail preserved |
| Rate limiting handled by guesswork | Bounded exponential retry on observed 429; no reliance on unavailable response headers |
| Mandatory section 14 breaks existing pilots | Enforcement is opt-in via `--require-value-model` with a documented migration path |
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
| D11 | Section 14 enforcement ships opt-in via `--require-value-model` |
| D12 | Interaction evidence uses the Log Analytics workspace surface (`AppTraces`) |
| D13 | Actuals `status` is produced by Cost Management alone; optional evidence carries its own status |
| D14 | Source resource-ID coverage and projection attribution coverage are distinct; only the latter is policy-gated |
