"""Shared pytest configuration for threadlight-consumption-iq.

Forces the PricingClient into offline mode for the whole suite so no test ever
makes a network call to the Azure Retail Prices API. Projections resolve from
the dated in-repo fixtures instead, keeping runs fast and deterministic.
"""
import os


os.environ.setdefault("THREADLIGHT_PRICING_OFFLINE", "1")
