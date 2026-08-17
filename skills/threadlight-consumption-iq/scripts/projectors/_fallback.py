"""
Dated fallback-rate loader for the v1 resource projectors.

The per-resource projectors call ``pricing_client.get_price()`` first; only when
that returns no price (offline / empty fixture) do they fall back to a fixed
rate. Those fixed rates used to be inline literals — they now live in the dated
``fallback-rates.json`` beside this module, so there are no rate literals in the
projector code.

Loaded via :mod:`importlib.resources` so it works both from the normal repo tree
AND from inside the Cowork ``cost-runtime.zip`` (zip-safe package data access).
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    from importlib.resources import files

    text = files(__package__).joinpath("fallback-rates.json").read_text(encoding="utf-8")
    return json.loads(text)


def section(name: str) -> dict[str, Any]:
    """Return the fallback-rate block for a projector (e.g. ``"cosmos"``)."""
    return _load().get(name, {})


def storage_price_matrix() -> dict[tuple[str, str], float]:
    """Rebuild storage's ``(redundancy, access_tier) -> usd`` matrix from JSON."""
    return {
        (entry["redundancy"], entry["access_tier"]): entry["usd"]
        for entry in section("storage").get("per_gb_month", [])
    }
