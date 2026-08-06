const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.join(__dirname, '../..');
const policyPath = path.join(repoRoot, 'skills/threadlight-design/references/runtime-policy.json');

// Exact skill-relative (or repo-root, for THREADLIGHT.md) path each consumer
// must use to reference the runtime-policy contract. `threadlight-design`'s
// own files are inside skills/threadlight-design/, so they use paths
// relative to that skill's own folder (`references/runtime-policy.json`,
// or the bare sibling filename from within references/ itself). Deploy and
// auto live in sibling skill folders, so they must reach across via
// `../threadlight-design/references/runtime-policy.json`. THREADLIGHT.md is
// a repo-root technical doc, so it uses the full repo-root path.
const consumerRuntimePolicyPaths = {
  'skills/threadlight-design/SKILL.md': 'references/runtime-policy.json',
  'skills/threadlight-design/references/foundation-template.md': 'runtime-policy.json',
  'skills/threadlight-deploy/SKILL.md': '../threadlight-design/references/runtime-policy.json',
  'skills/threadlight-auto/SKILL.md': '../threadlight-design/references/runtime-policy.json',
  'THREADLIGHT.md': 'skills/threadlight-design/references/runtime-policy.json',
};
const consumerPaths = Object.keys(consumerRuntimePolicyPaths);
const selectorKeys = ['framework', 'runtime_shape', 'protocol'];
const expectedBlockedWhen = [
  'workflow_model=workflow',
  'requires_toolbox',
  'requires_custom_python_tools',
  'requires_file_generation',
  'latency_sensitive_data_queries',
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

function selectorTuple(source) {
  return Object.fromEntries(selectorKeys.map((key) => [key, source[key]]));
}

function tupleKey(source) {
  return selectorKeys.map((key) => `${key}=${source[key]}`).join('|');
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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

// Pull `key: value` out of the first fenced code block that follows a given
// markdown heading — used to parse the machine-readable selector tuple out
// of specs/foundation.md-shaped documents without a full YAML parser.
function firstValueAfterHeading(content, headingPattern, key) {
  const headingMatch = content.match(headingPattern);
  assert.ok(headingMatch, `expected to find a heading matching ${headingPattern}`);
  const rest = content.slice(headingMatch.index + headingMatch[0].length);
  const fenceMatch = rest.match(/```yaml\n([\s\S]*?)```/);
  assert.ok(fenceMatch, 'expected a fenced yaml block after the heading');
  const block = fenceMatch[1];
  const keyMatch = block.match(new RegExp(`^${escapeRegex(key)}:\\s*(\\S+)`, 'm'));
  assert.ok(keyMatch, `expected key \`${key}\` in the fenced yaml block`);
  return keyMatch[1];
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

test('explicit-supported-choice declares the exact blocked_when capability-signal coverage', () => {
  const policy = loadPolicy();
  const explicitRoute = policy.routes.find((route) => route.id === 'explicit-supported-choice');

  assert.ok(explicitRoute, 'explicit-supported-choice route must exist');
  assert.ok(
    Array.isArray(explicitRoute.blocked_when),
    'explicit-supported-choice must declare a blocked_when array',
  );
  assert.deepStrictEqual(
    explicitRoute.blocked_when,
    expectedBlockedWhen,
    'explicit-supported-choice blocked_when must cover exactly the higher-priority capability signals',
  );

  // Every blocked_when signal must correspond to a `when` condition guarding
  // a higher-priority concrete route, so an explicit override can never rank
  // above the route that legitimately owns that capability signal.
  const combinedWhen = policy.routes
    .filter((route) => route.id !== 'explicit-supported-choice')
    .map((route) => route.when)
    .join(' || ');
  for (const signal of explicitRoute.blocked_when) {
    assert.ok(
      combinedWhen.includes(signal),
      `blocked_when signal \`${signal}\` must be owned by a documented higher-priority route`,
    );
  }
});

test('runtime policy is referenced by the exact skill-relative or repo-root path each consumer needs', () => {
  for (const consumerPath of consumerPaths) {
    const expectedReference = consumerRuntimePolicyPaths[consumerPath];
    const content = read(consumerPath);
    assert.ok(
      content.includes(expectedReference),
      `${consumerPath} must reference runtime-policy.json via \`${expectedReference}\``,
    );
  }
});

test('threadlight-design and foundation-template use skill-relative paths, never the repo-root form', () => {
  const repoRootForm = 'skills/threadlight-design/references/runtime-policy.json';
  for (const consumerPath of [
    'skills/threadlight-design/SKILL.md',
    'skills/threadlight-design/references/foundation-template.md',
  ]) {
    assert.ok(
      !read(consumerPath).includes(repoRootForm),
      `${consumerPath} must use a skill-relative path, not the repo-root form \`${repoRootForm}\``,
    );
  }
});

test('threadlight-deploy and threadlight-auto reach the design skill via ../threadlight-design, never the repo-root form', () => {
  const repoRootForm = 'skills/threadlight-design/references/runtime-policy.json';
  for (const consumerPath of ['skills/threadlight-deploy/SKILL.md', 'skills/threadlight-auto/SKILL.md']) {
    const content = read(consumerPath);
    assert.ok(
      !content.includes(repoRootForm),
      `${consumerPath} must reach threadlight-design via ../threadlight-design, not the repo-root form \`${repoRootForm}\``,
    );
    assert.ok(
      content.includes('../threadlight-design/references/runtime-policy.json'),
      `${consumerPath} must reference ../threadlight-design/references/runtime-policy.json`,
    );
  }
});

test('THREADLIGHT.md keeps the repo-root runtime-policy.json reference as a markdown link', () => {
  const content = read('THREADLIGHT.md');
  assert.match(
    content,
    /\[`?skills\/threadlight-design\/references\/runtime-policy\.json`?\]\(skills\/threadlight-design\/references\/runtime-policy\.json\)/,
    'THREADLIGHT.md must keep a repo-root markdown link to runtime-policy.json',
  );
});

test('foundation-template links its sibling runtime-policy.json instead of pasting a customer-broken filesystem path', () => {
  const content = read('skills/threadlight-design/references/foundation-template.md');
  const flattened = content.replace(/\n>\s?/g, ' ');
  assert.match(
    content,
    /\[`?runtime-policy\.json`?\]\(runtime-policy\.json\)/,
    'foundation-template.md must link its sibling runtime-policy.json with a relative markdown link',
  );
  assert.match(
    content,
    /schema:\s*threadlight\.runtime-policy\/v1/,
    'foundation-template.md must instruct recording the runtime-policy schema id in specs/foundation.md',
  );
  assert.match(
    flattened,
    /do not (?:paste|emit) a filesystem (?:link|path)/i,
    'foundation-template.md must warn against emitting a broken customer-project filesystem link',
  );
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

test('threadlight-deploy separates the Runtime-policy pre-flight from the Poly-Repo split remediation', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  const checklistHeadingMatch = content.match(/### Pre-flight checklist \(run this FIRST, before Phase 1\)/);
  const howToSplitHeadingMatch = content.match(/### How to split/);
  assert.ok(checklistHeadingMatch && howToSplitHeadingMatch, 'expected both Poly-Repo subsection headings');

  const checklistSection = content.slice(checklistHeadingMatch.index, howToSplitHeadingMatch.index);
  const stopBulletLines = checklistSection
    .split('\n')
    .filter((line) => /^- /.test(line.trim()));

  assert.strictEqual(
    stopBulletLines.length,
    5,
    'the Poly-Repo split checklist must only list repo-shape signals (5 bullets)',
  );
  for (const line of stopBulletLines) {
    assert.doesNotMatch(
      line,
      /runtime-policy|policy_route|compatible_combinations/i,
      'the Poly-Repo split checklist bullets must not include a runtime-policy signal',
    );
  }

  assert.match(
    content,
    /### Runtime-policy pre-flight \(mandatory/,
    'threadlight-deploy must declare a mandatory Runtime-policy pre-flight subsection',
  );

  const runtimePolicyHeadingMatch = content.match(/### Runtime-policy pre-flight \(mandatory[^\n]*\n/);
  const afterHeading = content.slice(runtimePolicyHeadingMatch.index + runtimePolicyHeadingMatch[0].length);
  const nextHeadingMatch = afterHeading.match(/\n#{2,3} /);
  const runtimePolicySection = nextHeadingMatch
    ? afterHeading.slice(0, nextHeadingMatch.index)
    : afterHeading;

  assert.match(
    runtimePolicySection,
    /install or enable\s+\*\*`threadlight-design`\*\*/,
    'Runtime-policy pre-flight remediation must instruct installing/enabling threadlight-design',
  );
  assert.match(
    runtimePolicySection,
    /return to `threadlight-design`|back to `threadlight-design`/,
    'Runtime-policy pre-flight remediation must send the operator back to threadlight-design',
  );
});

test('threadlight-deploy documents legacy-foundation migration, ambiguous-match hard stop, and hand-crafted mode without foundation.md', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  // Missing partial tuple (framework + runtime_shape present, protocol/policy_route absent).
  assert.match(
    content,
    /Legacy-foundation migration \(partial tuple\)/,
    'must explicitly cover the missing-partial-tuple legacy-foundation case',
  );
  assert.match(
    content,
    /missing[\s\S]{0,40}`protocol`[\s\S]{0,40}`policy_route`/,
    'must describe the framework+runtime_shape-present-but-protocol/policy_route-missing case',
  );
  assert.match(
    content,
    /migrated-from-legacy-foundation/,
    'must document the migration-note source value written back to foundation.md',
  );

  // Zero or multiple compatible-combination matches -> hard stop back to design.
  assert.match(
    content,
    /Zero or multiple matching compatible combinations/,
    'must explicitly cover the ambiguous/zero-match hard stop',
  );
  assert.match(
    content,
    /Zero or multiple matching compatible combinations[\s\S]{0,120}HARD STOP/,
    'the zero/multiple-match case must resolve to a HARD STOP',
  );

  // Foundation entirely absent -> hand-crafted deploy mode.
  assert.match(
    content,
    /Foundation entirely absent[\s\S]{0,40}hand-crafted deploy mode/,
    'must explicitly cover the foundation-absent hand-crafted-deploy case',
  );
  assert.match(
    content,
    /hand-crafted-deploy-inferred/,
    'must document the source value recorded for the hand-crafted minimal foundation record',
  );
});

test('design and deploy prose gate explicit-supported-choice on the absence of blocked_when signals', () => {
  const designContent = read('skills/threadlight-design/SKILL.md');
  const deployContent = read('skills/threadlight-deploy/SKILL.md');

  for (const [label, content] of [
    ['threadlight-design', designContent],
    ['threadlight-deploy', deployContent],
  ]) {
    assert.match(
      content,
      /blocked_when/,
      `${label} must reference blocked_when when describing explicit-supported-choice`,
    );
  }
});

test("example foundation.md's resolved selector tuple exists in compatible_combinations and matches its named concrete route", () => {
  const policy = loadPolicy();
  const foundationPath = 'examples/returns-triage-governed/specs/foundation.md';
  const content = read(foundationPath);
  const headingPattern = /## Framework & runtime shape\n/;

  const framework = firstValueAfterHeading(content, headingPattern, 'framework');
  const runtimeShape = firstValueAfterHeading(content, headingPattern, 'runtime_shape');
  const protocol = firstValueAfterHeading(content, headingPattern, 'protocol');
  const policyRoute = firstValueAfterHeading(content, headingPattern, 'policy_route');

  assertValidSelector(policy, 'frameworks', framework, foundationPath);
  assertValidSelector(policy, 'runtime_shapes', runtimeShape, foundationPath);
  assertValidSelector(policy, 'protocols', protocol, foundationPath);

  const tuple = { framework, runtime_shape: runtimeShape, protocol };
  const compatibleCombinationKeys = new Set(policy.compatible_combinations.map(tupleKey));
  assert.ok(
    compatibleCombinationKeys.has(tupleKey(tuple)),
    `${foundationPath} selector tuple (${tupleKey(tuple)}) must exist in compatible_combinations`,
  );

  const namedRoute = policy.routes.find((route) => route.id === policyRoute);
  assert.ok(namedRoute, `${foundationPath} policy_route \`${policyRoute}\` must name a real route`);
  assert.deepStrictEqual(
    selectorTuple(namedRoute),
    tuple,
    `${foundationPath} selector tuple must match the \`${policyRoute}\` route's own tuple`,
  );

  // This example is pinned to the canonical GHCP default, not the old MAF
  // house default.
  assert.strictEqual(framework, 'github-copilot-sdk');
  assert.strictEqual(runtimeShape, 'agent');
  assert.strictEqual(protocol, 'invocations');
  assert.strictEqual(policyRoute, 'default-agent');
});
