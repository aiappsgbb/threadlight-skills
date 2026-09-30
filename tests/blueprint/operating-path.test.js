const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const text = html => html.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&[a-z]+;/g, ' ').replace(/\s+/g, ' ');
const stages = [
  ['build', 'Build', './workbook.html#stage-design'],
  ['assure', 'Assure', './workbook.html#stage-production'],
  ['release', 'Governed release', './agent-governance.html#overview'],
  ['operate', 'Operate', './operate.html#overview'],
];
const journey = html => html.match(/<nav class="ops-journey"[^>]*data-ops-journey[^>]*>([\s\S]*?)<\/nav>/)?.[1];

test('the three hands-on guides share one four-stage operating path with the current stage marked', () => {
  for (const [file, current] of [
    ['docs/workbook.html', 'build'], ['docs/agent-governance.html', 'release'], ['docs/operate.html', 'operate'],
  ]) {
    const nav = journey(read(file));
    assert.ok(nav, `${file}: journey strip`);
    const items = [...nav.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)];
    assert.deepEqual(items.map(([, attrs]) => attrs.match(/data-ops-stage="([^"]+)"/)?.[1]), stages.map(([id]) => id), file);
    assert.deepEqual(items.map(([, attrs]) => attrs.match(/href="([^"]+)"/)?.[1]), stages.map(([, , href]) => href), file);
    items.forEach(([, attrs, body], index) => {
      assert.ok(text(body).includes(stages[index][1]), `${file}: ${stages[index][1]}`);
      assert.equal(/aria-current="step"/.test(attrs), stages[index][0] === current, `${file}: ${stages[index][0]}`);
    });
  }
});

test('the workbook map names page, skills, approver and cost boundary for each stage', () => {
  const html = read('docs/workbook.html');
  const section = html.match(/<section[^>]*id="operating-path"[\s\S]*?<\/section>/)?.[0];
  assert.ok(section, 'operating-path section');
  const rows = [...section.matchAll(/<article class="ops-stage"[^>]*data-ops-row="([^"]+)"[^>]*>([\s\S]*?)<\/article>/g)];
  assert.deepEqual(rows.map(([, id]) => id), stages.map(([id]) => id));
  for (const [, id, body] of rows) {
    for (const field of ['Open', 'Skills', 'Who approves', 'Cost and cloud']) {
      assert.ok(text(body).includes(field), `${id}: ${field}`);
    }
    for (const [skill] of body.matchAll(/threadlight-[a-z]+(?:-[a-z]+)*/g)) {
      assert.ok(fs.existsSync(path.join(root, 'skills', skill, 'SKILL.md')), skill);
    }
  }
  assert.ok(html.indexOf('id="operating-path"') < html.indexOf('id="recovery"'));
  assert.match(html, /<a class="wb-link"[^>]*href="\.\/agent-governance\.html#overview"/);
  assert.match(html, /<a class="wb-link"[^>]*href="\.\/operate\.html#overview"/);
});

test('the workbook separates the current production path from the historical case-study road', () => {
  const html = read('docs/workbook.html');
  const fork = html.match(/<div class="ops-fork"[^>]*data-production-fork[^>]*>([\s\S]*?)<\/div>\s*<\/div>/)?.[1];
  assert.ok(fork, 'production fork');
  assert.match(fork, /href="\.\/agent-governance\.html#overview"/);
  assert.match(text(fork), /current/i);
  assert.match(text(fork), /historical/i);
  assert.ok(html.indexOf('data-production-fork') < html.indexOf('class="wb-legs handoff'), 'fork precedes the historical legs');
  assert.match(html, /Historical reference &middot; the case-study road/);
});

test('workbook recovery cards all stay inside the troubleshooting disclosure', () => {
  const recovery = read('docs/workbook.html').match(/<section[^>]*id="recovery"[\s\S]*?<\/section>/)[0];
  const details = recovery.match(/<details class="wb-leg">[\s\S]*?<\/details>/)[0];
  assert.equal([...details.matchAll(/class="wb-rcard"/g)].length, 6);
  assert.equal([...recovery.matchAll(/class="wb-rcard"/g)].length, 6);
});

test('operate is a five-routine day-2 guide with prompts, expected results and checks', () => {
  const html = read('docs/operate.html');
  assert.match(html, /<title>[^<]*Operate/);
  assert.match(html, /aria-current="page">Operate</);
  assert.match(html, /data-pilot-guide/);
  const panels = [...html.matchAll(/<section[^>]*data-guide-step="([^"]+)"[^>]*>([\s\S]*?)<\/section>/g)];
  assert.deepEqual(panels.map(([, id]) => id), ['after-deploy', 'before-change', 'weekly', 'incident', 'learn']);
  for (const [, id, body] of panels) {
    assert.match(body, /class="pg-badge"/, id);
    assert.match(body, /<pre data-guide-prompt tabindex="0">/, id);
    assert.match(body, /Expected result/, id);
    const checks = body.match(/<ul class="pg-verify">([\s\S]*?)<\/ul>/)?.[1];
    assert.ok(checks && [...checks.matchAll(/<li>/g)].length >= 2, id);
    const skills = [...body.match(/<div class="pg-skills"[\s\S]*?<\/div>/)[0].matchAll(/>(threadlight-[a-z-]+)<\/a>/g)];
    assert.ok(skills.length >= 1, id);
    for (const [, skill] of skills) assert.ok(fs.existsSync(path.join(root, 'skills', skill, 'SKILL.md')), skill);
    const prompt = body.match(/<pre[^>]*>([\s\S]*?)<\/pre>/)[1];
    for (const [skill] of prompt.matchAll(/threadlight-[a-z]+(?:-[a-z]+)*/g)) {
      assert.ok(fs.existsSync(path.join(root, 'skills', skill, 'SKILL.md')), skill);
    }
    assert.doesNotMatch(prompt, /automatically (?:deploy|promote|delete|roll ?back)/i, id);
  }
  const body = text(html);
  for (const phrase of [/guidance, not cloud execution/i, /fresh after-deployment evidence/i, /unknown outcome/i,
    /keep (?:routing|traffic) closed/i, /same immutable image/i, /governance_probe_noop/, /regression case/i,
    /owner/i, /No automatic rollback/i]) {
    assert.match(body, phrase);
  }
  for (const script of ['assets/site-map.js', 'assets/chapter-experience.js', 'assets/pilot-to-production.js']) {
    assert.ok(html.includes(script), script);
  }
});

test('the governed-release guide explains its vocabulary and hands off to Operate', () => {
  const html = read('docs/agent-governance.html');
  const glossary = html.match(/<section[^>]*id="glossary"[\s\S]*?<\/section>/)?.[0];
  assert.ok(glossary, 'glossary');
  const terms = [...glossary.matchAll(/<dt>([\s\S]*?)<\/dt>\s*<dd>([\s\S]*?)<\/dd>/g)];
  assert.ok(terms.length >= 8);
  for (const term of ['Binding', 'Unbound read', 'Governed gateway', 'Trusted facts', 'Release candidate',
    'Immutable image', 'Fresh evidence', 'governance_probe_noop']) {
    assert.ok(terms.some(([, dt, dd]) => text(dt).includes(term) && text(dd).trim().length > 20), term);
  }
  const cue = html.match(/<div class="cx-journey"[^>]*>([\s\S]*?)<\/div>/)[1];
  assert.match(cue, /<a class="cx-journey-next" href="\.\/operate\.html">/);
  assert.match(html, /href="\.\/operate\.html#overview"/);
});
