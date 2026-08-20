"""
Join an existing forecast (`specs/cost-manifest.json`), observed actuals
(`threadlight-cost-actuals/v1`), and SPEC §14's `value_model` policy into a
pure, offline `threadlight-cost-reconciliation/v1` manifest.

See `skills/threadlight-consumption-iq/references/cost-reconciliation-manifest-schema.md`
for the field-by-field schema this module emits, and
`docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§7.3 / §9 / §10 for the RFC it implements.

## The Cost Management total is the only actual total

`totals.actual_window_usd` is copied from `actuals.cost.period_total_usd`
and from nowhere else (RFC §9.1). Azure Monitor token reprice is
*attribution* evidence: it explains where spend went, it is never spend
itself. `reconcile_costs` therefore has no parameter through which a token
cost could be passed in at all — the type boundary, not a code review
convention, is what prevents double counting. A token reprice that shows up
inside `actuals.usage.models` rows is read for volume diagnostics only and
never enters a money total.

## Evidence is always emitted; only malformed evidence raises

An incomplete or invalid SPEC §14 policy, an absent interaction count, an
absent token series, or an unmatched resource are all valid *observed
states*: each degrades a specific named status to `not-verified` while every
number that WAS observed is still reported (RFC §10, §12). None of them
raises, and none of them suppresses the manifest.

`ReconciliationInputError` is reserved for evidence that is structurally
broken — a non-mapping document, a resource list that is not a list, money
that is not a finite number or is too large to represent at cent precision,
a negative interaction count, a `complete_days` that is not a positive
integer, a `generated_at` that is not canonical UTC ISO-8601. Those are
producer/caller bugs that would otherwise silently corrupt a money total, so
they fail closed and loudly. Validation is narrow and explicit at each
field; there is no broad `except Exception` anywhere in this module.

## A threshold-gated verdict is only as good as its SPEC anchor

Every status that measures evidence against a SPEC §14 threshold —
`maturity`'s `policy_complete` check, `unit_economics.status` and
`target_status`, `variance_status`, and `drivers.payg_ptu.status` — is
gated by ONE fact, `_policy_is_complete`: the required leaves are present
and valid, the parser reported no errors, AND `policy_ref.spec_sha256` is a
64-character digest a consumer can re-derive from the SPEC bytes. A
threshold nobody can trace back to a published SPEC revision is not a
declared threshold, so it may not produce a `pass` or a `should-fix`
anywhere in the artifact. The observed totals, deltas and cost-per-
interaction inputs that needed no policy to measure are still reported.

## Money is `Decimal`, and the two coverage measures are different numbers

Every USD amount is computed with `decimal.Decimal` and rounded to the cent
with `ROUND_HALF_UP` at serialization only — never Python's `round()` on a
binary float. Cost-per-interaction is a *rate*, not a ledger amount, so it
keeps four decimal places; ratios keep six.

`coverage.source_resource_id_coverage_pct` (copied through from the actuals
manifest) and `coverage.projection_attribution_coverage_pct` (computed here)
answer different questions and only the latter is gated. See the schema
reference for the full rationale.

## No I/O, ever

Every function here is a pure transformation of `dict`s the caller already
loaded. This module performs no network access, no process execution, and
no file access; Task 9's live adapter is the only component allowed to talk
to Azure.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional

# Single source of truth for "which §14 leaves must be present" and "which
# price bases exist" — imported from the parser that defines them rather
# than re-listed here, so the completeness check can never drift from the
# schema it is checking.
from value_model import PRICE_BASES, REQUIRED_PATHS

SCHEMA = "threadlight-cost-reconciliation/v1"

# Canonical artifact locations (RFC §7.3). These are recorded in the emitted
# manifest's `*_ref.path` fields so a consumer can re-derive and re-check
# every hash from the repository itself.
FORECAST_PATH = "specs/cost-manifest.json"
ACTUALS_PATH = "specs/cost-actuals-manifest.json"
POLICY_PATH = "specs/SPEC.md"
POLICY_SECTION = 14

# The PAYG/PTU driver measures TOKEN VOLUME drift, never cost drift, so it
# is deliberately gated by its own SPEC-declared tolerance. Recording the
# field name in the artifact makes it auditable that
# `max_forecast_variance_pct` (the cost tolerance) was not reused here.
TOKEN_VARIANCE_THRESHOLD_FIELD = "max_token_volume_variance_pct"

# Every maturity check this module declares, in emission order (RFC §10).
# Fail-closed: the overall verdict is `pass` only when all of them pass, and
# a check whose evidence is absent is `not-verified`, never skipped.
MATURITY_CHECK_IDS: tuple[str, ...] = (
    "policy_complete",
    "actuals_status",
    "complete_days",
    "successful_interactions",
    "cost_settlement_age_hours",
    "window_end_age_days",
    "projection_attribution_coverage",
    "cost_accounting_basis",
    "price_basis_compatible",
)

PASS = "pass"
NOT_VERIFIED = "not-verified"
SHOULD_FIX = "should-fix"

COST_BASIS_LITERAL = "usage-pretax"

_MONTH_DAYS = Decimal(30)
_CENT = Decimal("0.01")
_RATE = Decimal("0.0001")
_RATIO = Decimal("0.000001")

_ISO_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)
_ISO_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Plain ASCII money grammar. `Decimal()` itself accepts underscore digit
# grouping and non-ASCII decimal digits; neither belongs in a serialized
# manifest, so string cells are gated before `Decimal` ever sees them.
_ASCII_MONEY_RE = re.compile(
    r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII
)

# `.../providers/microsoft.cognitiveservices/accounts/<account>/deployments/<deployment>`
# — matched against the already-normalized (casefolded, trailing-slash
# stripped) resource ID. Anchored on the full string so only a real
# deployment leaf rolls up, and the account segment is captured so roll-up
# stays strictly account-name-scoped (RFC §9.4).
_AOAI_DEPLOYMENT_RE = re.compile(
    r"^(?P<account>.*/providers/microsoft\.cognitiveservices/accounts/[^/]+)"
    r"/deployments/(?P<deployment>[^/]+)$",
    re.ASCII,
)
# The account form of the same ID. Matching it is what PROVES a resource ID's
# normalized type is Azure OpenAI; an ID that matches neither pattern is some
# other provider's resource and must never be described as an AOAI account.
_AOAI_ACCOUNT_RE = re.compile(
    r"^.*/providers/microsoft\.cognitiveservices/accounts/[^/]+$", re.ASCII
)
_AOAI_ACCOUNT_TYPE = "microsoft.cognitiveservices/accounts"

_PTU_TIERS = frozenset({"payg", "ptu"})

# A SPEC audit anchor is a SHA-256 digest and nothing else: 64 hex characters.
# Case is accepted in either direction because `hexdigest()` spellings differ
# between producers, but a placeholder or truncated string is not an anchor a
# consumer can re-derive from the SPEC bytes.
_SHA256_HEX_RE = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)


class ReconciliationInputError(RuntimeError):
    """Structurally broken evidence, never a policy or coverage shortfall."""


# ---------------------------------------------------------------------------
# Canonical hashing
# ---------------------------------------------------------------------------


def sha256_json(document: dict[str, object]) -> str:
    """SHA-256 of one document's canonical JSON serialization.

    Canonical means `sort_keys=True`, `separators=(",", ":")` and
    `ensure_ascii=True`: key insertion order, incidental whitespace, and the
    host's preferred text encoding all stop being able to change the hash of
    semantically identical evidence. No field is excluded as "volatile" —
    the whole document is hashed, so any edit at all (including a pure
    re-projection that only changed prices) invalidates a reconciliation
    that pinned the old hash. That invalidation is intentional and cheap to
    resolve: `reconcile` re-runs offline over the already-collected actuals.
    """
    if not isinstance(document, dict):
        raise ReconciliationInputError(
            f"sha256_json expects a mapping, got {type(document).__name__}"
        )
    try:
        payload = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReconciliationInputError(
            f"document is not JSON-serializable: {exc}"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Narrow field validation
# ---------------------------------------------------------------------------


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReconciliationInputError(
            f"{label} must be a mapping, got {type(value).__name__}"
        )
    return value


def _section(container: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    """Return a nested mapping, or `{}` when absent.

    An ABSENT section is missing evidence (handled downstream as
    `not-verified`); a section that is present but is not a mapping is a
    malformed document and raises.
    """
    value = container.get(key)
    if value is None:
        return {}
    return _require_mapping(value, label)


def _parse_money(raw: object, label: str) -> Decimal:
    """Parse one USD cell into `Decimal`. Rejects `bool` (an `int` subclass),
    non-finite floats, and anything that is not a finite real number.
    Negative values (refunds) are preserved unchanged, never clipped."""
    if isinstance(raw, bool):
        raise ReconciliationInputError(f"{label} is not a finite number: {raw!r}")
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise ReconciliationInputError(f"{label} is not a finite number: {raw!r}")
        # `str()` first: `Decimal(0.025)` captures the binary float's full
        # error tail, `Decimal(str(0.025))` captures the decimal the
        # producer serialized.
        return Decimal(str(raw))
    if isinstance(raw, str):
        candidate = raw.strip()
        if not _ASCII_MONEY_RE.fullmatch(candidate):
            raise ReconciliationInputError(f"{label} is not a finite number: {raw!r}")
        try:
            value = Decimal(candidate)
        except InvalidOperation as exc:
            raise ReconciliationInputError(
                f"{label} is not a finite number: {raw!r}"
            ) from exc
        if not value.is_finite():
            raise ReconciliationInputError(f"{label} is not a finite number: {raw!r}")
        return value
    raise ReconciliationInputError(f"{label} is not a finite number: {raw!r}")


def _optional_money(
    container: dict[str, Any], key: str, label: str
) -> Optional[Decimal]:
    raw = container.get(key)
    if raw is None:
        return None
    return _parse_money(raw, label)


def _optional_int(
    container: dict[str, Any], key: str, label: str, *, minimum: int
) -> Optional[int]:
    raw = container.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ReconciliationInputError(
            f"{label} must be an integer, got {raw!r}"
        )
    if raw < minimum:
        raise ReconciliationInputError(
            f"{label} must be >= {minimum}, got {raw!r}"
        )
    return raw


def _optional_unit_ratio(
    container: dict[str, Any], key: str, label: str
) -> Optional[float]:
    raw = container.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ReconciliationInputError(f"{label} must be a number, got {raw!r}")
    if not math.isfinite(float(raw)) or not 0.0 <= float(raw) <= 1.0:
        raise ReconciliationInputError(
            f"{label} must be between 0 and 1, got {raw!r}"
        )
    return float(raw)


def _optional_str(container: dict[str, Any], key: str, label: str) -> Optional[str]:
    raw = container.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ReconciliationInputError(f"{label} must be a string, got {raw!r}")
    return raw


def _require_token_count(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ReconciliationInputError(
            f"{label} must be a non-negative integer, got {raw!r}"
        )
    if raw < 0:
        raise ReconciliationInputError(
            f"{label} must be a non-negative integer, got {raw!r}"
        )
    return raw


def _require_generated_at(value: object) -> str:
    """Require exactly one canonical serialization: `YYYY-MM-DDTHH:MM:SSZ`.

    `+00:00` is the same instant but a different string, and this value is
    hashed and compared byte-for-byte by consumers, so one spelling is
    pinned rather than several accepted and normalized.
    """
    if not isinstance(value, str) or not _ISO_UTC_RE.fullmatch(value):
        raise ReconciliationInputError(
            "generated_at must be a UTC ISO-8601 instant of the form "
            f"YYYY-MM-DDTHH:MM:SSZ, got {value!r}"
        )
    try:
        datetime.strptime(value, _ISO_UTC_FORMAT)
    except ValueError as exc:
        raise ReconciliationInputError(
            f"generated_at is not a valid UTC instant: {value!r} ({exc})"
        ) from exc
    return value


def _require_ref_path(value: object, label: str) -> str:
    """Require a provenance path a consumer can actually open.

    A blank or non-string path would publish a `*_ref.path` that names
    nothing, which is worse than the canonical default: the digest beside it
    would look re-derivable while pointing at no artifact at all. `Path`
    objects are refused too — the manifest is JSON, and the caller decides
    how its own paths are spelled (`str(path)`).
    """
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationInputError(
            f"{label} must be a non-empty string naming the artifact this "
            f"document was read from, got {value!r}"
        )
    return value


def _require_error_list(value: object) -> list[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ReconciliationInputError(
            f"policy_errors must be a list of str, got {type(value).__name__}"
        )
    for entry in value:
        if not isinstance(entry, str):
            raise ReconciliationInputError(
                f"policy_errors must be a list of str, got entry {entry!r}"
            )
    return list(value)


def _require_list_of_mappings(
    container: dict[str, Any], key: str, label: str
) -> list[dict[str, Any]]:
    raw = container.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ReconciliationInputError(
            f"{label} must be a list, got {type(raw).__name__}"
        )
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ReconciliationInputError(
                f"{label}[{index}] must be a mapping, got {type(entry).__name__}"
            )
    return list(raw)


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


def _quantize(value: Decimal, exponent: Decimal, *, context: str) -> Decimal:
    """Round with `ROUND_HALF_UP` (ties away from zero) — the accounting
    standard, and never Python's `round()`, which does banker's rounding on
    an already-imprecise binary float (`round(2.675, 2) == 2.67`).

    A value too large to represent at the target precision under the ambient
    decimal context raises `ReconciliationInputError` naming which amount
    failed, rather than letting a bare `decimal` exception escape.
    """
    try:
        quantized = value.quantize(exponent, rounding=ROUND_HALF_UP)
    except ArithmeticError as exc:
        raise ReconciliationInputError(
            f"{context} could not be rounded: {value!r} is not representable "
            f"at this precision under the current decimal context ({exc})"
        ) from exc
    if quantized.is_zero():
        # `-0.00` is not a meaningful distinct amount and must never be
        # serialized as `-0.0`.
        quantized = abs(quantized)
    return quantized


def _usd(value: Optional[Decimal], *, context: str) -> Optional[float]:
    if value is None:
        return None
    return float(_quantize(value, _CENT, context=context))


def _rate(value: Optional[Decimal], *, context: str) -> Optional[float]:
    if value is None:
        return None
    return float(_quantize(value, _RATE, context=context))


def _ratio(value: Optional[Decimal], *, context: str) -> Optional[float]:
    if value is None:
        return None
    return float(_quantize(value, _RATIO, context=context))


# ---------------------------------------------------------------------------
# Policy reading — never raises, always fails closed
# ---------------------------------------------------------------------------


def _policy_leaf(policy: dict[str, Any], path: str) -> object:
    """Resolve one dotted `cost.*` path, or `None` if any hop is absent or
    is not a mapping. Policy content is never a hard error here (RFC §12)."""
    node: object = policy
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _validated_int(
    raw: object, path: str, *, minimum: int, warnings: Optional[list[str]]
) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        _warn(warnings, f"policy: {path} is present but invalid ({raw!r}); ignored")
        return None
    return raw


def _validated_float(
    raw: object,
    path: str,
    *,
    low: float,
    high: Optional[float],
    low_exclusive: bool,
    warnings: Optional[list[str]],
) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        _warn(warnings, f"policy: {path} is present but invalid ({raw!r}); ignored")
        return None
    value = float(raw)
    too_low = value <= low if low_exclusive else value < low
    too_high = high is not None and value > high
    if not math.isfinite(value) or too_low or too_high:
        _warn(warnings, f"policy: {path} is present but invalid ({raw!r}); ignored")
        return None
    return value


def _validated_enum(
    raw: object, path: str, allowed: Iterable[str], *, warnings: Optional[list[str]]
) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in set(allowed):
        _warn(warnings, f"policy: {path} is present but invalid ({raw!r}); ignored")
        return None
    return raw


def _validated_bool(
    raw: object, path: str, *, warnings: Optional[list[str]]
) -> Optional[bool]:
    if raw is None:
        return None
    if not isinstance(raw, bool):
        _warn(warnings, f"policy: {path} is present but invalid ({raw!r}); ignored")
        return None
    return raw


def _warn(warnings: Optional[list[str]], message: str) -> None:
    if warnings is not None and message not in warnings:
        warnings.append(message)


def _policy_snapshot(
    policy: dict[str, Any], warnings: Optional[list[str]] = None
) -> dict[str, object]:
    """Every numeric threshold and accounting basis this reconciliation used.

    Each key is ALWAYS present; a value is `None` when the policy leaf is
    absent OR present-but-invalid (an invalid value is additionally
    warned about and then treated exactly like a missing one — a threshold
    that did not validate must never silently gate a verdict).

    The snapshot exists so a historical verdict stays auditable after a later
    SPEC revision changes the thresholds it was rendered against.
    """
    get = lambda path: _policy_leaf(policy, path)  # noqa: E731
    return {
        "min_complete_days": _validated_int(
            get("cost.maturity_policy.min_complete_days"),
            "cost.maturity_policy.min_complete_days",
            minimum=1,
            warnings=warnings,
        ),
        "min_successful_interactions": _validated_int(
            get("cost.maturity_policy.min_successful_interactions"),
            "cost.maturity_policy.min_successful_interactions",
            minimum=1,
            warnings=warnings,
        ),
        "min_cost_settlement_age_hours": _validated_int(
            get("cost.maturity_policy.min_cost_settlement_age_hours"),
            "cost.maturity_policy.min_cost_settlement_age_hours",
            minimum=0,
            warnings=warnings,
        ),
        "max_window_end_age_days": _validated_int(
            get("cost.maturity_policy.max_window_end_age_days"),
            "cost.maturity_policy.max_window_end_age_days",
            minimum=1,
            warnings=warnings,
        ),
        "min_projection_attribution_coverage_pct": _validated_float(
            get("cost.maturity_policy.min_projection_attribution_coverage_pct"),
            "cost.maturity_policy.min_projection_attribution_coverage_pct",
            low=0.0,
            high=1.0,
            low_exclusive=True,
            warnings=warnings,
        ),
        "target_cost_per_successful_interaction_usd": _validated_float(
            get("cost.baseline.target_cost_per_successful_interaction_usd"),
            "cost.baseline.target_cost_per_successful_interaction_usd",
            low=0.0,
            high=None,
            low_exclusive=True,
            warnings=warnings,
        ),
        "max_forecast_variance_pct": _validated_float(
            get("cost.baseline.max_forecast_variance_pct"),
            "cost.baseline.max_forecast_variance_pct",
            low=0.0,
            high=1.0,
            low_exclusive=False,
            warnings=warnings,
        ),
        "max_token_volume_variance_pct": _validated_float(
            get(f"cost.baseline.{TOKEN_VARIANCE_THRESHOLD_FIELD}"),
            f"cost.baseline.{TOKEN_VARIANCE_THRESHOLD_FIELD}",
            low=0.0,
            high=1.0,
            low_exclusive=False,
            warnings=warnings,
        ),
        "actual_cost_basis": _validated_enum(
            get("cost.accounting.actual_cost_basis"),
            "cost.accounting.actual_cost_basis",
            (COST_BASIS_LITERAL,),
            warnings=warnings,
        ),
        "actual_billing_price_basis": _validated_enum(
            get("cost.accounting.actual_billing_price_basis"),
            "cost.accounting.actual_billing_price_basis",
            PRICE_BASES,
            warnings=warnings,
        ),
        "forecast_price_basis": _validated_enum(
            get("cost.accounting.forecast_price_basis"),
            "cost.accounting.forecast_price_basis",
            tuple(basis for basis in PRICE_BASES if basis != "unknown"),
            warnings=warnings,
        ),
        "allow_basis_mismatch_for_verdict": _validated_bool(
            get("cost.accounting.allow_basis_mismatch_for_verdict"),
            "cost.accounting.allow_basis_mismatch_for_verdict",
            warnings=warnings,
        ),
        "scope_policy": _validated_enum(
            get("cost.accounting.scope_policy"),
            "cost.accounting.scope_policy",
            ("dedicated_resource_group", "tagged_allocation"),
            warnings=warnings,
        ),
    }


# Which snapshot key each SPEC leaf path validates into. Paths absent from
# this map (the `success_event` group) are checked for PRESENCE only —
# `value_model.py` already validated their content, and this module never
# builds a query from them.
_SNAPSHOT_KEY_BY_PATH = {
    "cost.maturity_policy.min_complete_days": "min_complete_days",
    "cost.maturity_policy.min_successful_interactions": "min_successful_interactions",
    "cost.maturity_policy.min_cost_settlement_age_hours": (
        "min_cost_settlement_age_hours"
    ),
    "cost.maturity_policy.max_window_end_age_days": "max_window_end_age_days",
    "cost.maturity_policy.min_projection_attribution_coverage_pct": (
        "min_projection_attribution_coverage_pct"
    ),
    "cost.baseline.target_cost_per_successful_interaction_usd": (
        "target_cost_per_successful_interaction_usd"
    ),
    "cost.baseline.max_forecast_variance_pct": "max_forecast_variance_pct",
    f"cost.baseline.{TOKEN_VARIANCE_THRESHOLD_FIELD}": (
        TOKEN_VARIANCE_THRESHOLD_FIELD
    ),
    "cost.accounting.actual_cost_basis": "actual_cost_basis",
    "cost.accounting.actual_billing_price_basis": "actual_billing_price_basis",
    "cost.accounting.forecast_price_basis": "forecast_price_basis",
    "cost.accounting.allow_basis_mismatch_for_verdict": (
        "allow_basis_mismatch_for_verdict"
    ),
    "cost.accounting.scope_policy": "scope_policy",
}


def _missing_policy_paths(
    policy: dict[str, Any], snapshot: dict[str, object]
) -> list[str]:
    """Every `REQUIRED_PATHS` leaf that is absent or did not validate.

    An invalid value counts as missing on purpose: a threshold that could not
    be validated is not a threshold, and treating it as merely "present"
    would let a malformed policy gate a verdict.
    """
    missing = []
    for path in REQUIRED_PATHS:
        key = _SNAPSHOT_KEY_BY_PATH.get(path)
        if key is None:
            if _policy_leaf(policy, path) is None:
                missing.append(path)
        elif snapshot[key] is None:
            missing.append(path)
    return sorted(missing)


def _anchor_validity(policy_spec_sha256: Optional[str]) -> Optional[bool]:
    """Tri-state validity of the caller's SPEC audit anchor.

    `None` means "no anchor was supplied" — a standalone `evaluate_maturity`
    call has none to check and is not penalised for it. `True`/`False` is the
    answer to "is this a 64-character SHA-256 hex digest a consumer could
    re-derive from the SPEC bytes?".
    """
    if policy_spec_sha256 is None:
        return None
    return isinstance(policy_spec_sha256, str) and bool(
        _SHA256_HEX_RE.fullmatch(policy_spec_sha256)
    )


def _policy_is_complete(
    missing_paths: list[str],
    policy_errors: list[str],
    anchor_valid: Optional[bool],
) -> bool:
    """THE definition of "the declared policy can gate a verdict", used by
    every consumer of that fact.

    Three ways to be the same fact: a required leaf is absent or invalid, the
    parser reported an error, or the SPEC revision the thresholds came from
    cannot be re-derived from the recorded anchor. A threshold nobody can
    trace back to a published SPEC is not a declared threshold, so it may not
    produce a `pass` or a `should-fix` anywhere in the artifact.

    This function exists so `evaluate_maturity`'s `policy_complete` check,
    `unit_economics.status`, `variance_status` and `drivers.payg_ptu.status`
    cannot drift apart into two definitions — a divergence would let one
    verdict claim `pass` on exactly the evidence another declared unusable.
    """
    return not missing_paths and not policy_errors and anchor_valid is not False


# ---------------------------------------------------------------------------
# Observed-evidence readers
# ---------------------------------------------------------------------------


class _Actuals:
    """Narrowly validated view over one `threadlight-cost-actuals/v1` doc."""

    def __init__(self, actuals: dict[str, Any]) -> None:
        scope = _section(actuals, "scope", "actuals.scope")
        window = _section(actuals, "window", "actuals.window")
        cost = _section(actuals, "cost", "actuals.cost")
        usage = _section(actuals, "usage", "actuals.usage")

        self.status = _optional_str(actuals, "status", "actuals.status")
        self.subscription_id = _optional_str(
            scope, "subscription_id", "actuals.scope.subscription_id"
        )
        self.resource_group = _optional_str(
            scope, "resource_group", "actuals.scope.resource_group"
        )
        self.complete_days = _optional_int(
            window, "complete_days", "actuals.window.complete_days", minimum=1
        )
        self.settlement_age_hours = _optional_int(
            window,
            "settlement_age_hours",
            "actuals.window.settlement_age_hours",
            minimum=0,
        )
        self.window_end_age_days = _optional_int(
            window,
            "window_end_age_days",
            "actuals.window.window_end_age_days",
            minimum=0,
        )
        self.cost_basis = _optional_str(cost, "basis", "actuals.cost.basis")
        self.period_total_usd = _optional_money(
            cost, "period_total_usd", "actuals.cost.period_total_usd"
        )
        unattributed = _optional_money(
            cost, "unattributed_usd", "actuals.cost.unattributed_usd"
        )
        # An ABSENT `unattributed_usd` and a declared `0.00` are the same
        # number but different evidence: only the second one asserts that
        # nothing was left unattributed, and the accounting identity below
        # needs to tell them apart. `is None` rather than `or Decimal(0)`,
        # which would also swallow a declared zero.
        self.unattributed_declared = unattributed is not None
        self.unattributed_usd = Decimal(0) if unattributed is None else unattributed
        self.source_coverage_pct = _optional_unit_ratio(
            cost, "resource_id_coverage_pct", "actuals.cost.resource_id_coverage_pct"
        )
        self.resources_declared = cost.get("resources") is not None
        self.resources = _require_list_of_mappings(
            cost, "resources", "actuals.cost.resources"
        )
        # Parsed once, here, so a malformed cell fails closed before any
        # consumer of this document (including a standalone maturity call)
        # can compute a ratio over it.
        self.resources_total = Decimal(0)
        for index, resource in enumerate(self.resources):
            self.resources_total += _parse_money(
                resource.get("period_cost_usd"),
                f"actuals.cost.resources[{index}].period_cost_usd",
            )
        self.interaction_status = _optional_str(
            usage, "interaction_status", "actuals.usage.interaction_status"
        )
        self.model_attribution_status = _optional_str(
            usage,
            "model_attribution_status",
            "actuals.usage.model_attribution_status",
        )
        self.successful_interactions = _optional_int(
            usage,
            "successful_interactions",
            "actuals.usage.successful_interactions",
            minimum=0,
        )
        self.models = _require_list_of_mappings(
            usage, "models", "actuals.usage.models"
        )


COST_IDENTITY_FAILURE = "actual cost rows do not reconcile to period_total_usd"


def _cost_evidence_reconciles(observed: _Actuals) -> tuple[bool, str]:
    """Does the per-resource breakdown add back up to the declared total?

    `sum(resources[].period_cost_usd) + unattributed_usd` must equal
    `cost.period_total_usd`, compared at CENT precision on the aggregate — not
    on a sum of per-row cent roundings, which would flag half-cent rows that
    genuinely do add up (see the schema reference on double rounding).

    Absent evidence is not contradictory evidence: with no `period_total_usd`
    there is nothing to reconcile against, and with neither a resource list
    nor a declared `unattributed_usd` there is no breakdown to reconcile at
    all. Both return `True` here and are already `not-verified` elsewhere.

    Returns `(True, "")` or `(False, <reason>)` for every reconcilable input,
    but it is NOT a never-raises helper: the two `_quantize` calls below can
    raise `ReconciliationInputError` when a money magnitude cannot be
    represented at cent precision. `_Actuals` parses each cell for shape, not
    for magnitude, so a well-formed but astronomical figure reaches this
    function intact and fails here — deliberately, since a total nobody can
    represent must not silently become a reconciled one. Callers, including
    `_usable_coverage` and therefore a standalone `evaluate_maturity`, must
    expect that error.
    """
    if observed.period_total_usd is None:
        return True, ""
    if not observed.resources_declared and not observed.unattributed_declared:
        return True, ""
    breakdown = _quantize(
        observed.resources_total + observed.unattributed_usd,
        _CENT,
        context="actuals.cost breakdown total",
    )
    declared = _quantize(
        observed.period_total_usd, _CENT, context="actuals.cost.period_total_usd"
    )
    if breakdown == declared:
        return True, ""
    return False, (
        f"{COST_IDENTITY_FAILURE}: resource rows plus unattributed spend sum "
        f"to {breakdown} while the declared period total is {declared}"
    )


def _usable_coverage(
    raw: object, observed: _Actuals
) -> tuple[Optional[float], str]:
    """Return `(coverage, "")` when the value may gate a verdict, or
    `(None, <reason>)` when it may not.

    Two independent ways coverage stops being usable, checked in that order:

    1. The cost evidence contradicts itself. Coverage divides one part of that
       evidence by another, so its most dangerous possible value is a
       confident `1.0` computed over rows that do not add up to the bill.
    2. The value is not a ratio in `[0, 1]`. `True` (an `int` subclass), a
       percentage passed as `42`, a NaN, or a negative share are all rejected
       to `None` rather than raised on: an exception here would suppress an
       entire artifact over one bad ratio, and a NaN that reached `actual`
       would serialize as bare `NaN`, which is not valid JSON.
    """
    reconciles, reason = _cost_evidence_reconciles(observed)
    if not reconciles:
        return None, (
            f"coverage cannot be trusted because {reason}; it is reported as "
            "null rather than as a share of a total that does not hold"
        )
    if raw is None:
        return None, ""
    invalid = (
        f"projection_attribution_coverage_pct {raw!r} is not a ratio in "
        "[0, 1], so it cannot gate a verdict and is reported as null"
    )
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, invalid
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None, invalid
    return value, ""


def _normalize_id(raw: object, label: str) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ReconciliationInputError(f"{label} must be a string, got {raw!r}")
    normalized = raw.casefold().rstrip("/")
    return normalized or None


def _normalize_type(raw: object, label: str) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ReconciliationInputError(f"{label} must be a string, got {raw!r}")
    return raw.casefold().rstrip("/").strip()


# ---------------------------------------------------------------------------
# Resource matching (RFC §9.4)
# ---------------------------------------------------------------------------


class _ForecastGroup:
    """One forecast resource, or several that roll up to the same AOAI account."""

    __slots__ = (
        "match_id",
        "match_type",
        "forecast_ids",
        "deployment_ids",
        "monthly",
        "rolled_up",
    )

    def __init__(self, match_id: str, match_type: str) -> None:
        self.match_id = match_id
        self.match_type = match_type
        self.forecast_ids: list[str] = []
        self.deployment_ids: list[str] = []
        self.monthly = Decimal(0)
        self.rolled_up = False


def _forecast_groups(
    forecast: dict[str, Any], warnings: list[str]
) -> dict[str, _ForecastGroup]:
    groups: dict[str, _ForecastGroup] = {}
    for index, resource in enumerate(
        _require_list_of_mappings(forecast, "resources", "forecast.resources")
    ):
        label = f"forecast.resources[{index}]"
        original_id = resource.get("resource_id")
        normalized = _normalize_id(original_id, f"{label}.resource_id")
        if normalized is None:
            _warn(
                warnings,
                f"forecast: {label} has no resource_id and cannot be matched "
                "against observed spend",
            )
            continue
        kind = _normalize_type(resource.get("resource_kind"), f"{label}.resource_kind")

        aoai = _AOAI_DEPLOYMENT_RE.match(normalized)
        if aoai is not None:
            match_id = aoai.group("account")
            match_type = _AOAI_ACCOUNT_TYPE
        else:
            match_id = normalized
            match_type = kind

        group = groups.get(match_id)
        if group is None:
            group = _ForecastGroup(match_id, match_type)
            groups[match_id] = group
        elif group.match_type != match_type:
            # Two forecast entries that normalize to the same ID but declare
            # different types make the group's type meaningless; blank it so
            # the type fallback can never fire on a contradiction.
            group.match_type = ""
            _warn(
                warnings,
                f"forecast: {original_id!r} disagrees with an earlier resource "
                "on resource_kind; type fallback is disabled for it",
            )

        group.forecast_ids.append(str(original_id))
        if aoai is not None:
            group.rolled_up = True
            group.deployment_ids.append(str(original_id))

        if resource.get("monthly_cost_usd") is None:
            _warn(
                warnings,
                f"forecast: {original_id!r} has no monthly_cost_usd; it "
                "contributes 0.00 to matched forecast cost",
            )
        else:
            group.monthly += _parse_money(
                resource["monthly_cost_usd"], f"{label}.monthly_cost_usd"
            )

    for group in groups.values():
        group.forecast_ids.sort()
        group.deployment_ids.sort()
    return groups


class _ActualResource:
    __slots__ = ("resource_id", "match_id", "match_type", "cost")

    def __init__(self, resource_id: str, match_id: str, match_type: str) -> None:
        self.resource_id = resource_id
        self.match_id = match_id
        self.match_type = match_type
        self.cost = Decimal(0)


def _actual_resources(
    observed: _Actuals, warnings: list[str]
) -> dict[str, _ActualResource]:
    entries: dict[str, _ActualResource] = {}
    for index, resource in enumerate(observed.resources):
        label = f"actuals.cost.resources[{index}]"
        original_id = resource.get("resource_id")
        normalized = _normalize_id(original_id, f"{label}.resource_id")
        if normalized is None:
            _warn(
                warnings,
                f"actuals: {label} has no resource_id; its cost is reported "
                "as unmodeled rather than dropped",
            )
            normalized = f"__unidentified__{index}"
            original_id = ""
        resource_type = _normalize_type(
            resource.get("resource_type"), f"{label}.resource_type"
        )
        entry = entries.get(normalized)
        if entry is None:
            entry = _ActualResource(str(original_id), normalized, resource_type)
            entries[normalized] = entry
        elif entry.match_type != resource_type:
            entry.match_type = ""
            _warn(
                warnings,
                f"actuals: {original_id!r} appears twice with different "
                "resource_type values; type fallback is disabled for it",
            )
        entry.cost += _parse_money(
            resource.get("period_cost_usd"), f"{label}.period_cost_usd"
        )
    return entries


def _match(
    groups: dict[str, _ForecastGroup],
    actual_entries: dict[str, _ActualResource],
    warnings: list[str],
) -> list[tuple[_ForecastGroup, _ActualResource, str]]:
    """Exact normalized ID first, then a deliberately narrow unique-type
    fallback. Ambiguity is recorded and left unmatched, never guessed: a
    coin-flip pairing would produce a confident-looking but arbitrary
    per-resource variance."""
    pairs: list[tuple[_ForecastGroup, _ActualResource, str]] = []
    matched_actuals: set[str] = set()
    matched_groups: set[str] = set()

    for match_id in sorted(groups):
        entry = actual_entries.get(match_id)
        if entry is None:
            continue
        group = groups[match_id]
        method = "aoai_account_rollup" if group.rolled_up else "resource_id"
        pairs.append((group, entry, method))
        matched_groups.add(match_id)
        matched_actuals.add(entry.match_id)

    unmatched_groups = [
        groups[key] for key in sorted(groups) if key not in matched_groups
    ]
    unmatched_actuals = [
        actual_entries[key]
        for key in sorted(actual_entries)
        if key not in matched_actuals
    ]

    by_type_forecast: dict[str, list[_ForecastGroup]] = {}
    for group in unmatched_groups:
        # A rolled-up AOAI group already asserted an ACCOUNT-scoped identity.
        # If that exact account was not observed, the account genuinely was
        # not billed, and a same-type fallback would silently re-attribute
        # the deployment's forecast to a different account's bill.
        if group.match_type and not group.rolled_up:
            by_type_forecast.setdefault(group.match_type, []).append(group)
    by_type_actual: dict[str, list[_ActualResource]] = {}
    for entry in unmatched_actuals:
        if entry.match_type:
            by_type_actual.setdefault(entry.match_type, []).append(entry)

    for resource_type in sorted(set(by_type_forecast) & set(by_type_actual)):
        candidates_forecast = by_type_forecast[resource_type]
        candidates_actual = by_type_actual[resource_type]
        if len(candidates_forecast) == 1 and len(candidates_actual) == 1:
            pairs.append((candidates_forecast[0], candidates_actual[0], "unique_type"))
            continue
        _warn(
            warnings,
            f"coverage: resource type {resource_type!r} has "
            f"{len(candidates_forecast)} unmatched forecast and "
            f"{len(candidates_actual)} unmatched actual resources; the "
            "pairing is ambiguous so no cost was attributed",
        )
    return pairs


# ---------------------------------------------------------------------------
# Maturity (RFC §10)
# ---------------------------------------------------------------------------


def _check(
    check_id: str, status: str, actual: object, required: object, detail: str
) -> dict[str, object]:
    return {
        "id": check_id,
        "status": status,
        "actual": actual,
        "required": required,
        "detail": detail,
    }


def _threshold_check(
    check_id: str,
    actual: Optional[float],
    required: Optional[float],
    *,
    at_most: bool,
    detail_pass: str,
    detail_fail: str,
) -> dict[str, object]:
    if actual is None or required is None:
        return _check(check_id, NOT_VERIFIED, actual, required, detail_fail)
    satisfied = actual <= required if at_most else actual >= required
    return _check(
        check_id,
        PASS if satisfied else NOT_VERIFIED,
        actual,
        required,
        detail_pass if satisfied else detail_fail,
    )


def evaluate_maturity(
    actuals: dict[str, object],
    policy: dict[str, object],
    *,
    policy_errors: Iterable[str] = (),
    projection_attribution_coverage_pct: Optional[float] = None,
    policy_spec_sha256: Optional[str] = None,
) -> dict[str, object]:
    """Evaluate every named maturity check and the overall verdict.

    Returns `{"status": "pass" | "not-verified", "checks": [...]}` where
    `checks` always contains one entry per `MATURITY_CHECK_IDS`, in that
    order, each carrying `id`, `status`, `actual`, `required` and a
    human-readable `detail`. Observed numbers are preserved in `actual` even
    when the check fails, so a `not-verified` artifact still says HOW FAR
    from mature the evidence was.

    The verdict is `pass` only when every check passes (RFC §10). Absent
    evidence is `not-verified`, never an assumed pass, and never an
    exception: only structurally malformed evidence raises
    `ReconciliationInputError`.

    `projection_attribution_coverage_pct` is supplied by the caller because
    it is a property of the forecast/actuals JOIN, not of the actuals
    document alone. Omitting it leaves that one check `not-verified` — a
    standalone maturity call therefore fails closed rather than silently
    treating unknown coverage as complete. A supplied value that is not a
    ratio in `[0, 1]`, or one computed over cost rows that do not add up to
    `period_total_usd`, is treated exactly like an absent one and explained
    in that check's `detail`; neither raises.

    `policy_spec_sha256` is the caller's SPEC audit anchor. `None` means "not
    supplied" (a standalone call has no anchor to check) and does not fail
    anything; a supplied string that is not a 64-character hex digest fails
    `policy_complete`, because a policy whose provenance cannot be pinned to
    a SPEC revision is not a policy a consumer can re-verify. That is the
    same `_policy_is_complete` fact `reconcile_costs` gates unit economics,
    the cost variance verdict and the PAYG/PTU driver on.
    """
    _require_mapping(actuals, "actuals")
    _require_mapping(policy, "policy")
    errors = _require_error_list(policy_errors)
    observed = _Actuals(actuals)
    snapshot = _policy_snapshot(policy)
    missing = _missing_policy_paths(policy, snapshot)

    anchor_valid = _anchor_validity(policy_spec_sha256)

    checks: list[dict[str, object]] = []

    policy_complete = _policy_is_complete(missing, errors, anchor_valid)
    checks.append(
        _check(
            "policy_complete",
            PASS if policy_complete else NOT_VERIFIED,
            {
                "missing_paths": missing,
                "policy_error_count": len(errors),
                "policy_spec_sha256_valid": anchor_valid,
            },
            {
                "missing_paths": [],
                "policy_error_count": 0,
                "policy_spec_sha256_valid": True,
            },
            "SPEC section 14 value_model parsed cleanly, declares every "
            "required leaf, and is anchored to a re-derivable SPEC digest"
            if policy_complete
            else "SPEC section 14 value_model is incomplete, did not parse "
            "cleanly, or is not anchored to a re-derivable SPEC digest; "
            "thresholds cannot gate a verdict",
        )
    )

    # The check is "is there usable authoritative cost evidence", which needs
    # BOTH a verified collection and an actual period total: a document that
    # claims `pass` while carrying no total cannot be reconciled against
    # anything, and the total is never backfilled from token reprice.
    actuals_usable = observed.status == PASS and observed.period_total_usd is not None
    checks.append(
        _check(
            "actuals_status",
            PASS if actuals_usable else NOT_VERIFIED,
            observed.status,
            PASS,
            "Cost Management evidence was collected and validated"
            if actuals_usable
            else "Cost Management evidence is not verified; there is no "
            "authoritative actual total to reconcile"
            if observed.status != PASS
            else "the actuals document reports pass but carries no "
            "cost.period_total_usd, so there is no authoritative actual total "
            "to reconcile",
        )
    )

    checks.append(
        _threshold_check(
            "complete_days",
            observed.complete_days,
            snapshot["min_complete_days"],
            at_most=False,
            detail_pass="observed window covers at least the declared minimum "
            "of complete UTC days",
            detail_fail="observed window is shorter than the declared minimum "
            "of complete UTC days, or the day count is unknown",
        )
    )

    interactions_observed = (
        observed.successful_interactions
        if observed.interaction_status == PASS
        else None
    )
    successes_check = _threshold_check(
        "successful_interactions",
        interactions_observed,
        snapshot["min_successful_interactions"],
        at_most=False,
        detail_pass="enough successful interactions were observed to make "
        "unit economics representative",
        detail_fail="too few successful interactions were observed, or the "
        "interaction query never produced a verified count",
    )
    # Report the raw observed count even when the interaction query itself is
    # `not-verified`, so the artifact never hides a number it actually saw.
    successes_check["actual"] = observed.successful_interactions
    checks.append(successes_check)

    checks.append(
        _threshold_check(
            "cost_settlement_age_hours",
            observed.settlement_age_hours,
            snapshot["min_cost_settlement_age_hours"],
            at_most=False,
            detail_pass="the window has been settling long enough for charges "
            "to be representative",
            detail_fail="the window is too recent to have settled; late "
            "charges would still change the total",
        )
    )

    checks.append(
        _threshold_check(
            "window_end_age_days",
            observed.window_end_age_days,
            snapshot["max_window_end_age_days"],
            at_most=True,
            detail_pass="the window is recent enough to represent current "
            "operations",
            detail_fail="the window ended too long ago to represent current "
            "operations",
        )
    )

    coverage_value, coverage_reason = _usable_coverage(
        projection_attribution_coverage_pct, observed
    )
    if coverage_reason:
        checks.append(
            _check(
                "projection_attribution_coverage",
                NOT_VERIFIED,
                None,
                snapshot["min_projection_attribution_coverage_pct"],
                coverage_reason,
            )
        )
    else:
        checks.append(
            _threshold_check(
                "projection_attribution_coverage",
                coverage_value,
                snapshot["min_projection_attribution_coverage_pct"],
                at_most=False,
                detail_pass="the projection explains at least the declared minimum "
                "share of observed spend",
                detail_fail="too little observed spend maps onto a projected "
                "resource, or coverage could not be computed",
            )
        )

    basis_ok = (
        observed.cost_basis == COST_BASIS_LITERAL
        and snapshot["actual_cost_basis"] == COST_BASIS_LITERAL
    )
    checks.append(
        _check(
            "cost_accounting_basis",
            PASS if basis_ok else NOT_VERIFIED,
            {
                "actuals_cost_basis": observed.cost_basis,
                "policy_actual_cost_basis": snapshot["actual_cost_basis"],
            },
            {
                "actuals_cost_basis": COST_BASIS_LITERAL,
                "policy_actual_cost_basis": COST_BASIS_LITERAL,
            },
            "observed cost and declared policy both use the usage-pretax "
            "accounting metric"
            if basis_ok
            else "observed cost metric and the declared accounting metric are "
            "not both usage-pretax, so the two sides are not comparable",
        )
    )

    comparable, basis_reason = _price_bases_comparable(snapshot)
    checks.append(
        _check(
            "price_basis_compatible",
            PASS if comparable else NOT_VERIFIED,
            {
                "actual_billing_price_basis": snapshot["actual_billing_price_basis"],
                "forecast_price_basis": snapshot["forecast_price_basis"],
                "allow_basis_mismatch_for_verdict": snapshot[
                    "allow_basis_mismatch_for_verdict"
                ],
            },
            "equal-known-bases-or-explicitly-allowed",
            basis_reason,
        )
    )

    status = PASS if all(entry["status"] == PASS for entry in checks) else NOT_VERIFIED
    return {"status": status, "checks": checks}


def _price_bases_comparable(snapshot: dict[str, object]) -> tuple[bool, str]:
    """RFC §9.5: compare `actual_billing_price_basis` with
    `forecast_price_basis`. `actual_cost_basis` (`usage-pretax`) is the
    metric and source, never one side of this comparison."""
    actual = snapshot["actual_billing_price_basis"]
    forecast = snapshot["forecast_price_basis"]
    allow = snapshot["allow_basis_mismatch_for_verdict"]
    if allow is True:
        return True, (
            "price bases are not compared because SPEC sets "
            "allow_basis_mismatch_for_verdict: true"
        )
    if actual is None or forecast is None:
        return False, (
            "a declared price basis is missing, so forecast and actual prices "
            "cannot be shown to be comparable"
        )
    if actual == "unknown":
        return False, (
            "the actual billing price basis is unknown, so the observed price "
            "list cannot be shown to match the forecast's"
        )
    if actual != forecast:
        return False, (
            f"actual billing price basis {actual!r} differs from forecast "
            f"price basis {forecast!r} and SPEC does not set "
            "allow_basis_mismatch_for_verdict: true"
        )
    return True, f"forecast and actual are both priced on the {actual!r} basis"


# ---------------------------------------------------------------------------
# PAYG/PTU driver
# ---------------------------------------------------------------------------


def _payg_ptu_driver(
    forecast: dict[str, Any],
    observed: _Actuals,
    snapshot: dict[str, object],
    warnings: list[str],
    *,
    policy_complete: bool,
) -> dict[str, object]:
    """Compare observed AOAI TOKEN VOLUME against the volume the sizing
    recommendation assumed.

    This never prices tokens and never reads a cost threshold: a workload can
    absorb far more volume drift than cost drift before a PAYG/PTU
    recommendation stops holding, so the tolerance is SPEC's separate
    `max_token_volume_variance_pct`, recorded in `threshold_field` for audit.

    Because the verdict rests on that SPEC-declared tolerance, it is gated by
    the same `policy_complete` fact as every other threshold-gated status: an
    incomplete, unparsed or unanchored policy leaves `status` `not-verified`
    while the observed and forecast token volumes — which no policy was
    needed to measure — are still reported.
    """
    threshold = snapshot[TOKEN_VARIANCE_THRESHOLD_FIELD]
    driver: dict[str, object] = {
        "status": NOT_VERIFIED,
        "observed_volume_variance_pct": None,
        "forecast_monthly_tokens": None,
        "observed_monthly_tokens": None,
        "threshold_field": TOKEN_VARIANCE_THRESHOLD_FIELD,
        "threshold_pct": threshold,
        "detail": "",
    }

    accounts = _payg_ptu_accounts(forecast)
    if not accounts:
        driver["detail"] = (
            "no explicit PAYG-to-PTU or PTU-to-PAYG recommendation for an "
            "Azure OpenAI deployment is present in the forecast"
        )
        return driver

    deployment_ids = sorted(
        deployment_id
        for account_deployments in accounts.values()
        for deployment_id in account_deployments
    )
    forecast_tokens = _forecast_deployment_tokens(forecast, deployment_ids)
    driver["forecast_monthly_tokens"] = forecast_tokens

    if observed.model_attribution_status != PASS:
        driver["detail"] = (
            "token metrics were not collected, so observed volume cannot be "
            "compared against the recommendation's load assumption"
        )
        return driver

    observed_tokens, ambiguous = _observed_deployment_tokens(
        observed, accounts, warnings
    )
    if ambiguous:
        driver["detail"] = (
            "the recommendation spans more than one Azure OpenAI account and "
            "an observed token row carries only a bare deployment name, so "
            "observed volume cannot be attributed to a specific account"
        )
        return driver
    if observed_tokens is None:
        driver["detail"] = (
            "no observed token row is attributed to the recommended "
            "deployments, so observed volume is unknown"
        )
        _warn(
            warnings,
            "drivers.payg_ptu: no observed token row matches the recommended "
            "deployments in their own Azure OpenAI account; the driver is not "
            "verified",
        )
        return driver

    if observed.complete_days is None:
        driver["detail"] = (
            "the observed window length is unknown, so token volume cannot be "
            "normalized to a monthly figure"
        )
        return driver

    observed_monthly = (
        Decimal(observed_tokens) * _MONTH_DAYS / Decimal(observed.complete_days)
    )
    driver["observed_monthly_tokens"] = _ratio(
        observed_monthly, context="drivers.payg_ptu.observed_monthly_tokens"
    )

    if forecast_tokens <= 0:
        driver["detail"] = (
            "the forecast models zero monthly tokens for the recommended "
            "deployments, so there is no baseline to compare against"
        )
        return driver

    variance = (observed_monthly - Decimal(forecast_tokens)) / Decimal(forecast_tokens)
    quantized = _quantize(
        variance, _RATIO, context="drivers.payg_ptu.observed_volume_variance_pct"
    )
    driver["observed_volume_variance_pct"] = float(quantized)

    if not policy_complete:
        driver["detail"] = (
            "SPEC section 14 is incomplete, did not parse cleanly, or is not "
            "anchored to a re-derivable SPEC digest, so its declared "
            f"{TOKEN_VARIANCE_THRESHOLD_FIELD} cannot gate a verdict; the "
            "measured volumes are reported unchanged"
        )
        return driver

    if threshold is None:
        driver["detail"] = (
            f"SPEC does not declare {TOKEN_VARIANCE_THRESHOLD_FIELD}, so "
            "observed volume drift cannot be verdicted"
        )
        return driver

    if abs(quantized) <= Decimal(str(threshold)):
        driver["status"] = PASS
        driver["detail"] = (
            "observed token volume is inside the declared "
            f"{TOKEN_VARIANCE_THRESHOLD_FIELD} band, so the recommendation's "
            "load assumption still holds"
        )
    else:
        driver["status"] = SHOULD_FIX
        driver["detail"] = (
            "observed token volume is outside the declared "
            f"{TOKEN_VARIANCE_THRESHOLD_FIELD} band: rerun PAYG/PTU analysis "
            "at observed volume"
        )
    return driver


def _payg_ptu_accounts(forecast: dict[str, Any]) -> dict[str, list[str]]:
    """Recommended AOAI deployments, GROUPED BY their parent account.

    Anything else — a same-tier recommendation, a non-AOAI resource, a tier
    this driver does not model — is ignored rather than inferred.

    The grouping is the point: `deployment` in an observed token row is a leaf
    name, and two accounts can each own a `chat`. Keeping the account each
    recommended deployment belongs to is what lets the observed side refuse
    to add up names that only look alike (RFC §9.4).
    """
    accounts: dict[str, list[str]] = {}
    for index, recommendation in enumerate(
        _require_list_of_mappings(
            forecast, "recommendations", "forecast.recommendations"
        )
    ):
        label = f"forecast.recommendations[{index}]"
        current = _section(recommendation, "current_sku", f"{label}.current_sku")
        recommended = _section(
            recommendation, "recommended_sku", f"{label}.recommended_sku"
        )
        current_tier = _normalize_type(current.get("tier"), f"{label}.current_sku.tier")
        recommended_tier = _normalize_type(
            recommended.get("tier"), f"{label}.recommended_sku.tier"
        )
        if {current_tier, recommended_tier} != _PTU_TIERS:
            continue
        normalized = _normalize_id(
            recommendation.get("resource_id"), f"{label}.resource_id"
        )
        if normalized is None:
            continue
        aoai = _AOAI_DEPLOYMENT_RE.match(normalized)
        if aoai is None:
            continue
        deployments = accounts.setdefault(aoai.group("account"), [])
        if normalized not in deployments:
            deployments.append(normalized)
    return {account: sorted(accounts[account]) for account in sorted(accounts)}


def _forecast_deployment_tokens(
    forecast: dict[str, Any], deployment_ids: list[str]
) -> int:
    total = 0
    wanted = set(deployment_ids)
    for index, resource in enumerate(
        _require_list_of_mappings(forecast, "resources", "forecast.resources")
    ):
        label = f"forecast.resources[{index}]"
        normalized = _normalize_id(resource.get("resource_id"), f"{label}.resource_id")
        if normalized is None or normalized not in wanted:
            continue
        units = _section(
            resource, "monthly_units_consumed", f"{label}.monthly_units_consumed"
        )
        for axis in ("input_tokens", "output_tokens"):
            raw = units.get(axis)
            if raw is None:
                continue
            total += _require_token_count(
                raw, f"{label}.monthly_units_consumed.{axis}"
            )
    return total


class _RowIdentity:
    """What one observed token row says about which resource it came from.

    `account` is the resource the row's costs roll up to (an AOAI account
    when the ID is an AOAI one, otherwise whatever resource the ID names).
    `is_aoai` records whether the ID's NORMALIZED TYPE actually proves that —
    a warning must never call a storage account an "Azure OpenAI account".
    `deployment` is the deployment leaf the ID itself carries, which is the
    row's second, independent statement of the deployment name.
    """

    __slots__ = ("account", "is_aoai", "deployment")

    def __init__(
        self, account: str, is_aoai: bool, deployment: Optional[str]
    ) -> None:
        self.account = account
        self.is_aoai = is_aoai
        self.deployment = deployment


def _row_identity(row: dict[str, Any], label: str) -> Optional[_RowIdentity]:
    """The resource an observed token row belongs to, if it says.

    `resource_id` (a deployment or an account) is preferred; an
    `account_resource_id` from a collector that could only attribute to the
    billed account is accepted too. Absent identity returns `None` — a fact
    about the row, not an error.
    """
    for key in ("resource_id", "account_resource_id"):
        raw = row.get(key)
        if raw is None:
            continue
        normalized = _normalize_id(raw, f"{label}.{key}")
        if normalized is None:
            continue
        aoai = _AOAI_DEPLOYMENT_RE.match(normalized)
        if aoai is not None:
            return _RowIdentity(
                aoai.group("account"), True, aoai.group("deployment")
            )
        return _RowIdentity(
            normalized, _AOAI_ACCOUNT_RE.match(normalized) is not None, None
        )
    return None


def _account_phrase(identity: _RowIdentity) -> str:
    """How a warning may describe this row's account. Only an ID whose
    normalized type is `microsoft.cognitiveservices/accounts` earns the
    Azure OpenAI wording; anything else is named neutrally so the artifact
    never asserts a resource kind the evidence does not show."""
    return (
        "Azure OpenAI account" if identity.is_aoai else "resource account"
    )


def _observed_deployment_tokens(
    observed: _Actuals, accounts: dict[str, list[str]], warnings: list[str]
) -> tuple[Optional[int], bool]:
    """Observed tokens for the recommended deployments, and whether the
    attribution was ambiguous.

    Returns `(tokens, ambiguous)`. A row is counted when it names a
    recommended deployment AND either identifies its own account (which must
    be one of the recommended accounts) or the recommendation implicates
    exactly one account, which makes the bare name unambiguous. When more
    than one account is implicated and a matching row carries no identity,
    the whole driver is ambiguous: summing it would attribute one account's
    traffic to another, and dropping it would understate the account it
    really belongs to.
    """
    names_by_account = {
        account: {
            deployment_id.rsplit("/", 1)[-1] for deployment_id in deployment_ids
        }
        for account, deployment_ids in accounts.items()
    }
    every_name = set().union(*names_by_account.values()) if names_by_account else set()
    single_account = len(accounts) == 1

    total = 0
    matched = False
    for index, row in enumerate(observed.models):
        label = f"actuals.usage.models[{index}]"
        name = _normalize_type(row.get("deployment"), f"{label}.deployment")
        identity = _row_identity(row, label)
        leaf = identity.deployment if identity is not None else None
        if name and leaf is not None and name != leaf:
            # The row states its deployment twice and the two statements
            # disagree, so it identifies no deployment at all. Counting it
            # under either name would attribute one deployment's traffic to
            # another on evidence the row itself contradicts.
            if name in every_name or leaf in every_name:
                _warn(
                    warnings,
                    "drivers.payg_ptu: an observed token row contradicts "
                    f"itself — it declares deployment {name!r} but its "
                    f"resource identifier names deployment {leaf!r}; it is "
                    "excluded from observed volume",
                )
            continue
        if not name and leaf is not None:
            # A row identified only by a deployment resource ID still names a
            # deployment; read the leaf rather than discarding the row.
            name = leaf
        if not name or name not in every_name:
            continue
        if identity is not None:
            if name not in names_by_account.get(identity.account, set()):
                _warn(
                    warnings,
                    "drivers.payg_ptu: an observed token row for deployment "
                    f"{name!r} belongs to {_account_phrase(identity)} "
                    f"{identity.account!r}, which carries no PAYG/PTU "
                    "recommendation; it is excluded from observed volume",
                )
                continue
        elif not single_account:
            _warn(
                warnings,
                "drivers.payg_ptu: the PAYG/PTU recommendation spans "
                f"{len(accounts)} Azure OpenAI accounts and an observed token "
                f"row names only deployment {name!r} with no account or "
                "resource identifier; the driver is not verified rather than "
                "summing rows across accounts",
            )
            return None, True
        matched = True
        for axis in ("input_tokens", "output_tokens"):
            raw = row.get(axis)
            if raw is None:
                continue
            total += _require_token_count(raw, f"{label}.{axis}")
    return (total if matched else None), False


def _expected_scope_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReconciliationInputError(f"{label} must be a string, got {value!r}")
    text = value.strip()
    return text or None


def _validate_expected_scope(
    observed: _Actuals,
    *,
    expected_subscription_id: object,
    expected_resource_group: object,
) -> None:
    expected_sub = _expected_scope_text(
        expected_subscription_id, "expected_subscription_id"
    )
    expected_rg = _expected_scope_text(
        expected_resource_group, "expected_resource_group"
    )
    if (
        expected_sub is not None
        and (
            observed.subscription_id is None
            or observed.subscription_id.casefold() != expected_sub.casefold()
        )
    ):
        raise ReconciliationInputError(
            "actuals scope subscription_id "
            f"{observed.subscription_id!r} does not match expected {expected_sub!r}"
        )
    if (
        expected_rg is not None
        and (
            observed.resource_group is None
            or observed.resource_group.casefold() != expected_rg.casefold()
        )
    ):
        raise ReconciliationInputError(
            "actuals scope resource_group "
            f"{observed.resource_group!r} does not match expected {expected_rg!r}"
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def reconcile_costs(
    forecast: dict[str, object],
    actuals: dict[str, object],
    policy: dict[str, object],
    *,
    expected_subscription_id: str | None = None,
    expected_resource_group: str | None = None,
    policy_errors: list[str],
    generated_at: str,
    policy_spec_sha256: str,
    forecast_path: str = FORECAST_PATH,
    actuals_path: str = ACTUALS_PATH,
    policy_path: str = POLICY_PATH,
) -> dict[str, object]:
    """Return one `threadlight-cost-reconciliation/v1` document.

    `policy` may be partial and `policy_errors` non-empty; this function
    never refuses to produce a manifest. Errors are copied to
    `policy_errors` in the output and force `maturity.status`,
    `unit_economics.status` and `variance_status` to `not-verified`, but
    every observed number is still reported.

    `forecast_path` / `actuals_path` / `policy_path` are *provenance only*:
    they are echoed verbatim into `forecast_ref.path`, `actuals_ref.path`
    and `policy_ref.path` so the manifest names the artifacts the caller
    actually read. They default to the canonical repo-relative layout, and a
    caller that resolved its inputs elsewhere (a per-pilot directory, a
    temporary checkout) must pass what it opened. They never take part in
    any digest: `forecast_ref.sha256` and `actuals_ref.sha256` hash the
    documents' bytes, so moving evidence cannot change what a consumer
    re-derives from it.

    Inputs are never mutated: nothing is written back into `forecast`,
    `actuals`, `policy` or `policy_errors`, and every list in the returned
    document is a fresh list, so a caller mutating the result cannot reach
    back into its own inputs.

    Raises `ReconciliationInputError` only for structurally malformed
    evidence — see the module docstring for the exact boundary.
    """
    _require_mapping(forecast, "forecast")
    _require_mapping(actuals, "actuals")
    _require_mapping(policy, "policy")
    errors = _require_error_list(policy_errors)
    generated_at = _require_generated_at(generated_at)
    forecast_path = _require_ref_path(forecast_path, "forecast_path")
    actuals_path = _require_ref_path(actuals_path, "actuals_path")
    policy_path = _require_ref_path(policy_path, "policy_path")
    if not isinstance(policy_spec_sha256, str):
        raise ReconciliationInputError(
            f"policy_spec_sha256 must be a string, got {policy_spec_sha256!r}"
        )

    warnings: list[str] = []
    # A malformed anchor is caller evidence, not policy content, but this
    # module always emits: the string is echoed verbatim so a consumer can see
    # exactly what it was handed, and the verdict degrades through
    # `policy_complete` (see `evaluate_maturity`). Raising here would suppress
    # an artifact whose observed numbers are perfectly good; returning a
    # silent `pass` would claim a provenance nobody can re-derive.
    if _SHA256_HEX_RE.fullmatch(policy_spec_sha256) is None:
        _warn(
            warnings,
            f"policy_ref: spec_sha256 {policy_spec_sha256!r} is not a "
            "64-character SHA-256 hex digest, so the SPEC revision this "
            "policy was read from cannot be re-derived; maturity, unit "
            "economics, the cost variance verdict and the PAYG/PTU driver "
            "are not verified",
        )
    observed = _Actuals(actuals)
    _validate_expected_scope(
        observed,
        expected_subscription_id=expected_subscription_id,
        expected_resource_group=expected_resource_group,
    )
    snapshot = _policy_snapshot(policy, warnings)
    missing_paths = _missing_policy_paths(policy, snapshot)
    # ONE definition, shared with `evaluate_maturity`: an unusable anchor is
    # an incomplete policy, so every threshold-gated verdict below degrades
    # together instead of one of them claiming a `pass` the maturity block
    # already declared unprovable.
    policy_complete = _policy_is_complete(
        missing_paths, errors, _anchor_validity(policy_spec_sha256)
    )

    days = observed.complete_days

    # --- totals (RFC §9.2) -------------------------------------------------
    forecast_totals = _section(forecast, "totals", "forecast.totals")
    forecast_monthly = _optional_money(
        forecast_totals, "monthly_cost_current_usd", "forecast.totals.monthly_cost_current_usd"
    )
    forecast_monthly_usd = _usd(forecast_monthly, context="forecast_monthly_usd")

    forecast_window = (
        forecast_monthly * Decimal(days) / _MONTH_DAYS
        if forecast_monthly is not None and days is not None
        else None
    )
    forecast_window_dec = (
        _quantize(forecast_window, _CENT, context="forecast_window_usd")
        if forecast_window is not None
        else None
    )

    # The Cost Management period total is the ONLY source of actual spend.
    actual_window_dec = (
        _quantize(observed.period_total_usd, _CENT, context="actual_window_usd")
        if observed.period_total_usd is not None
        else None
    )

    run_rate = (
        actual_window_dec * _MONTH_DAYS / Decimal(days)
        if actual_window_dec is not None and days is not None
        else None
    )

    variance_dec = (
        actual_window_dec - forecast_window_dec
        if actual_window_dec is not None and forecast_window_dec is not None
        else None
    )

    variance_pct_dec: Optional[Decimal] = None
    if variance_dec is not None and forecast_window_dec is not None:
        if forecast_window_dec == 0:
            _warn(
                warnings,
                "totals: variance_pct is null because forecast_window_usd is "
                "0.00; a percentage against a zero baseline is undefined, "
                "never zero or infinite",
            )
        elif forecast_window_dec < 0:
            # Dividing by a negative baseline flips the sign of the ratio: an
            # overspend would report as a large negative "under budget"
            # percentage. The signed dollar amounts above are still exact and
            # are the honest way to read this case.
            _warn(
                warnings,
                "totals: variance_pct is null because forecast_window_usd is "
                "negative; a percentage against a negative baseline inverts "
                "the sign of the variance and would read as its opposite",
            )
        else:
            variance_pct_dec = _quantize(
                variance_dec / forecast_window_dec, _RATIO, context="variance_pct"
            )
    elif forecast_window_dec is None or actual_window_dec is None:
        _warn(
            warnings,
            "totals: variance_pct is null because the forecast window total or "
            "the observed window total could not be computed",
        )

    totals = {
        "forecast_monthly_usd": forecast_monthly_usd,
        "forecast_window_usd": _usd(forecast_window_dec, context="forecast_window_usd"),
        "actual_window_usd": _usd(actual_window_dec, context="actual_window_usd"),
        "actual_monthly_run_rate_usd": _usd(
            run_rate, context="actual_monthly_run_rate_usd"
        ),
        "variance_window_usd": _usd(variance_dec, context="variance_window_usd"),
        "variance_pct": float(variance_pct_dec) if variance_pct_dec is not None else None,
    }

    # --- coverage (RFC §9.4) ----------------------------------------------
    coverage = _coverage(forecast, observed, days, warnings)

    # --- maturity (RFC §10) ------------------------------------------------
    maturity = evaluate_maturity(
        actuals,
        policy,
        policy_errors=errors,
        projection_attribution_coverage_pct=coverage[
            "projection_attribution_coverage_pct"
        ],
        policy_spec_sha256=policy_spec_sha256,
    )

    # --- unit economics (RFC §9.3) ----------------------------------------
    unit_economics = _unit_economics(
        observed,
        actual_window_dec,
        snapshot,
        policy_complete=policy_complete,
    )

    # --- variance verdict (RFC §9.5) --------------------------------------
    variance_status = _variance_status(
        variance_pct_dec, snapshot, policy_complete=policy_complete, warnings=warnings
    )

    drivers = {
        "payg_ptu": _payg_ptu_driver(
            forecast, observed, snapshot, warnings, policy_complete=policy_complete
        )
    }

    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "status": maturity["status"],
        "variance_status": variance_status,
        "forecast_ref": {"path": forecast_path, "sha256": sha256_json(forecast)},
        "actuals_ref": {"path": actuals_path, "sha256": sha256_json(actuals)},
        "policy_ref": {
            "path": policy_path,
            "section": POLICY_SECTION,
            "spec_sha256": policy_spec_sha256,
        },
        "policy_snapshot": snapshot,
        "policy_errors": errors,
        "maturity": maturity,
        "totals": totals,
        "unit_economics": unit_economics,
        "coverage": coverage,
        "drivers": drivers,
        "warnings": warnings,
    }


def _coverage(
    forecast: dict[str, Any],
    observed: _Actuals,
    days: Optional[int],
    warnings: list[str],
) -> dict[str, object]:
    """Map observed spend onto projected resources and report both coverage
    measures.

    `projection_attribution_coverage_pct` uses GROSS ABSOLUTE cost on both
    sides — `sum(abs(resource cost)) + abs(unattributed)` as the denominator
    and `sum(abs(matched resource cost))` as the numerator — never net
    totals. A refund on an unmodeled resource would otherwise shrink the net
    denominator and push coverage toward (or past) 1.0 while the projection
    explained no more of the bill than before. `unmodeled_actual_usd`, by
    contrast, is a NET ledger amount: it is real money that must stay
    signed and must keep summing back to `actual_window_usd`.
    """
    groups = _forecast_groups(forecast, warnings)
    actual_entries = _actual_resources(observed, warnings)
    pairs = _match(groups, actual_entries, warnings)

    matched_actual_keys = {entry.match_id for _group, entry, _method in pairs}
    matched_group_ids = {group.match_id for group, _entry, _method in pairs}

    matched_resources = []
    for group, entry, method in sorted(pairs, key=lambda pair: pair[1].match_id):
        group_window = (
            group.monthly * Decimal(days) / _MONTH_DAYS if days is not None else None
        )
        matched_resources.append(
            {
                "actual_resource_id": entry.resource_id,
                "resource_type": entry.match_type,
                "forecast_resource_ids": list(group.forecast_ids),
                "forecast_deployment_ids": list(group.deployment_ids),
                "forecast_monthly_usd": _usd(
                    group.monthly, context="matched forecast_monthly_usd"
                ),
                "forecast_window_usd": _usd(
                    group_window, context="matched forecast_window_usd"
                ),
                "actual_window_usd": _usd(
                    entry.cost, context="matched actual_window_usd"
                ),
                "match_method": method,
            }
        )

    unmodeled_resources = []
    unmodeled_total = Decimal(0)
    for key in sorted(actual_entries):
        if key in matched_actual_keys:
            continue
        entry = actual_entries[key]
        unmodeled_total += entry.cost
        unmodeled_resources.append(
            {
                "resource_id": entry.resource_id,
                "resource_type": entry.match_type,
                "period_cost_usd": _usd(
                    entry.cost, context="unmodeled period_cost_usd"
                ),
            }
        )

    not_observed_resources = []
    not_observed_total = Decimal(0) if days is not None else None
    for key in sorted(groups):
        if key in matched_group_ids:
            continue
        group = groups[key]
        group_window = (
            group.monthly * Decimal(days) / _MONTH_DAYS if days is not None else None
        )
        if group_window is not None and not_observed_total is not None:
            not_observed_total += group_window
        not_observed_resources.append(
            {
                "forecast_resource_ids": list(group.forecast_ids),
                "forecast_deployment_ids": list(group.deployment_ids),
                "resource_type": group.match_type,
                "forecast_monthly_usd": _usd(
                    group.monthly, context="not-observed forecast_monthly_usd"
                ),
                "forecast_window_usd": _usd(
                    group_window, context="not-observed forecast_window_usd"
                ),
            }
        )

    gross_abs = abs(observed.unattributed_usd) + sum(
        (abs(entry.cost) for entry in actual_entries.values()), Decimal(0)
    )
    mapped_abs = sum(
        (abs(entry.cost) for _group, entry, _method in pairs), Decimal(0)
    )
    if gross_abs > 0:
        ratio = mapped_abs / gross_abs
        # Bounded defensively: the numerator is a subset of the denominator's
        # terms, so this can only ever clamp arithmetic noise, never mask a
        # real over-attribution.
        ratio = min(max(ratio, Decimal(0)), Decimal(1))
        projection_coverage = _ratio(
            ratio, context="projection_attribution_coverage_pct"
        )
    else:
        projection_coverage = None

    # Gate the RATIO, never the money. If the rows and the declared total
    # disagree, the numerator and denominator above come from evidence that
    # contradicts itself, and the most dangerous number it can produce is a
    # confident `1.0`. Every dollar below is still reported exactly as
    # observed; only the share is withdrawn.
    reconciles, reason = _cost_evidence_reconciles(observed)
    if not reconciles:
        projection_coverage = None
        _warn(
            warnings,
            f"coverage: projection_attribution_coverage_pct is null because {reason}",
        )

    return {
        "projection_attribution_coverage_pct": projection_coverage,
        "source_resource_id_coverage_pct": observed.source_coverage_pct,
        "unmodeled_actual_usd": _usd(unmodeled_total, context="unmodeled_actual_usd"),
        "forecast_not_observed_usd": _usd(
            not_observed_total, context="forecast_not_observed_usd"
        ),
        "matched_resources": matched_resources,
        "unmodeled_resources": unmodeled_resources,
        "forecast_not_observed_resources": not_observed_resources,
    }


def _unit_economics(
    observed: _Actuals,
    actual_window_dec: Optional[Decimal],
    snapshot: dict[str, object],
    *,
    policy_complete: bool,
) -> dict[str, object]:
    """Cost per successful interaction, and the two independent verdicts.

    `status` is an EVIDENCE gate: `pass` only when the actuals collection is
    verified, the declared policy is complete and error-free, the SPEC anchor
    is a valid re-derivable digest, the interaction count was actually
    observed, and there is at least one success to divide by (a divide-by-zero
    guard, not a threshold). Token metrics are attribution evidence and are
    deliberately not part of this gate.

    `target_status` is a separate COMPARISON against SPEC's declared target,
    evaluated only when `status` is `pass`.
    """
    successes = observed.successful_interactions
    verified = (
        observed.status == PASS
        and policy_complete
        and observed.interaction_status == PASS
        and successes is not None
        and successes > 0
        and actual_window_dec is not None
    )

    cost_per_interaction: Optional[float] = None
    status = NOT_VERIFIED
    target_status = NOT_VERIFIED
    target = snapshot["target_cost_per_successful_interaction_usd"]

    if verified:
        status = PASS
        cost_dec = _quantize(
            actual_window_dec / Decimal(successes),
            _RATE,
            context="cost_per_successful_interaction_usd",
        )
        cost_per_interaction = float(cost_dec)
        if target is not None:
            target_status = PASS if cost_dec <= Decimal(str(target)) else SHOULD_FIX

    return {
        "status": status,
        "successful_interactions": successes,
        "cost_per_successful_interaction_usd": cost_per_interaction,
        "target_usd": target,
        "target_status": target_status,
    }


def _variance_status(
    variance_pct: Optional[Decimal],
    snapshot: dict[str, object],
    *,
    policy_complete: bool,
    warnings: list[str],
) -> str:
    """Verdict the cost variance, or explain why it cannot be verdicted.

    This is deliberately NARROWER than the overall maturity verdict: a
    missing interaction count or thin attribution coverage degrades unit
    economics and coverage, not the cost comparison, so `variance_status`
    does not inherit `maturity.status`. What it does inherit is
    `policy_complete` — the tolerance it measures against is a SPEC-declared
    number, so a policy that is incomplete, did not parse, or cannot be
    traced to a re-derivable SPEC revision leaves nothing to verdict against.
    Consumers that need the full evidence gate read the top-level `status`
    as well — see the schema reference.
    """
    threshold = snapshot["max_forecast_variance_pct"]
    comparable, reason = _price_bases_comparable(snapshot)
    if not comparable:
        _warn(warnings, f"variance_status is not-verified: {reason}")
        return NOT_VERIFIED
    if not policy_complete:
        _warn(
            warnings,
            "variance_status is not-verified: SPEC section 14 is incomplete, "
            "did not parse cleanly, or is not anchored to a re-derivable SPEC "
            "digest, so its declared tolerance cannot gate a verdict",
        )
        return NOT_VERIFIED
    if variance_pct is None or threshold is None:
        return NOT_VERIFIED
    return PASS if abs(variance_pct) <= Decimal(str(threshold)) else SHOULD_FIX
