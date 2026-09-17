import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

test('Production explains actors and keeps model access separate from protected effects', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  const action = page.locator('#effect-authority');
  await expect(action.locator('[data-action-actor]')).toHaveCount(5);
  for (const phrase of ['Governed MCP gateway', 'Control plane', 'own writer identity',
    'Citadel/APIM', 'model gateway', 'unbound', 'direct', 'trusted host']) {
    await expect(action).toContainText(phrase);
  }
  await expect(page.locator('#workflow-in-action .wf-diagram')).toHaveCount(1);
  await expect(page.locator('[data-topic-tab][href="#effect-authority"]')).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('link', { name: 'Try the guided workbook', exact: true }).click();
  await expect(page).toHaveURL(/agent-governance\.html#overview$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Take your Threadlight pilot to governed production');
});

test('the guide reuses an existing pilot and exposes one step at a time', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/agent-governance.html');
  await expect(page.locator('[data-guide-step]')).toHaveCount(5);
  await expect(page.locator('[data-guide-step]:visible')).toHaveCount(1);
  await expect(page.locator('[data-governed-flow], .wf-diagram, [data-flow-node]')).toHaveCount(0);
  await expect(page.locator('[data-guide-step]:visible')).toContainText('specs/SPEC.md');
  await expect(page.locator('[data-guide-status]')).toContainText('guidance, not cloud execution');
});

test('old architecture and diagram fragments forward to their preserved Production targets', async ({ page }) => {
  for (const [old, target] of [['effect-authority', 'effect-authority'],
    ['workflow-in-action', 'workflow-in-action'], ['human-decisions', 'workflow-in-action'],
    ['evidence', 'evidence-boundaries'], ['freshness', 'effect-authority'], ['limits', 'effect-authority']]) {
    await page.goto(`/agent-governance.html#${old}`);
    await expect(page).toHaveURL(new RegExp(`production\\.html#${target}$`));
    await expect(page.locator(`#${target}`)).toBeVisible();
    await expect(page.locator('#actions-topic')).toBeVisible();
  }
});

test('former web exercise fragments open their exact approved workbook section', async ({ page }) => {
  await page.route('https://github.com/aiappsgbb/threadlight-skills/blob/**/docs/first-governed-workflow.md*',
    route => route.fulfill({ contentType: 'text/html', body: '<title>Workbook destination</title>' }));
  for (const [old, phase] of [['define', 1], ['prepare', 2], ['local', 3], ['authorize', 4], ['validate', 5], ['operate', 6]]) {
    await page.goto(`/agent-governance.html#${old}`);
    await expect(page).toHaveURL(new RegExp(`7782eba93754fb7cff85336d3f4a8703892bad76/.*#phase-${phase}-`));
  }
});

test('leaving the action topic pauses its illustrative playback without changing other topics', async ({ page }) => {
  await page.clock.install();
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('#workflow-in-action');
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.locator('[data-topic-tab][href="#operating-controls"]').click();
  await expect(flow).toBeHidden();
  await expect(flow).toHaveAttribute('data-playing', 'false');
  const step = await flow.getAttribute('data-step');
  await page.clock.runFor(20000);
  await expect(flow).toHaveAttribute('data-step', step);
  await expect(page.locator('#operating-controls')).toBeVisible();
  await expect(page.locator('#delivery-controls .pipe-svg')).toBeVisible();
});

for (const theme of ['light', 'dark']) {
  test(`both learning surfaces render clearly in ${theme} with native mobile navigation`, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 1280 ? 720 : 844 });
      for (const [url, target, label] of [
        ['/production.html#effect-authority', '#effect-authority', 'production-actions'],
        ['/agent-governance.html', 'main', 'web-workbook'],
      ]) {
        await page.goto(url);
        await expect(page.locator(target)).toBeVisible();
        expect.soft(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
        const violations = (await new AxeBuilder({ page }).include(target)
          .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
        expect.soft(violations).toEqual([]);
        const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
        if (directory) {
          if (!path.isAbsolute(directory)) throw new Error('Use an absolute screenshot directory');
          mkdirSync(directory, { recursive: true });
          await page.locator(target).screenshot({ path: path.join(directory,
            `${testInfo.project.name}-${theme}-${width}-${label}.png`), animations: 'disabled' });
        }
      }
    }
  });
}

test('both destinations and legacy forward links remain understandable without JavaScript', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/agent-governance.html#workflow-in-action`);
    await expect(page.locator('[data-guide-step]:visible')).toHaveCount(5);
    const back = page.getByRole('link', { name: 'Back to Production and the decision paths', exact: true });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).toHaveURL(/production\.html#effect-authority$/);
    await expect(page.locator('#effect-authority [data-action-actor]')).toHaveCount(5);
    await expect(page.locator('#effect-authority .wf-mobile')).toBeVisible();
    await expect(page.locator('#effect-authority .wf-static-note')).toBeVisible();
  } finally { await context.close(); }
});
