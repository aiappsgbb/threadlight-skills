const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const visual = () => read('docs/agent-governance.html')
  .match(/<section\b[^>]*id="workflow-in-action"[\s\S]*?<\/section>/)?.[0];

test('product illustration is static-first with separate pre-effect and business records', () => {
  const html = visual();
  assert.ok(html, 'compact section beside the existing guide CTA');
  for (const token of ['Agent proposes', 'Policy decides', 'Person authorizes',
    'Backend checks', 'Record connects', 'central audit ACK', 'No business effect',
    'No model, mailbox or backend is connected', 'Open the workbook',
    'Normal return', 'Invalid proposal', 'Supervisor handoff']) assert.ok(html.includes(token), token);
  for (const node of ['proposal', 'checks', 'allow', 'deny', 'review', 'fresh', 'ack', 'effect', 'result']) {
    assert.ok(html.includes(`data-flow-node="${node}"`), node);
  }
  assert.ok(html.indexOf('data-flow-node="ack"') < html.indexOf('data-flow-node="effect"'));
  assert.ok(html.indexOf('data-flow-node="effect"') < html.indexOf('data-flow-node="result"'));
  assert.match(html, /data-flow-controls hidden/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /first-governed-workflow\.md#phase-3-/);
  assert.match(html, /first-governed-workflow\.md#phase-5-/);
  const workbookLinks = [...html.matchAll(/href="([^"]*first-governed-workflow\.md[^"]*)"/g)];
  assert.equal(workbookLinks.length, 3);
  for (const [, href] of workbookLinks) {
    assert.ok(href.includes('/blob/7782eba93754fb7cff85336d3f4a8703892bad76/'),
      'the workbook must be reachable before a merge, not an absent main file');
  }
  assert.doesNotMatch(html, /<iframe|<form|<input|<canvas|pass.fail|payment executed/i);
});

test('all executable illustration paths acknowledge authorization before effects', () => {
  const { scenarios } = require('../../docs/assets/governed-workflow.js');
  assert.deepEqual(Object.keys(scenarios), ['normal', 'invalid', 'supervisor']);
  for (const name of ['normal', 'supervisor']) {
    const steps = scenarios[name].steps;
    assert.ok(steps.indexOf('checks') < steps.indexOf('ack'), name);
    assert.ok(steps.indexOf('ack') < steps.indexOf('effect'), name);
    assert.ok(steps.indexOf('effect') < steps.indexOf('result'), name);
    assert.equal(steps.filter((step) => step === 'effect').length, 1);
  }
  assert.deepEqual(scenarios.invalid.steps, ['proposal', 'checks', 'deny']);
  assert.ok(scenarios.supervisor.steps.indexOf('review') < scenarios.supervisor.steps.indexOf('fresh'));
  assert.ok(scenarios.supervisor.steps.indexOf('fresh') < scenarios.supervisor.steps.indexOf('ack'));
});

test('visual assets stay local, scoped, bounded and cache-busted', () => {
  const html = read('docs/agent-governance.html');
  const script = read('docs/assets/governed-workflow.js');
  const css = read('docs/assets/governed-workflow.css');
  for (const name of ['governed-workflow.js', 'governed-workflow.css']) {
    const token = crypto.createHash('sha256').update(read(`docs/assets/${name}`)).digest('hex').slice(0, 8);
    assert.ok(html.includes(`assets/${name}?v=${token}`), name);
    assert.ok(read('docs/ci/sync_cache_bust.py').includes(`"${name}"`), `registered ${name}`);
  }
  assert.match(script, /prefers-reduced-motion/);
  assert.match(script, /visibilitychange/);
  assert.match(script, /clearTimeout/);
  assert.doesNotMatch(script, /\bfetch\(|XMLHttpRequest|WebSocket|setInterval|localStorage|sessionStorage|innerHTML/);
  assert.match(css, /prefers-reduced-motion/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|infinite|@import/i);
  assert.match(css, /focus-visible/);
  assert.match(css, /var\(--cp-/);
});
