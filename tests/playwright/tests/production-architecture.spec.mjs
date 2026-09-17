import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('Production shares the site navigation without restoring the superseded assessment panels', async ({ page }) => {
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/production.html');
    await expect(page.locator('header .nav a')).toHaveText(['Home', 'Basics', 'Build', 'Case study', 'Production']);
    await expect(page.locator('[data-topic-tab]')).toHaveCount(3);
    await expect(page.locator('main details, .pr-evidence-console, .pr-posture-picker')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
});

test('the shared directory does not reset the selected action topic', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  await expect(page.locator('#tab-actions')).toHaveAttribute('aria-selected', 'true');
  const summary = page.locator('.cx-directory summary');
  await summary.click();
  await expect(page.locator('.cx-directory-panel')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.cx-directory-panel')).toBeHidden();
  await expect(summary).toBeFocused();
  await expect(page.locator('#tab-actions')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#effect-authority')).toBeVisible();
  await expect(page).toHaveURL(/#effect-authority$/);
});

test('deep links select the owning topic below the shared header', async ({ page }) => {
  for (const [id, tab] of [
    ['model-controls', 'tab-platform'],
    ['legs', 'tab-operations'],
    ['evidence-boundaries', 'tab-actions'],
  ]) {
    await page.goto(`/production.html#${id}`);
    await expect(page.locator(`#${tab}`)).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator(`#${id}`)).toBeVisible();
    await expect.poll(() => page.locator(`#${id}`).evaluate(el => {
      const top = el.getBoundingClientRect().top;
      return top >= document.querySelector('.masthead').getBoundingClientRect().bottom - 1 && top < innerHeight;
    })).toBe(true);
  }
});

test('the accepted introduction and action controls remain accessible with the shared shell', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/production.html');
  await expect(page.locator('[data-chapter-intro]')).toContainText('Share AI services');
  await expect(page.locator('[data-visual-anchor]')).toBeVisible();
  const results = await new AxeBuilder({ page }).include('#chapter-top').analyze();
  expect(results.violations.filter(v => ['critical', 'serious'].includes(v.impact))).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('production-architecture-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/production.html#effect-authority');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await expect(page.locator('.wf-mobile')).toBeVisible();
  const mobile = await new AxeBuilder({ page }).include('#effect-authority').analyze();
  expect(mobile.violations.filter(v => ['critical', 'serious'].includes(v.impact))).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('production-architecture-mobile.png'), fullPage: true });
});

test('without scripting all three topics and static navigation remain available', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, baseURL: testInfo.project.use.baseURL,
    viewport: testInfo.project.use.viewport,
  });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    for (const id of ['platform-topic', 'readiness-topic', 'actions-topic']) {
      await expect(page.locator(`#${id}`)).toBeVisible();
    }
    await expect(page.locator('header .nav a[href="./basics.html"]')).toBeVisible();
    await expect(page.locator('.cx-directory')).toHaveCount(0);
    await expect(page.locator('main details')).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test('go-live evidence stays explicit without an illustrative scorecard', async ({ page }) => {
  await page.goto('/production.html#legs');
  await expect(page.locator('#runtime-controls')).toContainText('authorized probes');
  await expect(page.locator('#runtime-controls')).toContainText('fresh evidence');
  await expect(page.locator('#runtime-controls')).toContainText('noop is not business-write proof');
  await expect(page.locator('#readiness-topic a[href$="/docs/agent-operations.md"]')).toBeVisible();
  await expect(page.locator('.scorecard-preview, .gap-flip')).toHaveCount(0);
});
