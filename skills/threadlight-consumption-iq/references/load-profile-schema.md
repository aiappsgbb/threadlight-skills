# SPEC § 12 `load_profile{}` schema

> Authored by `threadlight-consumption-iq`. Lives **inside** SPEC § 12
> (production-readiness declarations) as a YAML sub-block.
> `threadlight-design` emits an empty skeleton; this skill's wizard
> fills it in; subsequent re-runs are non-interactive.

## Why this lives in SPEC § 12

SPEC § 12 already declares everything about the **operational** posture
of the pilot (target posture, residency, RTO/RPO, SLA, incident owner).
The production load assumptions belong here too — they are the same kind
of "operational truth" the customer signs off on at architecture
review. Putting `load_profile{}` in § 12 also means
`threadlight-production-ready`'s pillar-10 cost projection has the same
contract surface it already reads.

## Schema (v1)

```yaml
load_profile:
  workload_class: chat-agent | batch | scheduled | hybrid
  peak_concurrent_sessions: 50
  avg_requests_per_session: 8
  avg_tokens_per_request: 1500       # combined input + output
  peak_requests_per_second: 12
  business_hours_only: true          # 8h/day × 5d/week vs 24/7
  cosmos_gb_year_one: 50
  storage_gb_year_one: 100
  ai_search_documents: 50000
  monthly_growth_rate: 0.15          # decimal; 0.15 = 15%/mo
  declared_constraints:
    max_p95_latency_ms: 2500
    min_redundancy: zone-redundant   # none | zone-redundant | geo-redundant
    pinned_region: eastus2           # optional; blocks cross-region recs
```

### Field reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `workload_class` | enum | yes | drives the wizard's default-suggestion table and the projector's request-distribution assumption (batch concentrates work; scheduled is bursty; hybrid uses both budgets) |
| `peak_concurrent_sessions` | int ≥ 1 | yes | number of users / agents active at peak |
| `avg_requests_per_session` | int ≥ 1 | yes | LLM calls (or equivalent) per session |
| `avg_tokens_per_request` | int ≥ 1 | yes | combined input + output |
| `peak_requests_per_second` | float ≥ 0 | yes | derived in the wizard from the three previous fields, with a manual override |
| `business_hours_only` | bool | yes | `true` → 8h × 22d / month; `false` → 24h × 30d / month |
| `cosmos_gb_year_one` | float ≥ 0 | yes | end-of-year-one Cosmos storage estimate |
| `storage_gb_year_one` | float ≥ 0 | yes | end-of-year-one blob/file storage estimate |
| `ai_search_documents` | int ≥ 0 | yes | indexed doc count at steady state |
| `monthly_growth_rate` | float ≥ 0 | yes | applied to the projection for the v2 12-month outlook |
| `declared_constraints.max_p95_latency_ms` | int ≥ 0 | yes | used by the recommender to drop too-cold alternatives |
| `declared_constraints.min_redundancy` | enum | yes | used by the recommender to drop LRS alternatives if customer needs ZRS+ |
| `declared_constraints.pinned_region` | string | optional | blocks cross-region recommendations if set |

## Validation

The skill **fails fast** (CLI exit 4) if any required field is blank
after the wizard runs in `--non-interactive` mode. We never produce a
projection on guessed numbers — the manifest's `load_profile_ref`
points to a section that must be real.

## Worked default-suggestion table (used by the wizard)

| workload_class | suggested peak_concurrent_sessions | suggested avg_requests_per_session | suggested avg_tokens_per_request |
|---|---|---|---|
| chat-agent | 50 | 8 | 1500 |
| batch | 1 | 10000 | 2000 |
| scheduled | 5 | 50 | 1200 |
| hybrid | 25 | 100 | 1500 |

These are starting points; the wizard always lets the user override.

## Forward compatibility

v2 will add:

  - `reservations: { aoai_ptu_units: 25, ... }` — for Savings Plans
  - `discounts: { ea_multiplier: 0.87 }` — for EA / MCA
  - `forecast: { months_out: 12, growth_curve: linear|exponential }`

v1 ignores any unknown keys but logs a `warning` so future adds are
discoverable.
