import { test, expect } from '@playwright/test';

const wordCount = locator => locator.evaluate(element => {
  const copy = element.cloneNode(true);
  copy.querySelectorAll('style, script, .wf-mobile, .pipe-mobile, svg [hidden], svg desc, svg title, [data-flow-proof][hidden], [data-flow-approval][hidden], [data-flow-evidence][hidden], [data-flow-confirmation][hidden], [data-evidence-jwt][hidden]').forEach(node => node.remove());
  copy.querySelectorAll('details > :not(summary)').forEach(node => node.remove());
  return copy.textContent.trim().split(/\s+/).length;
});

test('each part stays bounded while implementation depth stays in disclosures', async ({ page }) => {
  await page.goto('/production.html');
  for (const id of ['production-domains', 'platform-topic', 'readiness-topic', 'actions-topic', 'evidence-review', 'production-review']) {
    expect(await wordCount(page.locator(`#${id}`)), id).toBeLessThan(800);
  }
  await page.goto('/production.html#production-input-proof');
  for (const choice of ['valid', 'missing', 'changed']) {
    await page.locator(`[data-flow-evidence-case][value="${choice}"]`).check();
    expect(await wordCount(page.locator('#actions-topic')), choice).toBeLessThan(900);
  }
  await page.getByRole('tab', { name: 'User confirmation', exact: true }).click();
  expect(await wordCount(page.locator('#actions-topic')), 'user confirmation').toBeLessThan(900);
  expect(await wordCount(page.locator('main'))).toBeLessThan(3400);
});

test('data privacy is reviewed with the evidence and alternative platforms remain short notes', async ({ page }) => {
  await page.goto('/production.html#information-controls');
  expect(await page.locator('#information-controls').evaluate(el => el.closest('#evidence-review')?.id)).toBe('evidence-review');
  await expect(page.locator('#information-controls')).toContainText('Application and data owners');
  expect(await wordCount(page.locator('#information-controls'))).toBeLessThan(100);
  await page.goto('/production.html#platform-postures');
  await expect(page.locator('#platform-postures')).toContainText('Citadel is optional');
  expect(await wordCount(page.locator('#platform-postures'))).toBeLessThan(80);
  await expect(page.locator('.posture-trio-svg, [data-visual="data-lineage"]')).toHaveCount(0);
});

test('Validate leads directly into the retained pipeline', async ({ page }) => {
  await page.goto('/production.html#operating-controls');
  await expect(page.locator('[data-visual="release-paths"], [data-agentops-view]')).toHaveCount(0);
  expect(await wordCount(page.locator('#operating-controls'))).toBeLessThan(110);
  await expect(page.locator('#readiness-topic')).toContainText('before production');
  await expect(page.locator('#readiness-topic')).toContainText('before go-live');
  if (page.viewportSize().width > 680) {
    await expect(page.locator('.pipe-svg')).toBeVisible();
    expect(await page.locator('.pipe-svg').evaluate(svg => svg.getBoundingClientRect().top)).toBeLessThan(800);
  } else {
    await expect(page.locator('.pipe-mobile')).toBeVisible();
  }
});

test('action governance names actors and gateways before the walkthrough and ends without a scorecard', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  for (const component of ['Governed MCP gateway', 'Business API']) {
    await expect(page.locator('[data-action-primary]')).toContainText(component);
  }
  for (const component of ['Control plane', 'Human reviewer']) {
    await expect(page.locator('[data-action-support]')).toContainText(component);
  }
  await expect(page.locator('[data-action-primary]')).toBeVisible();
  await expect(page.locator('.gateway-pair')).toBeVisible();
  await page.locator('[data-action-implementation] > summary').click();
  await expect(page.locator('#effect-authority')).toContainText('effect boundary');
  await expect(page.locator('#evidence-boundaries')).toContainText('Follow the existing-pilot guide');
  await expect(page.locator('.scorecard-preview, .outcome-band, .stepper, .legend-3')).toHaveCount(0);
  expect(await wordCount(page.locator('#production-review'))).toBeLessThan(200);
});

test('each part has a real deep-dive destination and recognizable icons resolve', async ({ page }) => {
  await page.goto('/production.html');
  const attribution = page.locator('#platform-controls .source-attribution a');
  await expect(attribution).toHaveAttribute('href', 'https://github.com/Azure-Samples/ai-hub-gateway-solution-accelerator/tree/citadel-v1');
  await expect(attribution).toContainText('Azure-Samples/ai-hub-gateway-solution-accelerator');
  for (const [area, destination] of [
    ['platform-topic', /production-governance\.md#2-choose-the-foundation-you-will-operate$/],
    ['readiness-topic', /agentops-deep-dive\.md$/],
    ['actions-topic', /agent-governance-deep-dive\.md#4-architecture-and-trust-boundaries$/],
    ['evidence-review', /production-governance\.md#5-review-the-evidence-name-the-owners$/],
  ]) {
    const panel = page.locator(`#${area}`);
    await expect(panel.locator('[data-deep-dive]')).toHaveCount(1);
    await expect(panel.locator('[data-deep-dive]')).toHaveAttribute('href', destination);
  }
  for (const area of ['platform-topic', 'readiness-topic', 'actions-topic']) {
    expect(await page.locator(`#${area} [data-component-icon]`).count(), area).toBeGreaterThanOrEqual(4);
  }
  expect(await page.locator('[data-component-icon] use, use[data-component-icon]').evaluateAll(icons =>
    icons.every(icon => document.getElementById(icon.getAttribute('href').slice(1))))).toBe(true);
});

test('part headings share one scale and the walkthrough keeps its compact type', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  const sizes = await page.locator('section[data-toc-id] h2').evaluateAll(headings =>
    headings.map(el => getComputedStyle(el).fontSize));
  expect(new Set(sizes).size).toBe(1);
  for (const [selector, maximum] of [['.wf-label', 16], ['[data-node-layer] .wf-detail', 12],
    ['[data-evidence-sequence] .wf-detail', 14], ['.wf-edge-label', 11]]) {
    const sizes = await page.locator(selector).evaluateAll(elements =>
      elements.map(el => parseFloat(getComputedStyle(el).fontSize)));
    expect(Math.max(...sizes), selector).toBeLessThanOrEqual(maximum);
  }
});
