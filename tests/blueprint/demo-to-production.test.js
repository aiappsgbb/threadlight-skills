const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const text = html => html.replace(/<[^>]*>/g, ' ').replace(/&[a-z]+;/g, ' ').replace(/\s+/g, ' ');
const PROOF = ['Proven live', 'Proven live, with limits', 'Offline only'];
const REPO = 'https://github.com/aiappsgbb/threadlight-skills/blob/main/';

const capabilities = [
  'recommend-only', 'governed-write', 'voice', 'private-network', 'identity',
  'performance', 'tool-allowlist', 'pilot-value', 'eu-ai-act', 'finops', 'agent-topology',
];

test('From demo to production lists every capability once, with a proof level, its open gap and its doc', () => {
  const html = read('docs/demo-to-production.html');
  const rows = [...html.matchAll(/<tr data-capability="([^"]+)"[^>]*>([\s\S]*?)<\/tr>/g)];
  assert.deepEqual(rows.map(([, id]) => id), capabilities);
  for (const [, id, row] of rows) {
    const proof = row.match(/data-proof="([^"]+)"><span[^>]*><\/span>([^<]+)<\/span>/);
    assert.ok(proof, `${id}: proof label`);
    assert.ok(PROOF.includes(proof[2].trim()), `${id}: proof label "${proof[2]}" is from the fixed set`);
    assert.match(text(row), /Not proven:|No runtime|No legal review/, `${id}: states what is not proven`);
    const links = [...row.matchAll(/href="([^"]+)"/g)].map(([, href]) => href);
    assert.ok(links.length >= 1, `${id}: links its doc`);
    for (const href of links.filter(href => href.startsWith(REPO))) {
      const file = href.slice(REPO.length).split('#')[0];
      assert.ok(fs.existsSync(path.join(root, file)), `${id}: ${file} exists`);
    }
  }
  const figures = [...html.matchAll(/<figure class="d2p-diagram"[\s\S]*?<\/figure>/g)];
  assert.equal(figures.length, 1, 'exactly one diagram');
  for (const id of capabilities) assert.match(figures[0][0], new RegExp(`href="#cap-${id}"`), `diagram links ${id}`);
});

test('the overview is reachable from the README, the docs index and the Basics directory', () => {
  const page = 'https://aiappsgbb.github.io/threadlight-skills/demo-to-production.html';
  assert.ok(read('README.md').includes(page));
  assert.ok(read('docs/README.md').includes(page));
  const siteMap = require('../../docs/assets/site-map.js');
  const basics = siteMap.groups.find(group => group.id === 'basics').pages.map(p => p.slug);
  assert.ok(basics.includes('demo-to-production'));
});

test('proof claims on the overview match the recorded limits', () => {
  const body = text(read('docs/demo-to-production.html'));
  for (const limit of [
    /stand-in/i,                              // private profile: hub and customer gateway were stand-ins
    /synthetic/i,                             // voice: only synthetic TTS callers
    /real phone/i,
    /Agent 365/,                              // identity: Conditional Access licence (E7 or Agent 365 + P1)
    /no GHCP Outlook decision/i,              // governed write: email channel not evidenced on GHCP
    /never a compliance claim|does not state compliance/i,
  ]) assert.match(body, limit);
  assert.doesNotMatch(body, /\bcertif(ies|ied)\b(?! production)/i);
});

test('the governed-write kit states that the Outlook decision is not evidenced on GHCP', () => {
  const kit = read('examples/governed-write-ghcp/README.md').replace(/\s+/g, ' ');
  assert.match(kit, /No GHCP Outlook decision is evidenced yet/);
});
