"""
Targeted tests for recommender constraint scoring + ranking.

Lives alongside test_scaffold.py. Each test stands up minimal projected
resources (no real projector runs) so this layer can be exercised in
isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recommender import (  # noqa: E402
    REDUNDANCY_RANK,
    _priority,
    score_and_rank,
)


def _resource(
    *,
    kind="Microsoft.Storage/storageAccounts",
    logical_name="storage1",
    current_cost=100.0,
    current_sku=None,
    alternatives=None,
    region="eastus2",
):
    return {
        "resource_kind": kind,
        "resource_id": f"/subscriptions/x/.../{logical_name}",
        "logical_name": logical_name,
        "region": region,
        "current_sku": current_sku
        or {"name": "Standard_LRS", "tier": "Standard", "region": region, "capacity": None,
            "extra": {"redundancy": "LRS"}, "redundancy": "LRS"},
        "monthly_cost_usd": current_cost,
        "alternatives": alternatives or [],
    }


def _alt(*, sku, cost, satisfies=True, redundancy=None, region="eastus2", rationale="cheaper", caveats=None):
    s = dict(sku)
    s.setdefault("region", region)
    if redundancy is not None:
        s["redundancy"] = redundancy
    return {
        "sku": s,
        "monthly_cost_usd": cost,
        "delta_usd": cost - 100.0,
        "delta_pct": (cost - 100.0) / 100.0,
        "satisfies_constraints": satisfies,
        "rationale": rationale,
        "caveats": caveats or [],
    }


def test_no_recommendation_when_current_is_cheapest():
    r = _resource(
        current_cost=10.0,
        alternatives=[_alt(sku={"name": "Premium"}, cost=99.0)],
    )
    assert score_and_rank([r], {}) == []


def test_recommends_cheaper_alternative():
    r = _resource(
        current_cost=100.0,
        alternatives=[_alt(sku={"name": "Standard_LRS"}, cost=60.0)],
    )
    recs = score_and_rank([r], {})
    assert len(recs) == 1
    assert recs[0]["monthly_savings_usd"] == 40.0
    assert recs[0]["monthly_savings_pct"] == 0.4
    assert recs[0]["priority"] == "med"


def test_priority_thresholds():
    assert _priority(200) == "high"
    assert _priority(100) == "med"
    assert _priority(50) == "med"
    assert _priority(25) == "low"
    assert _priority(0) == "low"


def test_drops_alternative_that_violates_pinned_region():
    r = _resource(
        current_cost=100.0,
        alternatives=[
            _alt(sku={"name": "Standard_LRS"}, cost=30.0, region="westus2"),
            _alt(sku={"name": "Standard_LRS"}, cost=60.0, region="eastus2"),
        ],
    )
    recs = score_and_rank(
        [r], {"declared_constraints": {"pinned_region": "eastus2"}}
    )
    assert len(recs) == 1
    # westus2 was cheaper but blocked; cheapest surviving = eastus2 @ 60.
    assert recs[0]["recommended_sku"]["region"] == "eastus2"
    assert recs[0]["monthly_savings_usd"] == 40.0


def test_drops_alternative_below_min_redundancy():
    r = _resource(
        current_cost=100.0,
        current_sku={
            "name": "Standard_ZRS", "tier": "Standard", "region": "eastus2",
            "capacity": None, "extra": {"redundancy": "ZRS"}, "redundancy": "ZRS",
        },
        alternatives=[
            _alt(sku={"name": "Standard_LRS"}, cost=30.0, redundancy="LRS"),
            _alt(sku={"name": "Standard_GRS"}, cost=60.0, redundancy="GRS"),
        ],
    )
    recs = score_and_rank(
        [r], {"declared_constraints": {"min_redundancy": "zone-redundant"}}
    )
    assert len(recs) == 1
    # LRS dropped (rank 0 < zone-redundant rank 1); GRS survives.
    assert recs[0]["recommended_sku"]["name"] == "Standard_GRS"


def test_min_redundancy_ignored_when_alternative_has_no_redundancy_field():
    # Most resource kinds (AOAI, ACA, APIM, …) don't have a per-SKU
    # redundancy field. The constraint must NOT silently drop those.
    r = _resource(
        kind="Microsoft.App/containerApps",
        current_cost=100.0,
        alternatives=[_alt(sku={"name": "Consumption"}, cost=50.0)],
    )
    recs = score_and_rank(
        [r], {"declared_constraints": {"min_redundancy": "zone-redundant"}}
    )
    assert len(recs) == 1
    assert recs[0]["recommended_sku"]["name"] == "Consumption"


def test_results_sorted_by_savings_desc():
    r_small = _resource(
        kind="Microsoft.Storage/storageAccounts",
        logical_name="storage_small",
        current_cost=30.0,
        alternatives=[_alt(sku={"name": "Standard_LRS"}, cost=20.0)],
    )
    r_big = _resource(
        kind="Microsoft.CognitiveServices/accounts/deployments",
        logical_name="gpt4o",
        current_cost=1000.0,
        alternatives=[_alt(sku={"name": "gpt-4o", "tier": "PTU"}, cost=500.0)],
    )
    r_mid = _resource(
        kind="Microsoft.App/containerApps",
        logical_name="aca",
        current_cost=200.0,
        alternatives=[_alt(sku={"name": "Consumption"}, cost=100.0)],
    )
    recs = score_and_rank([r_small, r_big, r_mid], {})
    assert [r["logical_name"] for r in recs] == ["gpt4o", "aca", "storage_small"]


def test_alternative_with_satisfies_false_is_dropped():
    r = _resource(
        current_cost=100.0,
        alternatives=[
            _alt(sku={"name": "Standard_LRS"}, cost=20.0, satisfies=False),
            _alt(sku={"name": "Standard_LRS"}, cost=80.0, satisfies=True),
        ],
    )
    recs = score_and_rank([r], {})
    assert len(recs) == 1
    assert recs[0]["monthly_savings_usd"] == 20.0


def test_redundancy_rank_ordering_documented():
    # Sanity: zone-redundant > locally-redundant; geo-redundant > zone-redundant.
    assert REDUNDANCY_RANK["lrs"] < REDUNDANCY_RANK["zrs"] < REDUNDANCY_RANK["grs"]
    assert REDUNDANCY_RANK["locally-redundant"] == REDUNDANCY_RANK["lrs"]
    assert REDUNDANCY_RANK["zone-redundant"] == REDUNDANCY_RANK["zrs"]
    assert REDUNDANCY_RANK["geo-redundant"] == REDUNDANCY_RANK["grs"]


def test_unknown_redundancy_vocabulary_is_conservative():
    # If we see a redundancy label we don't recognize, don't silently
    # drop it — let the projector's caveat tell the user instead.
    r = _resource(
        current_cost=100.0,
        alternatives=[_alt(sku={"name": "Custom_XYZ"}, cost=50.0, redundancy="paxos")],
    )
    recs = score_and_rank(
        [r], {"declared_constraints": {"min_redundancy": "zone-redundant"}}
    )
    assert len(recs) == 1
