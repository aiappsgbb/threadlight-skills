import { test, expect } from '@playwright/test';
import { createRequire } from 'node:module';

const chapters = ['funnel', 'blueprint', 'industries', 'case-study', 'production', 'self-improving', 'customize', 'workbook', 'governance'];
const siteMap = createRequire(import.meta.url)('../../../docs/assets/site-map.js');

for (const name of chapters) {
  test(`${name}: start with a concrete visual and a short introduction`, async ({ page }) => {
    await page.goto(`/${name}.html`);
    await expect(page.locator('body')).toHaveClass(/chapter-experience/);
    const intro = page.locator('[data-chapter-intro]').first();
    await expect(intro).toBeVisible();
    expect((await intro.innerText()).trim().split(/\s+/).length).toBeLessThanOrEqual(60);
    const visual = page.locator('[data-visual-anchor]').first();
    await expect(visual).toBeVisible();
    expect((await visual.boundingBox()).y).toBeLessThan(page.viewportSize().width <= 600 ? 600 : 760);
    await expect(page.locator('header nav.nav a[href="./funnel.html"]')).toHaveText('Build');
    await page.locator('.cx-directory summary').click();
    await expect(page.locator('.cx-directory a')).toHaveCount(siteMap.groups.flatMap(group => group.pages).length);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('Explore closes on Escape and does not compete with the mobile menu', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/production.html');
  await page.locator('[data-mobile-nav-toggle]').click();
  await expect(page.locator('header nav.nav')).toBeVisible();
  const summary = page.locator('.cx-directory summary');
  await summary.click();
  await expect(page.locator('header nav.nav')).toBeHidden();
  await expect(page.locator('.cx-directory-panel')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.cx-directory-panel')).toBeHidden();
  await expect(summary).toBeFocused();
  await summary.click();
  await page.locator('[data-mobile-nav-toggle]').click();
  await expect(page.locator('.cx-directory-panel')).toBeHidden();
  await expect(page.locator('header nav.nav')).toBeVisible();
});

test('Home does not load the new chapter presentation or navigation', async ({ page }) => {
  await page.goto('/index.html');
  await expect(page.locator('body')).not.toHaveClass(/chapter-experience/);
  await expect(page.locator('link[href*="chapter-experience"], script[src*="chapter-experience"]')).toHaveCount(0);
});

test('zoomed chapter directory fits the actual layout width', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto('/production.html');
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  await expect(page.locator('body')).toHaveAttribute('data-cx-compact', '');
  await page.locator('.cx-directory summary').click();
  const panel = await page.locator('.cx-directory-panel').boundingBox();
  expect(panel.x).toBeGreaterThanOrEqual(0);
  expect(panel.x + panel.width).toBeLessThanOrEqual(1024);
  expect((await page.locator('.masthead').boundingBox()).height).toBeLessThan(250);
});

test('chapter links open the technical disclosure that owns their target', async ({ page }) => {
  await page.goto('/self-improving.html');
  await page.locator('.cx-chapter-index summary').click();
  await page.locator('.cx-chapter-index a[href="#how"]').click();
  await expect(page.locator('#how')).toBeVisible();
  await expect(page.locator('#how')).toBeInViewport();
  await page.goto('/self-improving.html#how');
  await expect(page.locator('#how')).toBeVisible();
});
