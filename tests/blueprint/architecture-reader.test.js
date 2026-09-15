const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

test('architecture report leads with scope, a contents map and implementation, not a deployment diary', () => {
  const report = read('docs/agent-governance-deep-dive.md');
  for (const heading of ['## Contents', 'What is covered elsewhere', 'What is covered here',
    'Architecture and trust boundaries', 'From proposal to execution',
    'Human approval and safe resume', 'State, audit and recovery', 'Implementation map',
    'Limits and adoption']) assert.ok(report.includes(heading), heading);
  assert.ok((report.match(/\]\(#/g) || []).length >= 9, 'Navigable contents');
  assert.ok(report.split(/\s+/).length <= 4000, 'Keep the main reading path concise');
  assert.doesNotMatch(report, /public hosted execution snapshot|S[123]-HOSTED|ProvisioningError|Billing Issue|2026-09-|sha256:[a-f0-9]{64}/i);
  for (const topic of ['content safety', 'IAM', 'model gateway', 'network isolation',
    'evaluations', 'business API', 'one-use', 'Outlook', 'unbound', 'without ACS']) {
    assert.ok(report.toLowerCase().includes(topic.toLowerCase()), topic);
  }
  assert.ok(report.includes('governed-returns-validation.md'), 'History remains independently accessible');
});

test('architecture diagrams are pre-rendered images with accessible text and retained editable sources', () => {
  for (const file of ['docs/agent-governance-deep-dive.md', 'docs/native-outlook-approval-architecture.md']) {
    const report = read(file);
    const sources = [...report.matchAll(/<!-- diagram: ([a-z0-9-]+) -->\s*```mermaid\n([\s\S]*?)\n```/g)];
    assert.ok(sources.length >= 3, file);
    for (const [, name] of sources) {
      assert.match(report, new RegExp(`!\\[[^\\]]{15,}\\]\\(assets/governance/${name}\\.svg\\)`));
      const svg = read(`docs/assets/governance/${name}.svg`);
      assert.match(svg, /<svg/);
      assert.doesNotMatch(svg, /Syntax error in text|mermaid-error|<script|<foreignObject/i);
    }
  }
});

test('production chapter explains the action boundary through a connected visual and a deeper reading path', () => {
  const page = read('docs/production.html');
  const section = page.match(/<section\b[^>]*id="effect-authority"[^>]*>([\s\S]*?)<\/section>/)[1];
  assert.match(section, /class="authority-map"/);
  assert.match(section, /role="img"[^>]*aria-labelledby="authority-map-title authority-map-desc"/);
  assert.match(section, /<path[^>]*marker-end=/);
  for (const label of ['Propose', 'Authorize', 'Execute', 'Human decision', 'Outlook',
    'Identity', 'Policy', 'Trusted facts', 'Audit']) assert.ok(section.includes(label), label);
  assert.match(section, /Read the architecture/);
  assert.match(section, /agent-governance-deep-dive\.md/);
  assert.doesNotMatch(section, /sha256:|2026-|S[123]-|ETag CAS|Idempotency-Key/);
  assert.doesNotMatch(page, /--cp-bg:\s*#f7f4ef|data-theme="dark"/, 'Do not replace the established site theme');
});

test('production presents complementary domains before the agent-action detail and explains its business gap', () => {
  const page = read('docs/production.html');
  const domains = ['platform-controls', 'model-controls', 'effect-authority',
    'quality-controls', 'information-controls', 'operating-controls'];
  for (const id of domains) {
    assert.ok(page.includes(`id="${id}"`), id);
    assert.ok(page.includes(`href="#${id}"`), id);
  }
  assert.ok(page.indexOf('id="production-domains"') < page.indexOf('id="effect-authority"'));
  const action = page.match(/<section\b[^>]*id="effect-authority"[^>]*>([\s\S]*?)<\/section>/)[1];
  for (const phrase of ['authenticated agent', 'outdated case', 'repeat a decision',
    'Routine work', 'Exceptions', 'Accountability', 'existing business API']) {
    assert.ok(action.includes(phrase), phrase);
  }
  assert.ok(action.indexOf('outdated case') < action.indexOf('class="authority-map"'));
  assert.doesNotMatch(page, /Threadlight proves the agent you run in it|Agent governance &mdash; not platform governance/);
});

test('technical guide follows the public domains and expands the contracts with code and JSON', () => {
  const report = read('docs/agent-governance-deep-dive.md');
  for (const heading of ['Platform and network controls', 'Model governance',
    'Agent behavior governance', 'Quality and evaluation', 'Information protection',
    'Operations and lifecycle']) assert.ok(report.includes(heading), heading);
  assert.ok(report.includes('<!-- contract: action-approval-fields -->'));
  assert.ok(report.includes('<!-- contract: resumed-decision -->'));
  assert.ok(report.includes('<!-- code: native-client-wiring -->'));
  assert.ok(report.includes('<!-- code: conditional-business-effect -->'));
});
