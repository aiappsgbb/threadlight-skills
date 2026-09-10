import { test, expect } from '@playwright/test';

for (const slug of ['funnel', 'workbook', 'blueprint', 'case-study']) {
  test(`${slug} keeps its first action visible with fallback fonts`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/${slug}.html`);
    await page.addStyleTag({
      content: 'body { --serif: "Times New Roman", serif; --sans: Arial, sans-serif; --mono: "Courier New", monospace; }',
    });
    const visual = page.locator('[data-visual-anchor]').first();
    if (slug === 'funnel') {
      await expect(page.locator('[data-chapter-intro]')).toHaveCSS('margin-top', '0px');
      await expect(page.locator('[data-chapter-intro]')).toHaveCSS('line-height', '22.5px');
    }
    await expect(visual).toBeVisible();
    const box = await visual.boundingBox();
    expect(box.y).toBeLessThan(600);
    if (slug === 'blueprint') {
      const action = await page.locator('#bp-custom button[type="submit"]').boundingBox();
      expect(action.y + action.height).toBeLessThanOrEqual(844);
    }
    if (slug === 'case-study') {
      const controls = await page.locator('.cs-wiz-nav').boundingBox();
      expect(Math.min(box.height, controls.y - box.y)).toBeGreaterThanOrEqual(200);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
