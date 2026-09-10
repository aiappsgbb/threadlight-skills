import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('Production has a compact index without horizontal scrolling or fluorescent fills', async ({ page }) => {
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/production.html');
    const index = page.locator('.pr-chapter-index');
    await expect(index).not.toHaveAttribute('open', '');
    await expect(page.locator('.pr-chapter-links a')).toHaveCount(4);
    await index.locator('summary').click();
    await expect(index.locator('a')).toHaveCount(10);
    const overflow = await index.evaluate(el => el.scrollWidth > el.clientWidth + 1);
    expect(overflow).toBe(false);
    const style = await page.locator('.pr-posture-picker button[aria-pressed="true"]').evaluate(el => ({
      background: getComputedStyle(el).backgroundColor,
      shadow: getComputedStyle(el).boxShadow,
    }));
    expect(style.background).not.toBe('rgb(168, 255, 96)');
    expect(style.shadow).toBe('none');
  }
});

test('optional architecture detail is folded but deep links still reveal it', async ({ page }) => {
  await page.goto('/production.html');
  await expect(page.locator('#pillar-details')).not.toHaveAttribute('open', '');
  await expect(page.locator('#readiness-context')).not.toHaveAttribute('open', '');
  await expect(page.locator('#operator-prompts')).not.toHaveAttribute('open', '');
  await page.goto('/production.html#posture-trio-caption');
  await expect(page.locator('#pillar-details')).toHaveAttribute('open', '');
  await expect(page.locator('#posture-trio-caption')).toBeInViewport();
});

test('the architectural visual explains each posture rather than certifying it', async ({ page }) => {
  await page.goto('/production.html');
  const diagram = page.locator('.pr-architecture');
  await expect(diagram).toHaveAttribute('data-posture', 'gateway');
  await expect(diagram).toContainText('Shared AI gateway');
  await expect(diagram).toContainText('Policy');
  await expect(diagram).toContainText('Approval');
  await expect(diagram).toContainText('Trusted facts');
  await expect(diagram).toContainText('Audit');
  await page.getByRole('button', { name: /Foundry-native/ }).click();
  await expect(diagram).toHaveAttribute('data-posture', 'native');
  await expect(diagram).toContainText('Direct model access');
  await expect(page.locator('[data-posture-description]')).toContainText('without a shared AI gateway');
  await page.getByRole('button', { name: /Customer-owned/ }).click();
  await expect(diagram).toHaveAttribute('data-posture', 'customer');
  await expect(diagram).toContainText('Customer model gateway');
  await expect(page.locator('[data-posture-description]')).toContainText('Keep the existing');
  await expect(page.locator('.pr-posture-picker button[aria-pressed="true"]')).toHaveCount(1);
  await expect(diagram).toContainText('not a live assessment');
});

test('Production uses the existing lime identity and keeps controls legible', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/production.html');
  await expect(page.locator('[data-visual-anchor]')).toBeVisible();
  if (testInfo.project.use.colorScheme === 'dark') {
    const fill = await page.locator('h1 em').evaluate(el => getComputedStyle(el).backgroundImage);
    expect(fill).toContain('168, 255, 96');
  }
  expect((await page.locator('[data-visual-anchor]').boundingBox()).y).toBeLessThan(600);
  for (const button of await page.locator('.pr-posture-picker button').all()) {
    const box = await button.boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.width).toBeGreaterThanOrEqual(44);
  }
  const results = await new AxeBuilder({ page }).include('#chapter-top').analyze();
  expect(results.violations.filter(v => ['critical', 'serious'].includes(v.impact))).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('production-architecture-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: /Foundry-native/ }).click();
  await expect(page.locator('.pr-architecture')).toHaveAttribute('data-posture', 'native');
  await page.screenshot({ path: testInfo.outputPath('production-architecture-mobile.png'), fullPage: true });
});

test('without scripting the architectural drawing remains useful', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    await expect(page.locator('.pr-architecture')).toBeVisible();
    await expect(page.locator('.pr-architecture')).toContainText('Shared AI gateway');
    await expect(page.locator('.pr-posture-picker')).toBeHidden();
    await expect(page.locator('a[href="#posture-trio-caption"]')).toBeVisible();
  } finally {
    await context.close();
  }
});

test('gap closure is an evidence cycle, not an automatic amber-to-green flip', async ({ page }) => {
  await page.goto('/production.html#legs');
  const cycle = page.locator('[data-evidence-visual="recheck"]');
  await expect(cycle).toBeVisible();
  await expect(cycle.locator('ol > li')).toHaveCount(4);
  await expect(cycle).toContainText('Still open');
  await expect(cycle).toContainText('Explicit waiver');
  await expect(cycle).toContainText('Closed by evidence');
  await expect(page.locator('#remediation-skills')).not.toHaveAttribute('open', '');
  await page.locator('#remediation-skills > summary').click();
  await expect(page.locator('#remediation-skills .gap-card')).toHaveCount(9);
  await expect(page.locator('.gap-flip')).toHaveCount(0);
});
