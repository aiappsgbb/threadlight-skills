const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.join(__dirname, '../..');
const policyPath = path.join(repoRoot, 'skills/threadlight-design/references/runtime-policy.json');
const consumerPaths = [
  'skills/threadlight-design/references/foundation-template.md',
  'skills/threadlight-design/SKILL.md',
  'skills/threadlight-deploy/SKILL.md',
  'skills/threadlight-auto/SKILL.md',
  'THREADLIGHT.md',
];

function read(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

function loadPolicy() {
  return JSON.parse(fs.readFileSync(policyPath, 'utf8'));
}

function assertValidSelector(policy, kind, value, context) {
  assert.ok(
    policy.selectors[kind].includes(value),
    `${context} must use a supported ${kind} selector`,
  );
}

test('runtime policy file declares the supported selectors and valid default route', () => {
  const policy = loadPolicy();

  assert.strictEqual(policy.schema, 'threadlight.runtime-policy/v1');
  assert.strictEqual(policy.version, 1);

  assert.deepStrictEqual(policy.selectors.frameworks, [
    'github-copilot-sdk',
    'microsoft-agent-framework',
  ]);
  assert.deepStrictEqual(policy.selectors.runtime_shapes, ['agent', 'workflow']);
  assert.deepStrictEqual(policy.selectors.protocols, ['invocations', 'responses']);

  assertValidSelector(policy, 'frameworks', policy.default.framework, 'default');
  assertValidSelector(policy, 'runtime_shapes', policy.default.runtime_shape, 'default');
  assertValidSelector(policy, 'protocols', policy.default.protocol, 'default');
  assert.strictEqual(policy.default.framework, 'github-copilot-sdk');
  assert.strictEqual(policy.default.runtime_shape, 'agent');
  assert.strictEqual(policy.default.protocol, 'invocations');
  assert.strictEqual(policy.default.policy_route, 'default-agent');
});

test('runtime policy routes are ordered, non-empty, and use supported selectors', () => {
  const policy = loadPolicy();

  assert.ok(Array.isArray(policy.routes) && policy.routes.length > 0);
  const priorities = policy.routes.map((route) => route.priority);
  assert.deepStrictEqual(priorities, [...priorities].sort((a, b) => a - b));
  assert.strictEqual(new Set(priorities).size, priorities.length, 'route priorities must be unique');

  for (const route of policy.routes) {
    assert.ok(route.id, 'route.id must be non-empty');
    assert.ok(route.when, `${route.id} must declare when`);
    assert.ok(route.rationale, `${route.id} must declare a rationale`);

    if (route.selection === 'operator') {
      assert.strictEqual(route.id, 'explicit-supported-choice');
      continue;
    }

    assertValidSelector(policy, 'frameworks', route.framework, route.id);
    assertValidSelector(policy, 'runtime_shapes', route.runtime_shape, route.id);
    assertValidSelector(policy, 'protocols', route.protocol, route.id);
  }

  assert.deepStrictEqual(
    policy.routes.map((route) => route.id),
    [
      'explicit-supported-choice',
      'deterministic-workflow',
      'maf-agent-capabilities',
      'default-agent',
    ],
  );
  assert.strictEqual(policy.routes[0].selection, 'operator');
  assert.strictEqual(policy.routes[1].runtime_shape, 'workflow');
  assert.strictEqual(policy.routes[1].framework, 'microsoft-agent-framework');
  assert.strictEqual(policy.routes[1].protocol, 'responses');
  assert.strictEqual(policy.routes[2].framework, 'microsoft-agent-framework');
  assert.strictEqual(policy.routes[2].runtime_shape, 'agent');
  assert.strictEqual(policy.routes[2].protocol, 'responses');
  assert.strictEqual(policy.routes[3].framework, 'github-copilot-sdk');
  assert.strictEqual(policy.routes[3].runtime_shape, 'agent');
  assert.strictEqual(policy.routes[3].protocol, 'invocations');
});

test('runtime policy is referenced by every documented consumer', () => {
  for (const consumerPath of consumerPaths) {
    assert.match(
      read(consumerPath),
      /references\/runtime-policy\.json/,
      `${consumerPath} must reference runtime-policy.json`,
    );
  }
});

test('foundation template and deploy skill repeat the canonical default selectors', () => {
  for (const consumerPath of [
    'skills/threadlight-design/references/foundation-template.md',
    'skills/threadlight-deploy/SKILL.md',
  ]) {
    const content = read(consumerPath);
    assert.match(content, /github-copilot-sdk/, `${consumerPath} must name github-copilot-sdk`);
    assert.match(content, /agent/, `${consumerPath} must name agent`);
    assert.match(content, /invocations/, `${consumerPath} must name invocations`);
  }

  assert.match(
    read('skills/threadlight-design/references/foundation-template.md'),
    /policy_route:\s*default-agent/,
    'foundation template must record the default policy route',
  );
});
