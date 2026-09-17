const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

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
    assert.equal([...body.matchAll(/```text\n[\s\S]*?```/g)].length, number === '4' ? 2 : 1,
      `phase ${number}: preparation prompt, plus separate deployment authorization in phase 4`);
    assert.match(body.split('**If blocked:**')[1], /\]\([^)]+#[^)]+\)/, `phase ${number}: exact runbook section`);
    assert.ok(body.indexOf('**Cost and safety:**') < body.indexOf('**Copilot prompt:**'));
  }
});

test('first-screen map separates work possible now from later Azure handoffs', () => {
  const text = guide();
  const start = text.indexOf('## Start here');
  assert.ok(start >= 0 && start < text.indexOf('## One example'), 'map precedes technical profile');
  const map = text.slice(start, text.indexOf('\n## ', start + 1));
  const steps = [...map.matchAll(/^\d\. \[[^\]]+\]\(#phase-(\d)-[^)]+\)/gm)];
  assert.deepEqual(steps.map((step) => Number(step[1])), [1, 2, 3, 4, 5, 6]);
  assert.match(map, /Need now[\s\S]*Copilot[\s\S]*synthetic/i);
  assert.match(map, /Need later[\s\S]*platform owner/i);
  assert.match(map, /Phases 1-3[\s\S]{0,80}without Azure/i);
  assert.match(map, /model\s+rehearsal[\s\S]{0,100}(optional|separate)/i);
});

test('shared safety box keeps core authority and evidence definitions outside the phase loop', () => {
  const text = guide();
  const box = text.match(/^> \*\*Rules for every phase\*\*[^\n]*(?:\n>[^\n]*)*/m)?.[0];
  assert.ok(box, 'one shared safety box');
  assert.ok(text.indexOf(box) < text.indexOf('## Phase 1'));
  for (const phrase of [/permission/i, /publisher/i, /central audit ACK/i,
    /before effects/i, /LOCAL-14/, /no borrowed evidence/i, /stop/i]) assert.match(box, phrase);
  assert.ok(box.includes('](agent-operations.md#required-controls-precede-effects)'));
});

test('reviewed deployment transition has bounded authorization and an observed exit gate', () => {
  const phase = phases().find((section) => section[1] === '4')[3];
  const transition = phase.split('### After platform review: authorize the exact deployment')[1];
  assert.ok(transition, 'explicit bridge before phase 5');
  const prompt = transition.match(/```text\n([\s\S]*?)```/)?.[1];
  assert.ok(prompt, 'copyable follow-up authorization example');
  for (const phrase of [/I authorize threadlight-deploy/i, /tenant\/index/i,
    /subscription/i, /resource group/i, /model deployment/i, /resource\/change list/i,
    /budget/i, /cleanup owner/i, /no unapproved grants/i, /stop/i]) assert.match(prompt, phrase);
  assert.match(transition, /replace[\s\S]{0,120}placeholders/i);
  assert.match(transition, /not[\s\S]{0,60}blanket authorization/i);
  assert.match(transition, /Before Phase 5[\s\S]*native hosted[\s\S]*services[\s\S]*fresh signed binding/i);
  assert.match(transition, /version[\s\S]{0,100}image[\s\S]{0,100}identit/i);
  assert.match(transition, /signed-remote-bootstrap-operator-contract/);
});

test('reviewed grants target observed identities while personal OAuth remains separate', () => {
  const phase = phases().find((section) => section[1] === '4')[3];
  const owner = phase.split('**Platform owner:**')[1].split('**Result:**')[0].replace(/\s+/g, ' ');
  const transition = phase.split('### After platform review: authorize the exact deployment')[1];
  const prompt = transition.match(/```text\n([\s\S]*?)```/)[1].replace(/\s+/g, ' ');
  assert.match(owner, /after registration[\s\S]*observed Agent Identity/i);
  assert.match(owner, /pre-approved MCP\/control-plane app roles/i);
  assert.match(owner, /workflow-scoped Reader[\s\S]{0,100}observed control-plane identity/i);
  assert.match(prompt, /exact role, resource scope and expected identity/i);
  assert.match(prompt, /only[\s\S]{0,100}approved assignments[\s\S]{0,100}actual observed identit/i);
  assert.match(prompt, /stop[\s\S]{0,100}missing approval[\s\S]{0,100}identity mismatch[\s\S]{0,100}broader permissions/i);
  assert.match(prompt, /personal OAuth consent[\s\S]{0,100}separate[\s\S]{0,100}not authorized here/i);
  assert.doesNotMatch(phase, /No new grants or consent/i);
});

test('browser choice belongs to the user rather than the guide author', () => {
  const text = guide();
  assert.match(text, /user's approved work-browser\/profile/i);
  assert.match(text, /Edge Work[\s\S]{0,80}optional example/i);
  assert.doesNotMatch(text, /use the user's \*\*Edge Work\*\* profile/);
  assert.match(text, /actual human[\s\S]{0,80}sign-in\/consent/i);
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
  assert.equal(prompts.length, 7);
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
  assert.doesNotMatch(text, /```(?:bicep|yaml)\n/);
});

test('workbook has editable inputs, user checks, three diagrams and bounded real commands', () => {
  const text = guide();
  assert.match(text, /workbook/i);
  assert.equal([...text.matchAll(/```mermaid\n/g)].length, 3);
  assert.match(text, /construction agent[\s\S]*business agent/i);
  assert.match(text, /ACK[\s\S]*Backend[\s\S]*audit/);
  for (const [, number, , body] of phases()) {
    assert.match(body, /\| Fill in locally \| Your value \|/, `phase ${number}: editable input card`);
    assert.match(body, /- \[ \]/, `phase ${number}: user checks`);
  }
  const expected = new Map([
    ['skill-check', ['skills/threadlight-design/scripts/skill_contract_check.py', ['--target', '--emit', '--gate', '--json']]],
    ['package', [`${reference}/package_returns_mcp.py`, ['--output']]],
    ['foundation', [`${reference}/generate.py`, ['--project', '--contract', '--configuration']]],
    ['native-validation', ['scripts/ci/run-governance-pin-tests.py', ['--deployment']]],
    ['reconcile', [`${reference}/returns_reconcile.py`, ['--configuration', '--output']]],
    ['release-preflight', ['skills/threadlight-cicd/scripts/release_runner.py', ['--repo', '--policy']]],
  ]);
  const commands = [...text.matchAll(/<!-- workbook-command: ([a-z-]+) -->\n```sh\n([\s\S]*?)```/g)];
  assert.deepEqual(commands.map((match) => match[1]).sort(), [...expected.keys()].sort());
  for (const [, name, command] of commands) {
    const [source, flags] = expected.get(name);
    assert.ok(command.includes(`"$CATALOG/${source}"`), name);
    assert.ok(text.includes(`](../${source})`), `source link for ${name}`);
    const implementation = read(source);
    for (const [, variable] of command.matchAll(/\$([A-Z_]+)/g)) {
      assert.ok(command.includes(`"\${${variable}:?`), `${name}: reject unset ${variable}`);
    }
    for (const flag of flags) {
      assert.ok(command.includes(flag), `${name}: ${flag}`);
      assert.ok(implementation.includes(`"${flag}"`), `${source}: real ${flag}`);
    }
  }
  assert.match(commands.find((match) => match[1] === 'foundation')[2], /\bfoundation\b/);
  assert.match(commands.find((match) => match[1] === 'release-preflight')[2], /\bpreflight\b/);
  assert.match(text, /real CI context/i);
  assert.match(text, /no candidate receipt/i);
  assert.match(text, /not (?:this |your )?pilot[\s\S]{0,40}(proof|acceptance)/i);
});

test('workbook packaging command produces source-only artifacts with independently matching hashes', () => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'threadlight-workbook-'));
  const output = path.join(scratch, 'package');
  try {
    execFileSync('python3', [path.join(root, reference, 'package_returns_mcp.py'), '--output', output]);
    const manifest = JSON.parse(fs.readFileSync(path.join(output, 'source-package.json'), 'utf8'));
    assert.equal(manifest.status, 'source-only-not-deployment-proof');
    assert.ok(Object.keys(manifest.files).length > 0);
    for (const [file, expected] of Object.entries(manifest.files)) {
      const filename = path.resolve(output, file);
      assert.ok(filename.startsWith(`${output}${path.sep}`));
      assert.equal(crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex'), expected, file);
    }
    const backend = `${reference}/returns_mcp_backend.py`;
    assert.equal(fs.readFileSync(path.join(output, backend), 'utf8'), read(backend));
    assert.throws(() => execFileSync('python3',
      [path.join(root, reference, 'package_returns_mcp.py'), '--output', output], { stdio: 'pipe' }),
    /refusing_to_overwrite_preserved_package/);
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});
test('scope, consent, signed publication and network prerequisites are explicit', () => {
  const text = guide();
  for (const token of ['personal tenant index', 'AZURE_CONFIG_DIR', 'AZD_CONFIG_DIR',
    'allowed_subscriptions', 'public-authenticated-proof', 'private-required',
    'Office 365', 'OAuth', 'Reader', 'publisher']) assert.ok(text.includes(token), token);
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
  assert.match(text, /only new temporaries owned\s+by this test/i);
  assert.match(text, /sanitized evidence[\s\S]{0,120}before cleanup/i);
  assert.match(text, /pre-existing, shared and\s+preserved demo/i);
  assert.doesNotMatch(text, /\/pull\/132|\/tree\/[^/]*production-operational|stop.lease/i);
});

test('beginner discoverability connects Production to the web and approved Markdown workbook', () => {
  assert.ok(read('README.md').includes(`](${guidePath})`));
  const site = read('docs/agent-governance.html');
  assert.match(site, /href="https:\/\/github\.com\/aiappsgbb\/threadlight-skills\/blob\/7782eba93754fb7cff85336d3f4a8703892bad76\/docs\/first-governed-workflow\.md"/);
  assert.ok(site.includes('Take your Threadlight pilot to governed production'));
  assert.ok(site.includes('href="./production.html#effect-authority"'));
  const production = read('docs/production.html');
  assert.ok(production.includes('href="./agent-governance.html#overview">Try the guided workbook'));
  assert.ok(production.includes('blob/af45bbb88cea9931082da3f20c289fa4feb73399/docs/agent-governance-deep-dive.md'));
});

test('guide has no private deployment data, dangerous recipes or blanket proof claims', () => {
  const text = guide();
  assert.doesNotMatch(text, /\/Users\/|\/home\/[a-z][a-z0-9_-]+\/|\/operator\/|[A-Z]:\\Users\\/i);
  assert.doesNotMatch(text, /\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/i);
  assert.doesNotMatch(text, /SecurityControl\s*=\s*Ignore|--role\s+(?:Owner|Contributor)|azd down|az account set|BEGIN .*PRIVATE KEY/i);
  assert.doesNotMatch(text, /100%\s*secure|fully verified end.to.end|automatically production.ready|all tools are governed/i);
});
