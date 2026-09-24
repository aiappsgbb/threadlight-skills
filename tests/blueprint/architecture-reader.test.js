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
  const mainReadingPath = report.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, '');
  assert.ok(mainReadingPath.split(/\s+/).length <= 4100, 'Keep the main reading path concise, including the fifth scenario');
  const proseWithoutLinkTargets = report.replace(/\]\([^)]*\)/g, ']');
  assert.doesNotMatch(proseWithoutLinkTargets, /public hosted execution snapshot|S[123]-HOSTED|ProvisioningError|Billing Issue|2026-09-|sha256:[a-f0-9]{64}/i);
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
  assert.match(section, /class="wf-diagram"/);
  assert.match(section, /role="img"[^>]*aria-labelledby="wf-map-title wf-map-desc"/);
  assert.match(section, /<path[^>]*marker-end=/);
  for (const label of ['Agent', 'Governed MCP gateway', 'Control plane', 'Human reviewer', 'Business API',
    'Outlook', 'policy', 'trusted facts', 'audit ACK']) assert.ok(section.includes(label), label);
  const outcomes = page.match(/<section\b[^>]*id="evidence-boundaries"[^>]*>([\s\S]*?)<\/section>/)[1];
  assert.match(outcomes, /Components and trust boundaries/);
  assert.match(outcomes, /agent-governance-deep-dive\.md/);
  assert.doesNotMatch(section, /sha256:|2026-|S[123]-|ETag CAS|Idempotency-Key/);
  assert.doesNotMatch(page, /--cp-bg:\s*#f7f4ef|data-theme="dark"/, 'Do not replace the established site theme');
});

test('production groups three complementary areas, keeps privacy cross-cutting and explains the action gap', () => {
  const page = read('docs/production.html');
  const domains = ['platform-controls', 'operating-controls', 'effect-authority'];
  for (const id of domains) {
    assert.ok(page.includes(`id="${id}"`), id);
    assert.ok(page.includes(`href="#${id}"`), id);
  }
  assert.ok(page.indexOf('id="production-domains"') < page.indexOf('id="effect-authority"'));
  assert.ok(page.indexOf('id="information-controls"') < page.indexOf('id="topic-explorer"'));
  assert.match(page, /<aside[^>]*id="information-controls"/);
  assert.match(page, /data-area-navigation="platform-topic"[^>]*>[\s\S]*?href="#model-controls"[\s\S]*?<\/nav>/);
  const action = page.match(/<section\b[^>]*id="effect-authority"[^>]*>([\s\S]*?)<\/section>/)[1];
  for (const phrase of ['current case revision', 'not authorization', 'not a payment',
    'effect boundary', 'independent business API']) {
    assert.ok(action.includes(phrase), phrase);
  }
  assert.ok(action.indexOf('id="returns-walkthrough"') < action.indexOf('class="wf-diagram"'));
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

test('guide separates upstream execution, installed compatibility packages and Threadlight implementation', () => {
  const report = read('docs/agent-governance-deep-dive.md');
  for (const phrase of ['Native components and Threadlight code', 'AGT core package',
    'not the full Agent Governance Toolkit (AGT) stack', 'responsibleai/agent-hooks', 'create_agent_hooks_middleware',
    'Threadlight implements', 'Public Preview']) assert.ok(report.includes(phrase), phrase);
  assert.match(report, /policy-engine/);
  assert.match(report, /solution-review-2026-09-15\.md#agt-upstream-status/);
});

test('SAFE principles are translated into the running return example rather than presented as a platform feature', () => {
  const report = read('docs/agent-governance-deep-dive.md');
  for (const principle of ['Scope', 'Anchored Decisions', 'Flow Integrity', 'Escalation']) {
    assert.ok(report.includes(`| ${principle} |`), principle);
  }
  assert.match(report, /SAFE is not a Foundry feature/);
  assert.match(report, /ASSERT.*not.*pre-effect/s);
});

test('dated upstream assessment distinguishes current activity, migration and production support', () => {
  const report = read('docs/solution-review-2026-09-15.md');
  for (const phrase of ['id="agt-upstream-status"', 'c63c51e881c442fbc060705f7e211f29993f2c1b',
    'imran-siddique/agent-governance', 'Public Preview', 'does not establish an SLA',
    'First-feedback boundary']) assert.ok(report.includes(phrase), phrase);
});

test('production feedback links resolve to the clarified governance guide snapshot', () => {
  const page = read('docs/production.html');
  const links = [...page.matchAll(/href="(https:\/\/github\.com\/aiappsgbb\/threadlight-skills\/blob\/[^/"]+\/docs\/agent-governance-deep-dive\.md)(?:#[^"]*)?"/g)];
  const current = 'https://github.com/aiappsgbb/threadlight-skills/blob/979772904624ad873969cb6f84388f4a6f3f587b/docs/agent-governance-deep-dive.md';
  assert.equal(links.length, 4, 'Three pinned architecture links plus the new confirmation contract');
  assert.equal(links.filter(([, target]) => target === current).length, 3);
  assert.match(page, /href="https:\/\/github\.com\/aiappsgbb\/threadlight-skills\/blob\/main\/docs\/agent-governance-deep-dive\.md#requesting-user-confirmation"/);
});

test('root guidance records the completed private human reference without extending its proof', () => {
  const readme = read('README.md');
  assert.match(readme, /verified private native Outlook approval/);
  assert.match(readme, /same-session resume/);
  assert.match(readme, /not current authority/);
  assert.doesNotMatch(readme, /still-unproved private\s+human-resume\/email/);
});
