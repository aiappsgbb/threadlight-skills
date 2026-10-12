const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { governanceHistory } = require('./helpers/governance-history');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

test('the new deep-dive extension is bounded and leaves its historical baseline intact', () => {
  const { extensions } = governanceHistory(read('docs/agent-governance-deep-dive.md'));
  assert.equal(extensions.length, 1);
  assert.ok(extensions[0].split(/\s+/).length <= 625);
  assert.match(extensions[0], /not a security boundary/);
  assert.match(extensions[0], /GPT-5\.4/);
});

test('existing governance chapter separates illustration, model execution and deterministic PEP proof', () => {
  const html = read('docs/governance.html');
  assert.match(html, /id="signed-evidence"/);
  for (const kind of ['illustrative', 'model-observation', 'deterministic-pep']) {
    assert.ok(html.includes(`data-proof-kind="${kind}"`));
  }
  for (const text of ['GPT-5.4', 'Evidence Provider', 'synthetic', 'not payment', 'local PEP']) {
    assert.ok(html.includes(text), text);
  }
  assert.match(html, /Recorded observations, not a live replay/);
  assert.match(html, /assets\/evidence\/signed-evidence-20260922\.json/);
  assert.doesNotMatch(html, /immune to jailbreaks|universally protected|documents certified true/i);
});

test('published experiment record contains only sanitized observed results and source binding', () => {
  const raw = read('docs/assets/evidence/signed-evidence-20260922.json');
  const report = JSON.parse(raw);
  assert.equal(report.schema, 'threadlight-public-evidence-experiment/v1');
  assert.equal(report.scope, 'Azure-model-local-PEP-synthetic-business-store');
  assert.ok(report.runs.some((run) => run.model_execution.model === 'gpt-5.4'));
  for (const run of report.runs) {
    assert.match(run.source_commit, /^[0-9a-f]{40}$/);
    assert.match(run.source_digest, /^sha256:[0-9a-f]{64}$/);
    assert.match(run.configuration_digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(run.model_execution.status, 'completed');
    assert.equal(run.model_execution.turns.length, 5);
    assert.equal(run.business_effect_count, 1);
    assert.equal(run.cases.length, 13);
    assert.ok(run.cases.every((row) => row.governance_receipts.length > 0));
    assert.equal(run.cases.find((row) => row.case === 'positive-replay').business_effects, 0);
  }
  assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\./);
  assert.doesNotMatch(raw, /\.azure\.com|\.azure\.net|\/subscriptions\/|\/Users\/|PRIVATE KEY/i);
  const aliasHashes = new Set(['0674a3040cc01c5d6bc47f63af1df72b37a68af626a0866d35b4d9c47597ed77',
    '0f447dc02c09d52bc7c21f49147b37ed1914e136b8493da37ab2f9db07d225a7',
    'f1d654b62242e415e8e2bc42714013e0823eb1772d8112e7bcdf7dbda364e0c5']);
  const words = raw.toLowerCase().match(/[a-z0-9]+/g) || [];
  assert.ok(!words.some((w) => aliasHashes.has(crypto.createHash('sha256').update(w).digest('hex'))));
});
