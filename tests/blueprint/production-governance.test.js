const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const docs = path.join(__dirname, '../../docs');
const read = (name) => fs.readFileSync(path.join(docs, name), 'utf8');
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&')
  .replace(/&(?:mdash|ndash);/g, '—').replace(/\s+/g, ' ');
const section = (html, id) => {
  const match = html.match(new RegExp(`<section\\b[^>]*id="${id}"[^>]*>([\\s\\S]*?)</section>`));
  assert.ok(match, `missing section ${id}`);
  return match[1];
};

test('primary pages remove universal authority, invented sign-off and readiness-speed claims', () => {
  for (const name of ['index.html', 'funnel.html', 'production.html']) {
    const source = read(name);
    assert.doesNotMatch(text(source), /Foundry runs & governs it|The scorecard that signs|Every gap has a skill that closes it|ready in ~7m|ship with (?:2|two) waivers|92[–—/]100|Safe by default/i, name);
    assert.doesNotMatch(source, /data-to="92"/, 'a removed score must not return through animation');
  }
  const production = text(read('production.html'));
  for (const phrase of ['Evidence for the people who sign.', 'Every gap needs an owner and verified closure.',
    'Report generation time varies; completion is not readiness.', 'Illustrative review scenario',
    'verification coverage', 'must-fix']) assert.ok(production.includes(phrase), phrase);
});

test('prototype remains available and selected governance is an explicit pre-effect choice', () => {
  for (const name of ['index.html', 'funnel.html']) {
    const source = read(name);
    assert.match(text(source), /prototype (?:path |flow )?remains unchanged/i);
    assert.match(text(source), /explicit opt-in/i);
    assert.match(source, /href="\.\/production\.html#effect-authority"/);
  }
  assert.match(read('index.html'), /id="reel"/);
  assert.match(read('funnel.html'), /id="scene-cta"/);
});

test('authority section is an accessible ordered contract with distinct identities', () => {
  const source = read('production.html');
  const body = section(source, 'effect-authority');
  assert.match(source, /id="effect-authority"[^>]*data-toc-id="effect-authority"/);
  assert.match(source, /id="effect-authority"[^>]*aria-labelledby="effect-authority-heading"/);
  assert.match(body, /<ol[^>]*aria-label="Selected effect authorization sequence"/);
  assert.equal((body.match(/<li class="why-card"/g) || []).length, 6);
  for (const phrase of ['The model proposes', 'Agent Identity', 'publisher', 'human reviewer',
    'gateway identity', 'downstream identity', 'business writer', 'ACS', 'OPA',
    'central audit ACK', 'ETag', 'unbound', 'without ACS', 'trusted host',
    'chain-of-thought', 'network isolation']) assert.ok(text(body).includes(phrase), phrase);
  assert.match(body, /agent-governance-deep-dive\.md/);
  assert.match(body, /references\/gateway\/dispatcher\.py/);
});

test('evidence cards keep public business history distinct from private BASIC model smoke', () => {
  const body = section(read('production.html'), 'evidence-boundaries');
  for (const phrase of ['S3', 'September 13, 2026', 'HISTORICAL', 'v5', 'Cosmos decision',
    '14 tool calls', '8 native responses', 'before the read response', 'post-run',
    'BASIC v7', 'Billing Issue', '10:00:39', '10:00:42', 'two active',
    'no MCP', 'my_tool', 'SkillsProvider', 'NOT PROVED', 'human-approved',
    'replay', 'email', 'two exposed tools', 'OBO', 'not whole-agent attestation']) {
    assert.ok(text(body).includes(phrase), phrase);
  }
  assert.match(body, /id="s3-business-evidence"[^>]*data-evidence-status="historical"/);
  assert.match(body, /id="s2-basic-evidence"[^>]*data-evidence-status="live-executed-model-only"/);
  assert.match(body, /id="private-governed-evidence"[^>]*data-evidence-status="private-selected-allow-deny-verified"/);
  assert.match(body, /governed-returns-validation\.md/);
});

test('private milestone credits selected allow, exact deny and inline read ACK, not pending or human closure', () => {
  const card = read('production.html').match(/<article\b[^>]*id="private-governed-evidence"[^>]*>([\s\S]*?)<\/article>/);
  assert.ok(card, 'private evidence card');
  const copy = text(card[1]);
  for (const phrase of ['PRIVATE SELECTED ALLOW + EXACT DENY VERIFIED', 'September 14, 2026',
    'returns-hosted-reference', 'v4', 'returns_get_case', 'returns_apply_decision',
    'closed case', 'one decision/audit', 'completed gateway operation', 'central allow receipt',
    'inline read audit ACK', 'before the read response', 'exact agent image and identity',
    'two tools', 'exact-ETag domain deny', 'central deny receipt', 'unchanged case and revision',
    'business audit count stayed one', 'reviewer was unavailable', 'No new pending intent was created',
    'pending approval', 'human-approved resume', 'replay',
    'email delivery', 'NOT PROVED', 'two inline read ACKs',
    '2 native responses', '4 tool calls', 'verified private Key Vault binding',
    'independent private Cosmos stores', '4/4', 'created and read back',
    'post-run reconciliation', 'separate from the before-return read ACKs']) {
    assert.ok(copy.includes(phrase), phrase);
  }
  assert.doesNotMatch(copy, /14 tool calls|8 native responses|post-run ledger is not yet completed/);
});

test('expired public authority is adjacent to proof and cannot be renewed by receipts', () => {
  const body = section(read('production.html'), 'evidence-boundaries');
  for (const phrase of ['EXPIRED', '2026-09-14T10:26:48.991423+00:00',
    '2026-09-14T12:03:41.859581+00:00', '12:26 Italy', '14:03 Italy',
    'fresh checks even before expiry', 'not current executable authority',
    'no automatic renewal', 'resource retention', 'SecurityControl=Ignore',
    'not a production default']) assert.ok(text(body).includes(phrase), phrase);
  assert.doesNotMatch(body, /https:\/\/[a-z0-9-]+\.(?:azurecr\.io|vault\.azure\.net|azurecontainerapps\.io)/);
  assert.doesNotMatch(body, /\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/i);
});

test('artifact names and local navigation remain source-backed', () => {
  const source = read('production.html');
  for (const name of ['docs/production-readiness-report.md', 'tests/production-readiness-manifest.json',
    'raw_score', 'score_with_waivers', 'would_fail_hard_gate', 'captured_at']) {
    assert.ok(source.includes(name), name);
  }
  for (const id of ['chapter-top', 'why', 'checks', 'legs', 'proof', 'target', 'ship', 'start', 'chapter-recap']) {
    section(source, id);
  }
  assert.match(source, /href="\.\/production\.html" aria-current="page"/);
});

test('remaining captions preserve conditional enforcement, private leases and working proof links', () => {
  const source = read('production.html');
  assert.doesNotMatch(text(source), /amber turns green|every tool call passes AGT|Nothing reaches the spoke unverified|never fails the build|Every check becomes a gate/i);
  assert.match(source, /href="\.\/agent-governance\.html"/);
  const card = source.match(/<article\b[^>]*id="private-governed-evidence"[^>]*>([\s\S]*?)<\/article>/)[1];
  assert.ok(card.includes('2026-09-15T11:12:28.719119+00:00'));
  assert.ok(card.includes('2026-09-15T11:32:54.753384+00:00'));
  assert.doesNotMatch(source, /blob\/main\/docs\/(?:agent-governance-deep-dive|governed-returns-validation)\.md/);
  assert.doesNotMatch(source, /blob\/main\/skills\/threadlight-deploy\/references\/governance\/returns_mcp_backend\.py/);
});

test('legacy implementation status and no-JavaScript test routing are explicit', () => {
  const specification = read('production-readiness-pages-spec.md');
  assert.ok(specification.includes('Legacy page copy implemented'));
  assert.ok(specification.includes('formal Playwright/axe runner remains unexecuted'));
  const browserTest = fs.readFileSync(path.join(docs, '../tests/playwright/tests/production-governance.spec.mjs'), 'utf8');
  assert.ok(browserTest.includes('baseURL: testInfo.project.use.baseURL'));
});
