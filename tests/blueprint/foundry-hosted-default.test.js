const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// 2.19.4: the hosted agent is ALWAYS the default. A prompt agent needs an explicit
// opt-in (SPEC agent_type: prompt with trivial_justification, or an explicit user
// request). An agent never picks prompt on its own initiative, and never when it
// refuses an app-side request.

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const canonicalPath = 'skills/_shared/foundry-only-agents.md';
const MARKER = '<!-- threadlight:foundry-only-agents -->';
const ruleSkills = [
  'threadlight-deploy', 'threadlight-auto', 'threadlight-design',
  'threadlight-qualify', 'threadlight-workspace-ui', 'threadlight-local-test',
];

const OPT_IN = /explicit(ly)? opt(s|ed)?[- ]in/i;
const OWN_INITIATIVE = /never (pick|choose|select)s? (a )?prompt[^\n]{0,40}own initiative/i;
const REFUSAL_HOSTED = /refus\w*[^\n]{0,120}(always|only)[^\n]{0,40}hosted/i;

const collapse = (text) => text.replace(/\n>\s?/g, ' ').replace(/\s+/g, ' ');

const shortBlock = () => {
  const rule = read(canonicalPath);
  const start = rule.indexOf('```markdown\n' + MARKER);
  assert.ok(start >= 0, 'canonical short block missing');
  const body = rule.slice(start + '```markdown\n'.length);
  return body.slice(0, body.indexOf('\n```'));
};

test('canonical rule: hosted is ALWAYS the default; prompt only by explicit opt-in', () => {
  const rule = collapse(read(canonicalPath));
  assert.match(rule, /hosted agent\*\* is \*\*always\*\* the default/i);
  assert.match(rule, OPT_IN);
  assert.match(rule, /SPEC[^.]{0,60}`agent_type: prompt`[^.]{0,80}trivial_justification/i);
  assert.match(rule, /user explicitly (asks|requests|opts)/i);
  assert.match(rule, OWN_INITIATIVE);
  assert.match(rule, REFUSAL_HOSTED);
  assert.match(rule, /route the work to a Foundry hosted agent[^.]{0,40}never (to )?a prompt agent/i);
});

test('short block carries the explicit opt-in rule and is copied verbatim into every listed SKILL.md', () => {
  const block = shortBlock();
  const flat = collapse(block);
  assert.match(flat, /always[^.]{0,30}hosted agent/i);
  assert.match(flat, OPT_IN);
  assert.match(flat, OWN_INITIATIVE);
  assert.match(flat, REFUSAL_HOSTED);
  for (const skill of ruleSkills) {
    const text = read(`skills/${skill}/SKILL.md`);
    assert.ok(text.includes(block), `${skill}: short block must match the canonical block verbatim`);
  }
});

test('core principles, README and docs state the explicit prompt opt-in', () => {
  for (const file of ['THREADLIGHT.md', 'README.md', 'docs/agent-operations.md', 'docs/skill-based-agents.md', 'docs/basics.html']) {
    const text = collapse(read(file));
    assert.match(text, OPT_IN, file);
    assert.match(text, OWN_INITIATIVE, file);
  }
});

test('SPEC and foundation templates require an explicit opt-in for agent_type: prompt', () => {
  for (const file of [
    'skills/threadlight-design/references/speckit-template.md',
    'skills/threadlight-design/references/foundation-template.md',
  ]) {
    const text = collapse(read(file));
    assert.match(text, OPT_IN, file);
    assert.match(text, OWN_INITIATIVE, file);
  }
});

test('runtime-policy.json encodes hosted-always default and explicit prompt opt-in', () => {
  const hosting = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json')).agent_hosting;
  assert.equal(hosting.default, 'hosted');
  assert.equal(hosting.prompt_requires_explicit_opt_in, true);
  assert.deepEqual(hosting.prompt_opt_in_sources, ['spec-agent-type-prompt', 'explicit-user-request']);
  assert.equal(hosting.agent_may_select_prompt_on_own_initiative, false);
  assert.equal(hosting.refusal_route, 'hosted');
});

test('design and deploy skills never suggest prompt as the simpler self-chosen option', () => {
  const design = collapse(read('skills/threadlight-design/SKILL.md'));
  assert.match(design, /Hosting shape[^.]{0,200}always[^.]{0,40}`foundry-hosted-agent`/i);
  assert.match(design, OPT_IN);
  const deploy = read('skills/threadlight-deploy/SKILL.md');
  assert.doesNotMatch(deploy, /Prompt Agent is simpler/i);
  assert.match(collapse(deploy), OPT_IN);
});

// Review follow-up: the SPEC is agent-written, so a SPEC value alone cannot be an
// opt-in. It counts only when it traces to the user (prompt_opt_in_source: user plus
// a quote of the request in prompt_opt_in_evidence).
const AGENT_WRITTEN_NOT_OPT_IN = /agent[- ](written|authored)[^.]{0,120}(is )?not an opt-in/i;

test('SPEC prompt opt-in must trace to the user, never to the agent that wrote the SPEC', () => {
  for (const file of [
    canonicalPath,
    'skills/threadlight-design/SKILL.md',
    'skills/threadlight-design/references/speckit-template.md',
    'skills/threadlight-design/references/foundation-template.md',
  ]) {
    const text = collapse(read(file));
    assert.match(text, /prompt_opt_in_source: user/, file);
    assert.match(text, /prompt_opt_in_evidence/, file);
    assert.match(text, AGENT_WRITTEN_NOT_OPT_IN, file);
    assert.doesNotMatch(text, /SPEC author or the user/i, file);
  }
  const hosting = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json')).agent_hosting;
  assert.deepEqual(hosting.prompt_opt_in_required_fields, ['trivial_justification', 'prompt_opt_in_source', 'prompt_opt_in_evidence']);
  assert.deepEqual(hosting.prompt_opt_in_source_allowed, ['user']);
  assert.equal(hosting.agent_authored_spec_counts_as_opt_in, false);
});

test('README, core principles and docs say a refusal routes to a hosted agent', () => {
  for (const file of ['THREADLIGHT.md', 'README.md', 'docs/agent-operations.md', 'docs/skill-based-agents.md', 'docs/basics.html']) {
    assert.match(collapse(read(file)), REFUSAL_HOSTED, file);
  }
});

test('no tracked skill or doc offers a prompt agent as trivial-only, simpler or a fallback without the opt-in', () => {
  const { execSync } = require('node:child_process');
  const files = execSync('git ls-files -- "*.md" "*.html" "*.json" "*.yaml" "*.yml"', { cwd: root, encoding: 'utf8' })
    .split('\n')
    .filter((f) => f && /^(skills\/|docs\/|[^/]+$)/.test(f))
    .filter((f) => !/^(CHANGELOG\.md|docs\/superpowers\/|docs\/archive|.*\/archive\/)/.test(f))
    .filter((f) => !/(^|\/)tests?\//.test(f));
  const permissive = /(prompt[- ]agent|agent_type: prompt|prompt agents?)[^\n]{0,80}(\(trivial only\)|is simpler|simpler|as a fallback|fallback)/i;
  const offenders = [];
  for (const file of files) {
    const lines = read(file).split('\n');
    lines.forEach((line, i) => {
      if (!permissive.test(line)) return;
      const ctx = lines.slice(Math.max(0, i - 3), i + 4).join(' ');
      if (OPT_IN.test(ctx) || /\b(never|not|no)\b[^\n]{0,80}fallback/i.test(line) || /never/i.test(line)) return;
      offenders.push(`${file}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(offenders, [], offenders.join('\n'));
});
