# Runtime Policy and Playbook Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Threadlight one enforceable runtime-selection policy and add generated, machine-readable playbook metadata to every existing process-library entry.

**Architecture:** A versioned JSON contract becomes the runtime-policy source of truth; existing skill documentation records or validates choices against it, and a Node guard prevents prose drift. The existing Python process-library producer decorates sanitized entries with a `threadlight.playbook/v1` block; Blueprint consumes that block while retaining its current signal-based derivation for legacy entries.

**Tech Stack:** JSON contracts, Markdown skill instructions, Python 3 standard library, vanilla JavaScript, Node 20 `node:test`.

---

## File map

- Create `skills/threadlight-design/references/runtime-policy.json` — canonical framework/runtime/protocol selectors and ordered routes.
- Create `tests/blueprint/runtime-policy.test.js` — contract and documentation-drift guard.
- Modify `skills/threadlight-design/references/foundation-template.md` — record canonical default selectors in generated foundations.
- Modify `skills/threadlight-design/SKILL.md` — use the runtime policy during foundation selection.
- Modify `skills/threadlight-deploy/SKILL.md` — validate the selected foundation route before generation.
- Modify `skills/threadlight-auto/SKILL.md` — declare that auto inherits the policy and hard-stops on mismatch.
- Modify `THREADLIGHT.md` — document the authority order and intentional protocol divergence from upstream.
- Modify `scripts/build_process_library.py` — derive `threadlight.playbook/v1` metadata after sanitization.
- Create `tests/blueprint/process-library-generator.test.js` — producer behavior and invalid-input tests.
- Modify `docs/assets/process-library.json` — regenerated entries containing playbook metadata.
- Modify `docs/assets/blueprint-logic.js` — prefer generated `build_skills`, with legacy fallback.
- Modify `tests/blueprint/process-library.test.js` — committed metadata contract checks.
- Modify `tests/blueprint/blueprint-logic.test.js` — metadata preference, fallback, and parity tests.
- Modify `.github/workflows/docs-blueprint.yml` — run guards when runtime-policy consumers change.

### Task 1: Add the canonical runtime policy

**Files:**
- Create: `tests/blueprint/runtime-policy.test.js`
- Create: `skills/threadlight-design/references/runtime-policy.json`
- Modify: `skills/threadlight-design/references/foundation-template.md`
- Modify: `skills/threadlight-design/SKILL.md`
- Modify: `skills/threadlight-deploy/SKILL.md`
- Modify: `skills/threadlight-auto/SKILL.md`
- Modify: `THREADLIGHT.md`

- [ ] **Step 1: Write the failing runtime-policy guard**

Create `tests/blueprint/runtime-policy.test.js`:

```javascript
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '../..');
const POLICY_PATH = path.join(
  ROOT, 'skills/threadlight-design/references/runtime-policy.json');

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8');
}

test('runtime policy has valid selectors and ordered routes', () => {
  const policy = JSON.parse(fs.readFileSync(POLICY_PATH, 'utf8'));
  assert.strictEqual(policy.schema, 'threadlight.runtime-policy/v1');
  assert.strictEqual(policy.version, 1);

  const selectors = policy.selectors;
  assert.ok(selectors.frameworks.includes(policy.default.framework));
  assert.ok(selectors.runtime_shapes.includes(policy.default.runtime_shape));
  assert.ok(selectors.protocols.includes(policy.default.protocol));

  const priorities = policy.routes.map(route => route.priority);
  assert.deepStrictEqual(priorities, [...priorities].sort((a, b) => a - b));
  assert.strictEqual(new Set(priorities).size, priorities.length);

  for (const route of policy.routes) {
    assert.ok(route.id && Array.isArray(route.when) && route.when.length > 0);
    assert.ok(route.rationale);
    if (route.selection === 'operator') continue;
    assert.ok(selectors.frameworks.includes(route.framework), route.id);
    assert.ok(selectors.runtime_shapes.includes(route.runtime_shape), route.id);
    assert.ok(selectors.protocols.includes(route.protocol), route.id);
  }
});

test('all runtime-policy consumers reference the canonical contract', () => {
  const consumers = [
    'skills/threadlight-design/references/foundation-template.md',
    'skills/threadlight-design/SKILL.md',
    'skills/threadlight-deploy/SKILL.md',
    'skills/threadlight-auto/SKILL.md',
    'THREADLIGHT.md',
  ];
  for (const rel of consumers) {
    assert.match(read(rel), /runtime-policy\.json/, `${rel} must reference runtime-policy.json`);
  }
});

test('design and deploy state the policy default', () => {
  const policy = JSON.parse(fs.readFileSync(POLICY_PATH, 'utf8'));
  const foundation = read('skills/threadlight-design/references/foundation-template.md');
  const deploy = read('skills/threadlight-deploy/SKILL.md');
  for (const value of Object.values(policy.default)) {
    assert.ok(foundation.includes(value), `foundation missing ${value}`);
    assert.ok(deploy.includes(value), `deploy missing ${value}`);
  }
});
```

- [ ] **Step 2: Run the guard and verify it fails**

Run:

```bash
node --test tests/blueprint/runtime-policy.test.js
```

Expected: FAIL with `ENOENT` for
`skills/threadlight-design/references/runtime-policy.json`.

- [ ] **Step 3: Add the runtime-policy contract**

Create `skills/threadlight-design/references/runtime-policy.json`:

```json
{
  "schema": "threadlight.runtime-policy/v1",
  "version": 1,
  "selectors": {
    "frameworks": [
      "github-copilot-sdk",
      "microsoft-agent-framework"
    ],
    "runtime_shapes": [
      "agent",
      "workflow"
    ],
    "protocols": [
      "invocations",
      "responses"
    ]
  },
  "default": {
    "framework": "github-copilot-sdk",
    "runtime_shape": "agent",
    "protocol": "invocations"
  },
  "routes": [
    {
      "id": "explicit-supported-choice",
      "priority": 1,
      "when": [
        "operator_explicitly_selects_supported_framework_shape_and_protocol"
      ],
      "selection": "operator",
      "rationale": "An explicit compatible operator decision overrides Threadlight defaults."
    },
    {
      "id": "deterministic-workflow",
      "priority": 2,
      "when": [
        "workflow_model=workflow"
      ],
      "framework": "microsoft-agent-framework",
      "runtime_shape": "workflow",
      "protocol": "responses",
      "rationale": "Fixed multi-phase orchestration and persona gates require a MAF workflow."
    },
    {
      "id": "maf-agent-capabilities",
      "priority": 3,
      "when": [
        "requires_toolbox",
        "requires_custom_python_tools",
        "requires_file_generation",
        "latency_sensitive_data_queries"
      ],
      "match": "any",
      "framework": "microsoft-agent-framework",
      "runtime_shape": "agent",
      "protocol": "responses",
      "rationale": "The current Threadlight MAF runtime covers these capabilities and latency requirements."
    },
    {
      "id": "default-agent",
      "priority": 4,
      "when": [
        "no_higher_priority_route_matches"
      ],
      "framework": "github-copilot-sdk",
      "runtime_shape": "agent",
      "protocol": "invocations",
      "rationale": "The verified GHCP hosted-agent path supports long-running streamed agent loops."
    }
  ]
}
```

- [ ] **Step 4: Align the design foundation**

In `skills/threadlight-design/references/foundation-template.md`:

1. Add an authority note pointing to `runtime-policy.json`.
2. Change the decision-summary framework default to
   `github-copilot-sdk`.
3. Add `invocations` as the default protocol.
4. Replace the framework YAML with:

```yaml
framework: github-copilot-sdk
runtime_shape: agent
protocol: invocations
policy_route: default-agent
```

5. Document the two MAF routes from the JSON contract without creating a
   separate decision table.

In `skills/threadlight-design/SKILL.md`, replace the MAF house-default wording
with instructions to:

```markdown
Read `references/runtime-policy.json` before locking the framework, runtime
shape, or protocol. Apply the first matching route, record its `id` as
`policy_route` in `specs/foundation.md`, and surface explicit operator choices
as route `explicit-supported-choice`.
```

- [ ] **Step 5: Align deploy and auto**

In `skills/threadlight-deploy/SKILL.md`, replace the independent runtime-default
paragraph with:

```markdown
**Runtime policy:** read
`../threadlight-design/references/runtime-policy.json`. The default route is
`github-copilot-sdk` + `agent` + `invocations`. Before Phase 1, validate the
framework, runtime shape, protocol, and `policy_route` recorded in
`specs/foundation.md`; stop on an unknown selector or a combination that does
not match the selected route. MAF agent and workflow are exception routes
defined only in the policy file.
```

Add the policy check to the Phase 0 pre-flight checklist and retain the existing
runtime implementation sections as route-specific guidance.

In `skills/threadlight-auto/SKILL.md`, add:

```markdown
`threadlight-auto` owns no framework or protocol default. Design and deploy
both consume `threadlight-design/references/runtime-policy.json`; a missing,
unknown, or incompatible foundation selection is a HARD STOP.
```

In `THREADLIGHT.md`, document the authority order:

```text
runtime-policy.json -> specs/foundation.md -> SPEC selectors -> generated runtime
```

State that GHCP SDK plus Invocations remains intentional until Threadlight's
host and Teams path support Responses end to end.

- [ ] **Step 6: Run the guard and existing Blueprint suite**

Run:

```bash
node --test tests/blueprint/runtime-policy.test.js
node --test tests/blueprint/*.test.js
```

Expected: both commands PASS.

- [ ] **Step 7: Commit the runtime policy**

```bash
git add skills/threadlight-design/references/runtime-policy.json \
  skills/threadlight-design/references/foundation-template.md \
  skills/threadlight-design/SKILL.md skills/threadlight-deploy/SKILL.md \
  skills/threadlight-auto/SKILL.md THREADLIGHT.md \
  tests/blueprint/runtime-policy.test.js
git commit -m "fix: centralize threadlight runtime policy"
```

### Task 2: Generate playbook metadata

**Files:**
- Create: `tests/blueprint/process-library-generator.test.js`
- Modify: `scripts/build_process_library.py`
- Modify: `tests/blueprint/process-library.test.js`
- Modify: `docs/assets/process-library.json`

- [ ] **Step 1: Write failing producer and data-contract tests**

Create `tests/blueprint/process-library-generator.test.js` with tests that:

```javascript
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const ROOT = path.join(__dirname, '../..');
const SCRIPT = path.join(ROOT, 'scripts/build_process_library.py');

function run(entries) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'threadlight-playbook-'));
  const source = path.join(dir, 'source.json');
  const out = path.join(dir, 'out.json');
  fs.writeFileSync(source, JSON.stringify(entries));
  const result = spawnSync('python3', [SCRIPT, '--source', source, '--out', out], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  return {
    ...result,
    data: result.status === 0 ? JSON.parse(fs.readFileSync(out, 'utf8')) : null,
  };
}

const base = {
  id: 'sample',
  name: 'Sample',
  industry: 'retail',
  complexity: 'low',
  summary: 'Handle a simple retail request.',
  description: 'Sample process.',
  tags: [],
  business_constraints: [],
  external_integrations: [],
  human_approvals: [],
  knowledge_sources: [],
};

test('producer decorates sanitized entries with playbook metadata', () => {
  const result = run([base]);
  assert.strictEqual(result.status, 0, result.stderr);
  const playbook = result.data[0].playbook;
  assert.strictEqual(playbook.schema, 'threadlight.playbook/v1');
  assert.strictEqual(playbook.level, 'Starter');
  assert.strictEqual(playbook.use_when, base.summary);
  assert.deepStrictEqual(playbook.run_skills, []);
  assert.strictEqual(playbook.run_skills_source, 'generated-by-threadlight-design');
  assert.ok(playbook.build_skills.includes('threadlight-design'));
  assert.ok(playbook.artifacts.includes('specs/SPEC.md'));
});

test('producer derives rich scenario skills and stable artifacts', () => {
  const result = run([{
    ...base,
    complexity: 'high',
    industry: 'financial_services',
    tags: ['scheduled'],
    external_integrations: [{ name: 'SAP' }],
    human_approvals: [{ step: 'review' }],
  }]);
  assert.strictEqual(result.status, 0, result.stderr);
  const playbook = result.data[0].playbook;
  for (const skill of [
    'threadlight-demo-data-factory',
    'threadlight-hitl-patterns',
    'threadlight-event-triggers',
    'threadlight-redteam',
    'threadlight-govern',
    'threadlight-production-ready',
    'threadlight-consumption-iq',
  ]) assert.ok(playbook.build_skills.includes(skill), skill);
  assert.strictEqual(new Set(playbook.artifacts).size, playbook.artifacts.length);
});

test('producer rejects unsupported complexity values', () => {
  const result = run([{ ...base, complexity: 'extreme' }]);
  assert.strictEqual(result.status, 2);
  assert.match(result.stderr, /unsupported complexity.*extreme/i);
});
```

Extend `tests/blueprint/process-library.test.js` so every committed entry must
have:

```javascript
const LEVELS = ['Starter', 'Intermediate', 'Advanced'];
const PLAYBOOK_SCHEMA = 'threadlight.playbook/v1';

test('every entry has generated playbook metadata', () => {
  for (const e of data) {
    const p = e.playbook;
    assert.strictEqual(p.schema, PLAYBOOK_SCHEMA, e.id);
    assert.ok(LEVELS.includes(p.level), `${e.id} bad level`);
    assert.ok(p.use_when, `${e.id} missing use_when`);
    assert.ok(Array.isArray(p.build_skills) && p.build_skills.length > 0);
    assert.deepStrictEqual(p.run_skills, []);
    assert.strictEqual(p.run_skills_source, 'generated-by-threadlight-design');
    assert.ok(Array.isArray(p.prerequisites) && p.prerequisites.length > 0);
    assert.ok(Array.isArray(p.artifacts) && p.artifacts.length > 0);
  }
});
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
node --test tests/blueprint/process-library-generator.test.js \
  tests/blueprint/process-library.test.js
```

Expected: producer tests fail because `playbook` is absent; committed-data test
fails on the first process-library entry.

- [ ] **Step 3: Implement metadata derivation in the producer**

In `scripts/build_process_library.py`:

1. Add `use_when` to the sanitization whitelist.
2. Add constants matching Blueprint's existing `CANON`, regulated industries,
   complexity mapping, prerequisites, and stable artifact mapping.
3. Add:

```python
def derive_build_skills(entry: dict) -> list[str]:
    need = {
        "threadlight-design",
        "threadlight-local-test",
        "threadlight-safe-check",
        "threadlight-deploy",
        "threadlight-cicd",
        "threadlight-evals",
    }
    if entry.get("external_integrations"):
        need.add("threadlight-demo-data-factory")
    if entry.get("human_approvals"):
        need.add("threadlight-hitl-patterns")
    tags = [str(tag).lower() for tag in entry.get("tags") or []]
    if any(re.search(r"event|trigger|schedul|webhook|cron|real[- ]?time|stream", tag)
           for tag in tags):
        need.add("threadlight-event-triggers")
    if entry.get("complexity") == "high":
        need.update({
            "threadlight-production-ready",
            "threadlight-govern",
            "threadlight-redteam",
        })
    regulated_tag = any(
        re.search(r"regulat|complian|hipaa|gdpr|sox|pci|audit", tag)
        for tag in tags
    )
    if entry.get("industry") in REGULATED or regulated_tag:
        need.add("threadlight-consumption-iq")
    return [skill for skill in CANON if skill in need]


def build_playbook(entry: dict) -> dict:
    complexity = entry.get("complexity")
    if complexity not in LEVELS:
        raise ValueError(f"unsupported complexity {complexity!r}")
    build_skills = derive_build_skills(entry)
    artifacts: list[str] = []
    for skill in build_skills:
        for artifact in ARTIFACTS_BY_SKILL.get(skill, []):
            if artifact not in artifacts:
                artifacts.append(artifact)
    return {
        "schema": "threadlight.playbook/v1",
        "level": LEVELS[complexity],
        "use_when": entry.get("use_when") or entry["summary"],
        "build_skills": build_skills,
        "run_skills": [],
        "run_skills_source": "generated-by-threadlight-design",
        "prerequisites": list(PREREQUISITES),
        "artifacts": artifacts,
    }
```

Change `sanitise()` to derive only from sanitized fields:

```python
def sanitise(entry: dict) -> dict:
    clean = {k: entry.get(k) for k in KEEP if k in entry}
    clean["playbook"] = build_playbook(clean)
    return clean
```

Wrap list generation in `main()`:

```python
try:
    out = [sanitise(e) for e in raw]
except (KeyError, TypeError, ValueError) as exc:
    print(f"invalid process-library entry: {exc}", file=sys.stderr)
    return 2
```

- [ ] **Step 4: Run producer tests**

Run:

```bash
node --test tests/blueprint/process-library-generator.test.js
```

Expected: PASS.

- [ ] **Step 5: Regenerate the committed process library**

Run:

```bash
python3 scripts/build_process_library.py \
  --source docs/assets/process-library.json \
  --out docs/assets/process-library.json
```

Expected: `wrote <count> entries -> docs/assets/process-library.json`.

- [ ] **Step 6: Run committed-data tests**

Run:

```bash
node --test tests/blueprint/process-library.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit generated metadata**

```bash
git add scripts/build_process_library.py docs/assets/process-library.json \
  tests/blueprint/process-library-generator.test.js \
  tests/blueprint/process-library.test.js
git commit -m "feat: generate process playbook metadata"
```

### Task 3: Make Blueprint consume playbook metadata

**Files:**
- Modify: `tests/blueprint/blueprint-logic.test.js`
- Modify: `docs/assets/blueprint-logic.js`

- [ ] **Step 1: Write failing metadata-consumption tests**

Add to `tests/blueprint/blueprint-logic.test.js`:

```javascript
test('deriveSkills prefers generated playbook metadata', () => {
  const p = {
    ...base,
    complexity: 'high',
    playbook: {
      build_skills: ['threadlight-design', 'threadlight-deploy'],
    },
  };
  assert.deepStrictEqual(
    L.deriveSkills(p),
    ['threadlight-design', 'threadlight-deploy'],
  );
});

test('deriveSkills retains signal fallback for legacy entries', () => {
  const legacy = {
    ...base,
    complexity: 'high',
    industry: 'financial_services',
    human_approvals: [{ step: 'review' }],
  };
  const skills = L.deriveSkills(legacy);
  assert.ok(skills.includes('threadlight-hitl-patterns'));
  assert.ok(skills.includes('threadlight-production-ready'));
  assert.ok(skills.includes('threadlight-consumption-iq'));
});

test('committed metadata matches legacy derivation', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const entries = JSON.parse(fs.readFileSync(
    path.join(__dirname, '../../docs/assets/process-library.json'), 'utf8'));
  for (const entry of entries) {
    const metadataSkills = L.deriveSkills(entry);
    const legacySkills = L.deriveSkills({ ...entry, playbook: undefined });
    assert.deepStrictEqual(metadataSkills, legacySkills, entry.id);
  }
});
```

- [ ] **Step 2: Run the test and verify metadata is ignored**

Run:

```bash
node --test tests/blueprint/blueprint-logic.test.js
```

Expected: FAIL in `deriveSkills prefers generated playbook metadata`; current
logic derives the high-complexity chain instead.

- [ ] **Step 3: Prefer generated metadata with canonical ordering**

At the top of `deriveSkills()` in
`docs/assets/blueprint-logic.js`, add:

```javascript
var declared = p.playbook && p.playbook.build_skills;
if (Array.isArray(declared)) {
  return CANON.filter(function (skill) {
    return declared.indexOf(skill) !== -1;
  });
}
```

Leave the existing signal derivation unchanged below it as the compatibility
fallback.

- [ ] **Step 4: Run Blueprint logic and full data tests**

Run:

```bash
node --test tests/blueprint/blueprint-logic.test.js
node --test tests/blueprint/*.test.js
```

Expected: both commands PASS and existing generated prompts remain unchanged.

- [ ] **Step 5: Commit Blueprint consumption**

```bash
git add docs/assets/blueprint-logic.js \
  tests/blueprint/blueprint-logic.test.js
git commit -m "feat: consume generated playbook skill metadata"
```

### Task 4: Wire CI and verify the integrated contracts

**Files:**
- Modify: `.github/workflows/docs-blueprint.yml`
- Modify: `docs/superpowers/specs/2026-08-05-runtime-policy-playbook-metadata-design.md`

- [ ] **Step 1: Extend workflow path filters**

Add these paths under both `pull_request.paths` and `push.paths` in
`.github/workflows/docs-blueprint.yml`:

```yaml
- "THREADLIGHT.md"
- "skills/threadlight-auto/SKILL.md"
- "skills/threadlight-deploy/SKILL.md"
- "skills/threadlight-design/SKILL.md"
- "skills/threadlight-design/references/foundation-template.md"
- "skills/threadlight-design/references/runtime-policy.json"
```

The existing `node --test tests/blueprint/*.test.js` command already discovers
both new guard files.

- [ ] **Step 2: Mark the design implemented**

In
`docs/superpowers/specs/2026-08-05-runtime-policy-playbook-metadata-design.md`,
change:

```markdown
- **Status:** Implemented
```

- [ ] **Step 3: Run integrated verification**

Run:

```bash
node --test tests/blueprint/*.test.js
python3 -m py_compile scripts/build_process_library.py
git diff --check
```

Expected: all Node tests PASS, Python compilation exits 0, and
`git diff --check` prints nothing.

- [ ] **Step 4: Confirm generated-data stability**

Run:

```bash
cp docs/assets/process-library.json /tmp/process-library.before.json
python3 scripts/build_process_library.py \
  --source docs/assets/process-library.json \
  --out docs/assets/process-library.json
cmp /tmp/process-library.before.json docs/assets/process-library.json
rm /tmp/process-library.before.json
```

Expected: producer reports the entry count and `cmp` exits 0, proving
idempotent generation.

- [ ] **Step 5: Commit CI and status updates**

```bash
git add .github/workflows/docs-blueprint.yml \
  docs/superpowers/specs/2026-08-05-runtime-policy-playbook-metadata-design.md
git commit -m "ci: guard runtime and playbook contracts"
```
