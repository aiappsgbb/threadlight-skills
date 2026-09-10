const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const docs = path.resolve(__dirname, '../../docs');
const read = slug => fs.readFileSync(path.join(docs, `${slug}.html`), 'utf8');
const primary = [
  { slug: 'index', title: 'Home' },
  { slug: 'basics', title: 'Basics' },
  { slug: 'funnel', title: 'Build' },
  { slug: 'case-study', title: 'Case study' },
  { slug: 'production', title: 'Production' },
];
const groups = [
  { id: 'basics', title: 'Basics', entry: 'basics', slugs: ['basics'] },
  { id: 'build', title: 'Build', entry: 'funnel', slugs: ['funnel', 'blueprint', 'industries', 'workbook'] },
  { id: 'example', title: 'Case study', entry: 'case-study', slugs: ['case-study'] },
  { id: 'production', title: 'Production', entry: 'production', slugs: ['production', 'governance', 'customize', 'self-improving'] },
];
const existing = ['index', ...groups.flatMap(group => group.slugs).filter(slug => slug !== 'basics')];
const text = html => html.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').trim();
const links = html => [...html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)].map(([, attrs, body]) => ({
  href: attrs.match(/href="([^"]+)"/)?.[1],
  title: text(body),
  current: attrs.match(/aria-current="([^"]+)"/)?.[1],
}));

for (const slug of existing) {
  test(`${slug}: static primary navigation uses the approved hierarchy`, () => {
    const nav = read(slug).match(/<nav class="nav"[^>]*>([\s\S]*?)<\/nav>/)?.[1];
    assert.ok(nav, 'primary navigation exists without JavaScript');
    const owner = groups.find(group => group.slugs.includes(slug));
    assert.deepEqual(links(nav), primary.map(item => ({
      href: `./${item.slug}.html`,
      title: item.title,
      current: item.slug === slug ? 'page' : item.slug === owner?.entry ? 'location' : undefined,
    })));
  });
}

test('site map is one portable, uniquely owned source for the grouped directory', () => {
  const siteMap = require('../../docs/assets/site-map.js');
  assert.deepEqual(siteMap.primary, primary);
  assert.deepEqual(siteMap.groups.map(group => ({
    id: group.id, title: group.title, entry: group.entry, slugs: group.pages.map(page => page.slug),
  })), groups);
  const pages = siteMap.groups.flatMap(group => group.pages);
  assert.equal(new Set(pages.map(page => page.slug)).size, pages.length);
  assert.deepEqual([...new Set(pages.map(page => page.slug))].sort(), [...existing.filter(slug => slug !== 'index'), 'basics'].sort());
  for (const page of pages) {
    assert.ok(page.title && page.description, `directory description for ${page.slug}`);
    assert.ok(fs.existsSync(path.join(docs, `${page.slug}.html`)), `reachable ${page.slug}`);
  }
  const browser = { window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(docs, 'assets/site-map.js'), 'utf8'), browser);
  assert.deepEqual(JSON.parse(JSON.stringify(browser.window.ThreadlightSiteMap)), siteMap);
});

test('non-Home pages load the map before the directory; Home keeps its protected scripts', () => {
  for (const slug of [...existing.filter(slug => slug !== 'index'), 'basics']) {
    const html = read(slug);
    const scripts = [...html.matchAll(/<script\b[^>]*src="([^"]+)"/g)].map(match => match[1].split('?')[0]);
    assert.equal(scripts.filter(src => src === 'assets/site-map.js').length, 1, slug);
    assert.ok(scripts.indexOf('assets/site-map.js') < scripts.indexOf('assets/chapter-experience.js'), slug);
  }
  assert.doesNotMatch(read('index'), /<script\b[^>]*src="[^"]*(?:site-map|chapter-experience)/);
});

test('every existing chapter has a compact parent and suggested next step', () => {
  for (const slug of existing.filter(slug => slug !== 'index')) {
    const html = read(slug);
    const cue = html.match(/<div class="cx-journey"[^>]*>([\s\S]*?)<\/div>/)?.[1];
    assert.ok(cue, `${slug}: context cue`);
    assert.match(cue, /<nav\b[^>]*aria-label="Breadcrumb"/, slug);
    assert.match(cue, /aria-current="page"/, slug);
    assert.match(cue, /<a\b[^>]*class="cx-journey-next"/, slug);
    const owner = groups.find(group => group.slugs.includes(slug));
    const parent = owner.entry === slug ? 'index' : owner.entry;
    assert.ok(links(cue).some(link => link.href === `./${parent}.html`), `${slug}: parent ${parent}`);
    for (const link of links(cue)) {
      assert.ok(fs.existsSync(path.join(docs, link.href)), `${slug}: reachable ${link.href}`);
    }
  }
});

test('Build names the lifecycle entry without changing its six phase routes', () => {
  const html = read('funnel');
  assert.match(html, /<title>Build — Threadlight<\/title>/);
  assert.match(html, /<meta property="og:title" content="Build — Threadlight">/);
  assert.match(html, /<meta name="twitter:title" content="Build — Threadlight">/);
  assert.match(html, /Build · follow one brief/);
  assert.equal([...html.matchAll(/data-phase-name/g)].length, 6);
});

test('Home next steps introduce Basics before the Build lifecycle and historical example', () => {
  const navigation = read('index').match(/<nav aria-label="Explore beyond the demo">([\s\S]*?)<\/nav>/)?.[1];
  assert.ok(navigation);
  assert.deepEqual(links(navigation).map(link => link.href), ['./basics.html', './funnel.html', './case-study.html']);
});
