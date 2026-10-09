const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
// The action part spans the effect-authority section and its continuation (#evidence-boundaries).
const actionPanel = () => {
  const page = read('docs/production.html');
  return page.slice(page.indexOf('<section class="chapter-section governance-section" id="effect-authority"'),
    page.indexOf('<section class="chapter-section" id="evidence-review"'));
};
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

test('Production owns actor explanation and the liked interactive decision path', () => {
  const html = actionPanel();
  assert.match(html, /data-governed-flow/);
  assert.match(html, /id="workflow-in-action"/);
  assert.match(html, /class="wf-diagram"/);
  assert.match(html, /id="action-architecture"/);
  assert.match(html, /id="returns-walkthrough"/);
  assert.equal([...html.matchAll(/data-action-actor="/g)].length, 5);
  for (const actor of ['Agent', 'Governed MCP gateway', 'Control plane', 'Human reviewer', 'Business API']) {
    assert.ok(text(html).includes(actor), actor);
  }
  for (const phrase of [/Citadel\/APIM/, /model gateway/, /selected.*(?:effects|actions)/i,
    /unbound.*read.*direct.*business API/i, /no database-write or signing credentials/i,
    /rejects unauthorized direct writes/i, /trusted host/i, /malicious administrator/i,
    /approval intents/i, /audit ACK/i, /own writer identity/i, /not a payment/i]) {
    assert.match(text(html), phrase);
  }
  assert.match(html, /href="\.\/agent-governance\.html#overview"[^>]*>Follow the existing-pilot guide/);
  assert.doesNotMatch(html, /class="authority-steps"|class="action-outcomes"/);
});

test('Citadel visual and global navigation stay byte-identical through the chapter revision', () => {
  // The October 2026 chapter revision intentionally restructured the page; the shared masthead and the
  // accepted Citadel hub figure remain exactly as approved (whitespace-normalized for re-indentation).
  const html = read('docs/production.html');
  const header = html.match(/<header class="masthead">[\s\S]*?<\/header>/)[0];
  assert.equal(createHash('sha256').update(header).digest('hex'),
    '4810e015803ba9b0bbd5cb19fb002368bd83217f58ee808b5d9f9f2e7703a566');
  const citadel = html.match(/<figure id="target"[\s\S]*?<\/figure>/)[0].replace(/\s+/g, ' ');
  assert.equal(createHash('sha256').update(citadel).digest('hex'),
    'ba95ce996d4bc332653d63d3e5cf5902615710cd8ba401cdece9b45eb9290469');
});

test('agent-governance guides an existing pilot without duplicating the architecture', () => {
  const html = read('docs/agent-governance.html');
  const main = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/)[1];
  assert.match(text(main), /Take your Threadlight pilot to governed production/);
  assert.equal([...main.matchAll(/data-guide-step="/g)].length, 5);
  assert.doesNotMatch(main, /class="wf-diagram"|data-flow-node=|data-action-actor=|class="gov-card"/);
  assert.match(main, /href="\.\/production\.html#effect-authority"/);
  assert.match(main, /platform owner/i);
  assert.match(main, /human consent/i);
});

test('the approved detailed workbook remains unchanged beside the existing-pilot guide', () => {
  const html = read('docs/agent-governance.html');
  const markdown = read('docs/first-governed-workflow.md');
  assert.doesNotMatch(html, /data-workbook-excerpt/);
  assert.match(html, /href="https:\/\/github.com\/aiappsgbb\/threadlight-skills\/blob\/7782eba93754fb7cff85336d3f4a8703892bad76\/docs\/first-governed-workflow.md"/);
  const bytes = Buffer.from(markdown);
  assert.equal(createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex'),
    '1d6ed17fa77e14e22633c5fdf56e7e45b814e312');
});

test('legacy architecture fragments have explicit forward destinations and no hidden old page', () => {
  const html = read('docs/agent-governance.html');
  const { legacyRoutes } = require('../../docs/assets/governed-workflow.js');
  const expected = {
    'effect-authority': './production.html#effect-authority',
    'workflow-in-action': './production.html#workflow-in-action',
    evidence: './production.html#evidence-boundaries',
    'human-decisions': './production.html#workflow-in-action',
    freshness: './production.html#effect-authority',
    limits: './production.html#effect-authority',
  };
  assert.deepEqual(legacyRoutes, expected);
  for (const id of Object.keys(expected)) assert.ok(html.includes(`id="${id}"`), id);
  assert.match(html, /Back to Production and the decision paths/);
  assert.doesNotMatch(html, /Autonomy for agents|CIO|CISO|Your business process.<br>/);
});
