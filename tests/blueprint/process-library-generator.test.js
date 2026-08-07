const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const L = require('../../docs/assets/blueprint-logic.js');

const repoRoot = path.join(__dirname, '../..');
const generatorPath = path.join(repoRoot, 'scripts/build_process_library.py');

const BASE_SKILLS = [
  'threadlight-design',
  'threadlight-local-test',
  'threadlight-safe-check',
  'threadlight-deploy',
  'threadlight-cicd',
  'threadlight-evals',
];

const BASE_ARTIFACTS = [
  'specs/foundation.md',
  'specs/SPEC.md',
  'specs/manifest.json',
  'AGENTS.md',
  'src/agent/skills/*/SKILL.md',
  'docs/safe-check-post.md',
  'azure.yaml',
  'infra/main.bicep',
  '.github/workflows/azd-deploy-prod.yml',
  'specs/evals-manifest.json',
];

const FULL_SKILLS = [
  'threadlight-design',
  'threadlight-demo-data-factory',
  'threadlight-local-test',
  'threadlight-hitl-patterns',
  'threadlight-event-triggers',
  'threadlight-safe-check',
  'threadlight-redteam',
  'threadlight-govern',
  'threadlight-deploy',
  'threadlight-cicd',
  'threadlight-production-ready',
  'threadlight-evals',
  'threadlight-consumption-iq',
];

const FULL_ARTIFACTS = [
  'specs/foundation.md',
  'specs/SPEC.md',
  'specs/manifest.json',
  'AGENTS.md',
  'src/agent/skills/*/SKILL.md',
  'specs/sample-data/*.json',
  'src/agent/skills/*/cards/*.json',
  'src/triggers/*/',
  'infra/triggers/*.bicep',
  'docs/safe-check-post.md',
  'docs/redteam-report.md',
  'specs/redteam-manifest.json',
  'specs/govern-manifest.json',
  'azure.yaml',
  'infra/main.bicep',
  '.github/workflows/azd-deploy-prod.yml',
  'docs/production-readiness-report.md',
  'tests/production-readiness-manifest.json',
  'specs/evals-manifest.json',
  'docs/cost-projection.md',
  'specs/cost-manifest.json',
];

const PREREQUISITES = [
  'github-copilot',
  'threadlight-skills',
  'azure-subscription',
];

function makeWorkDir(label) {
  // A fresh OS-temp scratch directory per run — never a repo-root path — so
  // a killed/failed run can't leave stray untracked directories behind in
  // the working tree; cleanup() below removes it fully.
  return fs.mkdtempSync(path.join(os.tmpdir(), `threadlight-playbook-${label}-`));
}

function runGenerator(entries, label) {
  const workDir = makeWorkDir(label);
  const sourcePath = path.join(workDir, 'source.json');
  const outPath = path.join(workDir, 'out.json');
  fs.writeFileSync(sourcePath, JSON.stringify(entries, null, 2));

  const result = spawnSync(
    'python3',
    [generatorPath, '--source', sourcePath, '--out', outPath],
    { cwd: repoRoot, encoding: 'utf8' }
  );

  return {
    workDir,
    outPath,
    result,
    output: result.status === 0 ? JSON.parse(fs.readFileSync(outPath, 'utf8')) : null,
  };
}

function cleanup(workDir) {
  fs.rmSync(workDir, { recursive: true, force: true });
}

test('generator adds baseline playbook metadata for a low retail entry', () => {
  const entry = {
    id: 'retail-order-routing',
    name: 'Retail Order Routing',
    industry: 'retail',
    complexity: 'low',
    summary: 'Routes retail exception orders for review.',
    description: 'A concise retail order-routing process.',
    tags: [],
    business_constraints: [],
    external_integrations: [],
    human_approvals: [],
    knowledge_sources: [],
  };

  const run = runGenerator([entry], 'baseline');
  try {
    assert.strictEqual(run.result.status, 0, run.result.stderr);
    const playbook = run.output[0].playbook;
    assert.strictEqual(playbook.schema, 'threadlight.playbook/v1');
    assert.strictEqual(playbook.level, 'Starter');
    assert.strictEqual(playbook.use_when, entry.summary);
    assert.deepStrictEqual(playbook.build_skills, BASE_SKILLS);
    assert.deepStrictEqual(playbook.build_skills, L.deriveSkills(run.output[0]));
    assert.deepStrictEqual(playbook.run_skills, []);
    assert.strictEqual(playbook.run_skills_source, 'generated-by-threadlight-design');
    assert.deepStrictEqual(playbook.prerequisites, PREREQUISITES);
    assert.deepStrictEqual(playbook.artifacts, BASE_ARTIFACTS);
  } finally {
    cleanup(run.workDir);
  }
});

test('generator derives conditional skills and deduplicated artifacts in canonical order', () => {
  const entry = {
    id: 'financial-ops-triage',
    name: 'Financial Ops Triage',
    industry: 'financial_services',
    complexity: 'high',
    summary: 'Routes regulated financial operations work.',
    use_when: 'When scheduled financial operations require compliance-heavy triage.',
    description: 'A regulated scheduled workflow.',
    tags: ['scheduled', 'compliance'],
    business_constraints: [],
    external_integrations: [{ name: 'Core system' }],
    human_approvals: [{ step: 'Manager approval' }],
    knowledge_sources: [],
  };

  const run = runGenerator([entry], 'conditional');
  try {
    assert.strictEqual(run.result.status, 0, run.result.stderr);
    const outputEntry = run.output[0];
    assert.strictEqual(outputEntry.playbook.use_when, entry.use_when);
    assert.deepStrictEqual(outputEntry.playbook.build_skills, FULL_SKILLS);
    assert.deepStrictEqual(outputEntry.playbook.build_skills, L.deriveSkills(outputEntry));
    assert.deepStrictEqual(outputEntry.playbook.artifacts, FULL_ARTIFACTS);
    assert.strictEqual(new Set(outputEntry.playbook.artifacts).size, outputEntry.playbook.artifacts.length);
  } finally {
    cleanup(run.workDir);
  }
});

test('generator adds event-trigger output artifacts when the skill is selected', () => {
  const entry = {
    id: 'scheduled-order-sync',
    name: 'Scheduled Order Sync',
    industry: 'retail',
    complexity: 'low',
    summary: 'Synchronizes orders on a schedule.',
    description: 'A scheduled order synchronization process.',
    tags: ['scheduled'],
    business_constraints: [],
    external_integrations: [],
    human_approvals: [],
    knowledge_sources: [],
  };

  const run = runGenerator([entry], 'event-trigger-artifacts');
  try {
    assert.strictEqual(run.result.status, 0, run.result.stderr);
    assert.ok(run.output[0].playbook.build_skills.includes('threadlight-event-triggers'));
    assert.deepStrictEqual(
      run.output[0].playbook.artifacts.filter((artifact) =>
        ['src/triggers/*/', 'infra/triggers/*.bicep'].includes(artifact)),
      ['src/triggers/*/', 'infra/triggers/*.bicep'],
    );
    assert.ok(
      !run.output[0].playbook.artifacts.includes('scripts/postdeploy.py'),
      'postdeploy.py is conditional on ACA Job receiver shape and is not a stable event-trigger artifact',
    );
  } finally {
    cleanup(run.workDir);
  }
});

test('generator rejects unsupported complexity values with exit code 2', () => {
  const run = runGenerator([{
    id: 'bad-complexity',
    name: 'Bad Complexity',
    industry: 'retail',
    complexity: 'expert',
    summary: 'Invalid complexity input.',
    description: 'Should fail.',
    tags: [],
    business_constraints: [],
    external_integrations: [],
    human_approvals: [],
    knowledge_sources: [],
  }], 'bad-complexity');

  try {
    assert.strictEqual(run.result.status, 2);
    assert.match(run.result.stderr, /invalid process-library entry: unsupported complexity expert/);
  } finally {
    cleanup(run.workDir);
  }
});

test('generator matches JS arr semantics for non-array integrations approvals and tags', () => {
  const run = runGenerator([{
    id: 'string-signals',
    name: 'String Signals',
    industry: 'retail',
    complexity: 'low',
    summary: 'String fields should not trigger conditional skills.',
    description: 'Should keep only baseline skills.',
    tags: 'scheduled',
    business_constraints: [],
    external_integrations: 'SAP',
    human_approvals: 'manager approval',
    knowledge_sources: [],
  }], 'string-signals');

  try {
    assert.strictEqual(run.result.status, 0, run.result.stderr);
    assert.deepStrictEqual(run.output[0].playbook.build_skills, BASE_SKILLS);
    assert.deepStrictEqual(run.output[0].playbook.build_skills, L.deriveSkills(run.output[0]));
  } finally {
    cleanup(run.workDir);
  }
});

test('generator rejects malformed explicit use_when', () => {
  const run = runGenerator([{
    id: 'bad-use-when',
    name: 'Bad Use When',
    industry: 'retail',
    complexity: 'low',
    summary: 'Fallback summary.',
    use_when: '   ',
    description: 'Should fail.',
    tags: [],
    business_constraints: [],
    external_integrations: [],
    human_approvals: [],
    knowledge_sources: [],
  }], 'bad-use-when');

  try {
    assert.strictEqual(run.result.status, 2);
    assert.match(run.result.stderr, /invalid process-library entry: malformed use_when/);
  } finally {
    cleanup(run.workDir);
  }
});
