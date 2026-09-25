const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const docsDir = path.join(__dirname, '../../docs');
const htmlFiles = fs.readdirSync(docsDir).filter((name) => name.endsWith('.html'));
// Declare observed Pages outputs; an arbitrary Markdown source is not publication evidence.
const generatedPages = new Map([['citadel-governance.html', 'citadel-governance.md']]);

const read = (name) => fs.readFileSync(path.join(docsDir, name), 'utf8');

function ids(text) {
  return new Set([...text.matchAll(/\sid=["']([^"']+)["']/g)].map(([, id]) => id));
}

function publishedTargets() {
  const targets = new Map(htmlFiles.map((name) => [name, ids(read(name))]));
  for (const [output, source] of generatedPages) {
    const text = read(source).replace(/^(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\1\s*$/gm, '');
    const fragments = ids(text);
    for (const [, heading] of text.matchAll(/^#{1,6} (.+)$/gm)) {
      fragments.add(heading.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-'));
    }
    targets.set(output, fragments);
  }
  return targets;
}

function assertLocalLink(source, href, idMap) {
  if (/^(?:https?:|mailto:|data:|javascript:)/.test(href)) return;
  const [rawTarget, fragment] = href.split('#', 2);
  const targetPath = rawTarget.split('?', 1)[0];
  const target = targetPath ? path.posix.normalize(path.posix.join(path.posix.dirname(source), targetPath)) : source;
  if (!target.endsWith('.html')) return;
  assert.ok(idMap.has(target), `${source}: missing target ${target}`);
  if (fragment) {
    assert.ok(idMap.get(target).has(fragment), `${source}: missing fragment ${target}#${fragment}`);
  }
}

test('docs html local links only point to published pages and fragments that exist', () => {
  const idMap = publishedTargets();
  for (const source of htmlFiles) {
    for (const match of read(source).matchAll(/href=["']([^"']+)["']/g)) {
      assertLocalLink(source, match[1], idMap);
    }
  }
});

test('declared Jekyll Markdown pages resolve through their generated HTML and real heading fragments', () => {
  const targets = publishedTargets();
  for (const href of ['./citadel-governance.html', './citadel-governance.html#policy-lifecycle',
    './citadel-governance.html?from=governance#contracts-and-canonical-joins']) {
    assert.doesNotThrow(() => assertLocalLink('governance.html', href, targets), href);
  }
  assert.throws(() => assertLocalLink('governance.html', './citadel-governance.html#invented', targets),
    /missing fragment citadel-governance.html#invented/);
});

test('missing pages and excluded or non-published paths cannot alias a published filename', () => {
  const targets = publishedTargets();
  assert.throws(() => assertLocalLink('governance.html', './missing-page.html', targets), /missing target/);
  for (const href of ['superpowers/governance.html', 'vendor/bundle/governance.html',
    '../governance.html', 'missing-folder/governance.html']) {
    assert.throws(() => assertLocalLink('governance.html', href, targets), /missing target/, href);
  }
  assert.throws(() => assertLocalLink('governance.html', './governance.html#invented', targets),
    /missing fragment governance.html#invented/);
});
