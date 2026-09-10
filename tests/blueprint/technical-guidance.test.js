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
