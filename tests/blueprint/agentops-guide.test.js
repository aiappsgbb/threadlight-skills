const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../..');

test('AgentOps deep dive follows the verified-release implementation', () => {
  const guide = fs.readFileSync(path.join(root, 'docs/agentops-deep-dive.md'), 'utf8');
  for (const phrase of ['## Contents', 'preproduction', 'partial',
    'before go-live', 'does not roll back', 'agentops.yaml', 'central-platform-boundary.md',
    'No cloud execution']) assert.ok(guide.includes(phrase), phrase);
  const template = fs.readFileSync(path.join(root,
    'skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl'), 'utf8');
  for (const line of ['needs: validation', 'release_runner.py', '--receipt-sha256']) {
    assert.ok(template.includes(line), line);
    assert.ok(guide.includes(line), line);
  }
  for (const phrase of ['immutable image', 'after identity waits', 'THREADLIGHT_AGENTOPS_CONTEXT',
    'application-owned adapters', 'not cloud acceptance']) assert.ok(guide.includes(phrase), phrase);
  assert.doesNotMatch(guide, /soft is the default|Accepts `comprehensive` or `partial`|producer placeholders/);
  assert.doesNotMatch(guide, /all gates green.*ships|automatic production approval/i);
  for (const [, link] of guide.matchAll(/\]\(([^)]+)\)/g)) {
    if (/^(https?:|#)/.test(link)) continue;
    assert.ok(fs.existsSync(path.resolve(root, 'docs', link.split('#')[0])), link);
  }
});

test('release documentation separates orientation, repository explanation and operator contracts', () => {
  for (const file of ['README.md', 'THREADLIGHT.md']) {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    for (const reference of ['docs/agentops-deep-dive.md', 'references/release-contract.md']) {
      assert.ok(text.includes(reference), `${file}: ${reference}`);
    }
    assert.match(text, /not.*cloud.*acceptance/i, file);
  }
  const guide = fs.readFileSync(path.join(root, 'docs/agentops-deep-dive.md'), 'utf8');
  for (const phrase of ['## Start here', '**Candidate**', '**Promotion**', '**Receipt**',
    '**Release approval**', '## Who supplies what', '## What has been verified',
    'catalog CI', 'customer pipeline', 'not a signed external attestation']) {
    assert.ok(guide.includes(phrase), phrase);
  }
  const contract = fs.readFileSync(path.join(root,
    'skills/threadlight-cicd/references/release-contract.md'), 'utf8');
  for (const phrase of ['## Operator sequence', '## Stop and recover', 'preflight',
    'max_age_seconds', 'outcome unknown', 'complete validation']) {
    assert.ok(contract.includes(phrase), phrase);
  }
});
