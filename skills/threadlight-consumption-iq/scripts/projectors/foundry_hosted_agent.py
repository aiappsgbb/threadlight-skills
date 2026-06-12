"""
Foundry hosted-agent tier projector.

Math (see references/consumption-formulas.md § Foundry hosted-agent):

  monthly_cost = hosted_agent_tier_price_per_month_usd
               + agent_message_count * per_message_price_usd

Alternatives compared: adjacent tiers up/down from current_sku.tier.
"""
from __future__ import annotations

from typing import Any


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): foundry_hosted_agent.project — see references/consumption-formulas.md.
    raise NotImplementedError(
        "foundry_hosted_agent.project is scaffolded but not yet implemented"
    )
