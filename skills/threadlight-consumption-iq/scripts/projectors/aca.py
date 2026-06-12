"""
Azure Container Apps projector.

Math (see references/consumption-formulas.md § ACA):

  Consumption tier:
    monthly_cost = (vcpu_seconds * vcpu_price_per_second_usd)
                 + (memory_gib_seconds * mem_price_per_gib_second_usd)
                 + (requests * request_price_per_million_usd / 1_000_000)
    Free grants: first 180k vCPU-seconds, 360k GiB-seconds, 2M requests per month.

  Dedicated tier:
    monthly_cost = workload_profile_price_per_hour * 730    # always-on baseline
                 + per-replica overage if replicas > workload_profile_replicas

Alternatives compared:
  * Consumption ↔ Dedicated (D4 / D8 / E4 / E8)
  * Replica min/max sweep (1-3, 1-10, 2-20, 3-30)
"""
from __future__ import annotations

from typing import Any


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): aca.project — see references/consumption-formulas.md § ACA.
    raise NotImplementedError("aca.project is scaffolded but not yet implemented")
