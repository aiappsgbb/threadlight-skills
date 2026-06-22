# Consumption formulas

> Per-resource math used by `scripts/projectors/<resource>.py`.
> Each section gives the formula, the load_profile fields consumed, the
> alternatives compared, and the citations for the underlying pricing
> dimensions.

> **Challenge me.** Every formula here is a defensible approximation,
> not a promise. Pricing models change. Where Azure changes a pricing
> dimension we update the formula and bump the projector's
> `formula_version` (recorded in the cost-manifest's `extra`).

## Time constants

```
hours_per_day        = 8     if load_profile.business_hours_only else 24
days_per_month       = 22    if load_profile.business_hours_only else 30
seconds_per_month    = hours_per_day * 3600 * days_per_month
```

`730` is the conventional "hours per month" for always-on resources
(ACA Dedicated, AI Search replicas, APIM Premium units, Cosmos
provisioned RU). It is not multiplied by `hours_per_day` /
`days_per_month` because those resources are billed continuously even
when idle.

---

## § AOAI — model deployments

### Formula

```
monthly_requests        = peak_requests_per_second * seconds_per_month
input_share, output_share = 0.65, 0.35       # default; override via extra.io_split

monthly_input_tokens    = monthly_requests * avg_tokens_per_request * input_share
monthly_output_tokens   = monthly_requests * avg_tokens_per_request * output_share

PAYG_cost  = (monthly_input_tokens  / 1000) * input_price_per_1k_usd
           + (monthly_output_tokens / 1000) * output_price_per_1k_usd

PTU_cost   = ptu_units * ptu_price_per_unit_per_month_usd
```

### Alternatives compared

| Variant | Knob |
|---|---|
| PAYG (current model) | baseline |
| PTU @ 1, 4, 10, 25, 50, 100 units | `ptu_units` |
| Same model, alternative region | `region ∈ {eastus2, swedencentral, northcentralus}` |
| Model swap | `gpt-4o ↔ gpt-4o-mini` |

### Constraint hooks

  * `pinned_region` — drop region swaps that violate
  * `max_p95_latency_ms` — drop model swaps that downgrade if measured
    p95 of current model is already above the threshold (we don't make
    it worse by recommending a smaller model)

### Citations

  * [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/)
  * PTU break-even heuristic: `paygo-ptu-cost-analyzer` in awesome-gbb

---

## § Foundry hosted-agent

### Formula

```
monthly_cost = hosted_agent_tier_price_per_month_usd
             + agent_message_count * per_message_price_usd
agent_message_count = peak_concurrent_sessions * avg_requests_per_session * (days_per_month / 30) * 30
```

### Alternatives compared

  * Adjacent tiers up/down from current_sku.tier (e.g. Free → Standard,
    Standard → Premium).

### Citations

  * [Microsoft Foundry hosted-agent pricing page](https://learn.microsoft.com/en-us/azure/ai-foundry/) — meter dimension via Azure-pricing MCP

---

## § ACA — Container Apps

### Consumption tier

```
vcpu_seconds_per_replica   = vcpu * seconds_per_month
mem_gib_seconds_per_replica = memory_gib * seconds_per_month

avg_replicas = max(min_replicas,
                   ceil(peak_requests_per_second / requests_per_second_per_replica))
total_vcpu_seconds = vcpu_seconds_per_replica   * avg_replicas
total_mem_seconds  = mem_gib_seconds_per_replica * avg_replicas

free_vcpu_seconds  = 180_000   # Azure free grant per month
free_mem_seconds   = 360_000
free_requests      = 2_000_000

billable_vcpu_seconds = max(0, total_vcpu_seconds - free_vcpu_seconds)
billable_mem_seconds  = max(0, total_mem_seconds  - free_mem_seconds)
billable_requests     = max(0, monthly_requests   - free_requests)

monthly_cost = billable_vcpu_seconds * vcpu_price_per_second_usd
             + billable_mem_seconds  * mem_price_per_gib_second_usd
             + billable_requests     * request_price_per_million_usd / 1_000_000
```

### Dedicated tier

```
monthly_cost = workload_profile_price_per_hour * 730
             + per-replica overage if replicas > workload_profile_replicas
```

### Alternatives compared

  * Consumption ↔ Dedicated D4 / D8 / E4 / E8
  * Replica min/max sweep: (1-3), (1-10), (2-20), (3-30)

### Citations

  * [Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
  * Workload profiles: [ACA workload profiles overview](https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview)

---

## § Cosmos DB (NoSQL)

### Provisioned throughput

```
monthly_cost = provisioned_ru * ru_price_per_hour_usd * 730
             + cosmos_gb_year_one * storage_price_per_gb_month_usd
```

### Serverless

```
ru_per_op_default = 5    # typical 1-KB read; override via extra.ru_per_op
monthly_ru_consumed = monthly_requests * ru_per_op_default
monthly_cost = monthly_ru_consumed * serverless_ru_price_per_million_usd / 1_000_000
             + cosmos_gb_year_one * storage_price_per_gb_month_usd
```

### Autoscale

```
utilization_factor = 0.6     # Microsoft's conservative default
monthly_cost = max_ru * autoscale_ru_price_per_hour_usd * 730 * utilization_factor
             + cosmos_gb_year_one * storage_price_per_gb_month_usd
```

### Alternatives compared

  * Provisioned @ 1k / 4k / 10k RU
  * Serverless
  * Autoscale @ 1k / 4k / 10k max RU

### Citations

  * [Azure Cosmos DB pricing](https://azure.microsoft.com/en-us/pricing/details/cosmos-db/)

---

## § Storage account

### Formula

```
stored_gb_avg     = storage_gb_year_one / 2     # linear-fill assumption; v2 will allow override
monthly_cost      = stored_gb_avg * tier_price_per_gb_month_usd
                  + monthly_transactions * txn_price_per_10k_usd / 10_000
                  + monthly_egress_gb * egress_price_per_gb_usd
```

Where `tier_price_per_gb_month_usd` is keyed on `(redundancy, access_tier)`.

### Alternatives compared

  * Redundancy × access tier matrix:
    `{LRS, ZRS, GRS} × {hot, cool, cold, archive}` with archive only
    suggested if `workload_class == batch`.

### Constraint hooks

  * `min_redundancy` — drop LRS alternatives if `min_redundancy >= zone-redundant`

### Citations

  * [Azure Blob Storage pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)

---

## § APIM

### Consumption tier

```
free_grant_calls = 1_000_000      # per Azure subscription
monthly_cost = max(0, monthly_requests - free_grant_calls) * consumption_price_per_10k_calls / 10_000
```

### Basic v2 / Standard v2 / Premium

```
monthly_cost = tier_units * tier_price_per_unit_per_hour_usd * 730
```

### Alternatives compared

  * Consumption ↔ Basic v2 ↔ Standard v2 ↔ Premium
  * Premium @ 1, 2, 4 unit configurations

### Citations

  * [API Management pricing](https://azure.microsoft.com/en-us/pricing/details/api-management/)

---

## § Azure AI Search

### Formula

```
monthly_cost = sku_unit_price_per_hour_usd * replicas * partitions * 730
             + image_extraction_ops * extraction_price_per_1k_usd / 1_000
             + semantic_ranker_ops  * semantic_price_per_1k_usd   / 1_000
```

### Alternatives compared

  * Free / Basic / S1 / S2 / S3 (subject to per-tier doc-count cap vs
    `ai_search_documents`)
  * Replica × partition sweep within the tier: (1×1), (2×1), (2×2), (3×3)

### Constraint hooks

  * `ai_search_documents` exceeds tier cap → mark alternative as
    `satisfies_constraints: false`

### Citations

  * [Azure AI Search pricing](https://azure.microsoft.com/en-us/pricing/details/search/)

---

## v2 backlog

  * Reservations & Savings Plans modelling
  * Spot pricing for ACA
  * Forecast / 12-month outlook with `monthly_growth_rate`
  * Sensitivity analysis: ±20% load_profile knobs → cost band

---

## Observability ingestion (pre-sales + post-deploy)

The `Microsoft.OperationalInsights/workspaces` projector sizes the Log
Analytics / Application Insights **ingestion** bill — the line telcos and
regulated customers routinely under-estimate because GenAI OTel emits a span
per request.

  * monthly GB ≈ `monthly_requests × bytes_per_trace × content_recording_band`
  * `content_recording_band`: prompts+completions recorded ⇒ larger payloads;
    metadata-only ⇒ smaller. No sampling assumed (100% telemetry) unless the
    load profile says otherwise.
  * £/GB at the pay-as-you-go analytics-logs rate; commitment tiers are a v2
    knob.

### Citations

  * [Azure Monitor pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/)

---

## Production-hardening / estate delta (pre-sales)

A **catalog-driven delta**, not a SKU swap — the resources that appear when a
workload leaves "pilot" and enters regulated production. Source:
`references/hardening-delta-catalog.json`, keyed by posture
(`demo` | `production` | `production-hardened`, cumulative).

  * Each line: `component`, `category`, `monthly_cost_usd`,
    `shared_platform_billed`, `price_source`, `rationale`.
  * `shared_platform_billed: true` (Defender, Sentinel, DDoS) ⇒ amortised
    across the estate, not charged wholly to this workload.
  * Figures are neutral public-list **ESTIMATES** for a generic pilot.

### Citations

  * [Azure Front Door pricing](https://azure.microsoft.com/en-us/pricing/details/frontdoor/)
  * [Microsoft Defender for Cloud pricing](https://azure.microsoft.com/en-us/pricing/details/defender-for-cloud/)
  * [Microsoft Sentinel pricing](https://azure.microsoft.com/en-us/pricing/details/microsoft-sentinel/)
