const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

// The Foundry Microsoft Copilot harness (prompt agent, harness.type
// microsoft_copilot_preview) is preview: it is used only on the user's explicit,
// preview-acknowledged opt-in with a harness_fit record, and any disqualifier
// forces hosted. https://learn.microsoft.com/azure/foundry/agents/how-to/use-tools-skills-copilot-harness

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const collapse = (text) => text.replace(/\n>\s?/g, ' ').replace(/\s+/g, ' ');
const canonicalPath = 'skills/_shared/foundry-only-agents.md';
const policyPath = 'skills/threadlight-design/references/runtime-policy.json';
const MARKER = '<!-- threadlight:foundry-only-agents -->';
const ruleSkills = [
  'threadlight-deploy', 'threadlight-auto', 'threadlight-design',
  'threadlight-qualify', 'threadlight-workspace-ui', 'threadlight-local-test',
];

const HARNESS_OPT_IN = /`agent_type: harness`[^.]{0,160}`harness_opt_in_source: user`/i;
const AGENT_WRITTEN = /agent[- ]written `agent_type: harness`[^.]{0,80}not an opt-in/i;
const NEVER_OWN = /never (pick|offer|choose)s? (the |a )?(Copilot )?harness[^.]{0,60}own initiative/i;
const DISQUALIFIERS = [
  'citadel-model-path-required',
  'acs-in-process-pep-required',
  'foundry-memory-required',
  'voice-frontend-required',
  'region-not-supported',
  'consequential-write-without-function-or-governed-mcp',
];

const shortBlock = () => {
  const rule = read(canonicalPath);
  const start = rule.indexOf('```markdown\n' + MARKER);
  const body = rule.slice(start + '```markdown\n'.length);
  return body.slice(0, body.indexOf('\n```'));
};

const FIT_OK = `harness_fit:
  citadel_model_path_required: false
  acs_in_process_pep_required: false
  foundry_memory_required: false
  voice_frontend_required: false
  region: swedencentral
  consequential_write_seam: function-tool
`;
const OPT_IN_OK = `agent_type: harness
harness_opt_in_source: user
harness_opt_in_evidence: "User: run the returns triage PoC on the Foundry Copilot harness (preview accepted)"
preview_acknowledged: true
${FIT_OK}`;

const resolve = (yaml) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'harness-spec-'));
  const spec = path.join(dir, 'SPEC.md');
  fs.writeFileSync(spec, `# SPEC\n\n## 11e. Workflow Model\n\n\`\`\`yaml\n${yaml}workflow_model: agent\n\`\`\`\n`);
  try {
    const out = execFileSync('python3', ['skills/_shared/agent_type_policy.py', '--spec', spec, '--json'],
      { cwd: root, encoding: 'utf8' });
    return JSON.parse(out);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
};

test('canonical rule allows the Copilot harness only on an explicit, preview-acknowledged user opt-in (item 2d)', () => {
  const rule = collapse(read(canonicalPath));
  assert.match(rule, /2d\./);
  assert.match(rule, HARNESS_OPT_IN);
  assert.match(rule, /harness_opt_in_evidence/);
  assert.match(rule, /preview_acknowledged: true/);
  assert.match(rule, /`harness_fit`/);
  assert.match(rule, AGENT_WRITTEN);
  assert.match(rule, NEVER_OWN);
  assert.match(rule, /microsoft_copilot_preview/);
  assert.match(rule, /any disqualifier[^.]{0,80}hosted/i);
  for (const id of DISQUALIFIERS) assert.ok(rule.includes(`\`${id}\``), id);
  assert.match(rule, /`agent\.kind: foundry-harness`/);
  assert.match(rule, /use-tools-skills-copilot-harness/);
  assert.doesNotMatch(rule, /including the Foundry-managed Copilot harness for prompt agents,\s*stay excluded/i);
});

test('short block carries the harness opt-in and stays verbatim in every rule SKILL.md', () => {
  const block = shortBlock();
  const flat = collapse(block);
  assert.match(flat, HARNESS_OPT_IN);
  assert.match(flat, /preview_acknowledged: true/);
  assert.match(flat, /harness_fit/);
  assert.match(flat, /disqualifier[^.]{0,120}hosted/i);
  for (const skill of ruleSkills) {
    assert.ok(read(`skills/${skill}/SKILL.md`).includes(block), `${skill}: short block must match verbatim`);
  }
});

test('runtime-policy.json encodes harness as a user-opt-in type with machine-checkable disqualifiers', () => {
  const hosting = JSON.parse(read(policyPath)).agent_hosting;
  assert.deepEqual(hosting.allowed_agent_types, ['hosted', 'prompt', 'voice', 'harness']);
  assert.equal(hosting.default, 'hosted');
  assert.equal(hosting.refusal_route, 'hosted');
  assert.equal(hosting.harness_requires_explicit_opt_in, true);
  assert.deepEqual(hosting.harness_opt_in_required_fields,
    ['harness_opt_in_source', 'harness_opt_in_evidence', 'preview_acknowledged', 'harness_fit']);
  assert.deepEqual(hosting.harness_opt_in_source_allowed, ['user']);
  assert.equal(hosting.agent_may_select_harness_on_own_initiative, false);
  assert.equal(hosting.harness_type, 'microsoft_copilot_preview');
  assert.deepEqual(hosting.harness_disqualifiers.map((d) => d.id), DISQUALIFIERS);
  for (const d of hosting.harness_disqualifiers) {
    assert.ok(d.fit_field && d.reason, d.id);
  }
  assert.ok(hosting.harness_supported_regions.includes('swedencentral'));
  assert.ok(!hosting.harness_supported_regions.includes('eastus2'), 'eastus2 is not a documented harness region');
  assert.ok(hosting.prohibited.includes('harness-without-explicit-opt-in'));
  assert.ok(hosting.prohibited.includes('preview-only-capabilities'));
});

test('SPEC and foundation templates record the harness opt-in fields and the fit record', () => {
  for (const file of [
    'skills/threadlight-design/references/speckit-template.md',
    'skills/threadlight-design/references/foundation-template.md',
  ]) {
    const text = read(file);
    assert.match(text, /agent_type: hosted\s+#\s*hosted \| prompt \| voice \| harness/, file);
    for (const field of ['harness_opt_in_source', 'harness_opt_in_evidence']) {
      assert.match(text, new RegExp(`${field}: null`), `${file}: ${field}`);
    }
    assert.match(text, /harness_fit:/, file);
    for (const fit of ['citadel_model_path_required', 'acs_in_process_pep_required', 'foundry_memory_required',
      'voice_frontend_required', 'region', 'consequential_write_seam']) {
      assert.match(text, new RegExp(`\\n\\s+${fit}: null`), `${file}: harness_fit.${fit}`);
    }
    assert.match(collapse(text), AGENT_WRITTEN, file);
  }
});

test('resolver: a user-opted-in harness with a clean fit record resolves to harness', () => {
  const result = resolve(OPT_IN_OK);
  assert.equal(result.agent_type, 'harness', JSON.stringify(result));
  assert.deepEqual(result.disqualifiers, []);
});

test('resolver: an agent-written agent_type: harness without user provenance is treated as hosted', () => {
  for (const yaml of [
    `agent_type: harness\npreview_acknowledged: true\n${FIT_OK}`,
    OPT_IN_OK.replace('harness_opt_in_source: user', 'harness_opt_in_source: agent'),
    OPT_IN_OK.replace(/harness_opt_in_evidence: .*\n/, 'harness_opt_in_evidence: null\n'),
    OPT_IN_OK.replace('preview_acknowledged: true', 'preview_acknowledged: null'),
    OPT_IN_OK.replace(FIT_OK, ''),
  ]) {
    const result = resolve(yaml);
    assert.equal(result.agent_type, 'hosted', yaml);
    assert.ok(result.reasons.length > 0, yaml);
  }
});

test('resolver: any disqualifier (or an unknown fit answer) forces hosted', () => {
  const cases = [
    ['citadel_model_path_required: false', 'citadel_model_path_required: true', 'citadel-model-path-required'],
    ['acs_in_process_pep_required: false', 'acs_in_process_pep_required: true', 'acs-in-process-pep-required'],
    ['foundry_memory_required: false', 'foundry_memory_required: true', 'foundry-memory-required'],
    ['voice_frontend_required: false', 'voice_frontend_required: true', 'voice-frontend-required'],
    ['region: swedencentral', 'region: eastus2', 'region-not-supported'],
    ['consequential_write_seam: function-tool', 'consequential_write_seam: direct-api', 'consequential-write-without-function-or-governed-mcp'],
    ['foundry_memory_required: false', 'foundry_memory_required: null', 'foundry-memory-required'],
    ['region: swedencentral', 'region: null', 'region-not-supported'],
  ];
  for (const [from, to, id] of cases) {
    const result = resolve(OPT_IN_OK.replace(from, to));
    assert.equal(result.agent_type, 'hosted', `${to}`);
    assert.ok(result.disqualifiers.includes(id), `${to}: ${JSON.stringify(result)}`);
  }
  const voiced = resolve(`${OPT_IN_OK}voice_frontend: hosted-voice\n`);
  assert.equal(voiced.agent_type, 'hosted');
  assert.ok(voiced.disqualifiers.includes('voice-frontend-required'));
});
