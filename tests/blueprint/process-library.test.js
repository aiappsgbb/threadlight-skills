const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const data = JSON.parse(fs.readFileSync(
  path.join(__dirname, '../../docs/assets/process-library.json'), 'utf8'));

const REQUIRED = ['id', 'name', 'industry', 'complexity', 'summary', 'description', 'tags'];
const INTERNAL = ['pregenerated_job_id'];
// NARROW leak scrub for third-party data: only true supply-chain / internal
// markers. Business words (competitive / confidential / compliance) are LEGIT.
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
