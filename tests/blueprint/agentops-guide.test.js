const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../..');

test('AgentOps deep dive follows executable templates rather than their optimistic comments', () => {
  const guide = fs.readFileSync(path.join(root, 'docs/agentops-deep-dive.md'), 'utf8');
  for (const phrase of ['## Contents', 'after deployment', 'echo', 'partial', 'soft',
    'before go-live', 'does not roll back', 'agentops.yaml', 'central-platform-boundary.md',
    'No cloud execution']) assert.ok(guide.includes(phrase), phrase);
  const template = fs.readFileSync(path.join(root,
    'skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl'), 'utf8');
  for (const line of ['needs: deploy',
    'sys.exit(0 if verdict in ("comprehensive", "partial") else 1)',
    'sys.exit(0 if verdict in ("hardened", "partial") else 1)']) {
    assert.ok(template.includes(line), line);
    assert.ok(guide.includes(line), line);
  }
  assert.doesNotMatch(guide, /all gates green.*ships|automatic production approval/i);
  for (const [, link] of guide.matchAll(/\]\(([^)]+)\)/g)) {
    if (/^(https?:|#)/.test(link)) continue;
    assert.ok(fs.existsSync(path.resolve(root, 'docs', link.split('#')[0])), link);
  }
});
