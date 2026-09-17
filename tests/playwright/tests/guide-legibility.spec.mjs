import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const skillNames = [
  ['threadlight-governed-actions'], ['threadlight-govern'],
  ['threadlight-deploy', 'threadlight-cicd'],
  ['threadlight-cicd', 'threadlight-governed-actions', 'threadlight-safe-check'],
  ['threadlight-cicd', 'threadlight-production-ready'],
];

for (const theme of ['light', 'dark']) {
  test(`guide type, skills, conditional outputs and copy remain readable in ${theme}`, async ({ page }, testInfo) => {
    await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {
      configurable: true, value: { writeText: async value => { window.guideCopied = value; } },
    }));
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    const findings = [];
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 1280 ? 900 : 844 });
      await page.goto('/agent-governance.html');
      for (let step = 0; step < 5; step++) {
        const panel = page.locator('[data-guide-step]:visible');
        await expect(panel.locator('.pg-skills a')).toHaveText(skillNames[step]);
        await expect(panel.locator('.pg-expected h3')).toHaveText('Expected result');
        const type = await panel.evaluate(node => {
          const pre = getComputedStyle(node.querySelector('pre'));
          const goal = getComputedStyle(node.querySelector('.pg-goal'));
          const checks = getComputedStyle(node.querySelector('.pg-verify'));
          const bounds = node.getBoundingClientRect();
          const overflow = [...node.querySelectorAll('pre, .pg-skills a, .pg-expected, .pg-verify')]
            .filter(item => item.scrollWidth > item.clientWidth + 1 ||
              item.getBoundingClientRect().right > bounds.right + 1).map(item => item.className);
          return { prompt: parseFloat(pre.fontSize), lineHeight: parseFloat(pre.lineHeight),
            goal: parseFloat(goal.fontSize), checks: parseFloat(checks.fontSize), overflow };
        });
        expect.soft(type.prompt).toBeGreaterThanOrEqual(15);
        expect.soft(type.prompt).toBeLessThanOrEqual(16);
        expect.soft(type.lineHeight / type.prompt).toBeGreaterThanOrEqual(1.6);
        expect.soft(type.goal).toBeGreaterThanOrEqual(16);
        expect.soft(type.checks).toBeGreaterThanOrEqual(16);
        expect.soft(type.overflow).toEqual([]);
        const prompt = (await panel.locator('pre').textContent()).trim();
        const copy = panel.getByRole('button', { name: 'Copy prompt', exact: true });
        await copy.focus();
        await copy.press('Enter');
        await expect.poll(() => page.evaluate(() => window.guideCopied)).toBe(prompt);
        await expect(panel.locator('[data-copy-status]')).toContainText('Prompt copied');
        const violations = (await new AxeBuilder({ page }).include('main')
          .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
        if (violations.length) findings.push({ width, step, violations });
        expect.soft(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
        const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
        if (directory && [0, 2, 3].includes(step)) {
          if (!path.isAbsolute(directory)) throw new Error('Use an absolute screenshot directory');
          mkdirSync(directory, { recursive: true });
          await panel.screenshot({ path: path.join(directory, `${theme}-${width}-step-${step + 1}.png`), animations: 'disabled' });
        }
        if (step < 4) await page.getByRole('button', { name: 'Next step', exact: true }).click();
      }
      await expect(page.getByRole('button', { name: 'Next step', exact: true })).toBeDisabled();
    }
    expect(findings).toEqual([]);
  });
}

test('without JavaScript all five skill/output groups remain attached to their prompts', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/agent-governance.html`);
    await expect(page.locator('[data-guide-step]:visible')).toHaveCount(5);
    for (const panel of await page.locator('[data-guide-step]').all()) {
      await expect(panel.locator('.pg-skills')).toBeVisible();
      await expect(panel.locator('.pg-expected')).toBeVisible();
      await expect(panel.locator('[data-guide-prompt]')).toBeVisible();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally { await context.close(); }
});
