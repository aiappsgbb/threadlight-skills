const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const actionStart = '<div class="topic-panel" id="actions-topic" data-topic-panel>';
const actionEnd = '\n      </div>\n    </div>\n  </div>\n  <section id="production-review"';
const actionPanel = () => read('docs/production.html').split(actionStart)[1].split(actionEnd)[0];
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
  assert.match(html, /href="\.\/agent-governance\.html#overview"[^>]*>Try the guided workbook/);
  assert.doesNotMatch(html, /class="authority-steps"|class="action-outcomes"/);
});

test('Citadel, AgentOps, global navigation and all non-action bytes stay unchanged', () => {
  const html = read('docs/production.html').replace(
    /^<(?:link rel="stylesheet" href="assets\/governed-workflow\.css\?v=[a-f0-9]+"|script src="assets\/governed-workflow\.js\?v=[a-f0-9]+")>(?:<\/script>)?\n/gm, '')
    .replace(/\/blob\/7ef20742ac78cfe2e86db4abf21a6135916a5f64\/docs\/agent-governance-deep-dive\.md/g,
      '/blob/af45bbb88cea9931082da3f20c289fa4feb73399/docs/agent-governance-deep-dive.md')
    .replace(/(<nav[^>]*data-area-navigation="actions-topic"[^>]*>)([\s\S]*?)(<\/nav>)/,
      (_, open, links, close) => {
        assert.ok(links.includes('href="#effect-authority">Actors &amp; authority'));
        assert.ok(links.includes('>Decision paths</a>'));
        assert.ok(links.includes('>Guided workbook</a>'));
        return open + links.replace('href="#effect-authority">Actors &amp; authority', 'href="#returns-walkthrough">Returns example')
          .replace('>Decision paths</a>', '>How it works</a>')
          .replace('>Guided workbook</a>', '>Four outcomes</a>') + close;
      });
  const start = html.indexOf(actionStart);
  const end = html.indexOf(actionEnd, start);
  assert.ok(start >= 0 && end > start);
  assert.equal(createHash('sha256').update(html.slice(0, start) + html.slice(end)).digest('hex'),
    '1d2df136e705c273975299bb42a304f6057c477e1d9352b8ba75134f0b008d0d');
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
