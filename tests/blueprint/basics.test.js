const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');

test('Basics leads with skill-based agents and two libraries rather than a general AI introduction', () => {
  const html = read('docs/basics.html');
  assert.match(html, /<section[^>]*id="building-blocks"/);
  for (const term of ['model', 'agent', 'tool', 'skill']) {
    assert.ok(html.includes(`data-basic-term="${term}"`), `define ${term}`);
  }
  const opening = html.match(/<section\b[^>]*\sid="chapter-top"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.ok(opening, 'the introductory section must exist');
  assert.match(opening, /engineering skills/i);
  assert.match(opening, /business skills/i);
  assert.match(opening, /data-actor="builder"/);
  assert.match(opening, /data-actor="process"/);
  assert.match(html, /skill-based agent[\s\S]*?library of focused procedures/i);
  assert.match(html, /prompt.*one request/i);
  assert.match(html, /orchestration.*coordinat/i);
  assert.doesNotMatch(html, /The concepts are clear/);
});

test('Basics introduces skills, two agents, host limits and a next step', () => {
  const html = read('docs/basics.html');
  for (const id of ['chapter-top', 'skills', 'two-agents', 'where-skills-run', 'next-step']) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  for (const actor of ['builder', 'process']) assert.ok(html.includes(`data-actor="${actor}"`));
  for (const term of ['SKILL.md', 'specs/SPEC.md', 'src/agent/skills/', 'registered tools',
    'planner', 'worker', 'host', 'workflow', 'not a deployment']) assert.ok(html.includes(term), term);
  assert.match(html, /not the same skill library/i);
  assert.doesNotMatch(html, /every host|automatically production.ready|guaranteed green|always skill.based|Agentic Loop/i);
});

test('real skill examples share a contract but produce different kinds of result', () => {
  const html = read('docs/basics.html');
  for (const name of ['threadlight-design', 'policy-eligibility']) {
    const example = html.match(new RegExp(`<article[^>]*data-skill-example="${name}"[^>]*>([\\s\\S]*?)</article>`))?.[1];
    assert.ok(example, `missing real example ${name}`);
    for (const field of ['When', 'Input', 'Procedure', 'Output']) assert.match(example, new RegExp(`<dt>${field}</dt>`));
    assert.match(example, /SKILL\.md/);
  }
  assert.match(html, /same format[\s\S]{0,100}different procedures and outputs/i);
  assert.match(html, /not the final decision or a refund/i);
  assert.match(read('examples/returns-triage-governed/src/agent/skills/policy-eligibility/SKILL.md'), /approve_candidate \| deny_candidate/);
  assert.match(read('skills/threadlight-design/SKILL.md'), /### Step 4: Checkpoint/);
});

test('Basics metadata and portable downloads have real targets', () => {
  const html = read('docs/basics.html');
  const title = html.match(/<title>([^<]+)<\/title>/)[1];
  const description = html.match(/<meta name="description" content="([^"]+)"/)[1];
  for (const key of ['og:title', 'twitter:title']) assert.ok(html.includes(`${key}" content="${title}"`));
  for (const key of ['og:description', 'twitter:description']) assert.ok(html.includes(`${key}" content="${description}"`));
  const image = html.match(/property="og:image" content="([^"]+)"/)[1];
  assert.ok(fs.existsSync(path.join(root, 'docs/assets/og', path.basename(image))), 'social image must exist');
  for (const name of ['threadlight-design', 'threadlight-qualify']) {
    assert.ok(html.includes(`./downloads/${name}.zip`));
    assert.ok(fs.existsSync(path.join(root, `docs/downloads/${name}.zip`)));
  }
  for (const target of ['funnel.html', 'case-study.html', 'production.html', 'governance.html']) {
    assert.ok(html.includes(`./${target}`));
  }
});

test('the two runtime skill-loading explanations match shipped adapters', () => {
  const html = read('docs/basics.html');
  assert.match(html, /skill_directories/);
  assert.match(html, /SkillsProvider\.from_paths/);
  assert.match(read('skills/threadlight-deploy/references/governance/ghcp-container.py'), /skill_directories=\[str\(base \/ "skills"\)\]/);
  assert.match(read('skills/threadlight-deploy/references/governance/maf-container.py'), /SkillsProvider\.from_paths\(skills\)/);
  const policy = JSON.parse(read('skills/threadlight-design/references/runtime-policy.json'));
  assert.ok(policy.compatible_combinations.some(route => route.runtime_shape === 'workflow'));
  assert.match(html, /runtime route/i);
  assert.match(read('scripts/build-cowork-zips.sh'), /build_qualify_zip\(\)/);
  assert.match(read('skills/threadlight-auto/references/orchestrator.py'), /def execute\(workspace: Path, worker,/);
});
