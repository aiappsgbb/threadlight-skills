"""
Tests for storage.project.

Mocks pricing_client so hardcoded fallback prices are used predictably.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.storage import (  # noqa: E402
    STORAGE_PRICE_PER_GB_MONTH,
    STORAGE_TXN_PRICE_PER_10K,
    project,
)


class FakePricing:
    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback"}


def _sku(redundancy="LRS", access_tier="hot"):
    return {
        "name": f"Standard_{redundancy}",
        "tier": "Standard",
        "capacity": None,
        "extra": {"redundancy": redundancy, "access_tier": access_tier},
    }


def _lp(storage_gb=200, workload_class="web"):
    return {"storage_gb_year_one": storage_gb, "workload_class": workload_class}


# ---------------------------------------------------------------------------
# test_storage_lrs_hot_baseline
# ---------------------------------------------------------------------------

def test_storage_lrs_hot_baseline():
    result = project(_sku("LRS", "hot"), _lp(storage_gb=200), FakePricing())

    stored_avg = 100.0  # 200 GB / 2
    # LRS hot: $0.0184/GB + 100k txns × $0.004/10k + $0 egress
    expected = stored_avg * 0.0184 + 100_000 * STORAGE_TXN_PRICE_PER_10K / 10_000
    assert abs(result["monthly_cost_usd"] - expected) < 0.01
    assert result["monthly_units_consumed"]["stored_gb_avg"] == stored_avg
    assert result["monthly_units_consumed"]["transactions"] == 100_000
    assert result["monthly_units_consumed"]["egress_gb"] == 50
    assert result["price_source"] == "fallback"


def test_storage_lrs_hot_cost_matches_matrix():
    """Spot-check the GB price used equals the matrix entry."""
    result = project(_sku("LRS", "hot"), _lp(storage_gb=1000), FakePricing())
    stored_avg = 500.0
    gb_price = STORAGE_PRICE_PER_GB_MONTH[("LRS", "hot")]
    expected_storage_component = stored_avg * gb_price
    # total = storage + txn; txn is same regardless
    txn_component = 100_000 * STORAGE_TXN_PRICE_PER_10K / 10_000
    assert abs(result["monthly_cost_usd"] - (expected_storage_component + txn_component)) < 0.01


# ---------------------------------------------------------------------------
# test_storage_archive_blocked_for_non_batch_workload
# ---------------------------------------------------------------------------

def test_storage_archive_blocked_for_non_batch_workload():
    result = project(_sku("LRS", "hot"), _lp(workload_class="web"), FakePricing())

    archive_alts = [
        a for a in result["alternatives"]
        if a["sku"]["extra"]["access_tier"] == "archive"
    ]
    assert len(archive_alts) >= 1, "at least one archive alternative expected"
    for alt in archive_alts:
        assert alt["satisfies_constraints"] is False
        assert any("Archive tier" in c for c in alt["caveats"])


def test_storage_archive_allowed_for_batch_workload():
    result = project(_sku("LRS", "hot"), _lp(workload_class="batch"), FakePricing())

    archive_alts = [
        a for a in result["alternatives"]
        if a["sku"]["extra"]["access_tier"] == "archive"
    ]
    assert len(archive_alts) >= 1
    for alt in archive_alts:
        assert alt["satisfies_constraints"] is True


# ---------------------------------------------------------------------------
# test_storage_alternatives_carry_redundancy_field
# ---------------------------------------------------------------------------

def test_storage_alternatives_carry_redundancy_field():
    """Every alternative must have sku.redundancy for the recommender min_redundancy check."""
    result = project(_sku("LRS", "hot"), _lp(), FakePricing())

    assert len(result["alternatives"]) > 0
    for alt in result["alternatives"]:
        assert "redundancy" in alt["sku"], (
            f"Alternative is missing sku.redundancy: {alt['sku']}"
        )
        # Redundancy value must be a recognisable string (not None / empty).
        assert alt["sku"]["redundancy"] in ("LRS", "ZRS", "GRS"), (
            f"Unexpected redundancy value: {alt['sku']['redundancy']}"
        )


def test_storage_current_config_not_in_alternatives():
    """Current (redundancy, access_tier) pair must not appear as an alternative."""
    result = project(_sku("ZRS", "cool"), _lp(), FakePricing())

    for alt in result["alternatives"]:
        combo = (alt["sku"]["redundancy"], alt["sku"]["extra"]["access_tier"])
        assert combo != ("ZRS", "cool"), "Current config should not be an alternative"


def test_storage_grs_alternatives_present():
    result = project(_sku("LRS", "hot"), _lp(), FakePricing())
    grs_alts = [a for a in result["alternatives"] if a["sku"]["redundancy"] == "GRS"]
    assert len(grs_alts) > 0, "GRS alternatives should be present"
