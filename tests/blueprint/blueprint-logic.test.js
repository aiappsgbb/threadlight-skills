const { test } = require('node:test');
const assert = require('node:assert');
const L = require('../../docs/assets/blueprint-logic.js');

const base = {
  name: 'X', summary: 'do X', industry: 'retail', complexity: 'low',
  business_constraints: [], external_integrations: [], human_approvals: [], knowledge_sources: [], tags: [],
};

test('baseline arc always present', () => {
  const s = L.deriveSkills(base);
  ['threadlight-design', 'threadlight-local-test', 'threadlight-safe-check',
    'threadlight-deploy', 'threadlight-evals'].forEach(k => assert.ok(s.includes(k), `missing ${k}`));
});

test('integrations add demo-data-factory', () => {
  assert.ok(L.deriveSkills({ ...base, external_integrations: [{ name: 'SAP' }] })
    .includes('threadlight-demo-data-factory'));
});

test('approvals add hitl-patterns', () => {
  assert.ok(L.deriveSkills({ ...base, human_approvals: [{ step: 'review' }] })
    .includes('threadlight-hitl-patterns'));
});

test('event/trigger tags add event-triggers', () => {
  assert.ok(L.deriveSkills({ ...base, tags: ['scheduled', 'webhook'] })
    .includes('threadlight-event-triggers'));
});

test('high complexity adds production/govern/redteam', () => {
  const s = L.deriveSkills({ ...base, complexity: 'high' });
  ['threadlight-production-ready', 'threadlight-govern', 'threadlight-redteam']
    .forEach(k => assert.ok(s.includes(k), `missing ${k}`));
});

test('regulated industry adds consumption-iq', () => {
  assert.ok(L.deriveSkills({ ...base, industry: 'financial_services' })
    .includes('threadlight-consumption-iq'));
});

test('deriveSkills is deterministic + de-duplicated', () => {
  const p = { ...base, complexity: 'high', external_integrations: [{ name: 'A' }] };
  const s = L.deriveSkills(p);
  assert.deepStrictEqual(s, L.deriveSkills(p));
  assert.strictEqual(new Set(s).size, s.length);
});

test('buildPrompt embeds name, summary + threadlight-auto', () => {
  const p = L.buildPrompt(base);
  assert.ok(p.includes('threadlight-auto'));
  assert.ok(p.includes('do X'));
  assert.ok(p.includes('X'));
});

test('buildPrompt lists integrations + approvals when present', () => {
  const p = L.buildPrompt({ ...base, external_integrations: [{ name: 'SAP' }], human_approvals: [{ step: 'legal sign-off' }] });
  assert.ok(p.includes('SAP'));
  assert.ok(p.includes('legal sign-off'));
});

test('buildAzd returns azd up quickstart', () => {
  assert.ok(L.buildAzd(base).includes('azd up'));
});
