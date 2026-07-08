# `pages-cache-bust.yml` — stale-asset guard for GitHub Pages

> Operator guide for the [`pages-cache-bust`](../../.github/workflows/pages-cache-bust.yml)
> GitHub Actions workflow. Keeps returning visitors from rendering the live
> site with a stale stylesheet/script after a deploy.

## The problem it prevents

`docs/*.html` load the shared assets with a cache-bust query:

```html
<link rel="stylesheet" href="assets/site.css?v=58556971">
<script src="assets/site.js?v=91ff002f"></script>
```

GitHub Pages serves those assets with `Cache-Control: max-age=600` and **no
content hash in the path**. A returning browser therefore reuses whatever copy
it already cached for that exact URL. If we edit `site.css`/`site.js` but the
`?v=` token does **not** change, the URL is byte-identical and the browser keeps
serving the **old** asset against the **new** HTML — producing broken,
half-updated layouts (e.g. horizontal overflow, clipped headings) until each
client's 10-minute cache happens to expire.

This actually happened: a CSS refactor shipped while the token stayed at
`ia-v24`, so returning visitors paired new HTML with the old cached stylesheet.

## The invariant

The token is the **sha256 prefix of the asset's contents**
(`?v=<sha256(file)[:8]>`), produced by
[`docs/ci/sync_cache_bust.py`](./sync_cache_bust.py):

- Any change to `site.css`/`site.js` changes its hash → changes its URL →
  forces every browser to fetch the fresh file.
- An unchanged asset keeps a stable URL, so we never bust caches needlessly.

This job runs `sync_cache_bust.py --check` and **fails the build** if any page
references a token that no longer matches the asset on disk.

## When it fires

- Every pull request to `main` that touches `docs/**`.
- Every push to `main` that touches `docs/**`.
- Manual via `gh workflow run pages-cache-bust.yml`.

## Failure triage

| Failure | Cause | Action |
|---|---|---|
| `STALE cache-bust tokens in: …` (exit 1) | You edited `site.css` and/or `site.js` but left the `?v=` token in the HTML unchanged. | Run `python docs/ci/sync_cache_bust.py --write`, then commit the updated `docs/*.html` alongside your asset change. |

## Local workflow

Whenever you touch `docs/assets/site.css` or `docs/assets/site.js`:

```bash
python docs/ci/sync_cache_bust.py --write   # rewrites the tokens in all docs/*.html
git add docs/assets/site.* docs/*.html       # commit asset + token bump together
```

That single habit guarantees a deploy can never leave a visitor on a stale
stylesheet.
