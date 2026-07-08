# Interactive Composer ("Blueprint") — Design Spec

- **Status:** Draft (awaiting user review)
- **Date:** 2026-07-03
- **Author:** brainstormed with Copilot CLI
- **Related work:** `threadlight-auto` orchestrator; the canonical pipeline arc (`threadlight-design → local-test → safe-check → deploy → evals`); `docs/index.html` + `docs/production.html` (the visual/voice bar this page must meet); `docs/industries.html` (sequenced follow-on that reuses this page's data asset)

## 1. Problem & motivation

The hardest moment in adopting the platform is the **blank page**. A practitioner
who has never built an agent knows *what* they want ("triage supplier invoices",
"handle utility outages") but not **what to ask Copilot, in what order, and which
skills apply to their shape of problem**. Today our pages explain the pipeline
beautifully but still leave the reader to translate their own scenario into a
first prompt by hand. That translation step is where momentum dies.

The `threadlight-skills` collection also has no **on-ramp that starts from the
reader's own domain**. We describe the machine; we don't yet hand someone a
running-start for *their* process.

This spec adds an **Interactive Composer** — a client-side page where a reader
either **picks a real scenario** (from a curated library of ~89 process templates
spanning 15 industries) or **describes their own** in a short form, and instantly
gets three copy-paste artifacts:

1. a **Copilot prompt** that drives the Threadlight pipeline for exactly their process,
2. the **skill sequence** that will fire (derived from the process's shape, not guessed), and
3. an **`azd up` quickstart** plus a preview of the **artifact tree** they'll get.

It turns "I don't know where to start" into "paste this into Copilot" in under a minute.

### Why this amplifies the platform (makes it trivial, does not replace it)

The Composer runs **entirely in the browser**. It calls no model, provisions
nothing, and is not a generator of its own. Its only output is **a prompt and a
command the reader runs in their own Copilot against their own Azure subscription**
— i.e. it is a *doorway to* Azure AI Foundry and the Threadlight skills, never a
substitute for either. Every path it emits terminates in first-party motions
(`azd up`, Foundry Hosted Agents, Foundry evaluations). It is the same "make the
safe/first-party path trivial" philosophy as the rest of the collection, applied
to the very first click.

### Non-goals (v1)

- **No backend / no server.** Pure static page on GitHub Pages.
- **No live generation, no model calls, no code execution.** The artifact tree is an
  illustrative preview of the *shape* of output, clearly labelled as such.
- **No auth, no rate limiting, no cost surface** (a direct consequence of "no backend").
- **No port of the source React app.** The upstream wizard is *reference* for the
  data model and flow only; the page is rebuilt in vanilla JS to match our static site.
- **No new deployable infrastructure.** This is a documentation-site page.

## 2. Where it lives + navigation

- New page: `docs/blueprint.html` (working title **"Blueprint"** — you describe a
  process, you get the blueprint to build it). Alternatives considered: "Composer",
  "Start". Final name is a cosmetic decision recorded here as **Blueprint**.
- Added to the shared top-nav on **all 8 existing pages** (`index`, `production`,
  `case-study`, `customize`, `funnel`, `industries`, `self-improving`, `workbook`)
  so the on-ramp is reachable from anywhere.

## 3. Data pipeline (the shared asset)

The scenario library originates as a single JSON document in an internal
full-stack app (`process_library.json`, 89 entries, 15 industries). A small,
**offline, pure-stdlib** build step imports and sanitises it into a committed
static asset:

```
scripts/build_process_library.py  (repo-local, run manually + in CI)
  reads   <source process_library.json>   (fetched out-of-band, not committed raw)
  → drops internal-only fields (e.g. pregenerated job identifiers, internal endpoints)
  → keeps  id, name, industry, complexity, summary, description, tags,
           business_constraints, external_integrations, human_approvals, knowledge_sources
  → runs   the banned-term scrub (fails the build on any hit)
  → writes docs/assets/process-library.json   (the only committed artifact)
```

The **same** `docs/assets/process-library.json` is the data source for the
Composer *and* the sequenced `industries.html` rebuild — the scrub-and-import
pipeline is built once and reused.

### Data model (committed asset, per entry)

| Field | Type | Use |
|---|---|---|
| `id` | string | stable key, deep-link anchor |
| `name` | string | card title |
| `industry` | enum (15) | filter facet |
| `complexity` | `low`\|`medium`\|`high` | badge + filter |
| `summary` | string | one-line card body |
| `description` | string | detail panel |
| `tags` | string[] | search + chips |
| `business_constraints` | string[] | drives govern/production skills |
| `external_integrations` | object[] | drives MCP tools + sample-data skills |
| `human_approvals` | object[] | drives HITL skill |
| `knowledge_sources` | object[] | drives knowledge-integration in design/deploy |

Internal fields present in the source (e.g. `pregenerated_job_id`) are **dropped**
by the build step and never committed.

## 4. UX flow — two entry paths → four output blocks

**Entry A — Pick a scenario.** A responsive card grid of the 89 templates.
Controls: free-text search, 15 industry filter pills, complexity badges. Selecting
a card opens a detail panel and prefills the output.

**Entry B — Describe your own.** A four-field form: `industry` (select),
`one-line description` (text), `key systems / integrations` (chips), `human
approvals needed?` (toggle + optional note). Submitting derives the same output.

**Output (identical shape for both paths):**

1. **Copilot prompt** — a copy-paste block that hands the process to
   `threadlight-auto` (the trivial path) with an expandable "explicit chain"
   alternative for readers who want step control.
2. **Skill sequence** — the exact Threadlight skills that will fire, rendered as
   chips, **derived** from the process fields (§5).
3. **`azd up` quickstart** — the three commands to stand the result up on Foundry.
4. **Artifact preview** — a labelled, illustrative file tree
   (`agents/ · src/mcp-tools/ · specs/ · infra/ · evals/`) showing the *shape* of
   what the pipeline produces, so the output is tangible.

## 5. Skill-derivation logic (the credible part)

The skill sequence is **not** a fixed list — it is computed from the process's own
fields, so the reader sees skills that match their shape:

| Signal in the process | Skill(s) added |
|---|---|
| *always* (baseline arc) | `threadlight-design → local-test → safe-check → deploy → evals` |
| `external_integrations` non-empty | `threadlight-demo-data-factory` (sample data) + MCP tool build in `design` |
| `human_approvals` non-empty | `threadlight-hitl-patterns` |
| event/trigger tags present | `threadlight-event-triggers` |
| `complexity == high` or production intent | `threadlight-production-ready`, `threadlight-govern`, `threadlight-redteam` |
| cost-sensitivity tags / regulated industry | `threadlight-consumption-iq` |
| team/CI intent | `threadlight-cicd` |

The baseline arc always anchors the output; the conditional skills make it feel
tailored. The mapping table is a single source-of-truth object in the page JS and
is unit-tested (§7).

## 6. Prompt-generation (output contract)

Given a process `P`, the primary prompt is deterministic:

```
Use threadlight-auto to build a production-grade agent for this process:

  <P.name> — <P.summary>

  Industry: <P.industry>
  Key integrations: <derived from P.external_integrations>
  Human approvals: <derived from P.human_approvals>
  Constraints: <P.business_constraints joined>

Take it through design → local-test → safe-check → deploy → evals, and
stop for my review before anything touches my subscription.
```

The "explicit chain" variant expands `threadlight-auto` into the derived skill
list from §5 as an ordered set of `@threadlight-<skill>` invocations. Prompt
assembly is a pure function `buildPrompt(process) -> string` — the unit under test.

## 7. Tech, design system, testing

- **Tech:** vanilla JS + the existing static-site conventions (no build step, no
  framework). Data loaded via `fetch('assets/process-library.json')`.
- **Design system:** reuse `docs/assets/site.css` tokens to meet the
  `index.html` / `production.html` visual bar (cards, chips, icons, reveal-on-scroll).
  If `site.css` is edited, run `python3 docs/ci/sync_cache_bust.py --write` then
  `--check` (the `pages-cache-bust.yml` CI fails on drift).
- **Verification:** Playwright light-mode pass (Edge is the target browser), same
  protocol used for `production.html`.
- **Tests:**
  - `buildPrompt(process)` and `deriveSkills(process)` get unit tests
    (fixture process → expected prompt string / expected skill set).
  - The build step asserts the committed `process-library.json` contains **zero**
    banned terms and **no** dropped internal fields (CI-enforced).

## 8. Sequenced follow-ons (out of scope for PR-1, recorded for continuity)

- **Industries (`industries.html`):** rebuilt on the same
  `assets/process-library.json` — 89 filterable real scenarios, each card linking
  into the Composer prefilled ("start from this scenario").
- **Homepage one-liner (`index.html`):** a crisp *leverage-the-platform* hero,
  folded in alongside PR-1 as a quick win.
- **Small credibility fixes:** repo description ("8 skills" → 17); surface or
  retire the `router-bench` orphan. Ride along as adjacent areas are touched.

## 9. Acceptance criteria (PR-1)

1. `docs/blueprint.html` exists, is in the shared nav on all pages, and renders at
   `index/production` visual parity in light mode.
2. `docs/assets/process-library.json` is committed, scrubbed (zero banned terms,
   no internal fields), and drives both entry paths.
3. Both entry paths (Pick / Describe) produce all four output blocks; copy-to-
   clipboard works on the prompt + azd blocks.
4. Skill sequence is **derived** from process fields per §5, not hard-coded.
5. `buildPrompt` / `deriveSkills` unit tests pass; the scrub assertion passes in CI.
6. No backend, no model call, no auth, no new infra introduced.
