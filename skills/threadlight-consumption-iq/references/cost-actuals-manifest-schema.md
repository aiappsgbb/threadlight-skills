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

* `start` / `end` — UTC ISO-8601 instants. Both are required to be
  timezone-aware, normalized to an offset of exactly zero (UTC), **and at
  exact midnight** (`00:00:00.000000`). A non-UTC offset or a non-midnight
  time of day is rejected outright rather than silently truncated to a
  calendar day — a window boundary that isn't a clean day edge makes
  `complete_days` a lie. This requirement is specific to `start`/`end`; it
  does **not** apply to `generated_at`, which is a real point-in-time
  timestamp and must never be forced onto a day boundary.
* `complete_days` — `(end - start).days`, computed from the **declared**
  boundaries, not from which days happened to return rows. Because `start`
  and `end` are both guaranteed UTC midnight, this is always an exact
  integer day count with no fractional-day ambiguity. A day with zero
  charges is still a complete day.
* `settlement_age_hours` / `window_end_age_days` — both computed as
  `generated_at - window.end`, because the Query API does not expose a
  trustworthy per-response "data through" timestamp; this is a measurable,
  conservative substitute, not a claim that every provider has finished
  billing. `generated_at` before `window.end` is rejected outright (a
  negative settlement age is evidence of a caller bug, not a value to
  silently clamp to zero).

### `UsageDate` normalization

Each Cost Management row's `UsageDate` cell is normalized to a plain `date`
before window-membership is checked. Three input shapes are accepted, all
restricted to **ASCII** `0`-`9` digits only (Unicode decimal digits such as
Arabic-Indic or fullwidth forms are rejected even though Python's `\d` regex
class and `int()` would otherwise silently parse them — accepting them would
let a row bucket into a day nobody actually wrote in ASCII):

* an integer or numeric string in `YYYYMMDD` form (Cost Management's native
  format);
* a plain ISO date string (`YYYY-MM-DD`);
* an ISO-8601 datetime string, but **only** when it is timezone-aware with
  an offset of exactly zero (UTC) **and** its time-of-day is exact midnight.
  A naive datetime, a non-UTC offset, or a non-midnight UTC time is
  rejected rather than accepted and silently reduced to a "local-date
  bucket" — that would let two different instants (or the same instant in
  two different reported offsets) map to two different observed days, or
  hide a genuinely different day behind a wall-clock read of the wrong
  offset.

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
  `"CostUSD"` / `"Cost"` (matched case-insensitively via
  `select_cost_column`, which casefolds whatever raw-cased column names it
  is given — callers are never required to pre-casefold — `PreTaxCost`
  wins when more than one is present). Recording the original casing lets a
  reader tell official evidence from an alias without re-querying. None of
  the three present is a parse error, never an inferred zero.
* `currency` — **v1 is USD-only.** Every field under `cost` is named with a
  `_usd` suffix and the forecast side of the reconciliation contract
  (`cost-manifest-schema.md`) is likewise USD-only, so a manifest reporting
  a different currency under those names would silently misrepresent the
  numbers. The observed `Currency` cell must casefold to `"usd"`; any other
  value (even if every row agrees on it, e.g. an all-`"EUR"` page) is
  rejected with `ActualsEvidenceError` rather than accepted as "some other
  currency labeled as if it were USD." More than one currency value across
  rows is also a parse error, independent of the USD check. When a
  Cost Management page is present but has **zero rows** (see
  [`cost_pages`](#cost_pages-empty-vs-zero-rows) below), no `Currency` cell
  was ever actually observed, so `currency` is `null` rather than fabricated
  as `"USD"` — a caller must not read a `null` currency as "assumed USD."
* `period_total_usd` — the raw actual total for the declared window,
  **including** unmodeled/unattributed cost and negative refund rows
  (refunds are preserved, never clipped to zero). A monthly run-rate is
  derived only in the reconciliation artifact, never stored here.
* `resources[]` — one entry per distinct resource ID observed (normalized
  with `.casefold().rstrip("/")` for grouping only; the original,
  first-observed ID string is what is reported), summed across every page
  and every in-window day:
  * `resource_id` — the first-observed raw ID string for that normalized
    key; later rows that differ only by case or a trailing `/` merge into
    the same bucket rather than creating a duplicate resource entry.
  * `resource_type`, `service_name` — as observed. If the row that first
    creates a resource's bucket left one of these blank but a **later**
    row for the same resource reports a nonblank value, the nonblank value
    backfills it — a resource is never permanently mislabeled blank just
    because Cost Management happened to return its identifying row before
    its typed row. If two rows for the same resource report **different,
    both-nonblank** values for the same field, that is a genuine data
    conflict and is rejected with `ActualsEvidenceError` naming the field
    and resource, rather than silently keeping whichever value arrived
    first.
  * `period_cost_usd` — the resource's summed cost for the window, quantized
    per the [rounding policy](#money-rounding-and-the-accounting-identity)
    below.
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

### `cost_pages`: empty vs. zero rows

An empty `cost_pages` list (`[]`) — i.e. Cost Management was never actually
queried, or the caller has nothing at all to hand this parser — is rejected
with `ActualsEvidenceError("... no Cost Management pages ...")`. This is
distinct from a **present** page whose `rows` list is empty: that is a
genuinely observed zero (Cost Management ran and reported nothing for the
window) and is accepted, producing `period_total_usd: 0.0`, `resources: []`,
`unattributed_usd: 0.0`, and — because no `Currency` cell was ever read —
`currency: null` (see above). Callers must not collapse these two cases:
"never asked" and "asked, got nothing" carry different evidentiary weight.

### Money rounding and the accounting identity

All money math is done in `Decimal`, summing every row's exact observed
value (via `Decimal(str(raw_float))`, which recovers the caller's intended
decimal text rather than the underlying binary float) before any rounding
happens. Quantization to whole cents uses `Decimal.quantize(Decimal("0.01"),
rounding=ROUND_HALF_UP)` — true half-up rounding, e.g. `2.675` quantizes to
`2.68`. Python's built-in `round()` on a `float` is never used for money: it
rounds half-to-even on the *binary* approximation of the value (`2.675` is
actually stored as `2.67499999999999982...`, so `round(2.675, 2) == 2.67`,
not the correct `2.68`). Values are cast from `Decimal` to `float` only
after this quantization step, and are never re-rounded afterward.

Because each resource bucket and `unattributed_usd` are quantized
**independently**, their sum can differ from the total (computed once,
directly, from the exact unrounded grand sum) by up to a cent whenever the
input contains sub-cent values such as `0.005`-per-row half-cent charges.
To guarantee `period_total_usd == sum(resources[].period_cost_usd) +
unattributed_usd` **exactly**, any such residual is deterministically
allocated to the resource bucket with the largest absolute quantized value
(ties broken by the lexicographically smallest normalized resource key, so
the choice is reproducible run to run); if there are no resource buckets at
all, the residual goes to `unattributed_usd` instead. This never invents a
new field or a hidden adjustment record — the reconciled numbers *are* the
reported numbers — but the policy itself is documented here precisely so
the adjustment is inspectable rather than a silent, unexplained cent
appearing or disappearing.



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
