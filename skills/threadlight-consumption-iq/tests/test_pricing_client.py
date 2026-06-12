"""
Tests for pricing_client.py — Phase 3 pricing client.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pricing_client import PricingClient, PricingUnavailableError, _match_sku  # noqa: E402

AOAI_RESOURCE_KIND = "Microsoft.CognitiveServices/accounts/deployments"


# ---------------------------------------------------------------------------
# PricingUnavailableError
# ---------------------------------------------------------------------------

def test_pricing_unavailable_error_importable():
    from pricing_client import PricingUnavailableError
    assert issubclass(PricingUnavailableError, RuntimeError)


# ---------------------------------------------------------------------------
# _fetch_live
# ---------------------------------------------------------------------------

def test_fetch_live_returns_none_on_network_error(tmp_path):
    """Any URLError / timeout should cause _fetch_live to return None (not raise)."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        result = client._fetch_live(
            AOAI_RESOURCE_KIND,
            {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
        )
    assert result is None


def test_fetch_live_envelope_shape(tmp_path):
    """Monkeypatched urlopen returning canned JSON should produce the expected envelope."""
    canned = {
        "Items": [
            {
                "meterId": "meter-abc",
                "meterName": "gpt-4o Input",
                "unitPrice": 0.0025,
                "retailPrice": 0.0025,
                "unitOfMeasure": "1K Tokens",
                "currencyCode": "USD",
                "armRegionName": "eastus2",
                "serviceName": "Cognitive Services",
                "skuName": "gpt-4o",
            }
        ],
        "Count": 1,
    }
    raw_bytes = json.dumps(canned).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    client = PricingClient(cache_path=tmp_path / "cache.json")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client._fetch_live(
            AOAI_RESOURCE_KIND,
            {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
        )

    assert result is not None
    assert result["unit_price_usd"] == 0.0025
    assert result["unit"] == "1K Tokens"
    assert result["price_source"] == "live"
    assert result["azure_meter_id"] == "meter-abc"
    assert "fetched_at" in result
    assert "raw" in result
    assert "all_items" in result


def test_fetch_live_returns_none_for_empty_items(tmp_path):
    """Empty Items list should return None."""
    canned = {"Items": [], "Count": 0}
    raw_bytes = json.dumps(canned).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    client = PricingClient(cache_path=tmp_path / "cache.json")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client._fetch_live(
            AOAI_RESOURCE_KIND,
            {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
        )
    assert result is None


def test_fetch_live_returns_none_for_unknown_resource_kind(tmp_path):
    client = PricingClient(cache_path=tmp_path / "cache.json")
    result = client._fetch_live("Microsoft.Unknown/thing", {"name": "foo"})
    assert result is None


# ---------------------------------------------------------------------------
# _lookup_fixture
# ---------------------------------------------------------------------------

def test_lookup_fixture_aoai_match(tmp_path):
    """Real AOAI fixture: gpt-4o PAYG eastus2 input should resolve to $0.0025."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    result = client._lookup_fixture(
        AOAI_RESOURCE_KIND,
        {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
    )
    assert result is not None
    assert result["unit_price_usd"] == 0.0025
    assert result["unit"] == "per-1k-input-tokens"
    assert result["price_source"] == "fixture"
    assert result["azure_meter_id"] is None


def test_lookup_fixture_aoai_output(tmp_path):
    """Real AOAI fixture: gpt-4o PAYG eastus2 output should resolve to $0.01."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    result = client._lookup_fixture(
        AOAI_RESOURCE_KIND,
        {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Output"},
    )
    assert result is not None
    assert result["unit_price_usd"] == 0.01


def test_lookup_fixture_aoai_mini_input(tmp_path):
    client = PricingClient(cache_path=tmp_path / "cache.json")
    result = client._lookup_fixture(
        AOAI_RESOURCE_KIND,
        {"name": "gpt-4o-mini", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
    )
    assert result is not None
    assert result["unit_price_usd"] == 0.00015


def test_lookup_fixture_returns_none_on_no_match(tmp_path):
    """Unknown model should return None (no entry in fixture)."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    result = client._lookup_fixture(
        AOAI_RESOURCE_KIND,
        {"name": "gpt-unknown-9000", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
    )
    assert result is None


def test_lookup_fixture_returns_none_for_missing_fixture(tmp_path):
    """Resource kind with no fixture file should return None."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    result = client._lookup_fixture("Microsoft.NoFixture/thing", {"name": "foo"})
    assert result is None


# ---------------------------------------------------------------------------
# get_price — cache + fallthrough logic
# ---------------------------------------------------------------------------

def test_get_price_prefers_cache_when_fresh(tmp_path):
    """If a fresh cache entry exists, _fetch_live must not be called."""
    from datetime import datetime, timezone

    client = PricingClient(cache_path=tmp_path / "cache.json")
    sku = {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"}
    key = client._cache_key(AOAI_RESOURCE_KIND, sku)

    fresh_entry = {
        "unit_price_usd": 0.0099,
        "unit": "per-1k-input-tokens",
        "price_source": "live",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "azure_meter_id": "cached-meter",
        "raw": {},
    }
    client._cache[key] = fresh_entry

    with patch.object(client, "_fetch_live") as mock_live:
        result = client.get_price(AOAI_RESOURCE_KIND, sku)

    mock_live.assert_not_called()
    assert result["unit_price_usd"] == 0.0099
    assert result["azure_meter_id"] == "cached-meter"


def test_get_price_falls_through_live_to_fixture(tmp_path):
    """When _fetch_live returns None, get_price should fall through to the fixture."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    sku = {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"}

    with patch.object(client, "_fetch_live", return_value=None):
        result = client.get_price(AOAI_RESOURCE_KIND, sku)

    assert result is not None
    assert result["price_source"] == "fixture"
    assert result["unit_price_usd"] == 0.0025


def test_get_price_returns_fallback_when_both_fail(tmp_path):
    """When both live and fixture fail, a fallback dict (unit_price_usd=None) is returned."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    sku = {"name": "gpt-unknown-9000", "region": "eastus2", "tier": "PAYG"}

    with patch.object(client, "_fetch_live", return_value=None):
        result = client.get_price(AOAI_RESOURCE_KIND, sku)

    assert result["price_source"] == "fallback"
    assert result["unit_price_usd"] is None


# ---------------------------------------------------------------------------
# warm
# ---------------------------------------------------------------------------

def test_warm_primes_cache(tmp_path):
    """warm() should call get_price with current_sku."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    resource = {
        "resource_kind": AOAI_RESOURCE_KIND,
        "current_sku": {"name": "gpt-4o", "region": "eastus2", "tier": "PAYG", "meter_substring": "Input"},
    }

    with patch.object(client, "get_price", return_value={"unit_price_usd": 0.0025}) as mock_get:
        client.warm(resource)

    mock_get.assert_called_once_with(
        AOAI_RESOURCE_KIND,
        resource["current_sku"],
    )


def test_warm_handles_missing_keys(tmp_path):
    """warm() should not raise if resource is missing optional keys."""
    client = PricingClient(cache_path=tmp_path / "cache.json")
    with patch.object(client, "get_price", return_value={"unit_price_usd": None}):
        client.warm({})  # no resource_kind or current_sku
