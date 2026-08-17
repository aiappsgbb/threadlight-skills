"""
Normalized meter-demand discovery (`meter_demand.py`).

The v1 cost engine models *resources* (an ACA app, an AI Search service). Modern
Foundry pilots also bill *consumption meters* that are not 1:1 with a resource —
Content Understanding extraction, Document Intelligence pages, Speech hours,
embeddings tokens, agentic retrieval sub-queries, the semantic ranker, and web
grounding transactions. These are selected in the SPEC and driven by declared
monthly volumes.

:func:`discover_meter_demands` turns ``(resources, load_profile, selectors)``
into a **deterministic**, ``meter_kind``-sorted list of demand rows. Each row
carries:

  * ``meter_kind``     — the stable meter identifier a projector registers for;
  * ``source``         — provenance, e.g. ``spec.selector.content_understanding``;
  * ``volume_driver``  — ``{unit, monthly_quantity}`` (quantity may be ``None``);
  * ``verified``       — ``False`` when a meter is selected but has no volume
                         evidence. Such a row is **never dropped** — it flows on
                         as ``not-verified`` so the incomplete-total semantics
                         hold (we never silently bill a selected meter as $0).
  * ``selector``       — the raw selector object (CU tier, DocIntel model,
                         semantic/agentic fan-out) for the projector to read.

Selector-driven, stdlib only, no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Meter kind constants (stable identifiers shared with the projector registry).
CONTENT_UNDERSTANDING_EXTRACTION = "content-understanding-extraction"
CONTENT_UNDERSTANDING_CONTEXTUALIZATION = "content-understanding-contextualization"
DOCUMENT_INTELLIGENCE = "document-intelligence"
SPEECH = "speech"
EMBEDDINGS = "embeddings"
SEARCH_AGENTIC_RETRIEVAL = "search-agentic-retrieval"
SEARCH_SEMANTIC_RANKER = "search-semantic-ranker"
WEB_GROUNDING = "web-grounding"


@dataclass(frozen=True)
class _MeterSpec:
    meter_kind: str
    selector_key: str
    unit: str
    # First volume key that resolves to a positive number wins.
    volume_keys: tuple[str, ...] = ()


# Ordering here is irrelevant — the output is sorted by meter_kind — but the
# table is the single source of truth for "what selects which meter".
_METER_SPECS: tuple[_MeterSpec, ...] = (
    _MeterSpec(
        CONTENT_UNDERSTANDING_EXTRACTION,
        "content_understanding",
        "pages",
        ("pages_per_month",),
    ),
    _MeterSpec(
        CONTENT_UNDERSTANDING_CONTEXTUALIZATION,
        "content_contextualization",
        "pages-or-images",
        ("contextualization_items_per_month",),
    ),
    _MeterSpec(
        DOCUMENT_INTELLIGENCE,
        "document_intelligence",
        "pages",
        ("document_intelligence_pages_per_month", "pages_per_month"),
    ),
    _MeterSpec(
        SPEECH,
        "speech",
        "hours",
        ("media_hours_per_month",),
    ),
    _MeterSpec(
        EMBEDDINGS,
        "embeddings",
        "tokens",
        ("embedding_tokens_per_month",),
    ),
    _MeterSpec(
        SEARCH_SEMANTIC_RANKER,
        "search_semantic",
        "requests",
        ("semantic_ranker_requests_per_month", "search_requests_per_month"),
    ),
    _MeterSpec(
        WEB_GROUNDING,
        "web_grounding",
        "transactions",
        ("web_grounding_transactions_per_month",),
    ),
)


def _selector_enabled(selector: Any) -> bool:
    """A meter is selected when its selector is present and not disabled.

    ``True`` / a non-empty dict enables the meter. ``{"enabled": false}``
    disables it. ``None`` / absent / ``False`` means not selected.
    """
    if selector is None or selector is False:
        return False
    if isinstance(selector, dict):
        return bool(selector.get("enabled", True))
    return bool(selector)


def _as_selector_dict(selector: Any) -> dict[str, Any]:
    return dict(selector) if isinstance(selector, dict) else {}


def _positive_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return value
    return None


def _resolve_volume(
    load_profile: dict[str, Any],
    selector: dict[str, Any],
    volume_keys: tuple[str, ...],
) -> float | int | None:
    """First positive value found across the selector then the load profile."""
    for key in volume_keys:
        found = _positive_number(selector.get(key))
        if found is not None:
            return found
        found = _positive_number(load_profile.get(key))
        if found is not None:
            return found
    return None


def _embedding_quantity(
    load_profile: dict[str, Any], selector: dict[str, Any]
) -> float | int | None:
    """Embeddings tokens: explicit ``embedding_tokens_per_month`` else derived.

    Derivation makes a document pipeline coherent — the same ``pages_per_month``
    that drives extraction also drives embeddings when the selector declares
    ``tokens_per_page`` (pages × tokens/page).
    """
    explicit = _resolve_volume(load_profile, selector, ("embedding_tokens_per_month",))
    if explicit is not None:
        return explicit
    pages = _positive_number(load_profile.get("pages_per_month"))
    tokens_per_page = _positive_number(
        selector.get("tokens_per_page") or load_profile.get("embedding_tokens_per_page")
    )
    if pages is not None and tokens_per_page is not None:
        return pages * tokens_per_page
    return None


def _agentic_quantity(
    load_profile: dict[str, Any], selector: dict[str, Any]
) -> float | int | None:
    """Agentic retrieval sub-queries = retrievals × fan-out.

    Returns ``None`` unless BOTH the retrieval volume and the fan-out are known —
    a half-specified meter stays not-verified rather than guessing a fan-out.
    """
    retrievals = _resolve_volume(
        load_profile, selector, ("retrievals_per_month", "search_requests_per_month")
    )
    fanout = _positive_number(
        selector.get("fanout") or load_profile.get("retrieval_fanout")
    )
    if retrievals is not None and fanout is not None:
        return retrievals * fanout
    return None


def discover_meter_demands(
    resources: list[dict[str, Any]] | None,
    load_profile: dict[str, Any] | None,
    selectors: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the deterministic, ``meter_kind``-sorted meter demand list.

    ``resources`` is accepted for signature stability (and future
    resource-implied detection) but v1 detection is selector-driven.
    """
    load_profile = load_profile or {}
    selectors = selectors or {}
    demands: list[dict[str, Any]] = []

    for spec in _METER_SPECS:
        raw_selector = selectors.get(spec.selector_key)
        if not _selector_enabled(raw_selector):
            continue
        selector = _as_selector_dict(raw_selector)

        if spec.meter_kind == EMBEDDINGS:
            quantity = _embedding_quantity(load_profile, selector)
        else:
            quantity = _resolve_volume(load_profile, selector, spec.volume_keys)

        demands.append(
            _make_demand(spec.meter_kind, spec.selector_key, spec.unit, quantity, selector)
        )

    # search-agentic-retrieval has a composite driver (retrievals × fan-out).
    agentic_raw = selectors.get("search_agentic")
    if _selector_enabled(agentic_raw):
        selector = _as_selector_dict(agentic_raw)
        quantity = _agentic_quantity(load_profile, selector)
        demands.append(
            _make_demand(
                SEARCH_AGENTIC_RETRIEVAL,
                "search_agentic",
                "retrieval-subqueries",
                quantity,
                selector,
            )
        )

    demands.sort(key=lambda d: d["meter_kind"])
    return demands


def _make_demand(
    meter_kind: str,
    selector_key: str,
    unit: str,
    quantity: float | int | None,
    selector: dict[str, Any],
) -> dict[str, Any]:
    verified = quantity is not None
    demand: dict[str, Any] = {
        "meter_kind": meter_kind,
        "source": f"spec.selector.{selector_key}",
        "volume_driver": {"unit": unit, "monthly_quantity": quantity},
        "verified": verified,
        "selector": selector,
    }
    if not verified:
        demand["reason"] = (
            f"{meter_kind} selected but no volume evidence declared for {unit}"
        )
    return demand
