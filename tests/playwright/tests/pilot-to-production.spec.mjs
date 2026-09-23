import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

test('prior diagram stays fixed while unused modules lose every arrow and gain explicit labels', async ({ page }) => {
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('#workflow-in-action');
  const geometry = () => flow.locator('.wf-diagram [data-flow-node]').evaluateAll(nodes =>
    nodes.map(node => [node.dataset.flowNode, node.getAttribute('transform'),
      node.querySelector('rect').getAttribute('width'), node.querySelector('rect').getAttribute('height')]));
  const baseline = await geometry();
  expect(baseline).toHaveLength(8);
  for (const label of ['Allowed', 'Blocked', 'Human review']) {
    await flow.getByRole('tab', { name: label, exact: true }).click();
    expect(await geometry()).toEqual(baseline);
    await expect(flow.locator('[data-evidence-sequence]')).toBeHidden();
    const unused = flow.locator('[data-node-layer] [data-used="false"]:not([hidden])');
    expect(await unused.count()).toBeGreaterThan(0);
    for (const node of await unused.all()) await expect(node.locator('[data-unused-label]')).toHaveText('Not used on this path');
    const invalidEdges = await flow.locator('.wf-diagram [data-flow-edge]').evaluateAll(edges =>
      edges.filter(edge => {
        const [from, to] = edge.dataset.flowEdge.split(':');
        const root = edge.closest('[data-governed-flow]');
        const used = [from, to].every(id => root.querySelector(`[data-flow-node="${id}"]`).dataset.used === 'true');
        return !used && (getComputedStyle(edge).display !== 'none' || edge.hasAttribute('marker-end'));
      }).map(edge => edge.dataset.flowEdge));
    expect(invalidEdges).toEqual([]);
  }
  await flow.getByRole('tab', { name: 'Signed evidence', exact: true }).click();
  await expect(flow.locator('.wf-diagram')).toHaveAttribute('viewBox', '0 0 1100 350');
  await expect(flow.locator('[data-node-layer]')).toBeHidden();
  expect(await geometry()).toEqual(baseline);
  await flow.getByRole('tab', { name: 'Allowed', exact: true }).click();
  expect(await geometry()).toEqual(baseline);
});

test('existing-pilot guide moves only through guidance and copies the selected prompt', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: async text => { window.copiedGuidePrompt = text; } }, configurable: true,
  }));
  await page.goto('/agent-governance.html');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Take your Threadlight pilot to governed production');
  await expect(page.locator('[data-guide-step]:visible')).toHaveCount(1);
  await expect(page.locator('[data-guide-step]:visible')).toContainText('specs/SPEC.md');
  await page.getByRole('button', { name: 'Copy prompt', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.copiedGuidePrompt)).toContain('existing');
  await expect(page.locator('[data-guide-step]:visible [data-copy-status]')).toContainText('Prompt copied');
  const next = page.getByRole('button', { name: 'Next step', exact: true });
  await next.focus();
  await next.press('Enter');
  await expect(page.locator('[data-guide-step]:visible')).toHaveAttribute('data-guide-step', 'select');
  await expect(page.locator('[data-guide-status]')).toContainText('guidance, not cloud execution');
  for (let index = 0; index < 3; index++) await next.click();
  await expect(page.locator('[data-guide-step]:visible')).toHaveAttribute('data-guide-step', 'promote');
  await expect(next).toBeDisabled();
  await page.getByRole('button', { name: 'Previous step', exact: true }).click();
  await expect(page.locator('[data-guide-step]:visible')).toHaveAttribute('data-guide-step', 'validate');
});

test('clipboard rejection is visible and never reports a copied prompt', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: async () => { throw new DOMException('Denied', 'NotAllowedError'); } }, configurable: true,
  }));
  await page.goto('/agent-governance.html');
  await page.getByRole('button', { name: 'Copy prompt', exact: true }).click();
  const status = page.locator('[data-guide-step]:visible [data-copy-status]');
  await expect(status).toContainText('Clipboard unavailable');
  await expect(status).not.toContainText('Prompt copied');
});

test('caption wraps all actor action output text readably on mobile', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('#workflow-in-action');
  for (const scenario of ['Allowed', 'Blocked', 'Human review']) {
    await flow.getByRole('tab', { name: scenario, exact: true }).click();
    for (let step = 0; step < 9; step++) {
      for (const field of ['actor', 'action', 'output']) {
        await expect(flow.locator(`[data-flow-${field}]`)).toBeVisible();
        await expect(flow.locator(`[data-flow-${field}]`)).not.toBeEmpty();
      }
      const issues = await flow.locator('.wf-step-detail').evaluate(detail => {
        const bounds = detail.getBoundingClientRect();
        return [...detail.querySelectorAll('dt, dd')].filter(node => {
          const box = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return parseFloat(style.fontSize) < 13 || node.scrollWidth > node.clientWidth + 1 ||
            node.scrollHeight > node.clientHeight + 1 || ['hidden', 'clip'].includes(style.overflow) ||
            box.left < bounds.left - 1 || box.right > bounds.right + 1;
        }).map(node => node.textContent);
      });
      expect(issues).toEqual([]);
      if (scenario === 'Human review' && await flow.getAttribute('data-node') === 'fresh') {
        await flow.locator('.wf-caption-line').screenshot({ path: testInfo.outputPath('mobile-caption.png') });
      }
      const next = flow.locator('[data-flow-control="next"]');
      if (await next.isDisabled()) break;
      await next.click();
    }
  }
});

test('no-JS guide keeps every prompt and verification readable', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/agent-governance.html`);
    await expect(page.locator('[data-guide-step]:visible')).toHaveCount(5);
    await expect(page.locator('[data-guide-prompt]')).toHaveCount(5);
    await expect(page.locator('[data-guide-controls]')).toBeHidden();
    await page.getByRole('link', { name: /05.*Promote/ }).click();
    await expect(page).toHaveURL(/#promote-observe$/);
  } finally { await context.close(); }
});

for (const theme of ['light', 'dark']) {
  test(`exact presentation is readable in ${theme} on desktop and mobile`, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 1280 ? 720 : 844 });
      for (const [url, selector, name] of [
        ['/production.html#workflow-in-action', '#workflow-in-action', 'fixed-diagram'],
        ['/agent-governance.html', 'main', 'existing-pilot-guide'],
      ]) {
        await page.goto(url);
        expect.soft(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
        expect.soft((await new AxeBuilder({ page }).include(selector)
          .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
        const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
        if (directory && testInfo.project.name === 'chromium-desktop') {
          mkdirSync(directory, { recursive: true });
          await page.locator(selector).screenshot({ path: path.join(directory, `${theme}-${width}-${name}.png`), animations: 'disabled' });
        }
      }
    }
  });
}
