const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const previewRevision = '4f59f7584a5f5d614c3625aa45f92f6a692c2194';

test('chapter redesign preserves shared assets byte for byte', () => {
  const frozen = {
    'docs/assets/site.css': '3eb89a7689c1609d00df5193485fd5d32ca69f65a9d12d230a222afd8a0b426b',
    'docs/assets/site.js': 'e4d413b740d2794931d5ad1107649d864c4dd64440b0ea6f8815d5540b0ac3db',
    'docs/assets/demo-reel.js': 'ff32dd6e1b19dc4baf266c0d223bcf8eee5801f1c3065a75ddab07224abbeb6e',
  };
  for (const [file, digest] of Object.entries(frozen)) {
    assert.equal(createHash('sha256').update(read(file)).digest('hex'), digest, file);
  }
});

test('Home leads with the demo rather than the relocated governance reference', () => {
  const html = read('docs/index.html');
  assert.match(html, /<main>\s*(?:<!--[\s\S]*?-->\s*)*<section class="scene demo-intro home-intro"/);
  assert.doesNotMatch(html, /aria-label="Runtime governance evidence boundary"/);
  for (const file of ['docs/governance.html', 'docs/agent-operations.md']) {
    for (const term of ['SAFE', 'ACS', 'Agent Hooks', 'ASSERT', 'governance_probe_noop']) {
      assert.ok(read(file).includes(term), `${file}: relocated boundary must retain ${term}`);
    }
  }
});

test('advanced chapter separates enforcement, evidence and opt-in preview', () => {
  const html = read('docs/governance.html');
  for (const id of ['chapter-top', 'before-action', 'evidence', 'agentops', 'read-deeper']) {
    assert.ok(html.includes(`id="${id}"`), `missing chapter ${id}`);
  }
  assert.match(html, /per selected binding/i);
  assert.match(html, /Illustration only/i);
  assert.match(html, /not whole-agent governance/i);
  assert.match(html, /governance_probe_noop/);
  assert.match(html, /not payment settlement/i);
  assert.match(html, /local process provenance, not Azure attestation/i);
  assert.match(html, /not-applicable/);
  assert.match(html, /partial/);
  assert.match(html, /quality failure/i);
  assert.match(html, /data-release="preview"/);
  assert.match(html, /Preview[^<]*PR #128 merged/);
  assert.doesNotMatch(html, /not added to the released skill inventory|proposed.*AgentOps integration/);
  assert.match(html, /https:\/\/github.com\/aiappsgbb\/threadlight-skills\/pull\/128/);
  assert.ok(html.includes(`/blob/${previewRevision}/skills/threadlight-agentops/SKILL.md`));
  assert.doesNotMatch(html, /(?:blob|tree)\/main\/skills\/threadlight-agentops/);
  assert.doesNotMatch(html, /24 skills|fully governed|guaranteed green|internal use|confidential/i);
});

test('advanced chapter metadata is consistent and its page-local CSS is cache-busted', () => {
  const html = read('docs/governance.html');
  const title = html.match(/<title>([^<]+)<\/title>/)[1];
  const description = html.match(/<meta name="description" content="([^"]+)"/)[1];
  for (const tag of ['og:title', 'twitter:title']) {
    assert.ok(html.includes(`content="${title}"`), tag);
    assert.match(html, new RegExp(`(?:property|name)="${tag}" content="${title}"`));
  }
  for (const tag of ['og:description', 'twitter:description']) {
    assert.ok(html.includes(`${tag}" content="${description}"`), tag);
  }
  assert.ok(html.includes('rel="canonical" href="https://aiappsgbb.github.io/threadlight-skills/governance.html"'));
  const digest = createHash('sha256').update(read('docs/assets/governance.css')).digest('hex').slice(0, 8);
  assert.ok(html.includes(`assets/governance.css?v=${digest}`));
  const scriptDigest = createHash('sha256').update(read('docs/assets/governance.js')).digest('hex').slice(0, 8);
  assert.ok(html.includes(`assets/governance.js?v=${scriptDigest}`));
});

test('advanced story has contextual entry points and a matching technical guide', () => {
  for (const file of ['docs/funnel.html', 'docs/production.html', 'docs/self-improving.html']) {
    const main = read(file).match(/<main[\s\S]*?<\/main>/)[0];
    assert.match(main, /href="\.\/governance\.html(?:#[^"]+)?"/, file);
  }
  for (const file of ['README.md', 'THREADLIGHT.md', 'docs/production-readiness.md']) {
    assert.match(read(file), /agent-operations\.md/, file);
  }
  const guide = read('docs/agent-operations.md');
  for (const term of ['LOCAL-14', 'governance-manifest.json', 'agentops.yaml', 'AOPS-001',
    'not-applicable', 'partial', 'local process provenance', previewRevision]) {
    assert.ok(guide.includes(term), term);
  }
  for (const match of guide.matchAll(/\]\(([^)#]+)(?:#[^)]*)?\)/g)) {
    if (/^https:/.test(match[1])) continue;
    assert.ok(fs.existsSync(path.resolve(root, 'docs', match[1])), `missing guide target: ${match[1]}`);
  }
});
