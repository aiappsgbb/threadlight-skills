const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = file => fs.readFileSync(path.resolve(__dirname, '../..', file), 'utf8');
const model = () => require('../../docs/assets/governed-workflow.js');

test('signed evidence follows human review as a scenario, not an introductory block', () => {
  const html = read('docs/production.html');
  assert.deepEqual([...html.matchAll(/data-flow-scenario="([^"]+)"/g)].map(m => m[1]),
    ['normal', 'invalid', 'supervisor', 'evidence']);
  assert.doesNotMatch(html.split('id="action-architecture"')[0], /data-input-evidence-policy/);
  assert.match(html, /id="production-input-proof"/);
  assert.match(html, /data-flow-evidence-case/);
  assert.match(html, /Policy example: corroborate purchase and amount/);
});

test('missing and changed proof stop; valid matching proof reaches the same effect path', () => {
  const { scenarioDetails, stepDetails, usedModules, permittedEdges, actorState } = model();
  for (const choice of ['missing', 'changed', 'valid']) {
    const route = scenarioDetails('evidence', choice).steps;
    assert.deepEqual(route.slice(0, 4), ['proposal', 'proof', 'present', 'checks']);
    assert.match(stepDetails('evidence', 'proof', choice).actor, /Evidence Provider/);
    const valid = choice === 'valid';
    assert.equal(route.at(-1), valid ? 'result' : 'deny');
    assert.equal(usedModules('evidence', choice).includes('effect'), valid);
    assert.equal(permittedEdges('evidence', choice).includes('ack:effect'), valid);
    assert.equal(actorState('evidence', 'business', route.length - 1, choice),
      valid ? 'Completed' : 'Not involved in write');
    if (!valid) assert.ok(!route.includes('ack'));
  }
  assert.match(stepDetails('evidence', 'checks', 'changed').output, /revision/i);
  assert.match(stepDetails('evidence', 'proof', 'missing').output, /No attestation/);
});

test('the provider returns evidence to the agent before a separate operational MCP call', () => {
  const { scenarioDetails, stepDetails, permittedEdges } = model();
  const edges = permittedEdges('evidence', 'valid');
  assert.deepEqual(edges.slice(0, 3), ['proposal:proof', 'proof:present', 'present:checks']);
  assert.ok(!edges.includes('proof:checks'));
  assert.equal(stepDetails('evidence', 'present', 'valid').actor, 'Agent');
  assert.match(stepDetails('evidence', 'present', 'valid').action, /operational tool.*proof/i);
  assert.ok(scenarioDetails('evidence', 'valid').steps.indexOf('present')
    < scenarioDetails('evidence', 'valid').steps.indexOf('checks'));
  const html = read('docs/production.html');
  assert.match(html, /data-evidence-participant="agent"/);
  assert.match(html, /Evidence Provider/);
  assert.match(html, /authenticated HTTPS/);
  assert.match(html, /operational call uses MCP/);
  const guide = read('docs/agent-governance-deep-dive.md');
  assert.match(guide, /\/evidence\/purchase/);
  assert.match(guide, /not an MCP provider endpoint/);
});

test('evidence is a request and return to one agent, never a provider-to-gateway pipeline', () => {
  const html = read('docs/production.html');
  const participants = [...html.matchAll(/data-evidence-participant="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(participants, ['agent', 'provider', 'gateway', 'backend']);
  const messages = [...html.matchAll(/data-evidence-message="([^"]+)" data-message-from="([^"]+)" data-message-to="([^"]+)"/g)]
    .map(([, ...message]) => message);
  assert.deepEqual(messages, [
    ['request', 'agent', 'provider'], ['return', 'provider', 'agent'],
    ['invoke', 'agent', 'gateway'], ['dispatch', 'gateway', 'backend'],
  ]);
  assert.doesNotMatch(html, /<select[^>]*data-flow-evidence-case/);
  assert.deepEqual([...html.matchAll(/type="radio"[^>]*value="([^"]+)"[^>]*data-flow-evidence-case/g)].map(m => m[1]),
    ['valid', 'missing', 'changed']);
});

test('public JWT explanation names the signed bindings and exact MCP metadata without a usable token', () => {
  const html = read('docs/production.html');
  const contract = html.split('data-evidence-jwt')[1]?.split('<!-- evidence-jwt:end -->')[0] || '';
  for (const field of ['JSON Web Token', 'RS256', 'threadlight-evidence+jwt', 'kid', 'iss', 'aud',
    'tid', 'holder', 'holder_client', 'sub', 'case_id', 'revision', 'action', 'purpose', 'profile',
    'arguments_digest', 'sources', 'claims', 'iat', 'exp', 'jti',
    'threadlight/evidence', 'X-Evidence-Fingerprint']) assert.ok(contract.includes(field), field);
  assert.match(contract, /not.*access token/i);
  assert.match(contract, /not.*usable token/i);
  assert.doesNotMatch(contract, /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
});

test('expert guide introduces scenarios and connected data before configuration or code', () => {
  const text = read('docs/agent-governance-deep-dive.md');
  assert.ok(text.indexOf('### Four policy scenarios') < text.indexOf('### A tool policy can require signed input evidence'));
  assert.ok(text.indexOf('### Follow one proposal and its data') < text.indexOf('```json'));
  const data = text.split('### Follow one proposal and its data')[1].split('<!-- contract: returns-decision -->')[0];
  for (const name of ['RMA-EXAMPLE', 'expected_etag', 'arguments_digest', 'evidence_fingerprint',
    'action_hash', 'nonce', 'governance_operation_id', 'if_match_etag']) assert.ok(data.includes(name), name);
  for (const heading of ['### What changes while approval is pending?', '### Which code connects these records?']) {
    assert.ok(text.includes(heading), heading);
  }
  assert.match(text, /ETag is not an approval token/);
  assert.match(text, /old arguments.*old proof.*backend/is);
  assert.match(text, /renew.*same.*fingerprint/is);
  assert.match(text, /completed.*expired.*attestation/is);
});

test('expert execution diagram and JWT wire contract match the public evidence sequence', () => {
  const text = read('docs/agent-governance-deep-dive.md');
  const sequence = text.split('<!-- diagram: action-execution -->')[1].split('```')[1];
  assert.deepEqual([...sequence.matchAll(/participant (\w+) as/g)].map(m => m[1]), ['A', 'E', 'G', 'B', 'C']);
  assert.match(sequence, /A->>E:.*HTTPS/);
  assert.match(sequence, /E-->>A: Signed JWT or insufficient evidence/);
  assert.match(sequence, /A->>G:.*MCP.*JWT/);
  assert.match(sequence, /G->>B:.*fingerprint.*no JWT/i);
  assert.doesNotMatch(sequence, /E-+>>?G:/);
  const contract = text.match(/<details data-jwt-wire-contract>([\s\S]*?)<\/details>/)?.[1];
  assert.ok(contract, 'JWT contract next to the business proposal, not only in another document');
  for (const field of ['RS256', 'threadlight-evidence+jwt', 'kid', 'iss', 'aud', 'iat', 'exp', 'jti',
    'tid', 'holder', 'holder_client', 'sub', 'case_id', 'revision', 'action', 'purpose', 'profile',
    'arguments_digest', 'sources', 'claims', 'threadlight/evidence', 'X-Evidence-Fingerprint']) {
    assert.ok(contract.includes(field), field);
  }
  const params = JSON.parse(contract.match(/```json\n([\s\S]*?)\n```/)[1]);
  assert.equal(params.name, 'returns_apply_decision');
  assert.equal(params.arguments.expected_etag, '"r7"');
  assert.equal(params._meta['threadlight/evidence'], '<provider-issued signed JWT>');
  assert.deepEqual(Object.keys(params.arguments).sort(), ['case_id', 'decision', 'expected_etag', 'reason']);
});
