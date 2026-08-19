"""
Tests for value_model.py — parse SPEC.md § 14 (Value Model) into a partial
policy dict + every validation error.

See `skills/threadlight-design/references/value-model-schema.md` for the
schema this module validates, and
`examples/returns-triage-governed/specs/SPEC.md § 14` for the canonical,
fully-populated reference this test suite is built against.

Core contract under test:
  - `parse_value_model` / `load_value_model` NEVER raise on malformed,
    missing, or unsafe POLICY CONTENT — every problem becomes an entry in
    `.errors`, and `.policy` carries whatever DID parse and validate.
  - Only `load_value_model`'s file I/O may raise (FileNotFoundError, etc).
  - Every error message begins with the exact dotted path of the field it
    concerns, e.g. `cost.baseline.max_forecast_variance_pct`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_SPEC = REPO_ROOT / "examples/returns-triage-governed/specs/SPEC.md"

from value_model import (  # noqa: E402
    PRICE_BASES,
    REQUIRED_PATHS,
    ValueModelResult,
    load_value_model,
    parse_value_model,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The exact `value_model:` yaml body from
# examples/returns-triage-governed/specs/SPEC.md § 14 — the canonical,
# fully-populated reference fixture. Every "break one field" test below
# starts from this string and mutates a single line.
CANONICAL_YAML_BODY = """\
value_model:
  cost:
    maturity_policy:
      min_complete_days: 7
      min_successful_interactions: 100
      min_cost_settlement_age_hours: 48
      max_window_end_age_days: 14
      min_projection_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
      max_token_volume_variance_pct: 0.25
    accounting:
      actual_cost_basis: usage-pretax
      actual_billing_price_basis: retail
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
"""

# The blank-template shape from speckit-template.md § 14 — every leaf key
# present, every value blank, decorated with a bounds/type comment. This is
# exactly what Fast-PoC mode emits, and it must produce one "missing" error
# per required leaf path — nothing else.
BLANK_TEMPLATE_YAML_BODY = """\
value_model:
  cost:
    maturity_policy:
      min_complete_days:                       # int >= 1
      min_successful_interactions:              # int >= 1
      min_cost_settlement_age_hours:            # int >= 0
      max_window_end_age_days:                  # int >= 1
      min_projection_attribution_coverage_pct:  # float, (0, 1]
    success_event:
      name:               # identifier
      trace_attribute:     # identifier
      success_values: []   # nonempty list of identifiers
    baseline:
      target_cost_per_successful_interaction_usd:  # float > 0
      max_forecast_variance_pct:                   # float, fractional [0, 1]
      max_token_volume_variance_pct:                # float, fractional [0, 1]
    accounting:
      actual_cost_basis: usage-pretax
      actual_billing_price_basis:        # retail | ea | mca | unknown
      forecast_price_basis:              # retail | ea | mca
      allow_basis_mismatch_for_verdict:  # bool
      scope_policy:                      # dedicated_resource_group | tagged_allocation
"""

ALL_REQUIRED_PATHS = {
    "cost.maturity_policy.min_complete_days",
    "cost.maturity_policy.min_successful_interactions",
    "cost.maturity_policy.min_cost_settlement_age_hours",
    "cost.maturity_policy.max_window_end_age_days",
    "cost.maturity_policy.min_projection_attribution_coverage_pct",
    "cost.success_event.name",
    "cost.success_event.trace_attribute",
    "cost.success_event.success_values",
    "cost.baseline.target_cost_per_successful_interaction_usd",
    "cost.baseline.max_forecast_variance_pct",
    "cost.baseline.max_token_volume_variance_pct",
    "cost.accounting.actual_cost_basis",
    "cost.accounting.actual_billing_price_basis",
    "cost.accounting.forecast_price_basis",
    "cost.accounting.allow_basis_mismatch_for_verdict",
    "cost.accounting.scope_policy",
}


def _section(yaml_body: str, heading: str = "## 14. Value Model", after: str = "") -> str:
    """Wrap a yaml body in a fenced code block under a § 14 heading."""
    return f"{heading}\n\n```yaml\n{yaml_body}```\n{after}"


def _spec(section14: str, before: str = "## 13. Assumptions\n\nSomething.\n\n---\n\n") -> str:
    return before + section14


def _error_paths(result: ValueModelResult) -> set[str]:
    return {err.split(":", 1)[0] for err in result.errors}


# ---------------------------------------------------------------------------
# Complete parse — no errors
# ---------------------------------------------------------------------------


def test_complete_canonical_fixture_parses_with_no_errors():
    result = parse_value_model(_spec(_section(CANONICAL_YAML_BODY)))

    assert result.errors == []
    assert result.is_complete is True

    cost = result.policy["cost"]
    assert cost["maturity_policy"] == {
        "min_complete_days": 7,
        "min_successful_interactions": 100,
        "min_cost_settlement_age_hours": 48,
        "max_window_end_age_days": 14,
        "min_projection_attribution_coverage_pct": 0.95,
    }
    assert cost["success_event"] == {
        "name": "return_decision_completed",
        "trace_attribute": "decision.outcome",
        "success_values": ["approved", "denied", "escalated"],
    }
    assert cost["baseline"] == {
        "target_cost_per_successful_interaction_usd": 0.18,
        "max_forecast_variance_pct": 0.20,
        "max_token_volume_variance_pct": 0.25,
    }
    assert cost["accounting"] == {
        "actual_cost_basis": "usage-pretax",
        "actual_billing_price_basis": "retail",
        "forecast_price_basis": "retail",
        "allow_basis_mismatch_for_verdict": False,
        "scope_policy": "dedicated_resource_group",
    }


def test_matches_shipped_reference_example_verbatim():
    """The real, merged reference SPEC must parse clean — this is the
    canonical fixture every other test in this file is derived from."""
    spec_text = REFERENCE_SPEC.read_text(encoding="utf-8")
    result = parse_value_model(spec_text)

    assert result.errors == []
    assert result.is_complete is True
    assert result.policy["cost"]["success_event"]["name"] == "return_decision_completed"
    assert result.policy["cost"]["accounting"]["actual_billing_price_basis"] == "retail"


# ---------------------------------------------------------------------------
# Section location: absent / boundary
# ---------------------------------------------------------------------------


def test_absent_section_is_error_and_empty_policy():
    spec_text = "## 13. Assumptions\n\nNo value model here.\n"
    result = parse_value_model(spec_text)

    assert result.policy == {}
    assert result.errors == ["value_model: SPEC section 14 (Value Model) not found"]
    assert result.is_complete is False


def test_section_15_is_a_hard_boundary():
    """Content after `## 15.` — even a decoy `value_model:` mention — must
    never leak into the parsed section 14 policy."""
    decoy = (
        "## 15. Something Else\n\n"
        "```yaml\n"
        "value_model:\n"
        "  cost:\n"
        "    maturity_policy:\n"
        "      min_complete_days: 999\n"
        "```\n"
    )
    spec_text = _spec(_section(CANONICAL_YAML_BODY, after=decoy))
    result = parse_value_model(spec_text)

    assert result.errors == []
    assert result.policy["cost"]["maturity_policy"]["min_complete_days"] == 7


def test_lower_numbered_heading_inside_body_is_not_a_boundary():
    """A stray `## 12.` mention inside § 14's body must not truncate it —
    only a STRICTLY GREATER top-level heading is a boundary."""
    body_with_stray_heading = (
        "Note: see `## 12.` for the load profile.\n\n" + _section(CANONICAL_YAML_BODY)
    )
    spec_text = _spec(body_with_stray_heading)
    result = parse_value_model(spec_text)

    assert result.errors == []
    assert result.policy["cost"]["baseline"]["max_forecast_variance_pct"] == 0.20


# ---------------------------------------------------------------------------
# Fenced yaml block: malformed / missing
# ---------------------------------------------------------------------------


def test_no_yaml_fence_at_all_is_error():
    spec_text = _spec("## 14. Value Model\n\nNothing fenced here.\n")
    result = parse_value_model(spec_text)

    assert result.policy == {}
    assert len(result.errors) == 1
    assert result.errors[0].startswith("value_model:")


def test_unterminated_fence_is_error_not_raise():
    spec_text = _spec(
        "## 14. Value Model\n\n```yaml\nvalue_model:\n  cost:\n    maturity_policy:\n"
        "      min_complete_days: 7\n"
        # No closing ``` fence before EOF.
    )
    result = parse_value_model(spec_text)

    assert result.policy == {}
    assert any("unterminated" in e.lower() for e in result.errors)


def test_fence_without_value_model_key_is_error():
    spec_text = _spec("## 14. Value Model\n\n```yaml\nsomething_else:\n  foo: bar\n```\n")
    result = parse_value_model(spec_text)

    assert result.policy == {}
    assert len(result.errors) == 1
    assert result.errors[0].startswith("value_model:")


# ---------------------------------------------------------------------------
# Blank template — every required path missing, exactly
# ---------------------------------------------------------------------------


_SUCCESS_VALUES_PATH = "cost.success_event.success_values"


def _assert_missing_or_explicit_empty_list(err: str) -> None:
    """Every path is 'missing' except `success_values`, which the template
    ships as an explicit `[]` (not blank) — a distinct, still-required-path
    error ('must be nonempty'), not a blank/missing leaf."""
    if err.startswith(f"{_SUCCESS_VALUES_PATH}:"):
        assert "nonempty" in err
    else:
        assert "missing" in err


def test_blank_template_reports_every_required_path_missing():
    """`speckit-template.md` § 14 pre-fills exactly one leaf —
    `accounting.actual_cost_basis: usage-pretax` — because it is a fixed
    literal, not an operator-confirmed value; every other leaf is blank
    (`success_values` ships as an explicit `[]`). So the blank template
    must report every OTHER required path as an error, while that one
    literal parses and validates cleanly."""
    result = parse_value_model(_spec(_section(BLANK_TEMPLATE_YAML_BODY)))

    literal_path = "cost.accounting.actual_cost_basis"
    expected_paths = ALL_REQUIRED_PATHS - {literal_path}

    assert result.is_complete is False
    assert _error_paths(result) == expected_paths
    for err in result.errors:
        _assert_missing_or_explicit_empty_list(err)

    assert result.policy == {"cost": {"accounting": {"actual_cost_basis": "usage-pretax"}}}


def test_fully_blank_section_reports_all_sixteen_required_paths():
    """With every single leaf blank — including the normally-prefilled
    `actual_cost_basis` literal — all 16 required paths must be reported,
    exactly, and the policy must be entirely empty."""
    fully_blank = BLANK_TEMPLATE_YAML_BODY.replace(
        "actual_cost_basis: usage-pretax", "actual_cost_basis:"
    )
    result = parse_value_model(_spec(_section(fully_blank)))

    assert result.policy == {}
    assert result.is_complete is False
    assert _error_paths(result) == ALL_REQUIRED_PATHS
    for err in result.errors:
        _assert_missing_or_explicit_empty_list(err)


def test_comment_only_value_is_treated_as_missing_not_as_text():
    """A field left blank with only a trailing bounds comment must be
    'missing', never mistaken for the literal comment text as a value."""
    yaml_body = CANONICAL_YAML_BODY.replace(
        "      min_complete_days: 7\n",
        "      min_complete_days:      # int >= 1\n",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == ["cost.maturity_policy.min_complete_days: missing"]
    # Every other field in the same group must still survive.
    mp = result.policy["cost"]["maturity_policy"]
    assert mp == {
        "min_successful_interactions": 100,
        "min_cost_settlement_age_hours": 48,
        "max_window_end_age_days": 14,
        "min_projection_attribution_coverage_pct": 0.95,
    }


# ---------------------------------------------------------------------------
# maturity_policy numeric bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("min_complete_days", "0"),
        ("min_successful_interactions", "0"),
        ("max_window_end_age_days", "0"),
    ],
)
def test_maturity_policy_int_fields_reject_less_than_one(field, bad_value):
    yaml_body = CANONICAL_YAML_BODY.replace(
        f"{field}: {'7' if field == 'min_complete_days' else ('100' if field == 'min_successful_interactions' else '14')}",
        f"{field}: {bad_value}",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert any(
        e.startswith(f"cost.maturity_policy.{field}:") for e in result.errors
    )
    assert field not in result.policy["cost"]["maturity_policy"]


def test_min_cost_settlement_age_hours_allows_zero():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "min_cost_settlement_age_hours: 48", "min_cost_settlement_age_hours: 0"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert result.policy["cost"]["maturity_policy"]["min_cost_settlement_age_hours"] == 0


def test_min_cost_settlement_age_hours_rejects_negative():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "min_cost_settlement_age_hours: 48", "min_cost_settlement_age_hours: -1"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.maturity_policy.min_cost_settlement_age_hours"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "min_cost_settlement_age_hours" not in result.policy["cost"]["maturity_policy"]


@pytest.mark.parametrize("bad_value,should_pass", [("0", False), ("1", True), ("1.5", False), ("-0.1", False)])
def test_projection_attribution_coverage_bounds(bad_value, should_pass):
    yaml_body = CANONICAL_YAML_BODY.replace(
        "min_projection_attribution_coverage_pct: 0.95",
        f"min_projection_attribution_coverage_pct: {bad_value}",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    field = "min_projection_attribution_coverage_pct"
    if should_pass:
        assert result.policy["cost"]["maturity_policy"][field] == float(bad_value)
    else:
        path = f"cost.maturity_policy.{field}"
        assert any(e.startswith(f"{path}:") for e in result.errors)
        assert field not in result.policy["cost"]["maturity_policy"]


def test_bool_used_for_int_field_is_rejected_not_coerced():
    """`min_complete_days: true` must be rejected as a non-integer, never
    coerced the way Python's `isinstance(True, int)` would suggest."""
    yaml_body = CANONICAL_YAML_BODY.replace("min_complete_days: 7", "min_complete_days: true")
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.maturity_policy.min_complete_days"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "min_complete_days" not in result.policy["cost"]["maturity_policy"]


# ---------------------------------------------------------------------------
# success_event: identifier grammar + nonempty list
# ---------------------------------------------------------------------------


def test_success_values_empty_list_is_invalid():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "success_values: [approved, denied, escalated]", "success_values: []"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.success_event.success_values"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    se = result.policy["cost"]["success_event"]
    assert se == {"name": "return_decision_completed", "trace_attribute": "decision.outcome"}


@pytest.mark.parametrize(
    "field,malicious",
    [
        ("name", "return'; DROP TABLE traces; --"),
        ("trace_attribute", "decision.outcome | where 1=1"),
    ],
)
def test_malicious_identifier_becomes_validation_error_not_raise(field, malicious):
    original = {
        "name": "name: return_decision_completed",
        "trace_attribute": "trace_attribute: decision.outcome",
    }[field]
    replacement = f"{field}: {malicious}"
    yaml_body = CANONICAL_YAML_BODY.replace(original, replacement)

    # Must not raise.
    result = parse_value_model(_spec(_section(yaml_body)))

    path = f"cost.success_event.{field}"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert field not in result.policy["cost"]["success_event"]


def test_malicious_identifier_inside_success_values_item_is_rejected():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "success_values: [approved, denied, escalated]",
        "success_values: [approved, denied; DROP TABLE traces, escalated]",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.success_event.success_values"
    assert any(e.startswith(f"{path}") for e in result.errors)
    assert "success_values" not in result.policy["cost"]["success_event"]


def test_identifier_grammar_accepts_dots_colons_dashes_underscores():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "trace_attribute: decision.outcome", "trace_attribute: decision.outcome:v1-2_3"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert (
        result.policy["cost"]["success_event"]["trace_attribute"] == "decision.outcome:v1-2_3"
    )


# ---------------------------------------------------------------------------
# baseline: cost + independent token-volume variance
# ---------------------------------------------------------------------------


def test_target_cost_must_be_greater_than_zero():
    for bad in ("0", "-0.5"):
        yaml_body = CANONICAL_YAML_BODY.replace(
            "target_cost_per_successful_interaction_usd: 0.18",
            f"target_cost_per_successful_interaction_usd: {bad}",
        )
        result = parse_value_model(_spec(_section(yaml_body)))
        path = "cost.baseline.target_cost_per_successful_interaction_usd"
        assert any(e.startswith(f"{path}:") for e in result.errors), bad
        assert "target_cost_per_successful_interaction_usd" not in result.policy["cost"]["baseline"]


@pytest.mark.parametrize("bad_value", ["1.5", "20", "-0.1"])
def test_max_forecast_variance_pct_rejects_out_of_bounds_including_over_one(bad_value):
    yaml_body = CANONICAL_YAML_BODY.replace(
        "max_forecast_variance_pct: 0.20", f"max_forecast_variance_pct: {bad_value}"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.baseline.max_forecast_variance_pct"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "max_forecast_variance_pct" not in result.policy["cost"]["baseline"]
    # The independent token-volume threshold must be unaffected.
    assert result.policy["cost"]["baseline"]["max_token_volume_variance_pct"] == 0.25


def test_max_token_volume_variance_pct_is_an_independent_threshold():
    """Breaking the token-volume variance must not affect the cost variance,
    and the two fields must never be substituted for one another."""
    yaml_body = CANONICAL_YAML_BODY.replace(
        "max_token_volume_variance_pct: 0.25", "max_token_volume_variance_pct: 1.5"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.baseline.max_token_volume_variance_pct"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "max_token_volume_variance_pct" not in result.policy["cost"]["baseline"]
    # The cost-variance field survives unchanged — never substituted.
    assert result.policy["cost"]["baseline"]["max_forecast_variance_pct"] == 0.20


def test_variance_fields_at_exact_bounds_are_valid():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "max_forecast_variance_pct: 0.20", "max_forecast_variance_pct: 1.0"
    ).replace("max_token_volume_variance_pct: 0.25", "max_token_volume_variance_pct: 0")

    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert result.policy["cost"]["baseline"]["max_forecast_variance_pct"] == 1.0
    assert result.policy["cost"]["baseline"]["max_token_volume_variance_pct"] == 0.0


# ---------------------------------------------------------------------------
# accounting: literal / enums / strict bool
# ---------------------------------------------------------------------------


def test_actual_cost_basis_is_a_fixed_literal():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "actual_cost_basis: usage-pretax", "actual_cost_basis: usage-posttax"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.accounting.actual_cost_basis"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "actual_cost_basis" not in result.policy["cost"]["accounting"]


@pytest.mark.parametrize("value", ["retail", "ea", "mca", "unknown"])
def test_actual_billing_price_basis_accepts_all_four_values(value):
    yaml_body = CANONICAL_YAML_BODY.replace(
        "actual_billing_price_basis: retail", f"actual_billing_price_basis: {value}"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert result.policy["cost"]["accounting"]["actual_billing_price_basis"] == value


def test_actual_billing_price_basis_rejects_unlisted_value():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "actual_billing_price_basis: retail", "actual_billing_price_basis: spot"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.accounting.actual_billing_price_basis"
    assert any(e.startswith(f"{path}:") for e in result.errors)


@pytest.mark.parametrize("value", ["retail", "ea", "mca"])
def test_forecast_price_basis_accepts_only_three_values(value):
    yaml_body = CANONICAL_YAML_BODY.replace(
        "forecast_price_basis: retail", f"forecast_price_basis: {value}"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert result.policy["cost"]["accounting"]["forecast_price_basis"] == value


def test_forecast_price_basis_rejects_unknown_unlike_billing_basis():
    """`unknown` is valid for `actual_billing_price_basis` but NOT for
    `forecast_price_basis` — the two enums must not be conflated."""
    yaml_body = CANONICAL_YAML_BODY.replace(
        "forecast_price_basis: retail", "forecast_price_basis: unknown"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.accounting.forecast_price_basis"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "forecast_price_basis" not in result.policy["cost"]["accounting"]


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_allow_basis_mismatch_accepts_strict_lowercase_bool(value, expected):
    yaml_body = CANONICAL_YAML_BODY.replace(
        "allow_basis_mismatch_for_verdict: false", f"allow_basis_mismatch_for_verdict: {value}"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert result.policy["cost"]["accounting"]["allow_basis_mismatch_for_verdict"] is expected


@pytest.mark.parametrize("value", ["True", "FALSE", "yes", "no", "1", "0"])
def test_allow_basis_mismatch_rejects_non_strict_bool_spellings(value):
    """Only exactly `true`/`false` are valid — no yes/no/1/0/Title-case
    coercion, unlike load_profile_wizard's looser bool parsing."""
    yaml_body = CANONICAL_YAML_BODY.replace(
        "allow_basis_mismatch_for_verdict: false", f"allow_basis_mismatch_for_verdict: {value}"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.accounting.allow_basis_mismatch_for_verdict"
    assert any(e.startswith(f"{path}:") for e in result.errors)
    assert "allow_basis_mismatch_for_verdict" not in result.policy["cost"]["accounting"]


@pytest.mark.parametrize("value", ["dedicated_resource_group", "tagged_allocation"])
def test_scope_policy_accepts_both_values(value):
    yaml_body = CANONICAL_YAML_BODY.replace(
        "scope_policy: dedicated_resource_group", f"scope_policy: {value}"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert result.errors == []
    assert result.policy["cost"]["accounting"]["scope_policy"] == value


def test_scope_policy_rejects_unlisted_value():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "scope_policy: dedicated_resource_group", "scope_policy: subscription_wide"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    path = "cost.accounting.scope_policy"
    assert any(e.startswith(f"{path}:") for e in result.errors)


# ---------------------------------------------------------------------------
# Unknown keys — fail closed (strict fixed schema)
# ---------------------------------------------------------------------------


def test_unknown_key_directly_under_value_model_is_an_error():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "value_model:\n  cost:", "value_model:\n  extra_top_level_key: nope\n  cost:"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert "extra_top_level_key: unknown key" in result.errors
    # The rest of the (valid) policy must still survive.
    assert result.policy["cost"]["maturity_policy"]["min_complete_days"] == 7


def test_unknown_key_directly_under_cost_is_an_error():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "  cost:\n    maturity_policy:",
        "  cost:\n    unexpected_group:\n      foo: bar\n    maturity_policy:",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert "cost.unexpected_group: unknown key" in result.errors
    assert result.policy["cost"]["maturity_policy"]["min_complete_days"] == 7


def test_unknown_key_inside_a_group_is_an_error_but_siblings_survive():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "    accounting:\n      actual_cost_basis: usage-pretax",
        "    accounting:\n      surprise_field: 1\n      actual_cost_basis: usage-pretax",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert "cost.accounting.surprise_field: unknown key" in result.errors
    accounting = result.policy["cost"]["accounting"]
    assert accounting["actual_cost_basis"] == "usage-pretax"
    assert accounting["scope_policy"] == "dedicated_resource_group"


# ---------------------------------------------------------------------------
# Malicious success_values item — exact error text (KQL-injection fixture)
# ---------------------------------------------------------------------------

# A real KQL-injection attempt: close the string literal, `union` in another
# table, then reopen a dummy comparison. `_v_identifier_list` must reject
# this outright (it never matches the identifier grammar) and the exact
# error text must name the offending index so an operator/reviewer can spot
# it immediately, even though the grammar detail may be appended after it.
_ATTACK_FIXTURE = 'approved") | union AppRequests | where ("x" == "x'


def test_malicious_success_value_produces_exact_invalid_error_with_index():
    yaml_body = CANONICAL_YAML_BODY.replace(
        "success_values: [approved, denied, escalated]",
        f"success_values: [{_ATTACK_FIXTURE}]",
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert any(
        "cost.success_event.success_values[0] invalid" in e for e in result.errors
    )
    assert "success_values" not in result.policy["cost"]["success_event"]


# ---------------------------------------------------------------------------
# Non-finite numerics (nan / NaN / inf / -inf) rejected before bounds
# ---------------------------------------------------------------------------

# (group, field, canonical-value) for every numeric leaf — both integer and
# float field classes — so nan/inf rejection is proven across the whole
# numeric surface, not just one validator.
_NUMERIC_FIELDS = [
    ("maturity_policy", "min_complete_days", "7"),
    ("maturity_policy", "min_successful_interactions", "100"),
    ("maturity_policy", "min_cost_settlement_age_hours", "48"),
    ("maturity_policy", "max_window_end_age_days", "14"),
    ("maturity_policy", "min_projection_attribution_coverage_pct", "0.95"),
    ("baseline", "target_cost_per_successful_interaction_usd", "0.18"),
    ("baseline", "max_forecast_variance_pct", "0.20"),
    ("baseline", "max_token_volume_variance_pct", "0.25"),
]


@pytest.mark.parametrize("non_finite", ["nan", "NaN", "inf", "-inf"])
@pytest.mark.parametrize("group,field,original", _NUMERIC_FIELDS)
def test_numeric_fields_reject_every_non_finite_value(group, field, original, non_finite):
    """Every numeric field — int or float — must reject nan/NaN/inf/-inf,
    and the rejected value must never be retained in `.policy` (nan/inf
    otherwise compare False against every bound, silently passing)."""
    yaml_body = CANONICAL_YAML_BODY.replace(f"{field}: {original}", f"{field}: {non_finite}")
    result = parse_value_model(_spec(_section(yaml_body)))

    path = f"cost.{group}.{field}"
    assert any(e.startswith(f"{path}:") for e in result.errors), (group, field, non_finite)
    assert field not in result.policy["cost"][group]


# ---------------------------------------------------------------------------
# CRLF support
# ---------------------------------------------------------------------------


def test_crlf_line_endings_parse_identically_to_lf():
    """The section heading, fence markers, and `value_model:` key regexes
    must all accept `\\r?\\n` — a SPEC.md saved/edited on Windows must parse
    exactly like its LF counterpart, not lose the whole section."""
    lf_text = _spec(_section(CANONICAL_YAML_BODY))
    crlf_text = lf_text.replace("\n", "\r\n")

    result = parse_value_model(crlf_text)

    assert result.errors == []
    assert result.is_complete is True
    assert result.policy == parse_value_model(lf_text).policy


# ---------------------------------------------------------------------------
# `value_model:` present but not a mapping (key presence vs. mapping body)
# ---------------------------------------------------------------------------


def test_value_model_scalar_value_reports_expected_mapping_not_key_missing():
    """`value_model: 5` — the key IS present, its body just isn't a
    mapping. The parser must not conflate this with the key being absent
    entirely (a wholly different, `not found`-style error)."""
    spec_text = _spec("## 14. Value Model\n\n```yaml\nvalue_model: 5\n```\n")
    result = parse_value_model(spec_text)

    assert result.policy == {}
    assert len(result.errors) == 1
    message = result.errors[0].lower()
    assert "expected a mapping" in message
    assert "not found" not in message
    assert "missing" not in message


# ---------------------------------------------------------------------------
# Public constants — REQUIRED_PATHS / PRICE_BASES
# ---------------------------------------------------------------------------


def test_required_paths_is_a_tuple_of_exactly_the_sixteen_cost_paths():
    assert isinstance(REQUIRED_PATHS, tuple)
    assert len(REQUIRED_PATHS) == 16
    assert len(set(REQUIRED_PATHS)) == 16  # no duplicates
    assert set(REQUIRED_PATHS) == ALL_REQUIRED_PATHS


def test_required_paths_are_all_actually_required_by_the_blank_template():
    """Every path in REQUIRED_PATHS must be exactly the set of paths the
    fully-blank template reports as missing — proving the constant isn't
    just decorative, it maps onto the actual required-field contract."""
    fully_blank = BLANK_TEMPLATE_YAML_BODY.replace(
        "actual_cost_basis: usage-pretax", "actual_cost_basis:"
    )
    result = parse_value_model(_spec(_section(fully_blank)))

    assert _error_paths(result) == set(REQUIRED_PATHS)


def test_price_bases_constant_is_pinned():
    assert PRICE_BASES == ("retail", "ea", "mca", "unknown")


def test_price_bases_used_by_actual_billing_price_basis_accepts_all_four():
    for value in PRICE_BASES:
        yaml_body = CANONICAL_YAML_BODY.replace(
            "actual_billing_price_basis: retail", f"actual_billing_price_basis: {value}"
        )
        result = parse_value_model(_spec(_section(yaml_body)))
        assert result.errors == [], value
        assert result.policy["cost"]["accounting"]["actual_billing_price_basis"] == value


def test_forecast_price_basis_excludes_unknown_though_it_is_in_price_bases():
    """`forecast_price_basis`'s internal enum may exclude `unknown` even
    though it's a member of the public `PRICE_BASES` tuple — the two enums
    are deliberately not the same set."""
    assert "unknown" in PRICE_BASES
    yaml_body = CANONICAL_YAML_BODY.replace(
        "forecast_price_basis: retail", "forecast_price_basis: unknown"
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert any(e.startswith("cost.accounting.forecast_price_basis:") for e in result.errors)


# ---------------------------------------------------------------------------
# Partial-policy survival across multiple simultaneous errors
# ---------------------------------------------------------------------------


def test_partial_policy_survives_multiple_simultaneous_errors():
    """Break one field in each of the four groups at once. Every OTHER
    field — across all four groups — must still show up in `.policy`,
    while every broken field produces its own error."""
    yaml_body = (
        CANONICAL_YAML_BODY.replace("min_complete_days: 7", "min_complete_days: 0")
        .replace("success_values: [approved, denied, escalated]", "success_values: []")
        .replace("max_forecast_variance_pct: 0.20", "max_forecast_variance_pct: 5")
        .replace("forecast_price_basis: retail", "forecast_price_basis: unknown")
    )
    result = parse_value_model(_spec(_section(yaml_body)))

    assert len(result.errors) == 4
    assert result.is_complete is False

    cost = result.policy["cost"]
    assert "min_complete_days" not in cost["maturity_policy"]
    assert cost["maturity_policy"]["min_successful_interactions"] == 100
    assert cost["maturity_policy"]["min_cost_settlement_age_hours"] == 48

    assert "success_values" not in cost["success_event"]
    assert cost["success_event"]["name"] == "return_decision_completed"

    assert "max_forecast_variance_pct" not in cost["baseline"]
    assert cost["baseline"]["max_token_volume_variance_pct"] == 0.25
    assert cost["baseline"]["target_cost_per_successful_interaction_usd"] == 0.18

    assert "forecast_price_basis" not in cost["accounting"]
    assert cost["accounting"]["actual_billing_price_basis"] == "retail"
    assert cost["accounting"]["scope_policy"] == "dedicated_resource_group"


# ---------------------------------------------------------------------------
# load_value_model: file I/O
# ---------------------------------------------------------------------------


def test_load_value_model_reads_file_and_matches_parse_value_model(tmp_path):
    spec_path = tmp_path / "SPEC.md"
    spec_path.write_text(_spec(_section(CANONICAL_YAML_BODY)), encoding="utf-8")

    from_file = load_value_model(spec_path)
    from_text = parse_value_model(spec_path.read_text(encoding="utf-8"))

    assert from_file.policy == from_text.policy
    assert from_file.errors == from_text.errors


def test_load_value_model_raises_file_not_found(tmp_path):
    missing = tmp_path / "does-not-exist" / "SPEC.md"

    with pytest.raises(FileNotFoundError):
        load_value_model(missing)


def test_load_value_model_propagates_os_error_reading_a_directory(tmp_path):
    with pytest.raises(OSError):
        load_value_model(tmp_path)


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


def test_result_is_frozen_dataclass_with_expected_fields():
    result = parse_value_model(_spec(_section(CANONICAL_YAML_BODY)))
    assert isinstance(result, ValueModelResult)
    with pytest.raises(Exception):
        result.errors = []  # type: ignore[misc]  # frozen — must reject mutation
