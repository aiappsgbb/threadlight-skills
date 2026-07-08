const { test } = require('node:test');
const assert = require('node:assert');
const L = require('../../docs/assets/blueprint-logic.js');

const base = {
  name: 'X', summary: 'do X', industry: 'retail', complexity: 'low',
  business_constraints: [], external_integrations: [], human_approvals: [], knowledge_sources: [], tags: [],
};

test('baseline arc always present (incl. CI/CD deploy path)', () => {
  const s = L.deriveSkills(base);
  ['threadlight-design', 'threadlight-local-test', 'threadlight-safe-check',
    'threadlight-deploy', 'threadlight-cicd', 'threadlight-evals'].forEach(k => assert.ok(s.includes(k), `missing ${k}`));
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

test('buildAutomation describes a hands-off CI/CD deploy with no laptop commands', () => {
  const steps = L.buildAutomation(base);
  assert.ok(Array.isArray(steps) && steps.length >= 3);
  const text = steps.map(s => s.text).join('\n');
  // The whole point: Copilot deploys through CI/CD — the user runs nothing.
  assert.ok(/CI\/CD/.test(text), 'must state the deploy goes through CI/CD');
  assert.ok(/never run a deploy command/i.test(text), 'must say the user runs no deploy command');
  // The old manual-command anti-pattern must be gone.
  assert.ok(!/azd up|azd auth|azd init/.test(text), 'must not tell the user to run azd');
  // The deploy line is flagged for emphasis.
  assert.ok(steps.some(s => s.accent && /CI\/CD/.test(s.text)), 'deploy step should be accented');
});

test('buildAutomation scales with the process (approvals, red-team, pricing)', () => {
  const rich = L.buildAutomation({
    ...base, complexity: 'high', industry: 'financial_services',
    human_approvals: [{ step: 'legal' }],
  });
  const text = rich.map(s => s.text).join('\n');
  assert.ok(/human-approval gates/.test(text), 'names the approval gates');
  assert.ok(/red-teams it/.test(text), 'high complexity red-teams');
  assert.ok(/prices every run/.test(text), 'regulated adds cost pricing');
});

test('buildPrompt tells Copilot to deploy through CI/CD, not the laptop', () => {
  const p = L.buildPrompt(base);
  assert.ok(/CI\/CD/.test(p) && /not my laptop/.test(p));
});

test('parseScenarioParam extracts the s id from a query string', () => {
  assert.strictEqual(L.parseScenarioParam('?s=commercial-loan-origination'), 'commercial-loan-origination');
  assert.strictEqual(L.parseScenarioParam('?foo=1&s=insurance-claims-processing'), 'insurance-claims-processing');
});

test('parseScenarioParam returns empty when absent or blank', () => {
  assert.strictEqual(L.parseScenarioParam(''), '');
  assert.strictEqual(L.parseScenarioParam('?x=1'), '');
  assert.strictEqual(L.parseScenarioParam('?s='), '');
  assert.strictEqual(L.parseScenarioParam(undefined), '');
});

test('parseScenarioParam sanitises to a slug charset (no injection / traversal)', () => {
  assert.strictEqual(L.parseScenarioParam('?s=%3Cscript%3E'), 'script');
  assert.strictEqual(L.parseScenarioParam('?s=abc/../x'), 'abcx');
});
