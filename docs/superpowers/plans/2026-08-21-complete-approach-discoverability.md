# Complete Approach Discoverability Implementation Plan

> **Historical approval record - scope update 2026-09-10.** Retained as the
> August implementation plan, not a command sequence to replay against the
> current site. Later approved work added Basics, grouped navigation and
> limited Home structure changes while preserving the demo. Consult
> [site-map.js](../../assets/site-map.js), the
> [demo baseline](../../../tests/blueprint/fixtures/home-demo-baseline.json), and
> the [engineering guide](../../skill-based-agents.md) for the current contracts.
> Original counts, task checkboxes and scores are not fresh acceptance evidence.
> Delivery was subsequently approved as one ordinary PR after alignment with
> current main. Merge and live publication still require separate approval;
> the original two-PR sequence below is historical.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the complete public Threadlight approach discoverable by turning `docs/funnel.html` into the canonical “How it works” page, wiring that page into the shared nav and contextual link graph, and proving 9/10 discoverability/consistency/responsive quality without touching the homepage demo content.

**Architecture:** Reuse the current cinematic `funnel.html` sections and shared chrome rather than inventing a new design system. Drive the information architecture from the PR1 `tests/blueprint/fixtures/public-contract.js` contract: reuse its six-phase map, page ownership map, and automation boundaries in both HTML copy and regression tests, then add link-graph and responsive checks that make orphaned/underlinked advanced pages impossible to ship.

**Tech Stack:** Static HTML/CSS/JS in `docs/`, existing shared nav/mobile-nav behavior in `docs/assets/site.js`, CommonJS `node --test` tests in `tests/blueprint/`, Playwright + axe in `tests/playwright/`, Git diff allowlist for the Home boundary.

---

## File structure

- Modify: `docs/funnel.html` — canonical “How it works” chapter using the existing cinematic sections and components.
- Modify: `docs/index.html` — nav/chrome only in PR2: add the How it works link and nothing else.
- Modify: `docs/blueprint.html` — add How it works nav entry, active state parity, contextual links back into the canonical approach.
- Modify: `docs/case-study.html` — add How it works nav entry and contextual links to proof/phase sections.
- Modify: `docs/production.html` — add How it works nav entry and contextual links into Assure/Ship.
- Modify: `docs/industries.html` — move from off-nav sub-page to owned chapter surface with nav current-state/breadcrumb consistency.
- Modify: `docs/self-improving.html` — explicitly surface `threadlight-router-bench`, qualify/sizing/ROI context, and deep links back to How it works.
- Modify: `docs/customize.html` — add How it works nav entry and contextual links to the Ship phase.
- Modify: `docs/workbook.html` — add How it works nav entry and contextual links to the hands-on page owner.
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `tests/blueprint/published-surfaces.test.js` — six-phase map, 22-skill uniqueness, page ownership, inbound-link graph, home allowlist, and nav parity assertions.
- Modify: `tests/blueprint/public-links.test.js` — fragment/link integrity for the repurposed funnel anchors.
- Modify: `tests/playwright/tests/site.spec.mjs` — shared nav/current-state, Home allowlist, responsive/no-overflow/touch-target checks.
- Modify: `tests/playwright/tests/how-it-works.spec.mjs` — move from Home primer band to canonical `/funnel.html` page contract.
- Modify: `tests/playwright/tests/blueprint.spec.mjs`
- Modify: `tests/playwright/tests/industries.spec.mjs`
- Modify: `tests/playwright/tests/gap-closure.spec.mjs`

### Task 1: Start PR2 from updated `main` and write failing IA tests first

**Files:**
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/playwright/tests/site.spec.mjs`
- Modify: `tests/playwright/tests/how-it-works.spec.mjs`

- [ ] **Step 1: Create the PR2 branch only after PR1 is merged, and write the failing information-architecture tests before changing HTML.**

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feat/public-site-how-it-works
```

```js
test('canonical How it works IA is present across public chapter pages', () => {
  const funnel = read('docs/funnel.html');
  assert.match(funnel, /How it works/i);
  assert.match(funnel, /Enter|Build|Integrate|Assure|Ship|Improve/);
  for (const file of [
    'docs/index.html',
    'docs/blueprint.html',
    'docs/case-study.html',
    'docs/production.html',
    'docs/industries.html',
    'docs/self-improving.html',
    'docs/customize.html',
    'docs/workbook.html',
  ]) {
    const html = read(file);
    assert.match(html, /href="\.\/funnel\.html"/, `${file} missing How it works nav link`);
  }
});
```

```js
test.describe('How it works page', () => {
  test('funnel.html is the canonical How it works page', async ({ page }) => {
    await page.goto('/funnel.html');
    await expect(page).toHaveTitle(/How it works/i);
    await expect(page.locator('#scene-hero')).toContainText(/How Threadlight works/i);
    await expect(page.locator('#scene-funnel .phase-card')).toHaveCount(6);
  });
});
```

- [ ] **Step 2: Run the targeted tests and verify they fail on the current five-stage/off-nav structure.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/how-it-works.spec.mjs tests/site.spec.mjs --grep "canonical How it works|How it works page"`

Expected: **FAIL** because `funnel.html` is still the old funnel narrative and Home has no How it works nav link yet.

- [ ] **Step 3: Commit only after the HTML changes in Tasks 2-5 make these IA tests pass.**

No commit yet; this task creates the failing test baseline for the rest of PR2.

### Task 2: Repurpose `funnel.html` into the canonical How it works chapter using existing sections

**Files:**
- Modify: `docs/funnel.html`
- Modify: `tests/playwright/tests/how-it-works.spec.mjs`
- Modify: `tests/blueprint/published-surfaces.test.js`

- [ ] **Step 1: Replace the hero + scene copy with the approved section mapping, reusing existing cinematic sections instead of inventing new widgets.**

```md
Section mapping for `docs/funnel.html`:

- `#scene-hero` → “How Threadlight works” hero + canonical public truth contract + Auto overlay callout.
- `#scene-show` → “What stays explicit” chain: governed working pilot, manual/live/cost-bearing legs, later-pilot actuals, production hand-offs.
- `#scene-funnel` → six phase cards: Enter, Build, Integrate, Assure, Ship, Improve.
- `#scene-chain` → 22-skill map: each skill appears exactly once under one phase rail; `threadlight-auto` appears once as an overlay banner, not a 23rd skill card.
- `#scene-prod-ready` → advanced theme grid part 1: qualify/sizing/ROI, connect, grounding, evals, red-team, governance, load, forecast/actuals/reconciliation, production-ready.
- `#scene-customize` → advanced theme grid part 2: HITL/workspace UI, event triggers, CI/CD, customize, upgrade, router-bench.
- `#scene-industries` → page-owner jump list: industries catalogue, workbook hands-on path, case-study proof, production assurance, README, THREADLIGHT.
- `#scene-kratos` → optional adjacent path note only; visually secondary, not part of the six-phase model.
- `#scene-cta` → final next-step matrix with direct links to Blueprint, Workbook, Case study, Production-ready, README, THREADLIGHT.
```

- [ ] **Step 2: Update the actual `funnel.html` hero, chain, and phase markup to the new copy and selectors.**

```html
<title>How it works — governed working pilot, explicit hand-offs, evidence-backed path.</title>
<meta name="description" content="How Threadlight works: six phases, twenty-one pipeline skills plus the threadlight-auto planner, explicit manual/live/cost-bearing boundaries, and direct links to the owning proof, production, workbook, and technical pages.">
...
<h1 class="hero-headline" id="hero-headline">How <em>Threadlight works.</em></h1>
<p class="hero-sub">
  A business process becomes a <strong>governed working pilot</strong> with an
  <strong>evidence-backed path to production</strong>. <code>threadlight-auto</code> is the
  <strong>agent-guided lifecycle planner</strong>; it chooses the next stage and hands execution
  to the coding agent. Manual, live, cost-bearing, and customer-environment legs stay explicit.
</p>
<aside class="hero-banner" role="note" aria-label="Planner boundary">
  <span class="hb-text"><strong>Planner, not worker.</strong> Auto plans the next move. It does not run qualify, connect, ground, loadtest, cicd, customize, upgrade, or router-bench for you.</span>
</aside>
```

```html
<section class="scene" id="scene-funnel" aria-labelledby="funnel-h" data-toc-id="scene-funnel" data-toc-label="Six phases">
  <div class="phase-grid">
    <article class="phase-card" data-phase="enter"><span class="fs-num">01 · Enter</span><h3>Qualify the opportunity and seed the starter plan.</h3></article>
    <article class="phase-card" data-phase="build"><span class="fs-num">02 · Build</span><h3>Draft, scaffold, and validate the pilot.</h3></article>
    <article class="phase-card" data-phase="integrate"><span class="fs-num">03 · Integrate</span><h3>Swap in real systems and human gates deliberately.</h3></article>
    <article class="phase-card" data-phase="assure"><span class="fs-num">04 · Assure</span><h3>Score quality, safety, governance, cost, and load evidence.</h3></article>
    <article class="phase-card" data-phase="ship"><span class="fs-num">05 · Ship</span><h3>Prepare the production hand-off, pipeline, and customer onboarding.</h3></article>
    <article class="phase-card" data-phase="improve"><span class="fs-num">06 · Improve</span><h3>Learn from finished runs and plan upgrades without mutating prod.</h3></article>
  </div>
</section>
```

- [ ] **Step 3: Replace the old Home-primer Playwright contract with the new `/funnel.html` contract.**

```js
test('renders six phases in order and keeps Auto as an overlay, not a phase', async ({ page }) => {
  await page.goto('/funnel.html');
  const phases = page.locator('#scene-funnel .phase-card');
  await expect(phases).toHaveCount(6);
  await expect(phases.nth(0)).toContainText(/Enter/);
  await expect(phases.nth(5)).toContainText(/Improve/);
  await expect(page.locator('#scene-chain [data-auto-overlay]')).toContainText(/planner, not worker/i);
});
```

- [ ] **Step 4: Re-run the targeted tests until they pass.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/how-it-works.spec.mjs`

Expected: **PASS** for the new canonical How it works structure.

- [ ] **Step 5: Commit the canonical How it works page conversion.**

```bash
git add docs/funnel.html tests/blueprint/published-surfaces.test.js tests/playwright/tests/how-it-works.spec.mjs
git commit -m "docs: make funnel the canonical how it works page"
```

### Task 3: Map all 22 skills exactly once and publish the advanced-theme cards

**Files:**
- Modify: `docs/funnel.html`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/playwright/tests/how-it-works.spec.mjs`
- Modify: `tests/playwright/tests/gap-closure.spec.mjs`

- [ ] **Step 1: Add failing assertions for the 21 pipeline skills, one Auto overlay, and the advanced-theme card fields.**

```js
test('How it works maps all 21 pipeline skills exactly once plus one Auto overlay', () => {
  const funnel = read('docs/funnel.html');
  const skills = [
    'threadlight-qualify', 'threadlight-design', 'threadlight-demo-data-factory',
    'threadlight-local-test', 'threadlight-deploy', 'threadlight-workspace-ui',
    'threadlight-connect', 'threadlight-ground', 'threadlight-hitl-patterns', 'threadlight-event-triggers',
    'threadlight-safe-check', 'threadlight-consumption-iq', 'threadlight-evals', 'threadlight-redteam', 'threadlight-govern', 'threadlight-loadtest',
    'threadlight-production-ready', 'threadlight-cicd', 'threadlight-customize',
    'threadlight-router-bench', 'threadlight-upgrade',
  ];
  for (const skill of skills) {
    assert.strictEqual((funnel.match(new RegExp(skill, 'g')) || []).length, 1, `${skill} must appear exactly once on /funnel.html`);
  }
  assert.strictEqual((funnel.match(/threadlight-auto/g) || []).length, 1, 'threadlight-auto must appear once as the planner overlay');
  for (const theme of ['qualify', 'connect', 'grounding', 'evals', 'red-team', 'governance', 'load', 'forecast-actuals-reconciliation', 'production-ready', 'hitl-workspace-ui', 'event-triggers', 'cicd', 'customize', 'upgrade', 'router-bench']) {
    assert.match(funnel, new RegExp(`data-theme="${theme}"`), `missing theme card ${theme}`);
  }
});
```

```js
await expect(page.locator('.advanced-theme-card')).toHaveCount(15);
for (const card of await page.locator('.advanced-theme-card').all()) {
  await expect(card.locator('.theme-purpose')).toBeVisible();
  await expect(card.locator('.theme-trigger')).toBeVisible();
  await expect(card.locator('.boundary-badge')).toBeVisible();
  await expect(card.locator('.evidence-artifact code')).toBeVisible();
  await expect(card.locator('a[data-skill-link]')).toBeVisible();
}
```

- [ ] **Step 2: Run the targeted tests and capture the current skill/theme failures.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/how-it-works.spec.mjs tests/gap-closure.spec.mjs --grep "maps all 21 pipeline skills|advanced-theme"`

Expected: **FAIL** because the old chain still duplicates concepts and does not expose 15 explicit advanced-theme cards.

- [ ] **Step 3: Replace the old chain markup with phase rails and advanced-theme cards.**

```html
<section class="scene" id="scene-chain" aria-labelledby="chain-h" data-toc-id="scene-chain" data-toc-label="Skill map">
  <div class="planner-overlay" data-auto-overlay>
    <p><code>threadlight-auto</code> overlays every phase as the planner. It reads evidence, chooses the next stage, and hands execution to the coding agent.</p>
  </div>
  <div class="phase-rail" data-phase="enter">threadlight-qualify · threadlight-design · threadlight-demo-data-factory</div>
  <div class="phase-rail" data-phase="build">threadlight-local-test · threadlight-deploy · threadlight-workspace-ui</div>
  <div class="phase-rail" data-phase="integrate">threadlight-connect · threadlight-ground · threadlight-hitl-patterns · threadlight-event-triggers</div>
  <div class="phase-rail" data-phase="assure">threadlight-safe-check · threadlight-consumption-iq · threadlight-evals · threadlight-redteam · threadlight-govern · threadlight-loadtest</div>
  <div class="phase-rail" data-phase="ship">threadlight-production-ready · threadlight-cicd · threadlight-customize</div>
  <div class="phase-rail" data-phase="improve">threadlight-router-bench · threadlight-upgrade</div>
</section>
```

```html
<article class="advanced-theme-card" data-theme="forecast-actuals-reconciliation">
  <p class="theme-purpose">Purpose: forecast early, read settled Azure actuals later, and verify cost per successful interaction only after reconciliation.</p>
  <p class="theme-trigger">Trigger: a pilot exists and the cost story is part of the customer review.</p>
  <span class="boundary-badge" data-boundary="later-pilot">Later-pilot evidence</span>
  <p class="evidence-artifact">Evidence: <code>docs/cost-projection.md</code>, <code>specs/cost-manifest.json</code>, later-pilot settled actuals + reconciliation.</p>
  <a data-skill-link="threadlight-consumption-iq" href="https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-consumption-iq">Open the skill</a>
</article>
```

- [ ] **Step 4: Re-run the targeted tests until they pass.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/how-it-works.spec.mjs tests/gap-closure.spec.mjs`

Expected: **PASS** with each skill appearing once and every advanced theme carrying purpose, trigger, boundary, evidence, and deep link.

- [ ] **Step 5: Commit the skill map + advanced-theme layer.**

```bash
git add docs/funnel.html tests/blueprint/published-surfaces.test.js \
  tests/playwright/tests/how-it-works.spec.mjs tests/playwright/tests/gap-closure.spec.mjs
git commit -m "docs: publish complete approach skill map"
```

### Task 4: Add How it works to shared nav and keep the Home change inside the allowlist

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/blueprint.html`
- Modify: `docs/case-study.html`
- Modify: `docs/production.html`
- Modify: `docs/industries.html`
- Modify: `docs/self-improving.html`
- Modify: `docs/customize.html`
- Modify: `docs/workbook.html`
- Modify: `tests/playwright/tests/site.spec.mjs`
- Modify: `tests/playwright/tests/blueprint.spec.mjs`
- Modify: `tests/playwright/tests/industries.spec.mjs`

- [ ] **Step 1: Add failing nav/current-state tests, including the Home diff allowlist.**

```js
test('shared nav exposes Home, How it works, Blueprint, Case study, Production-ready, Customize', async ({ page }) => {
  for (const url of ['/index.html', '/funnel.html', '/blueprint.html', '/case-study.html', '/production.html', '/industries.html', '/self-improving.html', '/customize.html', '/workbook.html']) {
    await page.goto(url);
    const links = page.locator('header.masthead nav.nav a');
    await expect(links).toHaveCount(6);
    await expect(links.nth(1)).toHaveAttribute('href', './funnel.html');
    await expect(links.nth(1)).toContainText(/How it works/);
  }
});
```

```js
test('PR2 Home diff allowlist only permits nav/chrome delta', () => {
  const index = read('docs/index.html');
  assert.match(index, /href="\.\/funnel\.html"/);
  assert.doesNotMatch(index, /How Threadlight works\./); // Home body copy must stay untouched.
});
```

- [ ] **Step 2: Run the targeted nav tests and verify they fail before the nav updates.**

Run: `cd tests/playwright && npx playwright test tests/site.spec.mjs tests/blueprint.spec.mjs tests/industries.spec.mjs --grep "shared nav exposes|diff allowlist"`

Expected: **FAIL** because the nav still has five links and Industries is still treated as off-nav.

- [ ] **Step 3: Add the How it works link consistently and update current-state handling.**

```html
<nav class="nav" aria-label="Site chapters">
  <a href="./index.html">Home</a>
  <a href="./funnel.html" aria-current="page">How it works</a>
  <a href="./blueprint.html">Blueprint</a>
  <a href="./case-study.html">Case study</a>
  <a href="./production.html">Production-ready</a>
  <a href="./customize.html">Customize</a>
</nav>
```

```html
<!-- Home only: add the How it works nav link, keep all body/demo markup unchanged -->
<nav class="nav" aria-label="Site chapters">
  <a href="./index.html" aria-current="page">Home</a>
  <a href="./funnel.html">How it works</a>
  <a href="./blueprint.html">Blueprint</a>
  <a href="./case-study.html">Case study</a>
  <a href="./production.html">Production-ready</a>
  <a href="./customize.html">Customize</a>
</nav>
```

- [ ] **Step 4: Re-run the targeted tests, then inspect the scoped Home diff allowlist manually.**

Run: `cd tests/playwright && npx playwright test tests/site.spec.mjs tests/blueprint.spec.mjs tests/industries.spec.mjs`

Expected: **PASS**.

Run:

```bash
git --no-pager diff -- docs/index.html
```

Expected: only nav/chrome lines change — the inserted `./funnel.html` anchor and any associated `aria-current`/mobile-nav current-state delta. No hero, reel, copy, geometry, asset, or interaction lines change.

- [ ] **Step 5: Commit the nav/current-state update.**

```bash
git add docs/index.html docs/blueprint.html docs/case-study.html docs/production.html \
  docs/industries.html docs/self-improving.html docs/customize.html docs/workbook.html \
  tests/playwright/tests/site.spec.mjs tests/playwright/tests/blueprint.spec.mjs \
  tests/playwright/tests/industries.spec.mjs
git commit -m "docs: add how it works to shared nav"
```

### Task 5: Build the contextual inbound-link graph so no advanced page is orphaned

**Files:**
- Modify: `docs/funnel.html`
- Modify: `docs/blueprint.html`
- Modify: `docs/case-study.html`
- Modify: `docs/production.html`
- Modify: `docs/industries.html`
- Modify: `docs/self-improving.html`
- Modify: `docs/customize.html`
- Modify: `docs/workbook.html`
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/blueprint/public-links.test.js`

- [ ] **Step 1: Add a failing inbound-link graph test that excludes footer links.**

```js
test('advanced public pages each receive at least three contextual inbound links', () => {
  const targets = [
    'funnel.html',
    'blueprint.html',
    'case-study.html',
    'production.html',
    'industries.html',
    'self-improving.html',
    'customize.html',
    'workbook.html',
    'README.md',
    'THREADLIGHT.md',
  ];
  const counts = new Map(targets.map((target) => [target, 0]));
  for (const file of fs.readdirSync(path.join(repoRoot, 'docs')).filter((name) => name.endsWith('.html'))) {
    const html = read(`docs/${file}`).replace(/<footer[\s\S]*?<\/footer>/g, '');
    for (const target of targets.filter((target) => target.endsWith('.html'))) {
      const re = new RegExp(`href="(?:\\.\\/)?${target.replace('.', '\\.')}`, 'g');
      counts.set(target, counts.get(target) + (html.match(re) || []).length);
    }
  }
  for (const target of ['README.md', 'THREADLIGHT.md']) {
    for (const file of fs.readdirSync(path.join(repoRoot, 'docs')).filter((name) => name.endsWith('.html'))) {
      const html = read(`docs/${file}`).replace(/<footer[\s\S]*?<\/footer>/g, '');
      counts.set(target, counts.get(target) + ((html.match(new RegExp(target.replace('.', '\\.'), 'g')) || []).length));
    }
  }
  for (const [target, count] of counts) {
    assert.ok(count >= 3, `${target} needs at least 3 contextual inbound links, found ${count}`);
  }
});
```

- [ ] **Step 2: Run the Node link tests and capture the underlinked pages.**

Run: `node --test tests/blueprint/published-surfaces.test.js tests/blueprint/public-links.test.js`

Expected: **FAIL** on underlinked `industries.html`, `self-improving.html`, `workbook.html`, and/or `README.md`/`THREADLIGHT.md`.

- [ ] **Step 3: Add the contextual links in-body, not in the footer.**

```html
<!-- Funnel owner jump list -->
<section class="scene" id="scene-cta" aria-labelledby="cta-h" data-toc-id="scene-cta" data-toc-label="Next steps">
  <div class="next-step-grid">
    <a class="next-step-card" href="./blueprint.html">Blueprint — process to starter plan</a>
    <a class="next-step-card" href="./workbook.html">Workbook — hands-on path</a>
    <a class="next-step-card" href="./case-study.html">Case study — proof</a>
    <a class="next-step-card" href="./production.html">Production-ready — assurance and evidence</a>
    <a class="next-step-card" href="./industries.html">Industries — scenario catalogue</a>
    <a class="next-step-card" href="./self-improving.html">Self-improving — diagnostics and upgrade</a>
    <a class="next-step-card" href="https://github.com/aiappsgbb/threadlight-skills/blob/main/README.md">README</a>
    <a class="next-step-card" href="https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md">THREADLIGHT</a>
  </div>
</section>
```

```md
Add at least three contextual inbound links to each owner page:
- `docs/blueprint.html`: link back to `./funnel.html#scene-funnel`, forward to `./industries.html`, `https://github.com/aiappsgbb/threadlight-skills/blob/main/README.md`, and `https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md`.
- `docs/production.html`: link to `./funnel.html#scene-prod-ready`, `./self-improving.html#maintain`, `./customize.html`, and `./case-study.html#proof`.
- `docs/self-improving.html`: link to `./funnel.html#scene-customize`, `./production.html`, `https://github.com/aiappsgbb/threadlight-skills/blob/main/README.md`, `https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md`, and explicitly mention `threadlight-router-bench`.
```

- [ ] **Step 4: Re-run the Node link tests until the graph passes.**

Run: `node --test tests/blueprint/published-surfaces.test.js tests/blueprint/public-links.test.js`

Expected: **PASS** with every required target at `>= 3` inbound contextual links.

- [ ] **Step 5: Commit the discoverability link graph work.**

```bash
git add docs/funnel.html docs/blueprint.html docs/case-study.html docs/production.html \
  docs/industries.html docs/self-improving.html docs/customize.html docs/workbook.html \
  README.md THREADLIGHT.md tests/blueprint/published-surfaces.test.js tests/blueprint/public-links.test.js
git commit -m "docs: wire contextual discoverability links"
```

### Task 6: Update Self-improving, Industries, and page ownership surfaces to match the approved owner map

**Files:**
- Modify: `docs/self-improving.html`
- Modify: `docs/industries.html`
- Modify: `docs/workbook.html`
- Modify: `docs/funnel.html`
- Modify: `tests/playwright/tests/industries.spec.mjs`
- Modify: `tests/playwright/tests/how-it-works.spec.mjs`

- [ ] **Step 1: Add failing assertions for the owner-map surfaces that the spec requires.**

```js
test('owner pages expose the right ownership links and labels', async ({ page }) => {
  await page.goto('/self-improving.html');
  await expect(page.locator('#maintain')).toContainText(/threadlight-router-bench/i);
  await expect(page.locator('#maintain')).toContainText(/threadlight-upgrade/i);
  await expect(page.locator('main')).toContainText(/qualify|ROI|sizing/i);

  await page.goto('/industries.html');
  await expect(page.locator('header.masthead nav.nav a[aria-current="page"]')).toHaveAttribute('href', './funnel.html');
  await expect(page.locator('main')).toContainText(/catalogue/i);

  await page.goto('/workbook.html');
  await expect(page.locator('main')).toContainText(/hands-on path/i);
});
```

- [ ] **Step 2: Run the targeted Playwright tests and note the missing owner cues.**

Run: `cd tests/playwright && npx playwright test tests/industries.spec.mjs tests/how-it-works.spec.mjs --grep "owner pages expose|catalogue|hands-on path"`

Expected: **FAIL** on the current off-nav Industries assumption and missing explicit router-bench/qualify-ROI cues.

- [ ] **Step 3: Add the missing owner-map copy.**

```html
<!-- self-improving.html -->
<section id="maintain" data-toc-id="maintain" data-toc-label="Lifecycle maintenance">
  <h2><span class="n">04</span>Keeping a shipped pilot current: router-bench learnings + plan-only upgrades</h2>
  <p>
    <a href="https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-router-bench"><code>threadlight-router-bench</code></a>
    turns finished runs into grounded learnings and optional router scorecards. Pair it with
    <a href="https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-upgrade"><code>threadlight-upgrade</code></a>
    when the next question is lifecycle drift. If the next engagement starts earlier, jump back to
    <a href="./funnel.html#scene-prod-ready">How it works</a> and the <a href="https://github.com/aiappsgbb/threadlight-skills/blob/main/README.md">qualification / sizing / ROI surfaces</a>.
  </p>
</section>
```

```html
<!-- industries.html -->
<p class="breadcrumb"><a href="./funnel.html">How it works</a> / Industries catalogue</p>
<p class="lede">This page owns the public scenario catalogue: browse a domain, launch Blueprint with that starter plan, then return to <a href="./funnel.html#scene-industries">How it works</a> for the full lifecycle map.</p>
```

- [ ] **Step 4: Re-run the targeted Playwright tests until they pass.**

Run: `cd tests/playwright && npx playwright test tests/industries.spec.mjs tests/how-it-works.spec.mjs`

Expected: **PASS** with Industries treated as an owned catalogue surface and Self-improving explicitly linking router-bench and lifecycle entry cues.

- [ ] **Step 5: Commit the owner-map cleanup.**

```bash
git add docs/self-improving.html docs/industries.html docs/workbook.html docs/funnel.html \
  tests/playwright/tests/industries.spec.mjs tests/playwright/tests/how-it-works.spec.mjs
git commit -m "docs: align owner surfaces for discoverability"
```

### Task 7: Run the responsive, accessibility, and 9/10 evidence gate before opening a normal PR

**Files:**
- Modify as needed: touched docs/test files from Tasks 1-6
- Test: `tests/playwright/tests/site.spec.mjs`
- Test: `tests/playwright/tests/how-it-works.spec.mjs`
- Test: `tests/playwright/tests/blueprint.spec.mjs`
- Test: `tests/playwright/tests/industries.spec.mjs`
- Test: `tests/blueprint/published-surfaces.test.js`
- Test: `tests/blueprint/public-links.test.js`

- [ ] **Step 1: Add the final responsive/a11y assertions to `tests/playwright/tests/site.spec.mjs`.**

```js
async function assertNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => {
    const root = document.scrollingElement || document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
  expect(overflow).toBe(false);
}

test('How it works and shared nav hold at 1440, 1024, 390, and simulated 200% zoom', async ({ page }) => {
  for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    await page.goto('/funnel.html');
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto('/funnel.html');
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  await assertNoHorizontalOverflow(page);
});

test('touch targets stay at least 44px on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/funnel.html');
  const targets = page.locator('header.masthead nav.nav a, .btn, [data-mobile-nav-toggle]');
  const boxes = await targets.evaluateAll((els) =>
    els.map((el) => {
      const rect = el.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    }),
  );
  for (const box of boxes) {
    expect(box.width >= 44 || box.height >= 44).toBe(true);
  }
});
```

- [ ] **Step 2: Run the targeted responsive tests and store review screenshots as test output, not committed files.**

Run: `cd tests/playwright && THREADLIGHT_CAPTURE_REVIEW=1 npx playwright test tests/site.spec.mjs tests/how-it-works.spec.mjs --project=chromium-desktop --project=chromium-mobile`

Expected: **PASS** and screenshots saved under Playwright output, not added to git.

- [ ] **Step 3: Run the full validation suite.**

Run: `node --test tests/blueprint/*.test.js`

Expected: **PASS**.

Run: `cd tests/playwright && npx playwright test`

Expected: **PASS** across desktop/mobile/light/dark.

- [ ] **Step 4: Complete the 9/10 evidence checklist before opening the PR.**

```md
- [ ] Art direction: `funnel.html` still uses the existing cinematic sections, rails, chips, terminals, and chapter patterns — no new generic card system introduced.
- [ ] Typography: headings/body/mono usage match existing tokens; no one-off font stack added.
- [ ] Responsive: 1440, 1024, 390, and simulated 200% zoom have no horizontal overflow.
- [ ] Bespoke UX: the How it works page retains the current custom visual language instead of a low-fi redesign.
- [ ] Cross-page consistency: nav/current-state/breadcrumb behavior is the same on every chapter page.
- [ ] Advanced discoverability: every advanced page plus README + THREADLIGHT has at least 3 contextual inbound links (footer excluded) and no page is orphaned.
- [ ] Content consistency: the fixture-driven Node tests pass and all contradiction bans stay green.
- [ ] Full advanced-story clarity: the 6-phase/22-skill map, advanced-theme cards, and owner links are all visible and deep-linked from `funnel.html`.
```

- [ ] **Step 5: Open one ordinary PR from updated `main`; do not stack PRs.**

```bash
git status --short
git log --oneline --decorate -5
```

Expected: a clean or intentionally staged branch with only PR2 changes. Open **one normal PR from the updated `main` base**. Do **not** create a dependent-open PR, stacked PR, or any PR that still relies on an unmerged PR1 branch.
