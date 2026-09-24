#!/usr/bin/env node
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, writeFile, copyFile, unlink, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { narrationClips, narrationProfile } = require('../docs/assets/governed-workflow.js');
const directory = path.join(root, 'docs/assets/audio/governance');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
if (process.argv.includes('--help')) {
  console.log('Render short narration with the home-page Ava Multilingual voice via online Edge TTS.\n'
    + 'Usage: node scripts/render-governance-narration.mjs [--check | --missing]\n'
    + 'Rendering requires python3 with edge-tts==7.2.8 and ffprobe. Only public narration text is sent.\n'
    + '--check verifies voice settings, scripts and shipped MP3 hashes offline.\n'
    + '--missing renders new clips only; preserve existing verified narration clips.');
} else if (process.argv.includes('--check')) {
  const manifest = JSON.parse(await readFile(path.join(directory, 'manifest.json'), 'utf8'));
  if (Object.entries(narrationProfile).some(([key, value]) => manifest[key] !== value)) {
    throw new Error('Narration voice differs from the home-page profile');
  }
  if (manifest.clips.length !== Object.keys(narrationClips).length
      || new Set(manifest.clips.map(clip => clip.id)).size !== manifest.clips.length) {
    throw new Error('Narration manifest does not match the clip inventory');
  }
  for (const clip of manifest.clips) {
    if (!Object.hasOwn(narrationClips, clip.id) || clip.text !== narrationClips[clip.id]
        || hash(await readFile(path.join(directory, `${clip.id}.mp3`))) !== clip.sha256) {
      throw new Error(`Stale narration: ${clip.id}`);
    }
  }
  console.log(`Checked ${manifest.clips.length} local narration clips.`);
} else {
  const previous = process.argv.includes('--missing')
    ? JSON.parse(await readFile(path.join(directory, 'manifest.json'), 'utf8')) : null;
  if (previous && (Object.entries(narrationProfile).some(([key, value]) => previous[key] !== value)
      || new Set(previous.clips.map(clip => clip.id)).size !== previous.clips.length)) {
    throw new Error('Existing narration profile or inventory is invalid; cannot preserve clips.');
  }
  const preserved = new Map();
  for (const clip of previous?.clips || []) {
    if (!Object.hasOwn(narrationClips, clip.id) || clip.text !== narrationClips[clip.id]
        || hash(await readFile(path.join(directory, `${clip.id}.mp3`))) !== clip.sha256) {
      throw new Error(`Existing narration differs: ${clip.id}; explicit full regeneration required.`);
    }
    preserved.set(clip.id, clip);
  }
  const version = execFileSync('python3', ['-m', 'edge_tts', '--version'], { encoding: 'utf8', timeout: 10000 }).trim();
  if (version !== 'edge-tts 7.2.8') throw new Error('Rendering requires edge-tts==7.2.8 in the active Python environment.');
  await mkdir(directory, { recursive: true });
  const scratch = await mkdtemp(path.join(tmpdir(), 'threadlight-narration-'));
  const manifest = {
    ...narrationProfile, renderer_version: version,
    home_voice_commit: '586c5193d06164b6563f27bb23130b18d7ebb05a',
    home_rate_commit: 'bc167f27f2af57d0ef6eed4aeb2ddefa7e2c0e83',
    clips: [],
  };
  const staged = [];
  try {
    for (const [id, text] of Object.entries(narrationClips)) {
      if (!/^[a-z][a-z-]+$/.test(id)) throw new Error('Invalid narration clip identifier');
      if (preserved.has(id)) {
        manifest.clips.push(preserved.get(id));
        continue;
      }
      const output = path.join(scratch, `${id}.mp3`);
      staged.push(output);
      execFileSync('python3', ['-m', 'edge_tts', '--voice', manifest.voice,
        '--rate', manifest.rate, '--text', text, '--write-media', output], { timeout: 45000 });
      const seconds = Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', output], { encoding: 'utf8', timeout: 10000 }));
      if (!Number.isFinite(seconds) || seconds <= 0 || seconds >= 20) throw new Error(`Unbounded narration: ${id}`);
      manifest.clips.push({ id, text, seconds, sha256: hash(await readFile(output)) });
    }
    for (const clip of manifest.clips) {
      if (preserved.has(clip.id)) continue;
      await copyFile(path.join(scratch, `${clip.id}.mp3`), path.join(directory, `${clip.id}.mp3`));
    }
    await writeFile(path.join(directory, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
    console.log(`Rendered ${staged.length} clips; preserved ${preserved.size}. Voice ${manifest.voice}, rate ${manifest.rate}.`);
  } finally {
    for (const file of staged) await unlink(file).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await rmdir(scratch);
  }
}
