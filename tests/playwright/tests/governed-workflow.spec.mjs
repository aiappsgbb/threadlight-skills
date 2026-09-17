import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const section = '#workflow-in-action';
const open = async (page) => {
  await page.goto('/agent-governance.html#workflow-in-action');
  await expect(page.locator(section)).toBeVisible();
};

test('direct preview anchor lands on the illustration heading', async ({ page }, testInfo) => {
  await open(page);
  const heading = page.locator(`${section} h2`);
  await expect.poll(() => heading.evaluate((node) => {
    const top = node.getBoundingClientRect().top;
    return top >= 0 && top < 240;
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
  await expect(flow).toHaveAttribute('data-step', '-1');
  await flow.getByRole('button', { name: 'Start' , exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', '0');
  await page.clock.runFor(2000);
  await flow.getByRole('button', { name: 'Pause', exact: true }).click();
  const paused = await flow.getAttribute('data-step');
  await page.clock.runFor(9000);
  await expect(flow).toHaveAttribute('data-step', paused);
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', String(Number(paused) + 1));
  await flow.getByRole('button', { name: 'Start', exact: true }).click();
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
  await flow.getByRole('button', { name: 'Invalid proposal', exact: true }).click();
  await flow.getByRole('button', { name: 'Start', exact: true }).click();
  await page.clock.runFor(16000);
  await expect(flow).toHaveAttribute('data-node', 'deny');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await expect(flow.locator('[data-flow-status]')).toContainText('no business effect');
  await flow.getByRole('button', { name: 'Supervisor handoff', exact: true }).click();
  await flow.getByRole('button', { name: 'Start', exact: true }).click();
  await page.clock.runFor(16000);
  await expect(flow).toHaveAttribute('data-node', 'review');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow).toHaveAttribute('data-node', 'fresh');
  await expect(flow.locator('[data-flow-status]')).toContainText('unchanged');
});

test('reduced motion uses immediate keyboard steps with no autoplay', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await open(page);
  const flow = page.locator(section);
  await expect(flow.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
  const next = flow.getByRole('button', { name: 'Next step', exact: true });
  await next.focus();
  await next.press('Enter');
  await expect(next).toBeFocused();
  await expect(flow).toHaveAttribute('data-node', 'proposal');
  await page.clock.runFor(20000);
  await expect(flow).toHaveAttribute('data-node', 'proposal');
  await next.press('Space');
  await expect(flow).toHaveAttribute('data-node', 'checks');
  await expect(flow.locator('[aria-current="step"]')).toHaveCount(1);
});

test('scenario change resets playback and runtime motion preference stops timers', async ({ page }) => {
  await page.clock.install();
  await open(page);
  const flow = page.locator(section);
  await flow.getByRole('button', { name: 'Start', exact: true }).click();
  await flow.getByRole('button', { name: 'Supervisor handoff', exact: true }).click();
  await expect(flow).toHaveAttribute('data-step', '-1');
  await page.clock.runFor(8000);
  await expect(flow).toHaveAttribute('data-step', '-1');
  await flow.getByRole('button', { name: 'Start', exact: true }).click();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await page.clock.runFor(8000);
  await expect(flow).toHaveAttribute('data-step', '0');
});

test('small screens show one alternative without a path from deny to execution', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const flow = page.locator(section);
  await expect(flow.locator('.wf-branch:visible')).toHaveCount(1);
  await expect(flow.locator('.wf-execution')).toBeVisible();
  await flow.getByRole('button', { name: 'Invalid proposal', exact: true }).click();
  await expect(flow.locator('.wf-branch:visible')).toHaveCount(1);
  await expect(flow.locator('[data-flow-node="deny"]')).toBeVisible();
  await expect(flow.locator('.wf-execution')).toBeHidden();
  await flow.getByRole('button', { name: 'Supervisor handoff', exact: true }).click();
  await expect(flow.locator('[data-flow-node="review"]')).toBeVisible();
  await expect(flow.locator('[data-flow-node="fresh"]')).toBeVisible();
  await expect(flow.locator('.wf-execution')).toBeVisible();
});

for (const theme of ['light', 'dark']) {
  test(`static path is readable, non-overflowing and accessible in ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await open(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    const violations = (await new AxeBuilder({ page }).include(section)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
    await expect(page.locator(section).getByRole('link', { name: 'Open the workbook', exact: true }))
      .toHaveAttribute('href', /first-governed-workflow\.md$/);
  });
}

test('without JavaScript the full path and workbook links remain usable', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/agent-governance.html#workflow-in-action`);
    const flow = page.locator(section);
    for (const text of ['Agent proposes', 'Policy decides', 'Person authorizes', 'central audit ACK',
      'Backend checks', 'Record connects', 'No business effect']) await expect(flow).toContainText(text);
    await expect(flow.locator('[data-flow-controls]')).toBeHidden();
    await expect(flow.getByRole('link', { name: 'Open the workbook', exact: true })).toBeVisible();
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
