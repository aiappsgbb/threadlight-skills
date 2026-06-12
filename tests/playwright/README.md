# `tests/playwright/` — local site smoke + a11y

Public-safe end-to-end checks for `docs/index.html` (landing) and
`docs/skills.html` (catalog). Runs locally with Chromium against a
short-lived `python3 -m http.server` rooted at `docs/`.

## What it covers

- Landing renders, no duplicate Setup link in the nav, footer carries
  the public-safe wording ("open guidance", not "internal use"), and
  every nav anchor target resolves.
- The new `#scene-cost` "Cost intelligence" widget animates from `$0`
  to the deterministic v0.1.0 golden numbers — `$397.57 /mo` current,
  `$341.19 /mo` recommended, `−$56.38 / −14.2%` savings — with 7 table
  rows and 3 recommendation cards.
- The catalog page (`docs/skills.html`) renders all 11 skill cards,
  the `threadlight-consumption-iq` card is tagged `new`, the phase chip
  filter narrows the grid, the search input filters live, and
  `skills.html#threadlight-consumption-iq` deep-link-scrolls to that
  card.
- `axe-core` reports no serious / critical WCAG 2.1 AA violations on
  either page (color-contrast is checked manually because the dark
  theme's gradient text doesn't lend itself to automated contrast).
- A public-safety audit confirms no leak terms ("internal use",
  "confidential", "do not share", "microsoft only", or the legacy
  "tier-1 european telco" phrasing) appear anywhere on either page.

Tests run in four projects: chromium-desktop @ 1440×900, chromium-mobile
(Pixel 7), chromium-dark, chromium-light. 14 tests × 4 projects = 56
assertions.

## Run it

```bash
cd tests/playwright
npm install
npx playwright install chromium
npx playwright test
```

The Playwright config auto-starts `python3 -m http.server 4173` rooted
at `../../docs` and tears it down when the suite ends.

## What's NOT checked

- The cost widget numbers are hand-mirrored from
  `skills/threadlight-consumption-iq/references/fixtures/sample-pilot-consumption/expected/cost-manifest.json`.
  If that golden ever changes, the widget must be hand-updated AND
  these tests will fail to surface the drift — that's intentional.
- Visual regression. Screenshots are not asserted byte-for-byte; the
  designer pass uses them manually.
