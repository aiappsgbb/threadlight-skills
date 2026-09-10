import { test, expect } from '@playwright/test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const siteMap = require('../../../docs/assets/site-map.js');
const chapters = siteMap.groups.flatMap(group => group.pages);
const primaryTitles = ['Home', 'Basics', 'Build', 'Case study', 'Production'];

for (const chapter of [{ slug: 'index', title: 'Home' }, ...chapters]) {
  test(`${chapter.slug}: primary and grouped Explore preserve page ownership`, async ({ page }) => {
    await page.goto(`/${chapter.slug}.html`);
    const nav = page.locator('.masthead nav.nav');
    await expect(nav.locator('a')).toHaveText(primaryTitles);
    const owner = siteMap.groups.find(group => group.pages.some(item => item.slug === chapter.slug));
    const active = nav.locator('[aria-current]');
    await expect(active).toHaveCount(1);
    await expect(active).toHaveAttribute('href', `./${owner?.entry || 'index'}.html`);
    await expect(active).toHaveAttribute('aria-current', chapter.slug === (owner?.entry || 'index') ? 'page' : 'location');
    if (page.viewportSize().width < 720) {
      await page.locator('[data-mobile-nav-toggle]').click();
      await expect(active).toBeVisible();
    }
    if (chapter.slug === 'index') {
      await expect(page.locator('script[src*="site-map"], script[src*="chapter-experience"]')).toHaveCount(0);
      return;
    }
    const summary = page.locator('.cx-directory > summary');
    await summary.focus();
    await page.keyboard.press('Enter');
    const panel = page.locator('.cx-directory-panel');
    await expect(panel).toBeVisible();
    await expect(panel.locator('h2')).toHaveText(siteMap.groups.map(group => group.title));
    for (const heading of await panel.locator('h2').all()) {
      const style = await heading.evaluate(node => ({
        fontSize: parseFloat(getComputedStyle(node).fontSize),
        clipped: node.scrollWidth > node.clientWidth + 1,
      }));
      expect(style.fontSize).toBeLessThanOrEqual(16);
      expect(style.clipped).toBe(false);
    }
    await expect(panel.locator('a')).toHaveCount(chapters.length);
    for (const group of siteMap.groups) {
      const section = panel.locator(`[data-site-group="${group.id}"]`);
      await expect(section.locator('a strong')).toHaveText(group.pages.map(item => item.title));
      expect(await section.locator('a').evaluateAll(nodes => nodes.map(node => node.getAttribute('href'))))
        .toEqual(group.pages.map(item => `./${item.slug}.html`));
    }
    await expect(panel.locator('[data-current-group]')).toHaveAttribute('data-site-group', owner.id);
    await expect(panel.locator('a[aria-current]')).toHaveCount(1);
    await expect(panel.locator('a[aria-current="page"] strong')).toHaveText(chapter.title);
    await page.keyboard.press('Tab');
    await expect(panel.locator('a').first()).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(summary).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}

test('grouped directory and contextual child routes fit narrow and zoomed layouts', async ({ page }, testInfo) => {
  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/blueprint.html');
    const cue = page.locator('.cx-journey');
    await expect(cue.getByRole('navigation', { name: 'Breadcrumb' })).toBeVisible();
    await expect(cue.getByRole('link', { name: 'Build', exact: true })).toBeVisible();
    await expect(cue.locator('.cx-journey-next')).toBeVisible();
    await page.locator('.cx-directory summary').click();
    const panel = page.locator('.cx-directory-panel');
    const box = await panel.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    if (width === 1440) expect(await panel.evaluate(node => node.scrollHeight <= node.clientHeight + 1)).toBe(true);
    if (width === 390 || width === 1440) await page.screenshot({ path: testInfo.outputPath(`navigation-${width}.png`) });
  }
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  await expect(page.locator('body')).toHaveAttribute('data-cx-compact', '');
  const box = await page.locator('.cx-directory-panel').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(1024);
});

test('mobile primary menu and native Explore close without stranding keyboard focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/governance.html');
  const menu = page.locator('[data-mobile-nav-toggle]');
  const nav = page.locator('.masthead nav.nav');
  const summary = page.locator('.cx-directory summary');
  const panel = page.locator('.cx-directory-panel');
  await menu.click();
  await expect(nav).toBeVisible();
  await summary.click();
  await expect(panel).toBeVisible();
  await expect(nav).toBeHidden();
  await menu.click();
  await expect(panel).toBeHidden();
  await expect(nav).toBeVisible();
  await nav.locator('a').first().focus();
  await page.keyboard.press('Escape');
  await expect(nav).toBeHidden();
  await expect(menu).toBeFocused();
  await summary.click();
  await page.locator('.masthead').click({ position: { x: 4, y: 4 } });
  await expect(panel).toBeHidden();
});

test('the static hierarchy remains usable without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    for (const slug of ['index', 'basics', 'blueprint', 'governance']) {
      await page.goto(`/${slug}.html`);
      const nav = page.locator('.masthead nav.nav');
      await expect(nav).toBeVisible();
      await expect(nav.locator('a')).toHaveText(primaryTitles);
      for (const link of await nav.locator('a').all()) await expect(link).toBeVisible();
      if (slug === 'blueprint' || slug === 'governance') {
        await expect(page.locator('.cx-journey')).toBeVisible();
        await expect(page.locator('.cx-journey-next')).toBeVisible();
      }
    }
  } finally {
    await context.close();
  }
});
