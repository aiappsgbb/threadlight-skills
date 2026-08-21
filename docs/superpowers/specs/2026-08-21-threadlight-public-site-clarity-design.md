# Threadlight Public Site Clarity - Design

- **Status:** Approved
- **Date:** 2026-08-21
- **Repository:** `aiappsgbb/threadlight-skills`
- **Delivery shape:** two ordinary sequential pull requests from `main`
- **Scope boundary:** public repository content only; no private roles, private discussions, fiscal labels, stakeholder names, or non-public workflow framing

## 1. As-is evidence and problem statement

The current public site already carries a strong visual system and should be
treated as a preservation asset, not as a redesign candidate.

### 1.1 Evidence that the visual system is already 9/10

- The home page uses a distinctive cinematic reel, atmospheric staging, custom
  motion, and a bespoke proof-oriented narrative instead of commodity SaaS
  sections.
- Chapter pages such as `docs/case-study.html`, `docs/production.html`,
  `docs/customize.html`, `docs/self-improving.html`, and `docs/workbook.html`
  already share a coherent typography system, editorial spacing, bespoke data
  visuals, floating ToC pattern, and polished page-specific art direction.
- Shared site chrome in `docs/assets/site.css` and `docs/assets/site.js`
  already gives the repository a recognizable visual language that should be
  reused rather than replaced.

### 1.2 Actual problem to solve

The public experience is not underperforming because it looks weak. It is
underperforming because the narrative layer is inconsistent and the advanced
story is hard to discover:

1. **Content consistency gaps** — current public copy still mixes older and newer
   semantics around `threadlight-auto`, Blueprint, cost evidence, remediation,
   and what a single guided session really proves.
2. **Advanced-topic completeness gaps** — the repository already has the raw
   material for the full public story, but it is spread across `README.md`,
   `THREADLIGHT.md`, `docs/funnel.html`, `docs/production.html`,
   `docs/case-study.html`, `docs/self-improving.html`, and `docs/workbook.html`
   without one canonical public explanation of how the complete approach fits
   together.
3. **Discoverability gaps** — the current global navigation omits a canonical
   "How it works" entry, several advanced pages are reachable only through deep
   reading or direct links, and inbound-link density is inconsistent enough that
   the best pages can feel optional.

### 1.3 Outcome required

Raise the public Threadlight site to a defensible minimum **9/10 in every
audited dimension** by improving truthfulness, completeness, and discoverability
while **preserving the existing cinematic/chapter/technical visual system** and
leaving the homepage demo/reel untouched.

## 2. Goals and non-goals

### 2.1 Goals

1. Make every public claim consistent with current repository behavior and proof
   boundaries.
2. Establish one canonical public explanation of the full Threadlight approach.
3. Make the advanced pages easy to find from the primary journey and from each
   other.
4. Clarify which pages own which parts of the story so duplication shrinks and
   maintenance becomes testable.
5. Represent the public capability model and claim set in a testable form so
   drift is caught automatically.
6. Define a measurable release rubric that prevents "looks done" sign-off
   without evidence.

### 2.2 Non-goals

- Do not modify the homepage demo/reel order, copy, visuals, behavior, assets,
  timing, or interaction.
- Do not introduce a new design system, new brand system, generic card-grid
  redesign, or lower-fidelity SaaS treatment.
- Do not add private or internal-only positioning.
- Do not imply that every advanced leg is automatic, default, or guaranteed.
- Do not build dependent-open PR stacks or any other stacked-PR workflow.
- Do not implement website changes in this design document; this spec defines
  the work only.

## 3. Canonical public truth contract

Every public surface touched by PR1 and PR2 must align to the contract below.
If an existing line of copy conflicts with any row, the copy changes.

| Topic | Canonical public truth |
|---|---|
| Core outcome | Threadlight turns a business process into a **governed working pilot** with an **evidence-backed path to production**. |
| Proof boundary | A working session can produce a deployed pilot and auditable evidence. It does **not** by itself prove customer-production readiness, settled actuals, or customer-environment onboarding. |
| `threadlight-auto` | `threadlight-auto` is an **agent-guided lifecycle planner**. It chooses eligible next steps from available evidence; it is **not** the worker that executes stages. |
| Blueprint | Blueprint emits a **deterministic starter lifecycle and starter prompt** from declared process signals. It does **not** promise the exact full execution of every later live, manual, customer-specific, or cost-bearing leg. |
| Manual and live legs | Connect, grounding proof, load evidence, CI/CD handoff, customer customization, and later actuals/reconciliation remain explicit stages with their own prerequisites. |
| Cost story | Forecast is early. **Actual cost** only becomes a trustworthy public number after **scope-bound reconciliation** against observed cost evidence for the intended workload scope. |
| Remediation | A remediation suggestion or remediation skill run is a follow-up action, not a proof of closure. **Running a remediation skill never guarantees a green result.** |
| Readiness | Safe-check, assurance, load, production-ready, and related artifacts describe evidence boundaries precisely; no public text should collapse "structurally complete", "assessed", and "ready" into one state. |

## 4. Pull request 1 - Align public claims with current behavior

### 4.1 Purpose

PR1 corrects the public contract without altering the current visual system.
This is a copy, metadata, and regression-test pass. It must ship before any
discoverability or navigation reshaping.

### 4.2 Exact surfaces to update

**Root truth surfaces**

- `README.md`
- `THREADLIGHT.md`

**Primary public Pages surfaces**

- `docs/funnel.html`
- `docs/blueprint.html`
- `docs/industries.html`
- `docs/production.html`
- `docs/case-study.html`
- `docs/self-improving.html`
- `docs/customize.html`
- `docs/workbook.html`

**Supporting public contract surfaces**

- `docs/index.html` as an **immutable homepage boundary** for regression checks
  only; PR1 and PR2 may verify it remains unchanged, but may not revise its
  demo, hero, reel, order, copy, visuals, behavior, assets, or interaction
- `docs/production-readiness.md`
- `docs/assets/site.js` only where active-nav or discoverability behavior must
  reflect the new canonical page model
- existing Node and Playwright suites under `tests/blueprint/` and
  `tests/playwright/tests/`

### 4.3 Exact contradictions to correct

The implementation should treat the following as concrete contradiction classes,
not vague guidance:

| Surface | Current contradiction or drift | Required correction |
|---|---|---|
| `docs/blueprint.html` title, hero, result copy | "exact build prompt", "drives the whole build", "you run nothing", and "ships to Azure through CI/CD" flatten later manual/live boundaries and overstate determinism | Recast Blueprint as the **starter lifecycle + starter prompt** surface; keep the convenience promise, remove exact/full-execution implication |
| `docs/blueprint.html` outputs | still references `overview.html` | Replace with the current public artifact story and remove retired artifact naming |
| `docs/funnel.html` five-stage framing | presents a simplified flow as if it were the complete canonical public approach | Reframe the page as the canonical **How it works** explanation with explicit automated/manual/evidence boundaries |
| `docs/funnel.html` stage ownership | conversation/design flow currently names `threadlight-auto` in a way that can be read as execution ownership | Preserve Auto only as planner guidance, never as the worker lane |
| `docs/industries.html` metadata and hero/library copy | promises an exact skill sequence and a no-commands path that hides later handoffs | State that Industries feeds the starter plan into Blueprint/How it works, while later legs depend on the chosen path |
| `docs/index.html` demo and hero boundary | the approved scope forbids touching the homepage demo/reel, hero, order, copy, visuals, behavior, assets, or interaction in either PR | Treat Home as **immutable content**. The only permitted Home impact is the shared global nav/chrome addition from PR2, and that addition must not alter demo content, geometry, timing, or interaction |
| `docs/production.html` and `docs/production-readiness.md` | remediation language can be read as if naming the fix is close to closing the gap | Make remediation explicit follow-up work and state that reruns determine outcome |
| `README.md` and `THREADLIGHT.md` | must remain the canonical exhaustive technical references and stay word-for-word aligned with the same truth contract used on Pages | Add or keep parity assertions so they do not drift apart |

### 4.4 Metadata and parity rules

PR1 must align:

1. page titles;
2. meta descriptions;
3. OG/Twitter descriptions;
4. visible H1/H2 framing where it defines the public meaning of a page;
5. skill counts and capability counts where they are presented publicly;
6. `README.md` / `THREADLIGHT.md` lifecycle wording;
7. retired artifact names and stale direct-output claims.

### 4.5 Regression-test contract for PR1

PR1 must add or extend tests that fail on public-contract regressions:

- **published surfaces assertions** for canonical terms and banned drift terms;
- **README/THREADLIGHT parity** checks for lifecycle classification, Auto
  wording, and cost-evidence phrasing;
- **metadata assertions** for key Pages surfaces;
- **immutable homepage assertions** proving the approved Home demo boundary was
  not touched;
- **link and fragment checks** for touched pages;
- **browser assertions** that the touched pages still render the corrected copy
  in the existing layouts.

At minimum, the test suite should fail if public text reintroduces:

- Auto-as-worker wording;
- Blueprint-as-exact-execution wording;
- actual-cost claims without reconciliation;
- remediation-guarantees-green wording;
- retired artifact names;
- any unauthorized `docs/index.html` content diff inside the immutable homepage
  boundary;
- page-title/nav mismatches for the canonical public journey.

### 4.6 Visual boundary

PR1 makes **no visual redesign changes**. Copy, metadata, link structure,
fixtures, and tests may change. The homepage reel and the broader visual system
must not. `docs/index.html` is regression-tested, not rewritten.

## 5. Pull request 2 - Make the complete approach discoverable

### 5.1 Purpose

PR2 turns the already-correct public story into an easy-to-follow public
journey. It does this by making one page canonical, tightening page ownership,
and enforcing a discoverability contract across the site.

### 5.2 Canonical "How it works" page

`docs/funnel.html` becomes the canonical public **How it works** page.

Implementation requirements:

1. Keep the file path `docs/funnel.html` unless a separate redirect decision is
   made later; for this work, the public label becomes **How it works**.
2. Update the page title, metadata, nav label, hero, and section framing so the
   page clearly owns the end-to-end public approach.
3. Make this page the canonical public place where the six-phase capability
   model, the public truth contract, and the page-to-page wayfinding are taught.
4. Do not duplicate the homepage reel or attempt to replace the home page with
   a new top-level long-form explainer.

### 5.3 Navigation and orphan-page rules

PR2 must enforce the following rules:

- global navigation adds **How it works** on every chapter page;
- active-nav state is consistent on desktop and mobile;
- no public page is discoverable only through footer links or direct URL entry;
- the shared nav/chrome update may appear on Home, but it must not change demo
  content, hero copy, reel geometry, motion timing, or interaction behavior;
- no chapter page is orphaned from the primary site journey.

### 5.4 Inbound-link contract

Every advanced page must have **at least three contextual inbound links** from
other public surfaces. "Contextual" means in-body or section-level links that
help a reader choose the next page for a reason; footer-only repetition does not
count.

Minimum requirement by category:

- `docs/production.html`
- `docs/case-study.html`
- `docs/self-improving.html`
- `docs/customize.html`
- `docs/workbook.html`
- `README.md`
- `THREADLIGHT.md`

Each of the above must receive at least three contextual inbound links from
owned surfaces that make sense for the story, for example:

- Home -> How it works / Case study / Workbook / Production
- How it works -> Blueprint / Production / Case study / Industries / Customize / Self-improving
- Blueprint -> How it works / Industries / Workbook
- Production -> Case study / Customize / README / THREADLIGHT
- Self-improving -> Production / README / THREADLIGHT

### 5.5 Page-level discoverability requirements

PR2 must ensure the user can move intentionally between:

1. **demo-first** interest (`docs/index.html`);
2. **complete approach** understanding (`docs/funnel.html`);
3. **starter-plan generation** (`docs/blueprint.html`, `docs/industries.html`);
4. **evidence and proof** (`docs/case-study.html`, `docs/production.html`);
5. **hands-on exploration** (`docs/workbook.html`);
6. **customer-production handoff** (`docs/customize.html`);
7. **advanced diagnostics and lifecycle learning** (`docs/self-improving.html`);
8. **exhaustive technical reference** (`README.md`, `THREADLIGHT.md`).

## 6. Six-phase capability model

### 6.1 Canonical phase map for all 22 skills

The public capability model must use exactly these six phases:

1. **Enter**
2. **Build**
3. **Integrate**
4. **Assure**
5. **Ship**
6. **Improve**

The public map must place every skill in the model exactly once, with the note
that `threadlight-auto` is a planner overlay, not a worker.

| Phase | Skills | Public role |
|---|---|---|
| Enter | `threadlight-qualify`, `threadlight-design` | turn a brief into a scoped, structured starting point |
| Build | `threadlight-auto`, `threadlight-demo-data-factory`, `threadlight-local-test`, `threadlight-deploy` | derive the starter lifecycle, seed the build, prove it locally, and land the first pilot |
| Integrate | `threadlight-connect`, `threadlight-hitl-patterns`, `threadlight-workspace-ui`, `threadlight-event-triggers` | attach real systems and operator surfaces where the use case needs them |
| Assure | `threadlight-safe-check`, `threadlight-ground`, `threadlight-consumption-iq`, `threadlight-evals`, `threadlight-redteam`, `threadlight-govern`, `threadlight-loadtest`, `threadlight-production-ready` | prove structure, grounding, cost, quality, safety, governance, load behavior, and readiness boundaries |
| Ship | `threadlight-cicd`, `threadlight-customize` | move the proven pilot through production delivery and customer-environment onboarding |
| Improve | `threadlight-router-bench`, `threadlight-upgrade` | turn finished runs into learnings and detect compatibility drift before it becomes breakage |

### 6.2 Advanced-theme documentation contract

For every advanced theme below, the canonical How it works page and any owning
reference surface must provide the same five things:

1. **Purpose** — what this theme is for
2. **Trigger** — when a user would reach for it
3. **Automation boundary** — what is automatic vs manual/live/advisory/plan-only
4. **Evidence artifact** — the artifact or proof surface it leaves behind
5. **Deep link** — the direct public link to learn more

| Theme | Backing skill(s) | Evidence artifact | Deep link target |
|---|---|---|---|
| Qualify / sizing / ROI | `threadlight-qualify` | `qualification/sizing.md`, `qualification/discovery.md`, optional `qualification/roi.md` | README / `skills/threadlight-qualify/` |
| Starter design and starter lifecycle | `threadlight-design` | `specs/SPEC.md`, `AGENTS.md`, public design artifacts | `docs/blueprint.html`, README, `THREADLIGHT.md` |
| Connect real systems | `threadlight-connect` | `specs/connect-manifest.json` | README / `THREADLIGHT.md` / skill page |
| Grounding proof | `threadlight-ground` | `specs/ground-manifest.json` | README / `THREADLIGHT.md` / skill page |
| HITL and workspace UI | `threadlight-hitl-patterns`, `threadlight-workspace-ui` | action-gate and operator-surface outputs derived from SPEC sections 8 and 8b | `docs/production.html`, README, skill pages |
| Event triggers | `threadlight-event-triggers` | receiver and trigger artifacts derived from SPEC section 10 | How it works, skill page |
| Forecast / actuals / reconciliation | `threadlight-consumption-iq` | `specs/cost-manifest.json`, `specs/cost-actuals-manifest.json`, `specs/cost-reconciliation-manifest.json`, cost docs | README / `THREADLIGHT.md` / `docs/production.html` |
| Evals | `threadlight-evals` | `specs/evals-manifest.json` | `docs/production.html`, README, skill page |
| Red-team | `threadlight-redteam` | `docs/redteam-report.md`, `specs/redteam-manifest.json` | `docs/production.html`, README, skill page |
| Governance | `threadlight-govern` | `specs/govern-manifest.json` | `docs/production.html`, README, skill page |
| Load | `threadlight-loadtest` | `specs/load-manifest.json` | `docs/production.html`, README, skill page |
| Production-ready | `threadlight-production-ready` | `docs/production-readiness-report.md` and companion manifest | `docs/production.html`, `docs/case-study.html`, README |
| CI/CD | `threadlight-cicd` | production pipeline and environment-setup runbooks | `docs/customize.html`, README, skill page |
| Customize | `threadlight-customize` | customer-profile, customization-map, non-coverage, and overlay runbooks | `docs/customize.html`, README, skill page |
| Upgrade | `threadlight-upgrade` | `specs/upgrade-manifest.json` and ordered migration plan | `docs/self-improving.html`, README, skill page |
| Router-bench | `threadlight-router-bench` | learnings digest and optional model-router scorecard | `docs/self-improving.html`, README, skill page |

Public copy for these themes must not collapse their automation boundaries. For
example, How it works may say "this stage adds live or manual proof when the
prerequisites exist," but it must not imply that those proofs are always
generated by the starter flow.

## 7. Page ownership

Each public surface must own one clear part of the story:

| Surface | Sole public responsibility |
|---|---|
| Home (`docs/index.html`) | demo-led introduction; immutable demo surface except for shared nav/chrome additions that do not alter demo content or geometry |
| Funnel / How it works (`docs/funnel.html`) | canonical complete public approach |
| Blueprint (`docs/blueprint.html`) | process-to-starter-plan generation |
| Case study (`docs/case-study.html`) | proof that the approach has been run |
| Production (`docs/production.html`) | assurance, architecture, evidence boundaries, and readiness framing |
| Industries (`docs/industries.html`) | catalogue and entry to starter plans |
| Self-improving (`docs/self-improving.html`) | diagnostics-to-backlog and upgrade path |
| Customize (`docs/customize.html`) | customer-production onboarding |
| Workbook (`docs/workbook.html`) | hands-on path |
| `README.md` and `THREADLIGHT.md` | exhaustive technical reference |

Implications:

- Home should tease, not exhaustively explain.
- Home is not an authorized copy-edit surface for this effort; it is an
  immutable demo boundary with regression proofs.
- How it works should connect the whole approach, not duplicate the homepage
  demo or the hands-on workbook.
- Blueprint should explain the starter-plan boundary, not swallow the full
  lifecycle explanation.
- Production should own the hardest boundary language around evidence, cost,
  and readiness.
- README and THREADLIGHT should remain the most complete technical references
  and receive deep links from the Pages experience.

## 8. Visual design requirements

The implementation must preserve and reuse the existing visual system:

1. reuse the current typography, tokens, chapter patterns, cinematic patterns,
   editorial spacing, and bespoke technical diagrams;
2. do not replace bespoke visuals with generic SaaS grids or undifferentiated
   card stacks;
3. keep all additions visually native to the current system;
4. support desktop, tablet, mobile, and 200% browser zoom;
5. allow **no horizontal overflow** at any reviewed breakpoint;
6. keep interactive controls at **44px minimum touch target** where practical;
7. run visual review at **1440px**, **1024px**, and **390px** widths;
8. preserve the homepage reel exactly as it is;
9. if Home receives the shared nav/chrome addition in PR2, that change must not
   alter hero copy, demo order, demo geometry, asset payloads, interaction
   timing, or behavior.

Navigation or discoverability changes must be expressed as extensions of the
existing chrome, not as a new site shell.

## 9. Content-system and testing architecture

### 9.1 Canonical public contract source

Where feasible, PR1 and PR2 should represent the public claim set and capability
map in **one testable source of truth** or small fixture set instead of
re-encoding the same facts in scattered tests.

That canonical testable source should include:

- canonical truth statements from section 3;
- the six-phase skill map from section 6;
- page ownership from section 7;
- required nav entries and required deep links;
- the immutable-homepage boundary definition and its approved allowlist;
- banned stale terms and banned misleading claims.

Whether this lives as a JSON fixture, JS fixture, or Markdown-derived test input
is an implementation detail. The important requirement is that tests read from a
single contract source wherever practical.

### 9.2 Required test coverage

The delivery must extend existing test surfaces, not invent an unrelated new
framework.

Required coverage:

1. **public-links assertions** — no broken links or fragments on touched pages;
2. **metadata assertions** — title, description, and canonical public labeling
   stay aligned with page ownership;
3. **no stale claims assertions** — banned drift terms fail fast;
4. **direct skill-link assertions** — capability deep links point to the correct
   public reference or skill page;
5. **browser tests** — nav, active state, How it works flow, inbound links, and
   ownership-signaled CTAs are exercised in Playwright;
6. **immutable homepage guard** — baseline hashes or a scoped git-diff allowlist
   prove the demo DOM/assets stayed untouched; permitted scope is limited to the
   shared nav/chrome addition in PR2;
7. **cache-bust coverage** — changed assets remain wired into existing
   cache-busting checks and workflows.

### 9.3 Safety rules for content architecture

- No claim should exist on one page only if that claim is canonical across the
  site.
- No page should claim an advanced capability without a discoverable deep link
  to the owning explanation.
- No technical reference should quietly diverge from the public site.
- No actual-cost wording should survive without explicit reconciliation context.
- No completion claim should be accepted unless the immutable homepage boundary
  proof is attached.

## 10. 9/10 release rubric for audited areas

The work is not complete because it "feels clearer." It is complete only when
every audited area scores at least **9/10** using the rubric below.

### 10.1 Scoring rule

- **10/10** — no material issue found; evidence fully supports the score
- **9/10** — at most one minor issue remains, with no misleading copy, no
  broken flow, and no visual or discoverability defect that changes user
  understanding
- **8/10 or lower** — fail; the area is not release-ready

### 10.2 Audited dimensions

| Dimension | Pass threshold | Evidence required |
|---|---|---|
| Art direction | Existing cinematic/chapter identity preserved with no generic visual regression | screenshot set at 1440/1024/390 plus reviewer checklist against current visual system |
| Typography | Existing hierarchy remains readable at desktop/mobile/200% zoom | zoom audit, responsive screenshots, no clipped headings/body text |
| Responsive behavior | No horizontal overflow; nav and TOC behavior remain usable | Playwright/mobile checks plus manual zoom review |
| Bespoke UX | New or changed wayfinding feels native to the current chrome, not generic | before/after screenshot review and interaction checklist |
| Homepage immutability | Home demo, hero, reel, assets, and interaction match baseline exactly apart from the approved shared nav/chrome delta in PR2 | baseline hash report or scoped git-diff allowlist plus focused browser comparison |
| Cross-page consistency | labels, counts, lifecycle boundaries, and active nav states agree across pages | contract tests plus manual click-through |
| Advanced discoverability | every advanced page has >=3 contextual inbound links and the user can intentionally reach it from the main journey | link-map checklist plus browser tests |
| Content consistency | canonical truth contract has zero contradictions across owned surfaces | claim-matrix checklist plus Node assertions |
| Full advanced-story clarity | a reader can understand the complete public approach without jumping into source code first | How it works page review, page ownership review, and deep-link verification |

### 10.3 Release evidence rule

No PR may claim completion, clarity, or 9/10 status without:

1. a completed checklist against every rubric row;
2. fresh targeted test runs;
3. fresh responsive screenshots or visual review captures;
4. explicit confirmation that the homepage demo/reel remained untouched;
5. baseline hash evidence or a scoped git-diff allowlist proving any Home delta
   stayed inside the approved nav/chrome boundary.

## 11. Sequencing, rollback boundaries, and scope cuts

### 11.1 Sequencing

1. **PR1 merges first** from this spec's truth contract and test architecture.
2. **PR2 starts only after PR1 is merged to `main`** and must branch from the
   updated `main`.
3. PR2 is an ordinary follow-up PR, not a dependent-open PR and not a native
   PR stack.

### 11.2 Rollback boundaries

- PR1 must be independently reversible as a truth-contract change set:
  copy/metadata/tests only.
- PR2 must be independently reversible as a discoverability and navigation
  change set layered on top of PR1.
- Shared fixture changes should be structured so PR1 rollback does not require
  untangling a nav rewrite.

### 11.3 Scope cuts if schedule pressure appears

Allowed scope cuts:

- defer non-essential secondary cross-links after the minimum contextual-link
  quota is met;
- defer low-value copy polish that does not affect truth, ownership, or
  discoverability;
- defer route renaming in favor of keeping `funnel.html` as the canonical How it
  works path.

Disallowed scope cuts:

- dropping the canonical truth contract;
- skipping README/THREADLIGHT parity;
- skipping the How it works nav entry;
- skipping the >=3 inbound-link rule for advanced pages;
- skipping rubric evidence or regression tests;
- skipping immutable-homepage proof;
- touching or moving the homepage reel.

## 12. Acceptance criteria and explicit exit condition

### 12.1 Acceptance criteria

1. Every public truth surface reflects the contract in section 3.
2. `docs/funnel.html` is the canonical public How it works page in label,
   metadata, and navigation.
3. Global navigation exposes How it works consistently and highlights active
   state correctly.
4. No public page is orphaned.
5. Every advanced page has at least three contextual inbound links.
6. The six-phase capability model maps all 22 skills and clearly distinguishes
   planner, automatic, manual, advisory, live, and plan-only boundaries.
7. Page ownership is explicit and respected by copy and links.
8. The current cinematic visual system is preserved; the homepage reel remains
   untouched.
9. Public claims, metadata, links, browser flows, and cache-bust behavior are
   covered by targeted existing test surfaces.
10. Home immutability is proven by baseline hashes or a scoped git-diff
    allowlist attached to the release evidence.
11. Every audited dimension in section 10 scores at least 9/10 with evidence.

### 12.2 Explicit exit condition

This effort is complete only when both sequential PRs have been implemented and
verified such that:

- PR1 leaves the public repo with no material truth-contract contradictions;
- PR2 leaves the public site with no orphan pages and no advanced-page
  discoverability gap below the required threshold;
- the homepage reel is unchanged;
- immutable-homepage proof shows no unauthorized Home delta;
- fresh tests and visual review evidence support a minimum 9/10 score in every
  audited area;
- no remaining open issue would force public readers to infer a false proof,
  false automation boundary, or false production claim.
