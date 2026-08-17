"""Speech (STT/TTS) hours meter projector."""
from __future__ import annotations

from typing import Any

from ._meter_base import project_usage_meter

METER_KIND = "speech"


def project(demand: dict[str, Any], pricing_client: Any) -> dict[str, Any]:
    return project_usage_meter(demand, pricing_client, METER_KIND)
