const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const visual = () => read('docs/production.html')
  .match(/<!-- governed-flow:start -->([\s\S]*?)<!-- governed-flow:end -->/)?.[1];

test('product illustration is static-first with separate pre-effect and business records', () => {
  const html = visual();
  assert.ok(html, 'compact decision path in Production');
  for (const token of ['Agent proposes', 'Gateway checks', 'Outlook review',
    'Backend', 'Decision + audit', 'central audit ACK', 'No business effect',
    'No model, mailbox or backend is connected',
    'Allowed', 'Blocked', 'Human review']) assert.ok(html.includes(token), token);
  for (const node of ['proposal', 'checks', 'deny', 'review', 'fresh', 'ack', 'effect', 'result']) {
    assert.ok(html.includes(`data-flow-node="${node}"`), node);
  }
  assert.ok(html.indexOf('data-flow-node="ack"') < html.indexOf('data-flow-node="effect"'));
  assert.ok(html.indexOf('data-flow-node="effect"') < html.indexOf('data-flow-node="result"'));
  assert.match(html, /data-flow-controls hidden/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /<svg[^>]*class="wf-diagram"/);
  assert.match(html, /<path[^>]*data-flow-edge="checks:ack"[^>]*marker-end=/);
  assert.match(html, /<path[^>]*data-flow-edge="checks:deny"[^>]*marker-end=/);
  assert.doesNotMatch(html, /data-flow-edge="deny:(?:ack|effect|result)"/);
  assert.match(html, /role="tablist"/);
  assert.match(html, /data-flow-progress/);
  assert.ok((html.match(/data-component-icon/g) || []).length >= 5);
  assert.match(read('docs/production.html'), /href="\.\/agent-governance\.html#overview"[^>]*>Try the guided workbook/);
  assert.doesNotMatch(read('docs/agent-governance.html'), /data-flow-node=/);
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
  const html = read('docs/agent-governance.html') + read('docs/production.html');
  const script = read('docs/assets/governed-workflow.js');
  const css = read('docs/assets/governed-workflow.css');
  for (const name of ['governed-workflow.js', 'governed-workflow.css']) {
    const token = crypto.createHash('sha256').update(read(`docs/assets/${name}`)).digest('hex').slice(0, 8);
    for (const page of ['docs/agent-governance.html', 'docs/production.html']) {
      assert.ok(read(page).includes(`assets/${name}?v=${token}`), `${page}: ${name}`);
    }
    assert.ok(read('docs/ci/sync_cache_bust.py').includes(`"${name}"`), `registered ${name}`);
  }
  assert.match(script, /prefers-reduced-motion/);
  assert.match(script, /visibilitychange/);
  assert.match(script, /clearTimeout/);
  assert.doesNotMatch(script, /\bfetch\(|XMLHttpRequest|WebSocket|setInterval|localStorage|sessionStorage|innerHTML/);
  assert.match(css, /prefers-reduced-motion/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|infinite|@import/i);
  assert.match(css, /focus-visible/);
  assert.match(css, /var\(--ink-/);
  assert.match(css, /var\(--chapter-accent/);
  assert.doesNotMatch(`${html}\n${css}`, /--cp-|scoutTheme|--(?:sans|serif|bg-0|ink-0|accent):/);
});

test('approved workbook bytes and existing workbook permalinks are immutable', () => {
  const bytes = fs.readFileSync(path.join(root, 'docs/first-governed-workflow.md'));
  const blob = crypto.createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
  assert.equal(blob, '1d6ed17fa77e14e22633c5fdf56e7e45b814e312');
  for (const [, href] of read('docs/agent-governance.html').matchAll(/href="([^"]*first-governed-workflow\.md[^"]*)"/g)) {
    assert.ok(href.includes('/blob/7782eba93754fb7cff85336d3f4a8703892bad76/'), href);
  }
});

test('governance joins native chapter navigation instead of creating a second site hierarchy', () => {
  const html = read('docs/agent-governance.html');
  const nav = (page) => page.match(/<nav class="nav"[^>]*>([\s\S]*?)<\/nav>/)[1]
    .replace(/aria-current="(?:page|location)"/g, '');
  assert.equal(nav(html), nav(read('docs/production.html')));
  assert.match(html, /chapter-experience/);
  assert.match(html, /class="cx-journey"/);
  assert.match(html, /class="cx-chapter-links"/);
  assert.match(html, /class="cx-chapter-index"/);
  for (const asset of ['site-map.js', 'chapter-experience.js', 'chapter-experience.css']) {
    assert.ok(html.includes(`assets/${asset}?v=`), asset);
  }
  assert.match(html, /href="\.\/production\.html" aria-current="location"/);
  assert.doesNotMatch(html, /get\("scoutTheme"\)|data-theme=|:root\s*\{/);
});
