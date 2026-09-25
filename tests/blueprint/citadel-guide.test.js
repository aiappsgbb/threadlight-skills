const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const guide = () => read('docs/citadel-governance.md');
const plain = () => guide().replace(/\s+/g, ' ');
const diagram = name => guide().match(
  new RegExp(`<!-- diagram: ${name} -->\\s*\`\`\`mermaid\\n([\\s\\S]*?)\\n\`\`\``),
)?.[1];

test('Citadel reading path explains artifacts, action flow and policy loading before reference details', () => {
  const text = guide();
  const headings = ['## What exists today', '## What you build and run',
    '## ADR: position of authority and ownership', '### One request, step by step',
    '## Policy lifecycle', '## Contracts and canonical joins',
    '## Identity, endpoints and protocols', '## Build, activate and integrate',
    '## Update, rollback and the external gate'];
  let previous = -1;
  for (const heading of headings) {
    const position = text.indexOf(heading);
    assert.ok(position > previous, `${heading} follows the explanatory path`);
    previous = position;
  }
  const intro = text.split('## What you build and run')[0];
  assert.match(intro, /working draft/i);
  assert.match(intro, /September 25[\s\S]*exact binding/);
  assert.match(intro, /not.*(?:certify|certification)/);
  assert.match(plain(), /Policy Decision Point \(PDP\)[\s\S]*Policy Enforcement Point \(PEP\)/);
  assert.doesNotMatch(text, /```text/);
  const visible = text.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, '');
  assert.ok(visible.split(/\s+/).length <= 2400, 'Main reading path stays short; wire detail is optional');
});

test('Citadel artifact map separates a buildable image from supporting services and release gates', () => {
  const section = guide().split('## What you build and run')[1]?.split('\n## ')[0] || '';
  for (const term of ['Dockerfile', 'build.py', 'citadel_package.py', 'APIM',
    'control plane', 'Cosmos', 'Blob', 'Key Vault', 'identities', 'not self-contained',
    'private ACR', 'public signed release']) assert.ok(section.includes(term), term);
  for (const target of [
    'skills/threadlight-govern/references/gateway/Dockerfile',
    'examples/citadel-governance/README.md',
    'examples/citadel-governance/build.py',
    'skills/threadlight-govern/references/gateway/README.md',
  ]) assert.ok(section.includes(`https://github.com/aiappsgbb/threadlight-skills/blob/main/${target}`), target);
});

test('Citadel main diagram keeps dependencies before the sole producer effect path', () => {
  const source = diagram('citadel-action-path');
  assert.ok(source, 'Named editable action diagram');
  for (const term of ['Consumer', 'APIM', 'ACA gateway', 'PEP', 'Producer ACS / Rego',
    'Consumer ACS / Rego', 'PDP', 'Control plane', 'audit ACK',
    'Human authority', 'if required', 'HTTP producer', 'Independent authorization']) {
    assert.ok(source.includes(term), term);
  }
  assert.match(source, /Consumer --> APIM --> Gateway/);
  assert.match(source, /Independent PDPs<br\/>Producer ACS \/ Rego<br\/>Consumer ACS \/ Rego/);
  assert.match(source, /Gateway --> Policies --> Gate/);
  assert.match(source, /Control -\.-> Gate/);
  assert.match(source, /Human -\.-> Control/);
  assert.match(source, /Gate --> Producer/);
  assert.equal((source.match(/-->\s*Producer\b/g) || []).length, 1, 'One effect path, no authority bypass');
  assert.doesNotMatch(source, /(?:Control|Human|APIM) --> Producer\b/);
  const flow = plain().split('### One request, step by step')[1]?.split('## Policy lifecycle')[0] || '';
  for (const term of ['Deny', 'same', 'pending_confirmation', 'pending_approval',
    'original operation', 'no HTTP', 'audit ACK', 'recheck', 'decision/audit', 'not settlement']) {
    assert.ok(flow.includes(term), term);
  }
});

test('Citadel policy lifecycle explains three verified envelopes and separate publisher permissions', () => {
  const source = diagram('citadel-policy-lifecycle');
  assert.ok(source, 'Named editable policy diagram');
  for (const term of ['Owner files', 'Two policy bundles', 'effective binding', 'Authorized publisher',
    'Blob / Key Vault', 'Read-only loading', 'Verify all three', 'Activate together']) {
    assert.ok(source.includes(term), term);
  }
  const text = plain();
  for (const term of ['earliest', 'atomic activation', 'same pinned signing key authority',
    'No hot refresh', 'App Configuration', 'optional', 'not an implemented publisher',
    'sign', 'verify', 'last-known-good', 'fresh complete generation']) {
    assert.ok(text.includes(term), term);
  }
});

test('Citadel diagrams work as static Pages images with collapsed editable sources', () => {
  const text = guide();
  const names = ['citadel-action-path', 'citadel-policy-lifecycle'];
  assert.deepEqual([...text.matchAll(/<!-- diagram: ([a-z0-9-]+) -->/g)].map(m => m[1]), names);
  for (const name of names) {
    assert.match(text, new RegExp(`!\\[[^\\]]{30,}\\]\\(assets/governance/${name}\\.svg\\)`));
    assert.match(text, new RegExp(`<details markdown="1">[\\s\\S]*?<summary>[^<]*Mermaid[^<]*</summary>[\\s\\S]*?<!-- diagram: ${name} -->[\\s\\S]*?</details>`));
    const svg = read(`docs/assets/governance/${name}.svg`);
    assert.match(svg, /<svg/);
    assert.doesNotMatch(svg, /<script|<foreignObject|Syntax error|mermaid-error/i);
  }
  assert.match(read('scripts/render-governance-diagrams.mjs'), /'citadel-governance'/);
  assert.equal((text.match(/<details markdown="1">/g) || []).length, 5,
    'Jekyll must parse Markdown in every disclosure, including optional contracts');
  assert.doesNotMatch(text, /\]\(\.\.\/(?:examples|skills|scripts)\//);
});

test('Citadel clarity preserves limits, historical proof and independent owner authority', () => {
  const text = plain();
  for (const term of ['one tenant', 'one producer', 'one consumer', 'one registered',
    'Deny overrides', 'AND obligations', 'Two independent reviews', 'unsupported',
    'Entra v2 app-only', 'Governance.Workload', 'GUID audience', 'not OBO',
    'not consent', 'not MFA', '2025-06-18', 'no arbitrary', 'CAS', 'idempotency',
    'reconciliation', 'not promote', 'cryptography', 'HIGH', 'scan', 'signing',
    'd8f97cc', 'd45806c', 'c538f4c', 'not model-driven']) {
    assert.ok(text.includes(term), term);
  }
  assert.match(text, /governed-returns-validation\.md#s7-/);
  assert.match(text, /expired leases cannot be extended/);
});

test('the Citadel guide opens S7 in the immutable GitHub document reader, not a raw Markdown route', () => {
  const target = guide().match(/\[S7 execution record\]\(([^)]+)\)/)?.[1];
  assert.equal(target, 'https://github.com/aiappsgbb/threadlight-skills/blob/' +
    'c538f4cf89c9c5e5f5ae0c43e3bbdae96c34778e/docs/governed-returns-validation.md' +
    '#s7-citadel-public-route-and-isolated-producer-ack-loss');
  assert.ok(read('docs/governance.html').includes(`href="${target}"`), 'Both readers use the same immutable record');
});
