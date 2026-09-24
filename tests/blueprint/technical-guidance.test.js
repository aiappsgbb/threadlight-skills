const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const docs = {
  guide: 'docs/agent-operations.md',
  readiness: 'docs/production-readiness.md',
  router: 'docs/ci/router-validation.md',
  e2e: 'docs/ci/threadlight-e2e.md',
  bridge: 'docs/KRATOS-BRIDGE.md',
  workshop: 'docs/WORKSHOP-1H-QUICKSTART.md',
};

test('presenter-ready is one opt-in process-owned contract across the six skills', () => {
  for (const skill of ['design', 'deploy', 'safe-check', 'local-test', 'workspace-ui', 'auto']) {
    const text = read(`skills/threadlight-${skill}/SKILL.md`);
    assert.match(text, /presenter-ready/, skill);
    assert.match(text, /presenter-ready\.md/, skill);
  }
  const text = read('docs/presenter-ready.md');
  for (const term of ['source-ready', 'deployed', 'backend-verified', 'script-verified',
    'human-accepted', 'independent readback', 'uncertain', 'immutable', 'Citadel',
    'model tokens', 'model rounds', 'logical tool calls', 'resource units']) {
    assert.ok(text.includes(term), term);
  }
  assert.match(text, /offline.*not.*hosted/i);
  assert.match(text, /second.*PoC/i);
  assert.match(text, /must not.*refresh/i);
  const guidance = JSON.parse(text.match(/<!-- deployment-guidance -->\s*```json\n([\s\S]*?)```/)[1]);
  assert.deepEqual(guidance, JSON.parse(read('skills/_shared/presenter-deployment-pin.json')));
});

test('model guidance separates operator-owned authoring from runtime deployment choices', () => {
  const text = read('skills/threadlight-design/references/model-selection.md').replace(/\s+/g, ' ');
  assert.match(text, /authoring[\s\S]*discovery[\s\S]*specification[\s\S]*architecture[\s\S]*code generation/i);
  assert.match(text, /sufficiently capable[\s\S]*complexity[\s\S]*explicit operator choice/i);
  assert.match(text, /does not (?:imply|require)[\s\S]{0,100}same[\s\S]{0,60}runtime/i);
  assert.match(text, /lower.cost runtime[\s\S]{0,140}does not require[\s\S]{0,100}authoring/i);
  assert.match(text, /cannot silently switch[\s\S]{0,150}host model/i);
  assert.match(text, /operator.supported[\s\S]{0,100}selection/i);
  assert.match(text, /never[\s\S]{0,50}global[\s\S]{0,40}settings/i);
  assert.match(text, /MODEL_DEPLOYMENT_NAME[\s\S]{0,130}not[\s\S]{0,40}(?:coding|authoring)/i);
  assert.match(text, /foundation\.md[\s\S]{0,100}runtime[\s\S]{0,150}not[\s\S]{0,50}(?:builder|authoring)/i);
});

test('runtime optimization preserves a fixed artifact and predeclared promotion constraints', () => {
  const text = read('skills/threadlight-design/references/model-selection.md').replace(/\s+/g, ' ');
  assert.match(text, /optional[\s\S]{0,120}lower.cost runtime/i);
  assert.match(text, /one[\s\S]{0,40}(?:validated |fixed )?artifact[\s\S]{0,100}runtime model/i);
  assert.match(text, /do not regenerate/i);
  for (const invariant of ['source', 'skills', 'tools', 'fixtures', 'held-out', 'validators']) {
    assert.ok(text.includes(invariant), invariant);
  }
  assert.match(text, /2x2[\s\S]{0,150}both builder artifacts[\s\S]{0,100}not required/i);
  assert.match(text, /predeclare[\s\S]*quality[\s\S]*tool[\s\S]*structured.output[\s\S]*PII[\s\S]*safety[\s\S]*governance[\s\S]*latency/i);
  assert.match(text, /negative cases[\s\S]*paraphrases/i);
  assert.match(text, /skill bod(?:y|ies)[\s\S]{0,200}catalog/i);
  assert.match(text, /preserve[\s\S]{0,100}failures[\s\S]{0,100}guardrail violations/i);
  assert.match(text, /keep[\s\S]{0,60}stronger runtime[\s\S]{0,120}never relax controls/i);
  assert.match(text, /avoid[\s\S]{0,50}bug[\s\S]{0,100}not[\s\S]{0,30}fix/i);
});

test('runtime comparisons disclose compatibility, full cost accounting and authorization boundaries', () => {
  const text = read('skills/threadlight-design/references/model-selection.md').replace(/\s+/g, ' ');
  assert.match(text, /actual[\s\S]{0,60}identity[\s\S]{0,40}version/i);
  assert.match(text, /protocol[\s\S]{0,80}tool[\s\S]{0,80}reasoning[\s\S]{0,80}compatib/i);
  assert.match(text, /client\/API adaptation[\s\S]*matched[\s\S]*never silently disable reasoning/i);
  assert.match(text, /region[\s\S]{0,60}data.boundary[\s\S]{0,60}capacity/i);
  assert.match(text, /construction[\s\S]{0,80}credits[\s\S]{0,60}iterations[\s\S]{0,60}review[\s\S]{0,120}separate/i);
  assert.match(text, /cost per correctly completed business task[\s\S]*failed.case spend/i);
  assert.match(text, /zero[\s\S]{0,50}correct[\s\S]{0,100}(?:undefined|unavailable)/i);
  assert.match(text, /list.price estimates[\s\S]{0,100}(?:bills|invoices)/i);
  assert.match(text, /cache[\s\S]{0,80}reasoning[\s\S]{0,80}assumptions/i);
  assert.match(text, /no automatic[\s\S]{0,80}paid[\s\S]{0,80}deployment/i);
  assert.match(text, /no[\s\S]{0,60}hidden downgrade[\s\S]{0,60}fallback/i);
  assert.match(text, /runtime-policy\.json[\s\S]*governance[\s\S]*authority/i);
});

test('SPEC model guidance does not override evidence-based runtime selection with a tool-count rule', () => {
  const spec = read('skills/threadlight-design/references/speckit-template.md')
    .split('## 7b. AI Services & Model Selection')[1].split('## 8.')[0]
    .replace(/\s+/g, ' ');
  assert.match(spec, /runtime[\s\S]{0,100}not[\s\S]{0,60}authoring/i);
  assert.match(spec, /lower.cost[\s\S]*fixed.artifact[\s\S]*thresholds/i);
  assert.doesNotMatch(spec, /Use ONLY when the agent has|never for the main agent loop/i);
  assert.match(spec, /model-selection\.md/);
});

test('model-choice handoffs resolve to one canonical reference and the A/B example changes only runtime', () => {
  const canonical = 'skills/threadlight-design/references/model-selection.md';
  for (const file of [
    'skills/threadlight-design/SKILL.md', 'skills/threadlight-auto/SKILL.md',
    'skills/threadlight-local-test/SKILL.md', 'skills/threadlight-evals/SKILL.md',
    'skills/threadlight-evals/references/ab-comparison.md',
  ]) {
    const text = read(file);
    const links = [...text.matchAll(/\[[^\]]+\]\(([^)]+model-selection\.md)(?:#[^)]*)?\)/g)];
    assert.ok(links.some(([, target]) =>
      path.resolve(root, path.dirname(file), target) === path.resolve(root, canonical)),
    `${file}: link to canonical model guidance must resolve`);
    assert.match(text, /authoring|fixed.artifact/i, file);
  }
  const ab = read('skills/threadlight-evals/references/ab-comparison.md');
  const example = ab.match(/```yaml\n([\s\S]*?)```/)[1];
  const prompts = [...example.matchAll(/^\s+prompt:\s*(\S+)/gm)].map(([, value]) => value);
  const models = [...example.matchAll(/^\s+model:\s*(\S+)/gm)].map(([, value]) => value);
  assert.equal(prompts.length, 2);
  assert.equal(prompts[0], prompts[1], 'runtime-only comparison must not also change prompt');
  assert.equal(models.length, 2);
  assert.notEqual(models[0], models[1]);
  assert.match(ab, /presence[\s\S]{0,100}not[\s\S]{0,80}(?:execution|promotion)/i);
});

test('MAF generation handoffs preserve autonomous trusted reads and separate approvals', () => {
  for (const file of [
    'skills/threadlight-design/SKILL.md', 'skills/threadlight-local-test/SKILL.md',
    'skills/threadlight-deploy/SKILL.md', 'skills/threadlight-auto/SKILL.md',
  ]) {
    assert.match(read(file), /maf-skill-approval\.md/, file);
  }
  const policy = read('skills/_shared/maf-skill-approval.md');
  for (const term of ['1.10', '1.13.0', 'disable_load_skill_approval',
    'disable_read_skill_resource_approval', 'disable_run_skill_script_approval',
    'user_input_requests', 'server_label']) {
    assert.ok(policy.includes(term), term);
  }
  assert.match(policy, /trusted local/i);
  assert.match(policy, /same.name/i);
  assert.match(policy, /approval_required/);
});

test('all current CI handoffs describe the same fail-closed release boundary', () => {
  for (const file of ['skills/threadlight-cicd/SKILL.md',
    'skills/threadlight-production-ready/SKILL.md', 'docs/agent-operations.md',
    'docs/production-readiness.md']) {
    const text = read(file);
    assert.match(text, /verified.release/i, file);
    assert.match(text, /release-contract\.md/, file);
  }
  const skill = read('skills/threadlight-production-ready/SKILL.md');
  assert.doesNotMatch(skill, /This is the \*basic\* scaffold|which writes 2 files/);
  assert.match(read('skills/threadlight-cicd/references/pipeline-design-checklist.md'),
    /receipt.*SHA-256|SHA-256.*receipt/s);
  for (const recipe of ['REL-102', 'OBS-102']) {
    const text = read(`skills/threadlight-production-ready/references/remediation-recipes/${recipe}.md`);
    assert.doesNotMatch(text, /already contains.*job stub/);
    assert.match(text, /release.contract/);
  }
  for (const template of ['azd-deploy-prod.yml.tmpl', 'central-team-uami-readme.md.tmpl']) {
    assert.ok(!fs.existsSync(path.join(root, 'skills/threadlight-production-ready/references/cicd-templates', template)));
  }
});

test('EU coverage documents inventory predicates, not universal green or schema validation', () => {
  for (const file of ['docs/eu-ai-act-evidence.md',
    'skills/threadlight-production-ready/references/eu-ai-act-mapping.md']) {
    const text = read(file);
    assert.match(text, /non-empty (?:JSON )?(?:objects|dicts).*lists/i, file);
    assert.match(text, /Art(?:icle)?\.? 11[\s\S]*Art(?:icle)?\.? 15/, file);
    assert.match(text, /schema-invalid/i, file);
    assert.match(text, /not (?:a )?certification/i, file);
    assert.doesNotMatch(text, /Every required source is present and its signal is green|missing or malformed.*treated as absent/i, file);
  }
});

test('readiness runs from a pinned complete catalog, not a copied single file', () => {
  for (const file of [docs.readiness, 'skills/threadlight-production-ready/SKILL.md']) {
    const text = read(file);
    assert.match(text, /CATALOG_REVISION=/, file);
    assert.match(text, /CATALOG_REVISION=8153bc2e0a677d99b8414053d7a00cfdab495444/, file);
    assert.match(text, /production_ready\.py" --root "\$PROJECT"/, file);
    assert.match(text, /skills\/_shared/, file);
    assert.match(text, /pyyaml/i, file);
    assert.doesNotMatch(text, /python(?:3)? tests\/production_ready\.py|Both invocations are supported|Copy as a tests script/, file);
  }
});

test('every readiness example selects the pilot separately from the catalog', () => {
  const invocation = 'python3 "$CATALOG/skills/threadlight-production-ready/scripts/production_ready.py" --root "$PROJECT"';
  for (const file of ['THREADLIGHT.md', 'skills/threadlight-production-ready/SKILL.md']) {
    const commands = read(file).split('\n')
      .filter((line) => /^\s*python(?:3)?\s+.*production_ready\.py/.test(line));
    assert.ok(commands.length >= 3, `${file}: expected readiness examples`);
    for (const command of commands) {
      assert.ok(command.trim().startsWith(invocation),
        `${file}: readiness command must use CATALOG and --root PROJECT: ${command}`);
    }
  }
  const howTo = read('skills/threadlight-production-ready/SKILL.md')
    .split('## How to invoke')[1].split('## Framing wizard questions')[0];
  assert.ok(howTo.includes('cd "$PROJECT"'),
    'onboarding/scaffold examples must also select the pilot working directory');
});

test('readiness dependency summary preserves signed-governance prerequisites', () => {
  const text = read('skills/threadlight-production-ready/SKILL.md');
  assert.ok(text.includes('control-plane package'), 'keep signed-governance dependency guidance');
  assert.ok(!/The CLI is \*\*one file\*\*|Dependencies: stdlib \+ `az` CLI subprocess only/.test(text),
    'remove the obsolete standalone/stdlib-only dependency summary');
});

test('EU reproducibility includes evaluation time and current freshness', () => {
  const text = read('docs/eu-ai-act-evidence.md');
  assert.ok(text.includes('generated_at'), 'document the generated wall-clock timestamp');
  assert.ok(/deterministic[\s\S]{0,160}evaluation time/i.test(text),
    'qualify determinism by evaluation time');
  assert.ok(/Article 9[\s\S]{0,180}(freshness|expir)/i.test(text),
    'unchanged artifacts can lose Article 9 coverage as evidence expires');
  assert.ok(!text.includes('Identical inputs produce deterministic outputs'),
    'do not promise byte-identical CLI output for unchanged artifact files');
});

test('EU fingerprints describe normalized text rather than exact source bytes', () => {
  const text = read('docs/eu-ai-act-evidence.md');
  assert.ok(/normalized.text/i.test(text), 'identify normalized-text fingerprints');
  assert.ok(text.includes('UTF-8') && text.includes('CRLF'),
    'explain encoding and newline normalization for source-hash comparisons');
  assert.ok(/not[\s\S]{0,80}(source.byte|on.disk byte)/i.test(text),
    'distinguish the emitted digest from a source-byte digest');
  assert.ok(!text.includes('These bind bytes'), 'do not overstate source-byte provenance');
});

test('router recovery is explicitly operator-owned, with snapshot and concurrency limits', () => {
  const text = read(docs.router);
  assert.match(text, /no (?:automatic )?trap/i);
  assert.match(text, /\/tmp\/model-router-routing\.snapshot\.json/);
  assert.match(text, /record[\s\S]*set[\s\S]*restore/);
  assert.match(text, /presence[\s\S]*not[\s\S]*equality/i);
  assert.match(text, /hardcoded/i);
  assert.match(text, /interrupt|cancel/i);
  assert.doesNotMatch(text, /on exit via (?:a )?trap|experiment never leaves|verified live every run/i);
});

test('router figures are token-cost estimates with price and attribution provenance', () => {
  const text = read(docs.router);
  assert.match(text, /estimated token cost/i);
  assert.match(text, /Azure Monitor/);
  assert.match(text, /load_prices\(None\)/);
  assert.match(text, /SEED_PRICES/);
  assert.match(text, /catalog commit/i);
  assert.match(text, /unrelated traffic/i);
  assert.doesNotMatch(text, /real billed Azure spend|Cost is \*\*billed Azure spend|reads real Azure billing/i);
});

test('Kratos safe-check requires explicit coding-agent adaptation and a real manifest', () => {
  for (const file of [docs.bridge, 'skills/threadlight-safe-check/SKILL.md']) {
    const text = read(file);
    assert.match(text, /coding.agent/i, file);
    assert.match(text, /--manifest specs\/manifest\.json/, file);
    assert.match(text, /threadlight-design\/SKILL\.md/, file);
    assert.match(text, /exit (?:code )?2/i, file);
    assert.doesNotMatch(text, /Pass `--from-infra`|is NOT raised|absence is a \*\*pass\*\*/i, file);
  }
});

test('readiness scoring and optional hard gate follow the implementation', () => {
  for (const file of [docs.readiness, 'skills/threadlight-production-ready/SKILL.md']) {
    const text = read(file);
    assert.match(text, /`should-fix`[^\n]*1\/4/, file);
    assert.match(text, /`not-applicable`[^\n]*excluded/i, file);
    assert.match(text, /`not-verified`[^\n]*0\/4/, file);
    assert.match(text, /`waived`[^\n]*3\/4/, file);
    assert.match(text, /--include-experimental/, file);
    assert.match(text, /--gate-preview[\s\S]*exit(?:s)? (?:code )?`?2/, file);
    assert.match(text, /would_fail_hard_gate[\s\S]*raw[\s\S]*must-fix/, file);
    assert.doesNotMatch(text, /never returns non-zero for findings|never turns into a deployment blocker|counts as pass for raw|excluded from raw score/i, file);
  }
});

test('implemented resume path is distinct from the legacy azd blocker', () => {
  const text = read(docs.readiness);
  assert.match(text, /resume-signed-bootstrap\/v1[\s\S]*postdeploy/);
  assert.match(text, /external prerequisites|operator prerequisites/i);
  assert.doesNotMatch(text, /cannot be reached by a successful protected deployment/);
});

test('AgentOps merge, checkout availability and historical snapshot are distinct', () => {
  const text = read(docs.guide);
  for (const value of ['2026-09-08T21:27:37Z',
    '19610ca8a3b5e3bd9cff16536442cfc2ea69a717',
    '4f59f7584a5f5d614c3625aa45f92f6a692c2194']) {
    assert.ok(text.includes(value), value);
  }
  assert.match(text, /checkout[\s\S]*PR #127/);
  assert.match(text, /23.skill/i);
  assert.match(text, /local process provenance, not Azure attestation/);
  assert.doesNotMatch(text, /this documentation work is based on|the local checkout does not contain/);
  assert.match(text, /A checkout through PR #127.*23.skill/s);
});

test('e2e setup is a real local section with identity, target and approval prerequisites', () => {
  const text = read(docs.e2e);
  assert.doesNotMatch(text, /threadlight-e2e-setup\.md/);
  assert.match(text, /## One-time prerequisites/);
  assert.match(text, /repo:aiappsgbb\/threadlight-skills:environment:e2e-ci/);
  for (const secret of ['AZURE_CLIENT_ID', 'AZURE_TENANT_ID',
    'AZURE_SUBSCRIPTION_ID', 'AZURE_AI_ENDPOINT']) {
    assert.ok(text.includes(secret), secret);
  }
  assert.match(text, /approval|approved/i);
});

test('workshop ends at a pilot and separates selected-binding governance follow-up', () => {
  const text = read(docs.workshop);
  assert.match(text, /deployed.*smoke-tested pilot/i);
  assert.match(text, /selected-binding governance/i);
  assert.match(text, /LOCAL-14/);
  assert.match(text, /governance_probe_noop/);
  assert.match(text, /agent-operations\.md/);
  assert.doesNotMatch(text, /→ governed Foundry agent|AGT v4 detection is automatic|Soft-gate — never fails/i);
});

test('L400/L500 operative spine links configure through rescore with real CLI and stop rules', () => {
  const text = read(docs.guide);
  assert.match(text, /L400\/L500/);
  assert.match(text, /L200\/L300/);
  for (const heading of ['Configure', 'Generate', 'Validate',
    'Approved deployment', 'Collect', 'Rescore']) {
    assert.match(text, new RegExp(`^### \\d+\\. ${heading}`, 'm'));
  }
  for (const token of ['runtime_readiness.py', '--configuration', '--project',
    'governed_actions.py', '--target', '--emit --gate', 'production_ready.py',
    '--root', '--static', '--gate-preview', 'Stop', 'Recovery']) {
    assert.ok(text.includes(token), token);
  }
  for (const file of ['README.md', 'THREADLIGHT.md']) {
    assert.match(read(file), /L400\/L500/, file);
    assert.match(read(file), /L200\/L300/, file);
  }
});

test('operative artifact names match the existing governed-actions renderer', () => {
  const renderer = read('skills/threadlight-governed-actions/scripts/render.py');
  const artifacts = [...renderer.matchAll(/^DEFAULT_\w+_RELATIVE_PATH = Path\("([^"]+)"\)/gm)];
  assert.equal(artifacts.length, 3);
  for (const [, artifact] of artifacts) {
    assert.ok(read(docs.guide).includes(artifact), `missing actual artifact ${artifact}`);
  }
});

test('scoped technical guides use existing local file and heading targets', () => {
  for (const file of [docs.guide, docs.e2e, docs.bridge]) {
    const text = read(file);
    for (const [, href] of text.matchAll(/\]\(([^)\s]+)\)/g)) {
      if (/^[a-z]+:/i.test(href)) continue;
      const [relative, fragment] = href.split('#');
      const target = relative ? path.resolve(root, path.dirname(file), relative)
        : path.resolve(root, file);
      assert.ok(fs.existsSync(target), `${file}: missing ${href}`);
      if (fragment && target.endsWith('.md')) {
        const targetText = fs.readFileSync(target, 'utf8');
        const slugs = [...targetText.matchAll(/^#{1,6} (.+)$/gm)].map((match) =>
          match[1].toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-'));
        assert.ok(slugs.includes(fragment), `${file}: missing anchor ${href}`);
      }
    }
  }
});
