import { test, expect } from '@playwright/test';

async function expectStepStart(page, id) {
  const panel = page.locator(`#${id}`);
  const heading = panel.locator('h2');
  await expect(heading).toBeFocused();
  await expect(heading).toHaveAttribute('tabindex', '-1');
  await expect.poll(() => heading.evaluate(node => {
    const bounds = node.getBoundingClientRect();
    return bounds.top >= document.querySelector('.masthead').getBoundingClientRect().bottom &&
      bounds.bottom <= innerHeight;
  })).toBe(true);
  return panel;
}

for (const width of [390, 1280]) {
  for (const reducedMotion of ['reduce', 'no-preference']) {
    test(`explicit guide navigation starts at the new heading at ${width} with ${reducedMotion}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.emulateMedia({ reducedMotion });
      await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: { writeText: async text => { window.copiedGuidePrompt = text; } },
      }));
      await page.goto('/agent-governance.html#choose-actions');
      await expect(page.locator('[data-guide-enhanced]')).toBeVisible();
      const historyLength = await page.evaluate(() => history.length);
      const next = page.getByRole('button', { name: 'Next step', exact: true });
      await next.scrollIntoViewIfNeeded();
      await next.focus();
      await next.press('ArrowDown');
      await next.press('Enter');
      const panel = await expectStepStart(page, 'prepare-candidate');
      await expect(page).toHaveURL(/#prepare-candidate$/);
      expect(await page.evaluate(() => history.length)).toBe(historyLength + 1);
      for (const target of await panel.locator('.pg-skills a, [data-guide-prompt], [data-copy-prompt]').all()) {
        await page.keyboard.press('Tab');
        await expect(target).toBeFocused();
      }
      await page.keyboard.press('Enter');
      await expect(panel.locator('[data-copy-status]')).toContainText('Prompt copied');
      expect(await page.evaluate(() => window.copiedGuidePrompt))
        .toBe((await panel.locator('[data-guide-prompt]').textContent()).trim());

      const previous = page.getByRole('button', { name: 'Previous step', exact: true });
      await previous.scrollIntoViewIfNeeded();
      await previous.focus();
      await previous.press('Enter');
      await expectStepStart(page, 'choose-actions');
      expect(await page.evaluate(() => history.length)).toBe(historyLength + 2);

      await page.locator('[data-guide-index="4"]').click();
      await expectStepStart(page, 'promote-observe');
      await expect(next).toBeDisabled();
      const lastHistoryLength = await page.evaluate(() => history.length);
      await page.locator('[data-guide-index="4"]').click();
      await expectStepStart(page, 'promote-observe');
      expect(await page.evaluate(() => history.length)).toBe(lastHistoryLength);
    });
  }
}

test('initial and history restoration do not explicitly focus or scroll a guide step', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(() => {
    window.guideNavigationCalls = [];
    for (const [prototype, name] of [[HTMLElement.prototype, 'focus'], [Element.prototype, 'scrollIntoView']]) {
      const original = prototype[name];
      prototype[name] = function (...args) {
        if (this.matches('[data-guide-step], [data-guide-step] h2')) {
          window.guideNavigationCalls.push({ name, behavior: args[0]?.behavior });
        }
        return original.apply(this, args);
      };
    }
  });
  await page.goto('/agent-governance.html#choose-actions');
  await expect(page.locator('[data-guide-step]:visible')).toHaveId('choose-actions');
  expect(await page.evaluate(() => window.guideNavigationCalls)).toEqual([]);

  await page.getByRole('button', { name: 'Next step', exact: true }).click();
  await expectStepStart(page, 'prepare-candidate');
  expect(await page.evaluate(() => window.guideNavigationCalls)).toEqual([
    { name: 'focus', behavior: undefined }, { name: 'scrollIntoView', behavior: 'instant' },
  ]);
  await page.evaluate(() => { window.guideNavigationCalls = []; });
  await page.goBack();
  await expect(page.locator('[data-guide-step]:visible')).toHaveId('choose-actions');
  expect(await page.evaluate(() => !!document.activeElement.closest('[hidden]'))).toBe(false);
  await page.goForward();
  await expect(page.locator('[data-guide-step]:visible')).toHaveId('prepare-candidate');
  expect(await page.evaluate(() => window.guideNavigationCalls)).toEqual([]);
  await page.evaluate(() => { location.hash = '#promote-observe'; });
  await expect(page.locator('[data-guide-step]:visible')).toHaveId('promote-observe');
  expect(await page.evaluate(() => window.guideNavigationCalls)).toEqual([]);
});
