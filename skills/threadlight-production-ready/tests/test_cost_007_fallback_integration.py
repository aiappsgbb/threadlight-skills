"""COST-007 integration — the *actual* cost manifest produced by the shared
``cost_api`` (threadlight-consumption-iq) must drive the right COST-007 outcome.

This is the fail-closed regression guard for PR #116: a resource projected with a
region-blind ``price_source='fallback'`` rate is advisory only. The manifest the
producer emits for it must carry a ``pricing_status='not-priceable'`` line, so
COST-007 reads **must-fix** — never ``pass``. A fixture/live-priced projection
still reads ``pass``.

The manifest is produced (not hand-authored) so the producer and the gate stay
in lockstep — a producer that silently re-certifies a fallback line fails here.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Shared cost API lives in the consumption-iq skill; project it exactly as the
# qualify/estimate callers do (no discovery, offline pricing).
_REPO = Path(__file__).resolve().parents[3]
_CIQ_SCRIPTS = _REPO / "skills" / "threadlight-consumption-iq" / "scripts"
if str(_CIQ_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CIQ_SCRIPTS))
os.environ.setdefault("THREADLIGHT_PRICING_OFFLINE", "1")

_PR_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
_spec = importlib.util.spec_from_file_location("production_ready", _PR_SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("production_ready", mod)
_spec.loader.exec_module(mod)

PINNED = "2026-06-12T12:00:00+00:00"


def _producer():
    from cost_api import project_profile  # noqa: WPS433
    from pricing_client import PricingClient  # noqa: WPS433

    return project_profile, PricingClient


def _fallback_manifest(tmp_path):
    project_profile, PricingClient = _producer()
    client = PricingClient(cache_path=tmp_path / "cache.json")
    # Storage projector only carries region-blind fallback constants.
    return project_profile(
        load_profile={"workload_class": "batch", "storage_gb_year_one": 100.0},
        resources=[
            {
                "resource_kind": "Microsoft.Storage/storageAccounts",
                "logical_name": "blob",
                "region": "eastus2",
                "current_sku": {
                    "name": "Standard_LRS",
                    "tier": "Standard",
                    "extra": {"redundancy": "LRS", "access_tier": "hot"},
                },
            }
        ],
        selectors={},
        pricing=client,
        transaction_unit="document",
        monthly_transactions=10000,
        generated_at=PINNED,
    )


def _fixture_priced_manifest(tmp_path):
    project_profile, PricingClient = _producer()
    client = PricingClient(cache_path=tmp_path / "cache.json")
    # Meters resolve from the dated in-repo fixtures → all priced + verified.
    return project_profile(
        load_profile={
            "workload_class": "batch",
            "business_hours_only": False,
            "pages_per_month": 10000,
            "embedding_tokens_per_month": 5_000_000,
        },
        resources=[],
        selectors={
            "content_understanding": {"enabled": True, "tier": "standard"},
            "embeddings": {"enabled": True, "model": "text-embedding-3-small"},
        },
        pricing=client,
        transaction_unit="document",
        monthly_transactions=10000,
        generated_at=PINNED,
    )


def test_cost007_must_fix_on_produced_fallback_manifest(tmp_path):
    manifest = _fallback_manifest(tmp_path)
    # Producer truth: the fallback line is not-priceable and the bill is partial.
    assert manifest["status"] == "partial"
    assert manifest["resources"][0]["pricing_status"] == "not-priceable"

    finding = mod._check_cost_007(manifest)
    assert finding.status == "must-fix", f"got {finding.status!r}: {finding.detail}"
    assert "not-priceable" in finding.detail.lower()


def test_cost007_pass_on_produced_fixture_priced_manifest(tmp_path):
    manifest = _fixture_priced_manifest(tmp_path)
    assert manifest["status"] == "complete"
    finding = mod._check_cost_007(manifest)
    assert finding.status == "pass", f"got {finding.status!r}: {finding.detail}"
