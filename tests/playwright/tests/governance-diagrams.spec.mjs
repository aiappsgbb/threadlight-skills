import { test, expect } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const documents = ['agent-governance-deep-dive', 'native-outlook-approval-architecture'];
const names = documents.flatMap(document => {
  const markdown = readFileSync(new URL(`../../../docs/${document}.md`, import.meta.url), 'utf8');
  return [...markdown.matchAll(/<!-- diagram: ([a-z0-9-]+) -->/g)].map(match => match[1]);
});

test('committed report diagrams render without clipped text or a Mermaid runtime', async ({ page }, testInfo) => {
  expect(names).toHaveLength(9);
  expect(new Set(names).size).toBe(9);
  expect(names).toEqual(expect.arrayContaining([
    'effect-boundaries', 'action-execution', 'human-resume', 'operation-ledger',
    'approval-ledger', 'outlook-boundaries', 'outlook-execution', 'outlook-outbox',
    'requesting-user-confirmation',
  ]));
  for (const name of names) {
    const response = await page.goto(`/assets/governance/${name}.svg`);
    expect(response.ok(), name).toBe(true);
    const image = page.locator('svg').first();
    await expect(image).toBeVisible();
    await expect(image).toHaveCSS('background-color', 'rgb(255, 255, 255)');
    const clipped = await image.evaluate(svg => {
      const boundary = svg.getBoundingClientRect();
      return [...svg.querySelectorAll('text')].filter(text => {
        const box = text.getBoundingClientRect();
        return box.width && box.height && (box.left < boundary.left - 1 ||
          box.top < boundary.top - 1 || box.right > boundary.right + 1 || box.bottom > boundary.bottom + 1);
      }).map(text => text.textContent);
    });
    expect(clipped, name).toEqual([]);
    const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
    if (directory && testInfo.project.name === 'chromium-desktop') {
      if (!path.isAbsolute(directory)) throw new Error('Screenshots need an absolute external directory');
      mkdirSync(directory, { recursive: true });
      await image.screenshot({ path: path.join(directory, `${name}.png`) });
    }
  }
});
