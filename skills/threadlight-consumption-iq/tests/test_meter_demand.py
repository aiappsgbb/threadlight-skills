"""Tests for normalized meter-demand discovery (`meter_demand.py`)."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from meter_demand import (  # noqa: E402
    discover_meter_demands,
    CONTENT_UNDERSTANDING_EXTRACTION,
    CONTENT_UNDERSTANDING_CONTEXTUALIZATION,
    DOCUMENT_INTELLIGENCE,
    EMBEDDINGS,
    SEARCH_AGENTIC_RETRIEVAL,
    SEARCH_SEMANTIC_RANKER,
    SPEECH,
    WEB_GROUNDING,
)


def _kinds(demands):
    return [d["meter_kind"] for d in demands]


def test_output_sorted_by_meter_kind():
    selectors = {
        "web_grounding": {"enabled": True},
        "content_understanding": {"enabled": True},
        "speech": {"enabled": True},
    }
    profile = {
        "pages_per_month": 1000,
        "media_hours_per_month": 10,
        "web_grounding_transactions_per_month": 500,
    }
    demands = discover_meter_demands([], profile, selectors)
    kinds = _kinds(demands)
    assert kinds == sorted(kinds)


def test_document_workload_emits_extraction_and_embedding():
    """A document pipeline (pages + embeddings) emits BOTH meters."""
    selectors = {
        "content_understanding": {"enabled": True, "tier": "standard"},
        "embeddings": {"enabled": True, "model": "text-embedding-3-small", "tokens_per_page": 800},
    }
    profile = {"pages_per_month": 12000}
    demands = discover_meter_demands([], profile, selectors)
    kinds = _kinds(demands)
    assert CONTENT_UNDERSTANDING_EXTRACTION in kinds
    assert EMBEDDINGS in kinds
    extraction = next(d for d in demands if d["meter_kind"] == CONTENT_UNDERSTANDING_EXTRACTION)
    embeddings = next(d for d in demands if d["meter_kind"] == EMBEDDINGS)
    assert extraction["volume_driver"] == {"unit": "pages", "monthly_quantity": 12000}
    # embeddings tokens derived from pages × tokens_per_page
    assert embeddings["volume_driver"]["unit"] == "tokens"
    assert embeddings["volume_driver"]["monthly_quantity"] == 12000 * 800
    assert embeddings["verified"] is True


def test_ai_search_features_are_distinct_meters():
    selectors = {
        "search_agentic": {"enabled": True, "fanout": 5},
        "search_semantic": {"enabled": True},
    }
    profile = {"retrievals_per_month": 2000, "semantic_ranker_requests_per_month": 8000}
    demands = discover_meter_demands([], profile, selectors)
    kinds = _kinds(demands)
    assert SEARCH_AGENTIC_RETRIEVAL in kinds
    assert SEARCH_SEMANTIC_RANKER in kinds
    assert SEARCH_AGENTIC_RETRIEVAL != SEARCH_SEMANTIC_RANKER
    agentic = next(d for d in demands if d["meter_kind"] == SEARCH_AGENTIC_RETRIEVAL)
    # retrieval-subqueries = retrievals × fanout
    assert agentic["volume_driver"]["unit"] == "retrieval-subqueries"
    assert agentic["volume_driver"]["monthly_quantity"] == 2000 * 5


def test_selected_meter_missing_volume_is_not_dropped():
    """A selected meter with no volume evidence stays as a not-verified row."""
    selectors = {"speech": {"enabled": True}}
    profile = {}  # no media_hours_per_month
    demands = discover_meter_demands([], profile, selectors)
    assert _kinds(demands) == [SPEECH]
    speech = demands[0]
    assert speech["verified"] is False
    assert speech["volume_driver"]["monthly_quantity"] is None
    assert "reason" in speech


def test_disabled_selector_emits_no_demand():
    selectors = {"web_grounding": {"enabled": False}}
    profile = {"web_grounding_transactions_per_month": 100}
    demands = discover_meter_demands([], profile, selectors)
    assert demands == []


def test_agentic_without_fanout_is_not_verified():
    selectors = {"search_agentic": {"enabled": True}}  # no fanout
    profile = {"retrievals_per_month": 2000}
    demands = discover_meter_demands([], profile, selectors)
    agentic = next(d for d in demands if d["meter_kind"] == SEARCH_AGENTIC_RETRIEVAL)
    assert agentic["verified"] is False
    assert agentic["volume_driver"]["monthly_quantity"] is None


def test_source_provenance_recorded():
    selectors = {"document_intelligence": {"enabled": True, "model": "prebuilt-layout"}}
    profile = {"pages_per_month": 5000}
    demands = discover_meter_demands([], profile, selectors)
    di = next(d for d in demands if d["meter_kind"] == DOCUMENT_INTELLIGENCE)
    assert di["source"] == "spec.selector.document_intelligence"
    assert di["selector"]["model"] == "prebuilt-layout"


def test_contextualization_distinct_from_extraction():
    selectors = {
        "content_understanding": {"enabled": True},
        "content_contextualization": {"enabled": True},
    }
    profile = {"pages_per_month": 1000, "contextualization_items_per_month": 400}
    demands = discover_meter_demands([], profile, selectors)
    kinds = _kinds(demands)
    assert CONTENT_UNDERSTANDING_EXTRACTION in kinds
    assert CONTENT_UNDERSTANDING_CONTEXTUALIZATION in kinds
    ctx = next(d for d in demands if d["meter_kind"] == CONTENT_UNDERSTANDING_CONTEXTUALIZATION)
    assert ctx["volume_driver"] == {"unit": "pages-or-images", "monthly_quantity": 400}


def test_empty_selectors_yields_empty():
    assert discover_meter_demands([], {"pages_per_month": 100}, {}) == []
    assert discover_meter_demands([], {}, None) == []
