"""
Parse observed Azure Cost Management `Usage` Query API evidence into a pure,
offline `threadlight-cost-actuals/v1` manifest — the raw-evidence half of the
design -> deploy cost-actuals reconciliation contract.

See `skills/threadlight-consumption-iq/references/cost-actuals-manifest-schema.md`
for the full, canonical field-by-field schema this module produces, and
`docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§7.2 (manifest contract), §8.1 (window/daily-granularity contract), and §9.7
(cost column selection) for the RFC this module implements.

## No Azure calls, ever

Every function in this module is a pure, offline parser: it consumes
`dict`/`list` shapes already returned by an Azure SDK/CLI call (or a
sanitized fixture standing in for one) and never performs I/O, network
access, or subprocess execution itself. That is what makes it exhaustively
unit-testable without live credentials, and it is the boundary Task 9's live
CLI adapter sits on top of.

## Fail closed, never silently drop or invent evidence

`ActualsEvidenceError` is raised — never swallowed into a default or a zero
— whenever the observed evidence disagrees with the declared request: a
missing cost column, an unparsable `UsageDate`, a row outside the declared
window, a mixed currency, a non-numeric cost value, or a `generated_at`
that precedes the window it is supposed to postdate. A response that
disagrees with the request it was born from is a contract violation, and
silently dropping the offending row (rather than raising) is exactly how an
off-by-one boundary bug would masquerade as a clean total. This mirrors
`value_model.py`'s fail-closed philosophy, but note the difference in
failure *shape*: `value_model.py` never raises on malformed *policy
content* (it accumulates every problem into `ValueModelResult.errors` and
still returns whatever did parse, because a half-filled SPEC section is a
valid design-time state). This module raises `ActualsEvidenceError`,
because a malformed *evidence* response is not a valid state to model as a
partial result — the total would be silently wrong if it did.

## Cost column selection is defensive compatibility, not a documented API

`COST_COLUMN_PRIORITY` accepts `PreTaxCost` (the Query API `2025-03-01`
`Usage` contract's official primary), then `CostUSD`, then `Cost`, matched
case-insensitively. This is defensive compatibility for responses
historically observed carrying an alias column, not a claim that any
particular account type actually returns them. Exactly one is ever used;
none present is an error, and the *original-cased* column name actually
consumed is recorded in the manifest as `cost.cost_column` so a reader can
tell `PreTaxCost` evidence from an alias without re-querying.

## `resource_id_coverage_pct` is source quality, not projection coverage

It measures how much of the *observed actual cost* carries a resource ID at
all — nothing about whether a forecast/projection modeled those resources.
That second, policy-gated number is `coverage.projection_attribution_coverage_pct`
in the separate reconciliation artifact (Task 8) and must never be
conflated with this one. Refunds (negative costs) make a naive
signed-total-based ratio potentially exceed [0, 1] or go negative, which
would misrepresent "quality" as an accounting artifact; both the numerator
(identified-row cost) and denominator (all cost) are therefore summed as
*absolute* Decimal values before dividing, keeping the ratio in [0, 1] by
construction. When gross absolute cost is exactly zero there is nothing to
measure a ratio against, so the result is `None` (serialized as JSON
`null`) rather than a division by zero or a fabricated 1.0/0.0.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import NamedTuple, Optional


class ActualsEvidenceError(RuntimeError):
    """Cost Management evidence does not parse or does not validate against
    the declared window/scope. Always raised, never swallowed."""


# Official v1 primary is `PreTaxCost`. `CostUSD` and `Cost` are accepted only
# as defensive compatibility with responses observed in the field; this is
# not a claim that any particular account type returns them. Order is
# priority order, matched case-insensitively (entries are already
# casefolded).
COST_COLUMN_PRIORITY = ("pretaxcost", "costusd", "cost")

_YYYYMMDD_RE = re.compile(r"\d{8}")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def select_cost_column(names: list[str]) -> str:
    """Return the casefolded name of the single cost column to use.

    `names` must already be casefolded. Exactly one column is used. If
    several of the accepted names are present, the highest-priority one
    wins and the chosen name is recorded in the manifest as
    `cost.cost_column` so a reader can tell which contract the numbers came
    from. If none is present this is an error, never a zero.
    """
    for candidate in COST_COLUMN_PRIORITY:
        if candidate in names:
            return candidate
    raise ActualsEvidenceError(
        "Cost Management response has no cost column "
        f"(expected one of {', '.join(COST_COLUMN_PRIORITY)})"
    )


def normalize_usage_date(value: object) -> date:
    """Normalize an observed `UsageDate` cell into a UTC calendar date.

    Accepts the integer/string `YYYYMMDD` form (`20260801` / `"20260801"`),
    an ISO date (`"2026-08-01"`), or an ISO datetime at UTC midnight
    (`"2026-08-01T00:00:00Z"`). Anything else — including `bool`, which is
    a `int` subclass in Python and must not be silently coerced — raises
    `ActualsEvidenceError("UsageDate is not a date: ...")`.
    """
    if isinstance(value, bool):
        raise ActualsEvidenceError(f"UsageDate is not a date: {value!r}")

    if isinstance(value, int):
        candidate = str(value)
        if _YYYYMMDD_RE.fullmatch(candidate):
            return _date_from_yyyymmdd(candidate, value)
        raise ActualsEvidenceError(f"UsageDate is not a date: {value!r}")

    if isinstance(value, str):
        candidate = value.strip()
        if _YYYYMMDD_RE.fullmatch(candidate):
            return _date_from_yyyymmdd(candidate, value)
        if _ISO_DATE_RE.fullmatch(candidate):
            try:
                return date.fromisoformat(candidate)
            except ValueError as exc:
                raise ActualsEvidenceError(
                    f"UsageDate is not a date: {value!r}"
                ) from exc
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ActualsEvidenceError(
                f"UsageDate is not a date: {value!r}"
            ) from exc
        return parsed.date()

    raise ActualsEvidenceError(f"UsageDate is not a date: {value!r}")


def _date_from_yyyymmdd(candidate: str, original: object) -> date:
    try:
        return date(int(candidate[0:4]), int(candidate[4:6]), int(candidate[6:8]))
    except ValueError as exc:
        raise ActualsEvidenceError(
            f"UsageDate is not a date: {original!r}"
        ) from exc


def _parse_page(
    page: dict[str, object],
) -> tuple[list[dict[str, object]], list[str], dict[str, str]]:
    """Validate one Query API page and return (rows, casefolded names,
    casefolded-name -> original-cased-name map). Columns are mapped by name,
    never position; malformed shapes fail closed."""
    if not isinstance(page, dict):
        raise ActualsEvidenceError("Cost Management response is not an object")
    props = page.get("properties")
    if not isinstance(props, dict):
        raise ActualsEvidenceError("Cost Management response has no properties")
    columns = props.get("columns")
    rows = props.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ActualsEvidenceError("Cost Management response has no columns/rows")
    if any(
        not isinstance(col, dict) or not isinstance(col.get("name"), str)
        for col in columns
    ):
        raise ActualsEvidenceError("Cost Management columns are malformed")

    names = [str(col["name"]).casefold() for col in columns]
    if len(names) != len(set(names)):
        seen: set[str] = set()
        dupes: set[str] = set()
        for name in names:
            (dupes if name in seen else seen).add(name)
        raise ActualsEvidenceError(
            "Cost Management columns contain duplicate column name(s): "
            f"{', '.join(sorted(dupes))}"
        )

    name_to_original = {
        str(col["name"]).casefold(): str(col["name"]) for col in columns
    }

    # Raises when no accepted cost column is present, and never trusts the
    # response's row shape until the column contract itself is sound.
    select_cost_column(names)
    if "usagedate" not in names:
        raise ActualsEvidenceError("UsageDate column missing")

    parsed_rows: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise ActualsEvidenceError("Cost Management row does not match columns")
        parsed_rows.append({names[index]: value for index, value in enumerate(row)})

    return parsed_rows, names, name_to_original


def rows_from_query_page(page: dict[str, object]) -> list[dict[str, object]]:
    """Map one Query API response page's `properties.columns`/`rows` into a
    list of dicts keyed by casefolded column name. Never assumes column
    position; malformed columns/rows raise `ActualsEvidenceError` rather
    than being dropped or reinterpreted."""
    rows, _names, _name_to_original = _parse_page(page)
    return rows


def _parse_cost_value(raw: object) -> Decimal:
    """Parse one cost cell into `Decimal`. Rejects `bool` (an `int`
    subclass), non-finite floats, and anything that does not represent a
    finite real number. Negative values (refunds) are accepted unchanged."""
    if isinstance(raw, bool):
        raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
        return Decimal(str(raw))
    if isinstance(raw, str):
        try:
            value = Decimal(raw.strip())
        except (InvalidOperation, ValueError) as exc:
            raise ActualsEvidenceError(
                f"cost value is not numeric: {raw!r}"
            ) from exc
        if not value.is_finite():
            raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
        return value
    raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ActualsEvidenceError(
            f"{label} must be a timezone-aware UTC datetime, got {value!r}"
        )
    if value.utcoffset() != timedelta(0):
        raise ActualsEvidenceError(
            f"{label} must be a timezone-aware UTC datetime, got {value!r}"
        )
    return value


class CostAggregate(NamedTuple):
    resources: list[dict[str, object]]
    total_usd: float
    currency: Optional[str]
    unattributed_usd: float
    cost_column: str  # original-cased name actually used
    usage_dates: set  # distinct in-window `date`s observed
    resource_id_coverage_pct: Optional[float]


def aggregate_cost_rows(
    pages: list[dict[str, object]],
    *,
    start: datetime,
    end: datetime,
) -> CostAggregate:
    """Aggregate paged Query API rows and validate the daily window.

    Raises `ActualsEvidenceError` when `start`/`end` are not timezone-aware
    UTC datetimes, when `end <= start`, when any page's columns/rows are
    malformed, when any row's `UsageDate` is unparseable, when a row falls
    outside `start.date() <= usage_date < end.date()`, when rows report
    more than one currency, or when pages disagree on which cost column to
    use. Out-of-window rows are never silently dropped: a response that
    disagrees with the request is a contract violation, and dropping rows
    would quietly understate the period total.

    Resource IDs are normalized with `.casefold().rstrip("/")` for grouping
    only; the original, first-observed ID string is retained for reporting.
    A blank resource ID's cost remains in `total_usd` and is reported under
    `unattributed_usd`, never dropped.
    """
    start = _require_utc(start, "start")
    end = _require_utc(end, "end")
    if end <= start:
        raise ActualsEvidenceError(
            f"window end must be after start (start={start.isoformat()}, "
            f"end={end.isoformat()})"
        )
    window_start_date = start.date()
    window_end_date = end.date()

    cost_key: Optional[str] = None
    cost_original: Optional[str] = None
    currency: Optional[str] = None
    usage_dates: set[date] = set()
    # casefolded/rstripped resource key -> accumulator
    resource_totals: dict[str, dict[str, object]] = {}
    unattributed = Decimal("0")
    gross_abs_total = Decimal("0")
    identified_abs_total = Decimal("0")

    for page in pages:
        rows, names, name_to_original = _parse_page(page)
        page_cost_key = select_cost_column(names)
        if cost_key is None:
            cost_key = page_cost_key
            cost_original = name_to_original[page_cost_key]
        elif page_cost_key != cost_key:
            raise ActualsEvidenceError(
                "Cost Management pages disagree on cost column: "
                f"{cost_original!r} vs {name_to_original[page_cost_key]!r}"
            )

        for row in rows:
            usage_date = normalize_usage_date(row["usagedate"])
            if not (window_start_date <= usage_date < window_end_date):
                raise ActualsEvidenceError(
                    f"UsageDate {usage_date.isoformat()} is outside the "
                    f"requested window [{window_start_date.isoformat()}, "
                    f"{window_end_date.isoformat()})"
                )
            usage_dates.add(usage_date)

            raw_currency = row.get("currency")
            if not isinstance(raw_currency, str) or not raw_currency.strip():
                raise ActualsEvidenceError("Cost Management row is missing Currency")
            normalized_currency = raw_currency.strip()
            if currency is None:
                currency = normalized_currency
            elif normalized_currency.casefold() != currency.casefold():
                raise ActualsEvidenceError(
                    "Cost Management rows report multiple currencies: "
                    f"{currency!r}, {normalized_currency!r}"
                )

            cost_value = _parse_cost_value(row[cost_key])
            abs_cost = abs(cost_value)
            gross_abs_total += abs_cost

            raw_resource_id = row.get("resourceid")
            resource_type = row.get("resourcetype")
            service_name = row.get("servicename")

            resource_key = None
            if isinstance(raw_resource_id, str) and raw_resource_id.strip():
                resource_key = raw_resource_id.casefold().rstrip("/")

            if resource_key:
                identified_abs_total += abs_cost
                bucket = resource_totals.setdefault(
                    resource_key,
                    {
                        "resource_id": raw_resource_id,
                        "resource_type": resource_type
                        if isinstance(resource_type, str)
                        else "",
                        "service_name": service_name
                        if isinstance(service_name, str)
                        else "",
                        "cost": Decimal("0"),
                    },
                )
                bucket["cost"] += cost_value
            else:
                unattributed += cost_value

    resources = [
        {
            "resource_id": bucket["resource_id"],
            "resource_type": bucket["resource_type"],
            "service_name": bucket["service_name"],
            "period_cost_usd": float(bucket["cost"]),
        }
        for bucket in resource_totals.values()
    ]
    total = sum((bucket["cost"] for bucket in resource_totals.values()), Decimal("0"))
    total += unattributed

    coverage: Optional[float] = None
    if gross_abs_total > 0:
        coverage = float(identified_abs_total / gross_abs_total)

    return CostAggregate(
        resources=resources,
        total_usd=float(total),
        currency=currency,
        unattributed_usd=float(unattributed),
        cost_column=cost_original or "",
        usage_dates=usage_dates,
        resource_id_coverage_pct=coverage,
    )


def build_actuals_manifest(
    *,
    scope: dict[str, object],
    start: datetime,
    end: datetime,
    generated_at: datetime,
    cost_pages: list[dict[str, object]],
    token_series: Optional[list[dict[str, object]]],
    interaction_counts: Optional[tuple[int, int]],
    provenance: dict[str, object],
    warnings: list[str],
) -> dict[str, object]:
    """Build `threadlight-cost-actuals/v1` without issuing live calls.

    Raises `ActualsEvidenceError` when `scope` is not a non-empty mapping,
    when `start`/`end`/`generated_at` are not timezone-aware UTC datetimes,
    when `generated_at` precedes `end` (a negative settlement age is
    evidence of a caller bug, never silently clamped to zero), when
    `interaction_counts` is present but not a `(total, successful)` pair of
    non-negative ints, or when any `cost_pages` evidence fails to parse or
    validate against the window (see `aggregate_cost_rows`).

    Top-level `status` is `pass` whenever this function returns at all: it
    is produced by the Cost Management source, scope, and window alone
    (RFC §7.2), all of which must already have parsed/validated by the time
    a manifest dict is built. Missing `token_series` / `interaction_counts`
    evidence never demotes it — each carries its own independent
    `usage.model_attribution_status` / `usage.interaction_status`, `pass`
    only when that evidence was actually collected (including a genuinely
    observed zero, which is `pass`, not `not-verified`).
    """
    if not isinstance(scope, dict) or not scope:
        raise ActualsEvidenceError("scope must be a non-empty mapping")

    start = _require_utc(start, "start")
    end = _require_utc(end, "end")
    generated_at = _require_utc(generated_at, "generated_at")

    aggregate = aggregate_cost_rows(cost_pages, start=start, end=end)

    if generated_at < end:
        raise ActualsEvidenceError(
            "generated_at must not be before window end (generated_at="
            f"{generated_at.isoformat()}, end={end.isoformat()})"
        )

    complete_days = (end - start).days
    settlement_delta = generated_at - end
    settlement_age_hours = int(settlement_delta.total_seconds() // 3600)
    window_end_age_days = settlement_delta.days

    if interaction_counts is None:
        interaction_status = "not-verified"
        total_interactions: Optional[int] = None
        successful_interactions: Optional[int] = None
    else:
        if (
            not isinstance(interaction_counts, (tuple, list))
            or len(interaction_counts) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in interaction_counts
            )
        ):
            raise ActualsEvidenceError(
                "interaction_counts must be a (total, successful) pair of "
                f"non-negative ints, got {interaction_counts!r}"
            )
        total_interactions, successful_interactions = interaction_counts
        if successful_interactions > total_interactions:
            raise ActualsEvidenceError(
                "interaction_counts: successful_interactions cannot exceed "
                "total_interactions"
            )
        interaction_status = "pass"

    if token_series is None:
        model_attribution_status = "not-verified"
        models: list[dict[str, object]] = []
    else:
        if not isinstance(token_series, list):
            raise ActualsEvidenceError("token_series must be a list or None")
        model_attribution_status = "pass"
        models = deepcopy(token_series)

    resources_sorted = sorted(
        aggregate.resources,
        key=lambda r: str(r["resource_id"]).casefold().rstrip("/"),
    )

    return {
        "schema": "threadlight-cost-actuals/v1",
        "generated_at": _iso_utc(generated_at),
        "status": "pass",
        "scope": deepcopy(scope),
        "window": {
            "start": _iso_utc(start),
            "end": _iso_utc(end),
            "complete_days": complete_days,
            "settlement_age_hours": settlement_age_hours,
            "window_end_age_days": window_end_age_days,
        },
        "cost": {
            "basis": "usage-pretax",
            "cost_column": aggregate.cost_column,
            "currency": aggregate.currency,
            "period_total_usd": round(aggregate.total_usd, 2),
            "resources": [
                {**resource, "period_cost_usd": round(resource["period_cost_usd"], 2)}
                for resource in resources_sorted
            ],
            "unattributed_usd": round(aggregate.unattributed_usd, 2),
            "resource_id_coverage_pct": aggregate.resource_id_coverage_pct,
        },
        "usage": {
            "interaction_status": interaction_status,
            "model_attribution_status": model_attribution_status,
            "total_interactions": total_interactions,
            "successful_interactions": successful_interactions,
            "success_predicate_ref": "SPEC.md#section-14-value-model",
            "models": models,
        },
        "provenance": deepcopy(provenance),
        "warnings": deepcopy(warnings),
    }


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
