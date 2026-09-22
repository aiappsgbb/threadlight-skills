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
    assert.deepEqual(route.slice(0, 3), ['proposal', 'proof', 'checks']);
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
