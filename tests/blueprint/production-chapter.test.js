// Contract for the continuous Production chapter (October 2026 revision).
// Pages stay L200/L300; each part links to its L400/L500 Markdown depth.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const docs = path.join(__dirname, '../../docs');
const read = (name) => fs.readFileSync(path.join(docs, name), 'utf8');
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&').replace(/&rsquo;/g, '\u2019')
  .replace(/&(?:mdash|ndash);/g, '—').replace(/\s+/g, ' ');
const section = (html, id) => {
  const match = html.match(new RegExp(`<section\\b[^>]*id="${id}"[^>]*>([\\s\\S]*?)</section>`));
  assert.ok(match, `missing section ${id}`);
  return match[1];
};

const parts = [
  ['production-domains', 'A working pilot is the starting point.'],
  ['platform-controls', 'Choose the foundation you will operate.'],
  ['operating-controls', 'Validate the candidate before promotion.'],
  ['effect-authority', 'Authorize selected actions before they change records.'],
  ['evidence-review', 'Review the evidence. Name the owners.'],
  ['production-review', 'Continue from the pilot you already have.'],
];

test('hero names the concrete journey and states the assessment limit', () => {
  const source = read('production.html');
  const hero = text(section(source, 'chapter-top'));
  assert.match(hero, /From a working pilot to a controlled release\./);
  assert.match(hero, /does not certify production/);
  assert.doesNotMatch(hero, /operational confidence|Explore the topics/);
  assert.match(source, /<title>From a working pilot to a controlled release/);
});

test('one continuous chapter replaces mutually exclusive topic tabs', () => {
  const source = read('production.html');
  assert.doesNotMatch(source, /data-topic-tab|data-topic-panel|data-area-navigation|id="topic-explorer"/);
  let cursor = -1;
  for (const [id, title] of parts) {
    const at = source.search(new RegExp(`<section\\b[^>]*id="${id}"[^>]*data-toc-id="${id}"`));
    assert.ok(at > cursor, `${id} is a chapter part in reading order`);
    cursor = at;
    assert.ok(text(section(source, id)).includes(title), `${id}: ${title}`);
  }
  assert.equal((source.match(/data-toc-id="/g) || []).length, parts.length, 'only the six parts are indexed');
});

test('one side-rail index and a skip link precede the chapter', () => {
  const source = read('production.html');
  const body = source.slice(source.indexOf('<body'));
  assert.match(body.slice(0, 2000), /<a class="skip-link" href="#main-content">Skip to the chapter<\/a>/);
  assert.match(source, /<main id="main-content"/);
  // One index only: no top sticky bar and no duplicated desktop disclosure.
  assert.doesNotMatch(source, /class="floating-toc|cx-chapter-links|cx-chapter-index/);
  const rails = [...source.matchAll(/<nav class="chapter-rail"[^>]*aria-label="Production chapter"[\s\S]*?<\/nav>/g)];
  assert.equal(rails.length, 1, 'exactly one chapter index');
  const rail = rails[0][0];
  assert.deepEqual([...rail.matchAll(/<a href="#([^"]+)"/g)].map((m) => m[1]), parts.map(([id]) => id));
  assert.match(rail, /<button[^>]*class="rail-toggle"[^>]*aria-expanded="false"[^>]*aria-controls="chapter-rail-list"[^>]*hidden/);
  assert.match(source, /<div class="chapter-layout[^"]*">\s*<nav class="chapter-rail"/);
  // The rail and the reading column sit side by side on desktop.
  assert.match(source, /\.chapter-layout\s*\{[^}]*grid-template-columns:\s*2\d\dpx minmax\(0, 1fr\)/);
  assert.match(source, /\.chapter-rail\s*\{[^}]*position:\s*sticky/);
});

test('the mobile header is opaque and the player is no longer width-capped', () => {
  const style = read('production.html').match(/<style>([\s\S]*?)<\/style>/)[1];
  assert.match(style, /\.chapter-production \.masthead\s*\{[^}]*background:\s*var\(--bg-0\)[^}]*backdrop-filter:\s*none/);
  assert.doesNotMatch(style, /#action-architecture\s*\{[^}]*max-width/);
});

test('the starting point names three separate decisions before any mechanism', () => {
  const body = text(section(read('production.html'), 'production-domains'));
  for (const decision of ['Release', 'Action authorization', 'Business acceptance']) {
    assert.ok(body.includes(decision), decision);
  }
  assert.match(body, /not automatic go-live approval/);
  assert.doesNotMatch(read('production.html'), /ciso-pentagon-svg/, 'constellation replaced by a readable matrix');
});

test('foundation starts from the hosted Foundry agent and keeps Citadel optional and attributed', () => {
  const body = section(read('production.html'), 'platform-controls');
  const plain = text(body);
  for (const phrase of ['hosted Foundry agent', 'existing customer gateway', 'Citadel is optional',
    'Azure-Samples/ai-hub-gateway-solution-accelerator']) assert.ok(plain.includes(phrase), phrase);
  assert.ok(plain.indexOf('hosted Foundry agent') < plain.indexOf('Citadel Governance Hub'),
    'default foundation is explained before the optional hub diagram');
  assert.match(body, /data-visual="citadel-hub"/);
  assert.match(body, /production-readiness\.md#3-the-three-postures/);
});

test('release pipeline is readable on mobile, labels one validation job and avoids low-contrast lime on light', () => {
  const source = read('production.html');
  const delivery = section(source, 'delivery-controls');
  const steps = delivery.match(/<ol class="pipe-mobile"[\s\S]*?<\/ol>/)?.[0];
  assert.ok(steps, 'vertical text equivalent of the pipeline');
  assert.ok((steps.match(/<li/g) || []).length >= 6);
  assert.match(text(delivery), /ONE VALIDATION JOB/);
  const svg = delivery.match(/<svg class="pipe-svg"[\s\S]*?<\/svg>/)[0];
  assert.doesNotMatch(svg, /(?:stroke|fill)="var\(--lime\)"/);
  assert.match(source, /\.pp-flow\s*\{[^}]*stroke:\s*var\(--pp-flow\)/);
  assert.match(source, /prefers-color-scheme: light\)[^{]*\{[^}]*--pp-flow:\s*var\(--accent\)/);
});

test('actors and the two gateways are visible before the scenario player', () => {
  const body = section(read('production.html'), 'effect-authority');
  const player = body.indexOf('id="workflow-in-action"');
  const details = body.indexOf('<details class="action-implementation"');
  for (const marker of ['data-action-primary', 'data-action-support', 'Two different gateways']) {
    const at = body.indexOf(marker);
    assert.ok(at >= 0 && at < player, `${marker} before player`);
    assert.ok(details < 0 || at < details, `${marker} outside implementation details`);
  }
  assert.match(text(body), /not a payment/);
  assert.match(text(body), /not evidence of a live deployment/);
});

test('evidence review pairs each question with owner, evidence and limit', () => {
  const body = section(read('production.html'), 'evidence-review');
  const table = body.match(/<table class="owner-matrix"[\s\S]*?<\/table>/)?.[0];
  assert.ok(table);
  assert.deepEqual([...table.matchAll(/<th scope="col">([^<]+)<\/th>/g)].map((m) => m[1]),
    ['Question', 'Owner', 'Evidence', 'Limit']);
  assert.equal((table.match(/<th scope="row">/g) || []).length, 6);
  const plain = text(body);
  for (const kind of ['Generated artifact', 'Offline inventory', 'LOCAL-14', 'Native/CTK', 'Live evidence',
    'governance_probe_noop', 'not supplied by the new action-governance runtime',
    'Unbound reads', 'whole-agent']) assert.ok(plain.includes(kind), kind);
  assert.match(body, /id="checks"/);
  assert.match(body, /id="information-controls"/);
});

test('calls to action describe their real destinations', () => {
  const source = read('production.html');
  const plain = text(source);
  assert.doesNotMatch(plain, /Assess your pilot|Try the guided workbook/);
  const anchors = [...source.matchAll(/<a\b[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)]
    .map(([, href, label]) => [href, text(label).trim()]);
  const guide = anchors.filter(([, label]) => /Follow the existing-pilot guide/.test(label));
  assert.ok(guide.length >= 1 && guide.every(([href]) => href === './agent-governance.html#overview'));
  for (const [href, label] of anchors.filter(([, l]) => /readiness requirements|assessment/i.test(l))) {
    assert.match(href, /production-readiness\.md/, label);
  }
  const review = section(source, 'production-review');
  assert.match(review, /id="start"[^>]*href="\.\/agent-governance\.html#overview"/);
  assert.match(review, /href="\.\/funnel\.html#scene-cta"/);
  assert.match(review, /href="\.\/operate\.html"/);
});

test('every part links to its Markdown depth and the companion exists', () => {
  const source = read('production.html');
  const starts = parts.map(([id]) => source.search(new RegExp(`<section\\b[^>]*id="${id}"`)));
  for (const [index, [id]] of parts.entries()) {
    if (index === 0) continue;
    const region = source.slice(starts[index], starts[index + 1] ?? source.indexOf('</main>'));
    assert.ok((region.match(/data-deep-dive=/g) || []).length >= 1, `${id}: a primary deep-dive link`);
  }
  assert.match(source, /blob\/main\/docs\/production-governance\.md/);
  const companion = read('production-governance.md');
  assert.match(companion, /production\.html/);
  for (const heading of ['## 1. A working pilot is the starting point', '## 2. Choose the foundation',
    '## 3. Validate the candidate', '## 4. Authorize selected actions', '## 5. Review the evidence',
    '## 6. Continue']) assert.ok(companion.includes(heading), heading);
  for (const fact of ['SAFE', 'ACS/Rego', 'Agent Hooks', 'PEP', 'AGT', 'ASSERT', 'LOCAL-14',
    'governance_probe_noop', 'returns_apply_decision', 'not payment settlement', 'live-unverified',
    'agent-operations.md', 'agent-governance-deep-dive.md', 'agentops-deep-dive.md',
    'production-readiness.md', 'citadel-governance.md', 'runtime-support.md']) {
    assert.ok(companion.includes(fact), fact);
  }
});

test('all inbound legacy anchors remain on the page', () => {
  const source = read('production.html');
  for (const id of ['chapter-top', 'why', 'production-domains', 'platform-controls', 'target', 'model-controls',
    'platform-postures', 'operating-controls', 'delivery-controls', 'ship', 'legs', 'quality-controls',
    'runtime-controls', 'effect-authority', 'returns-walkthrough', 'action-architecture',
    'production-input-proof', 'workflow-in-action', 'user-confirmation', 'evidence-boundaries',
    'information-controls', 'checks', 'production-review', 'chapter-recap', 'proof', 'start']) {
    assert.match(source, new RegExp(`id="${id}"`), id);
  }
});

test('a decorative divider separates each pair of top-level parts, never sub-sections', () => {
  const source = read('production.html');
  const dividers = [...source.matchAll(/<div class="part-divider" aria-hidden="true">/g)].map((m) => m.index);
  assert.equal(dividers.length, parts.length - 1);
  const starts = parts.map(([id]) => source.search(new RegExp(`<section\\b[^>]*id="${id}"`)));
  dividers.forEach((at, index) => {
    assert.ok(at > starts[index] && at < starts[index + 1], `divider ${index + 1} between parts`);
    const between = source.slice(at, starts[index + 1]);
    assert.doesNotMatch(between, /<section\b/, 'divider directly precedes the next part');
  });
});
