# Interactive Composer ("Blueprint") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `docs/blueprint.html` — a client-side on-ramp where a reader picks a real scenario (89-template library, 15 industries) or describes their own, and gets a copy-paste Copilot prompt + a *derived* skill sequence + an `azd up` quickstart + an illustrative artifact tree.

**Architecture:** Static page on the existing GitHub Pages site. A one-time offline Python producer sanitises an internal `process_library.json` into a committed `docs/assets/process-library.json` (field whitelist + narrow leak scrub). A UMD pure-logic module (`blueprint-logic.js`) computes the derived skills + prompt (node-testable). A DOM controller (`blueprint.js`) renders the UI and wires clipboard. Classic `<script>` includes with `?v=` cache-bust tokens, matching `site.js`/`demo-reel.js`.

**Tech Stack:** Vanilla JS (no build step), Python stdlib (producer), `node --test` (unit + data guard), Playwright (local e2e), existing `site.css` design tokens.

**Scrub policy (critical):**
- **Data asset** (third-party templates) → NARROW leak scrub only: `/agentic[- ]?loop/i`, `threadlight-vnext`, internal infra tokens (`northcentralus`, `remote-gw`, `gpt-5.1`). Business words (`competitive`, `confidential`, `compliance`) are LEGIT and must pass.
- **My prose** (page copy, spec, plan) → full session regex.
- Field whitelist drops `pregenerated_job_id` + any non-whitelisted field.

---

## File structure

**New:**
- `scripts/build_process_library.py` — offline producer: source JSON → scrubbed committed asset.
- `docs/assets/process-library.json` — committed scrubbed asset (~89 entries).
- `docs/assets/blueprint-logic.js` — UMD pure logic: `PIPELINE_ARC`, `SKILL_MAP`, `deriveSkills(p)`, `buildPrompt(p)`, `buildAzd(p)`.
- `docs/assets/blueprint.js` — DOM controller: fetch, render cards/filters/form/outputs, clipboard, reveal.
- `docs/blueprint.html` — the page (masthead+nav, hero, Pick/Describe tabs, results, artifact tree, footer).
- `tests/blueprint/blueprint-logic.test.js` — node:test unit tests for the logic.
- `tests/blueprint/process-library.test.js` — node:test data guard (fields, industries, leak scrub).
- `.github/workflows/docs-blueprint.yml` — CI: `node --test tests/blueprint/`.
- `tests/playwright/tests/blueprint.spec.mjs` — local e2e (page loads, cards render, pick→outputs, copy).

**Modified:**
- 8 × `docs/*.html` (index, funnel, production, self-improving, customize, industries, case-study, workbook) — insert Blueprint nav link after Home.
- `docs/assets/site.css` — composer component styles (tabs, card grid, filter pills, skill chips, output blocks, artifact tree).
- `docs/ci/sync_cache_bust.py` — add `blueprint-logic.js` + `blueprint.js` to `ASSETS`.
- All `docs/*.html` — `?v=` tokens refreshed by `sync_cache_bust.py --write` after css/js edits.

---

## Task 1: Offline producer + committed data asset

**Files:**
- Create: `scripts/build_process_library.py`
- Create (output): `docs/assets/process-library.json`

- [ ] **Step 1: Write the producer.** Pure stdlib. Reads `--source <path>`, writes `--out docs/assets/process-library.json`.

```python
#!/usr/bin/env python3
"""Sanitise an internal process_library.json into the committed static asset.

The raw source is NOT committed. This producer keeps only a whitelist of
presentation fields and asserts the output carries no supply-chain leak
markers. Business vocabulary (competitive/confidential/compliance) is LEGIT
third-party content and is deliberately NOT scrubbed.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

KEEP = ["id", "name", "industry", "complexity", "summary", "description",
        "tags", "business_constraints", "external_integrations",
        "human_approvals", "knowledge_sources"]
LEAK = re.compile(r"agentic[- ]?loop|threadlight-vnext|northcentralus|remote-gw|gpt-5\.1", re.I)

def sanitise(entry: dict) -> dict:
    return {k: entry.get(k) for k in KEEP if k in entry}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", default="docs/assets/process-library.json")
    a = ap.parse_args()
    raw = json.loads(Path(a.source).read_text(encoding="utf-8"))
    out = [sanitise(e) for e in raw]
    blob = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=False)
    hits = LEAK.findall(blob)
    if hits:
        print(f"LEAK markers in output: {sorted(set(h.lower() for h in hits))}", file=sys.stderr)
        return 1
    Path(a.out).write_text(blob + "\n", encoding="utf-8")
    print(f"wrote {len(out)} entries -> {a.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it** against the out-of-band source fetched to `/tmp/process_library.json`.

Run: `python3 scripts/build_process_library.py --source /tmp/process_library.json --out docs/assets/process-library.json`
Expected: `wrote 89 entries -> docs/assets/process-library.json`

- [ ] **Step 3: Sanity-check the asset.**

Run: `python3 -c "import json;d=json.load(open('docs/assets/process-library.json'));print(len(d), sorted({e['industry'] for e in d}))"`
Expected: `89` + 15 industry slugs, and NO `pregenerated_job_id` anywhere.

---

## Task 2: Data-guard test (node:test)

**Files:**
- Create: `tests/blueprint/process-library.test.js`

- [ ] **Step 1: Write the guard.**

```js
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const data = JSON.parse(fs.readFileSync(
  path.join(__dirname, '../../docs/assets/process-library.json'), 'utf8'));
const REQUIRED = ['id','name','industry','complexity','summary','description','tags'];
const INTERNAL = ['pregenerated_job_id'];
const LEAK = /agentic[- ]?loop|threadlight-vnext|northcentralus|remote-gw|gpt-5\.1/i;

test('library is a non-empty array', () => {
  assert.ok(Array.isArray(data) && data.length > 0);
});
test('every entry has required fields + valid complexity', () => {
  for (const e of data) {
    for (const k of REQUIRED) assert.ok(e[k] != null, `${e.id} missing ${k}`);
    assert.ok(['low','medium','high'].includes(e.complexity), `${e.id} bad complexity`);
  }
});
test('no internal fields survive the whitelist', () => {
  for (const e of data) for (const k of INTERNAL)
    assert.ok(!(k in e), `${e.id} leaked ${k}`);
});
test('no supply-chain leak markers', () => {
  assert.ok(!LEAK.test(JSON.stringify(data)));
});
```

- [ ] **Step 2: Run it.**

Run: `node --test tests/blueprint/process-library.test.js`
Expected: 4 tests pass.

---

## Task 3: Pure logic module + unit tests (TDD)

**Files:**
- Create: `docs/assets/blueprint-logic.js`
- Test: `tests/blueprint/blueprint-logic.test.js`

- [ ] **Step 1: Write failing unit tests first.**

```js
const { test } = require('node:test');
const assert = require('node:assert');
const L = require('../../docs/assets/blueprint-logic.js');

const base = { name:'X', summary:'do X', industry:'retail', complexity:'low',
  business_constraints:[], external_integrations:[], human_approvals:[], knowledge_sources:[] };

test('baseline arc always present', () => {
  const s = L.deriveSkills(base);
  ['threadlight-design','threadlight-local-test','threadlight-safe-check',
   'threadlight-deploy','threadlight-evals'].forEach(k => assert.ok(s.includes(k)));
});
test('integrations add demo-data-factory', () => {
  assert.ok(L.deriveSkills({...base, external_integrations:[{name:'SAP'}]})
    .includes('threadlight-demo-data-factory'));
});
test('approvals add hitl-patterns', () => {
  assert.ok(L.deriveSkills({...base, human_approvals:[{step:'review'}]})
    .includes('threadlight-hitl-patterns'));
});
test('high complexity adds production/govern/redteam', () => {
  const s = L.deriveSkills({...base, complexity:'high'});
  ['threadlight-production-ready','threadlight-govern','threadlight-redteam']
    .forEach(k => assert.ok(s.includes(k)));
});
test('buildPrompt embeds name + threadlight-auto', () => {
  const p = L.buildPrompt(base);
  assert.ok(p.includes('threadlight-auto') && p.includes('do X'));
});
test('buildAzd returns azd up quickstart', () => {
  assert.ok(L.buildAzd(base).includes('azd up'));
});
```

- [ ] **Step 2: Run — expect FAIL** (module not found / exports undefined).

Run: `node --test tests/blueprint/blueprint-logic.test.js`

- [ ] **Step 3: Implement `blueprint-logic.js`** (UMD; browser sets `window.TL_BLUEPRINT`, node sets `module.exports`).

```js
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.TL_BLUEPRINT = api;
})(typeof self !== 'undefined' ? self : this, function () {
  const PIPELINE_ARC = ['threadlight-design','threadlight-local-test',
    'threadlight-safe-check','threadlight-deploy','threadlight-evals'];

  function deriveSkills(p) {
    const s = [...PIPELINE_ARC];
    const add = k => { if (!s.includes(k)) s.push(k); };
    if ((p.external_integrations||[]).length) add('threadlight-demo-data-factory');
    if ((p.human_approvals||[]).length) add('threadlight-hitl-patterns');
    const tags = (p.tags||[]).join(' ').toLowerCase();
    if (/event|trigger|schedule|webhook/.test(tags)) add('threadlight-event-triggers');
    if (p.complexity === 'high') {
      add('threadlight-production-ready'); add('threadlight-govern'); add('threadlight-redteam');
    }
    if (/regulat|complian|risk|financ|pharma|health|insur|govern/.test(
        (p.industry||'') + ' ' + tags)) add('threadlight-consumption-iq');
    return s;
  }

  function buildPrompt(p) {
    const integ = (p.external_integrations||[]).map(i => i.name || i).filter(Boolean);
    const appr  = (p.human_approvals||[]).map(a => a.step || a.name || a).filter(Boolean);
    const cons  = (p.business_constraints||[]).map(c => c.name || c).filter(Boolean);
    return [
      'Use threadlight-auto to build a production-grade agent for this process:',
      '',
      `  ${p.name} — ${p.summary || ''}`.trimEnd(),
      '',
      `  Industry: ${p.industry || 'general'}`,
      integ.length ? `  Key integrations: ${integ.join(', ')}` : '',
      appr.length  ? `  Human approvals: ${appr.join(', ')}` : '',
      cons.length  ? `  Constraints: ${cons.join('; ')}` : '',
      '',
      'Take it through design → local-test → safe-check → deploy → evals,',
      'and stop for my review before anything touches my subscription.'
    ].filter(l => l !== '').join('\n');
  }

  function buildAzd() {
    return ['# once Copilot has scaffolded the project:',
            'azd auth login', 'azd up'].join('\n');
  }

  return { PIPELINE_ARC, deriveSkills, buildPrompt, buildAzd };
});
```

- [ ] **Step 4: Run — expect PASS.**

Run: `node --test tests/blueprint/blueprint-logic.test.js`
Expected: 6 tests pass.

- [ ] **Step 5: Commit Tasks 1-3.**

```bash
git add scripts/build_process_library.py docs/assets/process-library.json \
        docs/assets/blueprint-logic.js tests/blueprint/
git commit -m "feat(blueprint): scrubbed scenario library + derived-skill logic + tests"
```

---

## Task 4: CI workflow

**Files:**
- Create: `.github/workflows/docs-blueprint.yml`

- [ ] **Step 1: Write workflow.**

```yaml
name: docs-blueprint
on:
  pull_request:
    branches: [main]
    paths: ["docs/assets/blueprint-logic.js", "docs/assets/process-library.json", "tests/blueprint/**"]
  push:
    branches: [main]
    paths: ["docs/assets/blueprint-logic.js", "docs/assets/process-library.json", "tests/blueprint/**"]
  workflow_dispatch: {}
jobs:
  blueprint-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - name: Blueprint logic + data-guard unit tests
        run: node --test tests/blueprint/
```

- [ ] **Step 2: Run the suite locally.** `node --test tests/blueprint/` → all pass.
- [ ] **Step 3: Commit.** `git add .github/workflows/docs-blueprint.yml && git commit -m "ci(blueprint): node unit + data-guard tests"`

---

## Task 5: Page shell `blueprint.html` + nav on all pages

**Files:**
- Create: `docs/blueprint.html`
- Modify: 8 × `docs/*.html` (nav)

- [ ] **Step 1: Build `docs/blueprint.html`** replicating index's `<head>` (title/OG/theme/favicon/`site.css?v=`), `<header class="masthead">` + full 8+1 nav (Blueprint = `aria-current="page"`), `<main>` with sections: hero (`.wrap`, eyebrow/h1/sub, `.reveal`), a **tabs** control (Pick / Describe), `#pick` card-grid + `#filters` (search + 15 industry pills + complexity), `#describe` form (industry select, one-liner, systems chips, approvals toggle), `#result` with 4 output blocks (`#out-prompt`, `#out-skills`, `#out-azd`, `#out-tree`), then the shared `<footer>` and classic scripts: `assets/site.js?v=`, `assets/blueprint-logic.js?v=`, `assets/blueprint.js?v=`.

- [ ] **Step 2: Insert Blueprint nav link after the Home link in all 8 pages.** Use a scripted insert (idempotent) so all navs stay identical:

```bash
python3 - <<'PY'
import re, pathlib
LINK = ('    <a href="./blueprint.html"><svg class="nav-ico" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="7" rx="1"/>'
        '<rect x="3" y="14" width="9" height="7" rx="1"/><rect x="16" y="14" width="5" height="7" rx="1"/>'
        '</svg>Blueprint</a>\n')
for f in pathlib.Path("docs").glob("*.html"):
    if f.name == "blueprint.html": continue
    t = f.read_text(encoding="utf-8")
    if "blueprint.html" in t: continue          # idempotent
    t2 = re.sub(r'(<a href="\./index\.html"[^>]*>.*?Home</a>\n)', r'\1'+LINK, t, count=1, flags=re.S)
    if t2 != t: f.write_text(t2, encoding="utf-8"); print("navi", f.name)
PY
```

- [ ] **Step 3: Verify** every page now has exactly one Blueprint link: `grep -c blueprint.html docs/*.html`.
- [ ] **Step 4: Commit** the shell + nav (CSS/JS come next). `git commit -m "feat(blueprint): page shell + site-wide nav link"`

---

## Task 6: Composer styles + cache-bust registration

**Files:**
- Modify: `docs/assets/site.css`
- Modify: `docs/ci/sync_cache_bust.py`

- [ ] **Step 1: Add component styles** to `site.css` reusing existing tokens (spacing, radius, brand/accent colors, `.reveal`): `.bp-tabs`, `.bp-tab`, `.bp-grid` (responsive card grid), `.bp-card`, `.bp-pill` (industry filter), `.bp-badge` (complexity), `.bp-chip` (skill), `.bp-out` (output block), `.bp-tree` (artifact tree), `.bp-copy` (copy button). Match `index/production` visual bar.

- [ ] **Step 2: Register new assets** in `docs/ci/sync_cache_bust.py` `ASSETS`:

```python
ASSETS = {
    "site.css": DOCS / "assets" / "site.css",
    "site.js": DOCS / "assets" / "site.js",
    "blueprint-logic.js": DOCS / "assets" / "blueprint-logic.js",
    "blueprint.js": DOCS / "assets" / "blueprint.js",
}
```

- [ ] **Step 3: Sync tokens.** `python3 docs/ci/sync_cache_bust.py --write` then `--check` (exit 0).
- [ ] **Step 4: Commit.** `git commit -m "style(blueprint): composer components + cache-bust tokens"`

---

## Task 7: DOM controller `blueprint.js`

**Files:**
- Create: `docs/assets/blueprint.js`

- [ ] **Step 1: Implement controller** (classic script, uses `window.TL_BLUEPRINT`): `fetch('assets/process-library.json')`; render `.bp-grid` cards; wire search + industry pills + complexity filter; card click → `select(process)`; Describe form submit → build a process object → `select`; `select` calls `TL_BLUEPRINT.deriveSkills/buildPrompt/buildAzd` and paints `#out-prompt`, `#out-skills` (chips), `#out-azd`, `#out-tree` (static illustrative tree); copy buttons via `navigator.clipboard.writeText`. Guard all DOM lookups.
- [ ] **Step 2: Sync + check.** `python3 docs/ci/sync_cache_bust.py --write && python3 docs/ci/sync_cache_bust.py --check`.
- [ ] **Step 3: Commit.** `git commit -m "feat(blueprint): composer controller (render, filter, outputs, copy)"`

---

## Task 8: Local e2e + verification + ship

**Files:**
- Create: `tests/playwright/tests/blueprint.spec.mjs`

- [ ] **Step 1: Write e2e spec** (mirror `site.spec.mjs`): page 200s; nav has Blueprint; `.bp-grid .bp-card` count > 0; click first card → `#out-prompt` contains "threadlight-auto"; `#out-skills .bp-chip` count ≥ 5; industry pill filters the grid.
- [ ] **Step 2: Run e2e** from `tests/playwright`: `PORT=4173 npx playwright test blueprint --project=chromium-light` → pass.
- [ ] **Step 3: Visual verify** on the running devserver (:8799) in light mode via Playwright screenshot; confirm parity with `production.html`.
- [ ] **Step 4: Full scrub** of every new/modified file with the full prose regex; confirm 0 hits in committed page copy/spec/plan (data asset uses narrow scrub).
- [ ] **Step 5: Final cache-bust `--check`, then commit + push + open PR.**

```bash
python3 docs/ci/sync_cache_bust.py --check
git add -A && git commit -m "test(blueprint): local e2e spec + final verification"
git push -u origin part2-interactive-composer
```
Then open PR via `create_pull_request` (title: "feat: interactive Composer (Blueprint) on-ramp"). Body: what it is, no-backend guardrails, tests, follow-ons — full prose scrub.

---

## Self-review notes
- **Spec coverage:** §2 page+nav → T5; §3 data pipeline → T1; §4 entry paths+outputs → T5/T7; §5 skill-derivation → T3; §6 prompt contract → T3; §7 tech/design/tests → T3/T6/T8; §9 acceptance → all. Covered.
- **Type consistency:** `deriveSkills/buildPrompt/buildAzd` + `window.TL_BLUEPRINT` used identically in T3, T7, T8. Output IDs `#out-prompt/#out-skills/#out-azd/#out-tree` consistent T5↔T7↔T8.
- **Cache-bust:** new `blueprint-logic.js`/`blueprint.js` registered (T6) before referenced tokens are checked (T7/T8).
