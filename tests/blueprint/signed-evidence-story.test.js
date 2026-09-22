const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

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
  assert.doesNotMatch(raw, /\.azure\.com|\.azure\.net|\/subscriptions\/|\/Users\/|PRIVATE KEY|fruocco/i);
});
