import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';

const open = async (page) => {
  await page.goto('/production.html#workflow-in-action');
  return page.locator('#workflow-in-action');
};
const visiblePath = async (flow) => flow.locator('.wf-diagram [data-flow-node]').evaluateAll(
  (nodes) => nodes.map((node) => node.dataset.flowNode));

test('only relevant nodes and edges exist for each scenario, including denial audit', async ({ page }) => {
  const flow = await open(page);
  expect(await visiblePath(flow)).toEqual(['proposal', 'checks', 'ack', 'effect', 'result']);
  await expect(page.locator('[data-action-actor="human"] [data-actor-state]')).toHaveText('Not required');
  await flow.getByRole('tab', { name: 'Blocked', exact: true }).click();
  expect(await visiblePath(flow)).toEqual(['proposal', 'checks', 'denial-audit', 'deny']);
  expect(await flow.locator('[data-flow-edge]').evaluateAll(nodes => nodes.map(node => node.dataset.flowEdge)))
    .toEqual(['proposal:checks', 'checks:denial-audit', 'denial-audit:deny']);
  await expect(flow.locator('[data-flow-node="effect"], [data-mobile-node="effect"]')).toHaveCount(0);
  await expect(page.locator('[data-action-actor="business"] [data-actor-state]')).toHaveText('Not involved in write');
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow.locator('[data-flow-output]')).toHaveText('Deny');
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow.locator('[data-flow-output]')).toHaveText('Denial audit recorded');
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(flow.locator('[data-current-state]')).toHaveText('Completed');
  await expect(flow.locator('[data-flow-output]')).toHaveText('No business write');
  await expect(flow.locator('[data-flow-case-note]')).toContainText('facts/health');
});

test('human continuation cannot skip pending, real-decision concept, witness, grant and fresh ACK order', async ({ page }) => {
  await page.clock.install();
  const flow = await open(page);
  await flow.getByRole('tab', { name: 'Human review', exact: true }).click();
  expect(await visiblePath(flow)).toEqual(
    ['proposal', 'checks', 'pending', 'review', 'verify', 'fresh', 'ack', 'effect', 'result']);
  const commit = flow.getByRole('button', { name: /Step 8:/ });
  await expect(commit).toBeDisabled();
  await flow.getByRole('button', { name: 'Play', exact: true }).click();
  await page.clock.runFor(20000);
  await expect(flow).toHaveAttribute('data-node', 'review');
  await expect(flow).toHaveAttribute('data-playing', 'false');
  await expect(flow.locator('[data-current-state]')).toHaveText('Waiting for decision');
  await expect(flow.locator('[data-flow-node="pending"] [data-node-state]')).toHaveText('Completed');
  await expect(commit).toBeDisabled();
  await expect(flow.locator('[data-flow-output]')).toHaveText('Awaiting human decision');
  await flow.getByRole('button', { name: 'Show approved example', exact: true }).click();
  for (const [id, output] of [['verify', 'One-use grant'], ['fresh', 'Consumed once'], ['ack', 'Authorization receipt ACK'],
    ['effect', 'Decision + business audit'], ['result', 'Stable audit ID']]) {
    await expect(flow).toHaveAttribute('data-node', id);
    await expect(flow.locator('[data-flow-output]')).toHaveText(output);
    if (id !== 'result') await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  }
  await expect(flow.locator('[data-current-state]')).toHaveText('Completed');
  await flow.getByRole('button', { name: 'Replay', exact: true }).click();
  await expect(commit).toBeDisabled();
});

test('scenario-specific semantic renders stay readable on desktop/mobile in both themes', async ({ page }, testInfo) => {
  const failures = [];
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 1280 ? 720 : 844 });
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
      const flow = await open(page);
      for (const label of ['Allowed', 'Blocked', 'Human review']) {
        await flow.getByRole('tab', { name: label, exact: true }).click();
        const violation = (await new AxeBuilder({ page }).include('#effect-authority')
          .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
        if (violation.length) failures.push({ width, theme, label, violation });
        expect.soft(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
        const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
        if (directory && testInfo.project.name === 'chromium-desktop') {
          if (!path.isAbsolute(directory)) throw new Error('Use an absolute screenshot directory');
          mkdirSync(directory, { recursive: true });
          await flow.screenshot({ path: path.join(directory, `${theme}-${width}-${label.replaceAll(' ', '-')}.png`),
            animations: 'disabled' });
        }
      }
    }
  }
  expect(failures).toEqual([]);
});
