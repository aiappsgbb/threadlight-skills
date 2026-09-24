import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('fourth policy tab explains evidence variants without enabling a real action', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('[data-governed-flow]');
  await expect(flow.getByRole('tab')).toHaveText(['Allowed', 'Blocked', 'Human review', 'Signed evidence', 'User confirmation']);
  await flow.getByRole('tab', { name: 'Human review', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(flow.getByRole('tab', { name: 'Signed evidence', exact: true })).toBeFocused();
  const choices = flow.getByRole('group', { name: 'Purchase evidence', exact: true });
  await expect(choices.getByRole('radio')).toHaveCount(3);
  for (const choice of ['missing', 'valid', 'changed']) {
    await choices.locator(`input[value="${choice}"]`).check();
    await expect(flow).toHaveAttribute('data-step', '0');
    await expect(flow).toHaveAttribute('data-playing', 'false');
    await expect(flow.locator('[data-flow-evidence]')).toContainText('Policy example: corroborate purchase and amount');
    await flow.getByRole('button', { name: 'Step 2: Verify purchase evidence', exact: true }).click();
    await expect(flow.locator('[data-flow-actor]')).toHaveText('Evidence Provider');
    await flow.getByRole('button', { name: 'Step 3: Present proof with the action', exact: true }).click();
    await expect(flow.locator('[data-flow-actor]')).toHaveText('Agent');
    await expect(flow.locator('[data-flow-action]')).toContainText('action');
    await expect(flow.locator('[data-evidence-participant="agent"]')).toHaveCount(1);
    const returnArrow = flow.locator('[data-evidence-message="return"]');
    const callArrow = flow.locator('[data-evidence-message="invoke"]');
    await expect(returnArrow).toHaveAttribute('data-message-to', 'agent');
    await expect(callArrow).toHaveAttribute('data-message-from', 'agent');
    const geometry = await flow.locator('[data-evidence-message] line').evaluateAll(lines =>
      lines.map(line => ['x1', 'y1', 'x2', 'y2'].map(name => Number(line.getAttribute(name)))));
    expect(geometry).toEqual([[60, 190, 60, 83], [190, 78, 190, 185], [220, 224, 425, 224], [630, 224, 845, 224]]);
    await expect(returnArrow).toContainText(choice === 'missing' ? 'No proof' : 'Expiring proof');
    await expect(callArrow).toContainText(choice === 'missing' ? 'without proof' : '+ proof');
    await expect(flow.locator('[data-evidence-participant="agent"]')).toContainText(choice === 'missing' ? 'no proof' : 'proof');
    await expect(flow.locator('[data-evidence-sequence]')).not.toContainText(/JWT|HTTPS|MCP|returns_apply_decision|fingerprint/i);
    await expect(flow.locator('.wf-mobile')).not.toContainText(/JWT|HTTPS|MCP|returns_apply_decision|_meta|fingerprint/i);
    await flow.locator('[data-flow-progress] button').last().click();
    await expect(flow).toHaveAttribute('data-node', choice === 'valid' ? 'result' : 'deny');
    await expect(flow.locator('[data-flow-node="effect"]')).toHaveAttribute('data-used', String(choice === 'valid'));
    await expect(flow.locator('[data-flow-case-note]')).toContainText(/not.*certif/i);
  }
  await flow.getByRole('tab', { name: 'Allowed', exact: true }).click();
  await expect(flow.locator('[data-flow-evidence]')).toBeHidden();
  await expect(flow.locator('[data-evidence-sequence]')).toBeHidden();
  await expect(flow.locator('.wf-diagram')).toHaveAttribute('viewBox', '0 0 1100 215');
  await flow.getByRole('tab', { name: 'Signed evidence', exact: true }).click();
  await choices.getByRole('radio', { name: 'Valid proof', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(choices.getByRole('radio', { name: 'Missing proof', exact: true })).toBeChecked();
  await choices.getByRole('radio', { name: 'Valid proof', exact: true }).check();
  const jwt = flow.locator('[data-evidence-jwt]');
  await expect(jwt).toContainText('JSON Web Token');
  await jwt.getByText('Inspect the JWT contract and MCP request', { exact: true }).click();
  await expect(jwt.locator('pre')).toContainText('"threadlight/evidence"');
  await expect(jwt).toContainText('X-Evidence-Fingerprint');
  await expect(jwt.locator('details')).toHaveAttribute('open', '');
  expect(await jwt.evaluate(node => node.scrollWidth > node.clientWidth + 1)).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  expect((await new AxeBuilder({ page }).include('#workflow-in-action')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
});

test('signed-evidence fragment selects the right scenario and leaves a no-JS explanation', async ({ page, browser, baseURL }) => {
  await page.goto('/production.html#production-input-proof');
  await expect(page.locator('[data-governed-flow]')).toHaveAttribute('data-scenario', 'evidence');
  await expect(page.getByRole('group', { name: 'Purchase evidence', exact: true })).toBeVisible();
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

test('evidence exchanges remain readable at intermediate widths and 200 percent zoom equivalent', async ({ page }) => {
  for (const width of [1280, 1024, 768, 640, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/production.html#production-input-proof');
    const diagram = page.locator('.wf-diagram');
    if (await diagram.isVisible()) {
      const renderedSize = await diagram.evaluate(svg =>
        parseFloat(getComputedStyle(svg.querySelector('.wf-message text')).fontSize)
          * svg.getBoundingClientRect().width / 1100);
      expect(renderedSize).toBeGreaterThanOrEqual(12);
    } else {
      await expect(page.locator('.wf-mobile')).toBeVisible();
      await expect(page.locator('.wf-mobile')).toContainText('2. Evidence Provider → Agent');
      await expect(page.locator('.wf-mobile')).toContainText('3. Agent → Gateway');
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  }
});

test('business proof explains checked facts and never fabricates a certificate for the missing case', async ({ page }) => {
  await page.goto('/production.html#production-input-proof');
  const flow = page.locator('[data-governed-flow]');
  const proof = flow.locator('[data-flow-proof]');
  await expect(proof).toBeVisible();
  await expect(proof).toContainText('customer');
  await expect(proof).toContainText('return-window');
  await expect(proof).toContainText('not implemented');
  await expect(proof).toContainText('Loan fixture');
  await expect(proof.locator('[data-proof-certified]')).toBeVisible();
  await flow.getByRole('radio', { name: 'Missing proof', exact: true }).check();
  await expect(proof.locator('[data-proof-certified]')).toBeHidden();
  await expect(proof.locator('[data-proof-unavailable]')).toBeVisible();
  await expect(proof.locator('[data-proof-unavailable]')).toContainText('No certificate');
  await flow.getByRole('radio', { name: 'Changed inputs', exact: true }).check();
  await expect(proof.locator('[data-proof-certified]')).toContainText('r7');
  await expect(flow.locator('[data-flow-story]')).toContainText('r8');
  await flow.getByRole('tab', { name: 'Allowed', exact: true }).click();
  await expect(proof).toBeHidden();
});

test('human permission remains pending until review, then is consumed once with its original deadline', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html#workflow-in-action');
  const flow = page.locator('[data-governed-flow]');
  await flow.getByRole('tab', { name: 'Human review', exact: true }).click();
  const approval = flow.locator('[data-flow-approval]');
  await expect(approval).toBeVisible();
  await expect(approval).toContainText('not a JWT');
  await expect(approval.locator('[data-approval-state]')).toHaveText('Not issued — waiting for the person');
  await flow.getByRole('button', { name: 'Step 4: Person decides in Outlook', exact: true }).click();
  await flow.getByRole('button', { name: 'Show approved example', exact: true }).click();
  await expect(approval.locator('[data-approval-state]')).toHaveText('Granted — same 10:15 deadline');
  await flow.getByRole('button', { name: 'Next step', exact: true }).click();
  await expect(approval.locator('[data-approval-state]')).toHaveText('Consumed — cannot authorize another write');
  await flow.locator('[data-flow-progress] button').last().click();
  await expect(approval.locator('[data-approval-state]')).toContainText('recorded result');
  await flow.getByRole('tab', { name: 'Signed evidence', exact: true }).click();
  await expect(approval).toBeHidden();
});

test('the return example and diagram precede optional architecture detail', async ({ page }) => {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/production.html?layout-width=${width}#effect-authority`);
    const detail = page.locator('[data-action-implementation]');
    await expect(detail).not.toHaveAttribute('open', '');
    const positions = await page.locator('#effect-authority').evaluate(section => ({
      heading: section.querySelector('h2').getBoundingClientRect().bottom,
      tabs: section.querySelector('.wf-panel').getBoundingClientRect().top,
      technical: section.querySelector('[data-action-implementation]').getBoundingClientRect().top,
      scenarioEnd: section.querySelector('#workflow-in-action').getBoundingClientRect().bottom,
    }));
    expect(positions.tabs - positions.heading).toBeLessThan(width > 700 ? 260 : 380);
    expect(positions.technical).toBeGreaterThanOrEqual(positions.scenarioEnd);
    await detail.locator('summary').click();
    await expect(detail.locator('[data-action-primary]')).toBeVisible();
    expect((await new AxeBuilder({ page }).include('#effect-authority')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
  }
});
