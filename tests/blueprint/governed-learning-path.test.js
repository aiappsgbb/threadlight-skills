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

test('agent-governance is a short six-step web workbook, not a second architecture page', () => {
  const html = read('docs/agent-governance.html');
  const main = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/)[1];
  assert.match(text(main), /Build your first governed workflow/);
  assert.equal([...main.matchAll(/data-workbook-stage="/g)].length, 6);
  assert.match(main, /aria-label="Workbook steps"/);
  for (const id of ['define', 'prepare', 'local', 'authorize', 'validate', 'operate']) {
    const card = main.match(new RegExp(`<section[^>]*id="${id}"[\\s\\S]*?</section>`))?.[0];
    assert.ok(card, id);
    for (const label of ['Inputs', 'People', 'Result']) assert.ok(card.includes(label), `${id}: ${label}`);
    assert.match(card, /first-governed-workflow\.md#phase-/);
  }
  assert.ok(text(main).trim().split(/\s+/).length <= 1100, 'web summary, not another long reference');
  assert.doesNotMatch(main, /class="wf-diagram"|data-flow-node=|data-action-actor=|class="gov-card"/);
  assert.match(main, /navigation, not execution status/i);
  assert.match(main, /href="\.\/production\.html#effect-authority"/);
  assert.match(main, /platform owner/i);
  assert.match(main, /personal OAuth consent/i);
});

test('web prompt and command excerpts come verbatim from the approved workbook', () => {
  const html = read('docs/agent-governance.html');
  const markdown = read('docs/first-governed-workflow.md');
  const blocks = [...html.matchAll(/<pre data-workbook-excerpt="([^"]+)"[^>]*><code>([\s\S]*?)<\/code><\/pre>/g)];
  assert.equal(blocks.length, 2, 'one local prompt and one local command, not a command dump');
  const source = {
    design: markdown.split('## Phase 1:')[1].match(/```text\n([\s\S]*?)```/)[1].trim(),
    package: markdown.match(/<!-- workbook-command: package -->\n```sh\n([\s\S]*?)```/)[1].trim(),
  };
  for (const [, name, code] of blocks) {
    assert.equal(code.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim(), source[name], name);
  }
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
  assert.match(html, /Architecture and decision diagrams are now in/);
  assert.doesNotMatch(html, /Autonomy for agents|CIO|CISO|Your business process.<br>/);
});
