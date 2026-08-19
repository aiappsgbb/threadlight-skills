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
    build_success_kql,
    parse_interaction_counts,
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
        "service_names": ["ACA"],
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


# ---------------------------------------------------------------------------
# decimal.InvalidOperation / overflow / context failures from quantize must
# never escape as a raw decimal exception — always ActualsEvidenceError with
# cost context.
# ---------------------------------------------------------------------------


def test_scientific_notation_exponent_cost_raises_actuals_error_not_raw_decimal() -> None:
    # Decimal("1e300") parses fine (the constructor never applies context),
    # but quantizing it to the cent needs ~302 significant digits — far past
    # the default 28-digit Decimal context precision — and raises
    # decimal.InvalidOperation. That raw exception must never escape.
    with pytest.raises(ActualsEvidenceError, match="cost"):
        aggregate([[20260801, "x", "1e300", "USD", RID, "A"]])


def test_uppercase_signed_exponent_cost_raises_actuals_error_not_raw_decimal() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost"):
        aggregate([[20260801, "x", "1E+30", "USD", RID, "A"]])


def test_giant_digit_string_cost_raises_actuals_error_not_raw_decimal() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost"):
        aggregate([[20260801, "x", "9" * 60, "USD", RID, "A"]])


def test_giant_cost_via_python_int_raises_actuals_error_not_raw_decimal() -> None:
    # Ints bypass string parsing entirely (`Decimal(raw)` on an int), so the
    # same context-overflow-at-quantize hazard must be guarded independently
    # of the string path.
    with pytest.raises(ActualsEvidenceError, match="cost"):
        aggregate([[20260801, "x", 10**300, "USD", RID, "A"]])


def test_giant_cost_does_not_raise_raw_decimal_invalid_operation() -> None:
    import decimal

    try:
        aggregate([[20260801, "x", "1e300", "USD", RID, "A"]])
    except ActualsEvidenceError:
        pass
    except decimal.InvalidOperation:  # pragma: no cover - the bug we're fixing
        pytest.fail(
            "raw decimal.InvalidOperation escaped instead of "
            "ActualsEvidenceError"
        )


def test_giant_unattributed_cost_also_raises_actuals_error() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost"):
        aggregate([[20260801, "", "1e300", "USD", "", "Tax"]])


# A Python int of this magnitude is deliberately computed once at module
# scope and reused across the tests below: `Decimal(10**1_000_000)`
# involves rendering roughly a million decimal digits, which is not free,
# and every test below only needs to exercise the same value.
_GIANT_INT_COST = 10**1_000_000


@pytest.mark.parametrize(
    "raw", ["1E+1000000", _GIANT_INT_COST], ids=["exponent-string", "python-int"]
)
def test_pathological_cost_magnitude_raises_actuals_error_in_aggregate(raw) -> None:
    # A quoted "1E+1000000" or a comparably huge Python int both have an
    # exponent so large that even the ambient Decimal context's own Emax
    # (999999 by default) cannot represent them: unlike "1e300" (rejected
    # only later, at cent-quantization), a value this extreme overflows on
    # *any* arithmetic — including the plain abs() aggregate_cost_rows
    # performs on every parsed cost cell. `pytest.raises` here also proves
    # no raw `decimal.Overflow`/`decimal.InvalidOperation` escapes instead:
    # a leaked decimal exception is a different type than
    # `ActualsEvidenceError` and would fail this test uncaught.
    with pytest.raises(ActualsEvidenceError, match="cost value magnitude"):
        aggregate([[20260801, "x", raw, "USD", RID, "A"]])


@pytest.mark.parametrize(
    "raw", ["1E+1000000", _GIANT_INT_COST], ids=["exponent-string", "python-int"]
)
def test_pathological_cost_magnitude_raises_actuals_error_in_manifest(raw) -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["cost_pages"] = [page([[20260801, "x", raw, "USD", RID, "A"]])]
    # Same no-raw-decimal-exception guarantee as the aggregate-path test
    # above, but through the full `build_actuals_manifest` entry point.
    with pytest.raises(ActualsEvidenceError, match="cost value magnitude"):
        build_actuals_manifest(**kwargs)


# ---------------------------------------------------------------------------
# Money strings: reject underscore digit separators and any non-ASCII
# numeric syntax before Decimal() ever sees the string; only plain ASCII
# sign/digits/decimal-point/exponent is accepted.
# ---------------------------------------------------------------------------


def test_underscore_digit_separator_in_cost_string_is_rejected() -> None:
    # Decimal("1_000.50") silently parses as 1000.50 — a Python-literal
    # readability feature that must never leak into external evidence
    # parsing (a "1_000.50" cell arriving verbatim is far more likely a
    # data-quality bug than an intentional four-digit dollar amount).
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", "1_000.50", "USD", RID, "A"]])


def test_underscore_in_small_cost_string_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", "1_0.5", "USD", RID, "A"]])


def test_arabic_indic_digit_cost_string_is_rejected() -> None:
    # "١٠.٥٠" is "10.50" spelled with Arabic-Indic digits. Decimal() parses
    # these happily; re.ASCII on the money-shape regex must reject them.
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", "١٠.٥٠", "USD", RID, "A"]])


def test_fullwidth_digit_cost_string_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", "\uff11\uff10.\uff15\uff10", "USD", RID, "A"]])


def test_plain_ascii_signed_decimal_cost_string_is_still_accepted() -> None:
    # The rejection is specifically underscore/non-ASCII syntax, not
    # ordinary sign/decimal-point/exponent forms.
    result = aggregate([[20260801, "x", "-10.50", "USD", RID, "A"]])
    assert result.total_usd == -10.50


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
        "service_names": ["ACA"],
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


def test_multi_service_fixture_keeps_one_resource_with_both_service_names() -> None:
    # Regression for the live probe shape: one storage resource legitimately
    # carries both its own workload cost line and a security/protection
    # service cost line for the same day. Both belong to the same ResourceId
    # total; neither is a conflict.
    fixture = _load_fixture("cost-query-multi-service.json")
    rows = rows_from_query_page(fixture)
    assert rows

    result = aggregate_cost_rows(
        [fixture],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert len(result.resources) == 1
    resource = result.resources[0]
    assert resource["service_names"] == ["Microsoft Defender for Cloud", "Storage"]
    assert resource["service_name"] is None
    assert resource["resource_type"] == "microsoft.storage/storageaccounts"
    assert resource["period_cost_usd"] == result.total_usd


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


def test_negative_quantized_zero_is_normalized_to_plain_zero() -> None:
    # A raw -0.001 quantizes to Decimal("-0.00") (a signed zero), which
    # must never surface as -0.0 in the reported/serialized manifest.
    result = aggregate([[20260801, "x", -0.001, "USD", RID, "A"]])
    assert result.resources[0]["period_cost_usd"] == 0.0
    import math

    assert not math.copysign(1.0, result.resources[0]["period_cost_usd"]) < 0
    assert json.dumps(result.resources[0]["period_cost_usd"]) == "0.0"


def test_negative_quantized_zero_unattributed_is_normalized_to_plain_zero() -> None:
    result = aggregate([[20260801, "", -0.001, "USD", "", "Tax"]])
    assert result.unattributed_usd == 0.0
    assert json.dumps(result.unattributed_usd) == "0.0"


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
    # RID: raw 10.005 -> rounds to 10.01 (diff = raw - rounded = -0.005,
    # over-rounded). rid_b: raw -0.005 -> rounds to -0.01 (diff = +0.005,
    # under-rounded). unattributed: raw -0.005 -> rounds to -0.01 (diff =
    # +0.005, under-rounded, tied with rid_b). Raw total = 9.995 -> quantized
    # total 10.00; quantized parts sum to 9.99, so residual is +0.01. The
    # largest-remainder policy adds it to the most under-rounded bucket
    # (largest `raw - rounded`); rid_b and unattributed are tied, and the
    # deterministic tie-break sends it to the resource bucket, never
    # unattributed (unattributed always loses ties).
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
    by_id = {r["resource_id"]: r["period_cost_usd"] for r in result.resources}
    assert by_id[RID] == 10.01
    assert by_id[rid_b] == 0.0
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


# ---------------------------------------------------------------------------
# Largest-remainder residual distribution: over ALL attributed resources
# PLUS the unattributed bucket, deterministic, per-bucket error bound, no
# sign flips caused solely by allocation.
# ---------------------------------------------------------------------------


def _assert_reconciled(result, expected_total: str) -> dict[str, float]:
    by_id = {r["resource_id"]: r["period_cost_usd"] for r in result.resources}
    parts_sum = sum(
        (Decimal(str(v)) for v in by_id.values()), Decimal("0")
    ) + Decimal(str(result.unattributed_usd))
    assert parts_sum == Decimal(str(result.total_usd))
    assert Decimal(str(result.total_usd)) == Decimal(expected_total)
    return by_id


def test_100_half_cent_positive_rows_distribute_over_remainder_with_no_negative_artifact() -> None:
    # Every raw value is +0.005 (positive). Each independently rounds to
    # +0.01 (tie, HALF_UP rounds away from zero), so the 100 quantized parts
    # sum to 1.00 while the true raw total is only 0.50 -> residual is -0.50
    # (50 cents must be subtracted). All 100 buckets are tied at the same
    # `raw - rounded` remainder, so the tie-break (ascending normalized
    # resource key) picks the 50 lexicographically-smallest IDs to give back
    # a cent each. No bucket may go negative: every raw value here was
    # strictly positive, so "no negative artifact for positive raw" means
    # every reconciled value must land in {0.00, 0.01}, never -0.01.
    rows = [
        [20260801, "x", 0.005, "USD", f"{RID}/{i:03d}", "A"] for i in range(100)
    ]
    result = aggregate(rows)
    by_id = _assert_reconciled(result, "0.50")
    assert len(by_id) == 100
    values = sorted(by_id.values())
    assert values == [0.0] * 50 + [0.01] * 50
    assert all(v >= 0.0 for v in by_id.values())
    # Deterministic: the 50 lexicographically-smallest IDs are the ones that
    # gave back a cent (rounded down to 0.00).
    zeroed = {rid for rid, v in by_id.items() if v == 0.0}
    assert zeroed == {f"{RID}/{i:03d}" for i in range(50)}


def test_1000_small_positive_rows_distribute_over_remainder_deterministically() -> None:
    # Every raw value is +0.004 (positive, below the half-cent tie point),
    # so each independently rounds *down* to 0.00. 1000 * 0.00 == 0.00, but
    # the true raw total is 1000 * 0.004 == 4.000 -> quantized total 4.00,
    # a residual of +4.00 (400 cents to add). All 1000 buckets are tied at
    # the same remainder, so the ascending normalized-key tie-break gives a
    # cent to the 400 lexicographically-smallest IDs.
    rows = [
        [20260801, "x", 0.004, "USD", f"{RID}/{i:04d}", "A"] for i in range(1000)
    ]
    result = aggregate(rows)
    by_id = _assert_reconciled(result, "4.00")
    assert len(by_id) == 1000
    values = sorted(by_id.values())
    assert values == [0.0] * 600 + [0.01] * 400
    assert all(v >= 0.0 for v in by_id.values())
    raised = {rid for rid, v in by_id.items() if v == 0.01}
    assert raised == {f"{RID}/{i:04d}" for i in range(400)}


def test_mixed_positive_refund_and_unattributed_reconciles_with_bounded_error() -> None:
    rid_a, rid_b, rid_c, rid_d = (RID + f"/{c}" for c in "abcd")
    raws = {
        rid_a: Decimal("3.005"),
        rid_b: Decimal("-1.005"),
        rid_c: Decimal("0.002"),
        rid_d: Decimal("-0.001"),
    }
    unattributed_raw = Decimal("-0.004")
    rows = [
        [20260801, "x", str(raws[rid_a]), "USD", rid_a, "A"],
        [20260801, "x", str(raws[rid_b]), "USD", rid_b, "B"],
        [20260801, "x", str(raws[rid_c]), "USD", rid_c, "C"],
        [20260801, "x", str(raws[rid_d]), "USD", rid_d, "D"],
        [20260801, "", str(unattributed_raw), "USD", "", "Refund"],
    ]
    result = aggregate(rows)
    by_id = {r["resource_id"]: r["period_cost_usd"] for r in result.resources}
    parts_sum = sum(
        (Decimal(str(v)) for v in by_id.values()), Decimal("0")
    ) + Decimal(str(result.unattributed_usd))
    assert parts_sum == Decimal(str(result.total_usd))
    # Every reconciled bucket (resources + unattributed) must stay within
    # one cent of its own raw value — the allocation only ever moves a
    # bucket by at most one additional cent past its own independent
    # rounding, and independent HALF_UP rounding error is itself bounded by
    # half a cent, so the combined bound is <= 0.01 exactly.
    for rid, raw in raws.items():
        assert abs(Decimal(str(by_id[rid])) - raw) <= Decimal("0.01")
    assert abs(Decimal(str(result.unattributed_usd)) - unattributed_raw) <= Decimal(
        "0.01"
    )


def test_residual_distribution_is_independent_of_input_row_order() -> None:
    rid_a, rid_b, rid_c, rid_d = (RID + f"/{c}" for c in "abcd")
    rows = [
        [20260801, "x", 0.005, "USD", rid_a, "A"],
        [20260801, "x", 0.005, "USD", rid_b, "A"],
        [20260801, "x", -0.005, "USD", rid_c, "A"],
        [20260801, "", -0.005, "USD", "", "Refund"],
    ]
    forward = aggregate(rows)
    shuffled = aggregate(list(reversed(rows)))

    def _as_dict(result):
        return {
            **{r["resource_id"]: r["period_cost_usd"] for r in result.resources},
            "__unattributed__": result.unattributed_usd,
            "__total__": result.total_usd,
        }

    assert _as_dict(forward) == _as_dict(shuffled)


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
        "service_names": ["ACA"],
        "period_cost_usd": 10.0,
    }]


def test_conflicting_nonempty_resource_type_for_same_resource_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="resource_type"):
        aggregate([
            [20260801, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
            [20260802, "microsoft.storage/accounts", 5.0, "USD", RID, "ACA"],
        ])


# ---------------------------------------------------------------------------
# Multiple ServiceName values for one resource are a legitimate Cost
# Management shape (a workload resource also carries security//protection
# service cost lines), not a conflict. ResourceId stays the aggregation
# identity; resource_type conflicts stay fail-closed.
# ---------------------------------------------------------------------------


def test_multiple_service_names_for_same_resource_are_accepted_and_summed() -> None:
    result = aggregate([
        [20260801, "microsoft.storage/storageaccounts", 4.0, "USD", RID, "Storage"],
        [
            20260801,
            "microsoft.storage/storageaccounts",
            1.0,
            "USD",
            RID,
            "Microsoft Defender for Cloud",
        ],
    ])
    assert len(result.resources) == 1
    resource = result.resources[0]
    # ResourceId remains the aggregation identity: both cost dimensions
    # belong to the same resource and must land in the same total.
    assert resource["period_cost_usd"] == 5.0
    assert result.total_usd == 5.0
    assert resource["service_names"] == ["Microsoft Defender for Cloud", "Storage"]
    # Honest ambiguity: with more than one observed name there is no single
    # correct value, so the backward-compatible scalar is null rather than an
    # arbitrary first-observed pick.
    assert resource["service_name"] is None


def test_service_names_are_sorted_case_insensitively_and_deterministically() -> None:
    result = aggregate([
        [20260801, "x", 1.0, "USD", RID, "zeta service"],
        [20260801, "x", 1.0, "USD", RID, "Alpha Service"],
        [20260802, "x", 1.0, "USD", RID, "beta service"],
    ])
    assert result.resources[0]["service_names"] == [
        "Alpha Service",
        "beta service",
        "zeta service",
    ]


def test_single_service_name_still_populates_scalar_and_list() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
        [20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
    ])
    assert result.resources[0]["service_name"] == "ACA"
    assert result.resources[0]["service_names"] == ["ACA"]


def test_service_names_dedup_case_variants_keeping_first_display_casing() -> None:
    result = aggregate([
        [20260801, "x", 5.0, "USD", RID, "Storage"],
        [20260802, "x", 5.0, "USD", RID, "STORAGE"],
    ])
    # One logical service observed twice is still exactly one name, so the
    # scalar stays populated and the display casing is the first observed.
    assert result.resources[0]["service_names"] == ["Storage"]
    assert result.resources[0]["service_name"] == "Storage"


def test_no_observed_service_name_yields_empty_list_and_null_scalar() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 5.0, "USD", RID, ""],
        [20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "   "],
    ])
    assert result.resources[0]["service_names"] == []
    assert result.resources[0]["service_name"] is None


def test_blank_service_name_rows_do_not_pollute_the_name_list() -> None:
    result = aggregate([
        [20260801, "x", 5.0, "USD", RID, ""],
        [20260802, "x", 5.0, "USD", RID, "Storage"],
    ])
    # The blank row's cost is still counted; only the name is absent.
    assert result.resources[0]["period_cost_usd"] == 10.0
    assert result.resources[0]["service_names"] == ["Storage"]
    assert result.resources[0]["service_name"] == "Storage"


def test_multiple_service_names_do_not_mutate_the_input_pages() -> None:
    pages = [page([
        [20260801, "microsoft.storage/storageaccounts", 4.0, "USD", RID, "Storage"],
        [
            20260802,
            "microsoft.storage/storageaccounts",
            1.0,
            "USD",
            RID,
            "Microsoft Defender for Cloud",
        ],
    ])]
    before = deepcopy(pages)
    aggregate_cost_rows(pages, **WINDOW)
    assert pages == before


def test_conflicting_resource_type_is_still_rejected_with_multiple_services() -> None:
    # Accepting multiple service names must not weaken the resource-type
    # identity check: a single ResourceId reporting two types is still a
    # contract violation.
    with pytest.raises(ActualsEvidenceError, match="resource_type"):
        aggregate([
            [20260801, "microsoft.storage/storageaccounts", 4.0, "USD", RID, "Storage"],
            [
                20260802,
                "microsoft.app/containerapps",
                1.0,
                "USD",
                RID,
                "Microsoft Defender for Cloud",
            ],
        ])


def test_whitespace_only_resource_type_is_treated_as_blank_and_backfilled() -> None:
    # A cell containing only whitespace must be treated exactly like an
    # empty string for blank/backfill purposes — a bare truthiness check
    # would treat "   " as a real (if odd) observed value instead.
    result = aggregate([
        [20260801, "   ", 5.0, "USD", RID, "  "],
        [20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
    ])
    assert result.resources == [{
        "resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "service_name": "ACA",
        "service_names": ["ACA"],
        "period_cost_usd": 10.0,
    }]


def test_resource_type_differing_only_by_case_is_not_a_conflict() -> None:
    result = aggregate([
        [20260801, "Microsoft.App/ContainerApps", 5.0, "USD", RID, "ACA"],
        [20260802, "microsoft.app/containerapps", 5.0, "USD", RID, "ACA"],
    ])
    # First-observed display casing is retained, not overwritten by a
    # later row that is only a case variant of the same value.
    assert result.resources[0]["resource_type"] == "Microsoft.App/ContainerApps"


def test_service_name_differing_only_by_case_is_not_a_conflict() -> None:
    result = aggregate([
        [20260801, "x", 5.0, "USD", RID, "aca"],
        [20260802, "x", 5.0, "USD", RID, "ACA"],
    ])
    assert result.resources[0]["service_name"] == "aca"
    assert result.resources[0]["service_names"] == ["aca"]


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


def test_token_series_with_a_non_dict_element_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["token_series"] = [{"deployment": "gpt4o"}, "not-a-dict"]
    with pytest.raises(ActualsEvidenceError, match="token_series must be a list of dict"):
        build_actuals_manifest(**kwargs)


def test_empty_list_token_series_element_check_still_allows_empty_list() -> None:
    # An empty observed list is a genuinely observed zero (pass), not
    # subject to the per-element dict check simply because there are no
    # elements to check.
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["token_series"] = []
    manifest = build_actuals_manifest(**kwargs)
    assert manifest["usage"]["model_attribution_status"] == "pass"
    assert manifest["usage"]["models"] == []


def test_non_mapping_provenance_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["provenance"] = ["query_api_version", "2025-03-01"]
    with pytest.raises(ActualsEvidenceError, match="provenance must be a mapping"):
        build_actuals_manifest(**kwargs)


def test_non_list_warnings_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["warnings"] = "a single warning string, not a list"
    with pytest.raises(ActualsEvidenceError, match="warnings must be a list of str"):
        build_actuals_manifest(**kwargs)


def test_warnings_with_a_non_str_element_is_rejected() -> None:
    kwargs = _manifest_kwargs(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    kwargs["warnings"] = ["a fine warning", 42]
    with pytest.raises(ActualsEvidenceError, match="warnings must be a list of str"):
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


# ---------------------------------------------------------------------------
# build_success_kql: safe AppTraces KQL construction (RFC §8.2)
# ---------------------------------------------------------------------------


def test_success_kql_targets_the_workspace_table_not_app_insights() -> None:
    query = build_success_kql(
        "2026-08-01T00:00:00Z",
        "2026-08-08T00:00:00Z",
        "return_decision_completed",
        "decision.outcome",
        ["approved", "denied", "escalated"],
    )
    assert query.startswith("AppTraces\n")
    assert "TimeGenerated >= datetime(2026-08-01T00:00:00Z)" in query
    assert "TimeGenerated < datetime(2026-08-08T00:00:00Z)" in query
    assert 'Message == "return_decision_completed"' in query
    assert 'Properties["decision.outcome"]' in query
    assert '"approved", "denied", "escalated"' in query
    assert "union" not in query.casefold()
    # The App Insights classic surface (traces/timestamp/customDimensions)
    # must never leak into a workspace query.
    for forbidden in ("traces\n", "timestamp", "customDimensions"):
        assert forbidden not in query


def test_success_kql_rejects_arbitrary_fragment() -> None:
    with pytest.raises(ActualsEvidenceError, match="identifier"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            'event") | union AppRequests',
            "decision.outcome",
            ["approved"],
        )


def test_success_kql_rejects_unsafe_trace_attribute() -> None:
    with pytest.raises(ActualsEvidenceError, match="identifier"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            "return_decision_completed",
            'outcome"] | union AppRequests //',
            ["approved"],
        )


def test_success_kql_rejects_unsafe_success_value() -> None:
    with pytest.raises(ActualsEvidenceError, match="identifier"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            "return_decision_completed",
            "decision.outcome",
            ['approved") | union AppRequests //'],
        )


def test_success_kql_requires_non_empty_success_values() -> None:
    with pytest.raises(ActualsEvidenceError, match="success_values"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            "return_decision_completed",
            "decision.outcome",
            [],
        )


def test_success_kql_rejects_bare_str_success_values() -> None:
    # A bare `str` is iterable character-by-character and must never be
    # accepted where a `list[str]` of identifiers is required — silently
    # iterating "approved" as its individual characters would be a
    # confusing, wrong-shape failure far from this check.
    with pytest.raises(ActualsEvidenceError, match="success_values"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            "return_decision_completed",
            "decision.outcome",
            "approved",
        )


def test_success_kql_rejects_end_not_after_start() -> None:
    with pytest.raises(ActualsEvidenceError, match="end must be after start"):
        build_success_kql(
            "2026-08-08T00:00:00Z",
            "2026-08-01T00:00:00Z",
            "return_decision_completed",
            "decision.outcome",
            ["approved"],
        )


def test_success_kql_reserializes_parsed_timestamps_not_raw_text() -> None:
    # A non-canonical-but-valid ISO form is reparsed and reserialized to the
    # fixed `YYYY-MM-DDTHH:MM:SSZ` shape, never passed through verbatim.
    query = build_success_kql(
        "2026-08-01T00:00:00+00:00",
        "2026-08-08T00:00:00+00:00",
        "return_decision_completed",
        "decision.outcome",
        ["approved"],
    )
    assert "datetime(2026-08-01T00:00:00Z)" in query
    assert "datetime(2026-08-08T00:00:00Z)" in query
    assert "+00:00" not in query


def test_success_kql_rejects_naive_or_non_utc_timestamps() -> None:
    with pytest.raises(ActualsEvidenceError, match="start"):
        build_success_kql(
            "2026-08-01T00:00:00",
            "2026-08-08T00:00:00Z",
            "return_decision_completed",
            "decision.outcome",
            ["approved"],
        )
    with pytest.raises(ActualsEvidenceError, match="end"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T02:00:00+02:00",
            "return_decision_completed",
            "decision.outcome",
            ["approved"],
        )


def test_success_kql_rejects_fractional_start_rather_than_truncating() -> None:
    # `build_success_kql` renders `TimeGenerated` at second precision only
    # (`_iso_utc`'s `%Y-%m-%dT%H:%M:%SZ` has no `%f`). Silently truncating a
    # sub-second `start` would shift the query window's true lower bound
    # earlier than what the caller declared, so a fractional instant must be
    # rejected outright rather than rendered as if it were exact.
    with pytest.raises(ActualsEvidenceError, match="sub-second"):
        build_success_kql(
            "2026-08-01T00:00:00.500000Z",
            "2026-08-08T00:00:00Z",
            "return_decision_completed",
            "decision.outcome",
            ["approved"],
        )


def test_success_kql_rejects_fractional_end_rather_than_truncating() -> None:
    with pytest.raises(ActualsEvidenceError, match="sub-second"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00.123456Z",
            "return_decision_completed",
            "decision.outcome",
            ["approved"],
        )


def test_success_kql_rejects_fractional_window_that_would_collapse_to_empty() -> None:
    # `start` and `end` are a genuine `end > start` instant pair (900ms
    # apart) but would truncate to the *same* second
    # (`00:00:00Z`/`00:00:00Z`), silently collapsing an apparently
    # sub-second-wide window into a zero-width one. The sub-second check
    # must fire on `microsecond != 0` alone, before the `end > start`
    # comparison or any rendering is attempted, so this is rejected for
    # being fractional at all — never truncated into a false "same second"
    # window.
    with pytest.raises(ActualsEvidenceError, match="sub-second"):
        build_success_kql(
            "2026-08-01T00:00:00.100000Z",
            "2026-08-01T00:00:00.900000Z",
            "return_decision_completed",
            "decision.outcome",
            ["approved"],
        )


def test_success_kql_still_renders_exact_second_timestamps() -> None:
    # Whole-second instants (microsecond == 0) are unaffected by the new
    # sub-second guard and still render exactly as before.
    query = build_success_kql(
        "2026-08-01T00:00:00Z",
        "2026-08-08T00:00:00Z",
        "return_decision_completed",
        "decision.outcome",
        ["approved"],
    )
    assert "datetime(2026-08-01T00:00:00Z)" in query
    assert "datetime(2026-08-08T00:00:00Z)" in query


# ---------------------------------------------------------------------------
# parse_interaction_counts: safe Log Analytics tables/columns/rows parsing
# ---------------------------------------------------------------------------

LOG_ANALYTICS_RESPONSE = {
    "tables": [
        {
            "name": "PrimaryResult",
            "columns": [
                {"name": "total_interactions", "type": "long"},
                {"name": "successful_interactions", "type": "long"},
            ],
            "rows": [[120, 113]],
        }
    ]
}


def test_interaction_counts_parse_the_apptraces_response_shape() -> None:
    """`az monitor log-analytics query` returns tables/columns/rows, not a
    list of dicts. Pin the real shape so the parser is not written against
    an imagined one."""
    assert parse_interaction_counts(LOG_ANALYTICS_RESPONSE) == (120, 113)


def test_interaction_counts_map_columns_by_name_not_position() -> None:
    swapped = deepcopy(LOG_ANALYTICS_RESPONSE)
    swapped["tables"][0]["columns"].reverse()
    swapped["tables"][0]["rows"] = [[113, 120]]
    assert parse_interaction_counts(swapped) == (120, 113)


def test_interaction_counts_accept_stringified_longs() -> None:
    stringy = deepcopy(LOG_ANALYTICS_RESPONSE)
    stringy["tables"][0]["rows"] = [["120", "113"]]
    assert parse_interaction_counts(stringy) == (120, 113)


def test_interaction_counts_without_named_primary_result_uses_sole_table() -> None:
    unnamed = deepcopy(LOG_ANALYTICS_RESPONSE)
    del unnamed["tables"][0]["name"]
    assert parse_interaction_counts(unnamed) == (120, 113)


def test_interaction_counts_reject_duplicate_column_names() -> None:
    duplicated = deepcopy(LOG_ANALYTICS_RESPONSE)
    duplicated["tables"][0]["columns"].append(
        {"name": "Total_Interactions", "type": "long"}
    )
    duplicated["tables"][0]["rows"] = [[120, 113, 120]]
    with pytest.raises(ActualsEvidenceError, match="duplicate column"):
        parse_interaction_counts(duplicated)


def test_empty_result_set_is_zero_interactions_not_unverified() -> None:
    """An empty `summarize` result is a real observation of zero, distinct
    from never having run the query (which yields `None` counts and
    `interaction_status: not-verified` upstream)."""
    empty = deepcopy(LOG_ANALYTICS_RESPONSE)
    empty["tables"][0]["rows"] = []
    assert parse_interaction_counts(empty) == (0, 0)


def test_missing_expected_column_is_rejected() -> None:
    broken = deepcopy(LOG_ANALYTICS_RESPONSE)
    broken["tables"][0]["columns"] = [{"name": "total_interactions", "type": "long"}]
    broken["tables"][0]["rows"] = [[120]]
    with pytest.raises(ActualsEvidenceError, match="successful_interactions"):
        parse_interaction_counts(broken)


def test_multiple_ambiguous_tables_without_primary_result_are_rejected() -> None:
    ambiguous = {
        "tables": [
            {"name": "Table0", "columns": LOG_ANALYTICS_RESPONSE["tables"][0]["columns"],
             "rows": [[120, 113]]},
            {"name": "Table1", "columns": LOG_ANALYTICS_RESPONSE["tables"][0]["columns"],
             "rows": [[1, 1]]},
        ]
    }
    with pytest.raises(ActualsEvidenceError, match="multiple tables"):
        parse_interaction_counts(ambiguous)


def test_multiple_rows_are_rejected_as_malformed() -> None:
    malformed = deepcopy(LOG_ANALYTICS_RESPONSE)
    malformed["tables"][0]["rows"] = [[120, 113], [1, 1]]
    with pytest.raises(ActualsEvidenceError, match="exactly one row"):
        parse_interaction_counts(malformed)


def test_successful_exceeding_total_is_rejected() -> None:
    invalid = deepcopy(LOG_ANALYTICS_RESPONSE)
    invalid["tables"][0]["rows"] = [[10, 20]]
    with pytest.raises(ActualsEvidenceError, match="cannot exceed"):
        parse_interaction_counts(invalid)


@pytest.mark.parametrize("bad_value", [True, -1, "abc", 1.5, None])
def test_non_integer_or_negative_counts_are_rejected(bad_value) -> None:
    invalid = deepcopy(LOG_ANALYTICS_RESPONSE)
    invalid["tables"][0]["rows"] = [[bad_value, 0]]
    with pytest.raises(ActualsEvidenceError, match="total_interactions"):
        parse_interaction_counts(invalid)


def test_no_tables_at_all_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="no tables"):
        parse_interaction_counts({"tables": []})


def test_non_dict_response_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="not an object"):
        parse_interaction_counts(["not", "a", "dict"])
