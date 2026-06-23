#!/usr/bin/env python3
"""Keep the GitHub Pages asset cache-bust tokens in sync with file contents.

Problem this prevents
---------------------
``docs/*.html`` reference the shared stylesheet and script with a cache-bust
query, e.g. ``assets/site.css?v=<token>``. GitHub Pages serves those assets with
``Cache-Control: max-age=600`` and *no* content hash in the path, so a returning
browser reuses whatever copy it already cached for that exact URL. If we edit
``site.css``/``site.js`` but leave the token unchanged, the URL is byte-identical
and the browser keeps serving the *old* asset against the *new* HTML -- producing
broken, half-updated layouts until the cache happens to expire.

Fix
---
Bind the token to the asset's content: ``?v=<sha256(file)[:8]>``. Any change to
the asset changes its URL, guaranteeing a fresh fetch; an unchanged asset keeps a
stable URL (so we don't bust caches needlessly).

Usage
-----
    python docs/ci/sync_cache_bust.py --write   # rewrite tokens to match content
    python docs/ci/sync_cache_bust.py --check    # CI gate: exit 1 if any stale

Run ``--write`` whenever you touch ``site.css`` or ``site.js`` and commit the
HTML changes alongside the asset change. CI runs ``--check``.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
ASSETS = {
    "site.css": DOCS / "assets" / "site.css",
    "site.js": DOCS / "assets" / "site.js",
}
HASH_LEN = 8


def token_for(asset_path: Path) -> str:
    digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    return digest[:HASH_LEN]


def ref_pattern(asset_name: str) -> re.Pattern[str]:
    # Matches assets/site.css or assets/site.js with an optional ?v=<token>.
    escaped = re.escape(asset_name)
    return re.compile(r"(assets/" + escaped + r")(?:\?v=[^\"'#]*)?")


def process(write: bool) -> int:
    tokens = {name: token_for(path) for name, path in ASSETS.items()}
    html_files = sorted(DOCS.glob("*.html"))
    if not html_files:
        print("no docs/*.html files found", file=sys.stderr)
        return 1

    stale: list[str] = []
    for html in html_files:
        original = html.read_text(encoding="utf-8")
        updated = original
        for name, token in tokens.items():
            want = f"assets/{name}?v={token}"
            updated = ref_pattern(name).sub(want, updated)
        if updated != original:
            if write:
                html.write_text(updated, encoding="utf-8")
                print(f"updated {html.name}")
            else:
                stale.append(html.name)

    if not write:
        if stale:
            joined = ", ".join(stale)
            print(
                "STALE cache-bust tokens in: " + joined + "\n"
                "An asset under docs/assets/ changed but the ?v= token in the HTML "
                "was not updated. Run:\n"
                "    python docs/ci/sync_cache_bust.py --write\n"
                "and commit the updated HTML so returning browsers fetch fresh assets.",
                file=sys.stderr,
            )
            return 1
        print("cache-bust tokens are in sync:", ", ".join(f"{k}={v}" for k, v in tokens.items()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="rewrite tokens to match asset content")
    group.add_argument("--check", action="store_true", help="fail if any token is stale (CI gate)")
    args = parser.parse_args()
    return process(write=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
