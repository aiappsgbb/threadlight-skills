# Product Narrative and Pages Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the public repository narrative and GitHub Pages with the value, lifecycle, and cost evidence the current code actually provides.

**Architecture:** Treat public wording as a tested contract. Extend the existing Node and Playwright suites before changing Markdown or HTML, keep the current visual system, and add only one small shared JavaScript behavior for mobile navigation.

**Tech Stack:** Markdown, static HTML, vanilla JavaScript, CSS, Node `node:test`, Playwright, Python stdlib cache-bust tooling, GitHub Actions.

---

## File map

**Root truth surfaces**

- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `CHANGELOG.md`
- Modify: `examples/returns-triage-governed/README.md`
- Modify: `skills/threadlight-consumption-iq/SKILL.md`
- Modify: `skills/threadlight-production-ready/SKILL.md`
- Modify: `skills/threadlight-production-ready/references/report-template.md`
- Modify: `skills/threadlight-production-ready/references/handoff-checklist.md`

**Pages truth surfaces**

- Modify: `docs/index.html`
- Modify: `docs/production.html`
- Modify: `docs/case-study.html`
- Modify: `docs/self-improving.html`
- Modify: `docs/industries.html`
- Modify: `docs/funnel.html`

**Shared Pages behavior**

- Modify: `docs/assets/site.js`
- Modify: `docs/assets/site.css`

**Public-contract tests and CI**

- Modify: `tests/blueprint/published-surfaces.test.js`
- Create: `tests/blueprint/public-links.test.js`
- Modify: `tests/playwright/tests/site.spec.mjs`
- Modify: `.github/workflows/docs-blueprint.yml`
- Verify: `.github/workflows/pages-cache-bust.yml`

Do not change `docs/assets/process-library.json` or Blueprint derivation logic unless
an existing deterministic test proves generated-data drift.

### Task 1: Lock the root narrative contract

**Files:**
- Modify: `tests/blueprint/published-surfaces.test.js:71-128`
- Modify: `README.md:1-93`
- Modify: `THREADLIGHT.md:1-18`
- Modify: `THREADLIGHT.md:727-765`
- Modify: `CHANGELOG.md:1-12`

- [ ] **Step 1: Add the failing root-narrative test**

Append this test to `tests/blueprint/published-surfaces.test.js`:

```javascript
test('root docs describe a governed pilot, explicit value evidence, and Auto as a planner', () => {
  const readme = read('README.md');
  const briefing = read('THREADLIGHT.md');

  for (const text of [readme, briefing]) {
    assert.match(text, /governed working pilot/i);
    assert.match(text, /evidence-backed path to production/i);
    assert.match(text, /SPEC (?:§|section) 14/i);
    assert.match(text, /settled Azure actuals/i);
    assert.match(text, /cost per successful interaction/i);
    assert.doesNotMatch(text, /production-ready[^.\n]*in a single working session/i);
  }

  assert.match(readme, /agent-guided lifecycle planner/i);
  assert.match(briefing, /planner does not execute stages/i);
  assert.doesNotMatch(readme, /NEW v0\.1\.0-alpha/);
  assert.doesNotMatch(readme, /threadlight-production-ready[^|\n]*v0\.3\.0/i);
  assert.doesNotMatch(readme, /React workspace/i);
  assert.doesNotMatch(briefing, /overview\.html|threadlight-experience\.html/);
});
```

- [ ] **Step 2: Run the test and confirm the current copy fails**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: FAIL in
`root docs describe a governed pilot, explicit value evidence, and Auto as a planner`.

- [ ] **Step 3: Replace the README promise and add the canonical lifecycle matrix**

Replace the opening block in `README.md` with:

```markdown
# Threadlight — Pilot Pipeline Skills

> **Turn a business process into a governed working pilot, with an
> evidence-backed path to production.**
>
> Twenty-one pipeline skills plus one agent-guided lifecycle planner help a
> delivery team qualify, design, deploy, evaluate, govern, and measure a
> Microsoft Foundry hosted agent in the customer's tenant. A working session
> produces a pilot and auditable evidence; production certification, settled
> Azure actuals, and customer-environment onboarding happen on their own
> evidence timelines.
```

Replace the `threadlight-consumption-iq` row with current wording that includes:

```markdown
| [`threadlight-consumption-iq`](skills/threadlight-consumption-iq/) | Projects Azure cost before deployment, collects read-only Azure actuals for a settled window, reconciles forecast to actuals, and emits measured cost per successful interaction when SPEC § 14 evidence is mature. Forecasting is available early; actuals and reconciliation are later-pilot activities. |
```

Replace the `threadlight-production-ready` row with:

```markdown
| [`threadlight-production-ready`](skills/threadlight-production-ready/) | Produces an advisory production-readiness scorecard and handoff package. It verifies available lifecycle evidence, including reconciled Azure actuals, but does not certify production on its own. Remediation and deployment actions are explicit, separate choices. |
```

Replace the `threadlight-auto` row with:

```markdown
| [`threadlight-auto`](skills/threadlight-auto/) | **Agent-guided lifecycle planner.** Reads workspace evidence, decides which automatic stage should run next, and resumes around completed work. `orchestrator.py` plans; the coding agent executes the selected skills. Manual, live, cost-bearing, and plan-only legs remain explicit handoffs. |
```

Replace the workspace row with:

```markdown
| [`threadlight-workspace-ui`](skills/threadlight-workspace-ui/) | Framework-agnostic vanilla HTML/JavaScript reference patterns for operator workspaces, including case lists, decision panes, audit views, and explicit action boundaries. |
```

Insert this table immediately before `## Pipeline flow`:

```markdown
## Canonical lifecycle

| Stage class | What runs | Evidence boundary |
|---|---|---|
| No-repo entry | `threadlight-qualify` | Declared sizing and ROI inputs; no Azure access |
| Agent-guided pilot path | design, optional local test, deploy, safe-check, forecast, invoke, evals, red-team, govern | `threadlight-auto` plans the next stage; the coding agent executes it |
| Manual live evidence | connect, ground, load-test | Requires customer endpoints, identities, permissions, or budget |
| Optional handoff | production-ready, CI/CD, customize | Advisory assessment and customer-environment delivery |
| Later-pilot evidence | settled Azure actuals and reconciliation | Forecast → actuals → variance → cost per successful interaction |
| Offline improvement | router-bench and upgrade | Diagnostics-to-backlog and plan-only compatibility review |
```

- [ ] **Step 4: Align the technical briefing with the same truth**

Replace `THREADLIGHT.md:9-18` with:

```markdown
Threadlight is a **library of twenty-two `threadlight-*` skills**: twenty-one
pipeline skills plus the optional `threadlight-auto` agent-guided lifecycle
planner. Together they take a business process to a governed working pilot and
an evidence-backed path to production. A working session can produce a deployed,
evaluated, and observable pilot; production certification, customer-environment
onboarding, and settled Azure actuals require their own evidence and timing.

SPEC section 14 is the value contract: baseline, target, owner, timeframe,
measurement source, and maturity policy. Once a pilot has a settled window, the
cost path joins forecast → settled Azure actuals → reconciliation → cost per
successful interaction.
```

Replace the Auto appendix opening at `THREADLIGHT.md:727-743` with:

```markdown
## Appendix A — `threadlight-auto` (the lifecycle planner)

The canonical stages can be invoked individually when a delivery team wants
stage-by-stage control. `threadlight-auto` is a separate optional planner:

```text
freeform outcome
  → orchestrator.py reads evidence and chooses the next stage
  → the coding agent invokes that stage's skill
  → new evidence is read before the next decision
```

The planner does not execute stages. With `--commit`, `orchestrator.py` writes
`.threadlight/auto-next.json`; the agent-owned run updates
`.threadlight/auto-state.json`.
```

Keep the existing manual-handoff list, but remove any sentence saying the Python
orchestrator itself runs or persists the lifecycle.

Replace retired `overview.html` artifact references with `demo-deck.html`, and
replace the missing root `threadlight-experience.html` link with
`docs/index.html`.

- [ ] **Step 5: Add the outcome-oriented changelog entry**

Add under `## Unreleased` in `CHANGELOG.md`:

```markdown
### Changed

- Align public documentation with the current lifecycle evidence model:
  Threadlight produces a governed working pilot plus an evidence-backed path to
  production; SPEC § 14 and the forecast → settled actuals → reconciliation →
  cost-per-success chain are now first-class; `threadlight-auto` is described as
  an agent-guided lifecycle planner rather than an executable worker.
```

- [ ] **Step 6: Run the focused contract test**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit the root narrative**

```bash
git add README.md THREADLIGHT.md CHANGELOG.md tests/blueprint/published-surfaces.test.js
git commit -m "docs: align Threadlight public narrative"
```

### Task 2: Make the public example a coherent receipt

**Files:**
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `examples/returns-triage-governed/README.md:3-20`
- Modify: `examples/returns-triage-governed/README.md:100-110`

- [ ] **Step 1: Add the failing receipt-consistency test**

Append:

```javascript
test('the returns-triage receipt distinguishes run capture from regenerated assessment', () => {
  const example = read('examples/returns-triage-governed/README.md');
  const report = read('examples/returns-triage-governed/docs/production-readiness-report.md');
  const spec = read('examples/returns-triage-governed/specs/SPEC.md');

  assert.match(example, /run captured 2026-07-07/i);
  assert.match(example, /assessment regenerated 2026-08-19/i);
  assert.match(example, /29% NOT READY/i);
  assert.match(example, /agent-governance pillar \*\*amber 57%\*\*/i);
  assert.match(example, /14 numbered sections/i);
  assert.match(report, /Raw score:\*\* 29%/);
  assert.match(report, /Agent governance.*57%/);
  assert.match(spec, /^## 14\. Value Model/m);
});
```

- [ ] **Step 2: Run the test and observe stale scores**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: FAIL because the example still publishes `31%`, `71%`, and `13 sections`.

- [ ] **Step 3: Replace the example receipt header**

Use this exact block:

```markdown
> ### A captured, sanitized Threadlight run — read the receipts
>
> The live run was captured **2026-07-07**. Its production-readiness assessment
> was regenerated **2026-08-19** with `threadlight-production-ready` v0.11.0 so
> the public receipt reflects the current scorer without pretending the live run
> happened again.
>
> **Sanitized for public release.** Live credentials and resource identifiers are
> excluded or replaced with documented placeholders. The snapshot is evidence to
> inspect, not a bundle to deploy unchanged.
>
> **Start with the governance receipts:**
> - [`returns-triage.agt-policy.yaml`](./returns-triage.agt-policy.yaml) — committed policy.
> - [`docs/agt-governance-report.md`](./docs/agt-governance-report.md) — governance wiring verdict.
> - [`docs/production-readiness-report.md`](./docs/production-readiness-report.md) — **29% NOT READY**, agent-governance pillar **amber 57%**.
> - [`specs/SPEC.md`](./specs/SPEC.md) — 14 numbered sections, including the value model.
```

Change the key-files row to:

```markdown
| `specs/SPEC.md` | The SpecKit spec (14 numbered sections) — canonical source of truth |
```

- [ ] **Step 4: Run the receipt test**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: PASS.

- [ ] **Step 5: Commit the receipt correction**

```bash
git add examples/returns-triage-governed/README.md tests/blueprint/published-surfaces.test.js
git commit -m "docs: refresh the public evidence receipt"
```

### Task 3: Align the current cost and readiness skill contracts

**Files:**
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `skills/threadlight-consumption-iq/SKILL.md:55-90`
- Modify: `skills/threadlight-consumption-iq/SKILL.md:316-335`
- Modify: `skills/threadlight-production-ready/SKILL.md:18-45`
- Modify: `skills/threadlight-production-ready/SKILL.md:452-475`
- Modify: `skills/threadlight-production-ready/references/report-template.md:127-147`
- Modify: `skills/threadlight-production-ready/references/handoff-checklist.md:35-43`

- [ ] **Step 1: Add failing contract assertions**

Append:

```javascript
test('cost and readiness skill docs publish the current evidence contracts', () => {
  const consumption = read('skills/threadlight-consumption-iq/SKILL.md');
  const readiness = read('skills/threadlight-production-ready/SKILL.md');
  const readinessHead = readiness.split('\n').slice(0, 120).join('\n');
  const reportTemplate = read('skills/threadlight-production-ready/references/report-template.md');
  const handoff = read('skills/threadlight-production-ready/references/handoff-checklist.md');

  assert.match(consumption, /threadlight-cost-actuals\/v1/);
  assert.match(consumption, /threadlight-cost-reconciliation\/v1/);
  assert.match(consumption, /COST-102/);
  assert.match(consumption, /COST-103.*PAYG\/PTU recommendation/i);
  assert.doesNotMatch(consumption, /Live actual-cost queries.*threadlight-production-ready/i);

  assert.match(readinessHead, /v0\.11\.0/);
  assert.doesNotMatch(readinessHead, /v0\.3\.0/);
  assert.doesNotMatch(readinessHead, /docs\/production-readiness\.md/);
  assert.match(reportTemplate, /reconciled Azure actuals/i);
  assert.match(reportTemplate, /KPI-003/);
  assert.match(handoff, /SPEC (?:§|section) 14/i);
  assert.match(handoff, /scope-bound reconciliation/i);
});
```

- [ ] **Step 2: Run the contract test**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: FAIL on the stale Consumption IQ ownership/COST wording and the old
production-ready introduction.

- [ ] **Step 3: Rewrite the Consumption IQ ownership and output summary**

The opening contract must state:

```markdown
Consumption IQ owns cost projection, read-only Azure actuals collection, and
forecast-to-actual reconciliation. `threadlight-production-ready` consumes the
verified artifacts; it does not query Azure Cost Management or recompute
reconciliation.

Outputs:
- `specs/cost-manifest.json` — forecast
- `specs/cost-actuals-manifest.json` — observed scope/window/cost/usage evidence
- `specs/cost-reconciliation-manifest.json` — maturity, variance, unit economics,
  and PAYG/PTU driver
- `docs/cost-reconciliation-report.md` — operator-readable reconciliation
```

Replace the stale COST table entry with:

```markdown
| `COST-102` | Reconciled actual-versus-forecast evidence is mature, fresh, and scope-bound |
| `COST-103` | PAYG/PTU recommendation at observed token volume |
| `KPI-003` | Measured cost per successful interaction joins eval quality and live telemetry |
```

- [ ] **Step 4: Put the current production-ready contract first**

Make the first versioned heading in `threadlight-production-ready/SKILL.md`:

```markdown
# Threadlight Production Ready v0.11.0

An advisory assessment and explicit remediation workflow. Assessment is
read-only; repository remediation, CI scaffolding, and deployment occur only
through their documented explicit actions.
```

Remove the missing `docs/production-readiness.md` link. Make SPEC §12 behavior
mode-specific: assessment continues with framing/fallback evidence when it is
absent; commands that require declared production targets fail with the existing
input error.

- [ ] **Step 5: Refresh the report and handoff templates**

Replace direct seven-day-spend extrapolation in `report-template.md` with:

```markdown
### Forecast and reconciled actuals

- Forecast evidence: `specs/cost-manifest.json`
- Settled actuals: `specs/cost-actuals-manifest.json`
- Scope-bound reconciliation: `specs/cost-reconciliation-manifest.json`
- Outcome unit cost: KPI-003, measured cost per successful interaction

Young pilots may have forecast-only evidence. Mature go-live reviews must state
the actuals window, target scope, reconciliation status, and any unallocated cost.
```

Add these handoff checks:

```markdown
- [ ] SPEC § 14 names the success event, baseline, target, owner, measurement source, and maturity policy.
- [ ] Actuals declare the target subscription, resource group, and settled window.
- [ ] Reconciliation is scope-bound and records variance, coverage, and unallocated cost.
- [ ] KPI-003 is measured from reconciled actuals or is explicitly not verified.
```

- [ ] **Step 6: Run the contract test**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit the skill-contract documentation**

```bash
git add skills/threadlight-consumption-iq/SKILL.md skills/threadlight-production-ready/SKILL.md skills/threadlight-production-ready/references/report-template.md skills/threadlight-production-ready/references/handoff-checklist.md tests/blueprint/published-surfaces.test.js
git commit -m "docs: align cost and readiness contracts"
```

### Task 4: Put value and evidence boundaries on the primary Pages journey

**Files:**
- Modify: `tests/playwright/tests/site.spec.mjs`
- Modify: `docs/index.html:867-934`
- Modify: `docs/index.html:1240-1305`
- Modify: `docs/production.html:376-583`
- Modify: `docs/case-study.html:1567-1610`
- Modify: `docs/self-improving.html:152-193`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:6342-6350`

- [ ] **Step 1: Add failing browser assertions for value and proof boundaries**

Add inside the landing-page describe block:

```javascript
test('states the value contract, later-pilot cost chain, and reel evidence boundary', async ({ page }) => {
  await page.goto(LANDING);
  await expect(page.locator('#how-it-works')).toContainText(/governed working pilot/i);
  const evidence = page.locator('#value-evidence');
  await expect(evidence).toContainText(/SPEC\s+(?:§|section)\s+14/i);
  await expect(evidence).toContainText(/settled Azure actuals/i);
  await expect(evidence).toContainText(/cost per successful interaction/i);
  await expect(evidence.locator('a[href="./self-improving.html#how"]')).toHaveCount(1);
  await expect(page.locator('#reel')).toContainText(/evidence-backed recreation/i);
  await expect(page.locator('#reel a[href="./case-study.html#proof"]')).toHaveCount(1);
});
```

Add a new describe block:

```javascript
test.describe('evidence wording', () => {
  test('keeps forecast, actuals, and diagnostics distinct', async ({ page }) => {
    await page.goto('/case-study.html#cost');
    await expect(page.locator('#cost')).toContainText(/reviewed monthly projection/i);
    await expect(page.locator('#cost')).not.toContainText(/what it actually costs/i);

    await page.goto('/production.html#proof');
    await expect(page.locator('#proof')).toContainText(/forecast/i);
    await expect(page.locator('#proof')).toContainText(/Azure actuals/i);
    await expect(page.locator('#proof')).toContainText(/reconciliation/i);

    await page.goto('/self-improving.html#how');
    await expect(page.locator('#how')).toContainText(/diagnostics-to-backlog/i);
    await expect(page.locator('#how')).not.toContainText(/automatic remediation/i);
  });
});
```

- [ ] **Step 2: Run the focused browser tests**

Run:

```bash
cd tests/playwright
npx playwright test tests/site.spec.mjs --grep "value contract|evidence wording"
```

Expected: FAIL on missing `#value-evidence` and stale cost wording.

- [ ] **Step 3: Add the value-evidence section to the home page**

Insert after `#how-it-works` in `docs/index.html`:

```html
<section class="scene" id="value-evidence" aria-labelledby="value-evidence-h">
  <div class="wrap">
    <p class="eyebrow reveal"><span class="dot"></span>Value evidence</p>
    <h2 id="value-evidence-h" class="reveal">A governed working pilot first. <em>Measured value as it matures.</em></h2>
    <p class="lede reveal">
      SPEC § 14 records the baseline, target, owner, timeframe, measurement
      source, and maturity policy. Threadlight never guesses those values.
    </p>
    <ol class="recap-flow reveal" aria-label="Value and cost evidence progression">
      <li class="rf-step"><span class="rf-n">01</span><b>Forecast</b><span>model the planned Azure shape</span></li>
      <li class="rf-step"><span class="rf-n">02</span><b>Settled Azure actuals</b><span>collect a later-pilot billing window</span></li>
      <li class="rf-step"><span class="rf-n">03</span><b>Reconcile</b><span>bind scope, variance, and coverage</span></li>
      <li class="rf-step"><span class="rf-n">04</span><b>Outcome KPI</b><span>cost per successful interaction</span></li>
    </ol>
    <p class="reveal"><a href="./self-improving.html#how">See how finished runs become a diagnostics backlog</a></p>
  </div>
</section>
```

Change the reel intro text to:

```html
<p class="rc-sub">This interactive reel is an <strong>evidence-backed recreation</strong> of the idea-to-pilot journey. Inspect the <a href="./case-study.html#proof">captured live-run evidence</a> for prompts, traces, costs, limitations, and verdicts.</p>
```

Replace `Six skills, one continuous run` claims with wording that describes a
curated demo path through the 22-skill library.

- [ ] **Step 4: Correct cost and self-improving wording**

In `docs/case-study.html`, replace:

```html
<span class="t">What it actually costs, reviewed</span>
```

with:

```html
<span class="t">Reviewed monthly projection</span>
```

Add this paragraph to `docs/production.html#proof`:

```html
<p class="lede reveal">
  Forecast evidence is available before deployment. A mature pilot can add
  read-only Azure actuals for a settled billing window, reconcile them to the
  target subscription and resource group, and derive measured cost per successful
  interaction. Missing or mismatched evidence stays not verified.
</p>
```

Replace the self-improving mechanism summary with:

```html
<p class="lede reveal">
  The current flow is diagnostics-to-backlog: harvest one finished workflow run,
  classify grounded findings, and rank the next fixes. It does not apply changes,
  assign owners, rerun the workflow, or claim measured improvement automatically.
</p>
```

In `production_ready.py`, replace the sentence beginning `Joins the three signals`
with:

```python
        out.append("Joins the three outcome signals a production review needs "
                   "(eval quality + measured unit cost + live telemetry):")
```

- [ ] **Step 5: Run the browser and production-ready string tests**

Run:

```bash
cd tests/playwright
npx playwright test tests/site.spec.mjs --grep "value contract|evidence wording"
cd ../..
python -m pytest skills/threadlight-production-ready/tests/test_kpi_scorecard.py -q
```

Expected: both commands PASS.

- [ ] **Step 6: Commit the primary Pages narrative**

```bash
git add docs/index.html docs/production.html docs/case-study.html docs/self-improving.html tests/playwright/tests/site.spec.mjs skills/threadlight-production-ready/scripts/production_ready.py
git commit -m "docs: surface value and evidence boundaries"
```

### Task 5: Guard public links, fragments, counts, naming, and SPEC shape

**Files:**
- Create: `tests/blueprint/public-links.test.js`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `docs/funnel.html:574-609`
- Modify: `docs/industries.html:211-290`
- Modify: `docs/blueprint.html:269-273`
- Modify: `.github/workflows/docs-blueprint.yml:9-52`

- [ ] **Step 1: Create the internal-link and fragment test**

Create `tests/blueprint/public-links.test.js`:

```javascript
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.join(__dirname, '../..');
const docsRoot = path.join(repoRoot, 'docs');
const htmlFiles = fs.readdirSync(docsRoot).filter((name) => name.endsWith('.html'));

function read(name) {
  return fs.readFileSync(path.join(docsRoot, name), 'utf8');
}

function ids(html) {
  return new Set([...html.matchAll(/\sid=["']([^"']+)["']/g)].map((match) => match[1]));
}

test('every local HTML link and fragment resolves', () => {
  const idMap = new Map(htmlFiles.map((name) => [name, ids(read(name))]));

  for (const source of htmlFiles) {
    for (const match of read(source).matchAll(/href=["']([^"']+)["']/g)) {
      const href = match[1];
      if (/^(?:https?:|mailto:|data:|javascript:)/.test(href)) continue;

      const [rawPath, fragment] = href.split('#', 2);
      const target = rawPath ? path.basename(rawPath) : source;
      if (!target.endsWith('.html')) continue;

      assert.ok(idMap.has(target), `${source}: missing target ${target}`);
      if (fragment) {
        assert.ok(
          idMap.get(target).has(fragment),
          `${source}: missing fragment ${target}#${fragment}`,
        );
      }
    }
  }
});
```

- [ ] **Step 2: Run the link test and observe the broken industry fragments**

Run:

```bash
node --test tests/blueprint/public-links.test.js
```

Expected: FAIL on `industries.html#sector-grid`.

- [ ] **Step 3: Fix fragments and publish the current SPEC shape**

Change all six industry links in `docs/funnel.html` from:

```html
industries.html#sector-grid
```

to:

```html
industries.html#library
```

Change the `docs/industries.html` SPEC summary to:

```html
<span class="ss-meta">14 numbered sections &middot; explicit value model &middot; SpecKit shape</span>
```

Add a visible §14 row that names:

```html
<b>14. Value Model</b><span>Baseline, target, owner, measurement source, and maturity policy</span>
```

Change the funnel headline to:

```html
<h2 id="ind-h">Fifteen industries, <em>eighty-nine curated scenarios.</em></h2>
```

In `docs/blueprint.html`, replace the claim that the composer returns the
"exact" lifecycle with:

```html
<p>The composer returns a deterministic starter lifecycle from declared process
signals. Manual evidence legs and later-pilot activities are added when their
real prerequisites exist.</p>
```

Append this active-pages terminology test to
`published-surfaces.test.js`:

```javascript
test('active Pages use current counts, product naming, and bounded Blueprint claims', () => {
  const pages = [
    'docs/index.html',
    'docs/funnel.html',
    'docs/production.html',
    'docs/industries.html',
    'docs/blueprint.html',
    'docs/case-study.html',
  ].map(read).join('\n');
  assert.match(pages, /fifteen industries/i);
  assert.match(pages, /eighty-nine curated scenarios/i);
  assert.match(pages, /Microsoft Foundry/);
  assert.doesNotMatch(pages, /Azure AI Foundry/);
  assert.doesNotMatch(read('docs/blueprint.html'), /exact (?:arc|lifecycle)/i);
});
```

Replace active-page `Azure AI Foundry` product references with
`Microsoft Foundry`.

- [ ] **Step 4: Expand the docs workflow triggers**

Replace the enumerated Pages HTML and JavaScript paths in both trigger blocks of
`.github/workflows/docs-blueprint.yml` with:

```yaml
      - "docs/*.html"
      - "docs/assets/*.js"
      - "docs/assets/*.json"
      - "tests/playwright/**"
```

Keep the existing skill, root-doc, example, test, and generator paths.

- [ ] **Step 5: Add the existing Playwright suite as a Pages CI job**

Add to `.github/workflows/docs-blueprint.yml`:

```yaml
  pages-browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: tests/playwright/package-lock.json
      - run: npm ci
        working-directory: tests/playwright
      - run: npx playwright install --with-deps chromium
        working-directory: tests/playwright
      - run: npm test
        working-directory: tests/playwright
```

This reuses the existing runner and test framework; it does not create a second
browser-test stack.

- [ ] **Step 6: Run all Node public-contract tests**

Run:

```bash
node --test tests/blueprint/*.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit the public link guards**

```bash
git add tests/blueprint/public-links.test.js docs/funnel.html docs/industries.html .github/workflows/docs-blueprint.yml
git commit -m "test: guard public Pages links and claims"
```

### Task 6: Restore mobile chapter navigation

**Files:**
- Modify: `tests/playwright/tests/site.spec.mjs:93-130`
- Modify: `docs/assets/site.js:308-326`
- Modify: `docs/assets/site.css:193-243`
- Modify: `docs/*.html` cache tokens via `docs/ci/sync_cache_bust.py`

- [ ] **Step 1: Add the failing mobile-navigation test**

Add inside the primary-navigation describe block:

```javascript
test('mobile users can open and close the chapter navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(LANDING);

  const toggle = page.locator('[data-mobile-nav-toggle]');
  const nav = page.locator('header.masthead nav.nav');
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(nav).toBeHidden();

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(nav).toBeVisible();

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(nav).toBeHidden();
});
```

- [ ] **Step 2: Run the mobile test and confirm no toggle exists**

Run:

```bash
cd tests/playwright
npx playwright test tests/site.spec.mjs --grep "mobile users"
```

Expected: FAIL because `[data-mobile-nav-toggle]` is absent.

- [ ] **Step 3: Add the shared mobile-nav behavior**

Insert before `init()` in `docs/assets/site.js`:

```javascript
  function wireMobileNav() {
    const masthead = document.querySelector('header.masthead');
    const nav = masthead && masthead.querySelector('nav.nav');
    if (!masthead || !nav || masthead.querySelector('[data-mobile-nav-toggle]')) return;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'mobile-nav-toggle';
    button.setAttribute('data-mobile-nav-toggle', '');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', 'Toggle chapter navigation');
    button.textContent = 'Menu';

    button.addEventListener('click', () => {
      const open = nav.toggleAttribute('data-mobile-open');
      button.setAttribute('aria-expanded', String(open));
    });

    masthead.insertBefore(button, nav);
  }
```

Add `wireMobileNav();` as the first call in `init()`.

- [ ] **Step 4: Add the mobile CSS**

Replace the current max-width 720 rule with:

```css
  .mobile-nav-toggle {
    display: none;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 8px 12px;
    background: var(--bg-1);
    color: var(--ink-0);
    font: 600 12px var(--sans);
  }
  @media (max-width: 720px) {
    .mobile-nav-toggle { display: inline-flex; }
    .masthead .nav {
      display: none;
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      padding: 16px 20px;
      flex-direction: column;
      align-items: flex-start;
      background: var(--bg-1);
      border-bottom: 1px solid var(--line);
    }
    .masthead .nav[data-mobile-open] { display: flex; }
  }
```

- [ ] **Step 5: Regenerate cache tokens**

Run:

```bash
python docs/ci/sync_cache_bust.py --write
python docs/ci/sync_cache_bust.py --check
```

Expected: both commands exit 0; every `docs/*.html` page references the new
`site.js` and `site.css` hashes.

- [ ] **Step 6: Run the navigation browser tests**

Run:

```bash
cd tests/playwright
npx playwright test tests/site.spec.mjs --grep "primary navigation|mobile users"
```

Expected: PASS.

- [ ] **Step 7: Commit the mobile navigation**

```bash
git add docs/assets/site.js docs/assets/site.css docs/*.html tests/playwright/tests/site.spec.mjs
git commit -m "fix: restore mobile Pages navigation"
```

### Task 7: Run the complete PR 1 gate

**Files:**
- Verify only: all files changed by Tasks 1-5

- [ ] **Step 1: Run all static public-contract tests**

```bash
node --test tests/blueprint/*.test.js
```

Expected: PASS with zero failures.

- [ ] **Step 2: Run the cache invariant**

```bash
python docs/ci/sync_cache_bust.py --check
```

Expected: `OK`/exit 0 with no stale token.

- [ ] **Step 3: Run the complete Pages browser suite**

```bash
cd tests/playwright
npm ci
npx playwright install chromium
npx playwright test
```

Expected: PASS with zero failed tests.

- [ ] **Step 4: Run the touched production-ready test**

```bash
cd ../..
python -m pytest skills/threadlight-production-ready/tests/test_kpi_scorecard.py -q
```

Expected: PASS.

- [ ] **Step 5: Verify public-safe wording and clean scope**

```bash
node --test tests/blueprint/published-surfaces.test.js
git diff --check
git diff --name-only
```

Expected: the public-safety assertions pass; `git diff --check` exits 0; the
file list contains only intended PR 1 files.

## Scope cuts

If this plan exceeds one focused implementation session, cut in this order:

1. Additional animation or visual polish.
2. Changes to `workbook.html`, `blueprint-logic.js`, or process-library generation.
3. New screenshot assets.

Do not cut the root narrative contract, value/cost evidence distinction, example
receipt correction, broken-fragment test, or mobile navigation.
