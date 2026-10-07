const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

const MARKER = '<!-- threadlight:foundry-full-package -->';
const canonicalPath = 'skills/_shared/foundry-only-agents.md';
const gateSkills = [
  'threadlight-auto', 'threadlight-deploy', 'threadlight-evals', 'threadlight-production-ready',
];

test('canonical rule defines the blocking full Foundry package gate', () => {
  const rule = read(canonicalPath);
  assert.ok(rule.includes(MARKER), 'full-package marker missing');
  const section = rule.slice(rule.indexOf(MARKER));
  for (const pattern of [
    /real Foundry (hosted|prompt|hosted or prompt) agent/i,
    /no (fake|fallback)/i,
    /Application Insights|App Insights/,
    /Foundry tracing/i,
    /built-in evaluators/i,
    /custom rubric/i,
    /acceptance criteria/i,
    /threshold/i,
    /continuous evaluation/i,
    /INCOMPLETE/,
    /specs\/foundry-package-manifest\.json/,
    /foundry_package\.py/,
    /mock MCP/i,
    /synthetic data/i,
  ]) assert.match(section, pattern);
  assert.match(section, /never (advisory|optional)|not advisory|mandatory/i);
});

for (const skill of gateSkills) {
  test(`${skill} SKILL.md carries the full-package gate near the top`, () => {
    const text = read(`skills/${skill}/SKILL.md`);
    const idx = text.indexOf(MARKER);
    assert.ok(idx >= 0, `${skill}: full-package marker missing`);
    const lineNo = text.slice(0, idx).split('\n').length;
    assert.ok(lineNo <= 140, `${skill}: marker at line ${lineNo}, must be near the top`);
    const block = text.slice(idx, idx + 2500);
    assert.match(block, /INCOMPLETE/);
    assert.match(block, /foundry-package-manifest\.json/);
    assert.match(block, /_shared\/foundry-only-agents\.md/);
  });
}

test('auto no longer describes evals or the package gate as advisory/never-blocking', () => {
  const auto = read('skills/threadlight-auto/SKILL.md');
  const row7 = auto.split('\n').find((l) => /^\| 7 \| Evals/.test(l));
  assert.ok(row7, 'auto stage 7 row missing');
  assert.doesNotMatch(row7, /advisory|never blocks/i);
  assert.match(row7, /block|INCOMPLETE/i);
  const frontmatter = auto.slice(0, auto.indexOf('\n---', 4));
  assert.match(frontmatter, /INCOMPLETE|full Foundry package/i);
});

test('THREADLIGHT.md evals leg is not described as advisory', () => {
  const core = read('THREADLIGHT.md');
  assert.doesNotMatch(core, /Advisory and gracefully\s+degrading/);
});

test('shared gate checker exists with the documented manifest path', () => {
  const mod = read('skills/_shared/foundry_package.py');
  assert.match(mod, /specs\/foundry-package-manifest\.json/);
  assert.match(mod, /threadlight-foundry-package\/v1/);
});
