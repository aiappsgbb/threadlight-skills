"""
Tests for reconcile.py — join an existing forecast (`cost-manifest.json`),
observed actuals (`threadlight-cost-actuals/v1`), and SPEC §14's
`value_model` policy into a pure, offline
`threadlight-cost-reconciliation/v1` manifest.

See `skills/threadlight-consumption-iq/references/cost-reconciliation-manifest-schema.md`
for the emitted schema, and
`docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§7.3 / §9 / §10 for the RFC this module implements.

Core contract under test:
  - `actual_window_usd` comes ONLY from `actuals.cost.period_total_usd`.
    Azure Monitor token reprice is attribution evidence and is never added
    to (or substituted for) the Cost Management total (RFC §9.1).
  - Money is `Decimal` with `ROUND_HALF_UP` at serialization; ratios and
    rates have their own documented precisions. Refunds stay negative.
  - The manifest is ALWAYS emitted: an incomplete/invalid policy, absent
    interaction evidence, or absent token evidence each degrade a specific
    named status to `not-verified` while every observed number is still
    reported. Only structurally malformed *evidence* raises.
  - Two distinct coverage measures: the actuals manifest's source
    `resource_id_coverage_pct` (copied through, diagnostic) and this
    module's own `projection_attribution_coverage_pct` (gated).
  - Maturity fails closed: every named check is declared, and the overall
    verdict is `pass` only when all of them pass.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from reconcile import (  # noqa: E402
    MATURITY_CHECK_IDS,
    ReconciliationInputError,
    evaluate_maturity,
    reconcile_costs,
    sha256_json,
)


RID = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/containerApps/a"
AOAI_ACCOUNT = (
    "/subscriptions/s/resourceGroups/rg/providers/"
    "Microsoft.CognitiveServices/accounts/aoai1"
)
AOAI_DEPLOYMENT = AOAI_ACCOUNT + "/deployments/chat"
STORAGE_RID = (
    "/subscriptions/s/resourceGroups/rg/providers/"
    "Microsoft.Storage/storageAccounts/unmodeled"
)
GENERATED = "2026-08-10T00:00:00Z"
# A real 64-hex SHA-256 digest: `policy_ref.spec_sha256` is an audit anchor a
# consumer re-derives from the SPEC bytes, so a placeholder string is not a
# usable fixture for the code path that validates it.
SPEC_SHA256 = hashlib.sha256(b"# SPEC section 14\n").hexdigest()


def forecast(total=300.0):
    return {
        "schema_version": "1.0",
        "price_basis": "retail",
        "totals": {"monthly_cost_current_usd": total},
        "resources": [{
            "resource_id": RID,
            "resource_kind": "Microsoft.App/containerApps",
            "monthly_cost_usd": total,
        }],
        "recommendations": [],
    }


def actuals(total=70.0, successes=100):
    return {
        "schema": "threadlight-cost-actuals/v1",
        "status": "pass",
        "scope": {"subscription_id": "sub-a", "resource_group": "rg-a"},
        "window": {
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
            "complete_days": 7,
            "settlement_age_hours": 48,
            "window_end_age_days": 2,
        },
        "cost": {
            "basis": "usage-pretax",
            "cost_column": "PreTaxCost",
            "currency": "USD",
            "period_total_usd": total,
            "resources": [{
                "resource_id": RID,
                "resource_type": "Microsoft.App/containerApps",
                "service_name": "Azure Container Apps",
                "period_cost_usd": total,
            }],
            "unattributed_usd": 0.0,
            "resource_id_coverage_pct": 1.0,
        },
        "usage": {
            "interaction_status": "pass",
            "model_attribution_status": "not-verified",
            "total_interactions": successes + 5,
            "successful_interactions": successes,
            "models": [],
        },
    }


def policy():
    return {
        "cost": {
            "maturity_policy": {
                "min_complete_days": 7,
                "min_successful_interactions": 100,
                "min_cost_settlement_age_hours": 48,
                "max_window_end_age_days": 14,
                "min_projection_attribution_coverage_pct": 0.95,
            },
            "success_event": {
                "name": "return_decision_completed",
                "trace_attribute": "decision.outcome",
                "success_values": ["approved"],
            },
            "baseline": {
                "target_cost_per_successful_interaction_usd": 1.0,
                "max_forecast_variance_pct": 0.20,
                "max_token_volume_variance_pct": 0.25,
            },
            "accounting": {
                "actual_cost_basis": "usage-pretax",
                "actual_billing_price_basis": "retail",
                "forecast_price_basis": "retail",
                "allow_basis_mismatch_for_verdict": False,
                "scope_policy": "dedicated_resource_group",
            },
        }
    }


def run(f=None, a=None, p=None, errors=None, **kwargs):
    return reconcile_costs(
        f or forecast(),
        a or actuals(),
        policy() if p is None else p,
        policy_errors=errors or [],
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
        **kwargs,
    )


def check(result, check_id):
    for entry in result["maturity"]["checks"]:
        if entry["id"] == check_id:
            return entry
    raise AssertionError(f"no maturity check {check_id!r}")


# ---------------------------------------------------------------------------
# sha256_json — canonical hashing
# ---------------------------------------------------------------------------


def test_sha256_json_is_canonical_sorted_compact_ascii() -> None:
    doc = {"b": 1, "a": {"d": 2, "c": [3, 4]}}
    expected = hashlib.sha256(
        json.dumps(
            doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    assert sha256_json(doc) == expected


def test_sha256_json_ignores_key_insertion_order() -> None:
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})


def test_sha256_json_is_stable_for_non_ascii_content() -> None:
    """`ensure_ascii=True` pins one byte encoding for the same characters,
    so a hash computed on one machine's locale/encoding settings matches
    another's."""
    doc = {"note": "café €"}
    assert sha256_json(doc) == sha256_json(json.loads(json.dumps(doc)))
    assert len(sha256_json(doc)) == 64


def test_sha256_json_rejects_a_non_serializable_document() -> None:
    with pytest.raises(ReconciliationInputError):
        sha256_json({"bad": {1, 2}})


def test_sha256_json_rejects_a_non_mapping() -> None:
    with pytest.raises(ReconciliationInputError):
        sha256_json([1, 2, 3])


# ---------------------------------------------------------------------------
# RFC §9.1 — the authoritative total
# ---------------------------------------------------------------------------


def test_token_reprice_is_never_added_to_actual_total() -> None:
    a = actuals()
    a["usage"]["models"] = [{
        "model": "gpt-5.4",
        "input_tokens": 1000,
        "output_tokens": 100,
        "retail_repriced_cost_usd": 999.0,
    }]
    assert run(a=a)["totals"]["actual_window_usd"] == 70.0


def test_token_reprice_never_substitutes_for_a_missing_total() -> None:
    """Absent Cost Management evidence is `not-verified`, never quietly
    backfilled from a token reprice that happens to be present."""
    a = actuals()
    a["cost"]["period_total_usd"] = None
    a["usage"]["models"] = [{
        "model": "gpt-5.4",
        "input_tokens": 1000,
        "output_tokens": 100,
        "retail_repriced_cost_usd": 999.0,
    }]
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] is None
    assert result["totals"]["actual_monthly_run_rate_usd"] is None
    assert result["totals"]["variance_window_usd"] is None
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None
    assert result["status"] == "not-verified"


def test_expected_scope_matching_actuals_passes() -> None:
    result = run(
        expected_subscription_id="sub-a",
        expected_resource_group="rg-a",
    )
    assert result["status"] == "pass"


def test_mismatched_expected_subscription_raises() -> None:
    with pytest.raises(
        ReconciliationInputError,
        match="actuals scope subscription_id 'sub-a' does not match expected 'sub-b'",
    ):
        run(expected_subscription_id="sub-b")


def test_mismatched_expected_resource_group_raises() -> None:
    with pytest.raises(
        ReconciliationInputError,
        match="actuals scope resource_group 'rg-a' does not match expected 'rg-b'",
    ):
        run(expected_resource_group="rg-b")


def test_reconcile_costs_takes_no_token_cost_argument() -> None:
    """The type boundary itself is what prevents double counting: there is
    no parameter through which a token reprice could ever be passed in.

    The signature is pinned exactly, so a new parameter has to be added here
    deliberately — the provenance paths below carry no money and take part
    in no digest."""
    import inspect

    import reconcile

    names = set(inspect.signature(reconcile.reconcile_costs).parameters)
    assert names == {
        "forecast",
        "actuals",
        "policy",
        "expected_subscription_id",
        "expected_resource_group",
        "policy_errors",
        "generated_at",
        "policy_spec_sha256",
        "forecast_path",
        "actuals_path",
        "policy_path",
    }
    assert not [name for name in names if "token" in name or "cost_usd" in name]


# ---------------------------------------------------------------------------
# RFC §9.2 — window alignment and money arithmetic
# ---------------------------------------------------------------------------


def test_window_and_monthly_normalization() -> None:
    result = run()
    assert result["totals"]["forecast_monthly_usd"] == 300.0
    assert result["totals"]["forecast_window_usd"] == 70.0
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["totals"]["actual_monthly_run_rate_usd"] == 300.0
    assert result["totals"]["variance_window_usd"] == 0.0
    assert result["totals"]["variance_pct"] == 0.0


def test_variance_is_actual_minus_forecast() -> None:
    result = run(a=actuals(total=84.0))
    assert result["totals"]["variance_window_usd"] == 14.0
    assert result["totals"]["variance_pct"] == 0.2
    assert result["totals"]["actual_monthly_run_rate_usd"] == 360.0


def test_negative_variance_is_reported_signed() -> None:
    result = run(a=actuals(total=56.0))
    assert result["totals"]["variance_window_usd"] == -14.0
    assert result["totals"]["variance_pct"] == -0.2


def test_money_is_rounded_half_up_not_bankers() -> None:
    """`0.75 * 1 / 30` is exactly `0.025`: half-up gives `0.03`, while
    Python's float `round()` (banker's rounding on an already-imprecise
    binary float) gives `0.02`."""
    a = actuals()
    a["window"]["complete_days"] = 1
    result = run(f=forecast(0.75), a=a)
    assert result["totals"]["forecast_window_usd"] == 0.03


def test_zero_forecast_yields_null_variance_pct() -> None:
    result = run(f=forecast(0.0))
    assert result["totals"]["forecast_window_usd"] == 0.0
    assert result["totals"]["variance_window_usd"] == 70.0
    assert result["totals"]["variance_pct"] is None
    assert any("variance_pct" in warning for warning in result["warnings"])


def test_zero_forecast_variance_status_is_not_verified() -> None:
    assert run(f=forecast(0.0))["variance_status"] == "not-verified"


def test_missing_complete_days_blocks_window_math_without_raising() -> None:
    a = actuals()
    del a["window"]["complete_days"]
    result = run(a=a)
    assert result["totals"]["forecast_window_usd"] is None
    assert result["totals"]["actual_monthly_run_rate_usd"] is None
    assert result["totals"]["variance_pct"] is None
    # The observed period total is still reported.
    assert result["totals"]["actual_window_usd"] == 70.0
    assert check(result, "complete_days")["status"] == "not-verified"


def test_non_positive_complete_days_is_rejected() -> None:
    a = actuals()
    a["window"]["complete_days"] = 0
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_non_integer_complete_days_is_rejected() -> None:
    a = actuals()
    a["window"]["complete_days"] = 7.5
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_refund_only_window_keeps_a_negative_total() -> None:
    a = actuals(total=-10.0)
    a["cost"]["resources"][0]["period_cost_usd"] = -10.0
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == -10.0
    assert result["totals"]["actual_monthly_run_rate_usd"] == -42.86
    assert result["totals"]["variance_window_usd"] == -80.0


# ---------------------------------------------------------------------------
# RFC §9.4 — attribution, matching and the two coverage measures
# ---------------------------------------------------------------------------


def test_unmodeled_resource_of_a_different_type_remains_in_actual_total() -> None:
    """The unmatched actual resource must be a genuinely *different* ARM type,
    or the type fallback below would legitimately pair it with the forecast
    resource and this would stop testing unmodeled cost at all."""
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = STORAGE_RID
    a["cost"]["resources"][0]["resource_type"] = "Microsoft.Storage/storageAccounts"
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0
    assert result["coverage"]["forecast_not_observed_usd"] == 70.0
    assert result["coverage"]["matched_resources"] == []
    assert [entry["resource_id"] for entry in result["coverage"]["unmodeled_resources"]] == [
        STORAGE_RID
    ]


def test_unique_type_fallback_attributes_a_renamed_resource() -> None:
    """Exactly one unmatched forecast resource and exactly one unmatched
    actual resource share the normalized type, so the pairing is unambiguous
    and the fallback applies."""
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = RID + "-renamed"
    result = run(a=a)
    assert result["coverage"]["unmodeled_actual_usd"] == 0.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0
    matched = result["coverage"]["matched_resources"][0]
    assert matched["match_method"] == "unique_type"
    assert matched["forecast_resource_ids"] == [RID]
    assert matched["actual_resource_id"] == RID + "-renamed"


def test_ambiguous_same_type_actuals_are_not_attributed() -> None:
    """Two unmatched actual resources share the forecast resource's type, so
    there is no unique pairing. Guessing one would silently attribute the
    wrong spend, so neither is attributed."""
    a = actuals()
    a["cost"]["resources"] = [
        {
            "resource_id": RID + "-one",
            "resource_type": "Microsoft.App/containerApps",
            "service_name": "Azure Container Apps",
            "period_cost_usd": 40.0,
        },
        {
            "resource_id": RID + "-two",
            "resource_type": "Microsoft.App/containerApps",
            "service_name": "Azure Container Apps",
            "period_cost_usd": 30.0,
        },
    ]
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0
    assert any("ambiguous" in warning for warning in result["warnings"])


def test_ambiguous_same_type_forecasts_are_not_attributed() -> None:
    """Ambiguity on the forecast side blocks the fallback exactly the same
    way as ambiguity on the actual side."""
    f = forecast()
    f["resources"] = [
        {
            "resource_id": RID + "-one",
            "resource_kind": "Microsoft.App/containerApps",
            "monthly_cost_usd": 150.0,
        },
        {
            "resource_id": RID + "-two",
            "resource_kind": "Microsoft.App/containerApps",
            "monthly_cost_usd": 150.0,
        },
    ]
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = RID + "-renamed"
    result = run(f=f, a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0


def test_exact_id_match_is_case_and_trailing_slash_insensitive() -> None:
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = RID.upper() + "/"
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0
    assert result["coverage"]["matched_resources"][0]["match_method"] == "resource_id"


def test_matched_resource_entry_carries_costs_and_types() -> None:
    matched = run()["coverage"]["matched_resources"][0]
    assert matched == {
        "actual_resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "forecast_resource_ids": [RID],
        "forecast_deployment_ids": [],
        "forecast_monthly_usd": 300.0,
        "forecast_window_usd": 70.0,
        "actual_window_usd": 70.0,
        "match_method": "resource_id",
    }


def test_aoai_deployment_forecast_rolls_up_to_the_account_level_actual() -> None:
    """Cost Management bills AOAI at the account, while the forecast models
    per-deployment. Normalize the forecast ID to its parent account so the
    two meet (RFC §9.4)."""
    f = forecast()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_cost_usd": 300.0,
    }]
    a = actuals()
    a["cost"]["resources"] = [{
        "resource_id": AOAI_ACCOUNT,
        "resource_type": "Microsoft.CognitiveServices/accounts",
        "service_name": "Azure OpenAI",
        "period_cost_usd": 70.0,
    }]
    result = run(f=f, a=a)
    assert result["coverage"]["unmodeled_actual_usd"] == 0.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0
    matched = result["coverage"]["matched_resources"][0]
    assert matched["actual_resource_id"] == AOAI_ACCOUNT
    # Deployment detail is preserved for token diagnostics, not discarded.
    assert matched["forecast_deployment_ids"] == [AOAI_DEPLOYMENT]
    assert matched["match_method"] == "aoai_account_rollup"


def test_aoai_rollup_does_not_match_a_different_account() -> None:
    """Roll-up is scoped by account name. A deployment under `aoai1` must not
    absorb the bill for a second account `aoai2`."""
    f = forecast()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_cost_usd": 300.0,
    }]
    a = actuals()
    a["cost"]["resources"] = [{
        "resource_id": AOAI_ACCOUNT.replace("aoai1", "aoai2"),
        "resource_type": "Microsoft.CognitiveServices/accounts",
        "service_name": "Azure OpenAI",
        "period_cost_usd": 70.0,
    }]
    result = run(f=f, a=a)
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0


def test_multiple_aoai_deployments_collapse_into_one_account_entry() -> None:
    f = forecast()
    f["resources"] = [
        {
            "resource_id": AOAI_DEPLOYMENT,
            "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
            "monthly_cost_usd": 200.0,
        },
        {
            "resource_id": AOAI_ACCOUNT + "/deployments/embed",
            "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
            "monthly_cost_usd": 100.0,
        },
    ]
    a = actuals()
    a["cost"]["resources"] = [{
        "resource_id": AOAI_ACCOUNT,
        "resource_type": "Microsoft.CognitiveServices/accounts",
        "service_name": "Azure OpenAI",
        "period_cost_usd": 70.0,
    }]
    result = run(f=f, a=a)
    assert len(result["coverage"]["matched_resources"]) == 1
    matched = result["coverage"]["matched_resources"][0]
    assert matched["forecast_monthly_usd"] == 300.0
    assert matched["forecast_window_usd"] == 70.0
    assert matched["forecast_deployment_ids"] == [
        AOAI_DEPLOYMENT,
        AOAI_ACCOUNT + "/deployments/embed",
    ]
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0


def test_unattributed_cost_reduces_projection_coverage_not_total() -> None:
    a = actuals()
    a["cost"]["unattributed_usd"] = 7.0
    a["cost"]["period_total_usd"] = 77.0
    a["cost"]["resource_id_coverage_pct"] = 70.0 / 77.0
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 77.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == pytest.approx(
        70.0 / 77.0
    )
    # The source measure is carried through unchanged, for diagnosis only.
    assert result["coverage"]["source_resource_id_coverage_pct"] == pytest.approx(
        70.0 / 77.0
    )
    # Unattributed cost is not a *resource*, so it is not unmodeled either.
    assert result["coverage"]["unmodeled_actual_usd"] == 0.0


def test_full_resource_id_coverage_can_still_be_low_projection_coverage() -> None:
    """Every actual row carries a resource ID (source coverage 1.0), but half
    the spend is on a resource the forecast never projected. The two coverage
    measures are different numbers and only the projection one is gated."""
    a = actuals()
    a["cost"]["period_total_usd"] = 140.0
    a["cost"]["resources"].append({
        "resource_id": STORAGE_RID,
        "resource_type": "Microsoft.Storage/storageAccounts",
        "service_name": "Storage",
        "period_cost_usd": 70.0,
    })
    result = run(a=a)
    assert result["coverage"]["source_resource_id_coverage_pct"] == 1.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.5
    # And the maturity gate reads the projection measure, not the source one.
    assert result["maturity"]["status"] == "not-verified"
    assert check(result, "projection_attribution_coverage")["actual"] == 0.5


def test_projection_coverage_uses_gross_absolute_cost_for_refunds() -> None:
    """A refund on an unmodeled resource must not inflate coverage above the
    share of the bill the projection actually explains: netting `-10` against
    `+70` would report 1.0 (`70 / 60`), clamped or not, which is a lie."""
    a = actuals()
    a["cost"]["period_total_usd"] = 60.0
    a["cost"]["resources"].append({
        "resource_id": STORAGE_RID,
        "resource_type": "Microsoft.Storage/storageAccounts",
        "service_name": "Storage",
        "period_cost_usd": -10.0,
    })
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.875
    assert result["coverage"]["unmodeled_actual_usd"] == -10.0
    assert result["totals"]["actual_window_usd"] == 60.0


def test_zero_gross_cost_yields_null_projection_coverage() -> None:
    a = actuals(total=0.0)
    a["cost"]["resources"][0]["period_cost_usd"] = 0.0
    a["cost"]["resource_id_coverage_pct"] = None
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] is None
    assert result["coverage"]["source_resource_id_coverage_pct"] is None
    assert check(result, "projection_attribution_coverage")["status"] == "not-verified"


def test_no_actual_resources_yields_null_projection_coverage() -> None:
    a = actuals()
    a["cost"]["resources"] = []
    a["cost"]["period_total_usd"] = 0.0
    a["cost"]["unattributed_usd"] = 0.0
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] is None
    assert result["coverage"]["unmodeled_actual_usd"] == 0.0


def test_money_identity_holds_across_matched_unmodeled_and_unattributed() -> None:
    a = actuals()
    a["cost"]["period_total_usd"] = 147.0
    a["cost"]["unattributed_usd"] = 7.0
    a["cost"]["resources"].append({
        "resource_id": STORAGE_RID,
        "resource_type": "Microsoft.Storage/storageAccounts",
        "service_name": "Storage",
        "period_cost_usd": 70.0,
    })
    result = run(a=a)
    matched = sum(
        entry["actual_window_usd"] for entry in result["coverage"]["matched_resources"]
    )
    assert matched + result["coverage"]["unmodeled_actual_usd"] + 7.0 == pytest.approx(
        result["totals"]["actual_window_usd"]
    )


def test_forecast_not_observed_is_reported_in_window_usd() -> None:
    """`forecast_not_observed_usd` is window-scaled, so it is directly
    comparable with `forecast_window_usd` and `actual_window_usd` rather than
    mixing a monthly figure into a window ledger."""
    f = forecast()
    f["resources"].append({
        "resource_id": STORAGE_RID,
        "resource_kind": "Microsoft.Storage/storageAccounts",
        "monthly_cost_usd": 30.0,
    })
    result = run(f=f)
    assert result["coverage"]["forecast_not_observed_usd"] == 7.0
    entry = result["coverage"]["forecast_not_observed_resources"][0]
    assert entry["forecast_resource_ids"] == [STORAGE_RID]
    assert entry["forecast_monthly_usd"] == 30.0
    assert entry["forecast_window_usd"] == 7.0


def test_forecast_resource_without_a_monthly_cost_is_treated_as_zero() -> None:
    f = forecast()
    del f["resources"][0]["monthly_cost_usd"]
    result = run(f=f)
    assert result["coverage"]["matched_resources"][0]["forecast_monthly_usd"] == 0.0
    assert any("monthly_cost_usd" in warning for warning in result["warnings"])
    # The authoritative forecast total still comes from `totals`, untouched.
    assert result["totals"]["forecast_monthly_usd"] == 300.0


# ---------------------------------------------------------------------------
# The actual-cost accounting identity gates coverage
#
# `projection_attribution_coverage_pct` divides one part of the cost evidence
# by another. If the per-resource rows do not add back up to the authoritative
# `period_total_usd`, that ratio is computed over evidence that contradicts
# itself, and its most dangerous possible value is a confident `1.0`. So the
# identity is checked FIRST: every observed number is still reported, but
# coverage becomes `null` rather than a number nobody can trust.
# ---------------------------------------------------------------------------


def ten_row_actuals(row_cost=10.0, total=1000.0):
    """Ten rows that do NOT add up to the declared period total."""
    a = actuals(total=total)
    a["cost"]["resources"] = [
        {
            "resource_id": f"{RID}-{index}",
            "resource_type": "Microsoft.App/containerApps",
            "service_name": "Azure Container Apps",
            "period_cost_usd": row_cost,
        }
        for index in range(10)
    ]
    return a


def test_inconsistent_cost_rows_never_report_full_coverage() -> None:
    """Ten rows summing to $100 against a declared $1000 total. Every row is
    matched by the unique-type fallback... which is exactly the shape that
    would otherwise emit `1.0`: 100% of the rows, 10% of the bill."""
    result = run(a=ten_row_actuals())
    assert result["coverage"]["projection_attribution_coverage_pct"] is None


def test_inconsistent_cost_rows_preserve_every_numeric_total() -> None:
    """Fail closed on the *ratio*, never on the money. The observed totals are
    still what Cost Management said they were."""
    result = run(a=ten_row_actuals())
    assert result["totals"]["actual_window_usd"] == 1000.0
    assert result["totals"]["actual_monthly_run_rate_usd"] == pytest.approx(4285.71)
    assert result["totals"]["variance_window_usd"] == 930.0
    assert result["coverage"]["unmodeled_actual_usd"] == 100.0
    assert result["coverage"]["source_resource_id_coverage_pct"] == 1.0


def test_inconsistent_cost_rows_warn_explicitly() -> None:
    result = run(a=ten_row_actuals())
    assert any(
        "actual cost rows do not reconcile to period_total_usd" in warning
        for warning in result["warnings"]
    )


def test_inconsistent_cost_rows_degrade_the_coverage_check() -> None:
    entry = check(run(a=ten_row_actuals()), "projection_attribution_coverage")
    assert entry["status"] == "not-verified"
    assert entry["actual"] is None
    assert "actual cost rows do not reconcile to period_total_usd" in entry["detail"]
    assert run(a=ten_row_actuals())["maturity"]["status"] == "not-verified"


def test_sub_cent_row_rounding_still_reconciles() -> None:
    """The identity is evaluated on the cent-quantized SUM, not on a sum of
    per-row cent roundings, so half-cent rows that genuinely add up are not
    reported as contradictory evidence."""
    a = actuals(total=0.01)
    a["cost"]["resources"] = [
        {
            "resource_id": f"{RID}-{index}",
            "resource_type": "Microsoft.App/containerApps",
            "service_name": "Azure Container Apps",
            "period_cost_usd": 0.005,
        }
        for index in range(2)
    ]
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] is not None
    assert not any(
        "do not reconcile" in warning for warning in result["warnings"]
    )


def test_refund_rows_that_reconcile_are_not_flagged() -> None:
    a = actuals()
    a["cost"]["period_total_usd"] = 60.0
    a["cost"]["resources"].append({
        "resource_id": STORAGE_RID,
        "resource_type": "Microsoft.Storage/storageAccounts",
        "service_name": "Storage",
        "period_cost_usd": -10.0,
    })
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.875
    assert not any("do not reconcile" in w for w in result["warnings"])


def test_refund_row_that_breaks_the_identity_voids_coverage() -> None:
    """A refund row that the declared total does not account for is exactly
    the case where gross-absolute coverage looks healthiest and is least
    trustworthy."""
    a = actuals()
    a["cost"]["resources"].append({
        "resource_id": STORAGE_RID,
        "resource_type": "Microsoft.Storage/storageAccounts",
        "service_name": "Storage",
        "period_cost_usd": -10.0,
    })
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] is None
    assert any("do not reconcile" in w for w in result["warnings"])


def test_unattributed_participates_in_the_identity() -> None:
    """Rows alone are $70 short of the $77 total; the $7 of unattributed spend
    is what closes it, so the identity must include that term."""
    a = actuals()
    a["cost"]["period_total_usd"] = 77.0
    a["cost"]["unattributed_usd"] = 7.0
    assert run(a=a)["coverage"]["projection_attribution_coverage_pct"] is not None

    a["cost"]["unattributed_usd"] = 0.0
    result = run(a=a)
    assert result["coverage"]["projection_attribution_coverage_pct"] is None
    assert any("do not reconcile" in w for w in result["warnings"])


def test_absent_period_total_is_not_an_identity_mismatch() -> None:
    """An absent total is absent evidence — already `not-verified` through
    `actuals_status` — not evidence that contradicts itself."""
    a = actuals()
    a["cost"]["period_total_usd"] = None
    result = run(a=a)
    assert not any("do not reconcile" in w for w in result["warnings"])


def test_absent_row_breakdown_is_not_an_identity_mismatch() -> None:
    """Cost Management may return a period total with no per-resource
    breakdown at all. That is missing evidence, and coverage already reflects
    it without inventing a contradiction."""
    a = actuals()
    del a["cost"]["resources"]
    del a["cost"]["unattributed_usd"]
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert not any("do not reconcile" in w for w in result["warnings"])


def test_malformed_row_cost_still_raises_rather_than_degrading() -> None:
    a = ten_row_actuals()
    a["cost"]["resources"][3]["period_cost_usd"] = "abc"
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_identity_warning_is_emitted_once() -> None:
    result = run(a=ten_row_actuals())
    matching = [w for w in result["warnings"] if "do not reconcile" in w]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# RFC §10 — maturity, one check at a time
# ---------------------------------------------------------------------------


def test_maturity_declares_every_named_check_in_a_stable_order() -> None:
    result = run()
    assert [entry["id"] for entry in result["maturity"]["checks"]] == list(
        MATURITY_CHECK_IDS
    )
    for entry in result["maturity"]["checks"]:
        assert set(entry) == {"id", "status", "actual", "required", "detail"}
        assert entry["status"] in {"pass", "not-verified"}
        assert isinstance(entry["detail"], str) and entry["detail"]


def test_all_checks_pass_yields_pass() -> None:
    result = run()
    assert result["maturity"]["status"] == "pass"
    assert result["status"] == "pass"
    assert all(entry["status"] == "pass" for entry in result["maturity"]["checks"])


def test_incomplete_policy_yields_not_verified() -> None:
    p = policy()
    del p["cost"]["maturity_policy"]["min_complete_days"]
    result = run(p=p)
    assert result["maturity"]["status"] == "not-verified"
    assert check(result, "policy_complete")["status"] == "not-verified"
    assert "cost.maturity_policy.min_complete_days" in check(
        result, "policy_complete"
    )["actual"]["missing_paths"]
    # An incomplete declared policy leaves unit economics with no mature
    # policy to gate against either — it must not silently report `pass`
    # just because actuals are verified and successful_interactions > 0.
    assert result["unit_economics"]["status"] == "not-verified"


def test_policy_errors_alone_fail_the_policy_complete_check() -> None:
    result = run(errors=["cost.baseline.max_forecast_variance_pct: must be <= 1"])
    assert check(result, "policy_complete")["status"] == "not-verified"
    assert check(result, "policy_complete")["actual"]["policy_error_count"] == 1
    assert check(result, "policy_complete")["actual"]["missing_paths"] == []


def test_unverified_actuals_status_fails_its_own_check() -> None:
    a = actuals()
    a["status"] = "not-verified"
    result = run(a=a)
    assert check(result, "actuals_status")["status"] == "not-verified"
    assert check(result, "actuals_status")["actual"] == "not-verified"
    assert result["maturity"]["status"] == "not-verified"


def test_too_few_complete_days_yields_not_verified() -> None:
    a = actuals()
    a["window"]["complete_days"] = 3
    result = run(a=a)
    entry = check(result, "complete_days")
    assert entry["status"] == "not-verified"
    # Observed numbers are preserved even when the check fails.
    assert entry["actual"] == 3
    assert entry["required"] == 7
    assert result["maturity"]["status"] == "not-verified"


def test_too_few_successful_interactions_yields_not_verified() -> None:
    result = run(a=actuals(successes=99))
    entry = check(result, "successful_interactions")
    assert entry["status"] == "not-verified"
    assert entry["actual"] == 99
    assert entry["required"] == 100


def test_window_too_recent_to_settle_yields_not_verified() -> None:
    a = actuals()
    a["window"]["settlement_age_hours"] = 12
    result = run(a=a)
    assert result["maturity"]["status"] == "not-verified"
    entry = check(result, "cost_settlement_age_hours")
    assert entry["status"] == "not-verified"
    assert entry["actual"] == 12
    assert entry["required"] == 48


def test_window_too_old_for_policy_yields_not_verified() -> None:
    a = actuals()
    a["window"]["window_end_age_days"] = 30
    result = run(a=a)
    assert run(a=a)["maturity"]["status"] == "not-verified"
    entry = check(result, "window_end_age_days")
    assert entry["status"] == "not-verified"
    assert entry["actual"] == 30
    assert entry["required"] == 14


def test_low_projection_attribution_coverage_yields_not_verified() -> None:
    a = actuals()
    a["cost"]["period_total_usd"] = 100.0
    a["cost"]["unattributed_usd"] = 30.0
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_wrong_actual_cost_basis_yields_not_verified() -> None:
    a = actuals()
    a["cost"]["basis"] = "amortized"
    result = run(a=a)
    entry = check(result, "cost_accounting_basis")
    assert entry["status"] == "not-verified"
    assert entry["actual"]["actuals_cost_basis"] == "amortized"
    assert result["maturity"]["status"] == "not-verified"


def test_price_basis_mismatch_fails_the_price_basis_check() -> None:
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "ea"
    result = run(p=p)
    assert check(result, "price_basis_compatible")["status"] == "not-verified"
    assert result["maturity"]["status"] == "not-verified"


def test_price_basis_mismatch_with_explicit_allow_passes_maturity() -> None:
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "ea"
    p["cost"]["accounting"]["allow_basis_mismatch_for_verdict"] = True
    result = run(p=p)
    assert check(result, "price_basis_compatible")["status"] == "pass"
    assert result["maturity"]["status"] == "pass"


def test_evaluate_maturity_is_callable_on_its_own() -> None:
    verdict = evaluate_maturity(
        actuals(), policy(), projection_attribution_coverage_pct=1.0
    )
    assert verdict["status"] == "pass"
    assert [entry["id"] for entry in verdict["checks"]] == list(MATURITY_CHECK_IDS)


def test_evaluate_maturity_without_coverage_fails_closed() -> None:
    """Projection coverage is computed by the reconciler, not derivable from
    the actuals manifest alone, so an un-supplied coverage is `not-verified`
    rather than assumed complete."""
    verdict = evaluate_maturity(actuals(), policy())
    assert verdict["status"] == "not-verified"
    entry = next(e for e in verdict["checks"] if e["id"] == "projection_attribution_coverage")
    assert entry["status"] == "not-verified"
    assert entry["actual"] is None


def test_evaluate_maturity_accepts_policy_errors_keyword() -> None:
    verdict = evaluate_maturity(
        actuals(),
        policy(),
        policy_errors=("cost.baseline: bad",),
        projection_attribution_coverage_pct=1.0,
    )
    assert verdict["status"] == "not-verified"


def test_evaluate_maturity_does_not_mutate_its_inputs() -> None:
    a, p = actuals(), policy()
    before = (deepcopy(a), deepcopy(p))
    evaluate_maturity(a, p, projection_attribution_coverage_pct=1.0)
    assert (a, p) == before


@pytest.mark.parametrize(
    "bad",
    [
        True,
        False,
        42,
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.1,
        1.01,
        "1.0",
        [],
        {},
    ],
)
def test_invalid_projection_coverage_is_not_verified_never_raises(bad: object) -> None:
    """Coverage is a ratio in `[0, 1]`. A bool, a percentage expressed as
    `42`, a NaN, or an out-of-range value is unusable evidence: the check
    degrades to `not-verified` and reports `null`, because the alternative is
    either an exception that suppresses the whole artifact or a `pass` gated
    on a number that is not a share of anything."""
    verdict = evaluate_maturity(
        actuals(), policy(), projection_attribution_coverage_pct=bad
    )
    entry = next(
        e for e in verdict["checks"] if e["id"] == "projection_attribution_coverage"
    )
    assert entry["status"] == "not-verified"
    assert entry["actual"] is None
    assert "not a ratio" in entry["detail"]
    assert verdict["status"] == "not-verified"


def test_invalid_projection_coverage_keeps_the_verdict_json_serializable() -> None:
    """A NaN that reached `actual` would serialize as bare `NaN`, which is not
    valid JSON — the artifact must stay loadable by any consumer."""
    verdict = evaluate_maturity(
        actuals(), policy(), projection_attribution_coverage_pct=float("nan")
    )
    assert json.loads(json.dumps(verdict))["status"] == "not-verified"


@pytest.mark.parametrize("boundary", [0.0, 1.0, 0, 1])
def test_valid_projection_coverage_boundaries_are_accepted(boundary: float) -> None:
    verdict = evaluate_maturity(
        actuals(), policy(), projection_attribution_coverage_pct=boundary
    )
    entry = next(
        e for e in verdict["checks"] if e["id"] == "projection_attribution_coverage"
    )
    assert entry["actual"] == boundary
    assert entry["status"] == ("pass" if boundary >= 0.95 else "not-verified")


def test_evaluate_maturity_flags_inconsistent_cost_evidence_on_its_own() -> None:
    """The identity is a property of the actuals document, so a standalone
    maturity call fails closed on it too — a caller cannot restore a `pass` by
    passing a coverage number that was computed elsewhere."""
    verdict = evaluate_maturity(
        ten_row_actuals(), policy(), projection_attribution_coverage_pct=1.0
    )
    entry = next(
        e for e in verdict["checks"] if e["id"] == "projection_attribution_coverage"
    )
    assert entry["status"] == "not-verified"
    assert entry["actual"] is None
    assert "actual cost rows do not reconcile to period_total_usd" in entry["detail"]


# ---------------------------------------------------------------------------
# RFC §9.3 / §7.3 — unit economics
# ---------------------------------------------------------------------------


def test_unit_economics_target_status_pass_when_within_baseline() -> None:
    """`total=70.0` over `successes=100` is 0.70/interaction; baseline target is 1.0."""
    result = run()
    assert result["unit_economics"]["status"] == "pass"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] == 0.70
    assert result["unit_economics"]["successful_interactions"] == 100
    assert result["unit_economics"]["target_usd"] == 1.0
    assert result["unit_economics"]["target_status"] == "pass"


def test_unit_economics_target_status_should_fix_when_above_baseline() -> None:
    p = policy()
    p["cost"]["baseline"]["target_cost_per_successful_interaction_usd"] = 0.50
    result = run(p=p)
    assert result["unit_economics"]["status"] == "pass"
    assert result["unit_economics"]["target_status"] == "should-fix"


def test_unit_economics_target_status_passes_exactly_at_target() -> None:
    p = policy()
    p["cost"]["baseline"]["target_cost_per_successful_interaction_usd"] = 0.70
    assert run(p=p)["unit_economics"]["target_status"] == "pass"


def test_cost_per_successful_interaction_is_a_four_decimal_rate() -> None:
    """A unit cost is a rate, not a ledger amount: rounding `1.00 / 3` to
    whole cents (`0.33`) would throw away the precision the target
    comparison needs."""
    result = run(a=actuals(total=1.0, successes=3))
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] == 0.3333


def test_zero_successes_yields_not_verified_unit_economics() -> None:
    result = run(a=actuals(successes=0))
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None
    assert result["unit_economics"]["target_status"] == "not-verified"
    # The observed count is still reported.
    assert result["unit_economics"]["successful_interactions"] == 0


def test_unverified_actuals_collection_yields_not_verified_unit_economics() -> None:
    """A `not-verified` Cost Management collection means there is no verified
    actual total to divide by successful_interactions, independent of policy
    completeness or how many successful interactions were observed. Token
    metrics (`usage.models`) are irrelevant to this gate either way."""
    a = actuals()
    a["status"] = "not-verified"
    result = run(a=a)
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None
    assert result["unit_economics"]["target_status"] == "not-verified"


def test_unverified_interactions_block_unit_economics_but_not_cost_totals() -> None:
    """RFC §7.2/§9.3: a failed workspace query leaves the cost artifact fully
    valid, and only unit economics goes `not-verified`."""
    a = actuals()
    a["usage"]["interaction_status"] = "not-verified"
    a["usage"]["total_interactions"] = None
    a["usage"]["successful_interactions"] = None
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["variance_status"] == "pass"
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None


def test_missing_token_metrics_never_block_unit_economics() -> None:
    """`model_attribution_status: not-verified` is the default fixture state
    and must never leak into the unit-economics gate."""
    result = run()
    assert result["unit_economics"]["status"] == "pass"


def test_unit_economics_ignores_a_token_reprice_entirely() -> None:
    a = actuals()
    a["usage"]["model_attribution_status"] = "pass"
    a["usage"]["models"] = [{
        "deployment": "chat",
        "model": "gpt-5.4",
        "input_tokens": 10,
        "output_tokens": 1,
        "retail_repriced_cost_usd": 999.0,
    }]
    assert run(a=a)["unit_economics"]["cost_per_successful_interaction_usd"] == 0.70


# ---------------------------------------------------------------------------
# RFC §9.5 — price basis and the variance verdict
# ---------------------------------------------------------------------------


def test_matching_price_basis_permits_a_verdict() -> None:
    assert run()["variance_status"] == "pass"


def test_variance_outside_tolerance_is_should_fix() -> None:
    result = run(a=actuals(total=100.0))
    assert result["totals"]["variance_pct"] == pytest.approx(0.428571)
    assert result["variance_status"] == "should-fix"


def test_variance_exactly_at_tolerance_passes() -> None:
    result = run(a=actuals(total=84.0))
    assert result["totals"]["variance_pct"] == 0.2
    assert result["variance_status"] == "pass"


def test_negative_variance_outside_tolerance_is_should_fix() -> None:
    """Spending far *less* than forecast is still a forecast that failed."""
    result = run(a=actuals(total=10.0))
    assert result["variance_status"] == "should-fix"


def test_price_basis_mismatch_reports_delta_without_verdict() -> None:
    """The comparison is `accounting.actual_billing_price_basis` against
    `forecast_price_basis`. `actual_cost_basis: usage-pretax` is the metric
    and source, and is never one side of this comparison."""
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "ea"
    result = run(p=p)
    assert result["totals"]["variance_pct"] == 0.0
    assert result["variance_status"] == "not-verified"
    assert result["policy_snapshot"]["actual_billing_price_basis"] == "ea"
    assert any("price basis" in warning for warning in result["warnings"])


def test_unknown_actual_price_basis_is_treated_as_a_mismatch() -> None:
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "unknown"
    assert run(p=p)["variance_status"] == "not-verified"


def test_allow_basis_mismatch_restores_the_verdict() -> None:
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "ea"
    p["cost"]["accounting"]["allow_basis_mismatch_for_verdict"] = True
    assert run(p=p)["variance_status"] == "pass"


def test_missing_variance_threshold_yields_not_verified_variance() -> None:
    p = policy()
    del p["cost"]["baseline"]["max_forecast_variance_pct"]
    assert run(p=p)["variance_status"] == "not-verified"


def test_policy_errors_block_the_variance_verdict() -> None:
    """A policy that did not parse cleanly cannot be trusted to carry the
    threshold this verdict is measured against."""
    assert run(errors=["cost.baseline: bad"])["variance_status"] == "not-verified"


def test_variance_status_is_independent_of_the_interaction_gate() -> None:
    """Cost variance is a cost question. A missing interaction count degrades
    unit economics only — it must not silently void the cost verdict."""
    a = actuals()
    a["usage"]["interaction_status"] = "not-verified"
    a["usage"]["successful_interactions"] = None
    a["usage"]["total_interactions"] = None
    result = run(a=a)
    assert result["maturity"]["status"] == "not-verified"
    assert result["variance_status"] == "pass"


# ---------------------------------------------------------------------------
# Refs, snapshot, hashing and re-projection invalidation
# ---------------------------------------------------------------------------


def test_forecast_and_actual_hashes_are_recorded() -> None:
    f, a = forecast(), actuals()
    result = run(f=f, a=a)
    assert result["forecast_ref"]["sha256"] == sha256_json(f)
    assert result["actuals_ref"]["sha256"] == sha256_json(a)
    assert result["policy_ref"]["spec_sha256"] == SPEC_SHA256
    assert result["policy_snapshot"]["max_forecast_variance_pct"] == 0.20
    assert result["policy_snapshot"]["max_token_volume_variance_pct"] == 0.25
    assert result["policy_snapshot"]["min_projection_attribution_coverage_pct"] == 0.95


def test_refs_carry_the_canonical_artifact_paths() -> None:
    result = run()
    assert result["forecast_ref"]["path"] == "specs/cost-manifest.json"
    assert result["actuals_ref"]["path"] == "specs/cost-actuals-manifest.json"
    assert result["policy_ref"]["path"] == "specs/SPEC.md"
    assert result["policy_ref"]["section"] == 14


def test_refs_record_the_paths_the_caller_actually_read() -> None:
    """Provenance must name the bytes that were read, not a canonical guess.

    A pilot whose artifacts live outside the default layout still gets a
    re-derivable provenance block: the caller passes the paths it resolved,
    and they are echoed verbatim.
    """
    f, a = forecast(), actuals()
    result = reconcile_costs(
        f,
        a,
        policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
        forecast_path="pilots/alpha/specs/cost-manifest.json",
        actuals_path="pilots/alpha/specs/cost-actuals-manifest.json",
        policy_path="pilots/alpha/specs/SPEC.md",
    )
    assert result["forecast_ref"]["path"] == "pilots/alpha/specs/cost-manifest.json"
    assert (
        result["actuals_ref"]["path"]
        == "pilots/alpha/specs/cost-actuals-manifest.json"
    )
    assert result["policy_ref"]["path"] == "pilots/alpha/specs/SPEC.md"
    assert result["policy_ref"]["section"] == 14
    # The digests pin BYTES, never names: renaming where evidence lives must
    # not change what a consumer re-derives from it.
    assert result["forecast_ref"]["sha256"] == sha256_json(f)
    assert result["actuals_ref"]["sha256"] == sha256_json(a)
    assert result["policy_ref"]["spec_sha256"] == SPEC_SHA256


def test_overriding_the_paths_changes_nothing_but_the_paths() -> None:
    baseline = run()
    overridden = reconcile_costs(
        forecast(),
        actuals(),
        policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
        forecast_path="a/forecast.json",
        actuals_path="a/actuals.json",
        policy_path="a/SPEC.md",
    )
    ref_keys = {"forecast_ref", "actuals_ref", "policy_ref"}
    assert {k: v for k, v in overridden.items() if k not in ref_keys} == {
        k: v for k, v in baseline.items() if k not in ref_keys
    }
    for key in ref_keys:
        assert {
            field: value
            for field, value in overridden[key].items()
            if field != "path"
        } == {
            field: value
            for field, value in baseline[key].items()
            if field != "path"
        }


@pytest.mark.parametrize(
    "override",
    [
        {"forecast_path": ""},
        {"forecast_path": "   "},
        {"actuals_path": None},
        {"actuals_path": Path("specs/cost-actuals-manifest.json")},
        {"policy_path": 14},
    ],
)
def test_a_provenance_path_must_be_a_non_empty_string(override) -> None:
    """A blank or non-string path would publish a provenance block that names
    nothing a consumer can open."""
    with pytest.raises(ReconciliationInputError):
        reconcile_costs(
            forecast(),
            actuals(),
            policy(),
            policy_errors=[],
            generated_at=GENERATED,
            policy_spec_sha256=SPEC_SHA256,
            **override,
        )


def test_policy_snapshot_carries_every_threshold_and_basis() -> None:
    snapshot = run()["policy_snapshot"]
    assert snapshot == {
        "min_complete_days": 7,
        "min_successful_interactions": 100,
        "min_cost_settlement_age_hours": 48,
        "max_window_end_age_days": 14,
        "min_projection_attribution_coverage_pct": 0.95,
        "target_cost_per_successful_interaction_usd": 1.0,
        "max_forecast_variance_pct": 0.20,
        "max_token_volume_variance_pct": 0.25,
        "actual_cost_basis": "usage-pretax",
        "actual_billing_price_basis": "retail",
        "forecast_price_basis": "retail",
        "allow_basis_mismatch_for_verdict": False,
        "scope_policy": "dedicated_resource_group",
    }


def test_policy_snapshot_keys_survive_an_empty_policy() -> None:
    snapshot = run(p={})["policy_snapshot"]
    assert set(snapshot) == set(run()["policy_snapshot"])
    assert all(value is None for value in snapshot.values())


def test_invalid_policy_value_is_dropped_and_warned_not_raised() -> None:
    p = policy()
    p["cost"]["baseline"]["max_forecast_variance_pct"] = "twenty percent"
    result = run(p=p)
    assert result["policy_snapshot"]["max_forecast_variance_pct"] is None
    assert result["variance_status"] == "not-verified"
    assert any("max_forecast_variance_pct" in warning for warning in result["warnings"])


def test_reprojection_invalidates_the_reconciliation_by_design() -> None:
    """A re-projected forecast changes `forecast_ref.sha256`, so the previous
    reconciliation no longer describes the current forecast and must be
    treated as invalid. This is intentional: the fix is a cheap `reconcile`
    rerun over the *already collected* raw actuals, with no Azure calls and no
    re-collection, because the window and scope did not change."""
    a = actuals()
    first = run(f=forecast(), a=a)
    reprojected = forecast(360.0)
    second = run(f=reprojected, a=a)
    assert second["forecast_ref"]["sha256"] != first["forecast_ref"]["sha256"]
    # Same raw actuals: the actuals hash is unchanged, which is exactly what
    # lets the rerun skip collection.
    assert second["actuals_ref"]["sha256"] == first["actuals_ref"]["sha256"]
    assert second["totals"]["actual_window_usd"] == first["totals"]["actual_window_usd"]


def test_reconciliation_is_deterministic_for_identical_inputs() -> None:
    assert sha256_json(run()) == sha256_json(run())


# ---------------------------------------------------------------------------
# `drivers.payg_ptu`
# ---------------------------------------------------------------------------


def aoai_forecast(input_tokens=80000, output_tokens=10000):
    f = forecast()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_units_consumed": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }]
    f["recommendations"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "current_sku": {"tier": "PAYG"},
        "recommended_sku": {"tier": "PTU"},
    }]
    return f


def aoai_actuals(input_tokens=19000, output_tokens=2000):
    a = actuals()
    a["usage"]["model_attribution_status"] = "pass"
    a["usage"]["models"] = [{
        "deployment": "chat",
        "model": "gpt-5.4",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }]
    return a


def test_payg_ptu_driver_compares_observed_and_forecast_token_volume() -> None:
    result = run(f=aoai_forecast(), a=aoai_actuals())
    driver = result["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["forecast_monthly_tokens"] == 90000
    assert driver["observed_monthly_tokens"] == 90000.0
    assert driver["observed_volume_variance_pct"] == 0.0


def test_payg_ptu_driver_uses_the_token_threshold_not_the_cost_threshold() -> None:
    """Observed volume lands 22% above forecast: outside the 20% *cost*
    tolerance but inside the declared 25% *token volume* tolerance. Reusing
    `max_forecast_variance_pct` here would wrongly report `should-fix`."""
    f = aoai_forecast(input_tokens=90000, output_tokens=10000)
    a = aoai_actuals(input_tokens=25200, output_tokens=3266)
    driver = run(f=f, a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["threshold_field"] == "max_token_volume_variance_pct"
    assert driver["threshold_pct"] == 0.25
    assert 0.20 < driver["observed_volume_variance_pct"] <= 0.25


def test_payg_ptu_driver_should_fix_outside_the_token_band() -> None:
    f = aoai_forecast()
    a = aoai_actuals(input_tokens=30000, output_tokens=3000)
    driver = run(f=f, a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "should-fix"
    assert "rerun PAYG/PTU analysis at observed volume" in driver["detail"]


def test_payg_ptu_driver_never_reports_token_dollars() -> None:
    driver = run(f=aoai_forecast(), a=aoai_actuals())["drivers"]["payg_ptu"]
    assert not any("usd" in key for key in driver)


def test_payg_ptu_driver_is_not_verified_without_a_token_threshold() -> None:
    p = policy()
    del p["cost"]["baseline"]["max_token_volume_variance_pct"]
    assert run(p=p)["drivers"]["payg_ptu"]["status"] == "not-verified"


def test_payg_ptu_driver_is_not_verified_without_recommendation() -> None:
    assert run()["drivers"]["payg_ptu"]["status"] == "not-verified"


def test_payg_ptu_driver_ignores_a_non_tier_change_recommendation() -> None:
    f = aoai_forecast()
    f["recommendations"][0]["recommended_sku"] = {"tier": "PAYG"}
    assert run(f=f, a=aoai_actuals())["drivers"]["payg_ptu"]["status"] == "not-verified"


def test_payg_ptu_driver_is_not_verified_without_token_metrics() -> None:
    a = aoai_actuals()
    a["usage"]["model_attribution_status"] = "not-verified"
    driver = run(f=aoai_forecast(), a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "not-verified"
    assert driver["observed_monthly_tokens"] is None


def test_payg_ptu_driver_is_not_verified_at_zero_forecast_tokens() -> None:
    f = aoai_forecast(input_tokens=0, output_tokens=0)
    driver = run(f=f, a=aoai_actuals())["drivers"]["payg_ptu"]
    assert driver["status"] == "not-verified"
    assert driver["observed_volume_variance_pct"] is None


def test_payg_ptu_driver_is_not_verified_when_no_model_row_matches() -> None:
    a = aoai_actuals()
    a["usage"]["models"][0]["deployment"] = "other-deployment"
    assert run(f=aoai_forecast(), a=a)["drivers"]["payg_ptu"]["status"] == "not-verified"


def test_payg_ptu_driver_ptu_to_payg_is_also_considered() -> None:
    f = aoai_forecast()
    f["recommendations"][0]["current_sku"] = {"tier": "PTU"}
    f["recommendations"][0]["recommended_sku"] = {"tier": "PAYG"}
    assert run(f=f, a=aoai_actuals())["drivers"]["payg_ptu"]["status"] == "pass"


# ---------------------------------------------------------------------------
# `drivers.payg_ptu` — a bare deployment name is not an identity
#
# `deployment` in a token row is a LEAF name: two Azure OpenAI accounts can
# each own a deployment called `chat`. Summing them because the strings match
# would inflate the observed volume of whichever account the recommendation
# was actually about.
# ---------------------------------------------------------------------------


AOAI_ACCOUNT_2 = AOAI_ACCOUNT.replace("aoai1", "aoai2")
AOAI_DEPLOYMENT_2 = AOAI_ACCOUNT_2 + "/deployments/chat"


def two_account_aoai_forecast():
    f = aoai_forecast()
    f["resources"].append({
        "resource_id": AOAI_DEPLOYMENT_2,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_units_consumed": {"input_tokens": 80000, "output_tokens": 10000},
    })
    f["recommendations"].append({
        "resource_id": AOAI_DEPLOYMENT_2,
        "current_sku": {"tier": "PAYG"},
        "recommended_sku": {"tier": "PTU"},
    })
    return f


def test_payg_ptu_driver_single_account_bare_row_is_unchanged() -> None:
    """One implicated account: a bare deployment name is unambiguous, so the
    existing behaviour is preserved exactly."""
    driver = run(f=aoai_forecast(), a=aoai_actuals())["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["observed_monthly_tokens"] == 90000.0


def test_payg_ptu_driver_ignores_a_token_row_from_another_account() -> None:
    """Same deployment name, different account: that traffic belongs to a
    resource the recommendation never covered."""
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = AOAI_DEPLOYMENT_2
    driver = run(f=aoai_forecast(), a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "not-verified"
    assert driver["observed_monthly_tokens"] is None


def test_payg_ptu_driver_accepts_a_matching_row_identifier() -> None:
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = AOAI_DEPLOYMENT
    driver = run(f=aoai_forecast(), a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["observed_monthly_tokens"] == 90000.0


def test_payg_ptu_driver_accepts_an_account_scoped_row_identifier() -> None:
    """A collector that can only attribute to the billed account still
    identifies the row well enough to be included."""
    a = aoai_actuals()
    a["usage"]["models"][0]["account_resource_id"] = AOAI_ACCOUNT
    driver = run(f=aoai_forecast(), a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["observed_monthly_tokens"] == 90000.0


def test_payg_ptu_driver_refuses_to_sum_a_bare_name_across_two_accounts() -> None:
    """Two accounts are implicated and the only observed row says just
    `chat`. Attributing it — to either account, or to both — invents a fact
    the evidence does not contain."""
    driver = run(f=two_account_aoai_forecast(), a=aoai_actuals())["drivers"]["payg_ptu"]
    assert driver["status"] == "not-verified"
    assert driver["observed_monthly_tokens"] is None
    assert driver["observed_volume_variance_pct"] is None
    # The forecast side is still reported: it was never ambiguous.
    assert driver["forecast_monthly_tokens"] == 180000


def test_payg_ptu_driver_warns_when_a_bare_name_spans_two_accounts() -> None:
    result = run(f=two_account_aoai_forecast(), a=aoai_actuals())
    assert any(
        "account" in warning and "drivers.payg_ptu" in warning
        for warning in result["warnings"]
    )


def test_payg_ptu_driver_sums_identified_rows_across_two_accounts() -> None:
    """Ambiguity is about missing identity, not about account count: once
    every row says which account it came from, both are summed."""
    a = aoai_actuals()
    a["usage"]["models"] = [
        {
            "deployment": "chat",
            "model": "gpt-5.4",
            "resource_id": AOAI_DEPLOYMENT,
            "input_tokens": 19000,
            "output_tokens": 2000,
        },
        {
            "deployment": "chat",
            "model": "gpt-5.4",
            "resource_id": AOAI_DEPLOYMENT_2,
            "input_tokens": 19000,
            "output_tokens": 2000,
        },
    ]
    driver = run(f=two_account_aoai_forecast(), a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["forecast_monthly_tokens"] == 180000
    assert driver["observed_monthly_tokens"] == 180000.0


def test_payg_ptu_driver_rejects_a_malformed_row_identifier() -> None:
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = 17
    with pytest.raises(ReconciliationInputError):
        run(f=aoai_forecast(), a=a)


# ---------------------------------------------------------------------------
# `policy_ref.spec_sha256` — the audit anchor
# ---------------------------------------------------------------------------


def test_placeholder_spec_anchor_is_retained_but_fails_closed() -> None:
    """A malformed anchor is caller/input evidence, but this module always
    emits: the string is echoed verbatim (a consumer must be able to see what
    it was given), every observed number stands, and the verdict degrades."""
    result = reconcile_costs(
        forecast(), actuals(), policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256="spec-hash",
    )
    assert result["policy_ref"]["spec_sha256"] == "spec-hash"
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["status"] == "not-verified"
    entry = check(result, "policy_complete")
    assert entry["status"] == "not-verified"
    assert entry["actual"]["policy_spec_sha256_valid"] is False
    assert entry["actual"]["missing_paths"] == []
    assert any("spec_sha256" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "bad",
    ["", "spec-hash", "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63, "a" * 64 + " "],
)
def test_non_digest_spec_anchors_are_all_rejected(bad: str) -> None:
    result = reconcile_costs(
        forecast(), actuals(), policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256=bad,
    )
    assert result["status"] == "not-verified"
    assert check(result, "policy_complete")["actual"]["policy_spec_sha256_valid"] is False


@pytest.mark.parametrize("good", [SPEC_SHA256, SPEC_SHA256.upper()])
def test_a_real_digest_is_accepted_in_either_case(good: str) -> None:
    result = reconcile_costs(
        forecast(), actuals(), policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256=good,
    )
    assert result["status"] == "pass"
    assert check(result, "policy_complete")["actual"]["policy_spec_sha256_valid"] is True


def test_standalone_maturity_has_no_anchor_to_check() -> None:
    """`evaluate_maturity` is documented as a function of actuals + policy;
    an anchor it was never given is `null`, not a failure.

    This is the ONLY case in which an unanchored policy still verdicts: a
    standalone caller is asking "is this evidence mature?", not publishing an
    artifact whose provenance a third party must be able to re-derive.
    `reconcile_costs` always supplies an anchor, so it can never reach here.
    """
    verdict = evaluate_maturity(
        actuals(), policy(), projection_attribution_coverage_pct=1.0
    )
    entry = next(e for e in verdict["checks"] if e["id"] == "policy_complete")
    assert entry["actual"]["policy_spec_sha256_valid"] is None
    assert verdict["status"] == "pass"


def test_standalone_maturity_rejects_a_supplied_placeholder_anchor() -> None:
    """Supplying an anchor is opting in to having it checked: `None` means
    "not supplied", it is not a way to spell "do not check"."""
    verdict = evaluate_maturity(
        actuals(),
        policy(),
        projection_attribution_coverage_pct=1.0,
        policy_spec_sha256=BAD_ANCHOR,
    )
    entry = next(e for e in verdict["checks"] if e["id"] == "policy_complete")
    assert entry["actual"]["policy_spec_sha256_valid"] is False
    assert entry["status"] == "not-verified"
    assert verdict["status"] == "not-verified"


# ---------------------------------------------------------------------------
# An unusable anchor makes every DERIVED verdict non-authoritative
#
# `policy_ref.spec_sha256` is what lets a consumer re-derive which SPEC
# revision declared the thresholds a verdict was rendered against. When it is
# not a re-derivable digest, every verdict those thresholds gated is
# unprovable — not just the `policy_complete` check. Observed numbers are
# still reported: they are evidence, and the anchor says nothing about them.
# ---------------------------------------------------------------------------


BAD_ANCHOR = "spec-hash"


def run_anchored(anchor, f=None, a=None, p=None, errors=None):
    return reconcile_costs(
        f or forecast(),
        a or actuals(),
        policy() if p is None else p,
        policy_errors=errors or [],
        generated_at=GENERATED,
        policy_spec_sha256=anchor,
    )


def test_invalid_anchor_makes_unit_economics_non_authoritative() -> None:
    result = run_anchored(BAD_ANCHOR)
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["target_status"] == "not-verified"
    # The observed count and the declared target are still reported: an
    # unprovable provenance does not un-observe an interaction.
    assert result["unit_economics"]["successful_interactions"] == 100
    assert result["unit_economics"]["target_usd"] == 1.0


def test_invalid_anchor_makes_the_cost_variance_verdict_non_authoritative() -> None:
    result = run_anchored(BAD_ANCHOR)
    assert result["variance_status"] == "not-verified"
    # Every number the verdict would have been read off is still emitted.
    assert result["totals"]["forecast_window_usd"] == 70.0
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["totals"]["variance_window_usd"] == 0.0
    assert result["totals"]["variance_pct"] == 0.0


def test_invalid_anchor_makes_the_payg_ptu_driver_non_authoritative() -> None:
    """The driver's verdict rests on SPEC's declared
    `max_token_volume_variance_pct`, so it degrades with the policy that
    declared it — while the token volumes it measured stand."""
    result = run_anchored(BAD_ANCHOR, f=aoai_forecast(), a=aoai_actuals())
    driver = result["drivers"]["payg_ptu"]
    assert driver["status"] == "not-verified"
    assert driver["forecast_monthly_tokens"] == 90000
    assert driver["observed_monthly_tokens"] == 90000.0
    assert driver["observed_volume_variance_pct"] == 0.0
    assert driver["threshold_pct"] == 0.25
    assert "anchored" in driver["detail"]


def test_invalid_anchor_never_yields_a_should_fix_anywhere() -> None:
    """`should-fix` asserts a declared threshold was breached. Without a
    re-derivable anchor there is no provable declaration to breach."""
    f = aoai_forecast()
    f["totals"]["monthly_cost_current_usd"] = 100.0
    a = aoai_actuals(input_tokens=30000, output_tokens=3000)
    a["cost"]["period_total_usd"] = 500.0
    a["cost"]["resources"][0]["period_cost_usd"] = 500.0
    result = run_anchored(BAD_ANCHOR, f=f, a=a)
    assert result["variance_status"] == "not-verified"
    assert result["unit_economics"]["target_status"] == "not-verified"
    assert result["drivers"]["payg_ptu"]["status"] == "not-verified"
    # ... and the same evidence under a real anchor does report the breaches.
    verified = run_anchored(SPEC_SHA256, f=f, a=a)
    assert verified["variance_status"] == "should-fix"
    assert verified["unit_economics"]["target_status"] == "should-fix"
    assert verified["drivers"]["payg_ptu"]["status"] == "should-fix"


def test_a_valid_anchor_leaves_every_derived_verdict_authoritative() -> None:
    """The gate is the anchor's validity and nothing else: identical evidence
    with a real digest verdicts exactly as before."""
    assert run()["status"] == "pass"
    result = run(f=aoai_forecast(), a=aoai_actuals())
    assert result["variance_status"] == "pass"
    assert result["unit_economics"]["status"] == "pass"
    assert result["unit_economics"]["target_status"] == "pass"
    assert result["drivers"]["payg_ptu"]["status"] == "pass"


def test_one_policy_complete_definition_gates_every_derived_verdict() -> None:
    """Missing leaves, parse errors and an unusable anchor are three ways to
    be the same fact — "the declared policy cannot gate a verdict" — and all
    three degrade the same four statuses. A verdict that stayed `pass` under
    one but not the others would mean two definitions were in play."""
    incomplete = policy()
    del incomplete["cost"]["maturity_policy"]["min_complete_days"]
    cases = {
        "unusable anchor": run_anchored(
            BAD_ANCHOR, f=aoai_forecast(), a=aoai_actuals()
        ),
        "parse errors": run_anchored(
            SPEC_SHA256,
            f=aoai_forecast(),
            a=aoai_actuals(),
            errors=["cost.baseline: bad"],
        ),
        "missing leaf": run_anchored(
            SPEC_SHA256, f=aoai_forecast(), a=aoai_actuals(), p=incomplete
        ),
    }
    for label, result in cases.items():
        assert result["status"] == "not-verified", label
        assert check(result, "policy_complete")["status"] == "not-verified", label
        assert result["unit_economics"]["status"] == "not-verified", label
        assert result["unit_economics"]["target_status"] == "not-verified", label
        assert result["variance_status"] == "not-verified", label
        assert result["drivers"]["payg_ptu"]["status"] == "not-verified", label


def test_invalid_anchor_still_emits_the_full_manifest() -> None:
    result = run_anchored(BAD_ANCHOR)
    assert result["policy_ref"]["spec_sha256"] == BAD_ANCHOR
    assert result["policy_snapshot"]["max_forecast_variance_pct"] == 0.20
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0
    assert json.loads(json.dumps(result)) == result


# ---------------------------------------------------------------------------
# The accounting-identity check is not a never-raises helper
# ---------------------------------------------------------------------------


def test_cost_identity_check_raises_on_unquantizable_money() -> None:
    """`_cost_evidence_reconciles` quantizes the breakdown it compares, so a
    magnitude that cannot be represented at cent precision fails closed there
    — it does not silently report "reconciles". Reached through a standalone
    `evaluate_maturity`, which quantizes nothing else first."""
    a = actuals()
    a["cost"]["resources"][0]["period_cost_usd"] = 1e100
    a["cost"]["period_total_usd"] = 1e100
    with pytest.raises(ReconciliationInputError, match="breakdown total"):
        evaluate_maturity(a, policy(), projection_attribution_coverage_pct=1.0)


# ---------------------------------------------------------------------------
# `drivers.payg_ptu` — a row that contradicts itself is never counted
#
# A token row may declare a bare `deployment` leaf name AND a full
# `resource_id`. When the resource ID carries its own deployment leaf, the two
# are the same claim stated twice; if they disagree, the row does not identify
# a deployment at all.
# ---------------------------------------------------------------------------


AOAI_DEPLOYMENT_EMBED = AOAI_ACCOUNT + "/deployments/embed"


def test_token_row_contradicting_its_own_resource_id_is_excluded() -> None:
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = AOAI_DEPLOYMENT_EMBED
    result = run(f=aoai_forecast(), a=a)
    driver = result["drivers"]["payg_ptu"]
    assert driver["status"] == "not-verified"
    assert driver["observed_monthly_tokens"] is None
    assert any(
        "drivers.payg_ptu" in warning and "contradicts itself" in warning
        for warning in result["warnings"]
    )


def test_contradictory_token_row_never_inflates_observed_volume() -> None:
    """The dangerous shape: one honest row plus one self-contradictory row
    whose volume would swamp it. The contradictory row is dropped, never
    added to the deployment its `deployment` field happened to name."""
    a = aoai_actuals()
    a["usage"]["models"] = [
        {
            "deployment": "chat",
            "model": "gpt-5.4",
            "resource_id": AOAI_DEPLOYMENT,
            "input_tokens": 19000,
            "output_tokens": 2000,
        },
        {
            "deployment": "chat",
            "model": "gpt-5.4",
            "resource_id": AOAI_DEPLOYMENT_EMBED,
            "input_tokens": 500000,
            "output_tokens": 500000,
        },
    ]
    driver = run(f=aoai_forecast(), a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["observed_monthly_tokens"] == 90000.0


def test_token_row_whose_resource_id_leaf_agrees_is_counted() -> None:
    """The cross-check only excludes DISAGREEMENT: a row that states the same
    deployment twice is exactly as usable as one that states it once."""
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = AOAI_DEPLOYMENT
    result = run(f=aoai_forecast(), a=a)
    assert result["drivers"]["payg_ptu"]["observed_monthly_tokens"] == 90000.0
    assert not any("contradicts itself" in w for w in result["warnings"])


def test_account_scoped_identifier_has_no_leaf_to_contradict() -> None:
    """An account resource ID names no deployment, so there is nothing to
    cross-check and the row's `deployment` stands."""
    a = aoai_actuals()
    a["usage"]["models"][0]["account_resource_id"] = AOAI_ACCOUNT
    result = run(f=aoai_forecast(), a=a)
    assert result["drivers"]["payg_ptu"]["observed_monthly_tokens"] == 90000.0
    assert not any("contradicts itself" in w for w in result["warnings"])


def test_contradictory_row_is_excluded_even_when_its_leaf_is_recommended() -> None:
    """Mirror image: the resource ID names the recommended deployment while
    the `deployment` field names another. Neither claim is usable."""
    a = aoai_actuals()
    a["usage"]["models"][0]["deployment"] = "embed"
    a["usage"]["models"][0]["resource_id"] = AOAI_DEPLOYMENT
    result = run(f=aoai_forecast(), a=a)
    assert result["drivers"]["payg_ptu"]["status"] == "not-verified"
    assert result["drivers"]["payg_ptu"]["observed_monthly_tokens"] is None
    assert any("contradicts itself" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# `drivers.payg_ptu` — a warning never claims a resource is Azure OpenAI
# ---------------------------------------------------------------------------


def test_a_storage_row_identifier_is_never_called_an_azure_openai_account() -> None:
    """A row identifier whose normalized type is not
    `microsoft.cognitiveservices/accounts` is described neutrally. Calling a
    storage account an "Azure OpenAI account" would send a reader looking for
    an AOAI resource that does not exist."""
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = STORAGE_RID
    result = run(f=aoai_forecast(), a=a)
    assert result["drivers"]["payg_ptu"]["status"] == "not-verified"
    excluded = [
        warning
        for warning in result["warnings"]
        if "drivers.payg_ptu" in warning and "storageaccounts" in warning
    ]
    assert excluded
    assert all("resource account" in warning for warning in excluded)
    assert all("Azure OpenAI account" not in warning for warning in excluded)


def test_an_unimplicated_aoai_row_is_still_named_as_azure_openai() -> None:
    """The neutral wording is not a blanket downgrade: an identifier whose
    normalized type DOES prove Azure OpenAI is still named as one."""
    a = aoai_actuals()
    a["usage"]["models"][0]["resource_id"] = AOAI_DEPLOYMENT_2
    result = run(f=aoai_forecast(), a=a)
    warning = next(
        w
        for w in result["warnings"]
        if "drivers.payg_ptu" in w and "aoai2" in w
    )
    assert "Azure OpenAI account" in warning


def test_an_aoai_account_identifier_is_named_as_azure_openai() -> None:
    a = aoai_actuals()
    a["usage"]["models"][0]["account_resource_id"] = AOAI_ACCOUNT_2
    result = run(f=aoai_forecast(), a=a)
    warning = next(
        w
        for w in result["warnings"]
        if "drivers.payg_ptu" in w and "aoai2" in w
    )
    assert "Azure OpenAI account" in warning


# ---------------------------------------------------------------------------
# A negative forecast baseline has no percentage
# ---------------------------------------------------------------------------


def test_negative_forecast_total_yields_null_variance_pct() -> None:
    """Dividing by a negative baseline inverts the sign: $70 observed against
    a -$70 projection would report -2.0 — "200% under budget" — for a
    workload that came in over. The numbers are kept; the ratio is not."""
    result = run(f=forecast(-300.0))
    assert result["totals"]["forecast_monthly_usd"] == -300.0
    assert result["totals"]["forecast_window_usd"] == -70.0
    assert result["totals"]["variance_window_usd"] == 140.0
    assert result["totals"]["variance_pct"] is None
    assert result["variance_status"] == "not-verified"
    assert any("negative" in warning for warning in result["warnings"])


# ---------------------------------------------------------------------------
# Always-emit contract, purity and strict shapes
# ---------------------------------------------------------------------------


def test_policy_errors_still_emit_a_full_manifest() -> None:
    """RFC §12: an incomplete or invalid policy never suppresses evidence.
    `reconcile_costs` accepts the parser's error list and emits a
    `not-verified` manifest rather than refusing to produce one."""
    result = run(p={}, errors=["cost.maturity_policy.min_complete_days is missing"])
    assert result["schema"] == "threadlight-cost-reconciliation/v1"
    assert result["maturity"]["status"] == "not-verified"
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["policy_errors"] == [
        "cost.maturity_policy.min_complete_days is missing"
    ]
    # Observed evidence is still reported, because it was still observed.
    assert result["totals"]["actual_window_usd"] == 70.0


def test_top_level_shape_is_exactly_the_documented_schema() -> None:
    result = run()
    assert set(result) == {
        "schema",
        "generated_at",
        "status",
        "variance_status",
        "forecast_ref",
        "actuals_ref",
        "policy_ref",
        "policy_snapshot",
        "policy_errors",
        "maturity",
        "totals",
        "unit_economics",
        "coverage",
        "drivers",
        "warnings",
    }
    assert set(result["totals"]) == {
        "forecast_monthly_usd",
        "forecast_window_usd",
        "actual_window_usd",
        "actual_monthly_run_rate_usd",
        "variance_window_usd",
        "variance_pct",
    }
    assert set(result["unit_economics"]) == {
        "status",
        "successful_interactions",
        "cost_per_successful_interaction_usd",
        "target_usd",
        "target_status",
    }
    assert set(result["coverage"]) == {
        "projection_attribution_coverage_pct",
        "source_resource_id_coverage_pct",
        "unmodeled_actual_usd",
        "forecast_not_observed_usd",
        "matched_resources",
        "unmodeled_resources",
        "forecast_not_observed_resources",
    }
    assert set(result["drivers"]) == {"payg_ptu"}
    assert set(result["drivers"]["payg_ptu"]) == {
        "status",
        "observed_volume_variance_pct",
        "forecast_monthly_tokens",
        "observed_monthly_tokens",
        "threshold_field",
        "threshold_pct",
        "detail",
    }


def test_result_is_json_serializable() -> None:
    json.dumps(run())


def test_inputs_are_never_mutated() -> None:
    f, a, p, errors = forecast(), actuals(), policy(), ["cost.baseline: bad"]
    before = (deepcopy(f), deepcopy(a), deepcopy(p), deepcopy(errors))
    reconcile_costs(
        f, a, p,
        policy_errors=errors,
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
    )
    assert (f, a, p, errors) == before


def test_output_lists_are_copies_of_the_caller_list() -> None:
    errors = ["cost.baseline: bad"]
    result = run(errors=errors)
    result["policy_errors"].append("mutated")
    assert errors == ["cost.baseline: bad"]


def test_generated_at_must_be_utc_iso_with_a_z_suffix() -> None:
    for bad in (
        "2026-08-10T00:00:00+00:00",
        "2026-08-10 00:00:00Z",
        "2026-08-10",
        "2026-13-10T00:00:00Z",
        "not-a-time",
        None,
        1754784000,
    ):
        with pytest.raises(ReconciliationInputError):
            reconcile_costs(
                forecast(), actuals(), policy(),
                policy_errors=[],
                generated_at=bad,
                policy_spec_sha256=SPEC_SHA256,
            )


def test_generated_at_is_echoed_verbatim() -> None:
    assert run()["generated_at"] == GENERATED


def test_policy_spec_sha256_must_be_a_string() -> None:
    with pytest.raises(ReconciliationInputError):
        reconcile_costs(
            forecast(), actuals(), policy(),
            policy_errors=[],
            generated_at=GENERATED,
            policy_spec_sha256=None,
        )


def test_policy_errors_must_be_a_list_of_str() -> None:
    for bad in ("a string", [1], [None], {"a": 1}):
        with pytest.raises(ReconciliationInputError):
            reconcile_costs(
                forecast(), actuals(), policy(),
                policy_errors=bad,
                generated_at=GENERATED,
                policy_spec_sha256=SPEC_SHA256,
            )


@pytest.mark.parametrize("document", ["forecast", "actuals", "policy"])
def test_non_mapping_documents_are_rejected(document: str) -> None:
    args = {"f": forecast(), "a": actuals(), "p": policy()}
    args[{"forecast": "f", "actuals": "a", "policy": "p"}[document]] = ["not", "a", "map"]
    with pytest.raises(ReconciliationInputError):
        run(**args)


def test_malformed_actual_resource_list_is_rejected() -> None:
    a = actuals()
    a["cost"]["resources"] = "not-a-list"
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_malformed_actual_resource_entry_is_rejected() -> None:
    a = actuals()
    a["cost"]["resources"] = ["not-a-mapping"]
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_malformed_forecast_resource_list_is_rejected() -> None:
    f = forecast()
    f["resources"] = {"resource_id": RID}
    with pytest.raises(ReconciliationInputError):
        run(f=f)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "abc", []])
def test_non_finite_or_non_numeric_money_is_rejected(bad: object) -> None:
    a = actuals()
    a["cost"]["period_total_usd"] = bad
    with pytest.raises(ReconciliationInputError):
        run(a=a)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "abc"])
def test_non_finite_forecast_total_is_rejected(bad: object) -> None:
    f = forecast()
    f["totals"]["monthly_cost_current_usd"] = bad
    with pytest.raises(ReconciliationInputError):
        run(f=f)


def test_bool_interaction_count_is_rejected() -> None:
    a = actuals()
    a["usage"]["successful_interactions"] = True
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_negative_interaction_count_is_rejected() -> None:
    a = actuals()
    a["usage"]["successful_interactions"] = -1
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_malformed_source_coverage_is_rejected() -> None:
    a = actuals()
    a["cost"]["resource_id_coverage_pct"] = "high"
    with pytest.raises(ReconciliationInputError):
        run(a=a)


def test_missing_optional_sections_never_raise() -> None:
    """A minimal actuals document with no `usage`/`cost.resources` at all is
    absent evidence, not malformed evidence."""
    result = run(a={"schema": "threadlight-cost-actuals/v1", "status": "not-verified"})
    assert result["schema"] == "threadlight-cost-reconciliation/v1"
    assert result["status"] == "not-verified"
    assert result["totals"]["actual_window_usd"] is None
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["coverage"]["projection_attribution_coverage_pct"] is None


def test_warnings_are_strings() -> None:
    result = run(f=forecast(0.0))
    assert result["warnings"]
    assert all(isinstance(warning, str) for warning in result["warnings"])


def test_module_performs_no_io_or_subprocess_work() -> None:
    """This module is pure: no Azure calls, no shelling out, no file reads.
    Task 9's adapter is the only caller allowed to touch the network."""
    source = (SCRIPTS / "reconcile.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "urllib", "requests", "socket", "os.environ", "open("):
        assert forbidden not in source, f"reconcile.py must not reference {forbidden!r}"
