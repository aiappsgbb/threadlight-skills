import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

for (const theme of ['light', 'dark']) {
  test(`native Outlook journey is readable and accessible in ${theme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await page.goto('/agent-governance.html');
    await expect(page.locator('html')).not.toHaveAttribute('data-theme');
    await expect(page.locator('.floating-toc')).toHaveCSS('opacity', '1');
    const journey = page.locator('#human-decisions');
    await expect(journey.getByRole('heading', { level: 2 })).toContainText('Decide in Outlook');
    await expect(journey).toContainText('Approve or Reject');
    await expect(journey).toContainText('one-use');
    await expect(journey).toContainText('CIO');
    await expect(journey).toContainText('CISO');
    await expect(journey.getByRole('list', { name: 'Native human approval journey' })
      .locator(':scope > li')).toHaveCount(3);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    const violations = (await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
    const target = page.locator('#effect-authority');
    const link = page.getByRole('link', { name: 'Explore the architecture' });
    await link.focus();
    await expect(link).toBeFocused();
    await link.press('Enter');
    await expect.poll(() => target.evaluate(el => {
      const top = el.getBoundingClientRect().top;
      return top >= 0 && top < 240;
    })).toBe(true);
  });
}
