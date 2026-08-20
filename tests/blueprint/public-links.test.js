const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const docsDir = path.join(__dirname, '../../docs');
const htmlFiles = fs.readdirSync(docsDir).filter((name) => name.endsWith('.html'));

const read = (name) => fs.readFileSync(path.join(docsDir, name), 'utf8');

function ids(text) {
  return new Set([...text.matchAll(/\sid=["']([^"']+)["']/g)].map(([, id]) => id));
}

test('docs html local links only point to published pages and fragments that exist', () => {
  const idMap = new Map(htmlFiles.map((name) => [name, ids(read(name))]));

  for (const source of htmlFiles) {
    for (const match of read(source).matchAll(/href=["']([^"']+)["']/g)) {
      const href = match[1];
      if (/^(?:https?:|mailto:|data:|javascript:)/.test(href)) continue;

      const [rawTarget, fragment] = href.split('#', 2);
      const targetPath = rawTarget.split('?', 1)[0];
      const target = targetPath ? path.basename(targetPath) : source;
      if (!target.endsWith('.html')) continue;

      assert.ok(idMap.has(target), `${source}: missing target ${target}`);
      if (fragment) {
        assert.ok(idMap.get(target).has(fragment), `${source}: missing fragment ${target}#${fragment}`);
      }
    }
  }
});
