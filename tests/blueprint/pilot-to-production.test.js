const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { governanceHistory } = require('./helpers/governance-history');
const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');

test('the default Production diagram preserves existing geometry beside a separate evidence sequence', () => {
  const html = read('docs/production.html');
  assert.match(html, /viewBox="0 0 1100 215"/);
  const expected = {
    proposal: [20, 60, 160, 68], checks: [245, 60, 160, 68],
    ack: [470, 60, 160, 68], effect: [695, 60, 160, 68], result: [920, 60, 160, 68],
    deny: [470, 3, 210, 42], review: [245, 156, 160, 54], fresh: [470, 156, 160, 54],
  };
  const actual = Object.fromEntries([...html.matchAll(
    /data-flow-node="([^"]+)"[^>]*transform="translate\((\d+) (\d+)\)"[^>]*>\s*<rect width="(\d+)" height="(\d+)"/g
  )].map(([, id, ...values]) => [id, values.map(Number)]));
  assert.deepEqual(actual, expected);
  const { usedModules, permittedEdges } = require('../../docs/assets/governed-workflow.js');
  assert.deepEqual(usedModules('normal'), ['proposal', 'checks', 'ack', 'effect', 'result']);
  assert.deepEqual(usedModules('invalid'), ['proposal', 'checks', 'ack', 'deny']);
  assert.deepEqual(permittedEdges('invalid'), ['proposal:checks', 'checks:ack', 'ack:deny']);
  assert.ok(!usedModules('supervisor').includes('deny'));
  for (const scenario of ['normal', 'invalid', 'supervisor']) {
    for (const edge of permittedEdges(scenario)) {
      assert.ok(edge.split(':').every(node => usedModules(scenario).includes(node)));
    }
  }
});

test('the guide starts from an existing Threadlight pilot and has five actionable panels', () => {
  const html = read('docs/agent-governance.html');
  assert.match(html, /Take your Threadlight pilot to governed production/);
  const panels = [...html.matchAll(/<section[^>]*data-guide-step="([^"]+)"[\s\S]*?<\/section>/g)];
  assert.equal(panels.length, 5);
  for (const [panel] of panels) {
    assert.match(panel, /data-guide-prompt/);
    assert.match(panel, /class="pg-verify-title">[\s\S]*?Verify<\/h3>/);
    const checks = panel.match(/<ul class="pg-verify">([\s\S]*?)<\/ul>/)[1];
    assert.ok([...checks.matchAll(/<li>/g)].length >= 2);
    const prompt = panel.match(/<pre[^>]*data-guide-prompt[^>]*>([\s\S]*?)<\/pre>/)[1];
    assert.match(prompt, /existing|reuse|reviewed/i);
    for (const [skill] of prompt.matchAll(/threadlight-[a-z]+(?:-[a-z]+)*/g)) {
      assert.ok(fs.existsSync(path.join(root, 'skills', skill, 'SKILL.md')), skill);
    }
    assert.doesNotMatch(prompt, /threadlight-qualify|threadlight-design|create a new pilot|Design in Full mode/);
  }
  for (const token of ['specs/SPEC.md', 'specs/foundation.md', 'specs/manifest.json',
    'specs/governance-contract.json', 'specs/release-policy.json', '.threadlight-release/candidate.json',
    'governance_probe_noop', 'same immutable image']) assert.ok(html.includes(token), token);
  assert.match(html, /guidance, not cloud execution/i);
  const script = read('docs/assets/pilot-to-production.js');
  assert.match(script, /await navigator\.clipboard\.writeText/);
  assert.match(script, /Clipboard unavailable/);
  assert.doesNotMatch(script, /\bfetch\(|XMLHttpRequest|WebSocket|setInterval/);
  assert.match(html, /test-owned/i);
});

test('the workbook and current architecture baseline remain byte-frozen outside the signed-evidence addition', () => {
  for (const [file, expected] of [
    ['docs/first-governed-workflow.md', '1d6ed17fa77e14e22633c5fdf56e7e45b814e312'],
    ['docs/agent-governance-deep-dive.md', '006b99c4c80262eb6571a0a21ee46b12b4df95c6'],
    ['docs/assets/governance/effect-boundaries.svg', '69fa014350e3f7acaf53b8e37541717b99f8a6dd'],
  ]) {
    const bytes = file === 'docs/agent-governance-deep-dive.md'
      ? Buffer.from(governanceHistory(read(file)).baseline)
      : fs.readFileSync(path.join(root, file));
    assert.equal(createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex'), expected, file);
  }
});
