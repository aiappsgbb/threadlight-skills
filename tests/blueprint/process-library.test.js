const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const L = require('../../docs/assets/blueprint-logic.js');

const data = JSON.parse(fs.readFileSync(
  path.join(__dirname, '../../docs/assets/process-library.json'), 'utf8'));

const REQUIRED = ['id', 'name', 'industry', 'complexity', 'summary', 'description', 'tags'];
const INTERNAL = ['pregenerated_job_id'];
const PLAYBOOK_PREREQUISITES = ['github-copilot', 'threadlight-skills', 'azure-subscription'];
const PLAYBOOK_LEVELS = ['Starter', 'Intermediate', 'Advanced'];
const ALLOWED_SKILLS = new Set(L.CANON);
// NARROW leak scrub for third-party data: only true supply-chain / internal
// markers. Generic business vocabulary (e.g. audit / regulatory / risk) is legitimate.
const LEAK = /agentic[- ]?loop|threadlight-vnext|northcentralus|remote-gw|gpt-5\.1/i;

test('library is a non-empty array', () => {
  assert.ok(Array.isArray(data) && data.length > 0);
});

test('every entry has required fields + valid complexity', () => {
  for (const e of data) {
    for (const k of REQUIRED) assert.ok(e[k] != null, `${e.id} missing ${k}`);
    assert.ok(['low', 'medium', 'high'].includes(e.complexity), `${e.id} bad complexity`);
  }
});

test('no internal fields survive the whitelist', () => {
  for (const e of data) {
    for (const k of INTERNAL) assert.ok(!(k in e), `${e.id} leaked ${k}`);
  }
});

test('no supply-chain leak markers', () => {
  assert.ok(!LEAK.test(JSON.stringify(data)));
});

test('every entry has complete valid generated playbook metadata', () => {
  for (const e of data) {
    const p = e.playbook;
    assert.ok(p && typeof p === 'object', `${e.id} missing playbook`);
    assert.strictEqual(p.schema, 'threadlight.playbook/v1', `${e.id} bad playbook schema`);
    assert.ok(PLAYBOOK_LEVELS.includes(p.level), `${e.id} bad playbook level`);
    assert.strictEqual(typeof p.use_when, 'string', `${e.id} playbook use_when must be a string`);
    assert.ok(p.use_when.trim(), `${e.id} playbook use_when must be non-empty`);

    assert.ok(Array.isArray(p.build_skills) && p.build_skills.length > 0, `${e.id} missing build_skills`);
    assert.strictEqual(new Set(p.build_skills).size, p.build_skills.length, `${e.id} build_skills must be unique`);
    let canonIndex = -1;
    for (const skill of p.build_skills) {
      assert.ok(ALLOWED_SKILLS.has(skill), `${e.id} unknown build skill ${skill}`);
      const idx = L.CANON.indexOf(skill);
      assert.ok(idx > canonIndex, `${e.id} build skills out of canonical order`);
      canonIndex = idx;
    }
    assert.deepStrictEqual(p.build_skills, L.deriveSkills(e), `${e.id} build_skills drift from deriveSkills`);

    assert.deepStrictEqual(p.run_skills, [], `${e.id} run_skills must stay empty`);
    assert.strictEqual(p.run_skills_source, 'generated-by-threadlight-design', `${e.id} bad run_skills_source`);
    assert.deepStrictEqual(p.prerequisites, PLAYBOOK_PREREQUISITES, `${e.id} bad prerequisites`);

    assert.ok(Array.isArray(p.artifacts) && p.artifacts.length > 0, `${e.id} missing artifacts`);
    assert.strictEqual(new Set(p.artifacts).size, p.artifacts.length, `${e.id} artifacts must be unique`);
    for (const artifact of p.artifacts) {
      assert.strictEqual(typeof artifact, 'string', `${e.id} artifact must be a string`);
      assert.ok(artifact.trim(), `${e.id} artifact must be non-empty`);
    }
  }
});
