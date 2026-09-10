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
