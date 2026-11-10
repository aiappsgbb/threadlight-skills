const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Opt-in voice FRONT-END on a Threadlight hosted agent (Foundry Hosted Voice, preview):
// a managed `kind: voice` wrapper uses the GHCP hosted agent as its conversation
// engine through Voice Live Bridge Protocol 1.0 on /invocations_ws. Same explicit,
// preview-acknowledged user opt-in as `agent_type: voice`; tools and governance stay
// in the text agent. Learn: https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const collapse = (text) => text.replace(/\n>\s?/g, ' ').replace(/\s+/g, ' ');
const LEARN = 'https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent';

test('canonical rule adds the hosted-voice front-end with the same user opt-in', () => {
  const rule = collapse(read('skills/_shared/foundry-only-agents.md'));
  assert.match(rule, /`agent_type: hosted`[^.]{0,200}`voice_frontend: hosted-voice`/i);
  assert.match(rule, /`voice_frontend: hosted-voice`[^.]{0,400}`voice_opt_in_source: user`/i);
  assert.match(rule, /agent[- ]written `voice_frontend`[^.]{0,120}not an opt-in/i);
  assert.match(rule, /tools[^.]{0,80}(governance|MCP)[^.]{0,80}(stay|remain) in the (hosted|text) agent/i);
  assert.ok(rule.includes(LEARN));
  assert.match(rule, /items 2b and 2c are the only exceptions/i);
  assert.match(rule, /Why \(2\.32\.0\)/);
});

test('runtime-policy.json encodes the voice front-end opt-in', () => {
  const policy = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json'));
  const hosting = policy.agent_hosting;
  assert.equal(policy.contract_version, '2.2.0');
  assert.deepEqual(hosting.voice_frontend_values, ['none', 'hosted-voice']);
  assert.equal(hosting.voice_frontend_default, 'none');
  assert.equal(hosting.voice_frontend_requires_agent_type, 'hosted');
  assert.deepEqual(hosting.voice_frontend_runtimes, ['ghcp']);
  assert.deepEqual(hosting.voice_frontend_opt_in_required_fields,
    ['voice_opt_in_source', 'voice_opt_in_evidence', 'preview_acknowledged']);
  assert.equal(hosting.agent_may_select_voice_frontend_on_own_initiative, false);
  assert.match(hosting.voice_frontend_build_route, /generate_only/);
});

test('SPEC and foundation templates carry voice_frontend off by default', () => {
  for (const file of [
    'skills/threadlight-design/references/speckit-template.md',
    'skills/threadlight-design/references/foundation-template.md',
  ]) {
    const text = read(file);
    assert.match(text, /voice_frontend: none\s+#\s*none \| hosted-voice/, file);
    assert.match(text, /voice_name: null/, file);
    assert.match(text, /REQUIRED when agent_type: voice or voice_frontend: hosted-voice/, file);
  }
});

test('deploy skill and GHCP README document the generated voice front-end and its limits', () => {
  for (const file of ['skills/threadlight-deploy/SKILL.md', 'skills/threadlight-deploy/references/hosted-agent/ghcp/README.md',
    'skills/threadlight-deploy/references/generate-only-mode.md']) {
    const text = collapse(read(file));
    assert.match(text, /voice_frontend: hosted-voice/, file);
    assert.match(text, /kind: voice/, file);
    assert.match(text, /invocations_ws/, file);
    assert.match(text, /smoke_test_voice\.py/, file);
    assert.match(text, /barge-in/i, file);
    assert.match(text, /not validated/i, file);
  }
  const readme = collapse(read('skills/threadlight-deploy/references/hosted-agent/ghcp/README.md'));
  assert.match(readme, /THREADLIGHT_VOICE_BRIDGE/);
  assert.match(readme, /MAF[^.]{0,80}not (supported|generated)/i);
  assert.ok(readme.includes(LEARN));
});

test('workshop quickstart has an optional, preview-labelled voice segment', () => {
  const text = read('docs/WORKSHOP-1H-QUICKSTART.md');
  const section = text.split(/^### 6\.[0-9a-z]+ Optional: talk to the agent/m)[1];
  assert.ok(section, 'missing "Optional: talk to the agent" section in chapter 6');
  const body = collapse(section.split(/^##/m)[0]);
  assert.match(body, /preview/i);
  assert.match(body, /voice_frontend: hosted-voice/);
  assert.match(body, /smoke_test_voice\.py/);
  assert.match(body, /explicit/i);
});

test('docs operations spine and Pages basics mention the opt-in voice front-end', () => {
  for (const file of ['docs/agent-operations.md', 'docs/basics.html']) {
    const text = collapse(read(file));
    assert.match(text, /voice front-end[^.]{0,200}(opt-in|explicit)/i, file);
  }
});
