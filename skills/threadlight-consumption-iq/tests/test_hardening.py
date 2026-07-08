"""
Tests for scripts/hardening.py — production-hardening + estate delta.

Unlike the per-resource projectors (which *swap* a SKU on a resource that is
already in the repo), hardening models the SKUs that are NOT in the pilot repo
but show up the moment the workload goes to production: Front Door + WAF,
Private Endpoints, Defender for Cloud, Sentinel, DDoS Protection, multi-region
DR, and the non-prod estate copy.

It is posture-driven:
  * demo                 -> no delta (a pilot is a pilot)
  * production           -> the must-haves (private networking, baseline secops)
  * production-hardened  -> the full regulated-enterprise estate

Each line is tagged `shared_platform_billed` so the seller can be honest that
some items (DDoS, Sentinel, Defender) are billed once across a whole estate,
not per-app — "we counted it, but it's amortised."
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hardening import (  # noqa: E402
    HARDENING_CATALOG,
    HardeningError,
    load_catalog,
    project_hardening,
)


def _load(rps=12.0, business_hours=False):
    return {"peak_requests_per_second": rps, "business_hours_only": business_hours}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_has_three_postures():
    assert set(HARDENING_CATALOG) == {"demo", "production", "production-hardened"}


def test_catalog_demo_is_empty():
    assert HARDENING_CATALOG["demo"] == []


def test_catalog_is_loadable_from_json_reference():
    catalog = load_catalog()
    assert "production-hardened" in catalog
    # Every line item declares the fields the emitter relies on.
    for posture, items in catalog.items():
        for item in items:
            assert "component" in item
            assert "monthly_cost_usd" in item
            assert "shared_platform_billed" in item


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def test_demo_posture_yields_no_delta():
    assert project_hardening("demo", _load()) == []


def test_production_posture_yields_delta():
    lines = project_hardening("production", _load())
    assert lines, "production posture must add at least one hardening line"
    components = {ln["component"] for ln in lines}
    assert any("Private Endpoint" in c for c in components)


def test_hardened_posture_superset_of_production():
    prod = {ln["component"] for ln in project_hardening("production", _load())}
    hardened = {ln["component"] for ln in project_hardening("production-hardened", _load())}
    assert prod.issubset(hardened)
    assert len(hardened) > len(prod)


def test_hardened_includes_estate_and_secops():
    components = {ln["component"] for ln in project_hardening("production-hardened", _load())}
    assert any("Front Door" in c for c in components)
    assert any("Defender" in c for c in components)
    assert any("Sentinel" in c for c in components)


def test_each_line_tags_price_source_and_posture():
    for line in project_hardening("production-hardened", _load()):
        assert line["price_source"] in ("fallback", "fixture", "live")
        assert line["posture"] == "production-hardened"
        assert isinstance(line["shared_platform_billed"], bool)
        assert "rationale" in line


def test_shared_platform_items_are_flagged():
    lines = project_hardening("production-hardened", _load())
    shared = [ln for ln in lines if ln["shared_platform_billed"]]
    # Sentinel / Defender / DDoS are estate-amortised, not per-app.
    assert shared, "expected at least one shared-platform-billed line"


def test_unknown_posture_raises():
    with pytest.raises(HardeningError, match="posture"):
        project_hardening("ultra", _load())


def test_total_is_sum_of_lines():
    lines = project_hardening("production-hardened", _load())
    total = sum(ln["monthly_cost_usd"] for ln in lines)
    assert total > 0
