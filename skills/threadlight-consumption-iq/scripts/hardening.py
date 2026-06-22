"""
Production-hardening + estate delta (`hardening.py`).

The per-resource projectors swap SKUs on resources that already exist in the
pilot repo. This module models the cost that appears the moment the workload
leaves "pilot" and enters "regulated production" — SKUs that are *not in the
repo*: Front Door + WAF, Private Endpoints, Defender for Cloud, Sentinel, DDoS
Protection, multi-region DR, and the non-prod estate copy.

It is a **delta**, not a swap. The output is a flat list of cost lines, each
tagged with `shared_platform_billed` so a seller can be honest that some items
(Sentinel, Defender, DDoS) are billed once across a whole estate, not per app.

Posture-driven and cumulative:
  demo                 -> []                       (a pilot is a pilot)
  production           -> private networking + AZ  (the must-haves)
  production-hardened  -> the full regulated estate (strict superset)

Figures come from `references/hardening-delta-catalog.json` — neutral,
public-list ESTIMATES for one generic pilot, never a quote. Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "references" / "hardening-delta-catalog.json"

POSTURES = ("demo", "production", "production-hardened")


class HardeningError(RuntimeError):
    """Unknown posture or malformed hardening catalog."""


def load_catalog(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load the hardening delta catalog from JSON (drops the `_comment` key)."""
    path = path or _CATALOG_PATH
    raw = json.loads(path.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


HARDENING_CATALOG: dict[str, list[dict[str, Any]]] = load_catalog()


def project_hardening(
    posture: str,
    load_profile: dict[str, Any],
    pricing_client: Any = None,
) -> list[dict[str, Any]]:
    """Return the hardening + estate delta lines for `posture`.

    `load_profile` and `pricing_client` are accepted for symmetry with the
    per-resource projectors and to leave room for load-scaled sizing; v0.3.0
    uses flat per-pilot estimates from the catalog.
    """
    if posture not in HARDENING_CATALOG:
        raise HardeningError(
            f"unknown hardening posture {posture!r}; must be one of {list(POSTURES)}"
        )

    lines: list[dict[str, Any]] = []
    for item in HARDENING_CATALOG[posture]:
        line = dict(item)
        line["posture"] = posture
        line.setdefault("price_source", "fallback")
        line.setdefault("shared_platform_billed", False)
        line.setdefault("rationale", "")
        lines.append(line)
    return lines


def hardening_total_usd(lines: list[dict[str, Any]]) -> float:
    """Sum of the monthly cost of a list of hardening lines."""
    return sum(float(ln.get("monthly_cost_usd") or 0.0) for ln in lines)
