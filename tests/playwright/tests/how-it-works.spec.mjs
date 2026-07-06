// Home "How it works" primer band (docs/index.html).
// Closes two onboarding gaps: (1) a plain-English teach of the four building
// blocks, and (2) a one-glance "who does what" stack — Copilot builds, Foundry
// runs, Foundry governs.
import { test, expect } from '@playwright/test';

const LANDING = '/index.html';

test.describe('home — "how it works" primer band', () => {
  test('renders the three-role stack: Copilot builds, Foundry runs, Foundry governs', async ({ page }) => {
    await page.goto(LANDING);
    const band = page.locator('#how-it-works');
    await band.scrollIntoViewIfNeeded();
    await expect(band).toBeVisible();

    const roles = band.locator('.hiw-role');
    await expect(roles).toHaveCount(3);

    // Role 1 — GitHub Copilot builds
    await expect(roles.nth(0)).toContainText(/Copilot/i);
    await expect(roles.nth(0).locator('.hiw-verb')).toHaveText(/builds/i);
    // Role 2 — Foundry runs
    await expect(roles.nth(1)).toContainText(/Foundry/i);
    await expect(roles.nth(1).locator('.hiw-verb')).toHaveText(/runs/i);
    // Role 3 — Foundry governs
    await expect(roles.nth(2)).toContainText(/Foundry/i);
    await expect(roles.nth(2).locator('.hiw-verb')).toHaveText(/govern/i);

    // The run stage names the Foundry Hosted Agent — the production runtime.
    await expect(roles.nth(1)).toContainText(/Hosted Agent/i);
  });

  test('teaches the four building blocks in plain English', async ({ page }) => {
    await page.goto(LANDING);
    const band = page.locator('#how-it-works');
    await band.scrollIntoViewIfNeeded();

    const defs = band.locator('.hiw-def');
    await expect(defs).toHaveCount(4);

    const terms = (await band.locator('.hiw-def dt').allTextContents())
      .map((t) => t.trim().toLowerCase());
    expect(terms).toEqual(['agent', 'skill', 'tool', 'hosted runtime']);

    // Each block carries a one-line plain-English definition and an icon.
    for (let i = 0; i < 4; i++) {
      await expect(defs.nth(i).locator('dd')).not.toHaveText('');
      await expect(defs.nth(i).locator('svg')).toHaveCount(1);
    }
  });

  test('primer sits between the hero and the reel', async ({ page }) => {
    await page.goto(LANDING);
    const order = await page.evaluate(() => {
      const intro = document.querySelector('.demo-intro');
      const band = document.querySelector('#how-it-works');
      const reel = document.querySelector('#reel');
      if (!intro || !band || !reel) return null;
      const pos = (a, b) =>
        a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
      return {
        bandAfterIntro: Boolean(pos(intro, band)),
        reelAfterBand: Boolean(pos(band, reel))
      };
    });
    expect(order).not.toBeNull();
    expect(order.bandAfterIntro).toBe(true);
    expect(order.reelAfterBand).toBe(true);
  });
});
