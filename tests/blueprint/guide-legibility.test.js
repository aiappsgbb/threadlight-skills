const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { governanceHistory } = require('./helpers/governance-history');
const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const panels = () => [...read('docs/agent-governance.html').matchAll(/<section[^>]*data-guide-step="([^"]+)"([\s\S]*?)<\/section>/g)];
const skills = {
  inspect: ['threadlight-governed-actions'],
  select: ['threadlight-govern'],
  prepare: ['threadlight-deploy', 'threadlight-cicd'],
  validate: ['threadlight-cicd', 'threadlight-governed-actions', 'threadlight-safe-check'],
  promote: ['threadlight-cicd', 'threadlight-production-ready'],
};
const hashes = {
  inspect: ['ee3735ec2ed67dcec5b198b80f636f6f2206cee49ad3125117d80c1791265919', '70735bd2bc5b8f275f64e59baa2f3c5a0743a33a3a58e1067d0d2ee8e737ab9c'],
  select: ['d228f730e2b6b732fca52eafafdae9eb0a42f1c9db32017e0c5b3aa3e7567937', '0ceb542be687bf4d20de113674335fc9f1ef1dd6a0715938a17ab1129cadd69b'],
  prepare: ['0297b0f2f72854b0b6b70239e3d3935745bd2ec8d90bb4e86c6a48359237034e', '37d2ebd4807bef25b99a2e2c2362b7c38b8c0a16bbbb61847a7af2b9a8b05c99'],
  validate: ['327d9846e57592c3eb96422119aab771c5be849f8142858ee7bc64442fd6bea8', 'f46b4e988b73aa48d4e0ab6976d4cd51a5ba888fc874e8d64d2be53f38b9e827'],
  promote: ['5026aa0237a434158becb40a578d89ffbea9406023362b441d2a661b4d8712b0', 'ad9d36f42b819c8dc6e4126c6f9f2c7a1c0228b57e2cc7505e588c939a273234'],
};

test('every step names exact primary/support skills with real contract links', () => {
  assert.equal(panels().length, 5);
  for (const [, id, body] of panels()) {
    const row = body.match(/<div class="pg-skills"[\s\S]*?<\/div>/)?.[0];
    assert.ok(row, id);
    assert.ok(row.includes('>Skills</span>'), id);
    const links = [...row.matchAll(/href="([^"]+)"[^>]*>(threadlight-[a-z-]+)<\/a>/g)];
    assert.deepEqual(links.map(([, , name]) => name), skills[id], id);
    for (const [, href, name] of links) {
      assert.equal(href, `https://github.com/aiappsgbb/threadlight-skills/blob/c8de52e6652ef0e7830cf7774aa8deb2fffc6f2f/skills/${name}/SKILL.md`);
      assert.ok(fs.existsSync(path.join(root, 'skills', name, 'SKILL.md')), name);
    }
    assert.equal((row.match(/data-skill-role="primary"/g) || []).length, 1);
  }
});

test('short guide labels use only restrained decorative native icons', () => {
  for (const [, id, body] of panels()) {
    for (const [label, pattern, icon] of [
      ['Skills', /<span class="pg-skills-label">([\s\S]*?)<\/span>/, 'package'],
      ['Prompt', /<h3 class="pg-prompt-title">([\s\S]*?)<\/h3>/, 'inventory'],
      ['Verify', /<h3 class="pg-verify-title">([\s\S]*?)<\/h3>/, 'shield'],
    ]) {
      const heading = body.match(pattern)?.[1];
      assert.ok(heading, `${id}: ${label}`);
      assert.match(heading, new RegExp(`<svg class="pg-heading-icon" aria-hidden="true"><use href="#pg-icon-${icon}"\\s*\\/>`));
      assert.equal(heading.replace(/<[^>]*>/g, '').trim(), label);
    }
    assert.equal((body.match(/class="pg-heading-icon"/g) || []).length, 3);
    assert.doesNotMatch(body, /Prompt for your coding agent|Verify before continuing/);
  }
});

test('expected outputs are conditional, concrete and visually named without fake completion', () => {
  const expected = {
    inspect: ['Inventory summary', 'Evidence gaps', 'Read-only; no files emitted'],
    select: ['Governance delta proposal', 'specs/governance-contract.json'],
    prepare: ['Reviewed candidate inputs', 'specs/release-policy.json', 'Adapter handoff', 'No image build or deployment'],
    validate: ['Only after accepted execution', '.threadlight-release/candidate.json', 'Business/audit correlation'],
    promote: ['Only after the approved run', 'Production observation', 'Operations handoff'],
  };
  for (const [, id, body] of panels()) {
    const output = body.match(/<div class="pg-expected"[\s\S]*?<\/div>/)?.[0];
    assert.ok(output, id);
    assert.ok(output.includes('Expected result'), id);
    for (const value of expected[id]) assert.ok(output.includes(value), `${id}: ${value}`);
    const items = [...output.matchAll(/<li>/g)];
    assert.ok(items.length >= 2 && items.length <= 3, id);
    assert.equal((output.match(/<svg[^>]*aria-hidden="true"/g) || []).length, items.length);
    const href = output.match(/<a href="([^"]+)"/)?.[1];
    assert.ok(href, `${id}: detailed reference`);
    assert.ok(href.startsWith('https://github.com/aiappsgbb/threadlight-skills/blob/7782eba93754fb7cff85336d3f4a8703892bad76/docs/first-governed-workflow.md#phase-'), id);
    const fragment = href.split('#')[1];
    const anchors = [...read('docs/first-governed-workflow.md').matchAll(/^#{1,6} (.+)$/gm)]
      .map(([, heading]) => heading.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-'));
    assert.ok(anchors.includes(fragment), fragment);
    assert.doesNotMatch(output, />Generated<|>Passed<|production.ready/i);
  }
});

test('prompts and verification text remain exact while presentation changes', () => {
  for (const [, id, body] of panels()) {
    for (const [index, source] of [
      body.match(/<pre[^>]*data-guide-prompt[^>]*>([\s\S]*?)<\/pre>/)[1],
      body.match(/<ul class="pg-verify">([\s\S]*?)<\/ul>/)[1],
    ].entries()) assert.equal(createHash('sha256').update(source).digest('hex'), hashes[id][index], id);
  }
});

test('current presentation snapshots stay fixed outside the explicit signed-evidence extension', () => {
  for (const [file, expected] of [
    ['docs/production.html', '8ca8cc61e7b3791b7a4c04a8d7dcc61a0938355d'],
    ['docs/assets/governed-workflow.js', 'd7157b6d34e0e190055a86a3121033b4b418d700'],
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
