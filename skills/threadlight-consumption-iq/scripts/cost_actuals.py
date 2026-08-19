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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, getcontext
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

# `decimal.Decimal(str)` is far more permissive than a Cost Management cost
# cell should ever need: it silently accepts underscore digit separators
# (`Decimal("1_000.50")` parses as `1000.50`, a readability feature meant for
# Python source literals, not external data) and, without `re.ASCII`, `\d`
# would also match non-ASCII decimal digits (Arabic-Indic, fullwidth, ...)
# that `Decimal()` happily parses too. Both are silent-acceptance hazards a
# malformed or adversarial response cell could exploit to sneak a
# non-plain-ASCII numeric syntax past validation. Every string cost cell is
# matched against this strict sign/digits/decimal-point/exponent ASCII-only
# grammar *before* `Decimal()` ever sees it; anything else (underscores,
# non-ASCII digits, "Infinity"/"NaN" spellings, empty strings) is rejected
# as `"cost value is not numeric"` rather than silently coerced.
_ASCII_MONEY_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII
)


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


def _reject_pathological_cost_magnitude(value: Decimal, raw: object) -> None:
    """Guard against a `Decimal` whose exponent is so large that *any*
    further arithmetic on it — even a sign-only `abs()`, let alone the
    `+=` accumulation `aggregate_cost_rows` does per row — raises a raw
    `decimal.Overflow` (an `ArithmeticError` subclass) instead of the
    `ActualsEvidenceError` this module's callers were promised.

    `Decimal("1e300")` is deliberately *not* rejected here: it is a
    plausible (if absurd) money string, parses fine, and only becomes
    unrepresentable once `_quantize_usd` tries to round it to the cent
    (~302 significant digits, far past the default 28-digit context
    precision) — that failure is reported there, with its own
    "not representable at 2 decimal places" message. This guard instead
    catches the much more extreme case where the value's exponent
    (`value.adjusted()`) is close enough to the ambient context's `Emax`
    that the context itself cannot represent the value at all: a quoted
    `"1E+1000000"` cell or a `10**1000000`-magnitude Python `int` land
    here, both with an adjusted exponent within a few dozen of `Emax`
    (999999 by default), and `abs()`/`+` on such a `Decimal` overflow
    immediately — before `_quantize_usd`, and often before the value even
    reaches an accumulator. The margin below (`prec + 16`) is generous
    headroom for the exponent creep that repeated addition of many rows
    or the two extra digits of cent-quantization can introduce, so a
    value that clears this check is guaranteed safe for every arithmetic
    operation this module performs on it, not merely for construction.
    """
    ctx = getcontext()
    if value.adjusted() > ctx.Emax - (ctx.prec + 16):
        # Never format `raw` itself here: for a pathological `int` (e.g.
        # `10**1000000`) or an equally huge string, `repr(raw)` requires
        # rendering millions of digits, which for a Python `int` raises its
        # own `ValueError` (`int_max_str_digits`) before this
        # `ActualsEvidenceError` can even be constructed — trading one
        # leaking exception for another. Describe the magnitude instead,
        # via the already-bounded `Decimal.adjusted()` exponent.
        raise ActualsEvidenceError(
            "cost value magnitude is too large to process safely: "
            f"a {type(raw).__name__} with order of magnitude 1E+{value.adjusted()}"
        )


def _parse_cost_value(raw: object) -> Decimal:
    """Parse one cost cell into `Decimal`. Rejects `bool` (an `int`
    subclass), non-finite floats, and anything that does not represent a
    finite real number. Negative values (refunds) are accepted unchanged.

    String cells are validated against `_ASCII_MONEY_RE` (plain ASCII
    sign/digits/decimal-point/exponent only) *before* `Decimal()` ever sees
    them, rejecting underscore digit separators and non-ASCII digit
    syntaxes that `Decimal()` would otherwise silently accept. This does
    not reject `1e300`-style values with a huge exponent — those are
    syntactically valid money strings and are only unrepresentable once
    quantized to the cent; see `_quantize_usd` for that failure mode. A
    far more extreme magnitude (an exponent near the ambient `Decimal`
    context's `Emax`, e.g. `"1E+1000000"` or a `10**1000000`-sized `int`)
    is rejected here instead, via `_reject_pathological_cost_magnitude`,
    because such a value overflows on *any* arithmetic — including the
    plain `abs()` and `+=` accumulation callers perform on the value this
    function returns — not merely at cent-quantization time.
    """
    if isinstance(raw, bool):
        raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
    if isinstance(raw, int):
        value = Decimal(raw)
        _reject_pathological_cost_magnitude(value, raw)
        return value
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
        value = Decimal(str(raw))
        _reject_pathological_cost_magnitude(value, raw)
        return value
    if isinstance(raw, str):
        candidate = raw.strip()
        if not _ASCII_MONEY_RE.fullmatch(candidate):
            raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
        try:
            value = Decimal(candidate)
        except (InvalidOperation, ValueError) as exc:
            raise ActualsEvidenceError(
                f"cost value is not numeric: {raw!r}"
            ) from exc
        if not value.is_finite():
            raise ActualsEvidenceError(f"cost value is not numeric: {raw!r}")
        _reject_pathological_cost_magnitude(value, raw)
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

# Sentinel bucket key for the unattributed total in the largest-remainder
# candidate list. This never collides with a real, normalized
# `.casefold().rstrip("/")` resource key — not because
# `aggregate_cost_rows`'s blank/whitespace check on the *raw* resource ID
# guarantees a non-empty normalized key (it doesn't: a resource ID of only
# slashes, e.g. `"///"`, passes that whitespace check but still normalizes
# to `""` via `.rstrip("/")`), but because `aggregate_cost_rows` only ever
# inserts a key into `resource_totals` when the normalized key is truthy
# (`if resource_key:`); a resource ID that normalizes to `""` is instead
# folded into `unattributed`, so `resource_totals` itself can never contain
# this sentinel as a real key.
_UNATTRIBUTED_KEY = ""


def _quantize_usd(value: Decimal, *, context: str) -> Decimal:
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

    `Decimal.quantize` consults the *ambient decimal context* (default
    precision 28 significant digits), not just the two target fractional
    digits: a syntactically valid cost cell such as `"1e300"` parses fine
    (the `Decimal()` constructor never applies context) but needs roughly
    302 significant digits once quantized to whole cents, which raises
    `decimal.InvalidOperation` — a value simply too large to represent at
    cent precision, not a caller bug in the usual "bad input" sense, but
    still an evidence problem that must fail closed rather than let a raw
    `decimal` exception escape past this module's `ActualsEvidenceError`
    contract. `context` names *which* amount was being rounded (a specific
    resource, "unattributed total", or "period total") so the error is
    actionable rather than a bare traceback.

    A quantized result that is exactly zero but carries a negative sign
    (`Decimal("-0.00")`, e.g. from a raw `-0.001`) is normalized to a plain
    `0.00` — a signed-zero cent amount is not a meaningful distinct value
    and must never surface as `-0.0` in a serialized manifest.
    """
    try:
        quantized = value.quantize(_CENT, rounding=ROUND_HALF_UP)
    except ArithmeticError as exc:
        raise ActualsEvidenceError(
            f"cost value could not be rounded to the cent ({context}): "
            f"{value!r} is not representable at 2 decimal places under the "
            f"current decimal context ({exc})"
        ) from exc
    if quantized.is_zero():
        quantized = abs(quantized)
    return quantized


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
    gives `0.03`).

    Whenever the two disagree, the (always-exact, since every value here is
    already cent-quantized) residual is distributed one cent at a time
    across *every* attributed resource bucket plus the `unattributed`
    bucket — the classic largest-remainder method, not a single arbitrary
    bucket absorbing the whole residual:

    * Rank every bucket by its own signed remainder
      `raw - rounded` (how far its independent HALF_UP rounding is from the
      exact value it rounded away from).
    * A positive residual (parts under-sum the total) hands out `+0.01` to
      the buckets with the *largest* remainder first (the ones rounding
      *down* the most relative to their raw value) until the residual is
      exhausted.
    * A negative residual (parts over-sum the total) takes back `-0.01`
      from the buckets with the *smallest* (most negative) remainder first
      (the ones rounding *up* the most).
    * Ties are broken deterministically by the normalized
      `.casefold().rstrip("/")` resource key, ascending — the same
      ordering `cost.resources[]` is already sorted by — so the choice is
      reproducible run to run and independent of input row order.
      `unattributed` always loses ties against a real resource bucket (it
      is adjusted last), since a resource-level breakdown is the more
      informative place for a visible one-cent nudge when the choice is
      otherwise arbitrary.

    Because a correctly HALF_UP-rounded value is always within half a cent
    of its raw value, and this method only ever moves a given bucket by one
    additional cent, every bucket's final displayed error against its own
    raw value stays bounded at `<= 0.01`, and a bucket whose raw value was
    unambiguously non-negative is never nudged into a negative displayed
    value by allocation alone (it can only move from `0.00` up to `0.01`,
    never down past zero) — sign flips are a property of the *raw* value,
    not an artifact this method introduces.

    Distributing `|residual|` cents one at a time requires at least that
    many buckets (resources + `unattributed`) to exist; violating that
    would mean the exact-vs-independently-rounded gap exceeded what
    largest-remainder distribution can represent, an internal invariant
    this function was designed to always satisfy — if it is ever broken,
    that is a contract violation and is raised as `ActualsEvidenceError`
    rather than silently truncated or dropped.
    """
    try:
        raw_total = (
            sum((bucket["cost"] for bucket in resource_totals.values()), Decimal("0"))
            + unattributed_raw
        )
    except ArithmeticError as exc:
        raise ActualsEvidenceError(
            f"cost values could not be summed to a period total: {exc}"
        ) from exc

    quantized_total = _quantize_usd(raw_total, context="period total")

    raw_by_key: dict[str, Decimal] = {
        key: bucket["cost"] for key, bucket in resource_totals.items()
    }
    quantized_resources = {
        key: _quantize_usd(
            raw, context=f"resource {resource_totals[key]['resource_id']!r}"
        )
        for key, raw in raw_by_key.items()
    }
    quantized_unattributed = _quantize_usd(unattributed_raw, context="unattributed total")

    try:
        residual = quantized_total - (
            sum(quantized_resources.values(), Decimal("0")) + quantized_unattributed
        )
        residual_cents = int(residual / _CENT)
    except ArithmeticError as exc:
        raise ActualsEvidenceError(
            f"cost rounding residual could not be computed: {exc}"
        ) from exc

    if residual_cents != 0:
        # (remainder, is_unattributed, normalized_key) per candidate
        # bucket. `is_unattributed` sorts real resources (False == 0)
        # ahead of `unattributed` (True == 1) in *every* tie, in both the
        # ascending and descending orderings below.
        candidates: list[tuple[Decimal, bool, str]] = [
            (raw_by_key[key] - quantized_resources[key], False, key)
            for key in raw_by_key
        ]
        candidates.append(
            (unattributed_raw - quantized_unattributed, True, _UNATTRIBUTED_KEY)
        )

        if abs(residual_cents) > len(candidates):
            raise ActualsEvidenceError(
                f"cost rounding residual of {residual_cents} cent(s) cannot "
                f"be distributed at one cent per bucket across "
                f"{len(candidates)} bucket(s) — quantization invariant "
                "violated"
            )

        if residual_cents > 0:
            # Largest remainder first: the most under-rounded bucket
            # (raw - rounded largest) gets the +0.01 first.
            ordered = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))
            adjustment = _CENT
        else:
            # Mirror image: the most over-rounded bucket
            # (raw - rounded smallest/most negative) gives back -0.01 first.
            ordered = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))
            adjustment = -_CENT

        for _remainder, is_unattributed, key in ordered[: abs(residual_cents)]:
            if is_unattributed:
                quantized_unattributed += adjustment
            else:
                quantized_resources[key] += adjustment

        if quantized_unattributed.is_zero():
            quantized_unattributed = abs(quantized_unattributed)
        for key, value in quantized_resources.items():
            if value.is_zero():
                quantized_resources[key] = abs(value)

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
            # Whitespace-only strings are blank, exactly like the resource
            # ID blank check below (`.strip()`, never a bare truthiness
            # check) — a cell containing `"   "` must backfill the same as
            # an empty string, not be treated as a real observed value.
            observed_resource_type = (
                resource_type.strip() if isinstance(resource_type, str) else ""
            )
            observed_service_name = (
                service_name.strip() if isinstance(service_name, str) else ""
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
                    elif existing.casefold() != observed.casefold():
                        raise ActualsEvidenceError(
                            f"Cost Management rows disagree on {field} for "
                            f"resource {raw_resource_id!r}: {existing!r} vs "
                            f"{observed!r}"
                        )
                    # else: same value up to case — keep the first-observed
                    # (or backfilled) display casing; a later row that only
                    # differs by case is not a conflict and must never
                    # overwrite it.
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


# ---------------------------------------------------------------------------
# Log Analytics interaction-count evidence (RFC §8.2)
#
# The transport (Task 9) is `az monitor log-analytics query --workspace
# <customerId>` — the *Log Analytics workspace* query surface. That surface's
# table/column names (`AppTraces`, `TimeGenerated`, `Message`, `Properties`)
# are fixed and must never be confused with the differently-named
# *Application Insights* classic surface (`traces`, `timestamp`, `message`,
# `customDimensions`, reached only via `az monitor app-insights query` or the
# App Insights resource-scoped API): those names simply do not resolve
# against a workspace and the query fails. Any future App Insights transport
# would be a new, explicitly named adapter, never a silent column rename
# here.
# ---------------------------------------------------------------------------

# Deliberately conservative: a leading letter, then up to 127 more
# letters/digits/`_`/`.`/`:`/`-`. This is not a general KQL/identifier
# grammar — it is the narrow allowlist every value interpolated into
# `build_success_kql` must satisfy, so no dynamic content (quotes,
# pipes, whitespace, `|`, parens) can ever inject an arbitrary KQL
# fragment into the fixed query shape below.
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")

_SUCCESS_KQL_TEMPLATE = (
    "AppTraces\n"
    "| where TimeGenerated >= datetime({start})\n"
    "    and TimeGenerated < datetime({end})\n"
    '| where Message == "{event_name}"\n'
    '| extend outcome = tostring(Properties["{trace_attribute}"])\n'
    "| summarize total_interactions=count(),\n"
    "            successful_interactions=countif(\n"
    "                outcome in ({success_values})\n"
    "            )"
)


def _validate_identifier(value: object, label: str) -> str:
    """Return `value` unchanged if it is a `str` matching `IDENTIFIER`;
    raise `ActualsEvidenceError` naming the offending value otherwise. This
    is the sole gate standing between a caller-declared SPEC identifier
    (event name, trace attribute, success value) and the fixed
    `_SUCCESS_KQL_TEMPLATE` — nothing that fails this check is ever
    interpolated into a query string."""
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ActualsEvidenceError(
            f"{label} is not a safe identifier for a Log Analytics query: "
            f"{value!r}"
        )
    return value


def _parse_iso_instant(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ActualsEvidenceError(
            f"{label} must be an ISO 8601 UTC datetime string, got {value!r}"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActualsEvidenceError(
            f"{label} is not a parsable ISO 8601 datetime: {value!r}"
        ) from exc
    return _require_utc(parsed, label)


def build_success_kql(
    start_iso: str,
    end_iso: str,
    event_name: str,
    trace_attribute: str,
    success_values: list[str],
) -> str:
    """Build the fixed `AppTraces` success-count KQL query (RFC §8.2).

    `start_iso`/`end_iso` are parsed as timezone-aware UTC instants (any
    valid ISO 8601 form, including a trailing `Z`) and *reserialized* from
    the parsed `datetime` — the caller's original text is never passed
    through verbatim into the query. `end_iso` must denote an instant after
    `start_iso`.

    `event_name`, `trace_attribute`, and every entry of `success_values`
    must match `IDENTIFIER`; anything else raises `ActualsEvidenceError`
    naming the offending value, and the value is never interpolated into
    the query. This is a fixed-shape query builder, not general KQL
    construction: there is no way to reach the `union`/pipe/quote syntax
    that would let a value escape the `AppTraces`/`Message`/`Properties`
    shape below.
    """
    start = _parse_iso_instant(start_iso, "start")
    end = _parse_iso_instant(end_iso, "end")
    if end <= start:
        raise ActualsEvidenceError(
            f"end must be after start (start={start_iso!r}, end={end_iso!r})"
        )

    event_name = _validate_identifier(event_name, "event_name")
    trace_attribute = _validate_identifier(trace_attribute, "trace_attribute")

    if not isinstance(success_values, list) or not success_values:
        raise ActualsEvidenceError(
            "success_values must be a non-empty list of identifiers"
        )
    validated_values = [
        _validate_identifier(value, "success_values entry")
        for value in success_values
    ]

    return _SUCCESS_KQL_TEMPLATE.format(
        start=_iso_utc(start),
        end=_iso_utc(end),
        event_name=event_name,
        trace_attribute=trace_attribute,
        success_values=", ".join(f'"{value}"' for value in validated_values),
    )


def _parse_interaction_count(value: object, label: str) -> int:
    """Accept a Log Analytics `long` cell as either a real `int` or a
    stringified ASCII integer (`az monitor log-analytics query` can
    serialize `long` columns as JSON strings). `bool` (an `int` subclass in
    Python), negative values, and anything else non-integral are rejected
    rather than silently coerced or truncated."""
    if isinstance(value, bool):
        raise ActualsEvidenceError(
            f"{label} must be a non-negative integer, got {value!r}"
        )
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip(), re.ASCII):
        candidate = int(value.strip())
    else:
        raise ActualsEvidenceError(
            f"{label} must be a non-negative integer, got {value!r}"
        )
    if candidate < 0:
        raise ActualsEvidenceError(
            f"{label} must be a non-negative integer, got {value!r}"
        )
    return candidate


def parse_interaction_counts(doc: object) -> tuple[int, int]:
    """Parse `az monitor log-analytics query`'s `tables[].columns[]/rows[]`
    response shape (never a list of dicts) into `(total_interactions,
    successful_interactions)`.

    Columns are mapped by casefolded *name*, never position — a response
    with `total_interactions`/`successful_interactions` swapped in column
    order still parses correctly. When several tables are present, the one
    named `PrimaryResult` (case-insensitive) is used; with exactly one table
    and no such name it is used directly; anything more ambiguous than that
    (multiple tables, none/several named `PrimaryResult`) raises rather than
    guessing.

    A well-formed *empty* result set (`rows == []`) is a real, observed zero
    and returns `(0, 0)` — this is different from never having run the
    query at all, which the caller represents as `interaction_counts=None`
    upstream (`not-verified`, `null` counts). Any other row count (more than
    one row) is ambiguous for a single `summarize` result and is rejected.
    """
    if not isinstance(doc, dict):
        raise ActualsEvidenceError("Log Analytics response is not an object")

    tables = doc.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ActualsEvidenceError("Log Analytics response has no tables")
    if any(not isinstance(table, dict) for table in tables):
        raise ActualsEvidenceError("Log Analytics tables are malformed")

    named_primary = [
        table
        for table in tables
        if isinstance(table.get("name"), str)
        and table["name"].casefold() == "primaryresult"
    ]
    if named_primary:
        if len(named_primary) > 1:
            raise ActualsEvidenceError(
                "Log Analytics response has more than one PrimaryResult table"
            )
        primary = named_primary[0]
    elif len(tables) == 1:
        primary = tables[0]
    else:
        raise ActualsEvidenceError(
            "Log Analytics response has multiple tables and none is named "
            "PrimaryResult"
        )

    columns = primary.get("columns")
    rows = primary.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ActualsEvidenceError(
            "Log Analytics table has no columns/rows"
        )
    if any(
        not isinstance(col, dict) or not isinstance(col.get("name"), str)
        for col in columns
    ):
        raise ActualsEvidenceError("Log Analytics columns are malformed")

    names = [str(col["name"]).casefold() for col in columns]
    if len(names) != len(set(names)):
        raise ActualsEvidenceError(
            "Log Analytics columns contain duplicate column name(s)"
        )

    for required in ("total_interactions", "successful_interactions"):
        if required not in names:
            raise ActualsEvidenceError(
                f"Log Analytics response is missing expected column: "
                f"{required}"
            )

    if not rows:
        return (0, 0)
    if len(rows) != 1:
        raise ActualsEvidenceError(
            "Log Analytics response must summarize to exactly one row, got "
            f"{len(rows)}"
        )

    row = rows[0]
    if not isinstance(row, list) or len(row) != len(names):
        raise ActualsEvidenceError("Log Analytics row does not match columns")
    row_map = {names[index]: value for index, value in enumerate(row)}

    total = _parse_interaction_count(
        row_map["total_interactions"], "total_interactions"
    )
    successful = _parse_interaction_count(
        row_map["successful_interactions"], "successful_interactions"
    )
    if successful > total:
        raise ActualsEvidenceError(
            "successful_interactions cannot exceed total_interactions "
            f"(successful={successful}, total={total})"
        )
    return (total, successful)


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
    when `provenance` is not a mapping, when `warnings` is not a list of
    `str`, when `start`/`end`/`generated_at` are not timezone-aware UTC
    datetimes, when `generated_at` precedes `end` (a negative settlement
    age is evidence of a caller bug, never silently clamped to zero), when
    `interaction_counts` is present but not a `(total, successful)` pair of
    non-negative ints, when `token_series` is present but not a list of
    `dict`, or when any `cost_pages` evidence fails to parse or validate
    against the window (see `aggregate_cost_rows`).

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

    if not isinstance(provenance, dict):
        raise ActualsEvidenceError("provenance must be a mapping")

    if not isinstance(warnings, list) or any(
        not isinstance(warning, str) for warning in warnings
    ):
        raise ActualsEvidenceError("warnings must be a list of str")

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
        if not isinstance(token_series, list) or any(
            not isinstance(row, dict) for row in token_series
        ):
            raise ActualsEvidenceError(
                "token_series must be a list of dict, or None"
            )
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
