# `specs/cost-estimate-manifest.json` schema (1.1 — pre-sales)

> The machine-readable output of **pre-sales mode**. A superset of the v1
> `cost-manifest.json` (`schema_version: "1.0"`): same `ResourceProjection`
> and `Recommendation` shapes, wrapped in a phased envelope. **Backward
> compatible** — top-level `totals.*` mirror the `current_phase`, so
> `threadlight-production-ready`'s `COST-005`/`COST-006` still read a number.
>
> Every `*_usd` figure is an **ESTIMATE at public list prices, not a quote.**

## Top-level shape

```jsonc
{
  "schema_version": "1.1",
  "pre_sales": true,
  "generated_at": "2026-06-22T12:00:00+00:00",
  "deploy_ref": "<rollout>/<id>",               // no azd env in pre-sales; names the rollout
  "currency": "USD",
  "customer": "Generic Pilot",                  // HTML-escaped onto the one-pager
  "price_basis": "retail",                      // retail | ea | mca (= discount.basis when applied)
  "current_phase": "poc",                       // which phase the top-level totals mirror
  "phases": [ <Phase>, ... ],
  "totals": { <PhaseTotals> },                  // MIRRORS phases[current_phase].totals
  "discount": { <Discount> },
  "benchmark": { "metric": "queries_per_day", "value": 12000 }   // OPTIONAL
}
```

## `Phase`

```jsonc
{
  "id": "poc",
  "label": "Phase 1 — POC",
  "posture": "demo",                            // demo | production | production-hardened
  "audience": "internal",                       // internal | customer
  "resources":      [ <ResourceProjection>, ... ],   // v1 shape (see cost-manifest-schema.md)
  "hardening_delta":[ <HardeningLine>, ... ],        // [] for demo
  "recommendations":[ <Recommendation>, ... ],       // scored on the current phase only
  "totals": { <PhaseTotals> }
}
```

### `PhaseTotals`

```jsonc
{
  "monthly_cost_resources_usd": 628.89,         // sum of phase resources
  "monthly_cost_hardening_usd": 166.0,          // sum of hardening_delta
  "monthly_cost_hardening_shared_usd": 0.0,     // portion billed once estate-wide
  "monthly_cost_current_usd": 794.89,           // resources + hardening
  "monthly_cost_recommended_usd": 328.89,       // after applying recommendations
  "monthly_savings_potential_usd": 466.0,       // current - recommended
  "monthly_cost_current_discounted_usd": 675.66 // present ONLY when discount.applied
}
```

> `monthly_cost_hardening_shared_usd` is the subset of `monthly_cost_hardening_usd`
> coming from `shared_platform_billed` lines (Defender / Sentinel / DDoS). It is
> **kept inside** `monthly_cost_current_usd` as a conservative upper bound, but
> broken out so a seller can be honest that the customer may already pay it
> across the estate rather than wholly against this workload.

> The emitter **recomputes** every total from the line items — it never trusts
> upstream sums.

### `HardeningLine`

```jsonc
{
  "component": "Private Endpoints (x6 core resources)",
  "category": "networking",
  "monthly_cost_usd": 46.0,
  "shared_platform_billed": false,              // true => amortised across the estate
  "price_source": "fallback",                   // live | fixture | fallback
  "rationale": "..."
}
```

### `Discount`

```jsonc
{
  "applied": true,                              // false => NO *_discounted_* keys anywhere
  "multiplier": 0.85,                           // in (0, 1]
  "basis": "ea",                                // retail | ea | mca
  "caveats": [ "Discounted figures apply a flat 15% EA multiplier ... not a quote ..." ]
}
```

When `applied` is `false` (retail basis or `multiplier == 1.0`) the manifest
carries **no** `*_discounted_usd` keys and `price_basis` stays `"retail"`.
