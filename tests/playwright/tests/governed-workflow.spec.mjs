import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const section = '#workflow-in-action';
const open = async (page) => {
  await page.goto('/production.html#workflow-in-action');
  await expect(page.locator(section)).toBeVisible();
};

test('direct preview anchor lands on the Production illustration below its navigation', async ({ page }, testInfo) => {
  await open(page);
  await expect.poll(() => page.locator(section).evaluate((node) => {
    const top = node.getBoundingClientRect().top;
    const offset = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop);
    return top >= offset - 2 && top < innerHeight;
  })).toBe(true);
  const output = process.env.THREADLIGHT_SCREENSHOT_DIR;
  if (output) {
    if (!path.isAbsolute(output)) throw new Error('Use an absolute screenshot directory');
    mkdirSync(output, { recursive: true });
    await page.locator(section).screenshot({ path: path.join(output, `${testInfo.project.name}-workflow.png`) });
  }
});

test('explicit play, pause, step and bounded replay never run in the background', async ({ page }) => {
  await page.clock.install();
  await open(page);
  const flow = page.locator(section);
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await page.clock.runFor(12000);
  await expect(flow).toHaveAttribute('data-step', '0');
  await flow.getByRole('button', { name: 'Play' , exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', '0');
  await page.clock.runFor(2000);
  await flow.getByRole('button', { name: 'Pause', exact: true }).click();
  const paused = await flow.getAttribute('data-step');
  await page.clock.runFor(9000);
  await expect(flow).toHaveAttribute('data-step', paused);
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', String(Number(paused) + 1));
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(20000);
  await expect(flow).toHaveAttribute('data-node', 'result');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await flow.getByRole('button', { name: 'Replay', exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', '0');
  await expect(flow).toHaveAttribute('data-playing', 'false');
});

test('deny has no effect; human review pauses until a deliberate continuation', async ({ page }) => {
  await page.clock.install();
  await open(page);
  const flow = page.locator(section);
  await flow.getByRole('tab', { name: 'Blocked', exact: true }).click();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(16000);
  await expect(flow).toHaveAttribute('data-node', 'deny');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await expect(flow.locator('[data-flow-output]')).toHaveText('No business write');
  await flow.getByRole('tab', { name: 'Human review', exact: true }).click();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(16000);
  await expect(flow).toHaveAttribute('data-node', 'review');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await flow.getByRole('button', { name: 'Show approved example', exact: true }).click();
  await expect(flow).toHaveAttribute('data-node', 'verify');
  await expect(flow.locator('[data-flow-output]')).toHaveText('One-use grant');
});

test('reduced motion uses immediate keyboard steps with no autoplay', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await open(page);
  const flow = page.locator(section);
  await expect(flow.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
  const next = flow.getByRole('button', { name: 'Next step', exact: true });
  await next.focus();
  await next.press('Enter');
  await expect(next).toBeFocused();
  await expect(flow).toHaveAttribute('data-node', 'checks');
  await page.clock.runFor(20000);
  await expect(flow).toHaveAttribute('data-node', 'checks');
  await next.press('Space');
  await expect(flow).toHaveAttribute('data-node', 'ack');
  await expect(flow.locator('[data-flow-progress] [aria-current="step"]')).toHaveCount(1);
});

test('scenario change resets playback and runtime motion preference stops timers', async ({ page }) => {
  await page.clock.install();
  await open(page);
  const flow = page.locator(section);
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await flow.getByRole('tab', { name: 'Human review', exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', '0');
  await page.clock.runFor(8000);
  await expect(flow).toHaveAttribute('data-step', '0');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await page.clock.runFor(8000);
  await expect(flow).toHaveAttribute('data-step', '0');
});

test('small screens label unused modules without onward connections', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const flow = page.locator(section);
  await expect(flow.locator('.wf-mobile li:visible')).toHaveCount(8);
  await flow.getByRole('tab', { name: 'Blocked', exact: true }).click();
  await expect(flow.locator('.wf-mobile li:visible')).toHaveCount(8);
  await expect(flow.locator('[data-mobile-node="deny"]')).toBeVisible();
  await expect(flow.locator('[data-mobile-node="effect"]')).toHaveAttribute('data-used', 'false');
  await expect(flow.locator('[data-mobile-node="effect"] [data-mobile-next]')).toBeHidden();
  await flow.getByRole('tab', { name: 'Human review', exact: true }).click();
  await expect(flow.locator('.wf-mobile li:visible')).toHaveCount(8);
  await expect(flow.locator('[data-mobile-node="review"]')).toBeVisible();
  await expect(flow.locator('[data-mobile-node="fresh"]')).toBeVisible();
  await expect(flow.locator('[data-mobile-node="effect"]')).toBeVisible();
});

test('scenario tabs and progress support keyboard selection with visible path state', async ({ page }) => {
  await open(page);
  const flow = page.locator(section);
  await flow.getByRole('tab', { name: 'Allowed', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(flow.getByRole('tab', { name: 'Blocked', exact: true })).toBeFocused();
  await expect(flow).toHaveAttribute('data-scenario', 'invalid');
  expect(await flow.locator('[data-flow-edge][data-on-path="true"]').evaluateAll(nodes =>
    nodes.map(node => node.dataset.flowEdge))).toEqual(['proposal:checks', 'checks:ack', 'ack:deny']);
  await page.keyboard.press('End');
  await expect(flow.getByRole('tab', { name: 'Human review', exact: true })).toBeFocused();
  await flow.getByRole('button', { name: 'Step 4: Person decides in Outlook', exact: true }).click();
  await expect(flow).toHaveAttribute('data-node', 'review');
  await expect(flow.locator('[data-current-state]')).toHaveText('Waiting for decision');
  await flow.getByRole('button', { name: 'Show approved example', exact: true }).click();
  await expect(flow).toHaveAttribute('data-node', 'verify');
  await flow.getByRole('button', { name: 'Previous step', exact: true }).click();
  await expect(flow).toHaveAttribute('data-node', 'review');
});

for (const theme of ['light', 'dark']) {
  test(`static path is readable, non-overflowing and accessible in ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await open(page);
    await expect(page.locator('html')).not.toHaveAttribute('data-theme');
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    const violations = (await new AxeBuilder({ page }).include(section)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
    await expect(page.getByRole('link', { name: 'Try the guided workbook', exact: true }))
      .toHaveAttribute('href', './agent-governance.html#overview');
  });
}

test('without JavaScript the full path and workbook links remain usable', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/production.html#workflow-in-action`);
    const flow = page.locator(section);
    await expect(flow.locator('.wf-mobile li:visible')).toHaveCount(8);
    await expect(flow.locator('.wf-static-note')).toBeVisible();
    await expect(flow.locator('.wf-static-note')).toContainText('blocked proposal records a denial');
    await expect(flow.locator('.wf-static-note')).toContainText('waits in Outlook');
    for (const control of await flow.locator('[data-flow-controls]').all()) await expect(control).toBeHidden();
    await expect(page.getByRole('link', { name: 'Try the guided workbook', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});

test('workbook diagrams parse and render locally without remote assets', async ({ page }) => {
  const markdown = readFileSync(new URL('../../../docs/first-governed-workflow.md', import.meta.url), 'utf8');
  const diagrams = [...markdown.matchAll(/```mermaid\n([\s\S]*?)```/g)];
  expect(diagrams).toHaveLength(3);
  const require = createRequire(import.meta.url);
  const renderer = require.resolve('mermaid/dist/mermaid.min.js');
  await page.goto('/agent-governance.html');
  await page.addScriptTag({ path: renderer });
  for (const [index, [, source]] of diagrams.entries()) {
    const svg = await page.evaluate(async ({ source, index }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', htmlLabels: false });
      await mermaid.parse(source);
      return (await mermaid.render(`workbook-${index}`, source)).svg;
    }, { source, index });
    expect(svg).toContain('<svg');
    expect(svg).not.toMatch(/Syntax error|mermaid-error|<script/);
  }
});
