import { test, expect } from '@playwright/test';

const chapters = ['funnel', 'blueprint', 'industries', 'governance', 'self-improving', 'customize', 'workbook'];

for (const name of chapters) {
  test(`${name} uses the approved compact index and bounded accent`, async ({ page }) => {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/${name}.html`);
      const index = page.locator('.cx-chapter-index');
      await expect(index).not.toHaveAttribute('open', '');
      await expect(page.locator('.cx-chapter-links a').first()).toBeVisible();
      expect(await page.locator('.cx-chapter-links a').count()).toBeLessThanOrEqual(4);
      await index.locator('summary').click();
      const overflow = await page.locator('.floating-toc').evaluate(el =>
        [el, ...el.querySelectorAll('nav, ol, details')].some(node => node.scrollWidth > node.clientWidth + 1),
      );
      expect(overflow).toBe(false);
      const target = index.locator('a').last();
      const href = await target.getAttribute('href');
      await target.click();
      await expect(index).not.toHaveAttribute('open', '');
      await expect(page.locator(href)).toBeVisible();
      const color = await page.locator('h1 em').evaluate(el => getComputedStyle(el).backgroundImage);
      if (test.info().project.use.colorScheme === 'dark') expect(color).toContain('168, 255, 96');
    }
  });
}

test('each explanatory chapter has a diagram tied to its own subject', async ({ page }) => {
  const diagrams = {
    funnel: 'Credit memo routing',
    governance: 'Selected action boundary',
    'self-improving': 'From run evidence to reviewed change',
    customize: 'Upstream and customer overlay composition',
    workbook: 'Pilot checkpoints and live handoff',
    'case-study': 'Captured request trace',
  };
  for (const [name, label] of Object.entries(diagrams)) {
    await page.goto(`/${name}.html`);
    await expect(page.getByRole('img', { name: label, exact: true })).toBeVisible();
  }
});
