const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const guidePath = 'docs/first-governed-workflow.md';
const guide = () => read(guidePath);
const reference = 'skills/threadlight-deploy/references/governance';
const companionRevision = '2ef44f6b47803a0166956cc668e5f429c1c1f8cb';
const companionSkills = ['azure-tenant-isolation', 'foundry-hosted-agents'];
const phases = () => [...guide().matchAll(/^## Phase (\d): ([^\n]+)\n([\s\S]*?)(?=^## |(?![\s\S]))/gm)];

test('beginner journey has six actionable phases with explicit ownership and costs', () => {
  const sections = phases();
  assert.deepEqual(sections.map((section) => Number(section[1])), [1, 2, 3, 4, 5, 6]);
  for (const [, number, , body] of sections) {
    for (const label of ['Cost and safety', 'Goal', 'Skills', 'Inputs', 'Copilot prompt',
      'Assistant', 'Human', 'Platform owner', 'Result', 'Verify', 'If blocked']) {
      assert.ok(body.includes(`**${label}:**`), `phase ${number}: ${label}`);
    }
    assert.equal([...body.matchAll(/```text\n[\s\S]*?```/g)].length, 1, `phase ${number}: one copyable prompt`);
    assert.match(body.split('**If blocked:**')[1], /\]\([^)]+#[^)]+\)/, `phase ${number}: exact runbook section`);
    assert.ok(body.indexOf('**Cost and safety:**') < body.indexOf('**Copilot prompt:**'));
  }
});

test('guide selects the real MAF agent gateway profile and the smaller two-tool reference', () => {
  const text = guide();
  const policy = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json'));
  assert.ok(policy.compatible_combinations.some((tuple) =>
    tuple.framework === 'microsoft-agent-framework' && tuple.runtime_shape === 'agent' && tuple.protocol === 'responses'));
  assert.ok(policy.governance_compatibility['microsoft-agent-framework'].includes('governed-tool-gateway'));
  for (const token of ['microsoft-agent-framework', 'runtime_shape=agent', 'responses',
    'Foundry hosted', 'governed-tool-gateway', 'returns_get_case', 'returns_apply_decision']) {
    assert.ok(text.includes(token), token);
  }
  for (const tool of ['returns_get_case', 'returns_apply_decision']) {
    assert.ok(read(`${reference}/returns_mcp_agent.py`).includes(`"id": "${tool}"`));
  }
  assert.match(text, /construction agent[\s\S]*business agent/i);
  assert.match(text, /two-tool/i);
  assert.match(text, /OMS\/CRM/);
  assert.match(text, /not payment settlement/i);
  assert.match(text, /not a deterministic MAF workflow/i);
});

test('prompt skill names and source paths resolve to real catalog or pinned companion files', () => {
  const text = guide();
  const prompts = [...text.matchAll(/```text\n([\s\S]*?)```/g)].map((match) => match[1]);
  assert.equal(prompts.length, 6);
  for (const prompt of prompts) {
    const skills = [...prompt.matchAll(/\b((?:threadlight|foundry|azure)-[a-z]+(?:-[a-z]+)*)\b/g)]
      .map((match) => match[1]);
    assert.ok(skills.length, 'each prompt names its actual skills');
    for (const skill of skills) {
      if (companionSkills.includes(skill)) {
        assert.ok(text.includes(`https://github.com/aiappsgbb/awesome-gbb/blob/${companionRevision}/skills/${skill}/SKILL.md`), skill);
      } else {
        const source = `skills/${skill}/SKILL.md`;
        assert.ok(fs.existsSync(path.join(root, source)), skill);
        assert.match(read(source), new RegExp(`^name: ${skill}$`, 'm'));
        assert.ok(text.includes(`../${source}`), `missing linked skill ${skill}`);
      }
    }
    for (const [source] of prompt.matchAll(/\b(?:skills|scripts)\/[a-zA-Z0-9_./-]+\.(?:py|json|bicep)\b/g)) {
      assert.ok(fs.existsSync(path.join(root, source)), source);
      assert.ok(text.includes(`](../${source})`), `missing source link ${source}`);
    }
  }
  assert.ok(read(`${reference}/returns-mcp-demo.md`).includes(companionRevision));
});

test('all guide relative links and heading targets resolve without a network dependency', () => {
  const links = [...guide().matchAll(/\]\(([^)\s]+)\)/g)];
  assert.ok(links.length >= 20);
  for (const [, href] of links) {
    if (/^[a-z]+:/i.test(href)) continue;
    const [relative, fragment] = href.split('#');
    const target = relative ? path.resolve(root, 'docs', relative) : path.join(root, guidePath);
    assert.ok(target.startsWith(`${root}${path.sep}`), href);
    assert.ok(fs.existsSync(target), `missing ${href}`);
    if (fragment) {
      const source = fs.readFileSync(target, 'utf8');
      const ids = target.endsWith('.md')
        ? [...source.matchAll(/^#{1,6} (.+)$/gm)].map((match) =>
          match[1].toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-'))
        : [...source.matchAll(/\sid=["']([^"']+)["']/g)].map((match) => match[1]);
      assert.ok(ids.includes(fragment), `missing fragment ${href}`);
    }
  }
});

test('local iteration and source packaging cannot be mistaken for Azure enforcement', () => {
  const text = guide();
  for (const token of ['source-package.json', 'source-only-not-deployment-proof',
    'tests/quickstart.jsonl', 'LOCAL-14', 'governance_probe_noop']) assert.ok(text.includes(token), token);
  assert.ok(read(`${reference}/package_returns_mcp.py`).includes('"status": "source-only-not-deployment-proof"'));
  assert.match(text, /quickstart[\s\S]{0,220}not[\s\S]{0,100}(hosted|gateway)/i);
  assert.match(text, /Copilot[\s\S]{0,100}(billing|usage|cost)/i);
  assert.match(text, /no model call/i);
  assert.match(text, /instruction examples, not guaranteed\s+automation/i);
  assert.doesNotMatch(text, /```(?:sh|bash|python|bicep|yaml)\n/);
});

test('scope, consent, signed publication and network prerequisites are explicit', () => {
  const text = guide();
  for (const token of ['personal tenant index', 'AZURE_CONFIG_DIR', 'AZD_CONFIG_DIR',
    'allowed_subscriptions', 'public-authenticated-proof', 'private-required',
    'Office 365', 'OAuth', 'Edge Work', 'Reader', 'publisher']) assert.ok(text.includes(token), token);
  assert.match(text, /before every Azure operation/i);
  assert.match(text, /private key[\s\S]{0,100}outside the agent/i);
  assert.match(text, /never paste[\s\S]{0,100}(keys|tokens)/i);
  assert.match(text, /actual (?:mailbox user|human)/i);
  assert.match(text, /observed[\s\S]{0,180}version[\s\S]{0,100}image[\s\S]{0,100}identit/i);
  assert.match(text, /does not[\s\S]{0,80}(grant|supply)[\s\S]{0,80}(permissions|access)/i);
});

test('demonstration verifies exact resume and independent effects instead of model prose', () => {
  const text = guide();
  for (const token of ['pending_approval', 'expected_etag', 'governance_operation_id',
    'previous_response_id', 'session_id', 'case_id', 'decision', 'audit_id',
    'read_audit_id', 'outlook-native/v1', 'consumed']) assert.ok(text.includes(token), token);
  assert.match(text, /same native session/i);
  assert.match(text, /unchanged arguments/i);
  assert.match(text, /central audit ACK[\s\S]{0,100}before effects/i);
  assert.match(text, /no second business effect/i);
  assert.match(text, /nondeterministic/i);
  assert.match(text, /independent[\s\S]{0,60}(read|reconcil)/i);
  assert.match(text, /no borrowed evidence/i);
  assert.match(text, /deny[\s\S]*pending[\s\S]*Approve[\s\S]*Reject/i);
});

test('release and recovery handoff uses existing contracts without promising new operations', () => {
  const text = guide();
  for (const target of ['agentops-deep-dive.md', 'agent-operations.md',
    '../skills/threadlight-cicd/references/release-contract.md#stop-and-recover',
    'native-outlook-approval-architecture.md#9-failure-handling-and-operating-ownership']) {
    assert.ok(text.includes(`](${target})`), target);
  }
  assert.match(text, /same immutable\s+image/i);
  assert.match(text, /application-owned adapters/i);
  assert.match(text, /release approval[\s\S]{0,120}action approval[\s\S]{0,120}go-live/i);
  assert.match(text, /no automatic rollback/i);
  assert.match(text, /unknown outcome[\s\S]{0,200}(reconcil|stop)/i);
  assert.match(text, /only new temporaries owned by this test/i);
  assert.match(text, /sanitized evidence[\s\S]{0,120}before cleanup/i);
  assert.match(text, /pre-existing, shared and preserved demo/i);
  assert.doesNotMatch(text, /\/pull\/132|\/tree\/[^/]*production-operational|stop.lease/i);
});

test('beginner discoverability preserves the commercial CTA and pinned references', () => {
  assert.ok(read('README.md').includes(`](${guidePath})`));
  const site = read('docs/agent-governance.html');
  assert.match(site, /href="https:\/\/github\.com\/aiappsgbb\/threadlight-skills\/blob\/main\/docs\/first-governed-workflow\.md"/);
  assert.ok(site.includes('href="./funnel.html#scene-cta">Design a governed workflow'));
  assert.ok(site.includes('blob/c4cfb09926531869b787ad8e1a0e3a67188cad49/docs/native-outlook-approval-architecture.md'));
  assert.ok(site.includes('blob/772074123506c285eed2395c40ded4eaad229cc6/docs/agent-governance-deep-dive.md'));
});

test('guide has no private deployment data, dangerous recipes or blanket proof claims', () => {
  const text = guide();
  assert.doesNotMatch(text, /\/Users\/|\/home\/[a-z][a-z0-9_-]+\/|\/operator\/|[A-Z]:\\Users\\/i);
  assert.doesNotMatch(text, /\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/i);
  assert.doesNotMatch(text, /SecurityControl\s*=\s*Ignore|--role\s+(?:Owner|Contributor)|azd down|az account set|BEGIN .*PRIVATE KEY/i);
  assert.doesNotMatch(text, /100%\s*secure|fully verified end.to.end|automatically production.ready|all tools are governed/i);
});
