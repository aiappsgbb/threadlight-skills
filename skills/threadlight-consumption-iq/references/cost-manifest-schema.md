# `specs/cost-manifest.json` schema (v1)

> Strict v1 schema. Consumed by `threadlight-production-ready`'s cost
> pillar (`COST-005`, new `COST-006`) and `threadlight-auto`'s
> resumability check.

## Top-level shape

```jsonc
{
  "schema_version": "1.0",
  "generated_at": "2026-06-12T14:00:00Z",          // ISO-8601 UTC; used for 30-day staleness check
  "deploy_ref": "<azd env>/<deployment id>",       // pre_deploy=true sets this to "pre-deploy"
  "pre_deploy": false,                              // true => skipped azd env walk
  "load_profile_ref": "specs/SPEC.md#section-12-load-profile",
  "currency": "USD",
  "price_basis": "retail",                          // v1: always "retail" (v2 will add "ea" | "mca")
  "resources": [ <ResourceProjection>, ... ],
  "recommendations": [ <Recommendation>, ... ],
  "totals": {
    "monthly_cost_current_usd": 4820.10,
    "monthly_cost_recommended_usd": 3110.40,
    "monthly_savings_potential_usd": 1709.70
  }
}
```

## `ResourceProjection`

```jsonc
{
  "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
  "resource_id": "/subscriptions/.../resourceGroups/.../providers/Microsoft.CognitiveServices/accounts/foo/deployments/gpt4o",
  "logical_name": "gpt4o",                          // Bicep symbol name
  "region": "eastus2",
  "current_sku": {
    "name": "gpt-4o",
    "tier": "PAYG",                                 // PAYG | PTU | <azure-sku-name>
    "region": "eastus2",
    "capacity": 100,                                // resource-specific (tokens/min, PTU units, vCPU, RU, ...)
    "extra": { "model_version": "2024-08-06" }       // resource-specific
  },
  "monthly_cost_usd": 1240.50,
  "monthly_units_consumed": {                       // shape varies by resource
    "input_tokens": 22000000,
    "output_tokens": 11000000
  },
  "price_source": "live",                           // live | fixture | fallback (worst-case across line items)
  "alternatives": [ <Alternative>, ... ]
}
```

## `Alternative`

```jsonc
{
  "sku": {
    "name": "gpt-4o",
    "tier": "PTU",
    "units": 25,
    "region": "eastus2",
    "extra": { ... }
  },
  "monthly_cost_usd": 980.00,
  "delta_usd": -260.50,                             // negative => cheaper
  "delta_pct": -0.21,
  "satisfies_constraints": true,                    // set by the projector
  "caveats": [
    "PTU commitment is monthly; no overflow declared",
    "Requires PTU quota in eastus2"
  ],
  "rationale": "Peak RPS × tokens/req crosses PTU break-even at 25 units."
}
```

## `Recommendation`

One entry per resource where the cheapest constraint-satisfying
alternative is cheaper than `current_sku`. Sorted by
`monthly_savings_usd` desc.

```jsonc
{
  "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
  "resource_id": "/subscriptions/.../...",
  "logical_name": "gpt4o",
  "current_sku": { ... },
  "recommended_sku": { ... },
  "monthly_savings_usd": 260.50,
  "monthly_savings_pct": 0.21,
  "priority": "high",                               // high (>$100/mo) | med (>$25/mo) | low
  "rationale": "Peak RPS × tokens/req crosses PTU break-even at 25 units; declared load is steady.",
  "caveats": ["Requires PTU quota in eastus2"]
}
```

## Consumer contracts

### `threadlight-production-ready` — `COST-005` (tightened)

Passes only if:
  * `docs/cost-projection.md` exists, AND
  * `specs/cost-manifest.json` exists with `schema_version >= "1.0"`, AND
  * `generated_at` is within 30 days of the latest deploy timestamp.

Otherwise: `should-fix` (was: only checked that the markdown existed).

### `threadlight-production-ready` — `COST-006` (new)

Walks `recommendations[]`. For each entry where
`current_sku == deployed selector` (i.e., still unaddressed):
  * `monthly_savings_usd > 100` → `must-fix`
  * `monthly_savings_usd > 25` → `should-fix`
  * otherwise → `info` (rolled into the report; doesn't gate)

### `threadlight-auto` — resumability check

On a re-run, skip the wizard phase if:
  * SPEC § 12 `load_profile{}` is complete, AND
  * `cost-manifest.json.generated_at > last_deploy_timestamp`.

## Strictness

v1 is strict: unknown top-level keys are rejected by the
`production-ready` parser. Per-resource `extra: {}` and per-alternative
`extra: {}` are the extension points — use those for resource-specific
fields rather than inventing new top-level keys.

## Forward compatibility (v2 sketch)

  * `price_basis: "ea" | "mca"` with `discount_multiplier` field
  * `forecast: { months_out: 12, projection: [...] }` for time-series
  * `reservations: [...]` for Savings Plans modelling
  * `multi_region: [...]` for failover cost
