---
name: threadlight-consumption-iq
description: >
  Use after threadlight-safe-check --phase post-deploy returns green, before
  threadlight-production-ready, to project per-resource monthly Azure cost at
  the customer's declared production load and compare against 2–3 alternative
  SKUs per resource so the seller / SE can pick the cheapest config that still
  meets declared constraints before the customer signs off and turns on
  production traffic. Walks the deployed Bicep + `azd env`, reads SPEC § 12
  `load_profile{}` (interactive wizard fills it on first run + writes back to
  SPEC), hits the Azure Retail Prices API via the `Azure-pricing` MCP with a
  versioned fixture fallback, and emits `docs/cost-projection.md` (human-readable
  scorecard with side-by-side SKU comparisons + mermaid cost share donut +
  top-N recommendations) plus `specs/cost-manifest.json` (strict v1 schema
  consumed by `threadlight-production-ready`'s cost pillar). Covers AOAI model
  deployments (PAYG vs PTU vs regional vs model swap), Foundry hosted-agent
  tiers, ACA SKUs (Consumption vs Dedicated D-series / E-series, replica
  scaling), Cosmos DB (provisioned vs serverless vs autoscale), Storage
  redundancy + access tier, APIM (Consumption vs Basic v2 vs Standard v2 vs
  Premium), and Azure AI Search (Free / Basic / S1 / S2 / S3 + replica /
  partition combos). Soft advisory — never mutates Bicep; recommendations[] is
  read by `threadlight-production-ready`'s new `COST-006` check which flags
  unaddressed savings >$100/mo as `must-fix`. Every emitted $ value is tagged
  `price_source: live | fixture | fallback` so reviewers can see what's stale.
  USE FOR: azure consumption projection, cost projection, post-deploy cost,
  SKU diff, SKU comparison, PAYG vs PTU, ACA SKU comparison, Cosmos serverless
  vs provisioned, Storage redundancy comparison, APIM tier comparison, AI
  Search tier sizing, Foundry hosted-agent tier sizing, load profile wizard,
  SPEC § 12 load_profile, cost-projection.md, cost-manifest.json, drive
  consumption outcome, customer FinOps conversation, pre-production cost
  scorecard, monthly cost recommendation, retail prices API, Azure-pricing
  MCP, paygo-ptu beyond LLMs, generalize cost analysis across resources,
  monthly savings recommendation, cost rationale, cost caveat.
  DO NOT USE FOR: AOAI-only PAYG-vs-PTU break-even (use `paygo-ptu-cost-analyzer`
  in awesome-gbb directly if you don't have a deployed pilot); the static cost
  pillar checks (Budget declared? anomaly alert wired? projection artefact
  present?) — those stay in `threadlight-production-ready` pillar 10; live
  actual-cost queries against Cost Management (those are `threadlight-production-
  ready` COST-101..103); Bicep mutation — this skill is advisory only and
  `threadlight-deploy` is what actually changes infra on the next run; demand
  forecasting / time-series of usage (out of scope v1); reservations /
  Savings Plans modelling (out of scope v1, deferred to v2); EA / MCA discount
  modelling (out of scope v1, user-provided multiplier in v2).
metadata:
  version: "0.1.0"
---

# Threadlight Consumption IQ — post-deploy cost projection + SKU diff

> The single skill in the chain that asks "**what should this pilot actually
> cost to run at the customer's real production load, and which SKUs should
> we swap to before they sign off?**" and answers with a structured,
> evidence-backed artefact instead of a guess.
>
> Naming: the `-iq` suffix matches the target Field Outcomes
> view's `ai-foundry-account-iq` / `azure-account-iq` family under
> **Build & Deliver → Drive Consumption**.

## Why this skill exists

The `threadlight-*` chain ships a working agent in one session (design →
local-test → deploy → safe-check). `threadlight-production-ready`'s
pillar 10 (cost) then asks *static* questions: is a Budget declared? is
an anomaly alert wired? is `docs/cost-projection.md` present? Those are
yes/no checks. They do not answer **what the pilot will actually cost**
when the customer turns on production traffic.

Meanwhile `paygo-ptu-cost-analyzer` (in `awesome-gbb`) only covers AOAI
PAYG-vs-PTU break-even. Every other deployed resource — ACA, Cosmos,
Storage, APIM, AI Search, Foundry hosted-agent — gets eyeballed.

Without this skill the cost conversation at architecture review goes:

> **Customer FinOps:** "What's this going to cost per month at our actual
> load?"
> **SE:** "Uh, roughly… a few thousand?"

That's how pilots become lab graveyards.

This skill produces the answer in one command.

## What this skill does NOT replace

| Concern | Use instead |
|---|---|
| AOAI-only PAYG-vs-PTU break-even on a notebook (no deployed pilot) | `paygo-ptu-cost-analyzer` (awesome-gbb) |
| Static cost-pillar checks (Budget declared? anomaly alert? projection present?) | `threadlight-production-ready` pillar 10 |
| Live actual-cost queries (last-7-days vs budget) | `threadlight-production-ready` `COST-101..103` |
| Bicep mutation from recommendations | `threadlight-deploy` on the next run (this skill is advisory) |
| Real-time anomaly detection | `threadlight-production-ready` `COST-102` |
| Demand forecasting / usage time-series | out of scope; foundry-observability owns the trace side |

## When to invoke

| You start with… | Phase | What's produced |
|---|---|---|
| Green `threadlight-safe-check --phase post-deploy` and an upcoming customer architecture review | `run --all` | `docs/cost-projection.md` + `specs/cost-manifest.json` (+ back-filled SPEC § 12 `load_profile{}` if wizard ran) |
| Pre-deploy spec review and you want to sanity-check the SKU choices in `infra/main.bicep` before you push | `run --all --pre-deploy` | same artefacts, marked `pre_deploy: true` in manifest (no `azd env` walk; Bicep-only) |
| Re-run with `load_profile{}` already populated and recent deploy | `run --all` (wizard auto-skips) | refreshed artefacts |
| You want to check just one resource (e.g. "did we pick the right APIM tier?") | `project --only Microsoft.ApiManagement/service` | partial artefacts, scoped manifest |

> **Rule of thumb.** Run this **after** `safe-check` and **before**
> `production-ready`. `threadlight-auto` will wire it in automatically.

## The chain (where this fits)

```
threadlight-design → threadlight-demo-data-factory → threadlight-local-test →
threadlight-deploy → threadlight-safe-check →
threadlight-consumption-iq      ← THIS SKILL
foundry-evals + foundry-observability →
threadlight-production-ready
```

## Inputs (contracts)

| Input | Source | Required |
|---|---|---|
| `specs/manifest.json → deployment_manifest{}` | `threadlight-design` | yes |
| `infra/main.bicep` + modules | repo | yes |
| `azd env get-values` | live azd env | yes (skip with `--pre-deploy` to read Bicep only) |
| `specs/SPEC.md § 11c` (tech-stack selectors) | `threadlight-design` | yes |
| `specs/SPEC.md § 12 → load_profile{}` (NEW sub-block) | this skill's wizard OR hand-authored | yes (skill writes it back if absent) |
| Recent Application Insights / `foundry-observability` traces | live monitor | optional (fidelity boost post-launch) |

### NEW: SPEC § 12 `load_profile{}` sub-block

Documented in `references/load-profile-schema.md`. Seven required
fields; the skill **fails fast** (no math, friendly error) if any are
blank after the wizard completes — we never produce a projection on
guessed numbers.

```yaml
load_profile:
  workload_class: chat-agent | batch | scheduled | hybrid
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

## Outputs (contracts)

| Output | Consumer | Format |
|---|---|---|
| `docs/cost-projection.md` | humans + `threadlight-production-ready` `COST-005` | markdown: per-resource sections, side-by-side SKU tables, top-N recommendations, mermaid cost share donut |
| `specs/cost-manifest.json` | `threadlight-production-ready` `COST-006` + `threadlight-auto` resumability + downstream CI | strict v1 schema (see `references/cost-manifest-schema.md`) |
| `specs/SPEC.md § 12 load_profile{}` | re-runs of this skill, future deploys, `threadlight-design` template | back-filled if wizard ran |

## Resource coverage matrix (v1)

| Azure resource | Compared variants |
|---|---|
| AOAI model deployments | PAYG ↔ PTU (1/4/10/25/50/100 units); region (eastus2 / sweden / etc.); model swap (gpt-4o ↔ gpt-4o-mini) |
| Foundry hosted-agent | tier ↑/↓ |
| Azure Container Apps | Consumption ↔ Dedicated D4/D8/E4/E8; min/max replicas |
| Cosmos DB (NoSQL) | provisioned (1k/4k/10k RU) ↔ serverless ↔ autoscale |
| Storage account | redundancy (LRS/ZRS/GRS); access tier (hot/cool/cold/archive) |
| APIM | Consumption ↔ Basic v2 ↔ Standard v2 ↔ Premium |
| Azure AI Search | Free / Basic / S1 / S2 / S3; replica × partition combos |

**Out of scope for v1:** Reservations / Savings Plans, EA / MCA discounts,
automatic Bicep mutation, multi-region failover cost, spot ACA pricing,
cross-cloud comparison.

## Phases

| Phase | Script | What it does | Resumable from |
|---|---|---|---|
| 1. discover | `discover.py` | Walk Bicep + `azd env` → list of normalized resource selectors | always |
| 2. load-profile | `load_profile_wizard.py` | Read SPEC § 12; if missing, run interactive prompt; write back to SPEC | always (idempotent) |
| 3. price | `pricing_client.py` | Hit `Azure-pricing` MCP for each SKU + 2–3 alternatives per resource | cache to `.threadlight/cost-cache.json` (TTL 24h) |
| 4. project | `projectors/<resource>.py` | Apply per-resource consumption formulas to load_profile | always |
| 5. compare | `projectors/<resource>.py` | Build per-resource alternative comparisons | always |
| 6. recommend | `recommender.py` | Score alternatives vs `declared_constraints`; rank by $/mo savings | always |
| 7. emit | `emitter.py` | Write `docs/cost-projection.md` + `specs/cost-manifest.json` | always |

## CLI surface

```bash
# Individual phases
scripts/consumption_iq.py discover
scripts/consumption_iq.py load-profile [--non-interactive]
scripts/consumption_iq.py price
scripts/consumption_iq.py project [--only <resource_kind>]
scripts/consumption_iq.py recommend
scripts/consumption_iq.py emit

# Chained
scripts/consumption_iq.py run --all
scripts/consumption_iq.py run --all --pre-deploy   # skip azd env walk; Bicep-only
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Artefacts produced |
| 2 | Missing prerequisite (no SPEC § 12, stale `safe-check`, etc.) |
| 3 | I/O failure or `Azure-pricing` MCP unavailable AND no fixture fallback for at least one required SKU |
| 4 | `load_profile{}` incomplete after wizard (interactive mode required) |

Single-file design where possible; stdlib only; `Azure-pricing` MCP via
subprocess + JSON. Mirrors the dependency posture of
`threadlight-safe-check` and `threadlight-production-ready`.

## Downstream wiring

### `threadlight-production-ready` cost pillar updates

| Check | Update |
|---|---|
| `COST-005` (projection artefact present) | now passes only if **both** `docs/cost-projection.md` and `specs/cost-manifest.json` exist **and** `generated_at` is within 30 days of the latest deploy |
| **NEW `COST-006`** (recommendations addressed) | reads `recommendations[]`; flags `must-fix` if any rec with `monthly_savings_usd > $100/mo` is unaddressed (i.e., `current_sku` still matches deployed selectors); flags `should-fix` for recs ≥ $25/mo |
| `COST-103` (last-7-day actual vs budget) | numerator unchanged; denominator now reads `cost-manifest.json → totals.monthly_cost_current_usd` instead of prompting the user |

### `threadlight-auto` orchestrator updates

- Insert phase between `safe-check` (post-deploy gate) and
  `production-ready`. Smart-recovery: skip phase 2 (wizard) on a re-run
  if SPEC § 12 `load_profile{}` is filled **and** `cost-manifest.json`
  is newer than the last deploy.

### `threadlight-design` updates

- Generated `specs/SPEC.md § 12` now emits an empty `load_profile:`
  skeleton with comments pointing at this skill — so the wizard always
  has something to fill in rather than a missing section.

## Pricing source

- **Primary:** `Azure-pricing` MCP tool (live Azure Retail Prices API).
- **Fallback:** `references/pricing-fixtures/<resource>.json` (versioned
  snapshots, refreshed quarterly by a CI job).
- Every emitted `monthly_cost_usd` is tagged with
  `price_source: live | fixture | fallback` so reviewers can see what's
  stale.

## Reference index

- `references/load-profile-schema.md` — SPEC § 12 `load_profile{}` schema
- `references/cost-manifest-schema.md` — `specs/cost-manifest.json` v1 schema
- `references/consumption-formulas.md` — per-resource math + citations
- `references/pricing-fixtures/*.json` — fallback price snapshots
- `references/fixtures/sample-pilot-consumption/` — end-to-end fixture
  with filled SPEC + manifest + expected golden artefacts (used by the
  emitter golden-file test and CI e2e)
