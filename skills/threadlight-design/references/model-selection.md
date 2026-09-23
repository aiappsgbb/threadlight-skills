# Model selection — decision procedure (`threadlight-design`)

A thin **decision aid** for choosing the runtime model, capacity, and region a pilot
locks into `specs/foundation.md` § 2 and carries into SPEC § 7b. It answers *how
to decide*, not *what exists*:

- **The per-use-case model table** lives in the SPEC template — see
  `references/speckit-template.md` § 7b. This file does not duplicate it.
- **The authoritative, always-current model matrix** (families, dated versions,
  regional availability, TPM ceilings) lives in the foundry skill catalog —
  [`foundry-skill-catalog`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-skill-catalog/).
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
| 1 | Model tier | `model.default` | `gpt-5.4` |
| 2 | Version pin | `model.version` | current dated build |
| 3 | Reasoning effort | `model.reasoning_effort` | `medium` |
| 4 | Capacity type | `model.capacity_type` | `GlobalStandard` |
| 5 | Capacity (TPM) | `model.capacity_tpm` | `50K` (`gpt-5.4`) |
| 6 | Region + fallback | `model.region` / `model.fallback_region` | availability-driven |
| 7 | Data boundary | `model.data_boundary` | `none` |

---

## 1. Model tier — start at the default, move off it only on evidence

**Runtime starting default: `gpt-5.4`.** Keep the existing house starting point,
not a guarantee of tool-call discipline or a rule for the authoring host.
Confirm capability on the actual artifact; deviate deliberately on evidence.

- **Evaluate a lower-cost candidate** (for example `gpt-5.4-mini`) when the
  required capabilities permit. Short instruction/tool chains can make a useful
  first candidate, but tool count alone neither proves nor rules out suitability.
  Long chains need explicit tool/skill-use evidence under the checklist above;
  a smaller tier is not automatically excluded from the main business-agent loop.
- **Upgrade to `gpt-5.4-pro`** when **vision feeds multi-step reasoning** (read a
  document or image, then reason across several steps on what it saw).
- **`gpt-5.4-nano`** — bulk, cheap, shallow vision (return triage, photo
  screening). Not for reasoning.
- **`gpt-5.3-codex`** — diagram-→-code and code-related multimodal work.

> **Tier is provisional at Step 0.** Record the current choice; the **Step 2
> trait matrix** may nominate a lower-cost candidate after discovery. Confirm
> the provisional choice at the Step 4 checkpoint; promoting a replacement for
> a validated runtime still requires the fixed-artifact comparison above.

---

## 2. Version pin — pin an explicit dated build

Pin `model.version` to a specific dated build (e.g. `2026-03-05`), never a
floating alias. A pinned version keeps smoke tests reproducible and deployments
deterministic.

> **`gpt-4o` / `gpt-4o Vision` are legacy** (as of May 2026) — the `gpt-5.4`
> family supersedes them in context, latency, vision quality, and cost. A
> `gpt-4o` reference carried forward from an older template is a bug: sweep it to
> `gpt-5.4`, or the right row from § 7b.

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

**TPM.** Size `capacity_tpm` to the load profile: `50K` `GlobalStandard` is the
starting point for `gpt-5.4`; `-mini` supports `120K`; high-volume intake needs
`300K+`. The `threadlight-consumption-iq` wizard turns the SPEC § 12
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
Selection)** — the input contract for `foundry-doc-vision-speech` and the
`azure.yaml` `config.deployments` block. Decide once, here; everything downstream
carries the values instead of re-deciding them. A later approved A/B outcome
revises that decision through the same authority chain, not an environment-only
override.
