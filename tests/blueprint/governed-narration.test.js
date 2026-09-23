const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const model = require('../../docs/assets/governed-workflow.js');
const audioRoot = path.join(root, 'docs/assets/audio/governance');

test('every policy path introduces the case, then explains its steps without a technical monologue', () => {
  assert.equal(typeof model.narrationFor, 'function');
  for (const scenario of ['normal', 'invalid', 'supervisor', 'evidence']) {
    for (const choice of scenario === 'evidence' ? ['valid', 'missing', 'changed'] : ['valid']) {
      const introduction = model.narrationFor(scenario, 'intro', choice);
      assert.equal(typeof introduction.text, 'string');
      assert.match(introduction.text, /customer.*return.*order/i);
      assert.match(introduction.text, /real operation.*case.*audit.*not.*payment/i);
      assert.ok(introduction.text.split(/\s+/).length >= 30 && introduction.text.split(/\s+/).length <= 45);
      const clips = [introduction, ...model.scenarioDetails(scenario, choice).steps.map(id => model.narrationFor(scenario, id, choice))];
      const text = clips.map(clip => clip.text).join(' ');
      assert.ok(text.split(/\s+/).length <= 185, `${scenario}/${choice}: bounded explanation`);
      assert.doesNotMatch(text, /\bJWT\b|HTTPS|_meta|returns_apply_decision/);
      for (const clip of clips) {
        assert.ok(clip.text.length > 0 && clip.text.length <= (clip.id.startsWith('intro-') ? 320 : 170));
        assert.match(clip.id, /^[a-z][a-z-]+$/);
      }
    }
  }
  assert.match(model.narrationFor('supervisor', 'review').text, /pause.*person/i);
  assert.match(model.narrationFor('supervisor', 'verify').text, /once.*deadline/i);
  assert.match(model.narrationFor('evidence', 'proof', 'missing').text, /does not corroborate.*refuses/);
  assert.match(model.narrationFor('evidence', 'proof', 'valid').text, /expir/i);
  assert.match(model.narrationFor('evidence', 'result', 'valid').text, /case has changed.*not just.*approved/);
});

test('prerecorded narration ships locally with matching scripts and bounded duration', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(audioRoot, 'manifest.json'), 'utf8'));
  assert.equal(manifest.language, 'en-US');
  assert.equal(manifest.engine, 'edge-tts');
  assert.equal(manifest.voice, 'en-US-AvaMultilingualNeural');
  assert.equal(manifest.rate, '+0%');
  for (const [key, value] of Object.entries(model.narrationProfile)) assert.equal(manifest[key], value);
  assert.deepEqual(manifest.clips.map(clip => clip.id).sort(), Object.keys(model.narrationClips).sort());
  for (const clip of manifest.clips) {
    assert.equal(clip.text, model.narrationClips[clip.id]);
    const bytes = fs.readFileSync(path.join(audioRoot, `${clip.id}.mp3`));
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), clip.sha256);
    assert.ok(clip.seconds > 0 && clip.seconds < 20, clip.id);
    assert.ok(bytes.length > 1000 && bytes.length < 160000, clip.id);
  }
  for (const scenario of ['normal', 'invalid', 'supervisor', 'evidence']) {
    for (const choice of scenario === 'evidence' ? ['valid', 'missing', 'changed'] : ['valid']) {
      const seconds = ['intro', ...model.scenarioDetails(scenario, choice).steps].reduce((total, id) => {
        const clip = model.narrationFor(scenario, id, choice);
        return total + manifest.clips.find(item => item.id === clip.id).seconds;
      }, 0);
      assert.ok(seconds <= 80, `${scenario}/${choice}: ${seconds}s`);
    }
  }
});

test('the presentation contract identifies the home voice and distinguishes rendering from playback', () => {
  const text = fs.readFileSync(path.join(root, 'docs/production-readiness-pages-spec.md'), 'utf8');
  assert.match(text, /render-governance-narration\.mjs --check/);
  assert.match(text, /no automatic approval/i);
  assert.match(text, /prerecorded\s+English/i);
  assert.match(text, /en-US-AvaMultilingualNeural/);
  assert.match(text, /online.*(?:TTS|speech)/i);
  assert.match(text, /browser.*(?:MP3|prerecorded)/i);
});
