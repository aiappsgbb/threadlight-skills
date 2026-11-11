"""SPEC § 6 read/write classification (shared by threadlight-deploy and Pattern 0).

Explicit § 11a / § 5b classification wins. Without it, a declared mutation in
**Side Effects** makes a tool a write; common "no effect" phrasings do not; and
the name fallback accepts a read verb as the first or the second token
(`get_case`, `returns_get_case`), as Pattern 0 always did.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from skills._shared.spec_tool_contracts import declared_side_effects, tools  # noqa: E402


def _kind(name, side_effects=None, contract=None):
    effects = f"- **Side Effects**: {side_effects}\n" if side_effects is not None else ""
    spec = f"## 6. Tool Contracts\n\n### `{name}`\n- **Description**: tool.\n{effects}\n"
    if contract:
        spec += f"## 11a. Runtime Governance Contract\n\n```yaml\ntools:\n  - id: {name}\n    consequence: {contract}\n```\n"
    return tools(spec)[0]["kind"]


@pytest.mark.parametrize("text", [
    "none", "None", "None (read-only)", "n/a", "N/A.", "No side effects", "No side effect.",
    "No state changes", "No state change", "No writes", "No write", "read-only", "Read only.",
    "No mutations", "No state changes (lookup only).",
])
def test_no_effect_phrasings_are_not_side_effects(text):
    assert declared_side_effects(text) == ""
    assert _kind("returns_get_case", text) == "read"


@pytest.mark.parametrize("text", [
    "Writes the case.", "Updates the case status.", "Calls the OMS API (read)",
    "None; marks the case reserved.", "No refund, but stores the recommendation on the case.",
])
def test_declared_or_ambiguous_effects_make_a_write(text):
    assert declared_side_effects(text) != ""
    assert _kind("returns_get_case", text) == "write"


@pytest.mark.parametrize("name, kind", [
    ("get_case", "read"), ("returns_get_case", "read"), ("customer_get_profile", "read"),
    ("returns_list_open", "read"), ("oms_lookup_order", "read"),
    ("returns_record_recommendation", "write"), ("returns_apply_decision", "write"),
    ("record_get_case", "read"), ("budget_target_set", "write"),
])
def test_name_fallback_accepts_a_read_verb_in_the_first_or_second_token(name, kind):
    assert _kind(name) == kind


def test_explicit_contract_classification_wins():
    assert _kind("returns_get_case", contract="write") == "write"
    assert _kind("returns_record_recommendation", "No writes", contract="read") == "read"



def test_generated_many_flag_is_unchanged_by_the_second_token_rule():
    # examples/returns-triage-governed must generate exactly as before.
    spec = "## 6. Tool Contracts\n\n### `returns_list_open`\n- **Description**: list.\n"
    plan = tools(spec)[0]
    assert plan["kind"] == "read" and plan["many"] is False


def test_governed_example_plan_is_unchanged():
    # generate_only builds examples/returns-triage-governed from this plan; pinned
    # from the parser before the 2026-11-09 classification fix.
    spec = (REPO / "examples" / "returns-triage-governed" / "specs" / "SPEC.md").read_text()
    assert [(t["name"], t["kind"], t["many"]) for t in tools(spec)] == [
        ("oms_get_order", "read", False), ("returns_get_case", "read", False),
        ("returns_list_open", "read", False), ("customer_get_profile", "read", False),
        ("returns_apply_decision", "write", False)]


# --- Fail closed: a no-effect opener followed by any further clause is an effect ---
# Review 2026-11-09: the verb list let "None; issues a refund" through as a read.

@pytest.mark.parametrize("text", [
    "No state change; emails the customer",
    "No writes to the DB; posts a message to Teams",
    "No changes; submits the claim",
    "Read-only except it closes the case",
    "None; issues a refund to the customer",
    "No side effects except charging the card",
    "None, but notifies the supervisor",
    "None: pages the on-call engineer",
    "No writes — triggers a webhook",
    "Read-only besides logging the decision",
    "No side effects other than an audit entry",
    "None apart from a cache refresh",
    "Read-only; however it flags the case",
    "None and archives the file",
    "None (no refund)",
])
def test_opener_plus_any_clause_is_a_declared_effect(text):
    assert declared_side_effects(text) == text
    assert _kind("returns_get_case", text) == "write"


@pytest.mark.parametrize("text", [
    "None", "None.", "N/A", "n/a.", "No side effects (read-only lookup)", "Read-only.", "-", "—",
    "No state changes (lookup only).", "None (read-only)",
    # threadlight-design Fast-PoC wording (2026-11-09 returns-triage): a trailing
    # clause made only of pure qualifiers adds no effect.
    "none; read only.", "None, read-only.", "None; read-only lookup",
])
def test_clean_openers_are_no_effect(text):
    assert declared_side_effects(text) == ""


@pytest.mark.parametrize("text", [
    "none; read only except it closes the case", "None; read only, then posts to Teams",
    "None, reads and reserves the order",
])
def test_pure_trailing_clause_cannot_hide_an_effect(text):
    assert declared_side_effects(text) == text
    assert _kind("returns_get_case", text) == "write"


def test_name_fallback_cannot_hide_a_refund():
    assert _kind("case_lookup_and_refund", "None; issues a refund") == "write"
