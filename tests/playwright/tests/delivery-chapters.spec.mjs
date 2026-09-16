import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

for (const path of ['self-improving', 'customize']) {
  test.describe(path, () => {
    test('first-fold evidence has no serious accessibility violations', async ({ page }) => {
      await page.goto(`/${path}.html`);
      const result = await new AxeBuilder({ page }).include('[data-visual-anchor]').analyze();
      expect(result.violations.filter(v => ['serious', 'critical'].includes(v.impact))).toEqual([]);
    });
    for (const width of [1440, 390]) {
      test(`evidence-first chapter at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`/${path}.html`);
        await expect(page.locator('body')).toHaveClass(/chapter-experience/);
        expect((await page.locator('[data-chapter-intro]').innerText()).trim().split(/\s+/).length).toBeLessThanOrEqual(60);
        const anchor = page.locator('[data-visual-anchor]').first();
        await expect(anchor).toBeVisible();
        const box = await anchor.boundingBox();
        expect(box.y).toBeLessThanOrEqual(width === 390 ? 600 : 760);
        expect(Math.min(box.height, 900 - box.y)).toBeGreaterThanOrEqual(200);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        await expect(page.locator('.doc > .why-grid')).toHaveCount(0);
        const detail = page.locator('.doc details').first();
        await detail.locator('summary').click();
        await expect(detail).toHaveAttribute('open', '');
        await detail.locator('summary').press('Enter');
        await expect(detail).not.toHaveAttribute('open', '');
      });
    }
  });
}

test('diagnostic workbench scopes the historical example and the merged AgentOps adapter', async ({ page }) => {
  await page.goto('/self-improving.html');
  const anchor = page.locator('[data-visual-anchor]');
  for (const text of ['28437323962', '2026-06-30', '10/10', 'Human-reviewed change']) {
    await expect(anchor).toContainText(text);
  }
  await expect(page.locator('#agentops-preview')).toContainText(/merged[\s\S]*19610ca8/i);
  await expect(page.locator('#agentops-preview')).not.toContainText('proposed');
  await expect(page.locator('.masthead .nav > a[href="./production.html"][aria-current="location"]')).toHaveCount(1);
  await expect(page.locator('#modes')).toContainText(/paired/i);
  await expect(page.locator('#maintain')).toContainText('plan-only');
});

test('customer handoff is a manual, bounded overlay rather than an automatic production promise', async ({ page }) => {
  await page.goto('/customize.html');
  const map = page.locator('[data-visual-anchor]');
  for (const text of ['Keep upstream', 'Configure customer', 'Verify boundary', 'customer-profile.md', 'overlay/', 'non-coverage.md']) {
    await expect(map).toContainText(text);
  }
  await expect(page.locator('[data-chapter-intro]')).toContainText(/manual/i);
  await expect(page.locator('#how-to')).toContainText('must-fix');
  await expect(page.locator('.masthead .nav > a[href="./production.html"][aria-current="location"]')).toHaveCount(1);
});

test.describe('delivery chapters without JavaScript', () => {
  test.use({ javaScriptEnabled: false });
  test('proof and technical disclosures remain available', async ({ page }) => {
    for (const path of ['self-improving', 'customize']) {
      await page.goto(`/${path}.html`);
      await expect(page.locator('[data-visual-anchor]')).toBeVisible();
      const detail = page.locator('.doc details').first();
      await detail.locator('summary').click();
      await expect(detail).toHaveAttribute('open', '');
    }
  });
});
