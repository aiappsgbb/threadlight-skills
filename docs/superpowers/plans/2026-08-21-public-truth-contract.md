# Public Truth Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align every public Threadlight truth surface to the approved governed-working-pilot contract without changing the homepage demo, reel, hero, assets, visuals, geometry, or interaction.

**Architecture:** Put one small CommonJS fixture in `tests/blueprint/fixtures/public-contract.js` and make Node + Playwright assertions read from it instead of scattering copy rules through tests. Apply the contract surface-by-surface: freeze the Home boundary first, then update the contradiction pages (`blueprint`, `industries`, `funnel`, `workbook`, `case-study`, `production`) plus `README.md` and `THREADLIGHT.md`, and finally run the same regression suite the repo already uses.

**Tech Stack:** Static HTML in `docs/`, CommonJS `node --test` tests in `tests/blueprint/`, Playwright + axe in `tests/playwright/`, existing `python3 -m http.server` static serving from `tests/playwright/playwright.config.mjs`.

---

## File structure

- Create: `tests/blueprint/fixtures/public-contract.js` — single source for canonical public truths, six-phase map, page ownership, banned phrases, skill counts, automation boundaries, and the immutable-home selectors/assets list.
- Modify: `tests/blueprint/published-surfaces.test.js` — fixture schema checks, contradiction bans, metadata parity, immutable-home assertions, cost/remediation copy assertions.
- Modify: `tests/blueprint/public-links.test.js` — keep local-link integrity green while stale `overview.html` and orphan fragment references are removed.
- Modify: `tests/playwright/tests/site.spec.mjs` — homepage immutability guard, shared-nav freeze for PR1, funnel/home/value-contract regressions.
- Modify: `tests/playwright/tests/blueprint.spec.mjs` — Blueprint title/H1/result language becomes starter-lifecycle language, not exact whole-build language.
- Modify: `tests/playwright/tests/industries.spec.mjs` — Industries title/H1/body semantics become starter-plan language, not exact-sequence language.
- Modify: `tests/playwright/tests/case-study.spec.mjs` — case study stays proof of a governed working pilot and evidence-backed path, not a production-complete claim.
- Modify: `tests/playwright/tests/gap-closure.spec.mjs` — production/connect/ground/load surfaces keep their link coverage while the cost/remediation boundaries become explicit.
- Modify: `README.md` — opening contract, skill table rows, lifecycle summary, and any public value language that must stay in parity with the fixture.
- Modify: `THREADLIGHT.md` — opening contract, entry-skill picker language, lifecycle summary, and any public value language that must stay in parity with the fixture.
- Modify: `docs/blueprint.html` — metadata, hero/result copy, and any “exact build / whole build / you run nothing” language.
- Modify: `docs/industries.html` — metadata, hero/library copy, and any “same exact sequence / copy-paste whole build” language.
- Modify: `docs/funnel.html` — copy-only corrections in PR1; no visual or structural redesign yet.
- Modify: `docs/workbook.html` — workbook framing becomes hands-on starter path; remove full-auto/end-to-end/governed-production overclaims.
- Modify: `docs/case-study.html` — keep historical proof and pilot framing while avoiding production-final wording.
- Modify: `docs/production.html` — actuals/reconciliation wording, `cost-manifest.json` KPI wording, and “remediation never guarantees green” language.
- Read/test only: `docs/index.html`, `docs/assets/demo-reel.js`, `docs/assets/audio/*.mp3`, `docs/assets/shots/*.png` — immutable in PR1.

### Task 1: Create the canonical public-contract fixture

**Files:**
- Create: `tests/blueprint/fixtures/public-contract.js`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Test: `tests/blueprint/published-surfaces.test.js`

- [ ] **Step 1: Write the failing fixture-shape test in `tests/blueprint/published-surfaces.test.js`.**

```js
const PUBLIC_CONTRACT = require('./fixtures/public-contract');

test('public contract fixture exports the approved public shape', () => {
  assert.strictEqual(PUBLIC_CONTRACT.skillCounts.pipeline, 21);
  assert.strictEqual(PUBLIC_CONTRACT.skillCounts.total, 22);
  assert.strictEqual(PUBLIC_CONTRACT.skillCounts.planner, 'threadlight-auto');
  assert.deepStrictEqual(
    PUBLIC_CONTRACT.sixPhaseMap.map((phase) => phase.id),
    ['enter', 'build', 'integrate', 'assure', 'ship', 'improve'],
  );
  assert.deepStrictEqual(
    Object.keys(PUBLIC_CONTRACT.pageOwnership),
    ['home', 'funnel', 'blueprint', 'caseStudy', 'production', 'industries', 'selfImproving', 'customize', 'workbook', 'readme', 'threadlight'],
  );
  assert.ok(PUBLIC_CONTRACT.homeImmutability.file.endsWith('docs/index.html'));
  assert.ok(PUBLIC_CONTRACT.homeImmutability.assetPaths.includes('docs/assets/demo-reel.js'));
});
```

- [ ] **Step 2: Run the Node test to verify it fails because the fixture does not exist yet.**

Run: `node --test tests/blueprint/published-surfaces.test.js`

Expected: **FAIL** with `Cannot find module './fixtures/public-contract'`.

- [ ] **Step 3: Create `tests/blueprint/fixtures/public-contract.js` with the approved public contract.**

```js
module.exports = {
  skillCounts: {
    pipeline: 21,
    total: 22,
    planner: 'threadlight-auto',
  },
  canonicalTruths: {
    pilot: 'A business process becomes a governed working pilot with an evidence-backed path to production.',
    auto: 'threadlight-auto is an agent-guided lifecycle planner; it chooses the next stage and hands execution to the coding agent.',
    blueprint: 'Blueprint emits a deterministic starter lifecycle, starter skill list, and starter evidence plan; it never promises the exact whole execution.',
    production: 'Manual, live, cost-bearing, and customer-environment legs stay explicit; production readiness, settled Azure actuals, and customization each keep their own timelines.',
    cost: 'Actual cost claims require forecast + settled Azure actuals + scope-bound reconciliation before cost per successful interaction is verified.',
    remediation: 'Running a remediation skill can improve the evidence state, but no single remediation run guarantees a green score.',
  },
  sixPhaseMap: [
    { id: 'enter', label: 'Enter', skills: ['threadlight-qualify', 'threadlight-design', 'threadlight-demo-data-factory'] },
    { id: 'build', label: 'Build', skills: ['threadlight-local-test', 'threadlight-deploy', 'threadlight-workspace-ui'] },
    { id: 'integrate', label: 'Integrate', skills: ['threadlight-connect', 'threadlight-ground', 'threadlight-hitl-patterns', 'threadlight-event-triggers'] },
    { id: 'assure', label: 'Assure', skills: ['threadlight-safe-check', 'threadlight-consumption-iq', 'threadlight-evals', 'threadlight-redteam', 'threadlight-govern', 'threadlight-loadtest'] },
    { id: 'ship', label: 'Ship', skills: ['threadlight-production-ready', 'threadlight-cicd', 'threadlight-customize'] },
    { id: 'improve', label: 'Improve', skills: ['threadlight-router-bench', 'threadlight-upgrade'] },
  ],
  pageOwnership: {
    home: 'Demo-led landing page; demo, reel, hero, copy, visuals, behavior, assets, and interaction stay immutable.',
    funnel: 'Complete public approach and canonical How it works page after PR2.',
    blueprint: 'Process-to-starter-plan generator.',
    caseStudy: 'Proof that a governed working pilot and evidence-backed path happened.',
    production: 'Assurance, architecture, evidence, and readiness boundaries.',
    industries: 'Industry catalogue and scenario launcher.',
    selfImproving: 'Diagnostics, router-bench learnings, and upgrade lifecycle.',
    customize: 'Customer-production onboarding boundary.',
    workbook: 'Hands-on rehearsal path.',
    readme: 'Human-readable exhaustive technical reference.',
    threadlight: 'LLM-facing exhaustive technical reference.',
  },
  bannedPhrases: {
    plannerAsWorker: ['full-auto orchestrator', 'runs all three stages end-to-end', 'threadlight-auto prompt runs all three stages end-to-end'],
    blueprintAsExactBuild: ['exact build prompt', 'drives the whole build', 'you run nothing'],
    pilotAsProduction: ['governed production', 'idea → governed production', 'brief to a deployed, production-ready Foundry agent'],
    costWithoutReconciliation: ['cost-manifest.json at 800 rpm', 'actual cost proven from forecast only'],
    remediationGuaranteesGreen: ['amber turns green', 'guarantees green', 'single remediation run makes the score green'],
  },
  automationBoundaries: {
    qualify: 'Declared interview only; no Azure, no deployment, no live runtime.',
    connect: 'Manual hand-off; mock-to-real tool swap requires explicit evidence and approval.',
    ground: 'Manual evidence review; never runs a live probe itself.',
    loadtest: 'Manual, live, and cost-bearing; budget-capped and production-confirmed.',
    cicd: 'Human-led production pipeline handoff; not an auto-run stage.',
    customize: 'Human-led fork/onboarding runbook; not an auto-run stage.',
    upgrade: 'Plan-only; never edits the project.',
    routerBench: 'Offline improve leg; reads finished runs, does not operate the live pilot.',
    productionReady: 'Advisory scorecard and evidence gate, not certification.',
  },
  homeImmutability: {
    file: 'docs/index.html',
    immutableSelectors: ['#demo-h', '.demo-intro', '#how-it-works', '#value-evidence', '#reel', '.beat-rail', '[data-reel="cover"]', '[data-reel="start"]', 'section.recap'],
    assetPaths: [
      'docs/assets/demo-reel.js',
      'docs/assets/audio/intro.mp3',
      'docs/assets/audio/beat-1.mp3',
      'docs/assets/audio/beat-2.mp3',
      'docs/assets/audio/beat-3.mp3',
      'docs/assets/audio/beat-4.mp3',
      'docs/assets/audio/beat-5.mp3',
      'docs/assets/audio/beat-6.mp3',
      'docs/assets/audio/beat-7.mp3',
      'docs/assets/shots/deploy-workspace.png',
      'docs/assets/shots/deploy-teams.png',
      'docs/assets/shots/sales-kit-deck.png',
      'docs/assets/shots/sales-kit-prep-guide.png',
    ],
    allowedPr2HomeDiff: [
      'header.masthead nav.nav a[href="./funnel.html"]',
      'header.masthead nav.nav a[aria-current="page"]',
      'header.masthead [data-mobile-nav-toggle]',
    ],
  },
};
```

- [ ] **Step 4: Re-run the Node test and make sure the fixture contract passes.**

Run: `node --test tests/blueprint/published-surfaces.test.js`

Expected: **PASS** for `public contract fixture exports the approved public shape`.

- [ ] **Step 5: Commit the fixture baseline.**

```bash
git add tests/blueprint/fixtures/public-contract.js tests/blueprint/published-surfaces.test.js
git commit -m "test: add public truth contract fixture"
```

### Task 2: Freeze the immutable Home boundary before touching copy elsewhere

**Files:**
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/playwright/tests/site.spec.mjs`
- Read/test only: `docs/index.html`, `docs/assets/demo-reel.js`, `docs/assets/audio/*.mp3`, `docs/assets/shots/*.png`

- [ ] **Step 1: Add a failing Node + Playwright guard for PR1 Home immutability.**

```js
test('PR1 keeps the Home demo boundary immutable', () => {
  const index = read('docs/index.html');
  const selectorTokens = new Map([
    ['#demo-h', 'id="demo-h"'],
    ['.demo-intro', 'class="demo-intro"'],
    ['#how-it-works', 'id="how-it-works"'],
    ['#value-evidence', 'id="value-evidence"'],
    ['#reel', 'id="reel"'],
    ['.beat-rail', 'class="beat-rail"'],
    ['[data-reel="cover"]', 'data-reel="cover"'],
    ['[data-reel="start"]', 'data-reel="start"'],
    ['section.recap', 'class="recap"'],
  ]);
  for (const selector of PUBLIC_CONTRACT.homeImmutability.immutableSelectors) {
    assert.ok(index.includes(selectorTokens.get(selector)), `index missing immutable selector token ${selector}`);
  }
  for (const asset of PUBLIC_CONTRACT.homeImmutability.assetPaths) {
    const rel = asset.replace(/^docs\//, '');
    assert.ok(index.includes(rel) || fs.existsSync(path.join(repoRoot, asset)), `missing immutable home asset ${asset}`);
  }
});
```

```js
test('PR1 keeps Home nav at five chapter links and leaves the reel interaction untouched', async ({ page }) => {
  await page.goto('/index.html');
  await expect(page.locator('header.masthead nav.nav a')).toHaveCount(5);
  const reel = page.locator('#reel');
  await expect(reel.locator('.beat-rail .beat-chip')).toHaveCount(7);
  await reel.locator('[data-reel="start"]').click();
  await expect(reel).toHaveClass(/is-started/);
});
```

- [ ] **Step 2: Run the targeted tests and verify the new guard is wired correctly.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/site.spec.mjs --grep "Home nav at five chapter links|reel interaction"`

Expected: **FAIL** before the new assertions exist, then **PASS** once both test blocks are added.

- [ ] **Step 3: Record the scoped diff allowlist in the plan implementation notes and use it at every PR1 validation checkpoint.**

```bash
git --no-pager diff -- \
  docs/index.html \
  docs/assets/demo-reel.js \
  docs/assets/audio/intro.mp3 \
  docs/assets/audio/beat-1.mp3 \
  docs/assets/audio/beat-2.mp3 \
  docs/assets/audio/beat-3.mp3 \
  docs/assets/audio/beat-4.mp3 \
  docs/assets/audio/beat-5.mp3 \
  docs/assets/audio/beat-6.mp3 \
  docs/assets/audio/beat-7.mp3 \
  docs/assets/shots/deploy-workspace.png \
  docs/assets/shots/deploy-teams.png \
  docs/assets/shots/sales-kit-deck.png \
  docs/assets/shots/sales-kit-prep-guide.png
```

Expected: **No output in PR1.** If anything prints, stop and revert that unintended Home/demo change before continuing.

- [ ] **Step 4: Commit the immutable-home guard before editing any contradiction page.**

```bash
git add tests/blueprint/published-surfaces.test.js tests/playwright/tests/site.spec.mjs
git commit -m "test: freeze homepage demo boundary"
```

### Task 3: Fix Blueprint + Industries + root-doc contradictions

**Files:**
- Modify: `docs/blueprint.html`
- Modify: `docs/industries.html`
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/playwright/tests/blueprint.spec.mjs`
- Modify: `tests/playwright/tests/industries.spec.mjs`

- [ ] **Step 1: Add failing assertions for starter-lifecycle language and root-doc parity.**

```js
test('Blueprint and Industries stay on the starter-plan contract', () => {
  const blueprint = read('docs/blueprint.html');
  const industries = read('docs/industries.html');
  assert.match(blueprint, /deterministic starter lifecycle/i);
  assert.match(blueprint, /starter skill list/i);
  assert.match(blueprint, /starter evidence plan/i);
  assert.doesNotMatch(blueprint, /exact build prompt|drives the whole build|you run nothing/i);
  assert.match(industries, /starter lifecycle/i);
  assert.match(industries, /starter skill\/evidence plan/i);
  assert.doesNotMatch(industries, /same exact sequence|copy-paste prompt does the whole build/i);
  for (const surface of [read('README.md'), read('THREADLIGHT.md')]) {
    assert.match(surface, /agent-guided lifecycle planner/i);
    assert.match(surface, /evidence-backed path to production/i);
    assert.match(surface, /settled Azure actuals/i);
  }
});
```

```js
await expect(page.getByRole('heading', { level: 1 })).toContainText(/starter lifecycle/i);
await expect(page.locator('#bp-prompt')).toContainText(/threadlight-auto plans the next stage/i);
await expect(page.locator('#bp-auto')).toContainText(/manual evidence legs and later-pilot activities stay explicit/i);
await expect(page.getByRole('heading', { level: 1 })).toContainText(/starter plan/i);
```

- [ ] **Step 2: Run the targeted tests to see the current contradictions fail.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/blueprint.spec.mjs tests/industries.spec.mjs`

Expected: **FAIL** on the old “build prompt / whole build / you run nothing / off-nav starter-sequence” wording.

- [ ] **Step 3: Update `docs/blueprint.html`, `docs/industries.html`, `README.md`, and `THREADLIGHT.md` to the approved wording.**

```html
<title>Blueprint — deterministic starter lifecycle, starter skill list, starter evidence plan.</title>
<meta name="description" content="Blueprint turns a declared process into a deterministic starter lifecycle, starter skill list, and starter evidence plan. It does not claim the exact whole execution.">
...
<p class="lede">
  Blueprint reads the declared process, then emits a <strong>deterministic starter lifecycle</strong>,
  a <strong>starter skill list</strong>, and a <strong>starter evidence plan</strong>. It does not
  promise the exact whole build; manual evidence legs and later-pilot activities stay explicit.
</p>
...
<h4>What Copilot does — plans the next stage, then hands execution back to you</h4>
```

```html
<title>Industries — starter lifecycles and starter evidence plans by domain.</title>
<meta name="description" content="Pick a domain scenario, launch Blueprint with that scenario preselected, and start from a domain-specific starter lifecycle plus starter evidence plan. Production delivery stays explicit.">
...
<p class="lede">
  Each scenario launches Blueprint with a domain-specific <strong>starter lifecycle</strong> and
  <strong>starter skill/evidence plan</strong>. It is a starting point, not a claim that every
  engagement runs the same exact sequence.
</p>
```

```md
> **A business process becomes a governed working pilot with an evidence-backed path to production.**
>
> Twenty-one pipeline skills plus one agent-guided lifecycle planner (22 total) take a brief into a governed working pilot. `threadlight-auto` chooses the next stage and hands execution to the coding agent; manual, live, cost-bearing, and plan-only legs stay explicit.
```

- [ ] **Step 4: Re-run the targeted Node + Playwright tests until they pass.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/blueprint.spec.mjs tests/industries.spec.mjs`

Expected: **PASS** with Blueprint/Industries/root-doc wording now matching the fixture.

- [ ] **Step 5: Commit the starter-plan contract changes.**

```bash
git add docs/blueprint.html docs/industries.html README.md THREADLIGHT.md \
  tests/blueprint/published-surfaces.test.js \
  tests/playwright/tests/blueprint.spec.mjs tests/playwright/tests/industries.spec.mjs
git commit -m "docs: align starter-plan contract"
```

### Task 4: Fix Funnel + Workbook + Case study without changing Home

**Files:**
- Modify: `docs/funnel.html`
- Modify: `docs/workbook.html`
- Modify: `docs/case-study.html`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/playwright/tests/site.spec.mjs`
- Modify: `tests/playwright/tests/how-it-works.spec.mjs`
- Modify: `tests/playwright/tests/case-study.spec.mjs`

- [ ] **Step 1: Add failing assertions for Auto/planner wording, starter-lifecycle wording, and pilot-vs-production framing.**

```js
test('Funnel, Workbook, and Case study stay on the approved public contract', () => {
  const funnel = read('docs/funnel.html');
  const workbook = read('docs/workbook.html');
  const caseStudy = read('docs/case-study.html');
  assert.doesNotMatch(funnel, /full-auto orchestrator|one guided session to a deployed pilot/i);
  assert.match(funnel, /agent-guided lifecycle planner/i);
  assert.match(funnel, /manual, live, and cost-bearing legs stay explicit/i);
  assert.doesNotMatch(workbook, /governed production|runs all three stages end-to-end/i);
  assert.match(workbook, /hands-on starter path/i);
  assert.match(caseStudy, /governed working pilot/i);
  assert.match(caseStudy, /evidence-backed path to production/i);
  assert.doesNotMatch(caseStudy, /nothing mocked|production-complete/i);
});
```

```js
await expect(page.locator('#scene-hero .hero-sub')).toContainText(/agent-guided lifecycle planner/i);
await expect(page.locator('#scene-hero .hero-sub')).toContainText(/manual, live, and cost-bearing legs stay explicit/i);
await expect(page.locator('#how-it-works')).not.toContainText(/How it works/i); // PR1 keeps Home unchanged.
await expect(page.locator('.tx')).not.toContainText(/runs all three stages end-to-end/i);
await expect(page.locator('#verdict')).toContainText(/governed working pilot/i);
```

- [ ] **Step 2: Run the targeted tests and capture the current failures.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/site.spec.mjs tests/how-it-works.spec.mjs tests/case-study.spec.mjs --grep "approved public contract|agent-guided lifecycle planner|hands-on starter path|governed working pilot"`

Expected: **FAIL** on the old funnel/workbook overclaims.

- [ ] **Step 3: Replace the overclaim copy in `docs/funnel.html`, `docs/workbook.html`, and `docs/case-study.html`.**

```html
<p class="hero-sub">
  Describe a business process to a coding agent. It drafts the spec, scaffolds the
  starter code, validates it locally, and shows the <strong>evidence-backed path to production</strong>.
  <strong>threadlight-auto</strong> is the <strong>agent-guided lifecycle planner</strong>; it picks the next stage,
  but manual, live, and cost-bearing legs stay explicit.
</p>
```

```html
<title>Workbook — hands-on starter path from one paragraph to a governed working pilot.</title>
<meta name="description" content="A self-paced workbook that rehearses the starter lifecycle from one paragraph to a governed working pilot, then points to the explicit hand-offs required for production work.">
...
<span class="tx">Rather watch than drive? <b>One <code>threadlight-auto</code> prompt plans the next stage</b> and hands execution back to the coding agent. Manual evidence legs still need an explicit human hand-off.</span>
```

```html
<meta name="description" content="A real captured Threadlight run: a one-paragraph credit-memo brief becomes a live MVP and a governed working pilot on Azure, with an evidence-backed path to production. Historical proof, not a claim that every production hand-off was already complete.">
...
<p class="cap">Idea to governed working pilot — with an evidence-backed path to production.</p>
```

- [ ] **Step 4: Re-run the targeted Playwright + Node tests until they pass, then re-run the Home diff allowlist.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/site.spec.mjs tests/how-it-works.spec.mjs tests/case-study.spec.mjs`

Expected: **PASS**; then run the `git --no-pager diff -- ...` command from Task 2 and confirm it still prints nothing.

- [ ] **Step 5: Commit the planner/pilot framing fixes.**

```bash
git add docs/funnel.html docs/workbook.html docs/case-study.html \
  tests/blueprint/published-surfaces.test.js \
  tests/playwright/tests/site.spec.mjs tests/playwright/tests/how-it-works.spec.mjs tests/playwright/tests/case-study.spec.mjs
git commit -m "docs: align planner and pilot framing"
```

### Task 5: Fix Production cost/reconciliation wording and remediation boundaries

**Files:**
- Modify: `docs/production.html`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `tests/playwright/tests/gap-closure.spec.mjs`
- Modify: `tests/playwright/tests/site.spec.mjs`

- [ ] **Step 1: Add failing assertions for forecast/actuals/reconciliation and “no guaranteed green” wording.**

```js
test('Production page keeps cost and remediation boundaries explicit', () => {
  const production = read('docs/production.html');
  assert.match(production, /settled Azure actuals/i);
  assert.match(production, /Reconciliation binds the two/i);
  assert.match(production, /not-verified/i);
  assert.match(production, /no single remediation run guarantees a green score/i);
  assert.doesNotMatch(production, /amber turns green/i);
  assert.doesNotMatch(production, /cost-manifest\.json at 800 rpm/i);
});
```

```js
await expect(page.locator('#proof')).toContainText(/settled Azure actuals/i);
await expect(page.locator('#proof')).toContainText(/Reconciliation binds the two/i);
await expect(page.locator('#proof')).toContainText(/not-verified/i);
await expect(page.locator('#legs')).toContainText(/no single remediation run guarantees a green score/i);
```

- [ ] **Step 2: Run the targeted tests and verify the current page still fails on the old KPI wording.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/gap-closure.spec.mjs tests/site.spec.mjs --grep "cost and remediation boundaries|settled Azure actuals|guarantees a green score"`

Expected: **FAIL** on the old `cost-manifest.json` shorthand and the implied amber→green claim.

- [ ] **Step 3: Replace the ambiguous production wording with the approved public contract.**

```html
<span class="os-cap">Forecast from <code>cost-manifest.json</code>; actual cost waits for settled Azure actuals and reconciliation.</span>
...
<p class="section-lede" style="margin-top:12px;">
  Forecast before deployment tells you what to budget. Later-pilot, read-only <strong>Azure actuals</strong>
  tell you what settled in the target subscription and resource group. <strong>Reconciliation</strong> binds
  the two into a measured cost per successful interaction for that settled window; if scope is missing or the
  numbers do not line up, the scorecard keeps the claim <em>not-verified</em>.
</p>
...
<p>No single remediation run guarantees a green score. Each rerun only updates the evidence state for the pillar it actually closes.</p>
```

- [ ] **Step 4: Re-run the targeted tests until they pass.**

Run: `node --test tests/blueprint/published-surfaces.test.js && cd tests/playwright && npx playwright test tests/gap-closure.spec.mjs tests/site.spec.mjs`

Expected: **PASS** with the tightened cost and remediation wording.

- [ ] **Step 5: Commit the production-boundary copy update.**

```bash
git add docs/production.html tests/blueprint/published-surfaces.test.js \
  tests/playwright/tests/gap-closure.spec.mjs tests/playwright/tests/site.spec.mjs
git commit -m "docs: tighten production evidence boundaries"
```

### Task 6: Final metadata, public-safe validation, rubric evidence, and normal PR handoff

**Files:**
- Modify as needed: touched docs/test files from Tasks 1-5
- Test: `tests/blueprint/published-surfaces.test.js`
- Test: `tests/blueprint/public-links.test.js`
- Test: `tests/playwright/tests/site.spec.mjs`
- Test: `tests/playwright/tests/blueprint.spec.mjs`
- Test: `tests/playwright/tests/industries.spec.mjs`
- Test: `tests/playwright/tests/case-study.spec.mjs`
- Test: `tests/playwright/tests/gap-closure.spec.mjs`

- [ ] **Step 1: Add the final metadata/public-safe assertions that block regressions across all PR1 surfaces.**

```js
test('PR1 metadata stays in parity across touched public surfaces', () => {
  for (const file of ['docs/blueprint.html', 'docs/industries.html', 'docs/funnel.html', 'docs/workbook.html', 'docs/case-study.html', 'docs/production.html']) {
    const html = read(file);
    assert.match(html, /<title>.*<\/title>/);
    assert.match(html, /<meta name="description"/);
    assert.match(html, /<meta property="og:description"/);
    assert.match(html, /<meta name="twitter:description"/);
    assert.doesNotMatch(html, /internal use|Microsoft only|confidential/i);
  }
});
```

- [ ] **Step 2: Run the targeted Node tests, then the full blueprint Node suite.**

Run: `node --test tests/blueprint/published-surfaces.test.js tests/blueprint/public-links.test.js`

Expected: **PASS**.

Run: `node --test tests/blueprint/*.test.js`

Expected: **PASS** across the full Node doc suite.

- [ ] **Step 3: Run the targeted Playwright suite, then the full Playwright suite from `tests/playwright/`.**

Run: `cd tests/playwright && npx playwright test tests/site.spec.mjs tests/blueprint.spec.mjs tests/industries.spec.mjs tests/case-study.spec.mjs tests/gap-closure.spec.mjs`

Expected: **PASS**.

Run: `cd tests/playwright && npx playwright test`

Expected: **PASS** across desktop, mobile, dark, and light projects.

- [ ] **Step 4: Re-run the Home diff allowlist and complete the PR1 evidence checklist.**

```md
- [ ] `git --no-pager diff -- docs/index.html docs/assets/demo-reel.js docs/assets/audio/* docs/assets/shots/*` is empty.
- [ ] `node --test tests/blueprint/*.test.js` is green.
- [ ] `cd tests/playwright && npx playwright test` is green.
- [ ] No touched page contains banned phrases, internal-only terms, or stacked-PR language.
- [ ] README and THREADLIGHT both state the governed working pilot contract, the planner boundary, and the forecast → actuals → reconciliation → cost-per-successful-interaction arc.
- [ ] Evidence for the 9/10 content-consistency dimension is attached in the PR description: before/after copy diffs, the passing test commands, and the empty Home diff output.
```

- [ ] **Step 5: Commit the final cleanup if any metadata/test adjustments were needed, then open a normal PR from a branch cut from `main`.**

```bash
git add README.md THREADLIGHT.md docs/blueprint.html docs/industries.html docs/funnel.html \
  docs/workbook.html docs/case-study.html docs/production.html \
  tests/blueprint/published-surfaces.test.js tests/blueprint/public-links.test.js \
  tests/playwright/tests/site.spec.mjs tests/playwright/tests/blueprint.spec.mjs \
  tests/playwright/tests/industries.spec.mjs tests/playwright/tests/case-study.spec.mjs \
  tests/playwright/tests/gap-closure.spec.mjs
git commit -m "test: lock public truth contract metadata" || true
git status --short
```

Expected: either no new changes remain, or one final metadata/test commit exists on the PR1 branch. Open **one ordinary PR from `main`**. After it merges, discard the branch. **Do not stack PR2 on this open branch.**
