#!/usr/bin/env node
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'tests/playwright/package.json'));
const { chromium } = require('@playwright/test');
const renderer = path.join(path.dirname(require.resolve('mermaid')), 'mermaid.min.js');
const documents = ['agent-governance-deep-dive', 'native-outlook-approval-architecture'];
const validateOnly = process.argv.includes('--validate');
const check = process.argv.includes('--check');
const diagramOption = process.argv.indexOf('--diagram');
const selectedDiagram = diagramOption < 0 ? null : process.argv[diagramOption + 1];
if (diagramOption >= 0 && !/^[a-z0-9-]+$/.test(selectedDiagram || '')) {
  throw new Error('--diagram requires a declared diagram name');
}
const directory = path.join(root, 'docs/assets/governance');
let selectedCount = 0;
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.setContent('<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body></body></html>');
  await page.addScriptTag({ path: renderer });
  if (!validateOnly && !check) await mkdir(directory, { recursive: true });
  for (const document of documents) {
    const markdown = await readFile(path.join(root, `docs/${document}.md`), 'utf8');
    const blocks = [...markdown.matchAll(/(?:<!-- diagram: ([a-z0-9-]+) -->\s*)?```mermaid\n([\s\S]*?)\n```/g)];
    if (!blocks.length) throw new Error(`No diagrams in ${document}`);
    let renderedCount = 0;
    for (const [index, [, declaredName, source]] of blocks.entries()) {
      if (!declaredName && !validateOnly) throw new Error(`Missing diagram name in ${document}`);
      const name = declaredName || `${document}-${index}`;
      if (selectedDiagram && name !== selectedDiagram) continue;
      selectedCount += 1;
      renderedCount += 1;
      const svg = await page.evaluate(async ({ name, source }) => {
        mermaid.initialize({
          startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
          deterministicIds: true, deterministicIDSeed: name, theme: 'neutral',
          htmlLabels: false, fontFamily: 'Arial, sans-serif',
          flowchart: { htmlLabels: false, curve: 'linear', useMaxWidth: false },
          sequence: { useMaxWidth: false, wrap: true },
        });
        await mermaid.parse(source);
        const rendered = (await mermaid.render(name, source)).svg;
        const document = new DOMParser().parseFromString(rendered, 'image/svg+xml');
        document.documentElement.style.backgroundColor = 'white';
        return new XMLSerializer().serializeToString(document);
      }, { name, source });
      if (/<script|<foreignObject|Syntax error in text|mermaid-error/i.test(svg)) {
        throw new Error(`Unsafe or failed SVG output: ${name}`);
      }
      if (validateOnly) continue;
      const filename = path.join(directory, `${name}.svg`);
      const output = `${svg}\n`;
      if (check) {
        if (await readFile(filename, 'utf8') !== output) {
          throw new Error(`Stale diagram: ${filename}; run node scripts/render-governance-diagrams.mjs`);
        }
      } else {
        await writeFile(filename, output);
      }
    }
    if (renderedCount) console.log(`${check ? 'Checked' : validateOnly ? 'Validated' : 'Rendered'} ${document}: ${renderedCount} diagrams`);
  }
  if (selectedDiagram && !selectedCount) throw new Error(`Unknown diagram: ${selectedDiagram}`);
} finally {
  await browser.close();
}
