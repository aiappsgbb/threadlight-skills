const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// 2.19.4 scope extension (user-directed): continuous evaluation is NOT part of the
// mandatory full Foundry package. GA evaluation_rules reject hosted agents with
// 400 "Hosted and external agents are not supported", and beta.schedules is
// preview (out of scope). COMPLETE needs one working, executed Foundry eval.

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const MARKER = '<!-- threadlight:foundry-full-package -->';
const gateSkills = ['threadlight-auto', 'threadlight-deploy', 'threadlight-evals', 'threadlight-production-ready'];
const OPTIONAL = /continuous evaluation is (\*\*)?optional/i;
const collapse = (t) => t.replace(/\n>\s?/g, ' ').replace(/\s+/g, ' ');

test('canonical package gate: continuous evaluation optional, one executed batch eval required', () => {
  const rule = read('skills/_shared/foundry-only-agents.md');
  const section = collapse(rule.slice(rule.indexOf(MARKER)));
  assert.match(section, OPTIONAL);
  assert.match(section, /Hosted and external agents are not supported/);
  assert.match(section, /beta\.schedules[^.]{0,80}preview/i);
  assert.match(section, /azure_ai_target_completions/);
  assert.match(section, /azure_ai_agent/);
  assert.match(section, /deployed and invoked/i);
  assert.doesNotMatch(section, /4\. \*\*Continuous evaluation\*\* wired/);
  assert.match(section, /three parts/i);
});

for (const skill of gateSkills) {
  test(`${skill}: package block does not require continuous evaluation`, () => {
    const text = read(`skills/${skill}/SKILL.md`);
    const block = collapse(text.slice(text.indexOf(MARKER), text.indexOf(MARKER) + 2500));
    assert.doesNotMatch(block, /\(4\) continuous evaluation wired/i);
    assert.match(block, OPTIONAL);
  });
}

test('core principles, README, docs and auto row 7 do not make continuous evaluation mandatory', () => {
  for (const file of ['README.md', 'THREADLIGHT.md', 'docs/agent-operations.md']) {
    const text = collapse(read(file));
    assert.match(text, OPTIONAL, file);
    assert.doesNotMatch(text, /custom rubric,? and continuous evaluation/i, file);
    assert.doesNotMatch(text, /and continuous evaluation are part of/i, file);
  }
  const row7 = read('skills/threadlight-auto/SKILL.md').split('\n').find((l) => /^\| 7 \| Evals/.test(l));
  assert.match(row7, OPTIONAL);
});

test('production-ready COMPLETE line and pillar 6 do not require Foundry continuous evaluation for hosted agents', () => {
  const pr = read('skills/threadlight-production-ready/scripts/production_ready.py');
  assert.doesNotMatch(pr, /Full Foundry package:\*\* COMPLETE[^\n]*continuous eval"/);
  const pillar = collapse(read('skills/threadlight-production-ready/references/pillars/06-continuous-evals.md'));
  assert.match(pillar, /Hosted and external agents are not supported/);
  assert.match(pillar, /not part of the full Foundry package/i);
});
