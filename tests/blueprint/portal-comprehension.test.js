const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const exists = file => fs.existsSync(path.join(root, file));
const siteMap = require('../../docs/assets/site-map.js');
const plain = html => html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ');

test('Home names the product before the demo and keeps the demo headline', () => {
  const html = read('docs/index.html');
  const title = html.match(/<title>([^<]+)<\/title>/)[1];
  assert.match(title, /^Threadlight —/);
  assert.match(title, /working (?:agent )?pilot/i);
  for (const meta of ['og:title', 'twitter:title']) {
    const value = html.match(new RegExp(`(?:property|name)="${meta}"\\s+content="([^"]+)"`))[1];
    assert.equal(value, title, meta);
  }
  const band = html.match(/<section class="home-what"[\s\S]*?<\/section>/)?.[0];
  assert.ok(band, 'home-what band');
  assert.ok(html.indexOf('class="home-what"') < html.indexOf('id="demo-h"'), 'band precedes the demo headline');
  const text = plain(band);
  assert.match(text, /Threadlight is/);
  assert.match(text, /GitHub Copilot/);
  assert.match(text, /Microsoft Foundry/);
  assert.match(text, /hosted agent/i);
  assert.match(text, /not an official Microsoft product/i);
  assert.match(text, /does not certify production/i);
  for (const href of ['./basics.html', './workbook.html', './production.html', './glossary.html']) {
    assert.ok(band.includes(`href="${href}"`), href);
  }
});

test('Home has a static Explore directory that mirrors the site map', () => {
  const html = read('docs/index.html');
  const directory = html.match(/<details class="home-directory"[\s\S]*?<\/details>/)?.[0];
  assert.ok(directory, 'home directory');
  assert.ok(html.indexOf('class="home-directory"') < html.indexOf('</header>'), 'directory lives in the masthead');
  assert.match(directory, /<summary[^>]*aria-label="Explore all Threadlight chapters"[^>]*>Explore<\/summary>/);
  const hrefs = [...directory.matchAll(/href="\.\/([a-z-]+)\.html"/g)].map(m => m[1]);
  assert.deepEqual(hrefs, siteMap.groups.flatMap(group => group.pages.map(page => page.slug)));
  for (const group of siteMap.groups) {
    assert.ok(directory.includes(`data-site-group="${group.id}"`), group.id);
    for (const page of group.pages) assert.ok(plain(directory).includes(page.title), page.title);
  }
});

const GLOSSARY = {
  'skill': /reusable/i,
  'safe': /method/i,
  'acs': /policy decision point|PDP/i,
  'pdp': /decid/i,
  'pep': /native host or gateway/i,
  'agent-hooks': /intercept/i,
  'agt': /toolkit/i,
  'assert': /assurance/i,
  'binding': /selected/i,
  'local-14': /local/i,
  'ctk': /conformance/i,
  'collector': /governance_probe_noop/,
  'citadel': /optional/i,
  'spoke': /hub/i,
  'hosted-agent': /default/i,
  'prompt-agent': /opt-in|explicit/i,
  'mcp': /Model Context Protocol/,
  'otel': /OpenTelemetry/,
  'spec': /SPEC/,
  'agentops': /optional|opt-in/i,
  'hitl': /human/i,
};

test('Glossary defines the shared vocabulary within the technical boundaries', () => {
  assert.ok(exists('docs/glossary.html'));
  const html = read('docs/glossary.html');
  assert.match(html, /<title>Glossary — Threadlight<\/title>/);
  for (const [id, truth] of Object.entries(GLOSSARY)) {
    const entry = html.match(new RegExp(`<dt id="${id}"[\\s\\S]*?</dd>`))?.[0];
    assert.ok(entry, `glossary entry ${id}`);
    assert.match(plain(entry), truth, id);
  }
  assert.doesNotMatch(plain(html), /whole-agent governance is (?:proved|guaranteed)|certif(?:ies|ied) production/i);
  const basics = siteMap.groups.find(group => group.id === 'basics');
  assert.ok(basics.pages.some(page => page.slug === 'glossary'));
});

test('Jargon-heavy chapters link to the glossary', () => {
  for (const page of ['production', 'governance', 'agent-governance', 'case-study', 'workbook']) {
    assert.match(read(`docs/${page}.html`), /href="\.\/glossary\.html(?:#[a-z0-9-]+)?"/, page);
  }
});

test('Production chapters have distinguishable names', () => {
  const pages = Object.fromEntries(siteMap.groups.flatMap(group => group.pages).map(page => [page.slug, page]));
  assert.equal(pages.production.title, 'Production');
  assert.match(pages.production.description, /^Start here/);
  assert.equal(pages['agent-governance'].title, 'Release guide');
  assert.equal(pages['self-improving'].title, 'Self-improving CI');
  const titles = Object.values(pages).map(page => page.title);
  assert.equal(new Set(titles).size, titles.length);
  assert.match(read('docs/agent-governance.html'), /<title>Release guide — /);
  for (const file of fs.readdirSync(path.join(root, 'docs')).filter(name => name.endsWith('.html'))) {
    assert.doesNotMatch(read(`docs/${file}`), /Production guide|Next: Improve\b/, file);
  }
});

test('docs index and README give people a short entry path', () => {
  assert.ok(exists('docs/README.md'));
  const index = read('docs/README.md');
  const excluded = ['solution-review-2026-09-15.md', 'production-readiness-pages-spec.md', 'README.md'];
  for (const file of fs.readdirSync(path.join(root, 'docs')).filter(name => name.endsWith('.md'))) {
    if (!excluded.includes(file)) assert.ok(index.includes(`(${file}`) || index.includes(`(./${file}`), `index lists ${file}`);
  }
  assert.match(index, /https:\/\/aiappsgbb\.github\.io\/threadlight-skills\/glossary\.html/);
  const readme = read('README.md');
  const head = readme.split('\n').slice(0, 30).join('\n');
  assert.match(head, /Threadlight is/);
  assert.match(head, /docs\/README\.md/);
  assert.match(head, /not an official Microsoft product/i);
  const install = readme.indexOf('\n## Install');
  assert.ok(install > 0 && install < readme.indexOf('Foundry-only agents (non-negotiable)'), 'Install precedes the agent rules');
  assert.ok(readme.slice(0, install).split('\n').length < 140, 'Install is near the top');
});

test('Citadel is optional and AGT is described as a toolkit', () => {
  const readiness = read('docs/production-readiness.md');
  assert.doesNotMatch(readiness, /default · recommended/);
  assert.doesNotMatch(readiness, /recommended posture for any new pilot/);
  assert.match(readiness, /Citadel spoke \*\(optional/);
  assert.doesNotMatch(readiness, /AGT-only/);
  assert.match(read('THREADLIGHT.md').replace(/\s+/g, ' '), /enterprise target when the customer already runs or is provisioning a Citadel hub; it is optional/);
});

test('Install guidance and stale facts are consistent', () => {
  for (const file of ['docs/WORKSHOP-1H-QUICKSTART.md', 'docs/IDEA-TO-PRODUCTION-WORKBOOK.md']) {
    const text = read(file);
    assert.doesNotMatch(text, /install the awesome-gbb and threadlight-skills plugins/i, file);
    assert.doesNotMatch(text, /--plugin-dir \.\/awesome-gbb/, file);
    assert.match(text.replace(/\s+/g, ' '), /install the threadlight-skills plugin from aiappsgbb\/threadlight-skills first/, file);
  }
  const readme = read('README.md');
  assert.doesNotMatch(readme, /\*\*NEW v0\.\d/);
  assert.doesNotMatch(readme, /all 24 local capabilities/);
  const cicd = read('skills/threadlight-cicd/SKILL.md').match(/^\s*version:\s*"?([\d.]+)/m)[1];
  assert.match(read('THREADLIGHT.md'), new RegExp(`CI/CD \\*\\*${cicd.replace(/\./g, '\\.')}\\*\\*`));
  assert.doesNotMatch(read('docs/production-readiness.md'), /What's new in v0\.3\.0/);
});

test('deploy SKILL.md keeps its skill prose outside the embedded deploy-notes template', () => {
  const lines = read('skills/threadlight-deploy/SKILL.md').split('\n');
  const inCode = [];
  let open = null;
  for (const line of lines) {
    const match = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (!match) { inCode.push(Boolean(open)); continue; }
    if (!open) open = match[1];
    else if (match[1][0] === open[0] && match[1].length >= open.length && !match[2].trim()) open = null;
    inCode.push(true);
  }
  assert.equal(open, null);
  for (const heading of ['## Prerequisites', '## Deploy Steps', '## Deploy-time failure-mode index (signature → action)']) {
    const index = lines.indexOf(heading);
    assert.ok(index > 0, heading);
    assert.equal(inCode[index], false, `${heading} renders as prose`);
  }
});
