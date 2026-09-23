import { test, expect } from '@playwright/test';

const installAudio = async page => page.addInitScript(() => {
  window.Audio = class {
    constructor() {
      const audio = document.createElement('audio');
      audio.calls = [];
      Object.defineProperty(audio, 'paused', { value: true, writable: true });
      Object.defineProperty(audio, 'currentTime', { value: 0, writable: true });
      audio.play = () => {
        audio.calls.push(audio.src);
        audio.paused = false;
        audio.onplaying?.();
        return Promise.resolve();
      };
      audio.pause = () => { audio.paused = true; };
      audio.load = () => { audio.currentTime = 0; };
      window.testNarration = audio;
      return audio;
    }
  };
});

test('voice is ready by default but only Play starts the introduction and then the steps', async ({ page }) => {
  await installAudio(page);
  await page.clock.install();
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await expect(flow.getByRole('button', { name: 'Voice on', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(flow.locator('[data-flow-play-icon]')).toHaveAttribute('data-icon', 'play');
  expect(await page.evaluate(() => window.testNarration.calls)).toEqual([]);
  await page.clock.runFor(10000);
  expect(await page.evaluate(() => window.testNarration.calls)).toEqual([]);
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(flow.locator('[data-flow-play-icon]')).toHaveAttribute('data-icon', 'pause');
  await expect(flow.locator('[data-flow-narration]')).toContainText('customer');
  expect(await page.evaluate(() => window.testNarration.calls[0])).toContain('assets/audio/governance/intro-evidence-valid.mp3');
  await page.clock.runFor(3000);
  await expect(flow).toHaveAttribute('data-step', '0');
  await expect(flow).toHaveAttribute('data-narration-phase', 'intro');
  await expect(flow.locator('[data-flow-traveller]')).toHaveCount(0);
  await page.evaluate(() => window.testNarration.onended());
  await expect(flow).toHaveAttribute('data-step', '0');
  await expect(flow).toHaveAttribute('data-narration-phase', 'step');
  expect(await page.evaluate(() => window.testNarration.calls.at(-1))).toContain('evidence-request.mp3');
  await page.evaluate(() => window.testNarration.onended());
  await expect(flow).toHaveAttribute('data-step', '1');
  expect(await page.evaluate(() => window.testNarration.calls.at(-1))).toContain('evidence-proof-valid.mp3');
  await page.evaluate(() => { window.staleNarrationEnd = window.testNarration.onended; });
  await flow.getByRole('tab', { name: 'Blocked', exact: true }).click();
  await page.evaluate(() => window.staleNarrationEnd());
  await expect(flow).toHaveAttribute('data-step', '0');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  expect(await page.evaluate(() => window.testNarration.paused)).toBe(true);
});

test('captions span the player width and remain readable when muted', async ({ page }) => {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/production.html?caption-width=${width}#production-input-proof`);
    const flow = page.locator('[data-governed-flow]');
    const captions = flow.locator('[data-flow-captions]');
    await expect(captions).toBeVisible();
    const geometry = await captions.evaluate(node => {
      const panel = node.closest('.wf-panel').getBoundingClientRect();
      const box = node.getBoundingClientRect();
      const paragraph = node.querySelector('p');
      return { panelWidth: panel.width, width: box.width, left: box.left - panel.left,
        font: parseFloat(getComputedStyle(paragraph).fontSize), overflow: node.scrollWidth > node.clientWidth + 1 };
    });
    expect(geometry.width).toBeGreaterThanOrEqual(geometry.panelWidth - 3);
    expect(Math.abs(geometry.left)).toBeLessThanOrEqual(2);
    expect(geometry.font).toBeGreaterThanOrEqual(16);
    expect(geometry.overflow).toBe(false);
    await flow.getByRole('button', { name: 'Voice on', exact: true }).click();
    await expect(captions).toBeVisible();
    await expect(captions).toContainText('customer');
  }
});

test('voice stops at human review and cannot approve an action automatically', async ({ page }) => {
  await installAudio(page);
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('tab', { name: 'Human review', exact: true }).click();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  for (let index = 0; index < 5; index++) await page.evaluate(() => window.testNarration.onended());
  await expect(flow).toHaveAttribute('data-node', 'review');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await expect(flow.locator('[data-approval-state]')).toContainText('Not issued');
  await expect(flow.getByRole('button', { name: 'Step 5: Verify response and grant', exact: true })).toBeDisabled();
});

test('reduced motion permits requested narration without moving markers', async ({ page }) => {
  await installAudio(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await expect(flow.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(flow.locator('[data-flow-traveller]')).toHaveCount(0);
  expect(await page.evaluate(() => window.testNarration.calls.length)).toBe(1);
  await flow.getByRole('button', { name: 'Pause', exact: true }).click();
  expect(await page.evaluate(() => window.testNarration.paused)).toBe(true);
  await expect(flow).toHaveAttribute('data-step', '0');
  await flow.getByRole('button', { name: 'Voice on', exact: true }).click();
  await expect(flow.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
});

test('narration failure is visible and never skips ahead', async ({ page }) => {
  await installAudio(page);
  await page.clock.install();
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(28001);
  await expect(flow.locator('[data-flow-audio-note]')).toContainText('Narration unavailable');
  await expect(flow).toHaveAttribute('data-step', '0');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await expect(flow.getByRole('button', { name: 'Voice off', exact: true })).toBeVisible();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(2500);
  await expect(flow).toHaveAttribute('data-step', '1');
});

test('the watchdog distinguishes progressing audio from a stalled clip', async ({ page }) => {
  await installAudio(page);
  await page.clock.install();
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(27000);
  await page.evaluate(() => { window.testNarration.currentTime = 8; });
  await page.clock.runFor(2000);
  await expect(flow).toHaveAttribute('data-playing', 'true');
  await expect(flow).toHaveAttribute('data-narration-phase', 'intro');
  await expect(flow.locator('[data-flow-audio-note]')).toBeEmpty();
  await page.evaluate(() => window.testNarration.onended());
  await expect(flow).toHaveAttribute('data-narration-phase', 'step');
});

test('even progressing audio has a finite per-clip recovery deadline', async ({ page }) => {
  await installAudio(page);
  await page.clock.install();
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  for (let progress = 1; progress <= 3; progress++) {
    await page.clock.runFor(27000);
    await page.evaluate(value => { window.testNarration.currentTime = value; }, progress);
    await page.clock.runFor(1000);
    await expect(flow).toHaveAttribute('data-playing', 'true');
  }
  await page.evaluate(() => { window.testNarration.currentTime = 4; });
  await page.clock.runFor(6001);
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await expect(flow.locator('[data-flow-audio-note]')).toContainText('Narration unavailable');
});

test('the moving marker follows the real arrow and disappears on pause', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Voice on', exact: true }).click();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(flow.locator('[data-flow-traveller]')).toHaveCount(1);
  await expect(flow.locator('[data-flow-traveller]')).toHaveAttribute('data-edge', 'request');
  await flow.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(flow.locator('[data-flow-traveller]')).toHaveCount(0);
});

test('the shipped recording decodes and plays without an external speech service', async ({ page }) => {
  const requests = [];
  page.on('request', request => {
    if (request.resourceType() === 'media') requests.push(request.url());
  });
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(() => page.evaluate(() => {
    const audio = document.querySelector('[data-flow-audio]');
    return audio && !audio.paused && audio.currentTime > 0 && Number.isFinite(audio.duration);
  })).toBe(true);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(url => url.startsWith(new URL('/assets/audio/governance/', page.url()).href))).toBe(true);
  await flow.getByRole('button', { name: 'Pause', exact: true }).click();
});

test('actual clips complete the evidence story without a timing fallback', async ({ page }) => {
  test.setTimeout(150000);
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() => {
    const flow = document.querySelector('[data-governed-flow]');
    return flow.dataset.node === 'result' || flow.querySelector('[data-flow-audio-note]').textContent;
  }, null, { timeout: 120000 });
  await expect(flow.locator('[data-flow-audio-note]')).toBeEmpty();
  await expect(flow).toHaveAttribute('data-node', 'result');
  await expect(flow).toHaveAttribute('data-playing', 'false', { timeout: 28000 });
  await expect(flow.locator('[data-flow-narration]')).toContainText('case has changed');
});

test('leaving the topic cancels voice and stale completions cannot resume it', async ({ page }) => {
  await installAudio(page);
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.evaluate(() => { window.staleNarrationEnd = window.testNarration.onended; });
  await page.locator('[data-topic-tab]').first().click();
  await expect(flow).toHaveAttribute('data-playing', 'false');
  expect(await page.evaluate(() => window.testNarration.paused)).toBe(true);
  await page.evaluate(() => window.staleNarrationEnd());
  await expect(flow).toHaveAttribute('data-step', '0');
});

test('manual navigation skips the prologue and Replay restores it without autoplay', async ({ page }) => {
  await installAudio(page);
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow).toHaveAttribute('data-narration-phase', 'step');
  expect(await page.evaluate(() => window.testNarration.calls)).toEqual([]);
  await flow.locator('[data-flow-progress] button').last().click();
  await expect(flow.locator('[data-flow-play-icon]')).toHaveAttribute('data-icon', 'replay');
  await flow.getByRole('button', { name: 'Replay', exact: true }).click();
  await expect(flow).toHaveAttribute('data-narration-phase', 'intro');
  await expect(flow.locator('[data-flow-play-icon]')).toHaveAttribute('data-icon', 'play');
  await expect(flow.locator('[data-flow-narration]')).toContainText('customer');
  await expect(flow).toHaveAttribute('data-playing', 'false');
});
