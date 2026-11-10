const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Foundry voice agents (preview) are allowed only on an explicit user opt-in that
// acknowledges the preview. Threadlight generates no voice runtime: the official
// microsoft-foundry skill (create-voice) builds, deploys and invokes it.

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const collapse = (text) => text.replace(/\n>\s?/g, ' ').replace(/\s+/g, ' ');
const canonicalPath = 'skills/_shared/foundry-only-agents.md';

const VOICE_OPT_IN = /`agent_type: voice`[^.]{0,120}`voice_opt_in_source: user`/i;
const PREVIEW_ACK = /preview_acknowledged: true/;
const OFFICIAL_ROUTE = /`microsoft-foundry`[^.]{0,80}create-voice/i;
const NEVER_OWN = /never (pick|offer|choose)s? (a )?voice agent[^.]{0,60}own initiative/i;
const AGENT_WRITTEN = /agent[- ]written `agent_type: voice`[^.]{0,80}not an opt-in/i;

test('canonical rule allows a voice agent only on an explicit, preview-acknowledged user opt-in', () => {
  const rule = collapse(read(canonicalPath));
  assert.match(rule, VOICE_OPT_IN);
  assert.match(rule, /voice_opt_in_evidence/);
  assert.match(rule, PREVIEW_ACK);
  assert.match(rule, AGENT_WRITTEN);
  assert.match(rule, NEVER_OWN);
  assert.match(rule, OFFICIAL_ROUTE);
  assert.match(rule, /`agent\.kind: foundry-voice`/);
  assert.match(rule, /Voice Live[^.]{0,80}(directly|application code)/i);
  assert.match(rule, /only exception/i);
});

test('short block in every rule SKILL.md carries the voice opt-in', () => {
  const rule = read(canonicalPath);
  const start = rule.indexOf('```markdown\n<!-- threadlight:foundry-only-agents -->');
  const block = collapse(rule.slice(start, rule.indexOf('\n```', start + 12)));
  assert.match(block, VOICE_OPT_IN);
  assert.match(block, PREVIEW_ACK);
  assert.match(block, OFFICIAL_ROUTE);
});

test('runtime-policy.json encodes voice as an opt-in agent type built by the official skill', () => {
  const policy = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json'));
  const hosting = policy.agent_hosting;
  assert.equal(policy.contract_version, '2.2.0');
  assert.deepEqual(hosting.allowed_agent_types, ['hosted', 'prompt', 'voice']);
  assert.equal(hosting.default, 'hosted');
  assert.equal(hosting.voice_requires_explicit_opt_in, true);
  assert.deepEqual(hosting.voice_opt_in_required_fields, ['voice_opt_in_source', 'voice_opt_in_evidence', 'preview_acknowledged']);
  assert.deepEqual(hosting.voice_opt_in_source_allowed, ['user']);
  assert.equal(hosting.agent_may_select_voice_on_own_initiative, false);
  assert.equal(hosting.voice_build_route, 'azure@azure-skills/microsoft-foundry/foundry-agent/create/create-voice.md');
  assert.equal(hosting.refusal_route, 'hosted');
  assert.ok(!hosting.prohibited.includes('voice-agents'));
  for (const banned of ['voice-agents-without-explicit-opt-in', 'app-code-voice-live-calls', 'preview-only-capabilities', 'app-code-agent-loop']) {
    assert.ok(hosting.prohibited.includes(banned), banned);
  }
});

test('SPEC and foundation templates record the voice opt-in fields', () => {
  for (const file of [
    'skills/threadlight-design/references/speckit-template.md',
    'skills/threadlight-design/references/foundation-template.md',
  ]) {
    const text = read(file);
    assert.match(text, /agent_type: hosted\s+#\s*hosted \| prompt \| voice/, file);
    for (const field of ['voice_opt_in_source', 'voice_opt_in_evidence', 'preview_acknowledged']) {
      assert.match(text, new RegExp(`${field}: null`), `${file}: ${field}`);
    }
    assert.match(collapse(text), AGENT_WRITTEN, file);
  }
});

test('deploy routes an opted-in voice agent to the official microsoft-foundry voice workflow', () => {
  const deploy = collapse(read('skills/threadlight-deploy/SKILL.md'));
  assert.match(deploy, /agent_type: voice/);
  assert.match(deploy, /create-voice\.md/);
  assert.match(deploy, /Threadlight (generates|ships) no voice/i);
  assert.match(deploy, /foundry-voice/);
});

test('README, core principles and docs no longer say voice is never supported, and state the opt-in', () => {
  for (const file of ['THREADLIGHT.md', 'README.md', 'docs/agent-operations.md', 'docs/skill-based-agents.md', 'docs/basics.html', 'docs/production-governance.md']) {
    const text = collapse(read(file));
    assert.doesNotMatch(text, /voice agents[^.]{0,40}(are )?out of scope/i, file);
    assert.doesNotMatch(text, /Voice agents, preview-only/i, file);
    assert.match(text, /voice agent[^.]{0,160}(explicit|opt-in)/i, file);
  }
});
