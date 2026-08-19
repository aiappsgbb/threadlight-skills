"""Tests for the dated model catalog loader (`model_catalog.py`)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from model_catalog import (  # noqa: E402
    ModelCatalog,
    ModelCatalogError,
    STALE_AFTER_DAYS,
    load_model_catalog,
)

REPO_CATALOG = (
    Path(__file__).resolve().parent.parent / "references" / "model-catalog.json"
)


def test_repo_catalog_loads_and_has_expected_schema():
    catalog = load_model_catalog()
    assert catalog.schema == "threadlight.model-catalog/v1"
    assert isinstance(catalog.checked_at, date)
    assert catalog.source
    assert catalog.get("gpt-4o") is not None


def test_null_rate_is_preserved_not_zeroed():
    catalog = load_model_catalog()
    emb = catalog.get("text-embedding-3-small")
    assert emb is not None
    # embeddings have no output token cost — must stay None, never 0.0
    assert emb.output_per_1k_usd is None
    assert emb.cached_input_per_1k_usd is None
    assert emb.input_per_1k_usd == 0.00002


def test_comparisons_only_within_same_group():
    catalog = load_model_catalog()
    peers = catalog.comparisons("gpt-4o")
    peer_ids = {m.id for m in peers}
    assert "gpt-4o-mini" in peer_ids  # same comparison_group
    assert "gpt-4o" not in peer_ids  # excludes self
    # embeddings are a different comparison group — never offered as a chat swap
    assert "text-embedding-3-small" not in peer_ids
    for m in peers:
        assert m.comparison_group == catalog.get("gpt-4o").comparison_group


def test_absent_comparison_group_yields_no_swap(tmp_path: Path):
    data = {
        "schema": "threadlight.model-catalog/v1",
        "checked_at": "2026-06-12",
        "source": "test",
        "models": [
            {"id": "solo", "comparison_group": None, "input_per_1k_usd": 0.001},
            {"id": "other", "comparison_group": "x", "input_per_1k_usd": 0.002},
        ],
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    catalog = load_model_catalog(p)
    assert catalog.comparisons("solo") == []
    assert catalog.comparisons("unknown-model") == []
    # singleton group also yields no peers
    assert catalog.comparisons("other") == []


def test_catalog_marked_stale_after_90_days():
    catalog = load_model_catalog()
    # checked_at is 2026-06-12; 91 days later is stale, 90 is not.
    fresh = date(2026, 6, 12)
    assert catalog.is_stale(as_of=fresh) is False
    day_90 = date(2026, 9, 10)  # exactly 90 days
    assert catalog.age_days(as_of=day_90) == 90
    assert catalog.is_stale(as_of=day_90) is False
    day_91 = date(2026, 9, 11)  # 91 days
    assert catalog.age_days(as_of=day_91) == 91
    assert catalog.is_stale(as_of=day_91) is True


def test_stale_threshold_constant_is_90():
    assert STALE_AFTER_DAYS == 90


def test_bad_schema_raises(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"schema": "nope", "checked_at": "2026-01-01", "models": []}))
    with pytest.raises(ModelCatalogError):
        load_model_catalog(p)


def test_bad_date_raises(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps(
            {
                "schema": "threadlight.model-catalog/v1",
                "checked_at": "June 2026",
                "models": [],
            }
        )
    )
    with pytest.raises(ModelCatalogError):
        load_model_catalog(p)


def test_missing_catalog_raises(tmp_path: Path):
    with pytest.raises(ModelCatalogError):
        load_model_catalog(tmp_path / "does-not-exist.json")
