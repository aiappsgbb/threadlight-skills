const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = file => fs.readFileSync(path.resolve(__dirname, '../..', file), 'utf8');
const model = require('../../docs/assets/governed-workflow.js');

test('requesting-user confirmation has its own actors and no implicit reviewer consent', () => {
  const route = model.scenarioDetails('confirmation', 'confirmed').steps;
  assert.deepEqual(route, ['proposal', 'checks', 'confirmation-pending', 'confirm',
    'confirmation-verify', 'fresh', 'ack', 'effect', 'result']);
  assert.equal(model.stepDetails('confirmation', 'confirm', 'confirmed').actor, 'Requesting user');
  assert.match(model.stepDetails('confirmation', 'confirmation-pending').output, /pending_confirmation.*no effect/i);
  assert.match(model.stepDetails('confirmation', 'proposal').action, /registered.*context.*not consent/i);
  assert.match(model.stepDetails('confirmation', 'fresh').action, /policy.*facts.*subject.*consume/i);
  assert.equal(model.actorState('confirmation', 'human', 3), 'Not required');
  assert.equal(model.actorState('confirmation', 'user', 3), 'Waiting for decision');
  assert.equal(model.actorState('supervisor', 'user', 3), 'Not required');
  assert.equal(model.stageState('confirmation', 3, 3), 'Waiting for decision');
  assert.ok(model.usedModules('confirmation').includes('confirm'));
  assert.ok(!model.usedModules('confirmation').includes('review'));
  assert.ok(model.permittedEdges('confirmation').includes('checks:confirm'));
  assert.ok(!model.permittedEdges('confirmation').some(edge => edge.includes('review')));
});

test('rejected, expired or changed confirmation never reaches an authorization ACK or write', () => {
  for (const choice of ['confirmed', 'rejected', 'expired', 'changed']) {
    const route = model.scenarioDetails('confirmation', choice).steps;
    const allowed = choice === 'confirmed';
    assert.equal(route.at(-1), allowed ? 'result' : 'deny', choice);
    assert.equal(route.includes('effect'), allowed, choice);
    assert.equal(model.usedModules('confirmation', choice).includes('effect'), allowed, choice);
    assert.equal(model.permittedEdges('confirmation', choice).includes('ack:effect'), allowed, choice);
    assert.equal(route.includes('ack'), allowed, choice);
    assert.equal(model.actorState('confirmation', 'business', route.length - 1, choice),
      allowed ? 'Completed' : 'Not involved in write', choice);
    for (const step of ['intro', ...route]) {
      const clip = model.narrationFor('confirmation', step, choice);
      assert.equal(typeof clip.text, 'string', `${choice}/${step}`);
      assert.doesNotMatch(clip.text, /supervisor|reviewer/i);
    }
  }
});

test('confirmation UI is local, keyboard-accessible and cannot autoplay past the user decision', () => {
  const html = read('docs/production.html');
  const script = read('docs/assets/governed-workflow.js');
  const css = read('docs/assets/governed-workflow.css');
  assert.match(html, /aria-label="Five policy scenarios"/);
  assert.match(html, /id="wf-tab-confirmation".*data-flow-scenario="confirmation"/);
  assert.match(html, /data-flow-node="confirm"/);
  assert.match(html, /data-flow-edge="checks:confirm"/);
  assert.match(html, /data-flow-confirmation hidden/);
  assert.match(html, /data-confirmation-decision="confirmed"/);
  assert.match(html, /data-confirmation-decision="rejected"/);
  assert.match(html, /data-confirmation-decision="expired"/);
  assert.match(html, /data-confirmation-decision="changed"/);
  assert.match(script, /const decisionStep = .*review.*confirm/);
  assert.match(script, /decisionIndex.*index > decisionIndex.*!exampleApproved/);
  assert.match(script, /buttons\.next\.disabled = .*confirm/);
  assert.match(script, /ArrowRight:.*tabs\.length/);
  assert.match(script, /next\.focus\(\)/);
  assert.match(css, /focus-visible/);
  assert.doesNotMatch(script, /\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|window\.open|localStorage|sessionStorage/);
  const panel = html.match(/<div[^>]*data-flow-confirmation[\s\S]*?<!-- user-confirmation:end -->/)?.[0];
  assert.ok(panel);
  assert.doesNotMatch(panel, /<form|<input|https?:\/\/[^"]+(?:confirm|decide)\?/);
  for (const term of ['email is not MFA', 'matching authenticated user', 'exact proposal',
    'GET', 'scanners', 'B2C', 'not all implemented', 'MAF', 'GHCP', 'not live evidence']) {
    assert.ok(panel.includes(term), term);
  }
});

test('engineering guidance separates confirmation, reviewer authority and authentication assurance', () => {
  const guide = read('docs/agent-governance-deep-dive.md');
  for (const term of ['### Five policy scenarios', '### Requesting-user confirmation',
    'confirmation_requirement', 'provider_profile', 'max_age_seconds', '3600', 'user-confirmation',
    'pending_confirmation', 'confirmation_id', 'operation_id', 'Governance.Confirm',
    'MSAL', 'acrs', 'Conditional Access', 'iat', 'five-minute', 'PSD2', 'B2C']) {
    assert.ok(guide.includes(term), term);
  }
  assert.match(guide, /trigger.*always.*policy/);
  assert.match(guide, /opaque\s+reference.*not consent/is);
  assert.match(guide, /workload principal.*not OBO/is);
  assert.match(guide, /both.*different people/is);
  assert.match(guide, /GET.*scanners.*never approve/is);
  assert.match(guide, /active.*applicable.*mapping/is);
  assert.match(guide, /no.*Conditional Access policy/is);
  assert.match(guide, /unknown.*filters.*fail closed/is);
  assert.match(guide, /new.*MFA prompt.*not guaranteed/is);
  assert.match(guide, /one-use.*CAS/is);
  assert.match(guide, /unknown.*operation ID/is);
  assert.match(guide, /GHCP.*deferred.*local native.*unsupported/is);
  assert.match(guide, /developer-guide-conditional-access-authentication-context/);
  assert.match(guide, /concept-session-lifetime/);
});

test('public entry points link the scoped confirmation contract without claiming hosted success', () => {
  for (const file of ['docs/governance.html', 'docs/agent-governance.html',
    'docs/agent-operations.md', 'docs/production-readiness.md', 'docs/production-readiness-pages-spec.md']) {
    const text = read(file);
    assert.match(text, /requesting.user confirmation/i, file);
    assert.match(text, /(?:unverified|not live evidence|not hosted proof)/i, file);
  }
  const spec = read('docs/production-readiness-pages-spec.md');
  assert.match(spec, /five action scenarios/i);
  assert.match(spec, /no automatic confirmation/i);
  assert.match(spec, /preserv.*existing.*clips/is);
});

test('normal requester confirmation uses native Outlook buttons rather than a command-launch email', () => {
  const guide = read('docs/agent-governance-deep-dive.md');
  const page = read('docs/production.html');
  for (const text of [guide, page]) {
    assert.match(text, /Logic Apps.*Outlook/is);
    assert.match(text, /Approve.*Reject/is);
    assert.match(text, /original requesting user/is);
    assert.match(text, /independently.*native.*response/is);
    assert.match(text, /email is not MFA/i);
  }
  assert.doesNotMatch(page, /interactive CLI|transaction.digest typing|launch instructions|web BFF/);
  assert.match(guide, /CLI.*developer\s+diagnostics.*not.*normal user/is);
  assert.match(guide, /policy.*escalation.*without.*reviewer roles/is);
  assert.match(guide, /reviewer roles.*both/is);
  assert.doesNotMatch(page, /A separate page shows the exact proposal/);
  assert.match(model.stepDetails('confirmation', 'confirm').action, /Approve.*Reject.*Outlook/);
  assert.match(model.narrationFor('confirmation', 'confirm').text, /requesting user.*Approve.*Reject.*Outlook/);
  assert.match(model.narrationFor('confirmation', 'confirmation-pending').text, /Outlook/);
});
