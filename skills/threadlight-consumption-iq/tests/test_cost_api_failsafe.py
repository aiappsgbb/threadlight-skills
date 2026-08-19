"""Fail-safe tests for the vNext cost API (`cost_api.py`).

Two fail-closed invariants regressed by PR #116:

Issue 1 — a resource projected with ``price_source == 'fallback'`` (a region-blind
constant used when live + fixture both miss) must be advisory/non-certifying:
it may keep its numeric ``monthly_cost_usd`` on the line, but the line is
``pricing_status='not-priceable'`` + ``verified=False`` with a static reason, so
the manifest goes ``status='partial'``, ``totals.complete=False`` and
``cost_per_transaction_usd=None``. A true fixture/live price still certifies.

Issue 2 — ``ptu_units`` is a strictly-positive Draft-07 integer. A fractional
value (2.5) is rejected before output; an integral float (2.0) normalises to the
int 2; the emitted value is always an ``int`` and validates against the schema.
"""
from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cost_api import (  # noqa: E402
    build_cost_manifest,
    build_ptu_scenarios,
    project_profile,
)
from pricing_client import PricingClient  # noqa: E402

PINNED = "2026-06-12T12:00:00+00:00"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "cost-manifest.schema.json"
)


def _client(tmp_path, meters_fixture=None):
    return PricingClient(cache_path=tmp_path / "cache.json", meters_fixture=meters_fixture)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _jsonschema():
    try:
        import jsonschema
    except ModuleNotFoundError:
        return None
    return jsonschema


def _validate_manifest(manifest: dict) -> None:
    schema = _schema()
    validator = _jsonschema()
    if validator is not None:
        validator.Draft7Validator(schema).validate(manifest)
    else:
        assert schema["properties"]["ptu_scenarios"]["properties"]["ptu_units"] == {
            "type": "integer",
            "minimum": 1,
        }
        assert manifest["schema_version"] == "2.0"


def _validate_ptu_scenarios(ptu: dict) -> None:
    schema = _schema()["properties"]["ptu_scenarios"]
    assert schema["properties"]["ptu_units"] == {"type": "integer", "minimum": 1}
    assert isinstance(ptu["ptu_units"], int) and not isinstance(ptu["ptu_units"], bool)
    validator = _jsonschema()
    if validator is not None:
        validator.Draft7Validator(schema).validate(ptu)


def _storage_resource() -> dict:
    # The storage projector only has region-blind fallback constants (no live /
    # fixture rate), so it always emits price_source='fallback' with a numeric
    # monthly_cost_usd — the exact shape that must NOT certify a bill.
    return {
        "resource_kind": "Microsoft.Storage/storageAccounts",
        "resource_id": "declared/blob",
        "logical_name": "blob",
        "region": "eastus2",
        "current_sku": {
            "name": "Standard_LRS",
            "tier": "Standard",
            "extra": {"redundancy": "LRS", "access_tier": "hot"},
        },
    }


def test_schema_checks_remain_useful_without_optional_jsonschema(monkeypatch):
    real_import = builtins.__import__

    def import_without_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ModuleNotFoundError("jsonschema intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_jsonschema)
    _validate_ptu_scenarios({"ptu_units": 2})


# ---------------------------------------------------------------------------
# Issue 1 — fallback rate is advisory, never certifying
# ---------------------------------------------------------------------------


def test_fallback_resource_via_project_profile_is_not_certifying(tmp_path):
    """Integration: a real fallback-priced resource must fail closed.

    Before the fix this manifest is (wrongly) status=complete, totals.complete
    True and cost_per_transaction non-null.
    """
    client = _client(tmp_path)
    manifest = project_profile(
        load_profile={"workload_class": "batch", "storage_gb_year_one": 100.0},
        resources=[_storage_resource()],
        selectors={},
        pricing=client,
        transaction_unit="document",
        monthly_transactions=10000,
        generated_at=PINNED,
    )

    line = manifest["resources"][0]
    assert line["price_source"] == "fallback"
    # Advisory: the numeric rate is retained on the line …
    assert isinstance(line["monthly_cost_usd"], (int, float))
    # … but the line never certifies.
    assert line["pricing_status"] == "not-priceable"
    assert line["verified"] is False
    assert isinstance(line.get("reason"), str) and line["reason"].strip()

    assert manifest["status"] == "partial"
    assert manifest["totals"]["complete"] is False
    assert manifest["totals"]["monthly_cost_current_usd"] is None
    assert manifest["totals"]["cost_per_transaction_usd"] is None
    # The known subtotal is priced+verified lines only — the advisory fallback
    # is not summed into it.
    assert manifest["totals"]["monthly_cost_known_usd"] == 0.0
    # meter_coverage must expose the gap, never present it complete.
    assert manifest["meter_coverage"]["status"] != "complete"
    _validate_manifest(manifest)


def test_fallback_resource_line_marked_not_priceable(tmp_path):
    """Unit: build_cost_manifest annotates a fallback line as not-priceable."""
    client = _client(tmp_path)
    fallback = {
        "resource_kind": "Microsoft.DocumentDB/databaseAccounts",
        "logical_name": "cosmos",
        "monthly_cost_usd": 512.34,
        "price_source": "fallback",
    }
    manifest = build_cost_manifest(
        resources=[fallback],
        meters=[],
        load_profile={},
        transaction_unit="document",
        monthly_transactions=10000,
        pricing=client,
        generated_at=PINNED,
    )
    line = manifest["resources"][0]
    assert line["pricing_status"] == "not-priceable"
    assert line["verified"] is False
    assert isinstance(line.get("reason"), str) and line["reason"].strip()
    # Numeric advisory retained on the line, excluded from the known subtotal.
    assert line["monthly_cost_usd"] == 512.34
    assert manifest["totals"]["monthly_cost_known_usd"] == 0.0
    assert manifest["status"] == "partial"
    assert manifest["totals"]["complete"] is False
    assert manifest["totals"]["cost_per_transaction_usd"] is None
    _validate_manifest(manifest)


def test_fixture_priced_resource_line_still_certifies(tmp_path):
    """Control: a fixture/live-priced resource stays priced + verified + complete."""
    client = _client(tmp_path)
    priced = {
        "resource_kind": "Microsoft.Storage/storageAccounts",
        "logical_name": "blob",
        "monthly_cost_usd": 42.0,
        "price_source": "fixture",
    }
    manifest = build_cost_manifest(
        resources=[priced],
        meters=[],
        load_profile={},
        transaction_unit="document",
        monthly_transactions=1000,
        pricing=client,
        generated_at=PINNED,
    )
    line = manifest["resources"][0]
    assert line["pricing_status"] == "priced"
    assert line["verified"] is True
    assert manifest["status"] == "complete"
    assert manifest["totals"]["complete"] is True
    assert manifest["totals"]["monthly_cost_current_usd"] == 42.0
    assert manifest["totals"]["cost_per_transaction_usd"] is not None
    assert manifest["meter_coverage"]["status"] == "complete"
    _validate_manifest(manifest)


def test_resource_without_price_source_is_still_priced(tmp_path):
    """Absent price_source (v1 synthetic line) with a numeric cost stays priced —
    only an explicit 'fallback' source demotes the line."""
    client = _client(tmp_path)
    manifest = build_cost_manifest(
        resources=[{"resource_kind": "X", "monthly_cost_usd": 12.5}],
        meters=[],
        load_profile={},
        transaction_unit="x",
        monthly_transactions=100,
        pricing=client,
        generated_at=PINNED,
    )
    assert manifest["resources"][0]["pricing_status"] == "priced"
    assert manifest["totals"]["complete"] is True


def test_fixture_and_fallback_mix_is_partial_but_keeps_priced_subtotal(tmp_path):
    """A priced line + a fallback line → partial, and the known subtotal reflects
    the priced line only (never the advisory fallback, never a $0 for it)."""
    client = _client(tmp_path)
    manifest = build_cost_manifest(
        resources=[
            {"resource_kind": "A", "monthly_cost_usd": 30.0, "price_source": "fixture"},
            {"resource_kind": "B", "monthly_cost_usd": 999.0, "price_source": "fallback"},
        ],
        meters=[],
        load_profile={},
        transaction_unit="x",
        monthly_transactions=100,
        pricing=client,
        generated_at=PINNED,
    )
    statuses = {r["resource_kind"]: r["pricing_status"] for r in manifest["resources"]}
    assert statuses == {"A": "priced", "B": "not-priceable"}
    assert manifest["status"] == "partial"
    assert manifest["totals"]["complete"] is False
    assert manifest["totals"]["cost_per_transaction_usd"] is None
    # priced subtotal = 30.0 only (fallback 999.0 excluded, not summed as zero)
    assert manifest["totals"]["monthly_cost_known_usd"] == 30.0


# ---------------------------------------------------------------------------
# Issue 2 — ptu_units is a strictly-positive integer
# ---------------------------------------------------------------------------


def test_ptu_units_fractional_rejected(tmp_path):
    client = _client(tmp_path)
    with pytest.raises(ValueError, match="ptu_units"):
        build_ptu_scenarios(
            {"ptu_model": "gpt-4o", "pinned_region": "eastus2", "ptu_units": 2.5},
            client,
        )


def test_ptu_units_fractional_rejected_via_project_profile(tmp_path):
    client = _client(tmp_path)
    with pytest.raises(ValueError, match="ptu_units"):
        project_profile(
            load_profile={"ptu_model": "gpt-4o", "pinned_region": "eastus2", "ptu_units": 2.5},
            resources=[],
            selectors={},
            pricing=client,
            transaction_unit="x",
            monthly_transactions=100,
            generated_at=PINNED,
        )


def test_ptu_units_integer_emits_int_and_schema_valid(tmp_path):
    client = _client(tmp_path)
    ptu = build_ptu_scenarios(
        {"ptu_model": "gpt-4o", "pinned_region": "eastus2", "ptu_units": 2}, client
    )
    assert ptu is not None
    assert ptu["ptu_units"] == 2
    _validate_ptu_scenarios(ptu)


def test_ptu_units_integral_float_normalized_to_int(tmp_path):
    client = _client(tmp_path)
    ptu = build_ptu_scenarios(
        {"ptu_model": "gpt-4o", "pinned_region": "eastus2", "ptu_units": 2.0}, client
    )
    assert ptu is not None
    assert ptu["ptu_units"] == 2
    _validate_ptu_scenarios(ptu)


def test_ptu_units_emitted_int_validates_in_full_manifest(tmp_path):
    client = _client(tmp_path)
    manifest = project_profile(
        load_profile={"ptu_model": "gpt-4o", "pinned_region": "eastus2", "ptu_units": 2.0},
        resources=[],
        selectors={},
        pricing=client,
        transaction_unit="x",
        monthly_transactions=100,
        generated_at=PINNED,
    )
    assert manifest["ptu_scenarios"]["ptu_units"] == 2
    assert isinstance(manifest["ptu_scenarios"]["ptu_units"], int)
    _validate_manifest(manifest)
