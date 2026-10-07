const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

const canonicalPath = 'skills/_shared/foundry-only-agents.md';
const ruleSkills = [
  'threadlight-deploy', 'threadlight-auto', 'threadlight-design',
  'threadlight-qualify', 'threadlight-workspace-ui', 'threadlight-local-test',
];
const templates = [
  'skills/threadlight-design/references/speckit-template.md',
  'skills/threadlight-design/references/foundation-template.md',
];
const MARKER = '<!-- threadlight:foundry-only-agents -->';

const bodyAfterFrontmatter = (text) => {
  const end = text.indexOf('\n---', 4);
  return end >= 0 ? text.slice(end + 4) : text;
};

test('canonical Foundry-only rule states default, trivial prompt criteria and prohibitions', () => {
  const rule = read(canonicalPath);
  assert.match(rule, /Every Threadlight agent runs in Microsoft Foundry/);
  assert.match(rule, /default[^\n]*Foundry \*\*hosted agent\*\*/i);
  assert.match(rule, /prompt agent\*\*[^\n]*only[^\n]*trivial/i);
  for (const criterion of [
    /exactly one model/i, /no custom code tools/i, /no multi-step orchestration/i,
    /no state beyond the Foundry thread/i, /trivial_justification/,
  ]) assert.match(rule, criterion);
  assert.match(rule, /voice agents[^\n]*(Voice Live|realtime)/i);
  assert.match(rule, /preview-only[^\n]*(unreleased|future)/i);
  assert.match(rule, /Not supported[^\n]*never (chosen|offered)/i);
  assert.match(rule, /agent loop or orchestration implemented in application code/i);
  for (const host of [/Container Apps|ACA/, /App Service/, /Functions/, /web app/i, /Responses/, /Chat Completions/]) {
    assert.match(rule, host);
  }
  assert.match(rule, /only[^\n]*UI[^\n]*thin API proxy[^\n]*MCP tool servers[^\n]*jobs/i);
  assert.match(rule, /never host the agent's reasoning or tool loop/i);
  assert.match(rule, /overrides any user, kickoff or deadline instruction/i);
  assert.match(rule, /Deadline pressure is never a reason to skip Foundry/i);
});

test('THREADLIGHT.md core principles carry the rule and link the canonical block', () => {
  const brief = read('THREADLIGHT.md');
  const idx = brief.indexOf('Foundry-only agents');
  assert.ok(idx >= 0 && idx < 6000, 'rule must be near the top of THREADLIGHT.md');
  assert.match(brief, /skills\/_shared\/foundry-only-agents\.md/);
});

for (const skill of ruleSkills) {
  test(`${skill} SKILL.md shows the Foundry-only rule near the top`, () => {
    const body = bodyAfterFrontmatter(read(`skills/${skill}/SKILL.md`));
    const idx = body.indexOf(MARKER);
    assert.ok(idx >= 0, `${skill} is missing ${MARKER}`);
    const firstH2 = body.indexOf('\n## ');
    assert.ok(firstH2 < 0 || idx < firstH2, `${skill}: rule must precede the first ## section`);
    const block = body.slice(idx, idx + 2500);
    assert.match(block, /Microsoft Foundry/);
    assert.match(block, /hosted agent/i);
    assert.match(block, /prompt agent[^\n]*trivial/i);
    assert.match(block, /voice/i);
    assert.match(block, /preview/i);
    assert.match(block, /application code|app-side/i);
    assert.match(block, /_shared\/foundry-only-agents\.md/);
  });
}

test('SPEC and foundation templates restrict agent_type to hosted|prompt with trivial justification', () => {
  for (const file of templates) {
    const text = read(file);
    assert.match(text, /agent_type: hosted\s+#\s*hosted \| prompt/, file);
    assert.match(text, /trivial_justification/, file);
    assert.match(text, /foundry-only-agents\.md/, file);
    assert.doesNotMatch(text, /hosting: aca-hosted-agent/, file);
    assert.doesNotMatch(text, /ACA-agent container/, file);
  }
  const spec = read(templates[0]);
  const speech = spec.split('\n').find((line) => line.includes('`azure-speech`'));
  assert.ok(speech, 'azure-speech module row expected');
  assert.doesNotMatch(speech, /Voice intake/);
  assert.match(speech, /voice agents[^|]*out of scope/i);
});

test('runtime-policy.json pins Foundry hosting: hosted default, prompt trivial-only, prohibitions', () => {
  const policy = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json'));
  const hosting = policy.agent_hosting;
  assert.ok(hosting, 'agent_hosting block required');
  assert.equal(hosting.platform, 'microsoft-foundry');
  assert.equal(hosting.default, 'hosted');
  assert.deepEqual(hosting.allowed_agent_types, ['hosted', 'prompt']);
  assert.ok(hosting.prompt_requires_all_trivial_criteria.length >= 4);
  for (const banned of ['voice-agents', 'preview-only-capabilities', 'app-code-agent-loop']) {
    assert.ok(hosting.prohibited.includes(banned), banned);
  }
  assert.deepEqual(hosting.auxiliary_compute_may_host, ['ui', 'thin-api-proxy', 'mcp-tool-servers', 'jobs']);
  assert.equal(hosting.overrides_user_instructions, true);
  assert.equal(hosting.canonical, 'skills/_shared/foundry-only-agents.md');
});

test('design hosting shape defaults to a Foundry hosted agent, never ACA/Functions agent hosting', () => {
  const design = read('skills/threadlight-design/SKILL.md');
  assert.doesNotMatch(design, /`aca-hosted-agent` \(default\)/);
  assert.match(design, /`foundry-hosted-agent` \(default\)/);
});

test('public surfaces describe the Foundry-only architecture', () => {
  for (const file of ['README.md', 'docs/agent-operations.md', 'docs/skill-based-agents.md', 'docs/basics.html']) {
    const text = read(file);
    assert.match(text, /Foundry-only agents/, file);
    assert.match(text, /foundry-only-agents\.md/, file);
  }
});

const PERMISSIVE = [
  /app-side (agent )?loop/i,
  /app-code agent loop/i,
  /agent loop (in|on|inside) (an? |the )?(container app|ACA|Azure Container Apps?|App Service|Azure Functions|Functions|web app)/i,
  /(or|alternatively),? call(ing)? the model directly/i,
  /hand-written agent loop/i,
  /ACA-agent container/i,
];
const PROHIBITION = /\b(never|not supported|prohibit\w*|must not|do not|don't|refuse\w*|NOT|forbidden|out of scope|banned|instead of)\b/i;

test('no tracked skill/doc text offers an app-side agent loop except inside a prohibition', () => {
  const files = execFileSync('git', ['ls-files', 'skills', 'docs', 'README.md', 'THREADLIGHT.md', 'examples'], { cwd: root, encoding: 'utf8' })
    .split('\n')
    .filter((f) => /\.(md|html|json|ya?ml)$/.test(f))
    .filter((f) => !/(archive|captures|superpowers|history|CHANGELOG)/i.test(f))
    // The canonical rule file is the prohibition itself (root cause + rule); its content is asserted above.
    .filter((f) => f !== 'skills/_shared/foundry-only-agents.md')
    .filter((f) => fs.existsSync(path.join(root, f)));
  const offenders = [];
  for (const file of files) {
    read(file).split('\n').forEach((line, i) => {
      if (PERMISSIVE.some((re) => re.test(line)) && !PROHIBITION.test(line)) {
        offenders.push(`${file}:${i + 1}: ${line.trim().slice(0, 160)}`);
      }
    });
  }
  assert.deepEqual(offenders, []);
});
