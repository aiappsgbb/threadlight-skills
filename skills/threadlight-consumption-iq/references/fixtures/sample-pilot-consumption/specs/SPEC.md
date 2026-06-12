# SPEC.md (fixture: sample-pilot-consumption)

> End-to-end fixture for `threadlight-consumption-iq` golden-file tests
> and CI e2e. Mirrors the shape of
> `skills/threadlight-production-ready/references/fixtures/sample-pilot-citadel/`.

## § 11c — Tech stack selectors

| Selector | Value |
|---|---|
| `aoai-model-deployment` | `gpt-4o` |
| `foundry-hosted-agent` | `standard` |
| `aca-bot` | yes |
| `aca-job` | yes |
| `cosmos-db` | yes |
| `storage-account` | yes |
| `apim` | yes |
| `ai-search` | yes |

## § 12 — Production-readiness declarations

### Target posture

Citadel-spoke (per the sister `sample-pilot-citadel` fixture's posture).

### `load_profile{}` (consumed by `threadlight-consumption-iq`)

```yaml
load_profile:
  workload_class: chat-agent
  peak_concurrent_sessions: 50
  avg_requests_per_session: 8
  avg_tokens_per_request: 1500
  peak_requests_per_second: 12
  business_hours_only: true
  cosmos_gb_year_one: 50
  storage_gb_year_one: 100
  ai_search_documents: 50000
  monthly_growth_rate: 0.15
  declared_constraints:
    max_p95_latency_ms: 2500
    min_redundancy: zone-redundant
    pinned_region: eastus2
```

### RTO / RPO / SLA

  * RTO: 4 hours
  * RPO: 1 hour
  * SLA: 99.5%

### Incident owner

`fixture-incident-owner@example.com` (not a real address)
