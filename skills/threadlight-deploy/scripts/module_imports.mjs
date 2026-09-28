// Parse only: module code is never linked or evaluated.
import { readFileSync } from 'node:fs';
import { init, parse } from 'es-module-lexer';

const source = readFileSync(0, 'utf8');
await init;
const [imports] = parse(source);
const dependencies = [];
for (const entry of imports) {
  if (entry.d === -2) continue; // import.meta is not a dependency.
  if (entry.n === undefined) {
    throw new Error('Computed dynamic imports require an explicit bundled build before verification');
  }
  dependencies.push(entry.n);
}
process.stdout.write(JSON.stringify(dependencies));
