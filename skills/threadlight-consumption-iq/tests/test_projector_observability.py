"""
Tests for projectors/observability.py — Log Analytics / App Insights
ingestion cost projector.

This is the projector the post-deploy matrix never had. GenAI agents emit
OpenTelemetry traces on *every* request (spans for each tool call + model
call), and turning on content recording (prompt + completion capture) can
multiply ingest volume several-fold. Log ingestion is billed per GB and has
historically been the surprise line item — this projector makes it visible.

All tests use a FakePricing that returns unit_price_usd=None so the projector
falls back to the hardcoded PER_GB price. This isolates the formula.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors import PROJECTOR_REGISTRY, project_resource  # noqa: E402
from projectors.observability import (  # noqa: E402
    ANALYTICS_PER_GB,
    DEFAULT_BYTES_PER_TRACE,
    DEFAULT_CONTENT_BYTES_PER_TRACE,
    project,
)


class FakePricing:
    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "price_source": "fallback"}


def _sku(content_recording=False, bytes_per_trace=None, content_bytes=None, retention_days=30):
    extra = {"content_recording": content_recording, "retention_days": retention_days}
    if bytes_per_trace is not None:
        extra["bytes_per_trace"] = bytes_per_trace
    if content_bytes is not None:
        extra["content_bytes_per_trace"] = content_bytes
    return {
        "name": "PerGB2018",
        "tier": "PerGB2018",
        "region": "eastus2",
        "capacity": None,
        "extra": extra,
    }


def _load(rps=2.0, business_hours=False):
    return {"peak_requests_per_second": rps, "business_hours_only": business_hours}


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_observability_is_registered():
    assert "Microsoft.OperationalInsights/workspaces" in PROJECTOR_REGISTRY


def test_project_resource_dispatches_observability():
    resource = {
        "resource_kind": "Microsoft.OperationalInsights/workspaces",
        "resource_id": "/subscriptions/x/.../law",
        "logical_name": "law",
        "region": "eastus2",
        "current_sku": _sku(),
    }
    out = project_resource(resource, _load(), FakePricing())
    assert out["resource_kind"] == "Microsoft.OperationalInsights/workspaces"
    assert "monthly_cost_usd" in out
    assert "ingested_gb" in out["monthly_units_consumed"]


# ---------------------------------------------------------------------------
# Formula
# ---------------------------------------------------------------------------

def test_ingest_cost_no_content_recording():
    """
    24/7 (30d) @ 2 RPS, base 3 KB/trace, no content recording:

      seconds/month = 2,592,000
      monthly_traces = 2 × 2,592,000 = 5,184,000
      bytes/trace = 3000
      ingested_gb = 5,184,000 × 3000 / 1e9 = 15.552 GB
      cost = 15.552 × ANALYTICS_PER_GB
    """
    result = project(_sku(content_recording=False), _load(rps=2.0), FakePricing())
    seconds = 24 * 3600 * 30
    traces = 2.0 * seconds
    gb = traces * DEFAULT_BYTES_PER_TRACE / 1e9
    expected = gb * ANALYTICS_PER_GB
    assert result["monthly_units_consumed"]["ingested_gb"] == pytest.approx(gb, rel=1e-6)
    assert result["monthly_cost_usd"] == pytest.approx(expected, rel=1e-6)
    assert result["monthly_cost_usd"] > 0


def test_content_recording_multiplies_volume():
    """Turning on content recording adds DEFAULT_CONTENT_BYTES_PER_TRACE per trace."""
    off = project(_sku(content_recording=False), _load(rps=2.0), FakePricing())
    on = project(_sku(content_recording=True), _load(rps=2.0), FakePricing())
    assert on["monthly_cost_usd"] > off["monthly_cost_usd"]
    ratio = (DEFAULT_BYTES_PER_TRACE + DEFAULT_CONTENT_BYTES_PER_TRACE) / DEFAULT_BYTES_PER_TRACE
    assert on["monthly_cost_usd"] == pytest.approx(off["monthly_cost_usd"] * ratio, rel=1e-6)


def test_alternatives_include_sampling_bands():
    """Sampling reduces ingest linearly — 50% and 10% bands must appear cheaper."""
    result = project(_sku(content_recording=True), _load(rps=2.0), FakePricing())
    sampled = {
        a["sku"]["extra"].get("sampling_rate")
        for a in result["alternatives"]
        if "sampling_rate" in a["sku"].get("extra", {})
    }
    assert {0.5, 0.1}.issubset(sampled)
    for alt in result["alternatives"]:
        rate = alt["sku"].get("extra", {}).get("sampling_rate")
        if rate is not None:
            assert alt["monthly_cost_usd"] == pytest.approx(
                result["monthly_cost_usd"] * rate, rel=1e-6
            )


def test_alternatives_include_content_recording_toggle():
    """A content-recording-OFF alternative must be offered when current is ON."""
    result = project(_sku(content_recording=True), _load(rps=2.0), FakePricing())
    toggles = [
        a for a in result["alternatives"]
        if a["sku"].get("extra", {}).get("content_recording") is False
    ]
    assert toggles, "expected a content-recording-OFF alternative"
    assert toggles[0]["monthly_cost_usd"] < result["monthly_cost_usd"]


def test_custom_bytes_per_trace_override():
    """extra.bytes_per_trace overrides the default span size."""
    result = project(_sku(bytes_per_trace=6000), _load(rps=1.0), FakePricing())
    seconds = 24 * 3600 * 30
    gb = 1.0 * seconds * 6000 / 1e9
    assert result["monthly_units_consumed"]["ingested_gb"] == pytest.approx(gb, rel=1e-6)


def test_price_source_is_reported():
    result = project(_sku(), _load(), FakePricing())
    assert result["price_source"] == "fallback"
