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
  assert.match(body, /class="authority-steps"/);
  for (const phrase of ['The model proposes', 'Identity', 'Policy', 'Trusted facts', 'Human decision',
    'Outlook', 'independent business API', 'audit ACK', 'unbound', 'without ACS',
    'integration profiles', 'network isolation']) assert.ok(text(body).includes(phrase), phrase);
  assert.match(body, /agent-governance-deep-dive\.md/);
  assert.match(read('agent-governance-deep-dive.md'), /references\/gateway\/dispatcher\.py/);
});

test('commercial value cards link to technical evidence without exposing instance status', () => {
  const body = section(read('production.html'), 'evidence-boundaries');
  for (const phrase of ['accountable outcomes', 'Make autonomy useful', 'Human judgement, connected',
    'Connect actions to outcomes', 'Identity', 'policy', 'human decisions', 'audit']) {
    assert.ok(text(body).includes(phrase), phrase);
  }
  assert.doesNotMatch(text(body), /4\/4|NOT PROVED|EXPIRED|S3|BASIC v7|version 4/i);
  assert.match(body, /agent-governance-deep-dive\.md/);
  assert.match(body, /governed-returns-validation\.md/);
});

test('commercial reframing preserves private execution facts in the engineering record', () => {
  const record = read('governed-returns-validation.md');
  for (const fact of ['S2-PRIVATE-ALLOW-DENY-VERIFIED', 'returns-hosted-reference',
    '4/4', 'two responses', 'no new private pending intent', 'user was unavailable',
    'not fully governed', 'faa5f3e3b3824c9b9df0287e0639e313', '9d555842b82149dbb756d30e64fdeee8']) {
    assert.ok(record.includes(fact), fact);
  }
});

test('commercial pages retain private evidence and authority details only in linked technical documents', () => {
  const body = read('production.html');
  const record = read('governed-returns-validation.md');
  for (const value of ['2026-09-14T10:26:48.991423+00:00', '2026-09-14T12:03:41.859581+00:00',
    '2026-09-15T11:12:28.719119+00:00', 'SecurityControl=Ignore']) {
    assert.ok(record.includes(value), value);
    assert.ok(!body.includes(value), value);
  }
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
  assert.doesNotMatch(source, /blob\/main\/docs\/(?:agent-governance-deep-dive|governed-returns-validation)\.md/);
  assert.doesNotMatch(source, /blob\/main\/skills\/threadlight-deploy\/references\/governance\/returns_mcp_backend\.py/);
});

test('legacy implementation status and no-JavaScript test routing are explicit', () => {
  const specification = read('production-readiness-pages-spec.md');
  assert.ok(specification.includes('Legacy page copy implemented'));
  for (const evidence of ['16 targeted Playwright cases passed', '8 passed / 2 failed',
    'light and dark', 'zero violations', 'not full WCAG']) {
    assert.ok(specification.includes(evidence), evidence);
  }
  assert.ok(!specification.includes('formal Playwright/axe runner remains unexecuted'));
  const browserTest = fs.readFileSync(path.join(docs, '../tests/playwright/tests/production-governance.spec.mjs'), 'utf8');
  assert.ok(browserTest.includes('baseURL: testInfo.project.use.baseURL'));
});

test('new governance contrast fixes are page-local and keep links distinguishable', () => {
  const style = read('production.html').match(/<style>([\s\S]*?)<\/style>/)[1];
  const rules = [...style.matchAll(/([^{}]+)\{([^}]+)\}/g)];
  for (const selector of ['.governance-section .eyebrow', '.governance-section .rd-label',
    '.governance-section .section-lede a']) {
    assert.ok(rules.some(([, selectors, declarations]) => selectors.split(',').some((s) => s.trim() === selector)
      && /color:\s*var\(--ink-1\)/.test(declarations)), selector);
  }
  assert.ok(rules.some(([, selectors, declarations]) => selectors.trim() === '.governance-section .section-lede a'
    && /text-decoration:\s*underline/.test(declarations)));
  assert.doesNotMatch(style, /outline:\s*(?:none|0)(?:[;}])/);
});
