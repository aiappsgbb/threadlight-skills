---
name: threadlight-consumption-iq
description: >-
  Use after threadlight-safe-check post-deploy and before
  threadlight-production-ready to project per-resource monthly Azure cost at
  the customer's declared load and compare 2–3 SKUs per resource so the
  seller/SE picks the cheapest config that still meets constraints. Reads
  deployed Bicep + SPEC § 12 load_profile, hits the Azure Retail Prices API,
  emits cost-projection.md + cost-manifest.json. Advisory only. Also a
  PRE-SALES phased estimate with no deployed pilot (adoption phases, hardening
  delta, EA/MCA discount → seller one-pager). USE FOR: azure consumption
  projection, cost projection, post-deploy cost, SKU diff, PAYG vs PTU, AI
  Search sizing, load profile, cost-manifest.json, retail prices API,
  pre-sales cost estimate, EA/MCA discount, seller one-pager. DO NOT USE FOR:
  AOAI-only PAYG-vs-PTU break-even with no pilot (use
  paygo-ptu-cost-analyzer); live Cost Management actual-cost queries (stay in
  threadlight-production-ready); Bicep mutation (use threadlight-deploy).
metadata:
  version: "0.3.0"
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
| `specs/manifest.json → deployment_manifest{}` | `threadlight-design` | yes — **or** derived from the export's `infra/` + `azure.yaml` in Kratos-export mode |
| `infra/main.bicep` + modules | repo | yes |
| `azd env get-values` | live azd env | yes (skip with `--pre-deploy` to read Bicep only) |
| `specs/SPEC.md § 11c` (tech-stack selectors) | `threadlight-design` | yes — not present in a Kratos export; resources come from `infra/` instead |
| `specs/SPEC.md § 12 → load_profile{}` (NEW sub-block) | this skill's wizard OR hand-authored | yes (skill writes it back if absent; in Kratos-export mode it writes to `use-cases/<x>/load-profile.yml` since there's no SPEC) |
| Recent Application Insights / `foundry-observability` traces | live monitor | optional (fidelity boost post-launch) |

### Kratos-export mode (discover from `infra/`, not from a SPEC)

For a **Kratos-exported project** (`src/hosted-agent/` + `use-cases/<x>/`,
trimmed `infra/` — see [`docs/KRATOS-BRIDGE.md`](../../docs/KRATOS-BRIDGE.md))
there is no `specs/SPEC.md` and no `specs/manifest.json`. Adapt the `discover`
phase:

- **Walk the export's `infra/` (`az bicep build` → ARM JSON) + `azd env
  get-values`** to enumerate deployed resources, instead of reading a
  `deployment_manifest{}`. This is the same ARM-walker path the skill already
  uses — just sourced from the export's own Bicep.
- **Tolerate Kratos resource naming.** The trimmed Kratos infra names Cosmos /
  Foundry / ACA resources differently than a `threadlight-design` deployment.
  Match resources by **ARM type** (e.g. `Microsoft.DocumentDB/databaseAccounts`,
  `Microsoft.App/containerApps`, `Microsoft.CognitiveServices/accounts`) and by
  `azd env` output keys, **not** by hard-coded `threadlight-design` names. The
  trimmed infra has **no APIM** — simply omit the APIM projector rather than
  reporting it missing.
- **`load_profile{}` has no SPEC to write back to.** Run the wizard as usual,
  but persist the result to `use-cases/<x>/load-profile.yml` (and still emit
  `specs/cost-manifest.json` + `docs/cost-projection.md`). Idempotent on re-run.

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

## Pre-sales phased estimate mode

The default mode above answers *"what does this deployed pilot cost?"*. The
**pre-sales mode** answers the question that comes **before** a pilot exists:
*"what will this cost as the customer adopts it — POC, then expansion, then
business-wide?"* — without a deployed repo, an `azd env`, or a SPEC § 12 block.

```
# from a rollout profile (.json or .yaml), no deploy required
scripts/consumption_iq.py estimate --rollout rollout.json \
  --onepager docs/estimate-onepager.html
# or fold into the run wrapper (writes the post-deploy manifest path the
# production-ready COST gates read — see "outputs" below)
scripts/consumption_iq.py run --all --pre-sales --rollout rollout.json
```

A **rollout profile** declares N adoption phases; each phase carries its own
`load_profile{}` (same schema as SPEC § 12) and a hardening `posture`
(`demo` | `production` | `production-hardened`). The profile is **repo-optional**:
declare the resource topology directly on it — a top-level `resources[]` and/or a
per-phase `resources[]` override — and the estimate runs with no Bicep / `azd`
walk at all. A per-phase override is what expresses the real land-and-expand SKU
step (e.g. AI Search **Basic** in the POC → **S2/S3** once it's business-wide).
Only when a rollout declares **no** topology does the CLI fall back to repo
discovery. Per phase the orchestrator:

1. projects **every** resource at that phase's load (reusing the per-resource
   projectors — the Log Analytics workspace included, so GenAI OTel ingestion
   is sized explicitly, not forgotten);
2. appends the **production-hardening / estate delta** for that posture — the
   SKUs that appear when a workload leaves "pilot" and enters regulated
   production (Front Door + WAF, Private Endpoints, Defender, Sentinel, DDoS,
   multi-region DR, the non-prod estate copy), each tagged
   `shared_platform_billed` so estate-amortised items are honest;
3. scores SKU recommendations on the **current** phase only.

Optional `--discount 0.85 --discount-basis ea` applies a flat EA/MCA multiplier
(only `ea`/`mca` may carry a sub-1.0 multiplier; `retail` + a real discount, or
any out-of-range multiplier, fail fast with exit 4). The retail number is always
preserved alongside the discounted one. Outputs:
`docs/cost-estimate.md`, `specs/cost-estimate-manifest.json` (schema 1.1,
`pre_sales: true`, top-level `totals.*` mirror the current phase so the
`production-ready` COST gates still read a number), and the seller one-pager.
The standalone `estimate` subcommand writes those dedicated `cost-estimate*`
paths; `run --all --pre-sales` instead writes the phased manifest to the
**post-deploy** paths (`docs/cost-projection.md` / `specs/cost-manifest.json`) on
purpose, so the `production-ready` COST-005/006 gates consume one manifest
regardless of which mode produced it.

### Estimate-framing discipline (non-negotiable)

**A pre-sales number is dangerous because it looks authoritative.** Every figure
this mode emits is a planning **ESTIMATE at public list prices for one generic
pilot — not a quote.** Violating the letter of this rule violates its spirit.

| Rationalization | Reality |
|---|---|
| "The customer just wants one number." | One bare number is the failure mode. Show the phased ramp + the word *estimate*. |
| "It's basically a quote, I'll call it that." | It is **not** a quote. Real pricing depends on the customer's agreement, region, and commitment. Say "estimate". |
| "Discounted figure = their EA price." | The multiplier is **your assumption**, not a contractual rate. Keep retail visible; caveat the discount. |
| "Hardening is overkill for the demo." | Demo posture *has* no hardening. The delta only appears at `production`/`production-hardened` — and it's a deliberate, labelled choice. |
| "Logs are cheap, skip them." | GenAI OTel ingests on every span. The observability line is frequently top-3 — size it. |

**Red flags — STOP if you catch yourself:**

- About to present a single $/mo figure with no "estimate" framing.
- About to call any figure a "quote" or "price".
- About to share an `audience: internal` one-pager (the "do not share" / seller
  talk-track variant) with the customer.
- About to present a discounted number without the EA-assumption caveat.

### Classification discipline (internal vs customer)

The one-pager renders for one of two audiences:

| Audience | Classification strip | Seller talk-track ("how to open") | Use |
|---|---|---|---|
| `internal` (default) | **"Microsoft internal · do not share with the customer"** | included | sales enablement — peers start the conversation |
| `customer` | omitted | omitted | a customer-safe leave-behind |

`--audience` overrides; otherwise the one-pager inherits the **current phase's**
audience. Never hand the internal variant to a customer.

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
- `references/rollout-profile-schema.md` — pre-sales phased `rollout_profile{}` schema
- `references/cost-estimate-manifest-schema.md` — phased estimate manifest (schema 1.1)
- `references/hardening-delta-catalog.json` — per-posture production-hardening / estate delta
- `references/onepager-template.html` — seller one-pager template (HTML, best-effort PDF)
- `references/consumption-formulas.md` — per-resource math + citations
- `references/pricing-fixtures/*.json` — fallback price snapshots
- `references/fixtures/sample-pilot-consumption/` — end-to-end fixture
  with filled SPEC + manifest + expected golden artefacts (used by the
  emitter golden-file test and CI e2e)
- `references/fixtures/sample-presales-rollout/` — end-to-end pre-sales
  fixture (3-phase rollout + expected golden estimate + one-pager)
