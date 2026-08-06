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
const selectorKeys = ['framework', 'runtime_shape', 'protocol'];

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

function selectorTuple(source) {
  return Object.fromEntries(selectorKeys.map((key) => [key, source[key]]));
}

function tupleKey(source) {
  return selectorKeys.map((key) => `${key}=${source[key]}`).join('|');
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function canonicalDefaultSentence(policy) {
  return `Canonical default tuple: \`${policy.default.framework}\` + \`${policy.default.runtime_shape}\` + \`${policy.default.protocol}\` (\`policy_route: ${policy.default.policy_route}\`).`;
}

function canonicalDefaultSentenceRegex(policy) {
  const gap = '(?:\\s|>\\s*)*';
  return new RegExp(
    [
      'Canonical default tuple:',
      `\`${escapeRegex(policy.default.framework)}\``,
      '\\+',
      `\`${escapeRegex(policy.default.runtime_shape)}\``,
      '\\+',
      `\`${escapeRegex(policy.default.protocol)}\``,
      `\\(\`policy_route: ${escapeRegex(policy.default.policy_route)}\`\\)\\.`,
    ].join(gap),
  );
}

function defaultTupleBlockRegex(policy) {
  return new RegExp(
    [
      `framework:\\s*${escapeRegex(policy.default.framework)}`,
      `runtime_shape:\\s*${escapeRegex(policy.default.runtime_shape)}`,
      `protocol:\\s*${escapeRegex(policy.default.protocol)}`,
      `policy_route:\\s*${escapeRegex(policy.default.policy_route)}`,
    ].join('[\\s\\S]*?'),
  );
}

test('runtime policy file declares the supported selectors, compatible combinations, and valid default route', () => {
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

  assert.ok(
    Array.isArray(policy.compatible_combinations) && policy.compatible_combinations.length > 0,
    'compatible_combinations must be a non-empty array',
  );
  assert.deepStrictEqual(policy.compatible_combinations, [
    {
      framework: 'github-copilot-sdk',
      runtime_shape: 'agent',
      protocol: 'invocations',
    },
    {
      framework: 'microsoft-agent-framework',
      runtime_shape: 'agent',
      protocol: 'responses',
    },
    {
      framework: 'microsoft-agent-framework',
      runtime_shape: 'workflow',
      protocol: 'responses',
    },
  ]);

  const compatibleCombinationKeys = new Set();
  for (const combination of policy.compatible_combinations) {
    assertValidSelector(policy, 'frameworks', combination.framework, 'compatible combination');
    assertValidSelector(policy, 'runtime_shapes', combination.runtime_shape, 'compatible combination');
    assertValidSelector(policy, 'protocols', combination.protocol, 'compatible combination');
    compatibleCombinationKeys.add(tupleKey(combination));
  }

  assert.strictEqual(
    compatibleCombinationKeys.size,
    policy.compatible_combinations.length,
    'compatible_combinations must contain unique selector tuples',
  );
  assert.ok(
    compatibleCombinationKeys.has(tupleKey(policy.default)),
    'compatible_combinations must include the default selector tuple',
  );
});

test('runtime policy routes are ordered, non-empty, and concrete routes align with compatible combinations', () => {
  const policy = loadPolicy();

  assert.ok(Array.isArray(policy.routes) && policy.routes.length > 0);
  const priorities = policy.routes.map((route) => route.priority);
  assert.deepStrictEqual(priorities, [...priorities].sort((a, b) => a - b));
  assert.strictEqual(new Set(priorities).size, priorities.length, 'route priorities must be unique');

  const compatibleCombinationKeys = new Set(policy.compatible_combinations.map(tupleKey));
  const concreteRouteKeys = [];

  for (const route of policy.routes) {
    assert.ok(route.id, 'route.id must be non-empty');
    assert.ok(route.when, `${route.id} must declare when`);
    assert.ok(route.rationale, `${route.id} must declare a rationale`);

    if (route.selection === 'operator') {
      assert.strictEqual(route.id, 'explicit-supported-choice');
      assert.strictEqual(
        route.allowed_combinations_ref,
        'compatible_combinations',
        'explicit-supported-choice must validate against compatible_combinations',
      );
      assert.match(
        route.when,
        /compatible_combinations/,
        'explicit-supported-choice must direct operators to compatible_combinations',
      );
      continue;
    }

    assertValidSelector(policy, 'frameworks', route.framework, route.id);
    assertValidSelector(policy, 'runtime_shapes', route.runtime_shape, route.id);
    assertValidSelector(policy, 'protocols', route.protocol, route.id);
    concreteRouteKeys.push(tupleKey(route));
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

  assert.deepStrictEqual(
    [...new Set(concreteRouteKeys)].sort(),
    [...compatibleCombinationKeys].sort(),
    'concrete policy routes must cover each compatible selector tuple exactly once',
  );

  const defaultAgentRoute = policy.routes.find((route) => route.id === policy.default.policy_route);
  assert.ok(defaultAgentRoute, 'default policy route must exist');
  assert.deepStrictEqual(
    selectorTuple(defaultAgentRoute),
    selectorTuple(policy.default),
    'policy.default selector tuple must match the default-agent route tuple',
  );
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

test('runtime policy consumer docs state the canonical default tuple derived from policy.default', () => {
  const policy = loadPolicy();
  const canonicalSentence = canonicalDefaultSentenceRegex(policy);

  for (const consumerPath of consumerPaths) {
    assert.match(
      read(consumerPath),
      canonicalSentence,
      `${consumerPath} must state the canonical default tuple from policy.default`,
    );
  }
});

test('foundation template keeps the default selector tuple co-located with policy_route', () => {
  const policy = loadPolicy();
  const content = read('skills/threadlight-design/references/foundation-template.md');

  assert.match(
    content,
    defaultTupleBlockRegex(policy),
    'foundation template must keep framework/runtime_shape/protocol/policy_route co-located',
  );
});
