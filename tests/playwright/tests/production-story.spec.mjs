import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('the executive story moves from shared platform to release to runtime action governance', async ({ page }) => {
  await page.goto('/production.html');
  const sequence = ['#platform-controls', '#operating-controls', '#effect-authority'];
  expect(await page.locator('.production-map a').evaluateAll(links => links.map(a => a.getAttribute('href')))).toEqual(sequence);
  expect(await page.locator('[data-topic-tab]').evaluateAll(links => links.map(a => a.getAttribute('href')))).toEqual(sequence);
  expect(await page.locator('.ciso-pentagon-svg').evaluate(svg => svg.closest('[data-topic-panel]')?.id ?? null)).toBeNull();
  await expect(page.locator('.posture-trio-svg, .scorecard-preview')).toHaveCount(0);
  await expect(page.locator('#readiness-topic .ciso-pentagon-svg, #readiness-topic .posture-trio-svg, #readiness-topic .scorecard-preview')).toHaveCount(0);
});

test('Citadel has its own example and platform diagram, not Threadlight ownership claims', async ({ page }) => {
  await page.goto('/production.html#platform-controls');
  await expect(page.locator('#platform-controls [data-production-example]')).toContainText('Three teams');
  await expect(page.locator('[data-visual="citadel-hub"]')).toBeVisible();
  for (const word of ['FinOps', 'telemetry', 'model', 'use case', 'external']) {
    await expect(page.locator('#platform-controls')).toContainText(new RegExp(word, 'i'));
  }
  await expect(page.locator('#platform-controls .source-attribution a')).toBeVisible();
});

test('release routes keep the CI diagram central and keep action enforcement in its own area', async ({ page }) => {
  await page.goto('/production.html#operating-controls');
  await expect(page.locator('#operating-controls')).toContainText('GitHub Actions or Azure DevOps');
  await expect(page.locator('[data-visual="release-paths"]')).toHaveCount(0);
  await expect(page.locator('.pipe-svg')).toBeVisible();
  await expect(page.locator('#readiness-topic')).toContainText('go-live');
  await expect(page.locator('#readiness-topic [data-visual="runtime-evidence"]')).toHaveCount(0);
  await expect(page.locator('#effect-authority [data-production-example]')).toContainText('Can I return this order');
});

test('common coverage and go-live links work without switching to an unrelated area', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  for (const id of ['checks', 'proof', 'start', 'chapter-recap']) {
    await page.goto(`/production.html#${id}`);
    await expect(page.locator(`#${id}`)).toBeVisible();
    await expect.poll(() => page.locator(`#${id}`).evaluate(el => el.getBoundingClientRect().top >= 0
      && el.getBoundingClientRect().top < innerHeight)).toBe(true);
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html#proof');
  const violations = (await new AxeBuilder({ page }).include('#production-review')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
});
