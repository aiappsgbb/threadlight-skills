"""AI Search semantic ranker requests meter projector."""
from __future__ import annotations

from typing import Any

from ._meter_base import project_usage_meter

METER_KIND = "search-semantic-ranker"


def project(demand: dict[str, Any], pricing_client: Any) -> dict[str, Any]:
    return project_usage_meter(demand, pricing_client, METER_KIND)
