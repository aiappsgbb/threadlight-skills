# Model selection — decision procedure (`threadlight-design`)

A thin **decision aid** for choosing the runtime model, capacity, and region a pilot
locks into `specs/foundation.md` § 2 and carries into SPEC § 7b. It answers *how
to decide*, not *what exists*:

- **The per-use-case model table** lives in the SPEC template — see
  `references/speckit-template.md` § 7b. This file does not duplicate it.
- **The authoritative, always-current model matrix** (families, dated versions,
  regional availability, TPM ceilings) lives in the foundry skill catalog —
  [`microsoft-foundry`](https://github.com/microsoft/azure-skills/tree/v1.2.113/.github/plugins/azure-skills/skills/microsoft-foundry).
  Model availability moves faster than this skill ships; when a version or region
  named here looks stale, the catalog wins.

First separate the two model roles below; the seven runtime decisions then fill
those existing fields. This is guidance, not a model leaderboard or routing engine.

## Authoring and runtime are independent choices

| Role | Work | Selection owner |
|---|---|---|
| **Authoring model** (construction agent) | Discovery, specification, architecture, code generation and review | Operator in the coding-assistant host |
| **Runtime inference model(s)** (business agent) | Execute the already-built agent's business tasks, tools and skills | Reviewed pilot configuration and deployment |

For nontrivial design/build work, recommend a **sufficiently capable authoring
model**, considering complexity and explicit operator choice. Better construction
can justify development effort before recurring execution; it does not require
the most expensive model for every trivial task. A capable authoring model does
not imply deploying the same model at runtime. Selecting a lower-cost runtime
also does not require using that cheaper model for authoring.

A skill **cannot silently switch the outer Copilot CLI, Cowork or Claude host
model**. Use operator-supported model selection when available; otherwise state
the limitation and retain the operator's choice. Never write global CLI settings.
`MODEL_DEPLOYMENT_NAME` selects a configured runtime deployment, not the coding
model. The model/capacity fields in `specs/foundation.md` § 2 and SPEC § 7b remain
**runtime** fields, not builder provenance. Do not repurpose them.

Sample instruction:

> Use a capable authoring model for this nontrivial prototype. Once validated,
> propose a small lower-cost runtime comparison on the same prototype before
> promotion; keep the existing quality and control requirements.

## Optional lower-cost runtime comparison

After prototype validation, offer a **small optional A/B** against a lower-cost
runtime candidate when domain, capability and risk requirements permit. This
offer is **no automatic authorization for paid evaluation or deployment**.
Agree the experiment scope, budget and permitted resources first; do not invent
volumes, available deployments, prices, or latency/savings guarantees.

1. **Freeze one validated artifact; swap only its runtime model.** Reuse the
   source, prompts, skills/resources, registered tools and schemas, fixtures,
   held-out cases, validators and judge configuration. Do not regenerate the
   prototype for a runtime comparison. A **2x2 cross matrix** is useful only
   when comparing both builder artifacts as well; it is not required for every
   pilot. Fixing a code defect creates a new artifact: rerun both candidates
   against that revision rather than mixing results.
2. **Verify identity and compatibility before scoring.** Record actual runtime
   model identity and version from deployment metadata and responses where
   exposed, not just an alias. Check protocol, tool and reasoning compatibility,
   output budgets and SDK support. Preserve approved region, data-boundary and
   capacity constraints. If a client/API adaptation is necessary, obtain
   explicit approval, declare its diff and keep the compatibility condition
   matched across candidates with business logic unchanged. If that is not
   possible, label the comparison confounded or blocked. Never silently disable
   reasoning, patch installed SDKs or translate outputs to obtain green results.
3. **Predeclare the promotion bar.** Set business quality, actual tool/skill-use,
   structured-output, PII, safety, governance and latency constraints, including
   protected scenarios and tolerated regressions. Include boundaries, negative
   cases and paraphrases as well as happy paths. Verify skill bodies/resources
   were consumed and required tools executed; a catalog advertisement or a
   single happy path is not skill-execution evidence. Preserve all failures and
   guardrail violations, retries and per-case outcomes; do not select only
   successful reruns.
4. **Compare economics at the business-task level.** Keep construction costs
   (coding credits, iterations, review) separate from recurring runtime tokens,
   infrastructure and retries. Prefer measured **cost per correctly completed
   business task**: total runtime spend for all attempted business cases,
   including failed-case spend and retries, divided by correctly completed
   cases under the predeclared bar. With zero correct cases the ratio is
   undefined, not zero. Report pass counts and latency alongside cost; keep
   preflight/diagnostic spend separate and visible. Label list-price estimates
   versus actual bills and cost coverage; state cache and reasoning assumptions,
   measured usage categories and missing usage rather than treating it as free.
   Do not mix development credits into inference costs or infer savings from
   token price alone. SPEC § 14 and consumption-iq retain their existing
   forecast/actuals/reconciliation authority.
5. **Promote only with evidence and approval.** If the cheaper candidate misses
   any declared constraint, keep or escalate to the stronger runtime; never
   relax controls to make it pass. A stronger model may avoid triggering a bug;
   that does not fix the source. Repair and revalidate the artifact separately.
   A small successful A/B is not universal quality equivalence or production
   readiness. Use the existing `threadlight-evals` champion–challenger gate and
   approved configuration/release path; no hidden downgrade or fallback.

**Handoff, not a new stage.** Suggest this once at the validated local-test or
pilot handoff, not by adding a mandatory orchestrator stage. Existing
`runtime-policy.json` selectors, governance contracts and Foundation → SPEC
authority remain intact. Record a proposed runtime change for review; after
approval reconcile Foundation § 2, SPEC § 7b and deployment configuration before
promotion, renewing any affected validation/evidence. Do not mutate deployed
configuration merely to propose an experiment.

---

## The seven decisions (feed `foundation.md` § 2 → SPEC § 7b)

| # | Decision | Field in `foundation.md` § 2 | House default |
|---|----------|------------------------------|---------------|
| 1 | Model tier (fixed baseline) | `model.default` | `gpt-6-luna` |
| 1b | Routing | `model.routing` / `model.router` | `router-subset` {gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol}, Balanced |
| 2 | Version pin | `model.version` | current dated build |
| 3 | Reasoning effort | `model.reasoning_effort` | `medium` |
| 4 | Capacity type | `model.capacity_type` | `GlobalStandard` |
| 5 | Capacity (TPM) | `model.capacity_tpm` | `300K` per model deployment |
| 6 | Region + fallback | `model.region` / `model.fallback_region` | availability-driven |
| 7 | Data boundary | `model.data_boundary` | `none` |

---

## 1. Model tier and routing — router-ready, evidence-gated

**Runtime starting default: a model router subset with own deployments, and
`gpt-6-luna` as the fixed baseline.** The generated foundation deploys a
`model-router` (`2025-11-18`) in **Balanced** mode restricted to a curated subset of
at least two modern models — by default `gpt-6-luna`, `gpt-6.1-terra` and
`gpt-6.1-sol` — and an **own deployment of every subset model in the same
resource**. `model.default` names the cheapest adequate fixed model the router is
compared against before go-live. This replaces the earlier `gpt-5.4` default
($2.50 input / $15 output per 1M tokens, Global).

### Evidence

From [`threadlight-router-bench` findings](../../threadlight-router-bench/references/findings/2026-11-20-router-workload-post-ignite.md)
(`threadlight-router-bench/references/findings/2026-11-20-router-workload-post-ignite.md`):
internal test subscription, Sweden Central, Responses API, synthetic 40 easy + 20
hard tasks with deterministic graders, 2 repetitions. Costs are **estimates**
(tokens × Retail Prices API list price + routing fee), not bills; this is
evidence for a default, not for a specific customer workload.

| Arm (easy tier, all 100 % quality) | $ per 1k tasks |
|---|---|
| fixed `gpt-6-luna` | 0.054 |
| router subset + own deployments, Balanced | 0.082 |
| router full pool, Balanced | 0.147 |
| fixed `gpt-5.4-mini` | 0.385 |
| fixed `gpt-6.1-sol` | 0.916 |
| router full pool, Quality | 1.83 |

On the hard tier the subset router and the gpt-6 fixed models scored 40/40, the
full pool 38–39/40 and `gpt-5.4-mini` 37/40. Without own deployments,
router-managed capacity throttled at about 12–15 RPM with HTTP 429 and no
failover; with each subset model deployed in the same resource there were no
errors, because own deployments serve routed requests first
([Learn: model router quotas](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-quotas)).
The routing fee is $0.14 per 1M input tokens Global ($0.154 Data Zone), more than
`gpt-6-luna`'s own input price.

### Decision rule (before go-live)

1. Run `threadlight-router-bench` `workload` on the pilot's own task mix: the
   router arm against the fixed baseline (`model.default`) and, if useful, one
   stronger fixed model. Keep the predeclared quality bar from the procedure above.
2. **If 90 % or more of the router's calls land on one model, call that model
   directly** (`routing: fixed`, `default:` that model) and drop the routing fee.
   The subset router sent 100 % of the easy tier to `gpt-6-luna` in the evidence.
3. Keep the router only when traffic genuinely splits and the router meets the bar
   at an acceptable cost per correctly completed task.
4. Keep **Balanced** at deployment level. Use per-request `routing_config.mode:
   quality` (header `Foundry-Features: ModelRouterControls=V1Preview`) only for
   steps flagged as critical; the full pool in Quality cost about 34× the baseline.
5. **Never use Auto for EU-residency pilots**: Auto always processes data as
   Global Standard. For EU residency deploy the router as `DataZoneStandard`; a
   Data Zone router cannot route to Claude models
   ([Learn: model router models](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-models)).
6. Subsets do not gain new pool models automatically; review the subset when
   models retire or new ones arrive
   ([Learn: configure model router](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-configure)).

**Log what served each call.** Apps and agents log the response `model` (the
served model), token usage and the `x-ratelimit-key` header, and send an explicit
`session_id` for multi-turn conversations, so cost attribution and the decision
rule use real traffic.

**GitHub Copilot harness.** GitHub Copilot BYOK reaches model router over Chat
Completions, so the GHCP pilot uses the `completions` wire for the router and
`responses` for a fixed model; the generated infra outputs `AZURE_AI_MODEL_WIRE_API`
([Learn: model router with agents](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-agents)).

### Other tiers

- **`gpt-5.4-pro`** when **vision feeds multi-step reasoning** (read a document
  or image, then reason across several steps on what it saw).
- **`gpt-5.4-nano`** — bulk, cheap, shallow vision (return triage, photo
  screening). Not for reasoning.
- **`gpt-5.3-codex`** — diagram-→-code and code-related multimodal work.

> **Tier is provisional at Step 0.** Record the current choice; the **Step 2
> trait matrix** may nominate a different candidate after discovery. Confirm the
> provisional choice at the Step 4 checkpoint; promoting a replacement for a
> validated runtime still requires the fixed-artifact comparison above.

---

## 2. Version pin — pin an explicit dated build

Pin `model.version` to a specific dated build (e.g. `2026-03-05`), never a
floating alias. A pinned version keeps smoke tests reproducible and deployments
deterministic.

> **`gpt-4o` / `gpt-4o Vision` are legacy** (as of May 2026). A `gpt-4o`
> reference carried forward from an older template is a bug: sweep it to the
> router-ready default above, or the right row from § 7b.

---

## 3. Reasoning effort — match to the workload

`minimal | low | medium | high` (default `medium`).

- `minimal` / `low` — latency-sensitive, shallow turns; extraction, classification.
- `medium` — the default; most agent loops.
- `high` — deep multi-step reasoning where answer quality dominates latency and cost.

Higher effort trades latency and tokens for depth — raise it only where the
workload rewards it.

---

## 4–5. Capacity — type before TPM

**Type.** `GlobalStandard` is the default for demo sandboxes and customer pilots:
pay-per-token, no commitment, provisions in minutes. Choose
**`ProvisionedThroughput` (PTU)** only when the pilot carries a **sustained
throughput or latency SLO** (usually `deployment_target: production-bound`), and
record it as a deliberate cost commitment. Demo → `GlobalStandard`;
production-bound with an SLO → consider PTU.

**TPM.** Size `capacity_tpm` to the load profile, per model deployment: `300K`
`GlobalStandard` is the pilot starting point (the GHCP runtime returned 429 at
`50K` or less); high-volume intake needs more. The router deployment has its own
capacity in units of 1,000 TPM and 1 RPM from the subscription's model router quota. The `threadlight-consumption-iq` wizard turns the SPEC § 12
`load_profile{}` into a defensible TPM + SKU recommendation — use it rather than
guessing once the pilot has real volume.

---

## 6. Region + fallback — availability decides, boundary constrains

- **`region`** — pin to a region where the chosen model *and* capacity type is
  actually available. Availability shifts; confirm against the catalog, not memory.
- **`fallback_region`** — a second in-boundary region for capacity pressure or
  DR. Not the same as the data boundary — it is an operational escape hatch.

---

## 7. Data boundary — resolve this first when it applies

`data_boundary: none | eu`. If the pilot is **EU-resident**, set `eu` and let it
**constrain decision 6 (region/fallback must be in-boundary) and decision 4
(capacity type must be available in-boundary)**. A data-boundary requirement can
invalidate an otherwise-fine region choice, so settle it before pinning the region.

---

## Where the decision lands

The seven values populate **`specs/foundation.md` § 2 (Model & capacity)**, which
`threadlight-design` Step 3 pre-populates into **SPEC § 7b (AI Services & Model
Selection)** — the input contract for `azure-ai` and the
`azure.yaml` `config.deployments` block. Decide once, here; everything downstream
carries the values instead of re-deciding them. A later approved A/B outcome
revises that decision through the same authority chain, not an environment-only
override.
