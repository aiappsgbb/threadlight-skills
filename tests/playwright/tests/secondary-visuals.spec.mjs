import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const diagrams = {
  'self-improving': ['mode-evidence', 'upgrade-boundary'],
  customize: ['customer-seams', 'handoff-acceptance'],
  workbook: ['pilot-checkpoint', 'cloud-boundary']
};

test('mode lanes keep single-run diagnostics, paired comparison and plan-only upgrade separate', async ({ page }) => {
  await page.goto('/self-improving.html');
  const modes = page.locator('[data-secondary-visual="mode-evidence"]');
  await expect(modes.locator('[data-mode="learn"]')).toContainText('One finished run');
  await expect(modes.locator('[data-mode="bench"]')).toContainText('Paired');
  await expect(modes.locator('[data-mode="upgrade"]')).toContainText('Dated matrix');
  const upgrade = page.locator('[data-secondary-visual="upgrade-boundary"]');
  await expect(upgrade).toContainText('plan-only');
  await expect(upgrade).toContainText('No network calls');
  await expect(upgrade.locator('[data-human-gate]')).toContainText('Human');
  await expect(upgrade.locator('a[href="#upgrade-files"]')).toBeVisible();
  await upgrade.locator('a[href="#upgrade-files"]').click();
  await expect(page.locator('#upgrade-files')).toHaveAttribute('open', '');
});

test('customer seams lead to review rather than inherited acceptance', async ({ page }) => {
  await page.goto('/customize.html');
  const seams = page.locator('[data-secondary-visual="customer-seams"]');
  await expect(seams.locator('[data-customer-seam]')).toHaveCount(4);
  await expect(seams).toContainText('Unmodified skills');
  const acceptance = page.locator('[data-secondary-visual="handoff-acceptance"]');
  await expect(acceptance).toContainText('must-fix');
  await expect(acceptance).toContainText('Cannot override');
  await expect(acceptance).toContainText('Written reason');
  await expect(acceptance.locator('[data-human-gate]')).toContainText('Architecture review');
  await acceptance.locator('a[href="#handoff-steps"]').click();
  await expect(page.locator('#handoff-steps')).toHaveAttribute('open', '');
});

test('workbook checkpoints show expected paths, not fabricated results or automatic deployment', async ({ page }) => {
  await page.goto('/workbook.html');
  const check = page.locator('[data-secondary-visual="pilot-checkpoint"]');
  await expect(check).toContainText('Expected paths');
  await expect(check.locator('[data-pilot-case]')).toHaveCount(2);
  await expect(check).toContainText('pending_review');
  await expect(check).toContainText('Fresh conversation');
  await expect(check).toContainText('Not executed by this page');
  const cloud = page.locator('[data-secondary-visual="cloud-boundary"]');
  await expect(cloud.locator('[data-human-gate]')).toContainText('Authorize');
  await expect(cloud).toContainText('budget');
  await expect(cloud).toContainText('not production approval');
  await cloud.locator('a[href="#live-invocation"]').click();
  await expect(page.locator('#live-invocation')).toHaveAttribute('open', '');
});

for (const [path, names] of Object.entries(diagrams)) {
  test(`${path} body diagrams are responsive, accessible and preserve the first fold`, async ({ page }) => {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/${path}.html`);
      expect((await page.locator('[data-chapter-intro]').innerText()).trim().split(/\s+/).length).toBeLessThanOrEqual(60);
      expect((await page.locator('[data-visual-anchor]').first().boundingBox()).y).toBeLessThan(width === 390 ? 600 : 760);
      for (const name of names) {
        const diagram = page.locator(`[data-secondary-visual="${name}"]`);
        await expect(diagram).toBeVisible();
        await expect(diagram.locator('figcaption')).toBeVisible();
        await diagram.scrollIntoViewIfNeeded();
        await expect.poll(() => diagram.evaluate(node => {
          let opacity = 1;
          for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
            opacity *= Number(getComputedStyle(ancestor).opacity);
          }
          return opacity;
        })).toBe(1);
        for (const href of await diagram.locator('use').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')))) {
          const [asset, symbol] = href.split('#');
          const sprite = await page.request.get(`/${asset}`);
          expect(await sprite.text()).toContain(`id="${symbol}"`);
        }
        const neutral = await diagram.locator('.map-output strong').first().evaluate(node => getComputedStyle(node).color);
        for (const color of await diagram.locator('code, a').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).color))) {
          expect(color).toBe(neutral);
        }
        const box = await diagram.boundingBox();
        expect(box.height).toBeGreaterThan(180);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
      }
      const result = await new AxeBuilder({ page }).include('[data-secondary-visual]').analyze();
      expect(result.violations.filter(v => ['serious', 'critical'].includes(v.impact))).toEqual([]);
      await page.locator('main details').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    }
  });
}

test.describe('secondary diagrams without JavaScript', () => {
  test.use({ javaScriptEnabled: false });
  for (const [path, names] of Object.entries(diagrams)) {
    test(`${path} keeps diagrams and disclosure content keyboard-readable`, async ({ page }) => {
      await page.goto(`/${path}.html`);
      for (const name of names) await expect(page.locator(`[data-secondary-visual="${name}"]`)).toBeVisible();
      const details = page.locator('main details[id]').last();
      await details.locator(':scope > summary').focus();
      await page.keyboard.press('Enter');
      await expect(details).toHaveAttribute('open', '');
    });
  }
});
