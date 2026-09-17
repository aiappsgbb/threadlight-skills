const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const page = path.join(root, 'docs/agent-governance.html');
const read = () => fs.readFileSync(page, 'utf8');

test('dedicated governance page uses existing site assets and accessible landmarks', () => {
  assert.ok(fs.existsSync(page), 'Implemented page is missing');
  const html = read();
  assert.match(html, /<html lang="en">/);
  assert.match(html, /href="assets\/site\.css\?v=[a-f0-9]+"/);
  assert.match(html, /src="assets\/site\.js\?v=[a-f0-9]+"/);
  assert.match(html, /<link rel="icon" href="data:image\/svg\+xml,/);
  assert.match(html, /href="#main"/);
  assert.match(html, /<main id="main"/);
  assert.equal((html.match(/<h1\b/g) || []).length, 1);
  for (const id of ['effect-authority', 'evidence', 'freshness', 'limits', 'next']) {
    assert.ok(html.includes(`id="${id}"`), id);
  }
  assert.match(html, /aria-current="page"/);
  assert.doesNotMatch(html, /react|unpkg\.com|cdn\.jsdelivr/i);
});

test('Production owns actor architecture while the workbook stays practical and free of lab status', () => {
  const html = fs.readFileSync(path.join(root, 'docs/production.html'), 'utf8');
  for (const value of [
    'Governed MCP gateway', 'Control plane', 'Human reviewer', 'Business API',
    'agent-governance-deep-dive.md', 'governed-returns-validation.md',
  ]) assert.ok(html.includes(value), value);
  assert.ok(read().includes('Build your first governed workflow'));
  assert.doesNotMatch(html, /4\/4|NOT PROVED|EXPIRED|2026-09-|CI is not all green/);
  const record = fs.readFileSync(path.join(root, 'docs/governed-returns-validation.md'), 'utf8');
  for (const fact of ['S2-PRIVATE-ALLOW-DENY-VERIFIED', '4/4',
    '2026-09-15T11:12:28.719119+00:00', 'no new private pending intent']) {
    assert.ok(record.includes(fact), fact);
  }
  assert.doesNotMatch(html, /\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/i);
  assert.doesNotMatch(html, /\/Users\/|\/home\/[^/\s]+\/\.azure/);
  assert.doesNotMatch(html, /https:\/\/[a-z0-9-]+\.(?:azurecr\.io|vault\.azure\.net|documents\.azure\.com|blob\.core\.windows\.net|azurecontainerapps\.io|services\.ai\.azure\.com)/i);
});

test('all dedicated page local links resolve and README exposes the implemented page', () => {
  const html = read();
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  for (const [, link] of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
    if (/^(https?:|data:)/.test(link)) continue;
    const [file, fragment] = link.split('#');
    if (!file) assert.ok(ids.has(fragment), link);
    else assert.ok(fs.existsSync(path.join(root, 'docs', file.split('?')[0])), link);
  }
  assert.ok(fs.readFileSync(path.join(root, 'README.md'), 'utf8').includes('](docs/agent-governance.html)'));
});

test('Pages specification distinguishes the implemented dedicated page from legacy copy work', () => {
  const spec = fs.readFileSync(path.join(root, 'docs/production-readiness-pages-spec.md'), 'utf8');
  assert.ok(spec.includes('](agent-governance.html)'));
  assert.ok(spec.includes('Dedicated page implemented'));
  assert.ok(spec.includes('not a production Pages deployment'));
});

test('human workflow explains native Outlook and keeps exact dated proof in engineering records', () => {
  const html = fs.readFileSync(path.join(root, 'docs/production.html'), 'utf8');
  for (const term of ['Outlook', 'one-use', 'Human reviewer', 'Control plane']) {
    assert.ok(html.includes(term), term);
  }
  assert.ok(read().includes('id="human-decisions"'));
  const record = fs.readFileSync(path.join(root, 'docs/governed-returns-validation.md'), 'utf8');
  assert.ok(record.includes('S2-NATIVE-OUTLOOK-HUMAN-RESUME-REPLAY'));
  assert.ok(record.includes('09:50:18'));
  assert.ok(record.includes('09:51:33'));
  const deep = fs.readFileSync(path.join(root, 'docs/native-outlook-approval-architecture.md'), 'utf8');
  for (const term of ['outlook-native/v1', 'approval_review_required', 'prepared', 'sending', 'sent',
    'home tenant', 'not OBO', 'English', 'Italian', 'Reader', 'ambiguous', 'same native session']) {
    assert.ok(deep.includes(term), term);
  }
});
