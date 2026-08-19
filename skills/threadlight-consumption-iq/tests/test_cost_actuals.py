"""
Tests for cost_actuals.py — parse observed Azure Cost Management `Usage`
Query API evidence (RFC §8.1, §9.2, §9.7) into a pure, offline
`threadlight-cost-actuals/v1` manifest.

See `skills/threadlight-consumption-iq/references/cost-actuals-manifest-schema.md`
for the full manifest schema this module produces, and
`docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§7.2 / §8.1 / §9.7 for the RFC this module implements.

Core contract under test:
  - Cost Management response columns are mapped by casefolded *name*, never
    position. Malformed columns/rows fail closed (raise), never silently
    drop or reinterpret data.
  - `UsageDate` is required on every row and is validated against the
    declared window itself (start-inclusive, end-exclusive) rather than
    trusting the Query API's own boundary semantics.
  - Exactly one cost column is used, in priority order
    `PreTaxCost > CostUSD > Cost` (case-insensitive); none present is an
    error, never a silent zero.
  - Money is parsed via `decimal.Decimal`; negative refunds are preserved,
    not clipped.
  - `resource_id_coverage_pct` is a *source-quality* measure (gross absolute
    cost as the denominator, absolute identified-row cost as the numerator)
    and is unrelated to the reconciliation's own projection-attribution
    coverage (Task 8).
  - `build_actuals_manifest` never issues Azure calls; it raises
    `ActualsEvidenceError` for evidence that does not parse/validate and
    otherwise always returns `status: pass` for the Cost Management source,
    scope, and window — optional token/interaction evidence is recorded with
    its own independent `not-verified` sub-status and never demotes the
    top-level status.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "fixtures"
    / "sample-cost-actuals"
)

from cost_actuals import (  # noqa: E402
    ActualsEvidenceError,
    CostAggregate,
    aggregate_cost_rows,
    build_actuals_manifest,
    rows_from_query_page,
    select_cost_column,
)


COLUMNS = [
    {"name": "UsageDate", "type": "Number"},
    {"name": "ResourceType", "type": "String"},
    {"name": "PreTaxCost", "type": "Number"},
    {"name": "Currency", "type": "String"},
    {"name": "ResourceId", "type": "String"},
    {"name": "ServiceName", "type": "String"},
]
RID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/"
    "resourceGroups/rg-pilot/providers/Microsoft.App/containerApps/agent"
)
WINDOW = dict(
    start=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end=datetime(2026, 8, 8, tzinfo=timezone.utc),
)


def page(rows, columns=None):
    return {
        "properties": {
            "columns": deepcopy(columns or COLUMNS),
            "rows": rows,
            "nextLink": None,
        }
    }


def aggregate(rows, columns=None, **overrides):
    kwargs = {**WINDOW, **overrides}
    return aggregate_cost_rows([page(rows, columns)], **kwargs)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Column mapping by name, not position
# ---------------------------------------------------------------------------


def test_columns_are_mapped_by_name_not_position() -> None:
    rows = rows_from_query_page(
        page([[20260801, "microsoft.app/containerapps", 12.5, "USD", RID, "ACA"]])
    )
    assert rows[0]["pretaxcost"] == 12.5
    assert rows[0]["resourceid"] == RID
    assert rows[0]["usagedate"] == 20260801


def test_rows_for_same_resource_are_summed() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 12.5, "USD", RID, "ACA"],
        [20260802, "microsoft.app/containerapps", 2.5, "USD", RID, "ACA"],
    ])
    assert result.resources == [{
        "resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "service_name": "ACA",
        "period_cost_usd": 15.0,
    }]
    assert (result.total_usd, result.currency, result.unattributed_usd) == (
        15.0, "USD", 0.0
    )
    assert result.cost_column == "PreTaxCost"


def test_blank_resource_id_remains_in_total_and_is_unattributed() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 12.5, "USD", RID, "ACA"],
        [20260801, "", 3.0, "USD", "", "Bandwidth"],
    ])
    assert sum(r["period_cost_usd"] for r in result.resources) == 12.5
    assert result.total_usd == 15.5
    assert result.unattributed_usd == 3.0


def test_mixed_currency_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="multiple currencies"):
        aggregate([
            [20260801, "x", 1.0, "USD", RID, "A"],
            [20260801, "x", 1.0, "EUR", RID, "A"],
        ])


def test_currency_comparison_is_case_insensitive_but_preserves_first_casing() -> None:
    result = aggregate([
        [20260801, "x", 1.0, "usd", RID, "A"],
        [20260802, "x", 1.0, "USD", RID, "A"],
    ])
    assert result.currency == "usd"
    assert result.total_usd == 2.0


def test_missing_cost_column_is_rejected() -> None:
    columns = [c for c in COLUMNS if c["name"] != "PreTaxCost"]
    with pytest.raises(ActualsEvidenceError, match="no cost column"):
        rows_from_query_page(page([[20260801, "x", "USD", RID, "A"]], columns))


@pytest.mark.parametrize("alias", ["CostUSD", "Cost", "cost", "costusd"])
def test_cost_column_aliases_are_accepted(alias: str) -> None:
    columns = [
        dict(c, name=alias) if c["name"] == "PreTaxCost" else c for c in COLUMNS
    ]
    result = aggregate(
        [[20260801, "x", 4.0, "USD", RID, "A"]], columns
    )
    assert result.total_usd == 4.0
    assert result.cost_column == alias


def test_primary_cost_column_wins_when_several_are_present() -> None:
    columns = COLUMNS + [{"name": "CostUSD", "type": "Number"}]
    result = aggregate(
        [[20260801, "x", 4.0, "USD", RID, "A", 999.0]], columns
    )
    assert result.cost_column == "PreTaxCost"
    assert result.total_usd == 4.0


def test_duplicate_column_names_are_rejected() -> None:
    columns = COLUMNS + [{"name": "PreTaxCost", "type": "Number"}]
    with pytest.raises(ActualsEvidenceError, match="duplicate column"):
        rows_from_query_page(
            page([[20260801, "x", 1.0, "USD", RID, "A", 1.0]], columns)
        )


def test_duplicate_column_names_are_rejected_even_with_different_casing() -> None:
    columns = COLUMNS + [{"name": "pretaxcost", "type": "Number"}]
    with pytest.raises(ActualsEvidenceError, match="duplicate column"):
        rows_from_query_page(
            page([[20260801, "x", 1.0, "USD", RID, "A", 1.0]], columns)
        )


def test_non_numeric_cost_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", "free", "USD", RID, "A"]])


def test_nonfinite_cost_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", float("nan"), "USD", RID, "A"]])


def test_infinite_cost_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", float("inf"), "USD", RID, "A"]])


def test_boolean_cost_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", True, "USD", RID, "A"]])


def test_malformed_row_is_rejected_not_dropped() -> None:
    with pytest.raises(ActualsEvidenceError, match="row does not match columns"):
        rows_from_query_page(page([[20260801, 1.0]]))


def test_negative_refund_is_retained() -> None:
    result = aggregate([
        [20260801, "x", 10.0, "USD", RID, "A"],
        [20260802, "x", -2.0, "USD", RID, "A"],
    ])
    assert result.total_usd == 8.0
    assert result.resources[0]["period_cost_usd"] == 8.0


def test_total_equals_resources_plus_unattributed() -> None:
    result = aggregate([
        [20260801, "x", 10.0, "USD", RID, "A"],
        [20260801, "", 4.0, "USD", "", "Tax"],
    ])
    assert result.total_usd == sum(
        r["period_cost_usd"] for r in result.resources
    ) + result.unattributed_usd


def test_resource_id_coverage_is_a_source_quality_measure() -> None:
    result = aggregate([
        [20260801, "x", 9.0, "USD", RID, "A"],
        [20260801, "", 1.0, "USD", "", "Tax"],
    ])
    # 9 of 10 USD carry a resource ID. This measures the *source*, and is not
    # the reconciliation's projection-attribution coverage (Task 8).
    assert result.resource_id_coverage_pct == pytest.approx(0.9)


def test_coverage_stays_bounded_in_zero_to_one_with_a_negative_refund_row() -> None:
    # A refund (negative cost) on an unattributed row must not push the
    # source-quality coverage measure outside [0, 1] or negative: the
    # denominator and numerator are both gross *absolute* cost.
    result = aggregate([
        [20260801, "x", 10.0, "USD", RID, "A"],
        [20260802, "", -3.0, "USD", "", "Refund"],
    ])
    assert result.total_usd == 7.0
    assert result.unattributed_usd == -3.0
    assert 0.0 <= result.resource_id_coverage_pct <= 1.0
    assert result.resource_id_coverage_pct == pytest.approx(10.0 / 13.0)


def test_coverage_stays_bounded_when_the_identified_row_is_itself_a_refund() -> None:
    result = aggregate([
        [20260801, "x", -5.0, "USD", RID, "A"],
        [20260802, "", 1.0, "USD", "", "Tax"],
    ])
    assert result.total_usd == -4.0
    assert 0.0 <= result.resource_id_coverage_pct <= 1.0
    assert result.resource_id_coverage_pct == pytest.approx(5.0 / 6.0)


def test_zero_gross_cost_yields_null_coverage_not_a_zero_division() -> None:
    result = aggregate([[20260801, "x", 0.0, "USD", RID, "A"]])
    assert result.total_usd == 0.0
    assert result.resource_id_coverage_pct is None


def test_missing_usage_date_column_is_rejected() -> None:
    columns = [c for c in COLUMNS if c["name"] != "UsageDate"]
    with pytest.raises(ActualsEvidenceError, match="UsageDate column missing"):
        aggregate([["x", 1.0, "USD", RID, "A"]], columns)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (20260801, date(2026, 8, 1)),
        ("20260801", date(2026, 8, 1)),
        ("2026-08-01", date(2026, 8, 1)),
        ("2026-08-01T00:00:00Z", date(2026, 8, 1)),
    ],
)
def test_usage_date_is_normalized_from_every_observed_shape(raw, expected) -> None:
    result = aggregate([[raw, "x", 1.0, "USD", RID, "A"]])
    assert result.usage_dates == {expected}


def test_unparseable_usage_date_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="UsageDate is not a date"):
        aggregate([["last tuesday", "x", 1.0, "USD", RID, "A"]])


def test_row_before_window_start_is_rejected_not_dropped() -> None:
    with pytest.raises(ActualsEvidenceError, match="outside the requested window"):
        aggregate([[20260731, "x", 1.0, "USD", RID, "A"]])


def test_row_on_window_end_is_rejected_because_end_is_exclusive() -> None:
    # The request body sends `end` at UTC midnight; the Query API's own
    # inclusivity is not relied upon. The parser enforces
    # `start <= usage_date < end` itself, so a row dated 2026-08-08 in a
    # window ending 2026-08-08T00:00:00Z is a contract violation.
    with pytest.raises(ActualsEvidenceError, match="outside the requested window"):
        aggregate([[20260808, "x", 1.0, "USD", RID, "A"]])


def test_last_in_window_day_is_accepted() -> None:
    result = aggregate([[20260807, "x", 1.0, "USD", RID, "A"]])
    assert result.usage_dates == {date(2026, 8, 7)}


def test_non_positive_window_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="end must be after start"):
        aggregate(
            [[20260801, "x", 1.0, "USD", RID, "A"]],
            end=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )


def test_naive_start_datetime_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="timezone-aware UTC"):
        aggregate_cost_rows(
            [page([[20260801, "x", 1.0, "USD", RID, "A"]])],
            start=datetime(2026, 8, 1),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        )


def test_non_utc_offset_end_datetime_is_rejected() -> None:
    non_utc = timezone(timedelta(hours=5))
    with pytest.raises(ActualsEvidenceError, match="timezone-aware UTC"):
        aggregate_cost_rows(
            [page([[20260801, "x", 1.0, "USD", RID, "A"]])],
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=non_utc),
        )


def test_pagination_aggregates_rows_across_pages() -> None:
    page_1 = page([[20260801, "microsoft.app/containerapps", 10.0, "USD", RID, "ACA"]])
    page_2 = page([[20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"]])
    result = aggregate_cost_rows([page_1, page_2], **WINDOW)
    assert result.total_usd == 15.0
    assert result.usage_dates == {date(2026, 8, 1), date(2026, 8, 2)}
    assert result.resources == [{
        "resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "service_name": "ACA",
        "period_cost_usd": 15.0,
    }]


def test_pagination_still_validates_every_page_window() -> None:
    page_1 = page([[20260801, "x", 10.0, "USD", RID, "A"]])
    page_2 = page([[20260731, "x", 5.0, "USD", RID, "A"]])
    with pytest.raises(ActualsEvidenceError, match="outside the requested window"):
        aggregate_cost_rows([page_1, page_2], **WINDOW)


def test_pages_disagreeing_on_cost_column_are_rejected() -> None:
    aliased_columns = [
        dict(c, name="CostUSD") if c["name"] == "PreTaxCost" else c for c in COLUMNS
    ]
    page_1 = page([[20260801, "x", 10.0, "USD", RID, "A"]])
    page_2 = page([[20260802, "x", 5.0, "USD", RID, "A"]], aliased_columns)
    with pytest.raises(ActualsEvidenceError, match="disagree on cost column"):
        aggregate_cost_rows([page_1, page_2], **WINDOW)


def test_rows_from_query_page_never_mutates_the_input_page() -> None:
    original = page([[20260801, "x", 1.0, "USD", RID, "A"]])
    snapshot = deepcopy(original)
    rows_from_query_page(original)
    assert original == snapshot


def test_aggregate_cost_rows_never_mutates_the_input_pages() -> None:
    pages = [page([[20260801, "x", 1.0, "USD", RID, "A"]])]
    snapshot = deepcopy(pages)
    aggregate_cost_rows(pages, **WINDOW)
    assert pages == snapshot


# ---------------------------------------------------------------------------
# build_actuals_manifest
# ---------------------------------------------------------------------------


def _manifest_kwargs(*, start, end, generated_at):
    return dict(
        scope={"subscription_id": "00000000-0000-0000-0000-000000000000"},
        start=start,
        end=end,
        generated_at=generated_at,
        cost_pages=[page([[20260801, "x", 10.0, "USD", RID, "A"]])],
        token_series=None,
        interaction_counts=None,
        provenance={"query_api_version": "2025-03-01"},
        warnings=[],
    )


def test_settlement_age_hours_is_generated_at_minus_window_end() -> None:
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    assert manifest["window"]["settlement_age_hours"] == 48
    assert manifest["window"]["window_end_age_days"] == 2
    assert manifest["window"]["complete_days"] == 7


def test_generated_at_before_window_end_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="generated_at.*before.*end"):
        build_actuals_manifest(
            **_manifest_kwargs(
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 8, tzinfo=timezone.utc),
                generated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
        )


def test_naive_generated_at_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="timezone-aware UTC"):
        build_actuals_manifest(
            **_manifest_kwargs(
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 8, tzinfo=timezone.utc),
                generated_at=datetime(2026, 8, 10),
            )
        )


def test_top_level_status_is_pass_without_optional_evidence() -> None:
    """RFC §7.2: top-level status is produced by Cost Management source,
    scope, and window alone. Absent token metrics and absent interaction
    counts are recorded as `not-verified` sub-statuses and never demote it."""
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    assert manifest["status"] == "pass"
    assert manifest["usage"]["interaction_status"] == "not-verified"
    assert manifest["usage"]["model_attribution_status"] == "not-verified"
    assert manifest["usage"]["total_interactions"] is None
    assert manifest["usage"]["successful_interactions"] is None
    assert manifest["usage"]["models"] == []
    assert manifest["schema"] == "threadlight-cost-actuals/v1"
    assert manifest["cost"]["basis"] == "usage-pretax"
    assert manifest["cost"]["cost_column"] == "PreTaxCost"


def test_empty_scope_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["scope"] = {}
    with pytest.raises(ActualsEvidenceError, match="scope must be a non-empty mapping"):
        build_actuals_manifest(**kwargs)


def test_non_mapping_scope_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["scope"] = None
    with pytest.raises(ActualsEvidenceError, match="scope must be a non-empty mapping"):
        build_actuals_manifest(**kwargs)


def test_zero_interaction_counts_are_pass_not_unverified() -> None:
    """A real, observed zero is distinct from an unobserved value: it must
    still read `pass` with integer zeros, never fall back to `not-verified`
    or `null`."""
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["interaction_counts"] = (0, 0)
    manifest = build_actuals_manifest(**kwargs)
    assert manifest["usage"]["interaction_status"] == "pass"
    assert manifest["usage"]["total_interactions"] == 0
    assert manifest["usage"]["successful_interactions"] == 0


def test_unobserved_interaction_counts_remain_null_not_zero() -> None:
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    assert manifest["usage"]["interaction_status"] == "not-verified"
    assert manifest["usage"]["total_interactions"] is None
    assert manifest["usage"]["successful_interactions"] is None


def test_invalid_interaction_counts_shape_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["interaction_counts"] = (5,)
    with pytest.raises(ActualsEvidenceError, match="interaction_counts"):
        build_actuals_manifest(**kwargs)


def test_empty_token_series_is_observed_pass_with_empty_models() -> None:
    """An empty list means the token query ran and genuinely observed no
    rows; that is distinct from `None` (query never ran) and must still be
    `pass`."""
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["token_series"] = []
    manifest = build_actuals_manifest(**kwargs)
    assert manifest["usage"]["model_attribution_status"] == "pass"
    assert manifest["usage"]["models"] == []


def test_none_token_series_is_not_verified_with_empty_models() -> None:
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    assert manifest["usage"]["model_attribution_status"] == "not-verified"
    assert manifest["usage"]["models"] == []


def test_populated_token_series_is_recorded_verbatim() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    rows = [{"deployment": "gpt4o", "input_tokens": 100, "output_tokens": 50}]
    kwargs["token_series"] = rows
    manifest = build_actuals_manifest(**kwargs)
    assert manifest["usage"]["model_attribution_status"] == "pass"
    assert manifest["usage"]["models"] == rows
    # the returned list must not be the same object as the caller's list
    assert manifest["usage"]["models"] is not rows


def test_build_actuals_manifest_does_not_mutate_inputs() -> None:
    scope = {"subscription_id": "00000000-0000-0000-0000-000000000000"}
    cost_pages = [page([[20260801, "x", 10.0, "USD", RID, "A"]])]
    token_series = [{"deployment": "gpt4o", "input_tokens": 100}]
    provenance = {"query_api_version": "2025-03-01"}
    warnings = ["a warning"]

    scope_snapshot = deepcopy(scope)
    cost_pages_snapshot = deepcopy(cost_pages)
    token_series_snapshot = deepcopy(token_series)
    provenance_snapshot = deepcopy(provenance)
    warnings_snapshot = deepcopy(warnings)

    manifest = build_actuals_manifest(
        scope=scope,
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        cost_pages=cost_pages,
        token_series=token_series,
        interaction_counts=(5, 4),
        provenance=provenance,
        warnings=warnings,
    )

    assert scope == scope_snapshot
    assert cost_pages == cost_pages_snapshot
    assert token_series == token_series_snapshot
    assert provenance == provenance_snapshot
    assert warnings == warnings_snapshot

    # Mutating the returned manifest's nested containers must never reach
    # back into the caller's original objects.
    manifest["scope"]["subscription_id"] = "mutated"
    manifest["warnings"].append("mutated")
    manifest["provenance"]["query_api_version"] = "mutated"
    manifest["usage"]["models"].append({"mutated": True})

    assert scope == scope_snapshot
    assert warnings == warnings_snapshot
    assert provenance == provenance_snapshot
    assert token_series == token_series_snapshot


def test_scope_is_recorded_verbatim() -> None:
    scope = {
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "resource_group": "rg-pilot",
    }
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["scope"] = scope
    manifest = build_actuals_manifest(**kwargs)
    assert manifest["scope"] == scope


def test_warnings_and_provenance_are_copied_through() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["warnings"] = ["Log Analytics workspace not resolvable"]
    kwargs["provenance"] = {
        "query_api_version": "2025-03-01",
        "cost_management": {"granularity": "Daily"},
    }
    manifest = build_actuals_manifest(**kwargs)
    assert manifest["warnings"] == ["Log Analytics workspace not resolvable"]
    assert manifest["provenance"] == {
        "query_api_version": "2025-03-01",
        "cost_management": {"granularity": "Daily"},
    }


# ---------------------------------------------------------------------------
# Sanitized fixtures parse
# ---------------------------------------------------------------------------


def test_page_1_and_page_2_fixtures_parse_and_aggregate_across_pagination() -> None:
    page_1 = _load_fixture("cost-query-page-1.json")
    page_2 = _load_fixture("cost-query-page-2.json")

    rows_1 = rows_from_query_page(page_1)
    rows_2 = rows_from_query_page(page_2)
    assert rows_1 and rows_2

    result = aggregate_cost_rows(
        [page_1, page_2],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert result.total_usd > 0
    assert result.currency == "USD"
    assert result.cost_column == "PreTaxCost"
    assert len(result.usage_dates) > 1


def test_costusd_alias_fixture_parses() -> None:
    fixture = _load_fixture("cost-query-costusd-alias.json")
    result = aggregate_cost_rows(
        [fixture],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert result.cost_column == "CostUSD"
    assert result.total_usd > 0


def test_aoai_account_fixture_parses_and_rolls_up_by_account() -> None:
    fixture = _load_fixture("cost-query-aoai-account.json")
    rows = rows_from_query_page(fixture)
    assert rows

    result = aggregate_cost_rows(
        [fixture],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert result.total_usd > 0
    # Two distinct AOAI accounts must be reported as two distinct resources;
    # reconciliation-level deployment roll-up is Task 8, not this parser.
    resource_ids = {
        r["resource_id"].casefold().rstrip("/") for r in result.resources
    }
    assert len(resource_ids) == 2
    for resource in result.resources:
        assert resource["resource_type"] == "microsoft.cognitiveservices/accounts"


# ---------------------------------------------------------------------------
# Accounting-safe money: Decimal ROUND_HALF_UP, never Python round(float)
# ---------------------------------------------------------------------------


def test_money_rounds_with_decimal_round_half_up_not_python_round() -> None:
    # float(2.675) is actually 2.67499999999999982236..., so Python's
    # round(2.675, 2) gives 2.67 (a binary-float artifact, not true
    # half-up). Decimal(str(2.675)) recovers the exact "2.675" the caller
    # intended, and ROUND_HALF_UP correctly rounds the true tie up to 2.68.
    result = aggregate([[20260801, "x", 2.675, "USD", RID, "A"]])
    assert result.resources[0]["period_cost_usd"] == 2.68
    assert result.total_usd == 2.68
    assert round(2.675, 2) == 2.67  # sanity: this is exactly the trap


# ---------------------------------------------------------------------------
# Accounting identity: period_total_usd == sum(resources) + unattributed,
# exactly at the cent, even with half-cent rows and refunds
# ---------------------------------------------------------------------------


def test_several_half_cent_resource_rows_reconcile_exactly_after_quantization() -> None:
    rid_a, rid_b, rid_c = RID + "/a", RID + "/b", RID + "/c"
    result = aggregate([
        [20260801, "x", 0.005, "USD", rid_a, "A"],
        [20260801, "x", 0.005, "USD", rid_b, "A"],
        [20260801, "x", 0.005, "USD", rid_c, "A"],
    ])
    parts_sum = sum(
        (Decimal(str(r["period_cost_usd"])) for r in result.resources), Decimal("0")
    ) + Decimal(str(result.unattributed_usd))
    assert parts_sum == Decimal(str(result.total_usd))
    assert Decimal(str(result.total_usd)) == Decimal("0.02")
    # Deterministic tie-break: the lexicographically smallest normalized
    # resource key absorbs the residual (all three are tied at 0.01).
    by_id = {r["resource_id"]: r["period_cost_usd"] for r in result.resources}
    assert by_id == {rid_a: 0.0, rid_b: 0.01, rid_c: 0.01}


def test_half_cent_reconciliation_with_a_resource_and_unattributed_refund() -> None:
    rid_b = RID + "/b"
    result = aggregate([
        [20260801, "x", 10.005, "USD", RID, "A"],
        [20260801, "x", -0.005, "USD", rid_b, "B"],
        [20260801, "", -0.005, "USD", "", "Refund"],
    ])
    parts_sum = sum(
        (Decimal(str(r["period_cost_usd"])) for r in result.resources), Decimal("0")
    ) + Decimal(str(result.unattributed_usd))
    assert parts_sum == Decimal(str(result.total_usd))
    assert Decimal(str(result.total_usd)) == Decimal("10.00")
    # The residual is added to the largest-*absolute*-value resource
    # bucket (RID at 10.01), never to unattributed, since resources exist.
    by_id = {r["resource_id"]: r["period_cost_usd"] for r in result.resources}
    assert by_id[RID] == 10.02
    assert by_id[rid_b] == -0.01
    assert result.unattributed_usd == -0.01


def test_all_unattributed_half_cent_rows_still_reconcile_exactly() -> None:
    result = aggregate([
        [20260801, "", 0.005, "USD", "", "Tax"],
        [20260802, "", 0.005, "USD", "", "Tax"],
        [20260803, "", 0.005, "USD", "", "Tax"],
    ])
    assert result.resources == []
    assert Decimal(str(result.total_usd)) == Decimal(str(result.unattributed_usd))
    assert Decimal(str(result.total_usd)) == Decimal("0.02")


def test_manifest_accounting_identity_holds_exactly_after_serialization() -> None:
    rid_a, rid_b, rid_c = RID + "/a", RID + "/b", RID + "/c"
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["cost_pages"] = [page([
        [20260801, "x", 0.005, "USD", rid_a, "A"],
        [20260801, "x", 0.005, "USD", rid_b, "A"],
        [20260801, "x", -10.005, "USD", rid_c, "A"],
        [20260801, "", 5.005, "USD", "", "Tax"],
    ])]
    manifest = build_actuals_manifest(**kwargs)
    serialized = json.loads(json.dumps(manifest))
    cost = serialized["cost"]
    resources_sum = sum(Decimal(str(r["period_cost_usd"])) for r in cost["resources"])
    unattributed = Decimal(str(cost["unattributed_usd"]))
    total = Decimal(str(cost["period_total_usd"]))
    assert total == resources_sum + unattributed


# ---------------------------------------------------------------------------
# UsageDate: strict UTC-midnight datetimes, ASCII-only digits
# ---------------------------------------------------------------------------


def test_naive_iso_datetime_usage_date_is_rejected_not_bucketed_locally() -> None:
    with pytest.raises(ActualsEvidenceError, match="UTC"):
        aggregate([["2026-08-01T00:00:00", "x", 1.0, "USD", RID, "A"]])


def test_non_utc_offset_iso_datetime_usage_date_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="UTC"):
        aggregate([["2026-08-01T00:00:00+05:00", "x", 1.0, "USD", RID, "A"]])


def test_non_midnight_utc_iso_datetime_usage_date_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="midnight"):
        aggregate([["2026-08-01T13:45:00Z", "x", 1.0, "USD", RID, "A"]])


def test_utc_midnight_z_iso_datetime_usage_date_is_still_accepted() -> None:
    result = aggregate([["2026-08-01T00:00:00Z", "x", 1.0, "USD", RID, "A"]])
    assert result.usage_dates == {date(2026, 8, 1)}


def test_utc_midnight_explicit_offset_iso_datetime_usage_date_is_accepted() -> None:
    result = aggregate([["2026-08-01T00:00:00+00:00", "x", 1.0, "USD", RID, "A"]])
    assert result.usage_dates == {date(2026, 8, 1)}


def test_unicode_digits_in_yyyymmdd_usage_date_are_rejected() -> None:
    # "٢٠٢٦٠٨٠١" is 20260801 spelled with Arabic-Indic digits. Python's
    # `\d` (without re.ASCII) and `int()` both silently accept these, so
    # this must be explicitly rejected rather than parsed as the plain
    # ASCII form.
    with pytest.raises(ActualsEvidenceError, match="UsageDate is not a date"):
        aggregate([["٢٠٢٦٠٨٠١", "x", 1.0, "USD", RID, "A"]])


def test_unicode_digits_in_iso_date_usage_date_are_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="UsageDate is not a date"):
        aggregate([["٢٠٢٦-٠٨-٠١", "x", 1.0, "USD", RID, "A"]])


# ---------------------------------------------------------------------------
# Window start/end: UTC and exact midnight, so complete_days is exact
# ---------------------------------------------------------------------------


def test_non_midnight_start_datetime_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="midnight"):
        aggregate_cost_rows(
            [page([[20260801, "x", 1.0, "USD", RID, "A"]])],
            start=datetime(2026, 8, 1, 13, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        )


def test_non_midnight_end_datetime_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="midnight"):
        aggregate_cost_rows(
            [page([[20260801, "x", 1.0, "USD", RID, "A"]])],
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, 0, 0, 1, tzinfo=timezone.utc),
        )


def test_generated_at_is_not_required_to_be_midnight() -> None:
    # generated_at is a real point-in-time timestamp, not a window
    # boundary — it must never be forced onto a calendar-day boundary.
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, 14, 32, 0, tzinfo=timezone.utc),
        )
    )
    assert manifest["window"]["settlement_age_hours"] == 62


# ---------------------------------------------------------------------------
# Empty cost_pages fails; a present page with zero rows is an observed zero
# ---------------------------------------------------------------------------


def test_empty_cost_pages_list_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="no Cost Management pages"):
        aggregate_cost_rows([], **WINDOW)


def test_build_actuals_manifest_rejects_empty_cost_pages() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["cost_pages"] = []
    with pytest.raises(ActualsEvidenceError, match="no Cost Management pages"):
        build_actuals_manifest(**kwargs)


def test_single_valid_page_with_zero_rows_is_an_observed_zero_not_an_error() -> None:
    result = aggregate([])
    assert result.total_usd == 0.0
    assert result.resources == []
    assert result.unattributed_usd == 0.0
    assert result.usage_dates == set()
    assert result.cost_column == "PreTaxCost"
    # No rows means currency was never actually observed: it must not be
    # fabricated as "USD" just because that is the only currency v1 accepts.
    assert result.currency is None


# ---------------------------------------------------------------------------
# v1 is USD-only: a consistent non-USD currency is still rejected
# ---------------------------------------------------------------------------


def test_non_usd_currency_is_rejected_even_when_internally_consistent() -> None:
    with pytest.raises(ActualsEvidenceError, match="USD"):
        aggregate([
            [20260801, "x", 1.0, "EUR", RID, "A"],
            [20260802, "x", 1.0, "EUR", RID, "A"],
        ])


def test_non_usd_currency_error_names_schema_as_usd_only() -> None:
    with pytest.raises(ActualsEvidenceError, match="USD-only"):
        aggregate([[20260801, "x", 1.0, "GBP", RID, "A"]])


# ---------------------------------------------------------------------------
# resource_type/service_name backfill vs. conflict
# ---------------------------------------------------------------------------


def test_empty_first_resource_type_is_backfilled_from_a_later_nonempty_row() -> None:
    result = aggregate([
        [20260801, "", 5.0, "USD", RID, ""],
        [20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
    ])
    assert result.resources == [{
        "resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "service_name": "ACA",
        "period_cost_usd": 10.0,
    }]


def test_conflicting_nonempty_resource_type_for_same_resource_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="resource_type"):
        aggregate([
            [20260801, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
            [20260802, "microsoft.storage/accounts", 5.0, "USD", RID, "ACA"],
        ])


def test_conflicting_nonempty_service_name_for_same_resource_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="service_name"):
        aggregate([
            [20260801, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
            [20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "Other"],
        ])


# ---------------------------------------------------------------------------
# select_cost_column accepts raw (uncasefolded) casing
# ---------------------------------------------------------------------------


def test_select_cost_column_accepts_raw_original_casing() -> None:
    assert select_cost_column(["PreTaxCost", "Currency", "ResourceId"]) == "pretaxcost"
    assert select_cost_column(["ResourceId", "COSTUSD"]) == "costusd"


# ---------------------------------------------------------------------------
# interaction_counts / token_series: cheap extra validation coverage
# ---------------------------------------------------------------------------


def test_bool_interaction_counts_are_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["interaction_counts"] = (True, False)
    with pytest.raises(ActualsEvidenceError, match="interaction_counts"):
        build_actuals_manifest(**kwargs)


def test_negative_interaction_counts_are_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["interaction_counts"] = (-1, 0)
    with pytest.raises(ActualsEvidenceError, match="interaction_counts"):
        build_actuals_manifest(**kwargs)


def test_successful_interactions_exceeding_total_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["interaction_counts"] = (3, 5)
    with pytest.raises(ActualsEvidenceError, match="cannot exceed"):
        build_actuals_manifest(**kwargs)


def test_non_list_token_series_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["token_series"] = {"deployment": "gpt4o"}
    with pytest.raises(ActualsEvidenceError, match="token_series must be a list"):
        build_actuals_manifest(**kwargs)


def test_token_series_nested_structures_are_deep_copied() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    nested = [{"deployment": "gpt4o", "tags": {"env": "pilot"}}]
    kwargs["token_series"] = nested
    manifest = build_actuals_manifest(**kwargs)
    manifest["usage"]["models"][0]["tags"]["env"] = "mutated"
    assert nested[0]["tags"]["env"] == "pilot"


# ---------------------------------------------------------------------------
# Resource ID case/trailing-slash merge
# ---------------------------------------------------------------------------


def test_resource_id_case_and_trailing_slash_variants_merge_into_one_bucket() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
        [20260802, "microsoft.app/containerapps", 5.0, "USD", RID.upper() + "/", "ACA"],
    ])
    assert len(result.resources) == 1
    assert result.resources[0]["resource_id"] == RID  # first-observed casing retained
    assert result.resources[0]["period_cost_usd"] == 10.0


# ---------------------------------------------------------------------------
# CostAggregate.usage_dates is typed as set[date]
# ---------------------------------------------------------------------------


def test_cost_aggregate_usage_dates_is_typed_as_set_of_date() -> None:
    from typing import get_type_hints

    hints = get_type_hints(CostAggregate)
    assert hints["usage_dates"] == set[date]
