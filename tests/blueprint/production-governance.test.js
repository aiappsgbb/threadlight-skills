const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { homeHistory } = require('./helpers/home-history');

const docs = path.join(__dirname, '../../docs');
const read = (name) => fs.readFileSync(path.join(docs, name), 'utf8');
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&')
  .replace(/&(?:mdash|ndash);/g, '—').replace(/\s+/g, ' ');
const section = (html, id) => {
  const match = html.match(new RegExp(`<section\\b[^>]*id="${id}"[^>]*>([\\s\\S]*?)</section>`));
  assert.ok(match, `missing section ${id}`);
  return match[1];
};

test('returning readers receive the current Production navigation script', () => {
  const digest = createHash('sha256').update(read('assets/production.js')).digest('hex').slice(0, 8);
  assert.ok(read('production.html').includes(`assets/production.js?v=${digest}`));
  assert.match(read('ci/sync_cache_bust.py'), /"production\.js":/);
});

test('primary pages remove universal authority, invented sign-off and readiness-speed claims', () => {
  for (const name of ['index.html', 'funnel.html', 'production.html']) {
    const original = read(name);
    const source = name === 'index.html' ? homeHistory(original).current : original;
    if (name === 'index.html') {
      assert.equal(homeHistory(original).snapshots.length, 3);
      assert.match(source, /Historical demo captions are not current guarantees/);
    }
    assert.doesNotMatch(text(source), /Foundry runs & governs it|The scorecard that signs|Every gap has a skill that closes it|ready in ~7m|ship with (?:2|two) waivers|92[–—/]100|Safe by default/i, name);
    assert.doesNotMatch(source, /data-to="92"/, 'a removed score must not return through animation');
  }
  const production = text(read('production.html'));
  assert.doesNotMatch(production, /Illustrative scorecard layout|91\s*%|\$0\.0123|EU AI Act evidence pack/);
  for (const phrase of ['Selected actions, not blanket protection.', 'not automatic go-live approval',
    'fresh evidence']) assert.ok(production.includes(phrase), phrase);
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
  assert.match(body, /<ol[^>]*data-action-primary[^>]*aria-label="Primary business-action path"/);
  assert.match(body, /<aside[^>]*data-action-support/);
  assert.match(body, /class="action-actors"/);
  for (const phrase of ['Access to a system', 'Identity', 'Policy', 'Trusted facts', 'Human reviewer',
    'Outlook', 'independent business API', 'audit ACK', 'unbound', 'without ACS',
    'model gateway']) assert.ok(text(body).toLowerCase().includes(phrase.toLowerCase()), phrase);
  assert.match(source, /agent-governance-deep-dive\.md/);
  assert.match(read('agent-governance-deep-dive.md'), /references\/gateway\/dispatcher\.py/);
});

test('concrete action outcomes link to technical evidence without exposing instance status', () => {
  const body = section(read('production.html'), 'evidence-boundaries');
  for (const phrase of ['Build this workflow', 'Try the guided workbook', 'Start locally',
    'human-review exercise', 'independently verified evidence']) {
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
  for (const name of ['docs/production-readiness-report.md', 'tests/production-readiness-manifest.json']) {
    assert.ok(read('production-readiness.md').includes(name), name);
  }
  for (const field of ['score.raw_percent', 'score.with_waivers_percent', 'would_fail_hard_gate', 'captured_at']) {
    assert.ok(read('production-readiness.md').includes(field), field);
  }
  for (const id of ['chapter-top', 'why', 'checks', 'legs', 'proof', 'target', 'ship', 'start', 'chapter-recap']) {
    assert.match(source, new RegExp(`id="${id}"`), `retained anchor ${id}`);
  }
  assert.match(source, /href="\.\/production\.html" aria-current="page"/);
});

test('remaining captions preserve conditional enforcement, private leases and working proof links', () => {
  const source = read('production.html');
  assert.doesNotMatch(text(source), /amber turns green|every tool call passes AGT|Nothing reaches the spoke unverified|never fails the build|Every check becomes a gate/i);
  assert.match(source, /href="https:\/\/github\.com\/aiappsgbb\/threadlight-skills\/blob\/[a-f0-9]+\/docs\/agent-governance-deep-dive\.md"/);
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

test('production keeps the returns example inside agent governance, not the general domains', () => {
  const source = read('production.html');
  const authority = section(source, 'effect-authority');
  assert.match(authority, /id="returns-walkthrough"/);
  assert.match(text(authority), /Can I return this order/);
  assert.match(text(authority), /not a payment/);
  for (const id of ['production-domains', 'platform-controls', 'operating-controls', 'delivery-controls']) {
    const body = section(source, id);
    assert.doesNotMatch(body, /data-case-context|data-journey/, `${id}: independent production concern`);
    assert.doesNotMatch(text(body), /\b(?:refund|supervisor|order)\b|\breturn(?:s)?\s+(?:policy|agent|record|decisions|history|acceptance|eligibility)/i, id);
  }
  assert.doesNotMatch(source, /data-journey-next|data-journey-step/);
});

test('each concise topic keeps its core visual and delegates detail to references', () => {
  const source = read('production.html');
  const visuals = {
    'platform-controls': 'citadel-hub', 'effect-authority': 'effect-sequence',
    'delivery-controls': 'deployment-lifecycle',
  };
  for (const [id, visual] of Object.entries(visuals)) {
    const body = section(source, id);
    assert.match(body, new RegExp(`data-visual="${visual}"`), `${id}: distinct visual explanation`);
    if (id === 'delivery-controls') {
      for (const boundary of ['preproduction', 'same image', 'required checks', 'does not roll back']) {
        assert.ok(text(body).includes(boundary), boundary);
      }
    }
  }
  const jwtContract = source.match(/<div class="wf-jwt" data-evidence-jwt[\s\S]*?<!-- evidence-jwt:end -->/)?.[0];
  assert.ok(jwtContract);
  assert.doesNotMatch(source.replace(jwtContract, ''), /<(?:details|summary)\b/);
  assert.match(source, /Application and data owners/);
  assert.match(source, /not supplied by the new action-governance runtime/);
  assert.match(section(source, 'operating-controls'), /DevSecOps/);
});

test('AgentOps frames the release area as controlled delivery rather than just readiness', () => {
  const source = read('production.html');
  const overview = section(source, 'production-domains');
  assert.match(text(overview), /02 \/ AgentOps & release/);
  assert.match(source, /id="tab-operations"[^>]*>AgentOps &amp; release/);
  const introduction = text(section(source, 'operating-controls'));
  for (const phrase of ['AgentOps', 'development to production', 'DevSecOps', 'prompt', 'policy']) {
    assert.ok(introduction.toLowerCase().includes(phrase.toLowerCase()), phrase);
  }
  const delivery = text(section(source, 'delivery-controls'));
  for (const phrase of ['preproduction', 'owner approval', 'PROD GATE',
    'same image', 'does not roll back', 'not cloud acceptance']) assert.ok(delivery.includes(phrase), phrase);
  assert.match(read('production-readiness-pages-spec.md'), /not automatic adoption of the optional.*AgentOps/s);
});

test('accepted AgentOps visuals remain stable while the current generator enforces verified release', () => {
  const source = read('production.html');
  assert.doesNotMatch(source, /id="(?:readiness-reference|delivery-reference)"/);
  for (const visual of ['ciso-pentagon-svg', 'pipe-svg', 'wf-diagram']) {
    assert.ok(source.includes(visual), visual);
  }
  assert.doesNotMatch(source, /class="(?:scorecard-preview|posture-trio-svg)/);
  assert.doesNotMatch(text(source), /all gates green.*ships|hard-block the merge|nothing calls home/i);
  const delivery = text(section(source, 'delivery-controls'));
  assert.doesNotMatch(delivery, /Soft is the default|soft \| hard|echo|needs: deploy|CI RESULT/);
  for (const phrase of ['application team', 'same image', 'production environment',
    'expired', 'business actions', 'local', 'not cloud acceptance']) {
    assert.ok(delivery.includes(phrase), phrase);
  }
  assert.doesNotMatch(delivery, /--receipt-sha256|THREADLIGHT_AGENTOPS_CONTEXT|client_id/);
  for (const reference of ['docs/agentops-deep-dive.md',
    'skills/threadlight-cicd/references/release-contract.md',
    'skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl',
    'skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl']) {
    assert.ok(source.includes(`/blob/04331ba8c4764b41ad90722d1022914b239bae2a/${reference}`), reference);
  }
  const templates = ['github-actions/azd-deploy-prod.yml.tmpl', 'azure-devops/azure-pipelines.yml.tmpl'];
  for (const template of templates) {
    const pipeline = read('../skills/threadlight-cicd/references/' + template);
    assert.match(pipeline, /(?:needs|dependsOn): validation/);
    assert.match(pipeline, /release_runner\.py/);
    assert.match(pipeline, /--receipt-sha256/);
    assert.doesNotMatch(pipeline, /echo "Run threadlight-evals|continue-on-error|continueOnError/);
  }
});
