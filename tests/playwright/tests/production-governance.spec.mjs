import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { homeHistory } from '../../blueprint/helpers/home-history.js';

async function capture(locator, name, testInfo) {
  const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
  if (!directory) return;
  if (!path.isAbsolute(directory)) throw new Error('Screenshot artifacts need an absolute external directory');
  await mkdir(directory, { recursive: true });
  await locator.screenshot({
    path: path.join(directory, `${testInfo.project.name}-${name}.png`),
    animations: 'disabled',
  });
}

for (const name of ['index', 'funnel', 'production']) {
  test(`${name}: scoped copy, responsive chapter navigation and no overflow`, async ({ page }, testInfo) => {
    await page.goto(`/${name}.html`);
    await expect(page.locator('header.masthead .brand-name')).toContainText('Threadlight');
    await expect(page.locator('header nav.nav a')).toHaveCount(5);
    const toggle = page.locator('[data-mobile-nav-toggle]');
    if (await toggle.isVisible()) {
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    }
    await page.locator('header nav.nav a[href="./production.html"]').click();
    await expect(page).toHaveURL(/production\.html$/);
    await expect(page.locator('header nav.nav a[aria-current="page"]')).toHaveText('Production');
    await page.goto(`/${name}.html`);
    const html = await page.locator('main').innerHTML();
    const history = name === 'index' ? homeHistory(html) : { current: html };
    if (name === 'index') {
      expect(history.snapshots).toHaveLength(3);
      expect(history.current).toContain('Historical demo captions are not current guarantees');
    }
    const currentCopy = history.current.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&');
    expect(currentCopy).not.toMatch(/Foundry runs & governs it|ship with (?:two|2) waivers|ready in ~7m|92[–/]100/);
    const overflowing = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(overflowing, `${name} horizontal overflow`).toBe(false);
    await capture(page.locator('body'), name, testInfo);
  });
}

test('authority and evidence are named, readable without a diagram runtime and keyboard-linked', async ({ page }, testInfo) => {
  await page.goto('/production.html#effect-authority');
  const authority = page.locator('#effect-authority');
  const evidence = page.locator('#evidence-boundaries');
  await expect(authority.getByRole('heading', { level: 2 })).toContainText('return decision');
  await authority.locator('[data-action-implementation] > summary').click();
  await expect(authority.getByRole('list', { name: 'Primary business-action path' }).locator(':scope > li')).toHaveCount(3);
  await expect(authority.getByRole('list', { name: 'Supporting authority services' }).locator(':scope > li')).toHaveCount(2);
  if (page.viewportSize().width <= 700) {
    await expect(authority.locator('.wf-mobile')).toBeVisible();
    await expect(authority.locator('.wf-diagram')).toBeHidden();
  } else {
    await expect(authority.getByRole('img', { name: 'Checks required before a selected business action' })).toBeVisible();
  }
  await expect(evidence.getByRole('link', { name: /Components and trust boundaries/ })).toHaveAttribute('href', /agent-governance-deep-dive\.md#4-architecture-and-trust-boundaries$/);
  await expect(page.locator('main details:not([data-evidence-jwt] details):not([data-action-implementation])')).toHaveCount(0);
  await expect(evidence.getByRole('link', { name: 'Try the guided workbook', exact: true })).toBeVisible();
  await expect(evidence).toContainText('independently verified evidence');
  await expect(evidence).not.toContainText(/HISTORICAL|NOT PROVED|EXPIRED|4\/4|2026-09-/);
  await expect(evidence.locator('a[href*="governed-returns-validation.md"]')).toBeVisible();
  for (const section of [authority, evidence]) {
    await section.scrollIntoViewIfNeeded();
    await expect(section).toBeVisible();
    await expect(page.locator('[data-topic-tab][href="#effect-authority"]')).toHaveAttribute('aria-selected', 'true');
    const violations = (await new AxeBuilder({ page }).include(`#${await section.getAttribute('id')}`)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
  }
  const next = page.locator('[data-area-navigation="actions-topic"] a[href="#evidence-boundaries"]');
  await next.focus();
  await expect(next).toBeFocused();
  await next.press('Enter');
  await expect(page).toHaveURL(/#evidence-boundaries$/);
  await expect.poll(() => evidence.evaluate((element) => {
    const top = element.getBoundingClientRect().top;
    const offset = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop);
    const atEnd = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
    return top >= offset - 2 && (Math.abs(top - offset) < 2 || atEnd);
  })).toBe(true);
  await capture(authority, 'effect-authority', testInfo);
  await capture(evidence, 'evidence-boundaries', testInfo);
});

test('new authority and evidence remain readable when JavaScript is disabled', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport, baseURL: testInfo.project.use.baseURL,
  });
  const page = await context.newPage();
  try {
    await page.goto('/production.html');
    await expect(page.locator('#effect-authority')).toBeVisible();
    await expect(page.locator('[data-topic-panel]')).toHaveCount(3);
    await expect(page.locator('main details:not([data-evidence-jwt] details):not([data-action-implementation])')).toHaveCount(0);
    await page.locator('[data-action-implementation] > summary').click();
    await expect(page.locator('#evidence-boundaries')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Primary business-action path' }).locator(':scope > li')).toHaveCount(3);
    await expect(page.getByRole('list', { name: 'Supporting authority services' }).locator(':scope > li')).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});

test('production responsibilities are distinct, navigable and accessible before the action deep dive', async ({ page }, testInfo) => {
  await page.goto('/production.html');
  const domains = page.locator('#production-domains');
  await expect(page.locator('main > section').first()).toHaveAttribute('id', 'chapter-top');
  const links = domains.locator('.production-map').getByRole('link');
  await expect(links).toHaveCount(3);
  for (const link of await links.all()) {
    const target = page.locator(await link.getAttribute('href'));
    await expect(target).toHaveCount(1);
    const heading = target.getByRole('heading').first();
    await link.focus();
    await link.press('Enter');
    await expect.poll(() => heading.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return hit !== null && element.contains(hit);
    })).toBe(true);
  }
  const order = await page.evaluate(() => {
    const domains = document.querySelector('#production-domains');
    return Boolean(domains.compareDocumentPosition(document.querySelector('#effect-authority'))
      & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(order).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  const violations = (await new AxeBuilder({ page }).include('#production-domains')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
  expect(violations).toEqual([]);
  await capture(domains, 'production-responsibilities', testInfo);
});

test('topic navigation does not cover section introductions when it resizes', async ({ page }) => {
  for (const width of [1440, 768, 600, 412]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const id of ['production-domains', 'effect-authority', 'evidence-boundaries']) {
      await page.goto(`/production.html#${id}`);
      await expect.poll(() => page.locator(`#${id} h2, #${id} h3`).first().evaluate(element => {
        const rect = element.getBoundingClientRect();
        const bars = ['.masthead', '.topic-navigation'].map(selector => document.querySelector(selector))
          .filter(bar => {
            const box = bar.getBoundingClientRect();
            return getComputedStyle(bar).display !== 'none' && box.top < rect.bottom
              && box.left < rect.right && box.right > rect.left;
          });
        return rect.top >= Math.max(...bars.map(bar => bar.getBoundingClientRect().bottom)) + 8;
      }), { timeout: 1000 }).toBe(true);
    }
  }
});
