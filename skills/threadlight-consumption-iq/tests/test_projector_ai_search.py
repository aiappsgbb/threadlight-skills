"""
Tests for ai_search.project.

Mocks pricing_client so hardcoded fallback prices are used predictably.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.ai_search import (  # noqa: E402
    HOURS_PER_MONTH,
    REPLICA_PARTITION_SWEEPS,
    TIER_DOC_CAP,
    TIER_PER_HOUR,
    project,
)


class FakePricing:
    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback"}


def _sku(tier="basic", replicas=1, partitions=1):
    return {
        "tier": tier,
        "capacity": replicas * partitions,
        "extra": {"replicas": replicas, "partitions": partitions},
    }


def _lp(ai_search_documents=0, rps=1):
    return {"ai_search_documents": ai_search_documents, "peak_requests_per_second": rps}


# ---------------------------------------------------------------------------
# test_ai_search_free_tier_baseline
# ---------------------------------------------------------------------------

def test_ai_search_free_tier_baseline():
    result = project(_sku("free", 1, 1), _lp(ai_search_documents=5_000), FakePricing())

    assert result["monthly_cost_usd"] == 0.0
    assert result["monthly_units_consumed"]["indexed_documents"] == 5_000
    assert result["monthly_units_consumed"]["replica_partition_hours"] == 1 * 1 * HOURS_PER_MONTH
    assert result["price_source"] == "fallback"


def test_ai_search_s1_cost_math():
    result = project(_sku("S1", 2, 2), _lp(), FakePricing())

    expected = TIER_PER_HOUR["S1"] * 2 * 2 * HOURS_PER_MONTH
    assert abs(result["monthly_cost_usd"] - expected) < 0.01
    assert result["monthly_units_consumed"]["replica_partition_hours"] == 2 * 2 * HOURS_PER_MONTH


# ---------------------------------------------------------------------------
# test_ai_search_alternative_below_doc_cap_marked_unsatisfied
# ---------------------------------------------------------------------------

def test_ai_search_alternative_below_doc_cap_marked_unsatisfied():
    # 20M documents exceeds free (10k) and basic (1M) caps.
    result = project(_sku("S1", 1, 1), _lp(ai_search_documents=20_000_000), FakePricing())

    for alt in result["alternatives"]:
        tier = alt["sku"]["tier"]
        cap = TIER_DOC_CAP.get(tier, 0)
        if cap < 20_000_000:
            assert alt["satisfies_constraints"] is False, (
                f"{tier} (cap={cap:,}) should be unsatisfied for 20M docs"
            )
            assert any("ai_search_documents" in c for c in alt["caveats"])
        else:
            assert alt["satisfies_constraints"] is True, (
                f"{tier} (cap={cap:,}) should be satisfied for 20M docs"
            )


def test_ai_search_free_and_basic_unsatisfied_for_large_doc_count():
    result = project(_sku("S2", 1, 1), _lp(ai_search_documents=20_000_000), FakePricing())

    free_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "free"]
    basic_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "basic"]

    for a in free_alts:
        assert a["satisfies_constraints"] is False
    for a in basic_alts:
        assert a["satisfies_constraints"] is False


def test_ai_search_all_tiers_represented_in_alternatives():
    result = project(_sku("S3", 3, 3), _lp(), FakePricing())

    tiers_present = {a["sku"]["tier"] for a in result["alternatives"]}
    for tier in TIER_PER_HOUR:
        if tier == "S3":
            # S3 with (3,3) is the current config; other S3 (r,p) combos should appear.
            s3_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "S3"]
            assert len(s3_alts) > 0, "Other S3 replica/partition combos should be present"
        else:
            assert tier in tiers_present, f"Tier {tier} missing from alternatives"


def test_ai_search_replica_partition_sweeps_present():
    result = project(_sku("basic", 1, 1), _lp(), FakePricing())

    s1_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "S1"]
    s1_combos = {
        (a["sku"]["extra"]["replicas"], a["sku"]["extra"]["partitions"])
        for a in s1_alts
    }
    for sweep in REPLICA_PARTITION_SWEEPS:
        assert tuple(sweep) in s1_combos, f"Missing S1 sweep {sweep}"


def test_ai_search_current_config_not_duplicated():
    result = project(_sku("S1", 2, 1), _lp(), FakePricing())

    for alt in result["alternatives"]:
        if alt["sku"]["tier"] == "S1":
            r = alt["sku"]["extra"]["replicas"]
            p = alt["sku"]["extra"]["partitions"]
            assert not (r == 2 and p == 1), "Current config (S1 2×1) should not be an alternative"
