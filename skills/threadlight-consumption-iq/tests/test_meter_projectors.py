"""Tests for the eight consumption-meter projectors + registry dispatch."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pricing_client import PricingClient  # noqa: E402
from projectors import (  # noqa: E402
    METER_PROJECTOR_REGISTRY,
    project_meter_demand,
)
from meter_demand import (  # noqa: E402
    CONTENT_UNDERSTANDING_EXTRACTION,
    CONTENT_UNDERSTANDING_CONTEXTUALIZATION,
    DOCUMENT_INTELLIGENCE,
    SPEECH,
    EMBEDDINGS,
    SEARCH_AGENTIC_RETRIEVAL,
    SEARCH_SEMANTIC_RANKER,
    WEB_GROUNDING,
)


def _client(tmp_path: Path, meters_fixture: Path | None = None) -> PricingClient:
    return PricingClient(cache_path=tmp_path / "cache.json", meters_fixture=meters_fixture)


def _demand(meter_kind, unit, qty, selector=None, verified=None):
    return {
        "meter_kind": meter_kind,
        "source": f"spec.selector.{meter_kind}",
        "volume_driver": {"unit": unit, "monthly_quantity": qty},
        "verified": qty is not None if verified is None else verified,
        "selector": selector or {},
    }


ALL_EIGHT = {
    CONTENT_UNDERSTANDING_EXTRACTION,
    CONTENT_UNDERSTANDING_CONTEXTUALIZATION,
    DOCUMENT_INTELLIGENCE,
    SPEECH,
    EMBEDDINGS,
    SEARCH_AGENTIC_RETRIEVAL,
    SEARCH_SEMANTIC_RANKER,
    WEB_GROUNDING,
}


def test_all_eight_meters_registered():
    assert set(METER_PROJECTOR_REGISTRY) == ALL_EIGHT
    assert len(METER_PROJECTOR_REGISTRY) == 8
    for meter_kind, module in METER_PROJECTOR_REGISTRY.items():
        assert module.METER_KIND == meter_kind


def test_priced_line_multiplies_quantity_by_rate(tmp_path):
    client = _client(tmp_path)
    demand = _demand(CONTENT_UNDERSTANDING_EXTRACTION, "pages", 10000, {"tier": "standard"})
    line = project_meter_demand(demand, client)
    assert line["pricing_status"] == "priced"
    assert line["monthly_cost_usd"] == 10000 * 0.01
    assert line["price_source"] == "fixture"


def test_embeddings_uses_per_1k_divisor(tmp_path):
    client = _client(tmp_path)
    demand = _demand(EMBEDDINGS, "tokens", 5_000_000, {"model": "text-embedding-3-small"})
    line = project_meter_demand(demand, client)
    assert line["pricing_status"] == "priced"
    # 5,000,000 tokens / 1000 * 0.00002
    assert line["monthly_cost_usd"] == round(5_000_000 / 1000 * 0.00002, 6)


def test_web_grounding_real_rate(tmp_path):
    client = _client(tmp_path)
    demand = _demand(WEB_GROUNDING, "transactions", 10000)
    line = project_meter_demand(demand, client)
    assert line["monthly_cost_usd"] == round(10000 / 1000 * 35.0, 6)


def test_selected_meter_missing_volume_priceable_but_not_verified(tmp_path):
    client = _client(tmp_path)
    demand = _demand(SPEECH, "hours", None, verified=False)
    line = project_meter_demand(demand, client)
    assert line["pricing_status"] == "priced"  # a rate exists
    assert line["monthly_cost_usd"] is None  # but no volume → no cost
    assert line["verified"] is False
    assert "reason" in line


def test_no_rate_is_not_priceable(tmp_path):
    # Empty meters fixture → every meter is unavailable.
    empty = tmp_path / "empty-meters.json"
    empty.write_text('{"_schema_version": "1.0", "meters": {}}', encoding="utf-8")
    client = _client(tmp_path, meters_fixture=empty)
    demand = _demand(SEARCH_SEMANTIC_RANKER, "requests", 8000)
    line = project_meter_demand(demand, client)
    assert line["pricing_status"] == "not-priceable"
    assert line["monthly_cost_usd"] is None
    assert line["reason"]
    assert line["alternatives"] == []


def test_unregistered_meter_retained_as_not_priceable(tmp_path):
    client = _client(tmp_path)
    demand = _demand("some-brand-new-meter", "widgets", 100)
    line = project_meter_demand(demand, client)
    assert line["pricing_status"] == "not-priceable"
    assert line["reason"] == "no projector registered"
    assert line["monthly_cost_usd"] is None
    assert line["meter_kind"] == "some-brand-new-meter"


def test_agentic_and_semantic_are_distinct_lines(tmp_path):
    client = _client(tmp_path)
    agentic = project_meter_demand(
        _demand(SEARCH_AGENTIC_RETRIEVAL, "retrieval-subqueries", 10000), client
    )
    semantic = project_meter_demand(
        _demand(SEARCH_SEMANTIC_RANKER, "requests", 10000), client
    )
    assert agentic["meter_kind"] != semantic["meter_kind"]
    assert agentic["pricing_status"] == "priced"
    assert semantic["pricing_status"] == "priced"


def test_document_intelligence_model_selects_rate(tmp_path):
    client = _client(tmp_path)
    read = project_meter_demand(
        _demand(DOCUMENT_INTELLIGENCE, "pages", 1000, {"model": "prebuilt-read"}), client
    )
    layout = project_meter_demand(
        _demand(DOCUMENT_INTELLIGENCE, "pages", 1000, {"model": "prebuilt-layout"}), client
    )
    assert read["monthly_cost_usd"] == 1000 * 0.0015
    assert layout["monthly_cost_usd"] == 1000 * 0.01
