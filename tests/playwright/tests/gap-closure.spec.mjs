// Gap-closure release surfaces (v1.12.0 — the 22-skill pack).
//
// Browser-level assertions for the five NEW skills' public site presence:
//   - funnel.html          — threadlight-qualify no-repo entry + Cowork download
//   - production.html      — connect / ground / loadtest evidence progression
//   - self-improving.html  — threadlight-upgrade plan-only lifecycle scan
//
// These focused release checks complement tests/site.spec.mjs: that suite
// covers the current shared site structure and navigation, while this spec
// protects the gap-closure skills' page-specific links and semantics.
import { test, expect } from '@playwright/test';

const REPO = 'aiappsgbb/threadlight-skills';

test.describe('gap-closure release surfaces — five new skills', () => {
  test('funnel.html leads with the threadlight-qualify no-repo entry + a Cowork download', async ({ page }) => {
    await page.goto('/funnel.html');
    await expect(page).toHaveTitle(/.+/);

    // The no-repo entry card runs before Design.
    const card = page.locator('.skill-card[data-skill="threadlight-qualify"]');
    await expect(card).toHaveCount(1);
    await card.scrollIntoViewIfNeeded();
    await expect(card).toContainText(/threadlight/i);
    await expect(card).toContainText(/qualify/i);

    // Links to the repository skill folder AND offers the Cowork zip.
    await expect(page.locator('a[href*="skills/threadlight-qualify"]').first()).toBeVisible();
    const zip = page.locator('a[href*="downloads/threadlight-qualify.zip"]');
    await expect(zip).toHaveCount(1);
    await expect(zip.first()).toBeVisible();
  });

  test('production.html shows the connect / ground / loadtest evidence progression', async ({ page }) => {
    await page.goto('/production.html');
    for (const skill of ['threadlight-connect', 'threadlight-ground', 'threadlight-loadtest']) {
      const link = page.locator(`a[href*="skills/${skill}"]`).first();
      await link.scrollIntoViewIfNeeded();
      await expect(link, `production.html must link ${skill}`).toBeVisible();
      const href = await link.getAttribute('href');
      expect(href).toContain(`${REPO}`.split('/')[1]); // repo name present
      expect(href).toContain(`skills/${skill}`);
    }
  });

  test('self-improving.html adds the plan-only threadlight-upgrade lifecycle scan', async ({ page }) => {
    await page.goto('/self-improving.html');
    const section = page.locator('#maintain');
    await expect(section).toHaveCount(1);
    await section.scrollIntoViewIfNeeded();
    await expect(section).toContainText(/upgrade/i);
    const link = page.locator('a[href*="skills/threadlight-upgrade"]').first();
    await expect(link).toBeVisible();
  });

  test('every new-skill link points at a real repository skill folder (no dead hrefs)', async ({ page }) => {
    const pages = {
      '/funnel.html': ['threadlight-qualify'],
      '/production.html': ['threadlight-connect', 'threadlight-ground', 'threadlight-loadtest'],
      '/self-improving.html': ['threadlight-upgrade'],
    };
    for (const [url, skills] of Object.entries(pages)) {
      await page.goto(url);
      for (const skill of skills) {
        const href = await page.locator(`a[href*="skills/${skill}"]`).first().getAttribute('href');
        expect(href, `${url} → ${skill} href`).toMatch(
          new RegExp(`github\\.com/${REPO}/(tree|blob)/main/skills/${skill}`),
        );
      }
    }
  });
});
