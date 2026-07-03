#!/usr/bin/env python3
"""Sanitise an internal process_library.json into the committed static asset.

The raw source is NOT committed. This producer keeps only a whitelist of
presentation fields and asserts the output carries no supply-chain leak
markers. Business vocabulary (competitive/confidential/compliance) is LEGIT
third-party content and is deliberately NOT scrubbed.

Usage:
    python3 scripts/build_process_library.py --source /tmp/process_library.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEEP = [
    "id", "name", "industry", "complexity", "summary", "description",
    "tags", "business_constraints", "external_integrations",
    "human_approvals", "knowledge_sources",
]
LEAK = re.compile(r"agentic[- ]?loop|threadlight-vnext|northcentralus|remote-gw|gpt-5\.1", re.I)


def sanitise(entry: dict) -> dict:
    return {k: entry.get(k) for k in KEEP if k in entry}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="path to the raw process_library.json")
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
