import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('fourth policy tab explains evidence variants without enabling a real action', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('[data-governed-flow]');
  await expect(flow.getByRole('tab')).toHaveText(['Allowed', 'Blocked', 'Human review', 'Signed evidence']);
  await flow.getByRole('tab', { name: 'Human review', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(flow.getByRole('tab', { name: 'Signed evidence', exact: true })).toBeFocused();
  const select = flow.getByLabel('Purchase proof');
  for (const choice of ['missing', 'valid', 'changed']) {
    await select.selectOption(choice);
    await expect(flow).toHaveAttribute('data-step', '0');
    await expect(flow).toHaveAttribute('data-playing', 'false');
    await expect(flow.locator('[data-flow-evidence]')).toContainText('Policy example: corroborate purchase and amount');
    await flow.getByRole('button', { name: 'Step 2: Verify purchase evidence', exact: true }).click();
    await expect(flow.locator('[data-flow-actor]')).toHaveText('Evidence Provider');
    await flow.locator('[data-flow-progress] button').last().click();
    await expect(flow).toHaveAttribute('data-node', choice === 'valid' ? 'result' : 'deny');
    await expect(flow.locator('[data-flow-node="effect"]')).toHaveAttribute('data-used', String(choice === 'valid'));
    await expect(flow.locator('[data-flow-case-note]')).toContainText(/not.*certif/i);
  }
  await flow.getByRole('tab', { name: 'Allowed', exact: true }).click();
  await expect(flow.locator('[data-flow-evidence]')).toBeHidden();
  await expect(flow.locator('[data-flow-node="proof"]')).toBeHidden();
  await flow.getByRole('tab', { name: 'Signed evidence', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  expect((await new AxeBuilder({ page }).include('#workflow-in-action')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
});

test('signed-evidence fragment selects the right scenario and leaves a no-JS explanation', async ({ page, browser, baseURL }) => {
  await page.goto('/production.html#production-input-proof');
  await expect(page.locator('[data-governed-flow]')).toHaveAttribute('data-scenario', 'evidence');
  await expect(page.getByLabel('Purchase proof')).toBeVisible();
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  try {
    const p = await context.newPage();
    await p.goto('/production.html#production-input-proof');
    await expect(p.locator('.wf-static-note')).toContainText('Signed evidence');
    await expect(p.locator('.wf-static-note')).toContainText('missing or changed proof');
  } finally {
    await context.close();
  }
});
