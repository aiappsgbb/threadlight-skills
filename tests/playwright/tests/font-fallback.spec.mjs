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

// GitHub runner images ship different system fonts (DejaVu on Ubuntu) whose
// wider metrics pushed the case-study proof to y=781, under the wizard nav.
// Verdana plus extra letter-spacing approximates that worst case locally.
const WIDE_FONTS = 'body, body * { letter-spacing: .05em !important; } body { --serif: Verdana, "DejaVu Serif", serif; --sans: Verdana, "DejaVu Sans", sans-serif; --mono: "Courier New", monospace; }';

for (const viewport of [{ width: 390, height: 844 }, { width: 412, height: 839 }]) {
  test(`case-study shows its proof in the first mobile viewport with wide fonts (${viewport.width}px)`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/case-study.html');
    await page.addStyleTag({ content: WIDE_FONTS });
    const visual = page.locator('#chapter-top [data-visual-anchor]');
    await expect(visual).toBeVisible();
    const box = await visual.boundingBox();
    expect(box.y).toBeLessThan(600);
    const controls = await page.locator('.cs-wiz-nav').boundingBox();
    expect(Math.min(box.height, controls.y - box.y)).toBeGreaterThanOrEqual(200);
    // The short introduction still follows the proof inside the hero.
    await expect(page.locator('#chapter-top [data-chapter-intro]')).toBeVisible();
  });
}
