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
  assert.match(plugin.description, /21 pipeline skills \+ threadlight-auto orchestrator \(22 total\)/);
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
