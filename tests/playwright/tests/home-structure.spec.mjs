import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('Home puts the unchanged demo before optional explanations', async ({ page }) => {
  await page.goto('/index.html');
  await expect(page.locator('.home-context')).not.toHaveAttribute('open', '');
  await expect(page.locator('.home-journey a')).toHaveCount(3);
  const order = await page.evaluate(() =>
    Boolean(document.querySelector('#reel').compareDocumentPosition(document.querySelector('#how-it-works')) & Node.DOCUMENT_POSITION_FOLLOWING),
  );
  expect(order).toBe(true);
  await expect(page.locator('header nav a[href="./funnel.html"]')).toHaveText('Build');
  await page.locator('.home-context > summary').click();
  await expect(page.locator('#how-it-works .hiw-role')).toHaveCount(3);
});

test('reel playback, seek, replay, mute and keyboard controls preserve their contract', async ({ page }) => {
  await page.goto('/index.html');
  const reel = page.locator('#reel');
  await expect(reel).toHaveClass(/is-enhanced/);
  await reel.locator('[data-reel="start"]').click();
  await expect(reel).toHaveClass(/is-started/);
  await expect(reel.locator('[data-reel="cover"]')).toBeHidden();
  await expect(reel.locator('[data-reel="sound"]')).toHaveAttribute('aria-pressed', 'true');
  await reel.locator('[data-reel="sound"]').click();
  await expect(reel.locator('[data-reel="sound"]')).toHaveAttribute('aria-pressed', 'false');
  for (let beat = 1; beat <= 7; beat++) {
    await reel.locator(`.beat-chip[data-beat="${beat}"]`).click();
    await expect(reel.locator('.reel-stage')).toHaveAttribute('data-active-beat', String(beat));
  }
  const seek = reel.locator('[data-reel="seek"]');
  await seek.fill('500');
  await expect(reel).not.toHaveClass(/is-playing/);
  await reel.locator('[data-reel="play"]').click();
  await expect(reel).toHaveClass(/is-playing/);
  await reel.locator('[data-reel="play"]').click();
  await expect(reel).not.toHaveClass(/is-playing/);
  await reel.locator('[data-reel="replay"]').click();
  await expect(reel.locator('.reel-stage')).toHaveAttribute('data-active-beat', '1');
  await reel.locator('[data-reel="play"]').click();
  await reel.locator('.beat-chip[data-beat="2"]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(reel.locator('.reel-stage')).toHaveAttribute('data-active-beat', '2');
  await page.keyboard.press('ArrowLeft');
  await expect(reel.locator('.reel-stage')).toHaveAttribute('data-active-beat', '1');
});

test('reduced motion still uses the full static storyboard', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/index.html');
  await expect(page.locator('#reel')).toHaveClass(/is-static/);
  await expect(page.locator('#reel .beat')).toHaveCount(7);
  for (const beat of await page.locator('#reel .beat').all()) await expect(beat).toBeVisible();
});

test('no-JS storyboard and native disclosures remain available', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto('/index.html');
    await expect(page.locator('#reel')).not.toHaveClass(/is-enhanced/);
    for (const beat of await page.locator('#reel .beat').all()) await expect(beat).toBeVisible();
    await page.locator('.home-context > summary').click();
    await expect(page.locator('#how-it-works')).toBeVisible();
  } finally {
    await context.close();
  }
});

test('sales-kit lightbox still opens, closes and returns focus', async ({ page }) => {
  await page.goto('/index.html');
  const frame = page.locator('button.kit-frame').first();
  await frame.click();
  const lightbox = page.locator('#kit-lightbox');
  await expect(lightbox).toBeVisible();
  await expect(lightbox.locator('img')).toHaveAttribute('src', /sales-kit-/);
  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
  await expect(frame).toBeFocused();
});

test('surrounding Home CSS cannot change internal demo geometry', async ({ browser }) => {
  const context = await browser.newContext();
  try {
    const baselinePage = await context.newPage();
    const currentPage = await context.newPage();
    await baselinePage.route('**/assets/home-structure.css*', route =>
      route.fulfill({ contentType: 'text/css', body: '' }),
    );
    const geometry = page => page.locator('#reel').evaluate(reel => {
      const origin = reel.getBoundingClientRect();
      return [reel, ...reel.querySelectorAll('.reel-frame, .reel-stage, .reel-cover, .reel-transport, .beat-chip')]
        .map(node => {
          const box = node.getBoundingClientRect();
          return [box.x - origin.x, box.y - origin.y, box.width, box.height];
        });
    });
    for (const width of [1440, 1024, 390]) {
      for (const page of [baselinePage, currentPage]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto('/index.html');
        await expect(page.locator('#reel')).toHaveClass(/is-enhanced/);
      }
      const baseline = await geometry(baselinePage);
      const current = await geometry(currentPage);
      expect(current).toHaveLength(baseline.length);
      for (let i = 0; i < current.length; i++) {
        for (let j = 0; j < 4; j++) expect(Math.abs(current[i][j] - baseline[i][j])).toBeLessThan(.1);
      }
    }
  } finally {
    await context.close();
  }
});

test('new Home navigation reflows and keeps the optional reference reachable', async ({ page }, testInfo) => {
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/index.html');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator('.home-journey').scrollIntoViewIfNeeded();
    for (const link of await page.locator('.home-journey a').all()) {
      const box = await link.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    await page.locator('.home-journey').screenshot({ path: testInfo.outputPath(`home-journey-${width}.png`) });
  }
  const results = await new AxeBuilder({ page }).include('.home-journey').include('.home-context > summary').analyze();
  expect(results.violations.filter(v => ['critical', 'serious'].includes(v.impact))).toEqual([]);
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('.home-context > summary').click();
  await expect(page.locator('#how-it-works')).toBeVisible();
  await page.goto('/index.html#value-evidence');
  await expect(page.locator('.home-context')).toHaveAttribute('open', '');
  await expect(page.locator('#value-evidence-h')).toBeVisible();
});
