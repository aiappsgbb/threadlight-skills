# `specs/cost-actuals-manifest.json` schema (`threadlight-cost-actuals/v1`)

> Strict v1 schema. Produced offline by
> `skills/threadlight-consumption-iq/scripts/cost_actuals.py`'s
> `build_actuals_manifest` from already-fetched Azure Cost Management
> `Usage` Query API pages (`2025-03-01`) plus optional Azure Monitor token
> metrics and Log Analytics workspace interaction counts. This module never
> calls Azure itself — Task 9's live CLI adapter is the only caller that
> does, and it hands this parser plain `dict`/`list` response shapes.
>
> API reference:
> <https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01>

## This is evidence, not a verdict

`cost-actuals-manifest.json` records **what was observed**: the raw period
total, per-resource breakdown, and (when available) token/interaction
counts. It never computes variance against a forecast, a maturity verdict,
or a pass/fail unit-economics gate — those live in the separate
reconciliation artifact (Task 8), which *consumes* this manifest alongside
`cost-manifest.json` and SPEC §14's `value_model` policy. Do not add
verdict-shaped fields here; add them to the reconciliation artifact instead.

## Top-level shape

```jsonc
{
  "schema": "threadlight-cost-actuals/v1",
  "generated_at": "2026-08-10T00:00:00Z",
  "status": "pass",
  "scope": {
    "subscription_id": "00000000-0000-0000-0000-000000000000",
    "resource_group": "rg-pilot-prod"
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
    "resources": [
      {
        "resource_id": "/subscriptions/.../resourceGroups/rg-pilot-prod/providers/Microsoft.App/containerApps/agent",
        "resource_type": "microsoft.app/containerapps",
        "service_name": "Azure Container Apps",
        "period_cost_usd": 87.15
      }
    ],
    "unattributed_usd": 0.42,
    "resource_id_coverage_pct": 0.998
  },
  "usage": {
    "interaction_status": "pass",
    "model_attribution_status": "not-verified",
    "total_interactions": 1240,
    "successful_interactions": 1178,
    "success_predicate_ref": "SPEC.md#section-14-value-model",
    "models": []
  },
  "provenance": {
    "query_api_version": "2025-03-01"
  },
  "warnings": []
}
```

No unknown top-level keys are permitted. The extension points are
`cost.resources[].*` (add resource-specific fields there, not new top-level
keys) and `usage.models[]` entries.

## `schema` / `generated_at` / `status`

* `schema` is always the literal string `"threadlight-cost-actuals/v1"`.
* `generated_at` is the UTC ISO-8601 instant (`YYYY-MM-DDTHH:MM:SSZ`) the
  manifest was assembled, supplied by the caller (never inferred). It must
  not be before `window.end` — see [Window](#window).
* `status` is `"pass"` or `"not-verified"`. **It has exactly one producer
  rule**: `pass` if and only if the Cost Management source parsed, `scope`
  is a non-empty mapping, and `window` validated. `build_actuals_manifest`
  raises `ActualsEvidenceError` instead of returning a `not-verified`
  manifest when any of those three fail — so in practice a returned
  manifest is always `status: "pass"`; a higher-level caller (Task 8's
  reconciliation, or a live CLI wrapper) is what surfaces a
  `not-verified` collection outcome for a caller that could not produce a
  cost page at all (e.g. missing Cost Management Reader). Missing
  `usage.models` token metrics and missing `usage.total_interactions`
  interaction counts are recorded as their own `not-verified` sub-statuses
  below and **never** change this top-level value.

## `scope`

A non-empty mapping identifying what was queried (e.g.
`subscription_id`, `resource_group`). Passed through byte-for-byte from the
caller — this module does not interpret or validate its keys beyond
"non-empty mapping". An empty or non-mapping `scope` is rejected with
`ActualsEvidenceError("scope must be a non-empty mapping")`, because an
actuals manifest with no declared scope cannot be trusted to describe what
it billed.

## `window`

Start-inclusive, end-exclusive UTC window: a row is in-window iff
`window.start <= usage_date < window.end`. This is enforced by re-deriving
`usage_date` from every observed `UsageDate` cell and rejecting (not
dropping) any row outside that range — the Query API's own inclusivity is
never trusted on its own (RFC §8.1).

* `start` / `end` — UTC ISO-8601 instants, both required to be
  timezone-aware and normalized to UTC before formatting.
* `complete_days` — `(end - start).days`, computed from the **declared**
  boundaries, not from which days happened to return rows. A day with zero
  charges is still a complete day.
* `settlement_age_hours` / `window_end_age_days` — both computed as
  `generated_at - window.end`, because the Query API does not expose a
  trustworthy per-response "data through" timestamp; this is a measurable,
  conservative substitute, not a claim that every provider has finished
  billing. `generated_at` before `window.end` is rejected outright (a
  negative settlement age is evidence of a caller bug, not a value to
  silently clamp to zero).

## `cost`

* `basis` — always the literal `"usage-pretax"`. This names the **metric
  and source** (Cost Management `Usage` Query API, pre-tax cost) — it is
  **not** an invoice and **not** a price basis. It is not comparable to
  SPEC's `accounting.actual_billing_price_basis` /
  `accounting.forecast_price_basis` (`retail | ea | mca | unknown`), which
  is a separate, reconciliation-level comparison (RFC §9.5). Conflating the
  two is a category error.
* `cost_column` — the **original-cased** Query API column name this
  manifest's numbers actually came from: `"PreTaxCost"` (the official
  `2025-03-01` primary), or the defensive-compatibility aliases
  `"CostUSD"` / `"Cost"` (matched case-insensitively, `PreTaxCost` wins
  when more than one is present). Recording the original casing lets a
  reader tell official evidence from an alias without re-querying. None of
  the three present is a parse error, never an inferred zero.
* `currency` — the ISO currency code as observed (first row's casing is
  preserved; comparison across rows is case-insensitive). Exactly one
  currency is accepted per manifest; more than one is a parse error.
* `period_total_usd` — the raw actual total for the declared window,
  **including** unmodeled/unattributed cost and negative refund rows
  (refunds are preserved, never clipped to zero). A monthly run-rate is
  derived only in the reconciliation artifact, never stored here.
* `resources[]` — one entry per distinct resource ID observed (normalized
  with `.casefold().rstrip("/")` for grouping only; the original,
  first-observed ID string is what is reported), summed across every page
  and every in-window day:
  * `resource_id`, `resource_type`, `service_name` — as observed.
  * `period_cost_usd` — the resource's summed cost for the window.
* `unattributed_usd` — cost from rows with a blank resource ID. It remains
  inside `period_total_usd`; it is never dropped to make the total look
  more attributable than it is.
* `resource_id_coverage_pct` — a **source-quality** measure only: gross
  absolute observed cost that carries a nonblank resource ID, divided by
  total gross absolute observed cost (`null` when gross absolute cost is
  exactly zero — there is nothing to divide, so this is never a
  division-by-zero or a fabricated `0`/`1`). Both the numerator and
  denominator are summed as **absolute** values specifically so that
  refund rows cannot push the ratio outside `[0, 1]` or negative; a
  refund's true sign is still preserved in `period_cost_usd` /
  `period_total_usd` / `unattributed_usd`. **This is explicitly not**
  the reconciliation artifact's `coverage.projection_attribution_coverage_pct`
  (actual cost mapped to a *projected* resource, divided by total actual
  cost) — a window can have 100% resource-ID coverage here and very low
  projection-attribution coverage there. Never conflate the two, and never
  let this field gate anything: it is diagnostic, not a pass/fail input.

## `usage`

Two independently-verified evidence streams, each carrying its own
`pass | not-verified` sub-status. Neither can demote the top-level
`status`.

* `interaction_status` — `pass` only when `interaction_counts` was
  supplied to `build_actuals_manifest` (i.e. the interaction query
  actually ran and returned a parsable row set — a genuinely observed zero
  is `pass` with `0` counts, never `not-verified`). `not-verified` when
  `interaction_counts` is `None` (skipped, failed, or unparsable upstream),
  in which case `total_interactions` and `successful_interactions` are
  `null` — **never** `0`, so an unobserved value can never be confused with
  a real zero.
* `model_attribution_status` — `pass` only when `token_series` was
  supplied (including an empty list — a genuinely observed "no rows" is
  still `pass`); `not-verified` when it is `None`. `models` is a **list**,
  never a keyed object (a single window can observe many
  deployment/model combinations), and is `[]` whenever
  `model_attribution_status` is `not-verified`.
* `success_predicate_ref` — a fixed pointer to where the success predicate
  driving `successful_interactions` is declared
  (`SPEC.md#section-14-value-model`); it is not re-derived here.

## `provenance` / `warnings`

Passed through verbatim (deep-copied, never the caller's original object)
from `build_actuals_manifest`'s `provenance` / `warnings` arguments. This
module does not interpret their contents; it is where a caller records,
e.g., the exact API version queried or a note that a particular evidence
stream was skipped.

## Consumers

* **Task 8's reconciliation artifact** consumes this manifest (never
  reimplements its parsing) alongside `cost-manifest.json` and SPEC §14's
  `value_model` to produce `coverage.projection_attribution_coverage_pct`,
  variance, and unit-economics verdicts.
* **`threadlight-production-ready`**'s `COST-102` / `COST-103` checks (Task
  13) read the reconciliation artifact, not this one, directly.

## Forward compatibility

* A future `v2` may add reservation/commitment-aware cost bases or a
  `basis: "usage-amortized"` variant. Any new basis value is a new,
  explicitly-versioned field, not a silent redefinition of
  `"usage-pretax"`.
* A future version may add a per-resource `extra: {}` free-form bag (as
  `cost-manifest-schema.md`'s `ResourceProjection.extra` already does) as
  the extension point for resource-specific fields, rather than inventing
  new top-level keys.
