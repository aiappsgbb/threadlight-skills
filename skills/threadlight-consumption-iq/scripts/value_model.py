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
import math
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

# `int()`/`float()` accept far more than plain ASCII digits — underscore
# digit-grouping (`"1_000"`) and many non-ASCII Unicode decimal digits
# (e.g. Arabic-Indic `٧` for 7) both parse cleanly as of Python 3. Neither
# is acceptable input here (this is meant to be strict ASCII YAML scalar
# grammar, not "whatever Python's numeric literals happen to accept"), so
# every numeric field is gated by one of these two ASCII-only patterns
# BEFORE ever calling `int()`/`float()` — never after, since by then the
# unwanted forms have already been silently normalized away.
_INT_RE = re.compile(r"[+-]?[0-9]+")
_FLOAT_RE = re.compile(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?")

_COST_BASIS_LITERAL = "usage-pretax"

# Public: the full set of `*_price_basis` values § 14 recognizes anywhere.
# `forecast_price_basis` deliberately narrows this (see `_FORECAST_BASIS_VALUES`
# below) — `unknown` is a valid ACTUAL billing basis (we genuinely may not
# know which billing agreement priced an actual) but never a valid FORECAST
# basis (a forecast is always computed against one specific, known basis).
PRICE_BASES: tuple[str, ...] = ("retail", "ea", "mca", "unknown")
_FORECAST_BASIS_VALUES = tuple(basis for basis in PRICE_BASES if basis != "unknown")
_SCOPE_POLICY_VALUES = ("dedicated_resource_group", "tagged_allocation")

# Matches any top-level numbered SPEC heading, including lettered
# subsections such as `## 13b.` (which share section 13's leading integer).
_TOP_LEVEL_HEADING = re.compile(r"^##[ \t]+(\d+)[.\w]*\.", re.MULTILINE)
_SECTION_14_START = re.compile(r"^##[ \t]+14\.(?=[ \t]|$)[^\n]*$", re.MULTILINE)

# `\r?\n` throughout so a SPEC.md saved with CRLF line endings (e.g. edited
# on Windows) parses identically to its LF counterpart instead of silently
# failing to find the fence or key.
_OPEN_FENCE = re.compile(r"```yaml[ \t]*\r?\n")
_CLOSE_FENCE = re.compile(r"```")
# Only detects PRESENCE of the `value_model:` key — deliberately not
# anchored to a blank/comment-only tail — so a scalar body (`value_model: 5`)
# is still recognized as "key found, not a mapping" rather than being
# misreported as "key not found" (see the mapping check in
# `parse_value_model`, which is what actually tells those two cases apart).
_VALUE_MODEL_KEY = re.compile(r"^[ \t]*value_model:", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ValueModelResult:
    """Partial `value_model` policy + every validation error found.

    `policy` only ever contains fields that both parsed AND validated
    cleanly — a field that is missing, malformed, unsafe, or unknown never
    appears in it, but every such problem still gets an entry in `errors`.

    `errors` is a `list[str]` (approved public API), not a `tuple`. This
    dataclass is still `frozen=True`, so REBINDING `.errors` (or `.policy`)
    always raises `dataclasses.FrozenInstanceError` — only in-place list
    mutation (`.errors.append(...)`) is left possible, which is an accepted
    trade-off of this contract, not a bug.
    """

    policy: dict[str, object]
    errors: list[str]

    @property
    def is_complete(self) -> bool:
        """True only when `.errors` is entirely empty.

        This is a strictly BROADER contract than "every `REQUIRED_PATHS`
        leaf is present": ANY validation error at all — an unknown key, a
        duplicate key, an out-of-range value, an ambiguous/malformed yaml
        fence, an unsupported block-style list, etc. — makes this False,
        not only a required leaf that's missing. See `REQUIRED_PATHS`
        below for the narrower "which leaves must be present" contract.
        """
        return not self.errors


def _relativize_duplicate_path(path: str) -> str:
    """Strip a leading `value_model.` so a duplicate-key error path uses
    the same relative-to-`value_model` convention as every other error
    this module emits (`.policy`'s root already IS `value_model`). A bare
    `value_model` — the root key itself duplicated — is left as-is,
    matching this module's existing top-level `value_model: ...` messages.
    """
    prefix = "value_model."
    return path[len(prefix):] if path.startswith(prefix) else path


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

    parsed, duplicate_key_paths = _parse_indented_yaml(yaml_text)
    # Structural parse-time errors (duplicate keys at any level) are
    # reported first, before any field-level validation below.
    errors.extend(
        f"{_relativize_duplicate_path(path)}: duplicate key" for path in duplicate_key_paths
    )
    vm = parsed.get("value_model")
    if not isinstance(vm, dict):
        # `value_model:` IS present (guaranteed by `_extract_yaml_block`'s
        # `_VALUE_MODEL_KEY` check above) — it just isn't a mapping (a bare
        # blank leaf, or an inline scalar like `value_model: 5`). Distinct
        # from "key not found": report what's wrong with the body, not that
        # the key is missing.
        errors.append("value_model: expected a mapping")
        return ValueModelResult(policy={}, errors=errors)

    # `.policy`'s root already IS `value_model` (see `ValueModelResult`), so
    # every error path below is relative to it — never re-prefixed with
    # `value_model.`.
    for key in vm:
        if key != "cost":
            # Strict fixed v1 schema, deliberately: § 14 has no
            # forward-compatibility contract, so an unrecognized key is
            # always an error here, never silently ignored or passed
            # through (unlike e.g. `load_profile_wizard.py`'s wizard,
            # which does preserve unknown keys for forward-compat).
            errors.append(f"{key}: unknown key")

    cost = vm.get("cost")
    if cost is not None and not isinstance(cost, dict):
        errors.append("cost: expected a mapping")
        cost = None
    elif isinstance(cost, dict):
        for key in cost:
            if key not in _GROUP_FIELDS:
                # Same deliberate strict-schema rule as above, one level down.
                errors.append(f"cost.{key}: unknown key")

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
    """Return (yaml_content, errors) for section 14's ```yaml fence that
    actually contains the top-level `value_model:` key.

    Section 14 may contain more than one fenced ```yaml block (e.g. an
    earlier illustrative/decoy snippet); this scans EVERY fence rather
    than blindly assuming the first one is authoritative, and picks the
    one whose content matches `_VALUE_MODEL_KEY`. If more than one fence
    contains a `value_model:` key, which one is authoritative is
    genuinely ambiguous in the SPEC itself — that is reported as an error
    rather than silently picking "the first one". Malformed/unterminated/
    missing is reported as an error, never a raise.
    """
    fences: list[str] = []
    pos = 0
    while True:
        open_m = _OPEN_FENCE.search(section_text, pos)
        if open_m is None:
            break
        after_open = section_text[open_m.end():]
        close_m = _CLOSE_FENCE.search(after_open)
        if close_m is None:
            return None, ["value_model: unterminated ```yaml code fence in SPEC section 14"]
        fences.append(after_open[: close_m.start()])
        pos = open_m.end() + close_m.end()

    if not fences:
        return None, ["value_model: no fenced ```yaml code block found in SPEC section 14"]

    candidates = [content for content in fences if _VALUE_MODEL_KEY.search(content)]
    if not candidates:
        return None, ["value_model: `value_model:` key not found in fenced yaml block"]
    if len(candidates) > 1:
        return None, [
            "value_model: ambiguous — more than one fenced ```yaml code block in SPEC "
            "section 14 contains a top-level `value_model:` key, and no single block is "
            "authoritative"
        ]

    return candidates[0], []


# ---------------------------------------------------------------------------
# Tiny indentation parser (no PyYAML) — mappings, scalars, one flow list
# ---------------------------------------------------------------------------


class _BlockList(list):
    """Marks a block-style (`- item`) list that this parser's tiny YAML
    subset never supports — only an inline `[a, b]` flow list is. Kept as
    a distinct subtype (rather than an ordinary `list`, or silently
    dropped) so a validator can report an explicit "block lists aren't
    supported" error instead of either accepting it as if it were the
    inline form, or treating it as merely missing."""


# A container a stack entry points at is either a real, live `dict` being
# built, or `None` — a throwaway "discard" sentinel for a subtree that is
# already known to be invalid (an unrecognized duplicate key, or a scalar
# value that was illegally followed by indented children). Nothing pushed
# under a `None` container is ever attached to the real tree, and nothing
# under it is duplicate-checked (there is no legitimate key to compare
# against in the first place).


def _parse_indented_yaml(text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse a tiny YAML subset into a nested dict: mappings, scalar
    leaves, and a flow list (`[a, b, c]`) for `success_values`. Comments
    (full-line `#...` or trailing ` #...`) are stripped and are never
    values. A blank `key:` (no inline value) is a nested mapping only when
    a MORE indented line follows it; otherwise it is a blank leaf (`None`)
    — this is what lets a template's bare `key:  # comment` line mean
    "not yet filled in" rather than an empty mapping. A block-style `- `
    list under a blank `key:` is recognized (as a `_BlockList`) rather
    than silently swallowed as a blank leaf.

    Returns `(root, duplicate_key_paths)`. `duplicate_key_paths` entries
    are BARE dotted paths (no `: duplicate key` suffix yet) FROM THE TRUE
    ROOT of `text` (i.e. still prefixed with `value_model.` where
    applicable) — `parse_value_model` formats and relativizes each one the
    same way every other error is relativized.

    A repeated key at any level — mapping, scalar, or list — keeps its
    FIRST value (never last-win) and is reported once as a duplicate; the
    second occurrence's own value/children are fully discarded, never
    merged into or reparented alongside the first.
    """
    # `structural`: every non-blank, non-comment line that is either a
    # `key: val` mapping line OR a `- item` block-list line — i.e. every
    # line that can matter to indentation-based nesting. A line that is
    # neither (no colon, no dash) is structurally invisible, exactly as
    # before, and never affects adjacency decisions below.
    structural: list[tuple[int, str]] = []
    # `entries`: just the mapping lines, each carrying the index of its
    # own record in `structural` so lookahead can find "the very next
    # structurally significant line" regardless of how many non-mapping
    # (comment/blank/garbage) lines were skipped to get there.
    entries: list[tuple[int, str, str, int]] = []

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        content = line.lstrip()
        if " #" in content:
            content = content[: content.index(" #")].rstrip()

        is_dash = content.startswith("-")
        if ":" not in content and not is_dash:
            continue  # not a mapping or list line — ignore defensively

        structural.append((indent, content))
        struct_idx = len(structural) - 1
        if is_dash:
            continue  # dash items are only ever consumed as list children

        key, _, val = content.partition(":")
        entries.append((indent, key.strip(), val.strip(), struct_idx))

    root: dict[str, Any] = {}
    duplicate_errors: list[str] = []
    stack: list[tuple[int, str, dict[str, Any] | None]] = [(-1, "", root)]

    for indent, key, val, struct_idx in entries:
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent_path, parent = stack[-1][1], stack[-1][2]
        full_path = f"{parent_path}.{key}" if parent_path else key

        next_struct = structural[struct_idx + 1] if struct_idx + 1 < len(structural) else None
        follows_more_indented = next_struct is not None and next_struct[0] > indent
        follows_is_dash = follows_more_indented and next_struct[1].startswith("-")

        if parent is None:
            # Already inside a discarded (invalid) subtree: never touch a
            # real dict, and never flag a duplicate for a key that was
            # never valid to begin with. Still need to keep discarding
            # transitively so a further-nested line doesn't leak into
            # some real ancestor dict once this subtree's indent ends.
            if follows_more_indented and not follows_is_dash:
                stack.append((indent, full_path, None))
            continue

        if key in parent:
            duplicate_errors.append(full_path)
            if follows_more_indented and not follows_is_dash:
                stack.append((indent, full_path, None))
            continue

        if val == "":
            if follows_more_indented and follows_is_dash:
                items = _collect_block_list_items(structural, struct_idx, indent)
                parent[key] = _BlockList(items)
            elif follows_more_indented:
                child: dict[str, Any] = {}
                parent[key] = child
                stack.append((indent, full_path, child))
            else:
                parent[key] = None  # blank leaf — template placeholder
        else:
            parent[key] = _parse_scalar_or_list(val)
            if follows_more_indented:
                # e.g. `maturity_policy: 5` illegally followed by indented
                # children — the scalar value stands, but its children
                # must never be re-parented onto `parent` (this `key`'s
                # sibling level) as if they were `parent`'s own keys.
                stack.append((indent, full_path, None))

    return root, duplicate_errors


def _collect_block_list_items(
    structural: list[tuple[int, str]], key_struct_idx: int, key_indent: int
) -> list[str]:
    """Collect a block-style `- item` list's values, starting right after
    `key_struct_idx`, for as long as lines stay more indented than the
    key AND are themselves dash items."""
    items: list[str] = []
    i = key_struct_idx + 1
    while i < len(structural):
        line_indent, content = structural[i]
        if line_indent <= key_indent or not content.startswith("-"):
            break
        item_text = content[1:].strip()
        if item_text:
            items.append(_unquote(item_text))
        i += 1
    return items


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
        # ASCII-only numeric grammar gate: reject BEFORE calling `int()`,
        # since `int()` itself would otherwise silently accept underscore
        # digit-grouping and Unicode decimal digits (see `_INT_RE`).
        if not isinstance(raw, str) or not _INT_RE.fullmatch(raw):
            return None, [f"{path}: expected an integer >= {min_value}, got {raw!r}"]
        value = int(raw)
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
        # Same ASCII-only numeric grammar gate as `_v_int`, applied before
        # `float()` — this also rejects `"nan"`/`"inf"`/`"-inf"` outright
        # (none match `_FLOAT_RE`), so the explicit `math.isfinite` check
        # below is now belt-and-suspenders rather than the sole guard.
        if not isinstance(raw, str) or not _FLOAT_RE.fullmatch(raw):
            return None, [f"{path}: expected a float, got {raw!r}"]
        value = float(raw)
        # `float("nan")` / `float("inf")` / `float("-inf")` all parse
        # cleanly but compare False against every bound below (nan) or
        # only some (unbounded-above/-below fields) — so finiteness must
        # be checked explicitly, before any bounds comparison, or a
        # non-finite value can silently slip through as "valid".
        if not math.isfinite(value):
            return None, [f"{path}: must be a finite number, got {raw!r}"]
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
    if isinstance(raw, _BlockList):
        # `_BlockList` is a `list` subclass (see its definition), so this
        # check MUST come before the generic `isinstance(raw, list)` below
        # — otherwise a block-style list would silently fall through as if
        # it were an ordinary (supported) list. Only the inline `[a, b]`
        # flow-list syntax is supported here; report that explicitly
        # rather than treating a block list as merely missing or invalid.
        return None, [
            f"{path}: invalid — block-style (`- item`) lists are not supported, "
            "use an inline `[a, b]` list"
        ]
    if not isinstance(raw, list):
        return None, [f"{path}: expected a nonempty list, got {raw!r}"]
    if not raw:
        return None, [f"{path}: must be nonempty"]

    errs: list[str] = []
    values: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not _IDENTIFIER_RE.fullmatch(item):
            # Deliberate exception to this module's usual `<path>: ...`
            # colon grammar: the approved security contract requires the
            # contiguous substring `<path>[i] invalid` (no colon between
            # the index and `invalid`) so a reviewer/log-scanner can grep
            # for it verbatim without worrying about punctuation drift.
            errs.append(
                f"{path}[{i}] invalid: must match identifier grammar "
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
        ("actual_billing_price_basis", _v_enum(PRICE_BASES)),
        ("forecast_price_basis", _v_enum(_FORECAST_BASIS_VALUES)),
        ("allow_basis_mismatch_for_verdict", _v_bool_strict),
        ("scope_policy", _v_enum(_SCOPE_POLICY_VALUES)),
    ),
}

# Public: every dotted `cost.*` leaf path this module's fixed v1 schema
# defines — exactly the 16 leaves in `_GROUP_FIELDS`, derived from it (not
# hand-copied) so the two can never drift apart.
#
# This is NARROWER than `.is_complete`: `REQUIRED_PATHS` enumerates WHICH
# leaves must be present, but `.is_complete` (on `ValueModelResult`) is
# `not errors` — False for ANY validation error at all (an unknown key, a
# duplicate key, an out-of-range value, an ambiguous yaml fence, etc.),
# not only a `REQUIRED_PATHS` leaf that's missing. Do not treat "every
# `REQUIRED_PATHS` leaf appears in `.policy`" as equivalent to
# `.is_complete` — a fully-populated `.policy` can still coexist with a
# non-empty `.errors` (e.g. one bad UNKNOWN sibling key beside 16 good
# leaves), so callers that only spot-check `REQUIRED_PATHS` coverage
# against `.policy` will miss that kind of failure.
REQUIRED_PATHS: tuple[str, ...] = tuple(
    f"cost.{group}.{field}"
    for group, field_specs in _GROUP_FIELDS.items()
    for field, _ in field_specs
)


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
    path_prefix = f"cost.{group_name}"

    if group_raw is not None and not isinstance(group_raw, dict):
        errors.append(f"{path_prefix}: expected a mapping")
        group_raw = None

    known_fields = {name for name, _ in field_specs}
    if isinstance(group_raw, dict):
        for key in group_raw:
            if key not in known_fields:
                # Strict fixed v1 schema, deliberately (same rationale as
                # the top-level and `cost.*` unknown-key checks above): no
                # forward-compatibility passthrough for § 14.
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
