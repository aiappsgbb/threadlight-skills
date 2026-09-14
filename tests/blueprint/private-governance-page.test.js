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

test('governance page distinguishes actual private effect proof from historical and blocked paths', () => {
  const html = read();
  for (const value of [
    'The model proposes.', 'The system authorizes.', 'ACS', 'OPA',
    'before the effect', 'different identity', 'ETag', 'two responses',
    'Four call records', 'Private governed runner', '14 September 2026',
    'ALLOW', 'DENY', 'Inline read ACK', 'Post-run ledger',
    'Public governed runner', 'Historical', 'Private BASIC', 'Billing Issue',
    'No new private pending intent', 'not whole-agent attestation',
    '2026-09-15T11:12:28.719119+00:00', '2026-09-15T11:32:54.753384+00:00',
    '2026-09-14T10:26:48.991423+00:00', '2026-09-14T12:03:41.859581+00:00',
    'Human approval', 'Office 365 consent', 'CI is not all green',
    'trusted host', 'not financial settlement',
  ]) assert.ok(html.includes(value), value);
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
