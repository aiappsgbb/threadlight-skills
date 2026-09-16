import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('all six lifecycle phases explain the work with diagrams rather than prose columns', async ({ page }) => {
  await page.goto('/funnel.html');
  const diagrams = page.locator('[data-phase-visual]');
  await expect(diagrams).toHaveCount(6);
  expect(await diagrams.evaluateAll(nodes => nodes.map(node => node.dataset.phaseVisual)))
    .toEqual(['enter', 'build', 'integrate', 'assure', 'ship', 'improve']);
  for (const diagram of await diagrams.all()) {
    await expect(diagram).toBeVisible();
    expect(await diagram.locator('svg').count()).toBeGreaterThanOrEqual(2);
  }
  const visibleParagraphWords = await page.locator('main').evaluate(main =>
    [...main.querySelectorAll('p')].filter(p => p.checkVisibility())
      .map(p => p.innerText).join(' ').trim().split(/\s+/).length,
  );
  expect(visibleParagraphWords).toBeLessThanOrEqual(320);
  for (const detail of await page.locator('.phase-reference').all()) {
    await expect(detail).not.toHaveAttribute('open', '');
    await detail.locator(':scope > summary').click();
    await expect(detail.locator('a').first()).toBeVisible();
  }
});

test('phase diagrams remain legible and fit narrow screens without horizontal scrolling', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/funnel.html');
    await expect(page.locator('[data-phase-visual]')).toHaveCount(6);
    for (const figure of await page.locator('[data-phase-visual]').all()) {
      await figure.scrollIntoViewIfNeeded();
      expect(await figure.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      const overflow = await figure.locator('strong, span, p, figcaption').evaluateAll(nodes =>
        nodes.filter(node => {
          const r = node.getBoundingClientRect();
          return r.width > 0 && (r.left < -1 || r.right > innerWidth + 1);
        }).map(node => node.textContent),
      );
      expect(overflow).toEqual([]);
    }
    const result = await new AxeBuilder({ page }).include('.phase-diagram').analyze();
    expect(result.violations.filter(v => ['serious', 'critical'].includes(v.impact))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`visual-lifecycle-${width}.png`), fullPage: true });
  }
});

test('AgentOps and improvement replace repeated prose with evidence visuals', async ({ page }) => {
  await page.goto('/governance.html');
  await expect(page.locator('.agentops-flow svg')).toHaveCount(3);
  await expect(page.locator('[data-evidence-visual="agentops-states"]')).toBeVisible();
  await expect(page.locator('#agentops-technical')).not.toHaveAttribute('open', '');
  await expect(page.locator('#agentops')).toContainText('local process provenance, not Azure attestation');
  await page.goto('/self-improving.html');
  await expect(page.locator('[data-evidence-visual="diagnostics"]')).toBeVisible();
  const words = await page.locator('#diagnostics').evaluate(el =>
    [...el.querySelectorAll('p')].filter(p => p.checkVisibility()).map(p => p.innerText).join(' ').trim().split(/\s+/).length,
  );
  expect(words).toBeLessThanOrEqual(45);
});

test('customer handoff and workbook show boundaries before optional detail', async ({ page }) => {
  await page.goto('/customize.html');
  await expect(page.locator('#handoff-technical')).not.toHaveAttribute('open', '');
  await expect(page.getByRole('img', { name: 'Upstream and customer overlay composition', exact: true })).toBeVisible();
  await page.locator('#handoff-technical > summary').click();
  await expect(page.locator('#handoff-technical')).toContainText('non-coverage.md');
  await page.goto('/workbook.html');
  await expect(page.locator('[data-workbook-visual="preflight"]')).toBeVisible();
  await expect(page.locator('[data-workbook-visual="preflight"]')).toContainText('No cloud changes');
  await expect(page.getByRole('button', { name: 'Copy pre-flight', exact: true })).toBeVisible();
});
