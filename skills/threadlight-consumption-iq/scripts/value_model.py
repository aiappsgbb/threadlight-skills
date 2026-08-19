"""
Parse SPEC.md § 14 (Value Model) into a partial policy dict + every
validation error found — for the design -> deploy cost-actuals
reconciliation contract.

See `skills/threadlight-design/references/value-model-schema.md` for the
full, canonical field-by-field schema and rationale this module validates,
and `examples/returns-triage-governed/specs/SPEC.md § 14` for one pilot's
real, fully-populated reference.

## Never raises on content

`parse_value_model` / `load_value_model` never raise because malformed,
missing, or unsafe POLICY CONTENT — a blank template (Fast-PoC mode), a
half-answered Full-mode section, a broken yaml fence, or a hostile
identifier — is all a *valid design-time state*, not a programming error:
the reconciliation pipeline still needs to emit raw projection/actuals
evidence with a `not-verified` verdict. Every problem becomes one entry in
`ValueModelResult.errors`, and `ValueModelResult.policy` carries whatever
DID parse and validate cleanly — a single bad field never discards its
siblings. Only `load_value_model`'s file I/O (`Path.read_text`) may raise.

## No PyYAML; a tiny hand-rolled indentation parser

Following the same pattern as `load_profile_wizard.py`'s `_parse_load_profile_yaml`
(stdlib only, no third-party yaml module), `_parse_indented_yaml` below is a
tiny indentation-based parser for the narrow YAML subset § 14 actually uses:
nested mappings, scalar leaves, and one flow list (`success_values: [...]`).
It is generalized to arbitrary nesting depth (§ 14 is four levels deep,
`load_profile_wizard.py`'s schema is two) but intentionally supports nothing
else — no block lists, multi-line scalars, or anchors.

## Unknown keys fail closed

Unlike `load_profile_wizard.py`'s `_serialize_section_12`, which explicitly
preserves unrecognized keys for forward-compatibility (it is a wizard that
writes the SPEC back), this module treats § 14 as a strict, fixed schema:
an unrecognized key at any level is a validation error, not silently
ignored or passed through. § 14 has no forward-compatibility contract —
the schema is presence.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Identifier grammar (also documented in value-model-schema.md)
# ---------------------------------------------------------------------------

# `name` / `trace_attribute` / every `success_values` entry are compiled into
# a fixed AppTraces KQL query by the reconciliation code, which never builds
# arbitrary KQL from operator input. Anything outside this grammar must be
# rejected here, before it ever reaches a query string.
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}")

_COST_BASIS_LITERAL = "usage-pretax"
_BILLING_BASIS_VALUES = ("retail", "ea", "mca", "unknown")
_FORECAST_BASIS_VALUES = ("retail", "ea", "mca")
_SCOPE_POLICY_VALUES = ("dedicated_resource_group", "tagged_allocation")

# Matches any top-level numbered SPEC heading, including lettered
# subsections such as `## 13b.` (which share section 13's leading integer).
_TOP_LEVEL_HEADING = re.compile(r"^##[ \t]+(\d+)[.\w]*\.", re.MULTILINE)
_SECTION_14_START = re.compile(r"^##[ \t]+14\.(?=[ \t]|$)[^\n]*$", re.MULTILINE)

_OPEN_FENCE = re.compile(r"```yaml[ \t]*\n")
_CLOSE_FENCE = re.compile(r"```")
_VALUE_MODEL_KEY = re.compile(r"^[ \t]*value_model:[ \t]*(#.*)?$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ValueModelResult:
    """Partial `value_model` policy + every validation error found.

    `policy` only ever contains fields that both parsed AND validated
    cleanly — a field that is missing, malformed, unsafe, or unknown never
    appears in it, but every such problem still gets an entry in `errors`.
    """

    policy: dict[str, object]
    errors: list[str]

    @property
    def is_complete(self) -> bool:
        """True only when every required field parsed and validated cleanly."""
        return not self.errors


def parse_value_model(spec_text: str) -> ValueModelResult:
    """Parse SPEC.md's raw text into a `ValueModelResult`. Never raises."""
    errors: list[str] = []

    section_text = _extract_section_14(spec_text)
    if section_text is None:
        errors.append("value_model: SPEC section 14 (Value Model) not found")
        return ValueModelResult(policy={}, errors=errors)

    yaml_text, fence_errors = _extract_yaml_block(section_text)
    if yaml_text is None:
        errors.extend(fence_errors)
        return ValueModelResult(policy={}, errors=errors)

    parsed = _parse_indented_yaml(yaml_text)
    vm = parsed.get("value_model")
    if not isinstance(vm, dict):
        errors.append("value_model: expected a mapping under `value_model:`")
        return ValueModelResult(policy={}, errors=errors)

    for key in vm:
        if key != "cost":
            errors.append(f"value_model.{key}: unknown key")

    cost = vm.get("cost")
    if cost is not None and not isinstance(cost, dict):
        errors.append("value_model.cost: expected a mapping")
        cost = None
    elif isinstance(cost, dict):
        for key in cost:
            if key not in _GROUP_FIELDS:
                errors.append(f"value_model.cost.{key}: unknown key")

    cost_policy: dict[str, Any] = {}
    for group, field_specs in _GROUP_FIELDS.items():
        group_raw = cost.get(group) if isinstance(cost, dict) else None
        group_policy = _validate_group(group_raw, group, field_specs, errors)
        if group_policy:
            cost_policy[group] = group_policy

    policy: dict[str, Any] = {}
    if cost_policy:
        policy["cost"] = cost_policy

    return ValueModelResult(policy=policy, errors=errors)


def load_value_model(spec_path: Path) -> ValueModelResult:
    """Read `spec_path` and parse its § 14. File I/O errors propagate."""
    text = spec_path.read_text(encoding="utf-8")
    return parse_value_model(text)


# ---------------------------------------------------------------------------
# Section location
# ---------------------------------------------------------------------------


def _extract_section_14(spec_text: str) -> str | None:
    """Return the body of top-level SPEC `## 14.` (Value Model), or None.

    Scans every later top-level numbered heading in document order and
    stops at the first one whose leading integer is strictly greater than
    14 — the same rule `scripts/ci/check_pilot_contract.py`'s
    `extract_section` uses. A heading with an equal or lower integer (e.g.
    a stray `## 12.` mention inside § 14's own body) is never a boundary;
    only a *strictly greater* top-level heading (`## 15.` and beyond) is.
    """
    start = _SECTION_14_START.search(spec_text)
    if start is None:
        return None

    tail = spec_text[start.end():]
    for later in _TOP_LEVEL_HEADING.finditer(tail):
        if int(later.group(1)) > 14:
            return tail[: later.start()]
    return tail


def _extract_yaml_block(section_text: str) -> tuple[str | None, list[str]]:
    """Return (yaml_content, errors) for the section's one fenced yaml block.

    Malformed/unterminated/missing is reported as an error, never a raise.
    """
    open_m = _OPEN_FENCE.search(section_text)
    if open_m is None:
        return None, ["value_model: no fenced ```yaml code block found in SPEC section 14"]

    after_open = section_text[open_m.end():]
    close_m = _CLOSE_FENCE.search(after_open)
    if close_m is None:
        return None, ["value_model: unterminated ```yaml code fence in SPEC section 14"]

    yaml_content = after_open[: close_m.start()]
    if not _VALUE_MODEL_KEY.search(yaml_content):
        return None, ["value_model: `value_model:` key not found in fenced yaml block"]

    return yaml_content, []


# ---------------------------------------------------------------------------
# Tiny indentation parser (no PyYAML) — mappings, scalars, one flow list
# ---------------------------------------------------------------------------


def _parse_indented_yaml(text: str) -> dict[str, Any]:
    """Parse a tiny YAML subset into a nested dict: mappings, scalar
    leaves, and a flow list (`[a, b, c]`) for `success_values`. Comments
    (full-line `#...` or trailing ` #...`) are stripped and are never
    values. A blank `key:` (no inline value) is a nested mapping only when
    a MORE indented line follows it; otherwise it is a blank leaf (`None`)
    — this is what lets a template's bare `key:  # comment` line mean
    "not yet filled in" rather than an empty mapping.
    """
    entries: list[tuple[int, str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        content = line.lstrip()
        if " #" in content:
            content = content[: content.index(" #")].rstrip()

        if ":" not in content:
            continue  # not a mapping line — ignore defensively

        key, _, val = content.partition(":")
        entries.append((indent, key.strip(), val.strip()))

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for i, (indent, key, val) in enumerate(entries):
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if val == "":
            next_is_nested = i + 1 < len(entries) and entries[i + 1][0] > indent
            if next_is_nested:
                child: dict[str, Any] = {}
                parent[key] = child
                stack.append((indent, child))
            else:
                parent[key] = None  # blank leaf — template placeholder
        else:
            parent[key] = _parse_scalar_or_list(val)

    return root


def _parse_scalar_or_list(val: str) -> Any:
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_unquote(item.strip()) for item in inner.split(",")]
    return _unquote(val)


def _unquote(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    return val


# ---------------------------------------------------------------------------
# Field validators — each returns (value_or_None, list_of_errors)
# ---------------------------------------------------------------------------

_Validator = Callable[[Any, str], tuple[Any, list[str]]]


def _v_int(min_value: int) -> _Validator:
    def validator(raw: Any, path: str) -> tuple[Any, list[str]]:
        if raw is None:
            return None, [f"{path}: missing"]
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None, [f"{path}: expected an integer >= {min_value}, got {raw!r}"]
        if value < min_value:
            return None, [f"{path}: must be >= {min_value}, got {value}"]
        return value, []

    return validator


def _v_float_range(
    low: float,
    high: float | None,
    low_exclusive: bool = False,
    high_exclusive: bool = False,
) -> _Validator:
    def validator(raw: Any, path: str) -> tuple[Any, list[str]]:
        if raw is None:
            return None, [f"{path}: missing"]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None, [f"{path}: expected a float, got {raw!r}"]
        if low_exclusive and value <= low:
            return None, [f"{path}: must be > {low}, got {value}"]
        if not low_exclusive and value < low:
            return None, [f"{path}: must be >= {low}, got {value}"]
        if high is not None:
            if high_exclusive and value >= high:
                return None, [f"{path}: must be < {high}, got {value}"]
            if not high_exclusive and value > high:
                return None, [f"{path}: must be <= {high}, got {value}"]
        return value, []

    return validator


def _v_identifier(raw: Any, path: str) -> tuple[Any, list[str]]:
    if raw is None:
        return None, [f"{path}: missing"]
    if not isinstance(raw, str) or not _IDENTIFIER_RE.fullmatch(raw):
        return None, [
            f"{path}: must match identifier grammar "
            f"^[A-Za-z][A-Za-z0-9_.:-]{{0,127}}$, got {raw!r}"
        ]
    return raw, []


def _v_identifier_list(raw: Any, path: str) -> tuple[Any, list[str]]:
    if raw is None:
        return None, [f"{path}: missing"]
    if not isinstance(raw, list):
        return None, [f"{path}: expected a nonempty list, got {raw!r}"]
    if not raw:
        return None, [f"{path}: must be nonempty"]

    errs: list[str] = []
    values: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not _IDENTIFIER_RE.fullmatch(item):
            errs.append(
                f"{path}[{i}]: must match identifier grammar "
                f"^[A-Za-z][A-Za-z0-9_.:-]{{0,127}}$, got {item!r}"
            )
        else:
            values.append(item)

    if errs:
        return None, errs
    return values, []


def _v_literal(literal: str) -> _Validator:
    def validator(raw: Any, path: str) -> tuple[Any, list[str]]:
        if raw is None:
            return None, [f"{path}: missing"]
        if raw != literal:
            return None, [f"{path}: must be exactly {literal!r}, got {raw!r}"]
        return raw, []

    return validator


def _v_enum(allowed: tuple[str, ...]) -> _Validator:
    def validator(raw: Any, path: str) -> tuple[Any, list[str]]:
        if raw is None:
            return None, [f"{path}: missing"]
        if raw not in allowed:
            return None, [f"{path}: must be one of {', '.join(allowed)}; got {raw!r}"]
        return raw, []

    return validator


def _v_bool_strict(raw: Any, path: str) -> tuple[Any, list[str]]:
    if raw is None:
        return None, [f"{path}: missing"]
    if raw == "true":
        return True, []
    if raw == "false":
        return False, []
    return None, [f"{path}: must be exactly true or false, got {raw!r}"]


# ---------------------------------------------------------------------------
# Schema — required paths under `value_model.cost.*`
# ---------------------------------------------------------------------------

_GROUP_FIELDS: dict[str, tuple[tuple[str, _Validator], ...]] = {
    "maturity_policy": (
        ("min_complete_days", _v_int(1)),
        ("min_successful_interactions", _v_int(1)),
        ("min_cost_settlement_age_hours", _v_int(0)),
        ("max_window_end_age_days", _v_int(1)),
        (
            "min_projection_attribution_coverage_pct",
            _v_float_range(0, 1, low_exclusive=True),
        ),
    ),
    "success_event": (
        ("name", _v_identifier),
        ("trace_attribute", _v_identifier),
        ("success_values", _v_identifier_list),
    ),
    "baseline": (
        (
            "target_cost_per_successful_interaction_usd",
            _v_float_range(0, None, low_exclusive=True),
        ),
        ("max_forecast_variance_pct", _v_float_range(0, 1)),
        ("max_token_volume_variance_pct", _v_float_range(0, 1)),
    ),
    "accounting": (
        ("actual_cost_basis", _v_literal(_COST_BASIS_LITERAL)),
        ("actual_billing_price_basis", _v_enum(_BILLING_BASIS_VALUES)),
        ("forecast_price_basis", _v_enum(_FORECAST_BASIS_VALUES)),
        ("allow_basis_mismatch_for_verdict", _v_bool_strict),
        ("scope_policy", _v_enum(_SCOPE_POLICY_VALUES)),
    ),
}


def _validate_group(
    group_raw: Any,
    group_name: str,
    field_specs: tuple[tuple[str, _Validator], ...],
    errors: list[str],
) -> dict[str, Any]:
    """Validate one `value_model.cost.<group_name>` mapping in place.

    Appends every problem to `errors` (missing/invalid/unknown-key) and
    returns only the fields that parsed and validated cleanly.
    """
    path_prefix = f"value_model.cost.{group_name}"

    if group_raw is not None and not isinstance(group_raw, dict):
        errors.append(f"{path_prefix}: expected a mapping")
        group_raw = None

    known_fields = {name for name, _ in field_specs}
    if isinstance(group_raw, dict):
        for key in group_raw:
            if key not in known_fields:
                errors.append(f"{path_prefix}.{key}: unknown key")

    result: dict[str, Any] = {}
    for field, validator in field_specs:
        raw = group_raw.get(field) if isinstance(group_raw, dict) else None
        value, errs = validator(raw, f"{path_prefix}.{field}")
        if errs:
            errors.extend(errs)
        else:
            result[field] = value

    return result
