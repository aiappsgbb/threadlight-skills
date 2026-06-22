# Cost estimate — phased pre-sales projection

> Generated `2026-06-22T12:00:00+00:00` for `Generic Pilot`.
> Currency: `USD`. Price basis: `ea`. Discounted figures apply a 15% EA multiplier (an internal assumption, not a quote). Anchored to benchmark `queries_per_day = 5,000`.
>
> **All figures are planning ESTIMATES at public list prices for a single generic pilot — not a quote.** They frame a conversation; they do not commit a number.

## Headline (current phase)

Current phase: **`expansion`**. Estimated monthly cost: **$753.70** (estimate). After discount: **$640.64** (estimate).

## Cost by adoption phase

| Phase | Posture | Resources (est.) | Hardening Δ (est.) | Phase total (est.) | EA total (est.) |
| --- | --- | --- | --- | --- | --- |
| Phase 1 - Proof of concept | `demo` | $79.73 | $0.00 | **$79.73** | $67.77 |
| Phase 2 - Expansion ⭐ | `production` | $587.70 | $166.00 | **$753.70** | $640.64 |
| Phase 3 - Business-wide | `production-hardened` | $5,204.43 | $5,390.00 | **$10,594.43** | $9,005.27 |

## Production-hardening & estate delta

_Additional SKUs that appear as the workload leaves pilot and enters regulated production. `Estate-billed` items are amortised once across the whole estate, not per app. All ESTIMATES._

### Phase 2 - Expansion — `production`

| Component | Category | Monthly (est.) | Estate-billed? | Rationale |
| --- | --- | --- | --- | --- |
| Private Endpoints (x6 core resources) | networking | $46.00 | no | Private Link for AOAI, Search, Cosmos, Storage, Key Vault, ACA env — ~$7.66/endpoint/mo plus data processing. |
| Zone-redundancy uplift (compute + data) | resilience | $120.00 | no | Zone-redundant gateways, ZRS storage, and min-replica floors to meet a production availability SLO. |

### Phase 3 - Business-wide — `production-hardened`

| Component | Category | Monthly (est.) | Estate-billed? | Rationale |
| --- | --- | --- | --- | --- |
| Private Endpoints (x6 core resources) | networking | $46.00 | no | Private Link for AOAI, Search, Cosmos, Storage, Key Vault, ACA env — ~$7.66/endpoint/mo plus data processing. |
| Zone-redundancy uplift (compute + data) | resilience | $120.00 | no | Zone-redundant gateways, ZRS storage, and min-replica floors to meet a production availability SLO. |
| Azure Front Door Premium + WAF | edge | $330.00 | no | Global edge, managed TLS, and a WAF policy in front of the public ingress; base fee plus routing/rules. |
| Microsoft Defender for Cloud (multi-plan) | security | $200.00 | yes | Defender plans for servers / containers / databases / Key Vault. Billed per-resource but governed and amortised at estate level. |
| Microsoft Sentinel (SIEM ingestion) | security | $500.00 | yes | Security analytics ingestion + retention. One estate SIEM serves many workloads — counted here, amortised in reality. |
| DDoS Network Protection | security | $2,944.00 | yes | Tenant/region-wide DDoS Network Protection plan (~$2,944/mo) covering up to 100 public IPs — shared across the whole estate, not this app alone. |
| Multi-region DR (warm standby) | resilience | $850.00 | no | Warm standby of core compute + data in a paired region for regulated RTO/RPO; roughly duplicates the always-on floor. |
| Non-production estate (dev/test copy) | estate | $400.00 | no | A dev/test environment sized at ~40% of production so changes are validated before they reach the regulated workload. |

_Of which $3,644.00/mo is **shared platform** billed once across the estate — the customer may already pay it, so treat it as an upper bound for this workload._

---

> **Estimates only.** Public list prices for one generic pilot, not a quote. Validate against the Azure Pricing Calculator and the customer's agreement before sharing externally. This skill does not provision or mutate any infrastructure.
