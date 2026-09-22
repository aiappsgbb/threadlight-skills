import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const pageURL = '/agent-governance.html';

async function styleSample(page) {
  return page.evaluate(() => {
    const style = (selector) => getComputedStyle(document.querySelector(selector));
    return {
      bodyFont: style('body').fontFamily,
      background: style('body').backgroundColor,
      foreground: style('body').color,
      headingFont: style('h1').fontFamily,
      accent: style('body').getPropertyValue('--chapter-accent').trim(),
    };
  });
}

for (const theme of ['light', 'dark']) {
  test(`Production and Governance inherit the same native ${theme} theme and fonts`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await page.goto('/production.html');
    const production = await styleSample(page);
    const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
    if (directory) {
      if (!path.isAbsolute(directory)) throw new Error('Use an absolute screenshot directory');
      mkdirSync(directory, { recursive: true });
      await page.screenshot({ path: path.join(directory, `${testInfo.project.name}-${theme}-production.png`) });
    }
    await page.goto(`${pageURL}?scoutTheme=${theme === 'light' ? 'dark' : 'light'}`);
    expect(await styleSample(page)).toEqual(production);
    await expect(page.locator('html')).not.toHaveAttribute('data-theme');
    const nav = page.locator('.masthead .nav');
    await expect(nav.locator('a')).toHaveText(['Home', 'Basics', 'Build', 'Case study', 'Production']);
    await expect(nav.locator('[aria-current="location"]')).toHaveAttribute('href', './production.html');
    if (directory) {
      await page.screenshot({ path: path.join(directory, `${testInfo.project.name}-${theme}-governance.png`) });
    }
    const violations = (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
  });
}

test('connected illustration fits a desktop chapter instead of a second long page', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('#workflow-in-action');
  await expect(flow.locator('.wf-diagram')).toBeVisible();
  for (const reducedMotion of ['no-preference', 'reduce']) {
    await page.emulateMedia({ reducedMotion });
    expect((await flow.boundingBox()).height).toBeLessThanOrEqual(680);
    expect((await flow.locator('.wf-panel').boundingBox()).height).toBeLessThanOrEqual(480);
  }
  await expect(flow.getByRole('tab')).toHaveText(['Allowed', 'Blocked', 'Human review', 'Signed evidence']);
  await expect(flow).toHaveAttribute('data-node', 'proposal');
  await expect(flow.locator('[data-flow-progress] [aria-current="step"]')).toHaveCount(1);
  const clipped = await flow.locator('.wf-diagram').evaluate((svg) => {
    const bounds = svg.getBoundingClientRect();
    return [...svg.querySelectorAll('text')].filter((text) => {
      const box = text.getBoundingClientRect();
      return box.width && (box.left < bounds.left || box.right > bounds.right ||
        box.top < bounds.top || box.bottom > bounds.bottom);
    }).map((text) => text.textContent);
  });
  expect(clipped).toEqual([]);
  const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
  if (directory) await flow.screenshot({ path: path.join(directory, `${testInfo.project.name}-connected-flow.png`) });
});

test('workbook entrance preserves keyboard navigation and a compact mobile menu', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${pageURL}#overview`);
  const menu = page.locator('[data-mobile-nav-toggle]');
  await menu.click();
  const nav = page.locator('.masthead .nav');
  await expect(nav).toBeVisible();
  await expect(nav.locator('[aria-current="location"]')).toHaveText('Production');
  await nav.locator('a').first().focus();
  await page.keyboard.press('Escape');
  await expect(menu).toBeFocused();
  await expect(nav).toBeHidden();
  const back = page.getByRole('link', { name: 'Back to Production and the decision paths', exact: true });
  await back.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/production.html#effect-authority$/);
  await expect(page.locator('#effect-authority')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/agent-governance.html#overview$/);
  await expect(page.locator('[data-guide-step]')).toHaveCount(5);
  await expect(page.locator('.wf-diagram')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
  if (directory) await page.locator('main').screenshot({
    path: path.join(directory, `${testInfo.project.name}-web-workbook-mobile.png`),
  });
});
