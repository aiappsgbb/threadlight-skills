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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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

# `re.ASCII` is required, not cosmetic: without it Python's `\d` also
# matches non-ASCII decimal digits (e.g. Arabic-Indic, fullwidth), and
# `int()`/`date.fromisoformat()` both happily parse those too, so a
# YYYYMMDD/ISO-date `UsageDate` cell carrying Unicode digits would
# otherwise be silently accepted as if it were the plain-ASCII form.
_YYYYMMDD_RE = re.compile(r"\d{8}", re.ASCII)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def select_cost_column(names: list[str]) -> str:
    """Return the casefolded name of the single cost column to use.

    `names` may carry any original casing — every entry is casefolded
    internally before matching, so a caller never needs to pre-casefold
    its own list first (this module's own `_parse_page` already does that,
    but that is an implementation detail, not a contract this public
    function should require of every caller). The *return value* is always
    the casefolded key (e.g. `"pretaxcost"`), never the original casing;
    the original-cased name actually observed is recorded separately, at
    the call site, as `cost.cost_column` in the manifest. Exactly one
    column is used. If several of the accepted names are present, the
    highest-priority one wins. If none is present this is an error, never
    a zero.
    """
    casefolded_names = [name.casefold() for name in names]
    for candidate in COST_COLUMN_PRIORITY:
        if candidate in casefolded_names:
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
        # A datetime `UsageDate` cell must denote an unambiguous UTC
        # calendar day: naive (no offset) or non-UTC-offset datetimes are
        # rejected outright rather than reinterpreted as a "local-date
        # bucket" (silently treating a wall-clock time in some other zone
        # as if it were the UTC date is exactly the off-by-one that would
        # masquerade as a clean total), and a non-midnight UTC time is
        # rejected rather than truncated to a date, because Cost
        # Management's `Usage` Query API only ever reports whole UTC days
        # — a non-midnight timestamp is evidence of a caller/response bug,
        # not a sub-day observation to bucket.
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ActualsEvidenceError(
                f"UsageDate is not a date: {value!r} (datetime must be UTC, "
                "offset zero)"
            )
        if (parsed.hour, parsed.minute, parsed.second, parsed.microsecond) != (
            0,
            0,
            0,
            0,
        ):
            raise ActualsEvidenceError(
                f"UsageDate is not a date: {value!r} (datetime must be "
                "exact UTC midnight)"
            )
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
    """Require a timezone-aware UTC instant. Does not require midnight —
    `generated_at` is a real point-in-time timestamp (a pipeline can finish
    at any minute) and must never be forced onto a calendar-day boundary.
    Window boundaries additionally require midnight; see
    `_require_utc_midnight`."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ActualsEvidenceError(
            f"{label} must be a timezone-aware UTC datetime, got {value!r}"
        )
    if value.utcoffset() != timedelta(0):
        raise ActualsEvidenceError(
            f"{label} must be a timezone-aware UTC datetime, got {value!r}"
        )
    return value


def _require_utc_midnight(value: object, label: str) -> datetime:
    """Require a timezone-aware UTC instant that also lands exactly on a
    UTC calendar-day boundary (`00:00:00.000000`). `start`/`end` gate the
    daily-granularity Cost Management window (RFC §8.1): a non-midnight
    boundary would make `complete_days = (end - start).days` silently
    truncate a fractional day rather than reporting an exact whole-day
    count, so it is rejected outright rather than accepted and rounded."""
    value = _require_utc(value, label)
    if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
        raise ActualsEvidenceError(
            f"{label} must be exact UTC midnight (00:00:00), got "
            f"{value.isoformat()}"
        )
    return value


_CENT = Decimal("0.01")


def _quantize_usd(value: Decimal) -> Decimal:
    """Round one USD amount to the cent with `ROUND_HALF_UP` (ties away
    from zero) — the accounting-standard rounding, and never Python's
    `round()` on a `float`. `round()` operates on the *binary* float
    already parsed from the caller's number, which frequently is not the
    exact decimal the caller intended (`float(2.675)` is actually
    `2.67499999999999982236...`), so `round(2.675, 2) == 2.67` even though
    the intended value is a true half-cent tie that must round up to
    `2.68`. Every caller of this function must pass a `Decimal` built from
    the caller's original string/exact representation (as
    `_parse_cost_value` already does), never a `Decimal` re-derived from an
    already-rounded `float`.
    """
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _reconcile_quantized_costs(
    resource_totals: dict[str, dict[str, object]],
    unattributed_raw: Decimal,
) -> tuple[dict[str, Decimal], Decimal, Decimal]:
    """Quantize every resource bucket and `unattributed` to the cent, and
    guarantee the *displayed* parts sum exactly to the *displayed* total —
    the accounting identity `period_total_usd == sum(period_cost_usd) +
    unattributed_usd` must hold exactly at the cent after serialization,
    with no cent ever dropped or fabricated.

    The grand total is quantized once, directly from the exact (unrounded)
    Decimal sum of every raw part — never by re-summing already-rounded
    parts, which would compound rounding error across many rows. Each part
    is *also* independently quantized, because each part is itself a
    displayed manifest field. Independently-rounded parts are not
    guaranteed to sum to the independently-rounded total: three rows of
    exactly `0.005` sum to a raw `0.015` (which quantizes to `0.02`), but
    each `0.005` part quantizes to `0.01` on its own (summing those parts
    gives `0.03`). Whenever the two disagree, the (always-exact, since
    every value here is already cent-quantized) residual is added to the
    largest-absolute-value resource bucket — ties are broken
    deterministically by the same `.casefold().rstrip("/")` resource-key
    ordering the manifest already sorts `cost.resources[]` by (the
    lexically smallest normalized key wins), so the adjustment target is
    reproducible run to run and documented, not an arbitrary
    dict-iteration artifact. `unattributed` only ever absorbs the residual
    when there are no resource buckets at all — in that case
    `unattributed` is the *only* remaining part, so it is already trivially
    equal to the total and this branch never actually changes a value; it
    exists purely so the policy is total and well-defined (e.g. for a
    future multi-bucket `unattributed`), not because it is reachable
    today.
    """
    raw_total = (
        sum((bucket["cost"] for bucket in resource_totals.values()), Decimal("0"))
        + unattributed_raw
    )
    quantized_total = _quantize_usd(raw_total)

    quantized_resources = {
        key: _quantize_usd(bucket["cost"]) for key, bucket in resource_totals.items()
    }
    quantized_unattributed = _quantize_usd(unattributed_raw)

    residual = quantized_total - (
        sum(quantized_resources.values(), Decimal("0")) + quantized_unattributed
    )

    if residual != 0:
        if quantized_resources:
            target_key = max(
                sorted(quantized_resources),
                key=lambda key: abs(quantized_resources[key]),
            )
            quantized_resources[target_key] += residual
        else:
            quantized_unattributed += residual

    return quantized_resources, quantized_unattributed, quantized_total


class CostAggregate(NamedTuple):
    resources: list[dict[str, object]]
    total_usd: float
    currency: Optional[str]
    unattributed_usd: float
    cost_column: str  # original-cased name actually used
    usage_dates: set[date]  # distinct in-window `date`s observed
    resource_id_coverage_pct: Optional[float]


def aggregate_cost_rows(
    pages: list[dict[str, object]],
    *,
    start: datetime,
    end: datetime,
) -> CostAggregate:
    """Aggregate paged Query API rows and validate the daily window.

    Raises `ActualsEvidenceError` when `pages` is empty (there is no
    evidence to aggregate at all — a caller that could not fetch even one
    page must not silently report a zero-cost period; see below for the
    *different*, allowed case of a present page with zero rows), when
    `start`/`end` are not timezone-aware UTC datetimes at exact UTC
    midnight, when `end <= start`, when any page's columns/rows are
    malformed, when any row's `UsageDate` is unparseable or not a valid
    UTC-midnight date, when a row falls outside
    `start.date() <= usage_date < end.date()`, when rows report a
    non-`USD` currency or more than one currency (v1 is USD-only — see
    `cost-actuals-manifest-schema.md`), or when pages disagree on which
    cost column to use. Out-of-window rows are never silently dropped: a
    response that disagrees with the request is a contract violation, and
    dropping rows would quietly understate the period total.

    A *present* page with valid columns and zero rows is different from no
    pages at all: it is accepted as a genuinely observed zero for that
    page. Because currency is only ever learned from an actual row, a
    zero-row response never observes a currency at all — `currency` stays
    `None` rather than being fabricated as `"USD"` just because that is
    the only value v1 accepts.

    Resource IDs are normalized with `.casefold().rstrip("/")` for grouping
    only; the original, first-observed ID string is retained for reporting.
    A blank resource ID's cost remains in `total_usd` and is reported under
    `unattributed_usd`, never dropped. A blank `resource_type`/
    `service_name` on the first-seen row for a resource is backfilled from
    a later row for the *same* resource that does carry a value; two rows
    for the same resource that carry *conflicting* non-blank values is a
    contract violation (raised), never silently first-wins.

    Every USD amount returned (`total_usd`, `unattributed_usd`, and each
    `resources[].period_cost_usd`) is already rounded to the cent with
    `decimal.ROUND_HALF_UP` and reconciled so the parts sum exactly to the
    total — see `_reconcile_quantized_costs` for the residual-allocation
    policy. Python's `round()` on a `float` is never used for money: it
    operates on an already-imprecise binary float, not true decimal
    half-up (e.g. `round(2.675, 2) == 2.67`, not the correct `2.68`).
    """
    if not pages:
        raise ActualsEvidenceError(
            "Cost Management evidence has no Cost Management pages at all "
            "(0 pages) — this is not the same as a page with zero rows, "
            "which is a genuinely observed zero and is accepted"
        )

    start = _require_utc_midnight(start, "start")
    end = _require_utc_midnight(end, "end")
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
                if normalized_currency.casefold() != "usd":
                    raise ActualsEvidenceError(
                        "Cost Management currency must be USD — schema "
                        "threadlight-cost-actuals/v1 is USD-only, got "
                        f"{normalized_currency!r}"
                    )
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
            observed_resource_type = (
                resource_type if isinstance(resource_type, str) else ""
            )
            observed_service_name = (
                service_name if isinstance(service_name, str) else ""
            )

            resource_key = None
            if isinstance(raw_resource_id, str) and raw_resource_id.strip():
                resource_key = raw_resource_id.casefold().rstrip("/")

            if resource_key:
                identified_abs_total += abs_cost
                bucket = resource_totals.setdefault(
                    resource_key,
                    {
                        "resource_id": raw_resource_id,
                        "resource_type": observed_resource_type,
                        "service_name": observed_service_name,
                        "cost": Decimal("0"),
                    },
                )
                bucket["cost"] += cost_value
                for field, observed in (
                    ("resource_type", observed_resource_type),
                    ("service_name", observed_service_name),
                ):
                    if not observed:
                        continue
                    existing = bucket[field]
                    if not existing:
                        # Backfill: an earlier row for this same resource
                        # carried a blank value; a later row observed a
                        # real one.
                        bucket[field] = observed
                    elif existing != observed:
                        raise ActualsEvidenceError(
                            f"Cost Management rows disagree on {field} for "
                            f"resource {raw_resource_id!r}: {existing!r} vs "
                            f"{observed!r}"
                        )
            else:
                unattributed += cost_value

    quantized_resource_costs, quantized_unattributed, quantized_total = (
        _reconcile_quantized_costs(resource_totals, unattributed)
    )
    resources = [
        {
            "resource_id": bucket["resource_id"],
            "resource_type": bucket["resource_type"],
            "service_name": bucket["service_name"],
            "period_cost_usd": float(quantized_resource_costs[key]),
        }
        for key, bucket in resource_totals.items()
    ]

    coverage: Optional[float] = None
    if gross_abs_total > 0:
        coverage = float(identified_abs_total / gross_abs_total)

    return CostAggregate(
        resources=resources,
        total_usd=float(quantized_total),
        currency=currency,
        unattributed_usd=float(quantized_unattributed),
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
            # Already cent-quantized with Decimal `ROUND_HALF_UP` and
            # residual-reconciled in `aggregate_cost_rows` /
            # `_reconcile_quantized_costs` so the parts sum exactly to the
            # total. Re-rounding a float here with Python's `round()` is
            # exactly the banker's-rounding-on-binary-floats bug this
            # module must avoid, so these values are passed through
            # untouched.
            "period_total_usd": aggregate.total_usd,
            "resources": resources_sorted,
            "unattributed_usd": aggregate.unattributed_usd,
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
