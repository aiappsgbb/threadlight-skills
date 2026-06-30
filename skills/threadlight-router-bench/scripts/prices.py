#!/usr/bin/env python3
"""Seed price table ($/1M tokens) for the bench cost axis.

These are SEED values for relative cost reasoning, NOT a billing source of truth —
override with a real Azure price export via `load_prices(path)` or `--prices`.
Input/output are USD per 1,000,000 tokens.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Seed only — override with current Azure retail/EA pricing for real $ figures.
SEED_PRICES: dict[str, dict[str, float]] = {
    "gpt-5.4":      {"input": 2.50, "output": 10.00},
    "gpt-5.5":      {"input": 5.00, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.15, "output": 0.60},
}


def load_prices(path: Path | str | None = None) -> dict[str, dict[str, float]]:
    """Return the seed table, deep-merged with an optional override JSON file."""
    table: dict[str, dict[str, float]] = {m: dict(v) for m, v in SEED_PRICES.items()}
    if path is not None:
        override: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        for model, rates in override.items():
            table.setdefault(model, {})
            table[model].update(rates)
    return table
