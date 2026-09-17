const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const model = () => require('../../docs/assets/governed-workflow.js');

test('three scenarios have different participants, output and terminal paths', () => {
  const { scenarios, stepDetails } = model();
  assert.deepEqual(scenarios.normal.steps, ['proposal', 'checks', 'ack', 'effect', 'result']);
  assert.deepEqual(scenarios.invalid.steps, ['proposal', 'checks', 'denial-audit', 'deny']);
  assert.deepEqual(scenarios.supervisor.steps,
    ['proposal', 'checks', 'pending', 'review', 'verify', 'fresh', 'ack', 'effect', 'result']);
  for (const [scenario, output] of [['normal', 'Allow'], ['invalid', 'Deny'], ['supervisor', 'Review required']]) {
    assert.equal(stepDetails(scenario, 'checks').output, output);
    assert.match(stepDetails(scenario, 'checks').action, /local ACS/);
  }
  assert.deepEqual(stepDetails('invalid', 'denial-audit').actors, ['control']);
  assert.match(stepDetails('invalid', 'denial-audit').output, /Denial audit/);
  assert.match(stepDetails('invalid', 'deny').output, /No business write/);
  assert.ok(!scenarios.invalid.steps.some((id) => ['ack', 'effect', 'review', 'verify'].includes(id)));
  assert.match(scenarios.invalid.note, /facts\/health.*read/i);
  assert.match(stepDetails('supervisor', 'pending').output, /pending_approval/);
  assert.match(stepDetails('supervisor', 'verify').output, /One-use grant/);
  assert.match(stepDetails('supervisor', 'fresh').output, /Consumed once/);
  assert.match(stepDetails('normal', 'effect').action, /Authorize.*conditional/i);
});

test('waiting, current, completed and not-required are distinct semantic states', () => {
  const { stageState, actorState } = model();
  assert.equal(stageState('normal', 0, 0), 'In progress');
  assert.equal(stageState('normal', 1, 0), 'Waiting');
  assert.equal(stageState('normal', 0, 2), 'Completed');
  assert.equal(stageState('normal', 4, 4), 'Completed');
  assert.equal(stageState('supervisor', 3, 3), 'Waiting for decision');
  assert.equal(actorState('normal', 'human', 0), 'Not required');
  assert.equal(actorState('invalid', 'business', 3), 'Not involved in write');
  assert.equal(actorState('supervisor', 'business', 3), 'Waiting');
  assert.equal(actorState('supervisor', 'control', 4), 'In progress');
});

test('the existing deep-dive component map names actual trust and transport boundaries', () => {
  const document = read('docs/agent-governance-deep-dive.md');
  const diagram = document.match(/<!-- diagram: effect-boundaries -->\s*```mermaid\n([\s\S]*?)```/)[1];
  for (const term of ['local OPA', 'Control plane', 'Governance store', 'Logic App',
    'Outlook', 'ARM', 'Business API', 'Cosmos', 'downstream identity', 'Unbound read']) {
    assert.ok(diagram.includes(term), term);
  }
  assert.match(document, /Citadel\/APIM.*model gateway/s);
  assert.match(document, /not.*governed MCP effect gateway/s);
  assert.equal([...document.matchAll(/<!-- diagram: effect-boundaries -->/g)].length, 1);
  assert.match(read('scripts/render-governance-diagrams.mjs'), /--diagram/);
});

test('workbook web page is only a brief entrance with three prerequisites and one primary action', () => {
  const page = read('docs/agent-governance.html');
  const main = page.match(/<main[^>]*>([\s\S]*?)<\/main>/)[1];
  assert.equal([...main.matchAll(/data-prerequisite=/g)].length, 3);
  assert.equal([...main.matchAll(/class="btn btn-primary"/g)].length, 1);
  assert.match(main, />Open the workbook\s*</);
  assert.match(main, /two-tool returns/i);
  assert.match(main, /not a payment/i);
  assert.match(main, /platform owner/i);
  assert.match(main, /Outlook reviewer/i);
  assert.doesNotMatch(main, /data-workbook-stage|data-workbook-excerpt|<pre|class="wb-path"|data-flow-node/);
  const text = main.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  assert.ok(text.split(' ').length <= 260, 'an entrance, not a duplicated workbook');
});
