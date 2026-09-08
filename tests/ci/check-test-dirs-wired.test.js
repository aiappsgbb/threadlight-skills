const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const repoRoot = path.join(__dirname, '../..');
const CHECKER = path.join('scripts', 'ci', 'check-test-dirs-wired.py');
const WORKFLOW = path.join('.github', 'workflows', 'python-pytest.yml');
const GOVERNED_ACTIONS_TESTS = 'skills/threadlight-governed-actions/tests';

function runChecker(root) {
  return spawnSync('python3', [path.join(root, CHECKER)], {
    cwd: root,
    encoding: 'utf8',
  });
}

// The checker resolves its own repository root from __file__, so a faithful
// negative case needs a real directory tree rather than a monkeypatched
// constant: scripts/ci/<checker>, the workflow it reads, and the suites it
// globs. The tree is built under this test directory (never a shared temp
// dir) and always removed again, so a failed assertion cannot leave a stray
// skills/ tree behind for the real checker or pytest to discover.
function withFixtureRoot(mutateWorkflow, body) {
  const fixtureRoot = fs.mkdtempSync(path.join(__dirname, '.tmp-wired-'));
  try {
    fs.mkdirSync(path.join(fixtureRoot, 'scripts', 'ci'), { recursive: true });
    fs.mkdirSync(path.join(fixtureRoot, '.github', 'workflows'), { recursive: true });
    fs.copyFileSync(path.join(repoRoot, CHECKER), path.join(fixtureRoot, CHECKER));

    for (const suite of ['threadlight-governed-actions', 'threadlight-design']) {
      const suiteDir = path.join(fixtureRoot, 'skills', suite, 'tests');
      fs.mkdirSync(suiteDir, { recursive: true });
      fs.writeFileSync(path.join(suiteDir, 'test_fixture.py'), 'def test_fixture():\n    assert True\n');
    }

    const workflow = mutateWorkflow(fs.readFileSync(path.join(repoRoot, WORKFLOW), 'utf8'));
    fs.writeFileSync(path.join(fixtureRoot, WORKFLOW), workflow);

    body(fixtureRoot);
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
}

test('the real repository has every pytest suite wired into python-pytest.yml', () => {
  const result = runChecker(repoRoot);
  assert.strictEqual(result.status, 0, `checker failed:\n${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, new RegExp(GOVERNED_ACTIONS_TESTS));
});

test('omitting skills/threadlight-governed-actions/tests from the workflow fails the checker', () => {
  withFixtureRoot(
    (workflow) =>
      workflow
        .split('\n')
        .filter((line) => !line.includes(GOVERNED_ACTIONS_TESTS))
        .join('\n'),
    (fixtureRoot) => {
      const result = runChecker(fixtureRoot);
      assert.strictEqual(result.status, 1, `expected exit 1, got ${result.status}\n${result.stdout}`);
      assert.match(result.stderr, new RegExp(`- ${GOVERNED_ACTIONS_TESTS}`));
    },
  );
});

test('keeping the governed-actions step wired passes the same fixture', () => {
  withFixtureRoot(
    (workflow) => workflow,
    (fixtureRoot) => {
      const result = runChecker(fixtureRoot);
      assert.strictEqual(
        result.status,
        0,
        `expected exit 0, got ${result.status}\n${result.stdout}\n${result.stderr}`,
      );
      assert.match(result.stdout, new RegExp(GOVERNED_ACTIONS_TESTS));
    },
  );
});

test('python-pytest.yml runs the governed-actions suite as its own explicit step', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, WORKFLOW), 'utf8');
  const step = workflow.match(
    /^\s+- name: Test threadlight-governed-actions\n((?:[ \t]{8,}[^\n]*(?:\n|$))*)/m,
  );
  assert.ok(step, 'governed-actions must keep its own unit-test step');
  const command = step[1].split('\n').map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#')).join(' ');
  assert.match(
    command,
    /^run:\s*(?:>-\s*)?python -m pytest skills\/threadlight-governed-actions\/tests -q(?:\s|$)/,
  );
  assert.match(command, /-m "not governance_runtime"/);
  assert.match(workflow, /run: python scripts\/ci\/run-governance-pin-tests\.py --native-local/);
});
