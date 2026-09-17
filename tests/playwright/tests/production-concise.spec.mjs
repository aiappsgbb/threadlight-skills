import { test, expect } from '@playwright/test';

const wordCount = locator => locator.evaluate(element => {
  const copy = element.cloneNode(true);
  copy.querySelectorAll('style, script, .wf-mobile').forEach(node => node.remove());
  return copy.textContent.trim().split(/\s+/).length;
});

test('production has no expandable content and each area has a bounded reading length', async ({ page }) => {
  await page.goto('/production.html');
  await expect(page.locator('main details, main summary')).toHaveCount(0);
  for (const id of ['platform-topic', 'readiness-topic', 'actions-topic']) {
    expect(await wordCount(page.locator(`#${id}`)), id).toBeLessThan(700);
  }
  expect(await wordCount(page.locator('main'))).toBeLessThan(2400);
});

test('data privacy is independent and alternative platforms remain only a note', async ({ page }) => {
  await page.goto('/production.html#information-controls');
  expect(await page.locator('#information-controls').evaluate(el => el.closest('[data-topic-panel]'))).toBeNull();
  await expect(page.locator('#information-controls')).toContainText('Application and data owners');
  expect(await wordCount(page.locator('#information-controls'))).toBeLessThan(100);
  await page.goto('/production.html#platform-postures');
  await expect(page.locator('#platform-postures')).toContainText('Citadel is optional');
  expect(await wordCount(page.locator('#platform-postures'))).toBeLessThan(80);
  await expect(page.locator('.posture-trio-svg, [data-visual="data-lineage"]')).toHaveCount(0);
});

test('AgentOps leads directly into the retained pipeline instead of another introductory block', async ({ page }) => {
  await page.goto('/production.html#operating-controls');
  await expect(page.locator('[data-visual="release-paths"], [data-agentops-view]')).toHaveCount(0);
  await expect(page.locator('.pipe-svg')).toBeVisible();
  expect(await wordCount(page.locator('#operating-controls'))).toBeLessThan(110);
  expect(await page.locator('.pipe-svg').evaluate(svg => svg.getBoundingClientRect().top)).toBeLessThan(700);
  await expect(page.locator('#readiness-topic')).toContainText('before production');
  await expect(page.locator('#readiness-topic')).toContainText('before go-live');
});

test('action governance explains separate policy and authority services and ends without a scorecard', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  for (const component of ['Governed MCP gateway', 'Control plane', 'Human reviewer', 'Business API']) {
    await expect(page.locator('.action-actors')).toContainText(component);
  }
  await expect(page.locator('#effect-authority')).toContainText('effect boundary');
  await expect(page.locator('#evidence-boundaries')).toContainText('Try the guided workbook');
  await expect(page.locator('.scorecard-preview, .outcome-band, .stepper, .legend-3')).toHaveCount(0);
  expect(await wordCount(page.locator('#production-review'))).toBeLessThan(120);
  await expect(page.locator('#production-review a')).toHaveCount(2);
});

test('each area has recognizable component icons and a real deep-dive destination', async ({ page }) => {
  await page.goto('/production.html');
  const attribution = page.locator('#platform-controls .source-attribution a');
  await expect(attribution).toHaveAttribute('href', 'https://github.com/Azure-Samples/ai-hub-gateway-solution-accelerator/tree/citadel-v1');
  await expect(attribution).toContainText('Azure-Samples/ai-hub-gateway-solution-accelerator');
  for (const [area, destination] of [
    ['platform-topic', /Azure-Samples\/ai-hub-gateway-solution-accelerator/],
    ['readiness-topic', /agentops-deep-dive\.md$/],
    ['actions-topic', /agent-governance-deep-dive\.md#4-architecture-and-trust-boundaries$/],
  ]) {
    const panel = page.locator(`#${area}`);
    await expect(panel.locator('[data-deep-dive]')).toHaveCount(1);
    await expect(panel.locator('[data-deep-dive]')).toHaveAttribute('href', destination);
    expect(await panel.locator('[data-component-icon]').count()).toBeGreaterThanOrEqual(4);
  }
  expect(await page.locator('[data-component-icon] use, use[data-component-icon]').evaluateAll(icons =>
    icons.every(icon => document.getElementById(icon.getAttribute('href').slice(1))))).toBe(true);
});

test('governance typography follows the other areas instead of an oversized diagram', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  const sizes = await page.locator('[data-topic-panel] h2').evaluateAll(headings =>
    headings.map(el => getComputedStyle(el).fontSize));
  expect(new Set(sizes).size).toBe(1);
  for (const [selector, maximum] of [['.wf-label', 16], ['.wf-detail', 12], ['.wf-edge-label', 11]]) {
    const sizes = await page.locator(selector).evaluateAll(elements =>
      elements.map(el => parseFloat(getComputedStyle(el).fontSize)));
    expect(Math.max(...sizes), selector).toBeLessThanOrEqual(maximum);
  }
});
