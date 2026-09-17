const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const docs = path.resolve(__dirname, '../../docs');
const read = (name) => fs.readFileSync(path.join(docs, name), 'utf8');
const text = (source) => source.replace(/<style[\s\S]*?<\/style>|<script[\s\S]*?<\/script>/gi, '')
  .replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

test('Production explains active actors and the guided workbook leads to practical work', () => {
  const copy = text(read('production.html').split('<div class="topic-panel" id="actions-topic" data-topic-panel>')[1]
    .split('\n      </div>\n    </div>\n  </div>\n  <section id="production-review"')[0]);
  for (const phrase of ['Governed MCP gateway', 'Control plane', 'Human reviewer',
    'Business API', 'Try the guided workbook', 'not a payment']) {
    assert.ok(copy.includes(phrase), phrase);
  }
  assert.doesNotMatch(copy, /4\/4|14 tool calls|8 native responses|NOT PROVED|EXPIRED|CI is not all green|2026-09-|version [457]|Historical|Billing Issue/i);
  assert.match(text(read('agent-governance.html')), /Build your first governed workflow/);
});

test('commercial chapter copy separates the product architecture from instance validation status', () => {
  for (const name of ['index.html', 'funnel.html', 'production.html', 'agent-governance.html']) {
    const copy = text(read(name));
    assert.doesNotMatch(copy, /PRIVATE SELECTED ALLOW|NOT PROVED|S3 public hosted|S2 private BASIC|4\/4|2026-09-|100% secure/i, name);
    assert.doesNotMatch(copy, /the scorecard that signs|every tool call passes AGT|fully verified end.to.end/i, name);
  }
  const production = read('production.html');
  assert.match(production, /id="effect-authority"/);
  assert.match(production, /href="\.\/governance\.html"/);
  assert.match(production, /agent-governance-deep-dive\.md/);
  assert.match(production, /governed-returns-validation\.md/);
});

test('editorial contract keeps technical evidence intact and routes it outside commercial cards', () => {
  const specification = read('production-readiness-pages-spec.md');
  assert.ok(specification.includes('Commercial editorial direction'));
  assert.ok(specification.includes('not the commercial page contract'));
  const record = read('governed-returns-validation.md');
  const deepDive = read('agent-governance-deep-dive.md');
  assert.ok(record.includes('S2-PRIVATE-ALLOW-DENY-VERIFIED'));
  assert.ok(record.includes('no new private pending intent'));
  assert.ok(read('governed-returns-validation.md').includes('**4/4** call records'));
  assert.ok(read('governed-returns-validation.md').includes('user was unavailable'));
  assert.ok(deepDive.includes('## Contents'));
});

test('commercial page inherits the native site theme with readable local copy', () => {
  const html = read('agent-governance.html');
  assert.doesNotMatch(html, /--cp-|:root\s*\{|scoutTheme/);
  assert.match(read('assets/governed-workflow.css'), /\.governance-page \.wb-step dd\s*\{[^}]*color:\s*var\(--ink-1\)/);
});
