const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const guidePath = 'docs/skill-based-agents.md';
const guide = () => read(guidePath);
const sources = {
  policy: 'skills/threadlight-design/references/runtime-policy.json',
  design: 'skills/threadlight-design/SKILL.md',
  ghcp: 'skills/threadlight-deploy/references/governance/ghcp-container.py',
  maf: 'skills/threadlight-deploy/references/governance/maf-container.py',
  local: 'skills/threadlight-local-test/references/quickstart/threadlight_quickstart/agent_wiring.py',
  auto: 'skills/threadlight-auto/references/orchestrator.py',
  validator: 'skills/threadlight-design/scripts/skill_contract_check.py',
  packager: 'scripts/build-cowork-zips.sh',
};
const localSkills = fs.readdirSync(path.join(root, 'skills'), { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && entry.name.startsWith('threadlight-'))
  .map((entry) => entry.name).sort();

test('orientation defines construction and business agents before governance', () => {
  const text = guide();
  for (const pattern of [
    /construction agent/i, /business agent/i,
    /skills\/threadlight-\*/, /src\/agent\/skills\/.*SKILL\.md/,
    /Markdown[\s\S]{0,250}instruction[\s\S]{0,250}not[\s\S]{0,150}model/i,
    /not[\s\S]{0,150}permission[\s\S]{0,150}enforcement/i,
    /orchestration[\s\S]{0,200}(instructions|runtime|workflow)/i,
  ]) assert.match(text, pattern);
  assert.match(read(sources.design), /Do NOT create "orchestrator" skills/);
  assert.match(text, /no [^\n]*orchestrator[^\n]*skill/i);
  const readme = read('README.md');
  const orientation = readme.indexOf('## What is a skill?');
  assert.ok(orientation >= 0 && orientation < readme.indexOf('## Runtime governance:'));
  for (const file of ['README.md', 'THREADLIGHT.md', 'docs/agent-operations.md']) {
    assert.match(read(file), /skill-based-agents\.md/, file);
  }
});

test('runtime tuples and workflow branch come from the local policy and design source', () => {
  const policy = JSON.parse(read(sources.policy));
  assert.deepEqual(policy.default, {
    framework: 'github-copilot-sdk', runtime_shape: 'agent',
    protocol: 'invocations', policy_route: 'default-agent',
  });
  const text = guide();
  for (const tuple of policy.compatible_combinations) {
    assert.ok(text.split('\n').some((line) =>
      [tuple.framework, tuple.runtime_shape, tuple.protocol].every((value) =>
        line.includes(`\`${value}\``))), `missing tuple ${JSON.stringify(tuple)}`);
  }
  for (const token of ['runtime-policy.json', 'specs/foundation.md',
    'capability_signals', 'unresolved_signals', 'WORKFLOW.md']) {
    assert.ok(text.includes(token), token);
  }
  assert.match(read(sources.design), /WORKFLOW\.md[\s\S]{0,110}executor\/phase definitions/);
  assert.match(text, /deterministic workflow/i);
  assert.match(text, /Fast-PoC[\s\S]{0,160}checkpoint/i);
});

test('runtime-loading explains the actual separate instruction, skill and tool wiring', () => {
  const ghcp = read(sources.ghcp);
  const maf = read(sources.maf);
  const local = read(sources.local);
  assert.match(ghcp, /skill_directories=\[str\(base \/ "skills"\)\]/);
  assert.match(ghcp, /system_message=[\s\S]{0,150}copilot-instructions\.md/);
  assert.match(ghcp, /mcp_servers=servers/);
  assert.match(maf, /def build_host\([\s\S]*SkillsProvider\.from_paths\(skills\)/);
  assert.match(maf, /tools=tools, context_providers=contexts/);
  assert.match(maf, /class GovernedHost\(ResponsesHostServer\)/);
  assert.match(local, /continuing without it/);
  assert.match(local, /context_providers = \[skills_provider\] if skills_provider is not None else \[\]/);
  const text = guide();
  assert.match(text, /^## Runtime loading$/m);
  for (const file of [sources.ghcp, sources.maf, sources.local]) {
    assert.ok(text.includes(file), `missing source path ${file}`);
  }
  for (const token of ['skill_directories', 'system_message', 'mcp_servers',
    'SkillsProvider.from_paths', 'ResponsesHostServer', 'context_providers',
    'copilot-instructions.md']) assert.ok(text.includes(token), token);
  assert.match(text, /deployment package/i);
  assert.match(text, /runnable local host[\s\S]{0,100}not proof[\s\S]{0,80}skills/i);
  assert.match(text, /not[\s\S]{0,60}(hosted.route parity|parity with the hosted route)/i);
});

test('GHCP hosted application protocol and outbound model wire API are separate hops', () => {
  const ghcp = read(sources.ghcp);
  const policy = JSON.parse(read(sources.policy));
  assert.equal(policy.default.protocol, 'invocations');
  assert.match(ghcp, /class GovernedHost\(InvocationAgentServerHost\)/);
  assert.match(ghcp, /provider=ProviderConfig\([\s\S]{0,220}wire_api="responses"/);
  const section = guide().split('### GHCP selected-governance adapter')[1]
    .split('### MAF selected-governance adapter')[0];
  assert.match(section, /client → hosted application/);
  assert.match(section, /agent → model provider/);
  for (const token of ['protocol=invocations', 'InvocationAgentServerHost',
    'ProviderConfig', 'wire_api="responses"']) assert.ok(section.includes(token), token);
  assert.match(section, /does not change[\s\S]{0,100}hosted application/i);
});

test('Auto follows the selected DAG and delegates execution rather than inventing a worker', () => {
  const source = read(sources.auto);
  const sequence = JSON.parse(source.match(/^GOVERNANCE_STAGES = (\[[\s\S]*?\])/m)[1]);
  assert.ok(sequence.indexOf('govern') < sequence.indexOf('deploy'));
  assert.ok(sequence.indexOf('governed_actions_gate') < sequence.indexOf('deploy'));
  assert.ok(sequence.indexOf('governance_probe') > sequence.indexOf('deploy'));
  assert.match(source, /def _stages_for[\s\S]*if not selected\(document\)/);
  assert.match(source, /def execute\(workspace: Path, worker,[\s\S]*code = worker\(stage\)/);
  assert.match(source, /zero exit is not gate evidence/);
  const text = guide();
  for (const token of ['decide()', 'execute(workspace, worker', '_stages_for',
    'STAGES', 'GOVERNANCE_STAGES', 'run', 'skip', 'hard_stop']) {
    assert.ok(text.includes(token), token);
  }
  assert.ok(text.includes(sequence.map((stage) => `\`${stage}\``).join(' → ')),
    'document the actual selected-binding sequence, not a capability-group DAG');
  assert.match(text, /caller-supplied worker callback/i);
  assert.match(text, /legacy[\s\S]{0,160}(advisory|recommendation)/i);
  assert.match(text, /zero worker exit[\s\S]{0,100}not[\s\S]{0,30}gate evidence/i);
  assert.match(text, /not the business agent/i);
});

test('all local skills have a mechanism, artifact and evidence boundary in capability groups', () => {
  const text = guide();
  assert.equal(localSkills.length, 24);
  const rows = text.split('\n').filter((line) => /^\| \[`threadlight-/.test(line));
  const documented = rows.map((row) => row.match(/\[`(threadlight-[^`]+)`\]/)[1]).sort();
  assert.deepEqual(documented, localSkills);
  for (const row of rows) {
    const cells = row.split('|').slice(1, -1).map((cell) => cell.trim());
    assert.equal(cells.length, 5, `expected skill/purpose/mechanism/artifact/boundary: ${row}`);
    assert.ok(cells.every((cell) => cell.length > 8), row);
  }
  for (const group of ['Enter', 'Build', 'Integrate', 'Assure', 'Ship', 'Improve', 'Auto overlay', 'Optional AgentOps extension']) {
    assert.match(text, new RegExp(`^### ${group}$`, 'm'));
  }
  assert.match(text, /capability groups[\s\S]{0,100}not[\s\S]{0,100}universal execution DAG/i);
  assert.match(text, /governance baseline[\s\S]{0,200}8153bc2e0a677d99b8414053d7a00cfdab495444/i);
  assert.doesNotMatch(text, /AgentOps is absent from this local/i);
});

test('primary capability groups and artifact paths stay consistent with the public Build page', () => {
  const text = guide();
  for (const [group, skill] of [['Build', 'threadlight-design'], ['Assure', 'threadlight-consumption-iq']]) {
    const section = text.split(`### ${group}\n`)[1]?.split(/^### /m)[0];
    assert.ok(section?.includes(`[\`${skill}\`]`), `${skill} belongs to ${group}`);
  }
  for (const artifact of ['qualification/sizing-manifest.json', 'qualification/discovery.md',
    'qualification/roi.md', 'src/agent/copilot-instructions.md']) assert.ok(text.includes(artifact), artifact);
});

test('Cowork explains curated qualify packaging and capability rather than upload equivalence', () => {
  const packaging = read(sources.packager);
  assert.match(packaging, /COWORK_SAFE_SKILLS=\(\s*threadlight-design\s*\)/);
  assert.match(packaging, /build_qualify_zip\(\)/);
  assert.match(packaging, /^build_qualify_zip$/m);
  const text = guide();
  for (const name of ['threadlight-qualify', 'threadlight-design']) {
    const file = `docs/downloads/${name}.zip`;
    assert.ok(fs.existsSync(path.join(root, file)));
    assert.ok(text.includes(`downloads/${name}.zip`));
  }
  assert.match(text, /build_qualify_zip\(\)/);
  assert.match(text, /vendored[\s\S]{0,80}(Python|code|runtime)/i);
  assert.match(text, /repository packaging\s+constraints/i);
  assert.match(text, /https:\/\/learn\.microsoft\.com\/microsoft-365\/copilot\/cowork\/cowork-customize/);
  assert.match(text, /Customize[\s\S]{0,100}Skills[\s\S]{0,100}Add[\s\S]{0,100}Upload skill/);
  assert.match(text, /format compatibility[\s\S]{0,120}(not|≠)[\s\S]{0,100}(tools|identity|network)/i);
});

test('public returns example retains the actual domain and evidence limits', () => {
  const example = read('examples/returns-triage-governed/AGENTS.md');
  assert.match(example, /returns_apply_decision[\s\S]{0,60}Cosmos conditional case \+ audit transaction/);
  assert.match(example, /never\s+settles a payment/);
  const text = guide();
  for (const token of ['intake-validation', 'policy-eligibility',
    'fraud-escalation', 'disposition-decision', 'returns_apply_decision',
    'governance_probe_noop']) assert.ok(text.includes(token), token);
  assert.match(text, /Cosmos[\s\S]{0,60}decision[\s\S]{0,60}audit/i);
  assert.match(text, /not[\s\S]{0,30}settlement/i);
  assert.match(text, /business.binding[\s\S]{0,80}(unverified|not live)/i);
});

test('public tool trace links executable registration, backend and terminal transport', () => {
  const files = [
    'examples/returns-triage-governed/src/agent/governance_application.py',
    'examples/returns-triage-governed/src/agent/returns_backend.py',
    'examples/returns-triage-governed/src/agent/cosmos_effect.py',
  ];
  assert.match(read(files[0]), /self\.tools = \[[\s\S]{0,130}returns_apply_decision\]/);
  assert.match(read(files[1]), /require_effect_authorization_async\("returns_apply_decision"/);
  assert.match(read(files[2]), /def batch\(/);
  for (const file of files) assert.ok(guide().includes(file), file);
});

test('technical briefing retains phase-specific assurance gate failures', () => {
  const redteam = read('skills/threadlight-redteam/scripts/redteam_check.py');
  const governed = read('skills/threadlight-governed-actions/scripts/governed_actions.py');
  assert.match(redteam, /add_argument\("--gate"/);
  assert.match(governed, /if finding\.status != "not-verified":[\s\S]{0,320}return 1/);
  const briefing = read('THREADLIGHT.md');
  assert.match(briefing, /selected-phase required `not-verified`/);
  const section = briefing.split('### 13. `threadlight-redteam`')[1].split('### 14.')[0];
  assert.doesNotMatch(section, /never blocks/);
  assert.match(section, /--gate/);
});

test('offline validator commands and exit semantics match the implementation', () => {
  const source = read(sources.validator);
  const text = guide();
  for (const flag of ['--target', '--emit', '--gate', '--json']) {
    assert.ok(source.includes(`parser.add_argument("${flag}"`));
    assert.ok(text.includes(flag));
  }
  assert.match(source, /if args\.gate and man\["must_fix"\]:[\s\S]{0,200}return 2/);
  assert.match(text, /threadlight-skill-contract-manifest\/v1/);
  assert.match(text, /exit (?:code )?`?2`?[\s\S]{0,150}must_fix/i);
  assert.match(text, /not-verified[\s\S]{0,150}exit (?:code )?`?0/i);
  assert.ok(text.includes(`python3 ${sources.validator} --target examples/returns-triage-governed --gate --json`));
  assert.match(text, /repository root/i);
  assert.match(text, /--emit[\s\S]{0,180}(writes|write)/i);
  // This guide's runnable shell examples are deliberately offline, without setup or deployment.
  const shell = [...text.matchAll(/```(?:sh|bash)\n([\s\S]*?)```/g)].map((m) => m[1]).join('\n');
  assert.doesNotMatch(shell, /\b(?:pip|npm|azd|curl|docker)\b|\baz\s|\bgit\s+(?:clone|push)\b/);
  assert.doesNotMatch(text, /all skills require SPEC|both runtimes use SkillsProvider|always skill-based|Auto is a universal worker/i);
  assert.doesNotMatch(read('THREADLIGHT.md'), /Every other skill in the chain assumes|Both runtimes support \*\*`SkillsProvider`|only skill that runs cleanly inside/);
});

test('foundational guide links and heading targets resolve, including the public runtime-loading anchor', () => {
  const text = guide();
  assert.match(read('docs/basics.html'), /skill-based-agents\.md#runtime-loading/);
  for (const file of Object.values(sources)) assert.ok(text.includes(file), file);
  for (const [, href] of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    if (/^[a-z]+:/i.test(href)) continue;
    const [relative, fragment] = href.split('#');
    const target = relative ? path.resolve(root, 'docs', relative) : path.join(root, guidePath);
    assert.ok(fs.existsSync(target), `missing ${href}`);
    if (fragment && target.endsWith('.md')) {
      const slugs = [...fs.readFileSync(target, 'utf8').matchAll(/^#{1,6} (.+)$/gm)]
        .map((m) => m[1].toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-'));
      assert.ok(slugs.includes(fragment), `missing heading ${href}`);
    }
  }
  for (const [label, url] of [['Home', 'index.html'], ['Basics', 'basics.html'],
    ['Build', 'funnel.html'], ['Case study', 'case-study.html'], ['Production', 'production.html']]) {
    const publicUrl = `https://aiappsgbb.github.io/threadlight-skills/${url}`;
    assert.ok(text.includes(`[${label}](${publicUrl})`), label);
    assert.ok(read('README.md').includes(publicUrl), `README public navigation ${label}`);
    assert.ok(read('THREADLIGHT.md').includes(publicUrl), `technical briefing public navigation ${label}`);
  }
});
