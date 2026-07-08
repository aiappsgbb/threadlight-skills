# Model selection — decision procedure (`threadlight-design`)

A thin **decision aid** for choosing the model, capacity, and region a pilot
locks into `specs/foundation.md` § 2 and carries into SPEC § 7b. It answers *how
to decide*, not *what exists*:

- **The per-use-case model table** lives in the SPEC template — see
  `references/speckit-template.md` § 7b. This file does not duplicate it.
- **The authoritative, always-current model matrix** (families, dated versions,
  regional availability, TPM ceilings) lives in the foundry skill catalog —
  [`foundry-skill-catalog`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-skill-catalog/).
  Model availability moves faster than this skill ships; when a version or region
  named here looks stale, the catalog wins.

This file is the **procedure that fills those in**. Seven decisions, in order.

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

**Default: `gpt-5.4`.** It holds tool-call discipline across the long chains a
pilot runs (7+ skills, 10+ tool calls per turn). Start here; deviate deliberately.

- **Downgrade to `gpt-5.4-mini`** only when the agent runs **≤ 2 tool calls per
  turn** *and* short instruction chains. `-mini` degrades on long chains — it
  skips evidence-gathering tools and emits hollow commit-tool outputs. Cheaper
  and higher TPM, so worth it for genuinely trivial flows, never for the main
  agent loop.
- **Upgrade to `gpt-5.4-pro`** when **vision feeds multi-step reasoning** (read a
  document or image, then reason across several steps on what it saw).
- **`gpt-5.4-nano`** — bulk, cheap, shallow vision (return triage, photo
  screening). Not for reasoning.
- **`gpt-5.3-codex`** — diagram-→-code and code-related multimodal work.

> **Tier is provisional at Step 0.** Record the current choice; the **Step 2
> trait matrix** may drop the tier after discovery (a flow that turns out to be
> genuinely 1–2 step → `-mini`). Confirm at the Step 4 checkpoint. Do not
> re-litigate inside the foundation record.

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
carries the values instead of re-deciding them.
