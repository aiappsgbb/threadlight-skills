# `rollout_profile{}` schema (pre-sales phased estimate)

> The input contract for **pre-sales mode** (`consumption_iq estimate
> --rollout <path>`). A rollout profile models how a customer *adopts* the
> workload over N phases — each phase a different topology + load + hardening
> posture. JSON or YAML. Every figure derived from it is an **ESTIMATE at
> public list prices, never a quote.**

## Top-level shape

```jsonc
{
  "customer": "Generic Pilot",                 // neutral label; appears on the one-pager
  "currency": "USD",
  "current_phase": "poc",                       // which phase the top-level totals mirror
  "discount": {                                 // OPTIONAL — EA/MCA assumption, not a contract
    "multiplier": 0.85,                          // in (0, 1]; 1.0 (or omitted) => retail only
    "basis": "ea"                                // retail | ea | mca
  },
  "benchmark": {                                // OPTIONAL — anchor for the narrative
    "metric": "queries_per_day",
    "value": 12000
  },
  "phases": [ <Phase>, ... ]
}
```

## `Phase`

```jsonc
{
  "id": "poc",
  "label": "Phase 1 — POC",
  "posture": "demo",                            // demo | production | production-hardened
  "audience": "internal",                       // internal | customer (one-pager classification)
  "load_profile": { <LoadProfile> },            // SAME schema as references/load-profile-schema.md
  "benchmark": { ... }                          // OPTIONAL per-phase override
}
```

### `posture` → what gets added

| Posture | Hardening / estate delta |
|---|---|
| `demo` | none (pilot only) |
| `production` | Private Endpoints + zone redundancy |
| `production-hardened` | everything in `production` **plus** Front Door + WAF, Defender, Sentinel, DDoS, multi-region DR, a non-prod estate copy |

The delta comes from `references/hardening-delta-catalog.json` (cumulative —
`production-hardened` repeats every `production` line). Items tagged
`shared_platform_billed: true` (Defender, Sentinel, DDoS) are amortised across
the estate, not charged wholly to this workload.

### `load_profile{}` is the v1 schema, in full

Each phase's `load_profile` **must** satisfy the same `REQUIRED_FIELDS` +
`REQUIRED_CONSTRAINTS` the CLI enforces for SPEC § 12 (see
`load-profile-schema.md`). The CLI is **fail-fast**: an incomplete phase load
profile raises `RolloutProfileError` (exit 4). In-process callers
(`estimate.run_presales`) are lenient and default missing fields.

## Worked example

See `references/fixtures/sample-presales-rollout/rollout.json` — a complete
3-phase profile (POC `demo` → expansion `production` → business-wide
`production-hardened`) with its expected golden estimate + one-pager.
