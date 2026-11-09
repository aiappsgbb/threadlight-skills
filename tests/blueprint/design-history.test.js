const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const records = [
  'docs/superpowers/specs/2026-08-21-threadlight-public-site-clarity-design.md',
  'docs/superpowers/plans/2026-08-21-public-truth-contract.md',
  'docs/superpowers/plans/2026-08-21-complete-approach-discoverability.md',
];

test('August approvals remain historical records rather than current execution instructions', () => {
  for (const file of records) {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    assert.match(text.slice(0, 2200), /Historical approval record/, file);
    assert.match(text.slice(0, 2200), /2026-09-10/, file);
    assert.match(text.slice(0, 2200), /site-map\.js/, file);
    assert.match(text.slice(0, 2200), /not fresh acceptance evidence/, file);
    for (const [, href] of text.slice(0, 2200).matchAll(/\]\(([^)]+)\)/g)) {
      if (/^https?:/.test(href)) continue;
      assert.ok(fs.existsSync(path.resolve(root, path.dirname(file), href)), `${file}: ${href}`);
    }
  }
});

test('docs/superpowers keeps only design records that current sources still cite', () => {
  const dir = path.join(root, 'docs/superpowers');
  const files = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else files.push(path.relative(root, p).split(path.sep).join('/'));
    }
  };
  walk(dir);
  const readme = fs.readFileSync(path.join(dir, 'README.md'), 'utf8');
  assert.match(readme, /8670e16a/, 'README must point to the last commit holding the full planning history');
  const sources = [];
  const collect = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      if (['.git', 'node_modules', 'superpowers'].includes(e.name)) continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) collect(p);
      else if (/\.(md|py|js|mjs|ya?ml)$/.test(e.name) && !p.endsWith('CHANGELOG.md')) sources.push(fs.readFileSync(p, 'utf8'));
    }
  };
  for (const top of ['skills', 'tests', 'docs', '.github']) collect(path.join(root, top));
  const corpus = sources.join('\n');
  for (const file of files) {
    if (file === 'docs/superpowers/README.md') continue;
    assert.ok(corpus.includes(path.basename(file)), `${file} is no longer cited; it lives in git history (see README)`);
  }
});
