const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.join(__dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(repoRoot, rel), 'utf8');

const NEW_SKILLS = [
  'threadlight-qualify',
  'threadlight-connect',
  'threadlight-ground',
  'threadlight-loadtest',
  'threadlight-upgrade',
];

const THREADLIGHT_SKILLS = [
  'threadlight-auto',
  'threadlight-cicd',
  'threadlight-connect',
  'threadlight-consumption-iq',
  'threadlight-customize',
  'threadlight-demo-data-factory',
  'threadlight-deploy',
  'threadlight-design',
  'threadlight-evals',
  'threadlight-event-triggers',
  'threadlight-govern',
  'threadlight-ground',
  'threadlight-hitl-patterns',
  'threadlight-loadtest',
  'threadlight-local-test',
  'threadlight-production-ready',
  'threadlight-qualify',
  'threadlight-redteam',
  'threadlight-router-bench',
  'threadlight-safe-check',
  'threadlight-upgrade',
  'threadlight-workspace-ui',
];

// Active product surfaces that must never carry a stale skill-count claim. NB:
// "thirteen pillars" / "13 pillars" are production-ready PILLAR counts, not
// skill counts, and are deliberately NOT matched by STALE_COUNT below.
const ACTIVE_SURFACES = [
  'README.md',
  'THREADLIGHT.md',
  'plugin.json',
  'docs/funnel.html',
  'docs/production.html',
  'docs/self-improving.html',
  'docs/index.html',
  'docs/customize.html',
];
const STALE_COUNT = /17 skills|17 total|16 pipeline|13-skill library|sixteen[ -]skill|seventeen[ -]skill|all 17 skills/i;

test('filesystem publishes exactly 22 threadlight-* skills', () => {
  const dirs = fs
    .readdirSync(path.join(repoRoot, 'skills'), { withFileTypes: true })
    .filter((d) => d.isDirectory() && d.name.startsWith('threadlight-'))
    .map((d) => d.name);
  assert.strictEqual(dirs.length, 22, `expected 22 skills, found ${dirs.length}: ${dirs.join(', ')}`);
});

test('plugin.json is version 1.12.0 with the 22-total description', () => {
  const plugin = JSON.parse(read('plugin.json'));
  assert.strictEqual(plugin.version, '1.12.0');
  assert.match(
    plugin.description,
    /21 pipeline skills \+ threadlight-auto agent-guided lifecycle planner \(22 total\).*brief to a governed working pilot with an evidence-backed path to production/i,
  );
  assert.doesNotMatch(plugin.description, /full-auto orchestrator/i);
  assert.doesNotMatch(plugin.description, /brief to a deployed, production-ready Foundry agent/i);
});

test('marketplace metadata versions stay in parity with plugin.json', () => {
  const plugin = JSON.parse(read('plugin.json'));
  const marketplace = JSON.parse(read('.github/plugin/marketplace.json'));

  assert.strictEqual(marketplace.metadata.version, plugin.version);
  assert.strictEqual(marketplace.plugins[0].version, plugin.version);
});

test('published surfaces enumerate the 22-skill pack', () => {
  // The plan's canonical smoke assertions.
  assert.match(read('plugin.json'), /22 total/);
  assert.match(read('README.md'), /threadlight-qualify/);
  assert.match(read('THREADLIGHT.md'), /threadlight-upgrade/);
});

test('THREADLIGHT inventory is the exact unique alphabetical 22-skill set', () => {
  const briefing = read('THREADLIGHT.md');
  const inventory = briefing.match(
    /The twenty-two skills \(alphabetical,[\s\S]*?\n```(?:text)?\n([\s\S]*?)\n```/,
  );
  assert.ok(inventory, 'THREADLIGHT.md must contain the fenced twenty-two-skill inventory');

  const published = inventory[1].match(/threadlight-[a-z0-9-]+/g) || [];
  assert.deepStrictEqual(published, THREADLIGHT_SKILLS);
  assert.strictEqual(new Set(published).size, 22, 'inventory skill IDs must be unique');
});

test('README, THREADLIGHT and plugin.json each name all five new skills', () => {
  for (const surface of ['README.md', 'THREADLIGHT.md', 'plugin.json']) {
    const text = read(surface);
    for (const skill of NEW_SKILLS) {
      assert.ok(text.includes(skill), `${surface} is missing ${skill}`);
    }
  }
});

test('GitHub Pages link every new skill to its repository skill folder', () => {
  const funnel = read('docs/funnel.html');
  const production = read('docs/production.html');
  const selfImproving = read('docs/self-improving.html');

  // funnel = the no-repo qualify entry + a Cowork download.
  assert.ok(funnel.includes('skills/threadlight-qualify'), 'funnel must link threadlight-qualify');
  assert.ok(funnel.includes('downloads/threadlight-qualify.zip'), 'funnel must offer the Cowork zip');

  // production = the connect/ground/load evidence progression.
  for (const skill of ['threadlight-connect', 'threadlight-ground', 'threadlight-loadtest']) {
    assert.ok(production.includes(`skills/${skill}`), `production must link ${skill}`);
  }

  // self-improving = the plan-only upgrade lifecycle scan.
  assert.ok(selfImproving.includes('skills/threadlight-upgrade'), 'self-improving must link threadlight-upgrade');
});

test('index + customize render the accurate 22 count', () => {
  assert.match(read('docs/index.html'), /Threadlight is 22/);
  assert.match(read('docs/customize.html'), /22 skills/);
});

test('no active product surface carries a stale skill-count claim', () => {
  for (const surface of ACTIVE_SURFACES) {
    const text = read(surface);
    const m = STALE_COUNT.exec(text);
    assert.strictEqual(m, null, `${surface} still has a stale skill-count claim: ${m && m[0]}`);
  }
});

test('plugin.json and marketplace metadata use governed-pilot planner wording', () => {
  const plugin = JSON.parse(read('plugin.json'));
  const marketplace = JSON.parse(read('.github/plugin/marketplace.json'));

  const descriptions = [
    ['plugin.json', plugin.description],
    ['marketplace.metadata.description', marketplace.metadata.description],
    ['marketplace.plugins[0].description', marketplace.plugins[0].description],
  ];

  for (const [label, description] of descriptions) {
    assert.match(description, /22 total/);
    assert.match(description, /agent-guided lifecycle planner/i, `${label} must call threadlight-auto a planner`);
    assert.match(description, /governed working pilot/i, `${label} must describe a governed pilot`);
    assert.doesNotMatch(description, /full-auto orchestrator/i, `${label} must not claim full-auto orchestration`);
    assert.doesNotMatch(description, /brief to a deployed, production-ready Foundry agent/i, `${label} must not claim brief-to-production-ready`);
    assert.doesNotMatch(description, /17 total|16 pipeline/i, `${label} must not keep the stale count`);
  }
});

test('root docs describe a governed pilot, explicit value evidence, and Auto as a planner', () => {
  const readme = read('README.md');
  const threadlight = read('THREADLIGHT.md');
  const oneSessionProductionClaim =
    /(?:production-ready[\s\S]{0,120}(?:single working session|one session)|(?:single working session|one session)[\s\S]{0,120}production-ready)/i;

  for (const surface of [readme, threadlight]) {
    for (const phrase of [
      'governed working pilot',
      'evidence-backed path to production',
      'SPEC § 14',
      'settled Azure actuals',
      'cost per successful interaction',
    ]) {
      assert.ok(surface.includes(phrase), `missing ${phrase}`);
    }
  }

  for (const claim of [
    'production-ready — in a single working session',
    'production-ready\nin one session',
    'one session\nproduction-ready',
  ]) {
    assert.match(claim, oneSessionProductionClaim, `claim pattern should be rejected: ${claim}`);
  }

  for (const good of ['A pilot can be produced in a working session.', 'A working session produces the pilot and evidence.']) {
    assert.doesNotMatch(good, oneSessionProductionClaim, `legitimate working-session phrasing should stay allowed: ${good}`);
  }

  assert.ok(!oneSessionProductionClaim.test(readme));
  assert.ok(!oneSessionProductionClaim.test(threadlight));
  assert.match(readme, /agent-guided lifecycle planner/);
  assert.match(threadlight, /planner does not execute stages/);

  for (const stale of [
    'NEW v0.1.0-alpha',
    'v0.3.0',
    'React workspace',
    'overview.html',
    'threadlight-experience.html',
  ]) {
    assert.ok(!readme.includes(stale), `README still includes stale reference: ${stale}`);
    assert.ok(!threadlight.includes(stale), `THREADLIGHT still includes stale reference: ${stale}`);
  }
});

test('cost and readiness skill docs publish the current evidence contracts', () => {
  const consumptionIq = read('skills/threadlight-consumption-iq/SKILL.md');
  const productionReady = read('skills/threadlight-production-ready/SKILL.md');
  const productionReadyLines = productionReady.split('\n');
  const productionReadyIntro = productionReadyLines.slice(0, 120).join('\n');
  const framingQuestions = productionReadyLines.slice(170, 186).join('\n');
  const customerOverrides = productionReadyLines.slice(998, 1009).join('\n');
  const reportSectionsBlock = productionReady.match(
    /### `docs\/production-readiness-report\.md`[\s\S]*?The report is the customer-facing artefact\./,
  )[0];
  const readinessNotBlock = productionReady.match(
    /## What this skill is NOT[\s\S]*?## Out of scope for v0\.5\.0/,
  )[0];
  const reportTemplate = read('skills/threadlight-production-ready/references/report-template.md');
  const handoffChecklist = read('skills/threadlight-production-ready/references/handoff-checklist.md');
  const handoffCurrentSection = handoffChecklist.split('\n').slice(65, 70).join('\n');

  assert.match(consumptionIq, /threadlight-cost-actuals\/v1/);
  assert.match(consumptionIq, /threadlight-cost-reconciliation\/v1/);
  assert.match(consumptionIq, /COST-102[\s\S]{0,200}mature\/fresh\/scope-bound reconciliation/i);
  assert.match(consumptionIq, /COST-103[\s\S]{0,200}PAYG\/PTU recommendation at observed token volume/i);
  assert.match(consumptionIq, /production-ready consumes verified artifacts and does not query or recompute/i);
  assert.doesNotMatch(consumptionIq, /Live actual-cost queries[\s\S]{0,120}threadlight-production-ready/i);

  assert.match(productionReadyIntro, /v0\.11\.0/);
  assert.doesNotMatch(productionReadyIntro, /v0\.3\.0/);
  assert.ok(!productionReadyIntro.includes('docs/production-readiness.md'), 'intro must not link a missing production-readiness doc');

  assert.match(framingQuestions, /`github-actions` is the only supported value/i);
  assert.doesNotMatch(framingQuestions, /only supported v0\.5\.0 value/i);
  assert.doesNotMatch(framingQuestions, /new in v0\.5\.0/i);

  assert.match(customerOverrides, /only valid on the assessment codepath/i);
  assert.doesNotMatch(customerOverrides, /v0\.3\.0 assess codepath/i);
  assert.doesNotMatch(customerOverrides, /v0\.5\.0/i);

  assert.match(handoffCurrentSection, /governance \+ capacity surface/i);
  assert.match(handoffCurrentSection, /current governance and capacity findings/i);
  assert.doesNotMatch(handoffCurrentSection, /v0\.3\.0/i);

  assert.match(reportTemplate, /reconciled Azure actuals/i);
  assert.match(reportTemplate, /Actuals window: `\{actuals_window\}`\./);
  assert.match(reportTemplate, /Target scope: `\{actuals_scope\}`\./);
  assert.match(reportTemplate, /Scope-bound reconciliation coverage: `\{reconciliation_coverage\}`\./);
  assert.match(reportTemplate, /Variance vs forecast: `\{reconciliation_variance\}`\./);
  assert.match(reportTemplate, /Unallocated cost: `\$\{unallocated_cost\}` USD\./);
  assert.match(reportTemplate, /KPI-003/);
  assert.match(reportSectionsBlock, /7\. \*\*Cost evidence and unit economics\*\*/);
  assert.match(reportSectionsBlock, /8\. \*\*Eval summary\*\*/);
  assert.doesNotMatch(reportSectionsBlock, /7\. \*\*Cost projection\*\*/);
  assert.doesNotMatch(reportSectionsBlock, /8\. \*\*Outcome KPI scorecard\*\*/);
  assert.match(readinessNotBlock, /consumes reconciled actuals from[\s\S]{0,40}threadlight-consumption-iq/i);
  assert.match(
    readinessNotBlock,
    /deeper PAYG-vs-PTU analysis, run `paygo-ptu-cost-analyzer`/i,
  );
  assert.doesNotMatch(
    readinessNotBlock,
    /surfaces PAYG-vs-PTU recommendations from[\s\S]{0,60}`paygo-ptu-cost-analyzer` outputs/i,
  );

  assert.match(handoffChecklist, /SPEC (?:§|section) 14/i);
  assert.match(
    handoffChecklist,
    /Reconciled Azure actuals, if collected, target the intended subscription\/resource-group scope and a settled window/i,
  );
  assert.match(
    handoffChecklist,
    /Scope-bound reconciliation has been reviewed for variance, coverage, and unallocated cost before handoff/i,
  );
  assert.match(
    handoffChecklist,
    /KPI-003 measured cost per successful interaction is recorded, or the report explicitly says `not-verified`/i,
  );
});

test('the returns-triage receipt distinguishes run capture from regenerated assessment', () => {
  const readme = read('examples/returns-triage-governed/README.md');
  const report = read('examples/returns-triage-governed/docs/production-readiness-report.md');
  const spec = read('examples/returns-triage-governed/specs/SPEC.md');

  assert.match(readme, /captured 2026-07-07/i);
  assert.match(readme, /regenerated 2026-08-19/i);
  assert.match(readme, /29% NOT READY/i);
  assert.match(readme, /agent-governance pillar.*amber 57%/i);
  assert.match(readme, /14 numbered sections/i);

  assert.match(report, /Raw score.*29%/i);
  assert.match(report, /Agent governance \(AGT\).*57%/i);

  assert.match(spec, /^## 14\. Value Model$/m);
});

test('the returns-triage README discloses receipt compatibility limits and preserves report findings', () => {
  const readme = read('examples/returns-triage-governed/README.md');
  const report = read('examples/returns-triage-governed/docs/production-readiness-report.md');
  const spec = read('examples/returns-triage-governed/specs/SPEC.md');

  for (const phrase of [
    'exact committed snapshot',
    'no different input set',
    'older section shape',
    'current parser cannot verify all existing information',
    '§9 evaluation evidence',
    '§10 cost contract',
    'visible compatibility findings',
    'not hidden corrections',
  ]) {
    assert.match(readme, new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), `README must disclose: ${phrase}`);
  }

  assert.match(spec, /^### Evaluation Scenarios$/m);
  assert.match(spec, /success_event:\s*\n\s+name:\s+return_decision_completed/);
  assert.match(spec, /target_cost_per_successful_interaction_usd:\s+0\.18/);
  assert.match(spec, /actual_cost_basis:\s+usage-pretax/);

  assert.match(report, /SPEC sec 9 missing — no eval scenarios declared/i);
  assert.match(report, /SPEC sec 10 \(Cost\) missing — pricing plan undocumented/i);
  assert.match(report, /What was not verified/i);
});

test('the generated process library exposes qualify as entry metadata, never a runtime skill', () => {
  const library = JSON.parse(read('docs/assets/process-library.json'));
  assert.ok(Array.isArray(library) && library.length > 0);
  for (const entry of library) {
    assert.strictEqual(entry.playbook.entry_skill, 'threadlight-qualify', `${entry.id} entry_skill`);
    assert.ok(
      !entry.playbook.build_skills.includes('threadlight-qualify'),
      `${entry.id} must not deploy threadlight-qualify as a build/runtime skill`,
    );
  }
});
