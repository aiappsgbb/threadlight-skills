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
const capabilitySignalKeys = [
  'requires_toolbox',
  'requires_custom_python_tools',
  'requires_file_generation',
  'latency_sensitive_data_queries',
];
// The five fields `runtime-policy.json` routes (and `blocked_when`) key on:
// `workflow_model` plus the four capability booleans above.
const routeSignals = ['workflow_model', ...capabilitySignalKeys];
const expectedBlockedWhen = ['workflow_model=workflow', ...capabilitySignalKeys];

const exampleDir = 'examples/returns-triage-governed';
const exampleFoundationPath = `${exampleDir}/specs/foundation.md`;
const exampleSpecPath = `${exampleDir}/specs/SPEC.md`;
const exampleAzureYamlPath = `${exampleDir}/azure.yaml`;
const examplePyprojectPath = `${exampleDir}/src/agent/pyproject.toml`;
const exampleContainerPath = `${exampleDir}/src/agent/container.py`;

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

// azure.yaml (azd schema) declares its protocol under a service's
// `protocols: [{ protocol: <value> }]` list — pull the first one out without
// a full YAML parser (the runtime-policy tests avoid a new dependency).
function firstAzureYamlProtocol(content) {
  const match = content.match(/protocols:\s*\n\s*-\s*protocol:\s*(\S+)/);
  assert.ok(match, 'expected to find `protocols:\\n  - protocol: <value>` in azure.yaml');
  return match[1];
}

// The `microsoft-agent-framework` selector maps to the `agent-framework*`
// PyPI package family (agent-framework-core, agent-framework-foundry, ...).
// Presence of any such dependency in pyproject.toml is the ground-truth
// signal that the example is actually wired to MAF, independent of what
// specs/foundation.md claims.
function pyprojectDeclaresAgentFramework(content) {
  return /["']agent-framework(?:-[\w.-]+)?["'\s=<>!]/.test(content);
}

// Extract the exact `capability_signals:` block (the top-level key line plus
// every immediately-following indented line) out of a foundation/SPEC-shaped
// document. Deliberately a line-based extraction, not a YAML parser — this
// repo avoids adding a YAML dependency just for test assertions.
function extractCapabilitySignalsBlock(content) {
  const lines = content.split('\n');
  const startIndex = lines.findIndex((line) => /^capability_signals:/.test(line));
  assert.ok(startIndex !== -1, 'expected a top-level `capability_signals:` line');
  const blockLines = [lines[startIndex]];
  for (let i = startIndex + 1; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^[ \t]+\S/.test(line)) {
      blockLines.push(line);
    } else {
      break;
    }
  }
  return blockLines.join('\n');
}

// Pull the `paths:` list out of a top-level GitHub Actions trigger block
// (`pull_request:` or `push:`) inside a workflow YAML file — a deliberately
// line-based / textual extraction (2-space-indented trigger key, 4-space
// `paths:` heading, 6-space `- "..."` entries), not a full YAML parser, so
// this repo avoids adding a YAML dependency just for a CI-config test.
function extractWorkflowPathsList(content, triggerKey) {
  const triggerHeadingMatch = content.match(new RegExp(`\\n {2}${escapeRegex(triggerKey)}:\\n`));
  assert.ok(triggerHeadingMatch, `expected a top-level \`${triggerKey}:\` trigger block in the workflow file`);
  const afterTrigger = content.slice(triggerHeadingMatch.index + triggerHeadingMatch[0].length);
  const nextTopLevelKeyMatch = afterTrigger.match(/\n {2}\S/);
  const triggerBlock = nextTopLevelKeyMatch ? afterTrigger.slice(0, nextTopLevelKeyMatch.index) : afterTrigger;

  const pathsHeadingMatch = triggerBlock.match(/ {4}paths:\n/);
  assert.ok(pathsHeadingMatch, `expected a \`paths:\` list under \`${triggerKey}:\``);
  const pathsBlock = triggerBlock.slice(pathsHeadingMatch.index + pathsHeadingMatch[0].length);

  const entries = pathsBlock
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('- '))
    .map((line) => line.slice(2).trim().replace(/^"|"$/g, ''));
  assert.ok(entries.length > 0, `expected at least one path entry under \`${triggerKey}: paths:\``);
  return entries;
}

// A workflow `paths:` entry either matches a file exactly, or (when it ends
// in `/**`) matches any file nested under that directory prefix.
function workflowPathsCover(pathEntries, targetPath) {
  return pathEntries.some((entry) => {
    if (entry === targetPath) return true;
    if (entry.endsWith('/**')) {
      const prefix = entry.slice(0, -'**'.length);
      return targetPath.startsWith(prefix);
    }
    return false;
  });
}

// Parse an extracted `capability_signals:` block into a plain object, so
// tests can deep-compare *values* (booleans, the `unresolved_signals` list,
// `source`) instead of relying on incidental comment text lining up. Strips
// trailing `#`-comments before parsing. Handles both the multi-line
// `- item` list form and the empty inline `[]` form for `unresolved_signals`.
function parseCapabilitySignalsBlock(blockText) {
  const rawLines = blockText
    .split('\n')
    .map((line) => line.replace(/#.*$/, '').replace(/\s+$/, ''));

  const result = {};
  let currentListKey = null;

  for (let i = 1; i < rawLines.length; i += 1) {
    const line = rawLines[i];
    if (!line.trim()) continue;

    const listItemMatch = line.match(/^\s*-\s*(.+)$/);
    if (listItemMatch && currentListKey) {
      result[currentListKey].push(listItemMatch[1].trim());
      continue;
    }

    const kvMatch = line.match(/^\s*([A-Za-z_]+):\s*(.*)$/);
    assert.ok(kvMatch, `unexpected line in capability_signals block: \`${line}\``);
    const [, key, rawValue] = kvMatch;
    const value = rawValue.trim();

    if (value === '') {
      result[key] = [];
      currentListKey = key;
    } else if (value === '[]') {
      result[key] = [];
      currentListKey = null;
    } else if (value === 'true' || value === 'false') {
      result[key] = value === 'true';
      currentListKey = null;
    } else {
      result[key] = value;
      currentListKey = null;
    }
  }

  return result;
}

test('runtime policy file declares the supported selectors, compatible combinations, and valid default route', () => {
  const policy = loadPolicy();

  assert.strictEqual(policy.schema, 'threadlight.runtime-policy/v1');
  assert.strictEqual(policy.version, 2);

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

test('runtime policy declares contract ownership, regional constraints, and route lifecycle', () => {
  const policy = loadPolicy();

  assert.strictEqual(policy.contract_version, '2.0.0');
  assert.match(policy.last_reviewed, /^\d{4}-\d{2}-\d{2}$/);
  assert.deepStrictEqual(policy.authority, {
    repository: 'aiappsgbb/threadlight-skills',
    path: 'skills/threadlight-design/references/runtime-policy.json',
    cross_repository_consumers: false,
  });
  assert.deepStrictEqual(policy.region_policy, {
    default: 'eastus2',
    eu_residency: ['swedencentral'],
    selection_rule: 'Use an EU region only when the complete required resource set is available there.',
  });

  for (const route of policy.routes) {
    assert.match(route.decision_date, /^\d{4}-\d{2}-\d{2}$/, `${route.id} must declare decision_date`);
    const lifecycleMechanisms = ['permanent', 'review_by', 'expiry_condition']
      .filter((key) => Object.hasOwn(route, key));
    assert.strictEqual(
      lifecycleMechanisms.length,
      1,
      `${route.id} must declare exactly one lifecycle mechanism`,
    );
    if (lifecycleMechanisms[0] === 'permanent') {
      assert.strictEqual(route.permanent, true, `${route.id} permanent must be true`);
    }
  }

  const defaultAgentRoute = policy.routes.find((route) => route.id === 'default-agent');
  assert.strictEqual(defaultAgentRoute.decision_date, '2026-08-05');
  assert.strictEqual(
    defaultAgentRoute.expiry_condition,
    'Responses works end to end for the generated hosted runtime and every documented channel.',
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

test('explicit-supported-choice requires every capability signal to be resolved before an operator override is honored', () => {
  const policy = loadPolicy();
  const explicitRoute = policy.routes.find((route) => route.id === 'explicit-supported-choice');

  assert.ok(explicitRoute, 'explicit-supported-choice route must exist');
  assert.strictEqual(
    explicitRoute.requires_resolved_signals,
    true,
    'explicit-supported-choice must declare `requires_resolved_signals: true` — a false-vs-unknown signal must never be ' +
      'silently treated as resolved',
  );
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

  // Hand-crafted step: SPEC § 11e is the sole signal source, and the newly
  // written foundation record becomes the selector authority afterward.
  const handCraftedHeadingMatch = content.match(/Foundation entirely absent[\s\S]{0,40}hand-crafted deploy mode/);
  assert.ok(handCraftedHeadingMatch, 'expected to locate the hand-crafted-deploy-mode branch');
  const handCraftedSection = content.slice(handCraftedHeadingMatch.index, handCraftedHeadingMatch.index + 1200);
  assert.match(
    handCraftedSection,
    /sole signal source/i,
    'the hand-crafted-deploy branch must state SPEC § 11e is the sole signal source (no foundation capability_signals exists yet)',
  );
  assert.match(
    handCraftedSection,
    /becomes[\s\S]{0,40}authority/i,
    'the hand-crafted-deploy branch must state the newly written foundation record becomes the selector authority',
  );
});

test('threadlight-deploy skips selector migration for Kratos-export mode after checking the policy dependency', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');
  const runtimePolicyHeadingMatch = content.match(/### Runtime-policy pre-flight \(mandatory[^\n]*\n/);
  assert.ok(runtimePolicyHeadingMatch, 'expected the Runtime-policy pre-flight section');
  const afterHeading = content.slice(runtimePolicyHeadingMatch.index + runtimePolicyHeadingMatch[0].length);
  const nextHeadingMatch = afterHeading.match(/\n#{2,3} /);
  const runtimePolicySection = nextHeadingMatch
    ? afterHeading.slice(0, nextHeadingMatch.index)
    : afterHeading;

  assert.match(
    runtimePolicySection,
    /Kratos-export mode[\s\S]{0,300}(?:skip|does not run)[\s\S]{0,120}(?:selector|migration|steps 2)/i,
    'Kratos-export mode must skip selector migration because it preserves the exported runtime',
  );
  assert.match(
    runtimePolicySection,
    /Kratos-export mode[\s\S]{0,400}Phase 2[\s\S]{0,80}skip/i,
    'the exemption must be justified by Kratos-export mode skipping runtime generation in Phase 2',
  );
  assert.match(
    runtimePolicySection,
    /Kratos-export mode[\s\S]{0,400}(?:dependency|policy file|step 1)[\s\S]{0,100}(?:still|required|applies)/i,
    'the policy dependency/readability check must still apply in Kratos-export mode',
  );
});

test('threadlight-auto defers greenfield selector validation until Design and Deploy and exempts Kratos artifacts', () => {
  const content = read('skills/threadlight-auto/SKILL.md');
  const stage0HeadingMatch = content.match(/## Stage 0 — Preflight/);
  const resumptionHeadingMatch = content.match(/## Resumption/);
  assert.ok(stage0HeadingMatch && resumptionHeadingMatch, 'expected Stage 0 and Resumption headings');
  const stage0Section = content.slice(stage0HeadingMatch.index, resumptionHeadingMatch.index);

  assert.match(
    stage0Section,
    /runtime-policy\.json[\s\S]{0,180}(?:always|unconditionally)[\s\S]{0,80}(?:readable|available)/i,
    'Stage 0 must always check that the policy dependency is readable',
  );
  assert.match(
    stage0Section,
    /greenfield[\s\S]{0,240}foundation\.md[\s\S]{0,160}(?:does not exist|created)[\s\S]{0,180}(?:defer|Design)[\s\S]{0,120}Deploy/i,
    'greenfield Stage 0 must defer selector validation until Design creates foundation.md and Deploy validates it',
  );
  assert.match(
    stage0Section,
    /Kratos-export mode[\s\S]{0,220}(?:skip|exempt)[\s\S]{0,120}(?:foundation|selector)/i,
    'Kratos-export Stage 0 must not require Threadlight foundation selectors for a preserved runtime',
  );
  assert.match(
    stage0Section,
    /when `?specs\/foundation\.md`? exists[\s\S]{0,160}(?:validate|resolve)/i,
    'Stage 0 selector validation must be conditional on foundation.md already existing',
  );
  assert.match(
    stage0Section,
    /explicit-supported-choice[\s\S]{0,180}(?:requires_resolved_signals|unresolved_signals[\s\S]{0,60}empty)/i,
    'existing explicit-supported-choice foundations must satisfy the policy resolved-signal gate in Stage 0',
  );
});

test('threadlight-auto defers incomplete legacy foundations to deploy migration', () => {
  const content = read('skills/threadlight-auto/SKILL.md');
  const stage0HeadingMatch = content.match(/## Stage 0 — Preflight/);
  const resumptionHeadingMatch = content.match(/## Resumption/);
  assert.ok(stage0HeadingMatch && resumptionHeadingMatch, 'expected Stage 0 and Resumption headings');
  const stage0Section = content.slice(stage0HeadingMatch.index, resumptionHeadingMatch.index);

  assert.match(
    stage0Section,
    /legacy[\s\S]{0,180}foundation\.md[\s\S]{0,220}(?:missing|without)[\s\S]{0,100}(?:protocol|policy_route|capability_signals)[\s\S]{0,260}(?:defer|continue)[\s\S]{0,160}Deploy/i,
    'Stage 0 must let Deploy migrate a pre-contract foundation instead of rejecting its incomplete tuple',
  );
  assert.match(
    stage0Section,
    /complete[\s\S]{0,100}foundation\.md[\s\S]{0,180}validate/i,
    'Stage 0 must keep validating already-complete foundations',
  );
});

test('threadlight-auto applies deploy route-integrity rules to complete foundations', () => {
  const content = read('skills/threadlight-auto/SKILL.md');
  const stage0HeadingMatch = content.match(/## Stage 0 — Preflight/);
  const resumptionHeadingMatch = content.match(/## Resumption/);
  assert.ok(stage0HeadingMatch && resumptionHeadingMatch, 'expected Stage 0 and Resumption headings');
  const stage0Section = content.slice(stage0HeadingMatch.index, resumptionHeadingMatch.index);

  assert.match(
    stage0Section,
    /complete foundation[\s\S]{0,420}(?:same|identical)[\s\S]{0,120}Deploy[\s\S]{0,120}(?:step 5|complete-foundation)/i,
    'Auto must use the same complete-foundation validation contract as Deploy',
  );
  assert.match(
    stage0Section,
    /concrete[\s\S]{0,80}policy_route[\s\S]{0,220}(?:exact|first matching route)/i,
    'Auto must reject a complete foundation whose concrete route does not own the resolved tuple/signals',
  );
  assert.match(
    stage0Section,
    /explicit-supported-choice[\s\S]{0,260}(?:source|provenance)[\s\S]{0,100}provided/i,
    'Auto must require operator-provided provenance for an explicit choice',
  );
});

test('threadlight-auto resumes when foundation exists before SPEC generation completes', () => {
  const content = read('skills/threadlight-auto/SKILL.md');
  const stage0HeadingMatch = content.match(/## Stage 0 — Preflight/);
  const resumptionHeadingMatch = content.match(/## Resumption/);
  assert.ok(stage0HeadingMatch && resumptionHeadingMatch, 'expected Stage 0 and Resumption headings');
  const stage0Section = content.slice(stage0HeadingMatch.index, resumptionHeadingMatch.index);

  assert.match(
    stage0Section,
    /foundation[\s\S]{0,220}SPEC[^\n]{0,20}(?:absent|does not exist|not yet)[\s\S]{0,260}(?:do not|must not)[\s\S]{0,80}(?:hard-stop|reject)/i,
    'an interrupted Design run with Foundation but no SPEC must remain resumable',
  );
  assert.match(
    stage0Section,
    /SPEC[^\n]{0,20}(?:absent|does not exist|not yet)[\s\S]{0,260}defer[\s\S]{0,120}(?:mirror|cross-check)[\s\S]{0,180}(?:Design|Deploy)/i,
    'only the SPEC mirror cross-check should be deferred until SPEC exists',
  );
});

test('threadlight-auto binds preflight freshness to the current foundation hash', () => {
  const content = read('skills/threadlight-auto/SKILL.md');

  assert.match(
    content,
    /preflight-passed\.json[\s\S]{0,220}foundation_sha256/i,
    'the preflight marker contract must record the foundation hash (or null before Foundation exists)',
  );
  assert.match(
    content,
    /Preflight[\s\S]{0,180}< 24 h[\s\S]{0,220}foundation_sha256[\s\S]{0,180}(?:match|unchanged)/i,
    'a fresh preflight marker may skip only when its recorded foundation hash still matches',
  );
});

test('legacy partial migration does not promote the pre-contract defaulted MAF value to an operator choice', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');
  const branchHeadingMatch = content.match(/Legacy-foundation migration \(partial tuple\)/);
  assert.ok(branchHeadingMatch, 'expected the partial-tuple legacy migration branch');
  const branchSection = content.slice(branchHeadingMatch.index, branchHeadingMatch.index + 1800);
  const providedStart = branchSection.indexOf('`source`: `provided`');
  const defaultedStart = branchSection.indexOf('`source`: `defaulted`');
  assert.ok(providedStart >= 0 && defaultedStart > providedStart, 'expected distinct provided and defaulted migration branches');
  const providedSection = branchSection.slice(providedStart, defaultedStart);

  assert.match(
    branchSection,
    /source[\s\S]{0,120}`provided`[\s\S]{0,220}(?:preserve|verbatim|recorded pair)/i,
    'only a source: provided legacy runtime pair may be preserved verbatim',
  );
  assert.match(
    providedSection,
    /compatible_combinations[\s\S]{0,220}protocol[\s\S]{0,180}explicit-supported-choice/i,
    'a trusted operator-provided pair must recover its protocol from compatible_combinations and use explicit-supported-choice',
  );
  assert.match(
    providedSection,
    /requires_resolved_signals[\s\S]{0,160}blocked_when|blocked_when[\s\S]{0,160}requires_resolved_signals/i,
    'a migrated explicit operator choice must enforce both explicit-supported-choice gates',
  );
  assert.match(
    providedSection,
    /(?:defer|after)[\s\S]{0,180}(?:requires_resolved_signals|blocked_when)[\s\S]{0,220}(?:step 3|capability_signals)[\s\S]{0,180}step 5/i,
    'explicit-choice gates must run only after step 3 has recovered the legacy capability signals',
  );
  assert.doesNotMatch(
    providedSection,
    /concrete `routes\[\]` entry/i,
    'a trusted operator choice must not be relabeled as a concrete capability/default route',
  );
  assert.match(
    providedSection,
    /(?:preserve|keep)[\s\S]{0,180}(?:source|provenance)[\s\S]{0,80}provided/i,
    'migrating an explicit legacy choice must preserve its operator-provided framework provenance',
  );
  assert.match(
    branchSection,
    /separate[\s\S]{0,100}migration[\s\S]{0,120}migrated-from-legacy-foundation/i,
    'tuple migration provenance must be recorded separately instead of overwriting the framework decision source',
  );
  assert.match(
    branchSection,
    /microsoft-agent-framework[\s\S]{0,180}(?:defaulted-after-skip|defaulted)[\s\S]{0,260}(?:must not|do not)[\s\S]{0,100}(?:migrate|promote|preserve)/i,
    'the old defaulted MAF house default must not be promoted into an intentional MAF route',
  );
  assert.match(
    branchSection,
    /(?:defaulted-after-skip|defaulted)[\s\S]{0,420}(?:capability_signals|workflow_model)[\s\S]{0,260}(?:re-resolve|first matching policy route)/i,
    'defaulted legacy values must be re-resolved from current capability/workflow signals',
  );
  assert.match(
    branchSection,
    /(?:missing|unknown)[\s\S]{0,80}source[\s\S]{0,180}HARD STOP/i,
    'legacy records without trustworthy source provenance must hard-stop instead of switching runtimes',
  );
});

test('threadlight-deploy hard-stops the hand-crafted/no-foundation branch when SPEC § 11e has no capability_signals block', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  const handCraftedHeadingMatch = content.match(/Foundation entirely absent[\s\S]{0,40}hand-crafted deploy mode/);
  assert.ok(handCraftedHeadingMatch, 'expected to locate the hand-crafted-deploy-mode branch');
  const handCraftedSection = content.slice(handCraftedHeadingMatch.index, handCraftedHeadingMatch.index + 1800);

  assert.match(
    handCraftedSection,
    /SPEC[^\n]{0,10}§\s*11e[\s\S]{0,100}no[\s\S]{0,20}capability_signals[\s\S]{0,200}HARD STOP/i,
    'the hand-crafted/no-foundation branch must HARD STOP to threadlight-design when specs/SPEC.md § 11e itself ' +
      'has no capability_signals block — this skill has no other signal source once foundation.md is absent too',
  );
  assert.match(
    handCraftedSection,
    /HARD STOP[\s\S]{0,300}never[\s\S]{0,40}default[\s\S]{0,60}four booleans[\s\S]{0,60}false/i,
    'the hand-crafted/no-foundation branch must never default the four booleans to false on its own authority',
  );
});

test('threadlight-deploy documents a distinct legacy-foundation branch for a full tuple missing capability_signals', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  assert.match(
    content,
    /Legacy-foundation migration \(missing `?capability_signals`?\)/,
    'must explicitly cover the full-tuple-but-missing-capability_signals legacy case as its own branch, distinct from the partial-tuple case',
  );

  const branchHeadingMatch = content.match(/Legacy-foundation migration \(missing `?capability_signals`?\)/);
  const branchSection = content.slice(branchHeadingMatch.index, branchHeadingMatch.index + 1300);

  assert.match(
    branchSection,
    /SPEC[^\n]{0,10}§\s*11e/,
    'the missing-capability_signals branch must derive signals from SPEC § 11e',
  );
  assert.match(
    branchSection,
    /migrated-from-legacy-foundation/,
    'the missing-capability_signals branch must record migrated-from-legacy-foundation provenance',
  );
  assert.match(
    branchSection,
    /(?:copy|write)[\s\S]{0,120}verbatim[\s\S]{0,120}(?:including|preserve)[\s\S]{0,80}source/i,
    'the missing-capability_signals branch must preserve the SPEC signal block source when mirroring it into foundation',
  );
  assert.match(
    branchSection,
    /separate[\s\S]{0,100}migration[\s\S]{0,120}migrated-from-legacy-foundation/i,
    'capability-signal migration provenance must be separate from the mirrored block source',
  );
  assert.match(
    branchSection,
    /(?:incomplete|missing)[\s\S]{0,120}(?:source|required fields)[\s\S]{0,180}HARD STOP/i,
    'an incomplete SPEC capability_signals block must hard-stop instead of creating a mismatched mirror',
  );
  assert.match(
    branchSection,
    /neither[\s\S]{0,160}nor[\s\S]{0,200}HARD STOP/i,
    'must hard-stop to threadlight-design when neither foundation nor SPEC carries a capability_signals block',
  );
});

test('threadlight-deploy cross-checks capability_signals against SPEC only when the foundation block actually exists', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  assert.match(
    content,
    /only when[\s\S]{0,80}capability_signals[\s\S]{0,120}cross-check/i,
    'the complete-foundation validate step must guard the SPEC cross-check on capability_signals actually existing',
  );
});

test('threadlight-deploy validates the complete capability_signals schema before route evaluation', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');
  const completeHeadingMatch = content.match(/Complete foundation — validate/);
  assert.ok(completeHeadingMatch, 'expected the complete-foundation validation branch');
  const completeSection = content.slice(completeHeadingMatch.index, completeHeadingMatch.index + 2600);

  assert.match(
    completeSection,
    /before[\s\S]{0,120}(?:cross-check|route)[\s\S]{0,220}four[\s\S]{0,80}boolean/i,
    'all four capability fields must be validated as booleans before cross-checking or selecting a route',
  );
  assert.match(
    completeSection,
    /unresolved_signals[\s\S]{0,160}(?:array|list)[\s\S]{0,180}(?:known|allowed)[\s\S]{0,100}(?:signal|name)/i,
    'unresolved_signals must be a list containing only known capability-signal names',
  );
  assert.match(
    completeSection,
    /source[\s\S]{0,160}(?:present|allowed|taxonomy)/i,
    'the capability_signals source must be present and valid',
  );
  assert.match(
    completeSection,
    /(?:incomplete|malformed)[\s\S]{0,220}HARD STOP/i,
    'an incomplete existing capability_signals block must hard-stop instead of treating omissions as false',
  );
});

test('threadlight-deploy requires concrete policy routes to own the exact foundation selector tuple', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');
  const completeHeadingMatch = content.match(/Complete foundation — validate/);
  assert.ok(completeHeadingMatch, 'expected the complete-foundation validation branch');
  const completeSection = content.slice(completeHeadingMatch.index, completeHeadingMatch.index + 3200);

  assert.match(
    completeSection,
    /concrete[\s\S]{0,80}policy_route[\s\S]{0,220}exact(?:ly)?[\s\S]{0,100}(?:framework|selectors)[\s\S]{0,180}(?:runtime_shape|protocol)/i,
    'a concrete route id must be validated against the selectors declared by that exact route',
  );
  assert.match(
    completeSection,
    /first matching[\s\S]{0,100}(?:policy )?route[\s\S]{0,220}(?:capability_signals|workflow_model)[\s\S]{0,220}(?:equal|match)[\s\S]{0,100}policy_route/i,
    'a concrete route must also be the first route activated by the current workflow/capability signals',
  );
  assert.match(
    completeSection,
    /explicit-supported-choice[\s\S]{0,260}(?:source|provenance)[\s\S]{0,100}provided/i,
    'explicit-supported-choice must be backed by operator-provided provenance',
  );
  assert.match(
    completeSection,
    /explicit-supported-choice[\s\S]{0,420}blocked_when[\s\S]{0,220}requires_resolved_signals|explicit-supported-choice[\s\S]{0,420}requires_resolved_signals[\s\S]{0,220}blocked_when/i,
    'complete-foundation explicit choices must enforce both policy gates',
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

test('design Step 0 and deploy preflight refuse explicit-supported-choice while unresolved_signals is non-empty', () => {
  const designContent = read('skills/threadlight-design/SKILL.md');
  const deployContent = read('skills/threadlight-deploy/SKILL.md');

  for (const [label, content] of [
    ['threadlight-design', designContent],
    ['threadlight-deploy', deployContent],
  ]) {
    assert.match(
      content,
      /unresolved_signals/,
      `${label} must reference \`unresolved_signals\` when gating explicit-supported-choice`,
    );
    assert.match(
      content,
      /refuse[\s\S]{0,200}unresolved_signals[\s\S]{0,80}non-empty/i,
      `${label} must explicitly refuse to honor explicit-supported-choice while unresolved_signals is non-empty`,
    );
  }
});

test("example foundation.md's resolved selector tuple exists in compatible_combinations and matches its named concrete route", () => {
  const policy = loadPolicy();
  const content = read(exampleFoundationPath);
  const headingPattern = /## Framework & runtime shape\n/;

  const framework = firstValueAfterHeading(content, headingPattern, 'framework');
  const runtimeShape = firstValueAfterHeading(content, headingPattern, 'runtime_shape');
  const protocol = firstValueAfterHeading(content, headingPattern, 'protocol');
  const policyRoute = firstValueAfterHeading(content, headingPattern, 'policy_route');

  assertValidSelector(policy, 'frameworks', framework, exampleFoundationPath);
  assertValidSelector(policy, 'runtime_shapes', runtimeShape, exampleFoundationPath);
  assertValidSelector(policy, 'protocols', protocol, exampleFoundationPath);

  const tuple = { framework, runtime_shape: runtimeShape, protocol };
  const compatibleCombinationKeys = new Set(policy.compatible_combinations.map(tupleKey));
  assert.ok(
    compatibleCombinationKeys.has(tupleKey(tuple)),
    `${exampleFoundationPath} selector tuple (${tupleKey(tuple)}) must exist in compatible_combinations`,
  );

  const namedRoute = policy.routes.find((route) => route.id === policyRoute);
  assert.ok(namedRoute, `${exampleFoundationPath} policy_route \`${policyRoute}\` must name a real route`);
  if (namedRoute.selection === 'operator') {
    // explicit-supported-choice has no concrete tuple of its own — it
    // validates against compatible_combinations instead (already checked
    // above); assert that ref explicitly here too.
    assert.strictEqual(
      namedRoute.allowed_combinations_ref,
      'compatible_combinations',
      `${exampleFoundationPath}'s \`${policyRoute}\` route must validate against compatible_combinations`,
    );
  } else {
    assert.deepStrictEqual(
      selectorTuple(namedRoute),
      tuple,
      `${exampleFoundationPath} selector tuple must match the \`${policyRoute}\` route's own tuple`,
    );
  }

  // This example actually runs Microsoft Agent Framework (SkillsProvider +
  // FoundryChatClient + ResponsesHostServer over the Responses protocol) per
  // src/agent/container.py + pyproject.toml + azure.yaml. No capability
  // signal mandates MAF for this pilot (no Toolbox/custom-tool/file-gen/
  // latency-sensitive trigger) — it is an explicit, compatible operator
  // choice, not a capability-mandated route.
  assert.strictEqual(framework, 'microsoft-agent-framework');
  assert.strictEqual(runtimeShape, 'agent');
  assert.strictEqual(protocol, 'responses');
  assert.strictEqual(policyRoute, 'explicit-supported-choice');
});

test('example foundation.md does not emit a customer-project-broken references/runtime-policy.json filesystem path', () => {
  const content = read(exampleFoundationPath);
  assert.ok(
    !content.includes('references/runtime-policy.json'),
    `${exampleFoundationPath} must not paste a references/runtime-policy.json path — the recorded schema id already ` +
      'identifies the contract version, and that path does not exist relative to a customer project',
  );
});

test("example foundation.md and SPEC.md § 11e capability_signals blocks are identical, fully resolved, and provided", () => {
  const foundationContent = read(exampleFoundationPath);
  const specContent = read(exampleSpecPath);

  const foundationBlock = extractCapabilitySignalsBlock(foundationContent);
  const specBlock = extractCapabilitySignalsBlock(specContent);

  const foundationParsed = parseCapabilitySignalsBlock(foundationBlock);
  const specParsed = parseCapabilitySignalsBlock(specBlock);

  assert.deepStrictEqual(
    foundationParsed,
    specParsed,
    `${exampleFoundationPath} and ${exampleSpecPath} § 11e capability_signals blocks must carry identical resolved values`,
  );

  for (const key of capabilitySignalKeys) {
    assert.strictEqual(
      foundationParsed[key],
      false,
      `\`${key}\` must be resolved \`false\` for this pilot (confirmed absent, not merely unknown)`,
    );
  }
  assert.deepStrictEqual(
    foundationParsed.unresolved_signals,
    [],
    'every capability signal is resolved for this pilot — unresolved_signals must be empty',
  );
  assert.strictEqual(foundationParsed.source, 'provided');
});

test('example azure.yaml declared protocol matches the protocol resolved in specs/foundation.md', () => {
  const foundationContent = read(exampleFoundationPath);
  const headingPattern = /## Framework & runtime shape\n/;
  const foundationProtocol = firstValueAfterHeading(foundationContent, headingPattern, 'protocol');

  const azureYamlContent = read(exampleAzureYamlPath);
  const azureYamlProtocol = firstAzureYamlProtocol(azureYamlContent);

  assert.strictEqual(
    azureYamlProtocol,
    foundationProtocol,
    `${exampleAzureYamlPath} protocol (\`${azureYamlProtocol}\`) must match ${exampleFoundationPath}'s resolved protocol (\`${foundationProtocol}\`)`,
  );
});

test("example pyproject.toml's agent-framework packages map to microsoft-agent-framework and match specs/foundation.md's framework", () => {
  const foundationContent = read(exampleFoundationPath);
  const headingPattern = /## Framework & runtime shape\n/;
  const foundationFramework = firstValueAfterHeading(foundationContent, headingPattern, 'framework');

  const pyprojectContent = read(examplePyprojectPath);
  const hasAgentFrameworkPackages = pyprojectDeclaresAgentFramework(pyprojectContent);

  assert.ok(
    hasAgentFrameworkPackages,
    `${examplePyprojectPath} must declare at least one agent-framework* dependency (the example runs MAF)`,
  );
  assert.strictEqual(
    foundationFramework,
    'microsoft-agent-framework',
    `${exampleFoundationPath}'s \`framework\` must be microsoft-agent-framework to match the agent-framework* packages declared in ${examplePyprojectPath}`,
  );
});

test('example container.py names the MAF runtime surfaces (SkillsProvider, ResponsesHostServer) claimed by specs/foundation.md', () => {
  const content = read(exampleContainerPath);

  // Kept intentionally loose (substring checks, no line/format coupling) so
  // this stays robust to refactors of container.py that preserve the same
  // runtime surfaces.
  assert.ok(
    content.includes('SkillsProvider'),
    `${exampleContainerPath} must reference SkillsProvider (progressive skill loading) to back the MAF framework claim`,
  );
  assert.ok(
    content.includes('ResponsesHostServer'),
    `${exampleContainerPath} must reference ResponsesHostServer to back the \`protocol: responses\` claim`,
  );
});

test('capability_signals is a machine-readable contract block in both foundation and speckit templates', () => {
  const templates = {
    'skills/threadlight-design/references/foundation-template.md': read(
      'skills/threadlight-design/references/foundation-template.md',
    ),
    'skills/threadlight-design/references/speckit-template.md': read(
      'skills/threadlight-design/references/speckit-template.md',
    ),
  };

  for (const [label, content] of Object.entries(templates)) {
    assert.match(
      content,
      /capability_signals:/,
      `${label} must declare a machine-readable \`capability_signals:\` block`,
    );
    for (const key of capabilitySignalKeys) {
      assert.match(
        content,
        new RegExp(`${key}:\\s*(?:true|false)`),
        `${label}'s capability_signals block must set \`${key}\` to a boolean`,
      );
    }
    assert.match(
      content,
      /false`?\s*only when[\s\S]{0,80}absent/i,
      `${label} must document that a capability signal is \`false\` only when confirmed absent, not merely unknown`,
    );
    assert.match(
      content,
      /open.question/i,
      `${label} must document the open-question fallback when discovery cannot determine a signal`,
    );
    assert.match(
      content,
      /explicit-supported-choice[\s\S]{0,80}(?:GHCP )?override/i,
      `${label} must state that an unresolved signal blocks honoring an explicit GHCP override`,
    );
  }
});

test('capability_signals sample block is byte/structure identical between foundation-template and speckit-template § 11e', () => {
  const foundationContent = read('skills/threadlight-design/references/foundation-template.md');
  const speckitContent = read('skills/threadlight-design/references/speckit-template.md');

  const foundationBlock = extractCapabilitySignalsBlock(foundationContent);
  const speckitBlock = extractCapabilitySignalsBlock(speckitContent);

  assert.strictEqual(
    foundationBlock,
    speckitBlock,
    'capability_signals sample block text must be byte-for-byte identical between foundation-template.md § 1 and ' +
      'speckit-template.md § 11e',
  );

  const foundationParsed = parseCapabilitySignalsBlock(foundationBlock);
  const speckitParsed = parseCapabilitySignalsBlock(speckitBlock);
  assert.deepStrictEqual(
    foundationParsed,
    speckitParsed,
    'parsed capability_signals key/list values must match between the two templates',
  );

  // The template sample represents the unresolved placeholder state — every
  // boolean is a placeholder, not a decision, while its name is still listed
  // in unresolved_signals.
  for (const key of capabilitySignalKeys) {
    assert.strictEqual(
      foundationParsed[key],
      false,
      `template sample \`${key}\` is a placeholder value, but the field must still be present as a boolean`,
    );
  }
  assert.deepStrictEqual(
    foundationParsed.unresolved_signals,
    capabilitySignalKeys,
    'the template sample must list all four signal names as unresolved by default',
  );
  assert.strictEqual(
    foundationParsed.source,
    'open-question',
    'the template sample source must be open-question — a placeholder, not a resolved decision',
  );
});

test('both templates document the unresolved_signals derivation rule (remove resolved names; empty list means fully resolved)', () => {
  const templates = {
    'skills/threadlight-design/references/foundation-template.md': read(
      'skills/threadlight-design/references/foundation-template.md',
    ),
    'skills/threadlight-design/references/speckit-template.md': read(
      'skills/threadlight-design/references/speckit-template.md',
    ),
  };

  for (const [label, content] of Object.entries(templates)) {
    assert.match(
      content,
      /unresolved_signals/,
      `${label} must reference the \`unresolved_signals\` per-signal unknown marker`,
    );
    assert.match(
      content,
      /remove[\s\S]{0,120}unresolved_signals/i,
      `${label} must document removing a resolved signal's name from unresolved_signals`,
    );
    assert.match(
      content,
      /empty[\s\S]{0,80}unresolved_signals[\s\S]{0,80}(?:all four|resolved)|unresolved_signals[\s\S]{0,80}empty[\s\S]{0,80}(?:all four|resolved)/i,
      `${label} must document that an empty unresolved_signals list means all four signals are resolved`,
    );
  }
});

test('foundation-template decision-summary row 5 updates the source taxonomy to inferred | defaulted-after-skip | open-question', () => {
  const content = read('skills/threadlight-design/references/foundation-template.md');
  const rowMatch = content.match(/\|\s*5\s*\|\s*Capability signals[^\n]*\|/);
  assert.ok(rowMatch, 'expected to find decision-summary row 5 (Capability signals)');
  assert.match(
    rowMatch[0],
    /inferred \\?\|\s*defaulted-after-skip \\?\|\s*open-question/,
    'row 5 source taxonomy must read inferred | defaulted-after-skip | open-question, not the bare "defaulted"',
  );
});

test('foundation-template source taxonomy and capability_signals source comment document the two deploy-preflight-written source values', () => {
  const content = read('skills/threadlight-design/references/foundation-template.md');
  const deployContent = read('skills/threadlight-deploy/SKILL.md');
  const deployWrittenSourceValues = ['migrated-from-legacy-foundation', 'hand-crafted-deploy-inferred'];

  // Both values must match the exact `source:` strings threadlight-deploy
  // actually writes back to specs/foundation.md — the taxonomy documents
  // what the preflight does, it does not invent its own vocabulary.
  for (const sourceValue of deployWrittenSourceValues) {
    assert.match(
      deployContent,
      new RegExp(`source:\\s*${escapeRegex(sourceValue)}`),
      `threadlight-deploy must write \`source: ${sourceValue}\` somewhere in its runtime-policy pre-flight`,
    );
  }

  const taxonomyHeadingMatch = content.match(/`source` taxonomy \(same as SPEC § 13\):/);
  assert.ok(taxonomyHeadingMatch, 'expected the `source` taxonomy paragraph below the decision-summary table');
  const taxonomySection = content.slice(taxonomyHeadingMatch.index, taxonomyHeadingMatch.index + 700);
  for (const sourceValue of deployWrittenSourceValues) {
    assert.match(
      taxonomySection,
      new RegExp('`' + escapeRegex(sourceValue) + '`'),
      `the source taxonomy paragraph must document \`${sourceValue}\` with a concise meaning`,
    );
  }

  const capabilitySignalsBlock = extractCapabilitySignalsBlock(content);
  const sourceCommentLine = capabilitySignalsBlock.split('\n').find((line) => /^\s*source:/.test(line));
  assert.ok(sourceCommentLine, 'expected a `source:` line inside the capability_signals sample block');
  for (const sourceValue of deployWrittenSourceValues) {
    assert.ok(
      sourceCommentLine.includes(sourceValue),
      `the capability_signals source comment must list \`${sourceValue}\` as an allowed value`,
    );
  }
});

test('foundation-template documents the Fast-PoC capability_signals escalation guard', () => {
  const content = read('skills/threadlight-design/references/foundation-template.md');
  assert.match(
    content,
    /Fast-PoC[\s\S]{0,600}complexity triage/i,
    'Fast-PoC prose must reference a basic-scenario complexity triage before defaulting capability_signals',
  );
  assert.match(
    content,
    /complexity triage[\s\S]{0,300}escalate[\s\S]{0,80}Full/i,
    'Fast-PoC must escalate to Full mode when the complexity triage cannot confirm a signal is unneeded, instead of silently defaulting it',
  );
});

test('all five route-selection signals (workflow_model + capability booleans) are documented across design and deploy instructions', () => {
  const targets = {
    'skills/threadlight-design/references/speckit-template.md': read(
      'skills/threadlight-design/references/speckit-template.md',
    ),
    'skills/threadlight-design/references/foundation-template.md': read(
      'skills/threadlight-design/references/foundation-template.md',
    ),
    'skills/threadlight-design/SKILL.md': read('skills/threadlight-design/SKILL.md'),
    'skills/threadlight-deploy/SKILL.md': read('skills/threadlight-deploy/SKILL.md'),
  };

  for (const [label, content] of Object.entries(targets)) {
    for (const signal of routeSignals) {
      assert.ok(content.includes(signal), `${label} must reference the \`${signal}\` route-selection signal`);
    }
  }
});

test('threadlight-design Step 3 writes capability_signals into SPEC § 11e consistent with foundation', () => {
  const content = read('skills/threadlight-design/SKILL.md');

  assert.match(
    content,
    /11e[\s\S]{0,600}capability_signals/,
    'Step 3 generation instructions for § 11e must mention writing the capability_signals block',
  );
  assert.match(
    content,
    /capability_signals[\s\S]{0,200}consisten/i,
    'Step 3 instructions must require capability_signals to remain consistent with specs/foundation.md',
  );
});

test('threadlight-deploy preflight reads capability signals from foundation and cross-checks SPEC before route validation and hand-crafted inference', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  assert.match(
    content,
    /capability_signals/,
    'threadlight-deploy must reference the capability_signals contract field',
  );
  assert.match(
    content,
    /cross-check[\s\S]{0,150}(?:§\s*11e|workflow_model)/i,
    'threadlight-deploy preflight must cross-check capability signals against SPEC § 11e / workflow_model',
  );
});

test('blocked_when consumer prose avoids the misleading "higher-priority route" phrasing introduced by this feature', () => {
  const policy = loadPolicy();
  const explicitRoute = policy.routes.find((route) => route.id === 'explicit-supported-choice');

  assert.doesNotMatch(
    explicitRoute.rationale,
    /higher-priority/i,
    'explicit-supported-choice rationale must not describe a blocked_when route as "higher-priority"',
  );
  assert.match(
    explicitRoute.rationale,
    /capability route that owns/i,
    'explicit-supported-choice rationale must describe the owning capability route instead',
  );

  // Only the blocked_when-conflict sentence introduced by this feature is
  // checked here — routine route-priority language predating it (e.g. the
  // Fast-PoC "unless a higher-priority route matches" sentence, describing
  // normal priority-1..4 route resolution) is unrelated prose and stays as-is.
  assert.doesNotMatch(
    read('skills/threadlight-design/SKILL.md'),
    /higher-priority MAF route/,
    'threadlight-design must not describe the blocked_when-owning route as the "higher-priority MAF route"',
  );
  assert.doesNotMatch(
    read('skills/threadlight-deploy/SKILL.md'),
    /higher-priority required MAF route/,
    'threadlight-deploy must not describe the blocked_when-owning route as the "higher-priority required MAF route"',
  );
});

test('threadlight-deploy describes ghcp-hosted-agents as the canonical runtime implementation companion, not an "Alternative runtime"', () => {
  const content = read('skills/threadlight-deploy/SKILL.md');

  assert.doesNotMatch(
    content,
    /Alternative runtime/,
    'threadlight-deploy must not label ghcp-hosted-agents as an "Alternative runtime" — GHCP Invocations is the canonical default route',
  );

  const ghcpSeeAlsoRow = content
    .split('\n')
    .find((line) => line.includes('ghcp-hosted-agents') && line.trim().startsWith('|'));
  assert.ok(ghcpSeeAlsoRow, 'expected a See Also table row referencing ghcp-hosted-agents');
  assert.match(
    ghcpSeeAlsoRow,
    /Canonical runtime/i,
    'the ghcp-hosted-agents See Also row must describe it relative to the canonical runtime default',
  );
});

test('docs-blueprint workflow paths (pull_request and push) cover every file this runtime-policy suite reads', () => {
  const workflowContent = read('.github/workflows/docs-blueprint.yml');

  // Every distinct file this test suite loads via read()/loadPolicy() —
  // the CI trigger must re-run whenever any of these changes, or a drifted
  // contract doc/example could merge without the guard ever executing.
  const testInputPaths = [
    ...new Set([
      ...consumerPaths,
      'skills/threadlight-design/references/speckit-template.md',
      'skills/threadlight-design/references/runtime-policy.json',
      '.github/workflows/docs-blueprint.yml',
      exampleFoundationPath,
      exampleSpecPath,
      exampleAzureYamlPath,
      examplePyprojectPath,
      exampleContainerPath,
    ]),
  ];

  for (const triggerKey of ['pull_request', 'push']) {
    const pathEntries = extractWorkflowPathsList(workflowContent, triggerKey);
    for (const testInputPath of testInputPaths) {
      assert.ok(
        workflowPathsCover(pathEntries, testInputPath),
        `${triggerKey}.paths must cover \`${testInputPath}\` (directly or via a \`**\` glob) so CI re-runs when it changes`,
      );
    }
  }
});

test('docs-blueprint workflow keeps pull_request.paths and push.paths symmetric', () => {
  const workflowContent = read('.github/workflows/docs-blueprint.yml');

  const prPaths = extractWorkflowPathsList(workflowContent, 'pull_request');
  const pushPaths = extractWorkflowPathsList(workflowContent, 'push');

  assert.deepStrictEqual(
    prPaths,
    pushPaths,
    'pull_request.paths and push.paths must list the exact same entries in the exact same order',
  );
});
