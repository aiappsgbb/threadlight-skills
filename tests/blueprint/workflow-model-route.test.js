// 2.19.2 regression: a SPEC with `workflow_model: workflow` used to route
// threadlight-deploy to a phantom "workflow" skill that never existed, so
// agents told users to install it or ask the maintainer to publish it. Follow
// the real route resolution and check every instruction an agent reads on
// that path. Text contract only; it does not prove generated runtime behaviour.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(repoRoot, rel), 'utf8');
const policy = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json'));
const deploy = read('skills/threadlight-deploy/SKILL.md');
const design = read('skills/threadlight-design/SKILL.md');
const speckit = read('skills/threadlight-design/references/speckit-template.md');
const runtimeDoc = read('docs/skill-based-agents.md');

function knownSkills() {
  const names = new Set(
    fs.readdirSync(path.join(repoRoot, 'skills')).filter((d) =>
      fs.existsSync(path.join(repoRoot, 'skills', d, 'SKILL.md'))),
  );
  const lock = JSON.parse(read('skills/_shared/official-skills-lock.json'));
  Object.values(lock.plugins).forEach((p) => Object.keys(p.skills).forEach((s) => names.add(s)));
  Object.values(lock.catalog_skill_names || {}).forEach((list) => list.forEach((s) => names.add(s)));
  const deps = JSON.parse(read('skills/_shared/skill-dependencies.json'));
  deps.custom_catalog.skills.forEach((s) => names.add(s));
  return names;
}

function resolveRoute(signals) {
  return policy.routes.find((route) => {
    if (route.id === 'explicit-supported-choice') return false;
    if (route.when === 'no higher-priority route matches') return true;
    return route.when.split('||').some((clause) => {
      const [key, value] = clause.trim().split('=');
      return value === undefined ? signals[key] === true : signals[key] === value;
    });
  });
}

function between(text, start, end) {
  const from = text.indexOf(start);
  assert.ok(from >= 0, `missing section start: ${start}`);
  const to = text.indexOf(end, from + start.length);
  assert.ok(to > from, `missing section end: ${end}`);
  return text.slice(from, to);
}

const spec = {
  workflow_model: 'workflow',
  requires_toolbox: false,
  requires_custom_python_tools: false,
  requires_file_generation: false,
  latency_sensitive_data_queries: false,
};

const workflowPath = [
  ['required skills table', between(deploy, '| Skill or reference | When Needed |', '## Workflow')],
  ['1d runtime variant', between(deploy, '#### 1d. Choose runtime variant', '#### 1e.')],
  ['Workflow model gate', between(deploy, '**Workflow model gate.**', 'Create these files in the project root')],
  ['design workflow_model selector', between(design, 'workflow_model: agent', 'The trait matrix')],
  ['design classification', between(design, '**Both are valid.**', 'The\n   > choice affects')],
  ['design 11e contract', between(design, '11e. **Workflow Model**', '11f. **Deployment Posture**')],
  ['SPEC template 11e', between(speckit, '## 11e. Workflow Model', '> **Deriving `capability_signals`.**')],
  ['runtime-loading doc', between(runtimeDoc, '`workflow_model=workflow` selects', 'Do not force the workflow branch')],
  ['policy route rationale', JSON.stringify(policy.routes.find((r) => r.id === 'deterministic-workflow'))],
];

test('workflow_model=workflow resolves to the deterministic-workflow MAF/Responses route', () => {
  const route = resolveRoute(spec);
  assert.strictEqual(route.id, 'deterministic-workflow');
  assert.strictEqual(route.framework, 'microsoft-agent-framework');
  assert.strictEqual(route.protocol, 'responses');
});

test('no instruction on the workflow route names a skill that does not exist', () => {
  const names = knownSkills();
  const skillRef = /`([a-z0-9]+(?:-[a-z0-9]+)+)`\s+skill\b|\|\s*`([a-z0-9]+(?:-[a-z0-9]+)+)`\s*\|/g;
  for (const [label, text] of workflowPath) {
    assert.ok(!text.includes('threadlight-workflow'), `${label} names the phantom threadlight-workflow skill`);
    for (const match of text.matchAll(skillRef)) {
      const name = match[1] || match[2];
      assert.ok(names.has(name), `${label} references unknown skill \`${name}\``);
    }
  }
});

test('workflow route never asks the user to install, publish or request a missing skill', () => {
  for (const [label, text] of workflowPath.slice(1)) {
    assert.doesNotMatch(
      text,
      /\b(?:install|publish|maintainer|\/skills list|when it is installed|not installed|unavailable)\b/i,
      `${label} must not route the user to a missing skill`,
    );
  }
});

test('workflow route states its own deterministic behaviour: MAF Agent container, graph not shipped', () => {
  const gate = workflowPath.find(([label]) => label === 'Workflow model gate')[1];
  assert.match(gate, /MAF\s+Agent/);
  assert.match(gate, /Responses/);
  assert.match(gate, /not\s+shipped/i);
  assert.match(gate, /do not stop/i);
  for (const [label, text] of workflowPath) {
    assert.doesNotMatch(
      text,
      /DurableWorkflow container|scaffolds a DurableWorkflow|generated workflow code|MAF workflow runtime/,
      `${label} must not promise generated workflow-graph code`,
    );
  }
  const variant = workflowPath.find(([label]) => label === '1d runtime variant')[1];
  assert.match(variant, /not\s+shipped/i, '§1d must say workflow-graph generation is not shipped');
});
