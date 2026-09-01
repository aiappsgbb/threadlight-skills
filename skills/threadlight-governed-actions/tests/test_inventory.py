"""Tests for threadlight-governed-actions' explicit action inventory.

Exercises ``inventory.build_action_inventory`` and its component functions
against the ``conformant-maf`` fixture (a minimal, correctly-declared
Microsoft Agent Framework-style layout) and a series of small ad hoc
``tmp_path`` layouts that each isolate one rule from Task 2 of the design:
SPEC/registry/Python-tool merging, alias/consequence conflict handling,
consequence-class precedence, canonical schema hashing, explicit
owner/reversibility/compensation/approval passthrough, deterministic
ordering, SAFE-declaration reporting, and policy-file discovery.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_inventory.py -q
"""
from __future__ import annotations

import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import canonical
import contracts
import inventory


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# Fixture-writing helpers
# ---------------------------------------------------------------------------


def write_fixture_with_python_tool(root: Path, action_id: str) -> Path:
    """Write a single Python file declaring ``action_id`` via ``@tool`` only.

    No registry file and no ``specs/SPEC.md`` are written, so the action is
    discoverable solely through AST inspection of Python source — exactly
    the "Python-only" scenario the design calls out as both undeclared
    (missing from any registry) and unclassified (no explicit consequence).
    """
    module = root / "app_agent.py"
    func_name = action_id.replace(".", "_").replace("-", "_")
    module.write_text(
        textwrap.dedent(
            f"""\
            def tool(*, name):
                def decorator(func):
                    return func
                return decorator


            @tool(name="{action_id}")
            def {func_name}():
                return None
            """
        ),
        encoding="utf-8",
    )
    return module


def write_registry(root: Path, tools: list[dict], *, filename: str = "agent.yaml") -> Path:
    """Write a minimal YAML tool registry with exactly the given entries."""
    import yaml

    path = root / filename
    path.write_text(yaml.safe_dump({"tools": tools}, sort_keys=False), encoding="utf-8")
    return path


def write_spec(root: Path, section_8_body: str) -> Path:
    specs_dir = root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    path = specs_dir / "SPEC.md"
    path.write_text(
        "# Fixture SPEC\n\n## 1. Overview\n\nFiller.\n\n"
        f"## 8. Action Governance\n\n{section_8_body}\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Core merge behavior (conformant-maf fixture)
# ---------------------------------------------------------------------------


def test_inventory_merges_spec_registry_and_python_tools(fixture_root: Path) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    assert [action.action_id for action in result.actions] == [
        "customer.lookup",
        "payments.refund",
    ]
    refund = result.actions[1]
    assert refund.consequence == "irreversible"
    assert refund.execution_modes == (
        "background",
        "direct-tool",
        "interactive",
        "subagent",
    )
    assert result.findings == ()


def test_conformant_fixture_marks_refund_approval_required_from_spec(
    fixture_root: Path,
) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    by_id = {action.action_id: action for action in result.actions}
    assert by_id["payments.refund"].approval_required is True
    assert by_id["customer.lookup"].consequence == "read"
    # A mention of an action elsewhere in section 8 that is *not* one of
    # the backtick-delimited action tokens must not spuriously mark it as
    # approval-required.
    assert by_id["customer.lookup"].approval_required is None


def test_conformant_fixture_reports_all_safe_requirements_as_pass(
    fixture_root: Path,
) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    assert dict(result.safe_requirements) == {
        "authorization": "pass",
        "approval": "pass",
        "idempotency-or-transaction": "pass",
        "output-mediation": "pass",
        "audit": "pass",
    }


def test_conformant_fixture_actions_and_implementation_refs_are_populated(
    fixture_root: Path,
) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    by_id = {action.action_id: action for action in result.actions}
    assert by_id["customer.lookup"].implementation_refs == ("app/agent.py",)
    assert by_id["customer.lookup"].declaration_refs == ("agent.yaml",)
    assert by_id["customer.lookup"].source == "python+registry"


# ---------------------------------------------------------------------------
# Undeclared / unclassified action -> ACT-001 + ACT-002 (must-fix)
# ---------------------------------------------------------------------------


def test_undeclared_or_unclassified_action_is_must_fix(tmp_path: Path) -> None:
    write_fixture_with_python_tool(tmp_path, "mail.send")
    result = inventory.build_action_inventory(tmp_path)
    # No ``specs/SPEC.md`` exists at all here, so every SAFE declaration
    # also defaults to ``not-verified`` (never an inferred pass), adding a
    # third, distinct ACT-001 finding alongside the two action-specific
    # ones for the undeclared, unclassified ``mail.send`` action.
    assert {(f.finding_id, f.status) for f in result.findings} == {
        ("ACT-001", "must-fix"),
        ("ACT-002", "must-fix"),
        ("ACT-001", "not-verified"),
    }
    assert result.actions[0].action_id == "mail.send"
    assert result.actions[0].consequence is None


# ---------------------------------------------------------------------------
# Conflicting duplicate IDs -> InventoryError
# ---------------------------------------------------------------------------


def test_duplicate_id_with_conflicting_metadata_raises_inventory_error(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {"id": "dup.action", "consequence": "read", "execution_modes": ["interactive"]},
            {"id": "dup.action", "consequence": "write", "execution_modes": ["interactive"]},
        ],
    )
    with pytest.raises(inventory.InventoryError):
        inventory.parse_action_registries(tmp_path)


def test_duplicate_id_with_identical_metadata_merges_without_error(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {"id": "dup.action", "consequence": "read", "execution_modes": ["interactive"]},
            {"id": "dup.action", "consequence": "read", "execution_modes": ["interactive"]},
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    assert set(registry) == {"dup.action"}
    assert registry["dup.action"].consequence == "read"


# ---------------------------------------------------------------------------
# Duplicate aliases -> ACT-002 (finding, not an exception)
# ---------------------------------------------------------------------------


def test_duplicate_alias_across_two_actions_emits_act_002(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "orders.cancel",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            },
            {
                "id": "orders.delete",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            },
        ],
    )
    result = inventory.build_action_inventory(tmp_path)
    alias_findings = [f for f in result.findings if f.finding_id == "ACT-002"]
    assert any(
        set(f.affected_actions) == {"orders.cancel", "orders.delete"}
        for f in alias_findings
    )


# ---------------------------------------------------------------------------
# Unknown consequence string -> InventoryError
# ---------------------------------------------------------------------------


def test_unknown_consequence_string_raises_inventory_error(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [{"id": "bogus.action", "consequence": "obliterate", "execution_modes": ["interactive"]}],
    )
    with pytest.raises(inventory.InventoryError):
        inventory.parse_action_registries(tmp_path)


# ---------------------------------------------------------------------------
# Consequence precedence: irreversible > external-egress > write > read
# ---------------------------------------------------------------------------


def test_consequence_precedence_orders_primary_and_secondary(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "multi.class",
                "consequence": ["write", "irreversible", "read"],
                "execution_modes": ["interactive"],
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    record = registry["multi.class"]
    assert record.consequence == "irreversible"
    assert record.secondary_consequences == ("write", "read")


def test_consequence_precedence_external_egress_over_write(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "multi.class2",
                "consequence": ["write", "external-egress"],
                "execution_modes": ["interactive"],
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    record = registry["multi.class2"]
    assert record.consequence == "external-egress"
    assert record.secondary_consequences == ("write",)


# ---------------------------------------------------------------------------
# Canonical input/output schema hashes
# ---------------------------------------------------------------------------


def test_input_output_schema_hashes_bind_canonical_schema(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "schema.action",
                "consequence": "read",
                "execution_modes": ["interactive"],
                "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"b": {"type": "number"}}},
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    record = registry["schema.action"]
    expected_input = "sha256:" + canonical.sha256_hex(
        canonical.canonical_bytes(
            {"type": "object", "properties": {"a": {"type": "string"}}}
        )
    )
    expected_output = "sha256:" + canonical.sha256_hex(
        canonical.canonical_bytes(
            {"type": "object", "properties": {"b": {"type": "number"}}}
        )
    )
    assert record.input_schema_sha256 == expected_input
    assert record.output_schema_sha256 == expected_output


def test_schema_hash_is_key_order_independent(tmp_path: Path) -> None:
    import json

    write_registry(
        tmp_path,
        [
            {
                "id": "schema.one",
                "consequence": "read",
                "execution_modes": ["interactive"],
                "input_schema": {"a": 1, "b": 2},
            }
        ],
        filename="agent.yaml",
    )
    (tmp_path / "tool-registry.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "id": "schema.two",
                        "consequence": "read",
                        "execution_modes": ["interactive"],
                        "input_schema": {"b": 2, "a": 1},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = inventory.parse_action_registries(tmp_path)
    assert (
        registry["schema.one"].input_schema_sha256
        == registry["schema.two"].input_schema_sha256
    )


# ---------------------------------------------------------------------------
# Explicit owner / reversibility / compensation / approval fields
# ---------------------------------------------------------------------------


def test_explicit_owner_reversibility_compensation_approval_pass_through(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "payments.refund2",
                "consequence": "irreversible",
                "execution_modes": ["interactive"],
                "owner": "team-payments",
                "reversible": False,
                "compensation_ref": "runbook://refund-reversal",
                "approval_required": True,
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    record = registry["payments.refund2"]
    assert record.owner == "team-payments"
    assert record.reversible is False
    assert record.compensation_ref == "runbook://refund-reversal"
    assert record.approval_required is True


def test_missing_optional_fields_remain_none_not_inferred(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "customer.lookup2",
                "consequence": "read",
                "execution_modes": ["interactive"],
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    record = registry["customer.lookup2"]
    assert record.owner is None
    assert record.reversible is None
    assert record.compensation_ref is None
    assert record.approval_required is None


# ---------------------------------------------------------------------------
# Byte-stable action order independent of filesystem enumeration order
# ---------------------------------------------------------------------------


def test_action_order_is_byte_stable_regardless_of_file_enumeration_order(
    tmp_path: Path,
) -> None:
    # "a_module.py" sorts before "b_module.py" on every filesystem, but it
    # declares the action that should sort *last* by action_id — proving
    # the final order depends only on action_id, not discovery order.
    (tmp_path / "a_module.py").write_text(
        textwrap.dedent(
            """\
            def tool(*, name):
                def decorator(func):
                    return func
                return decorator

            @tool(name="zzz.tool")
            def zzz_tool():
                return None
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "b_module.py").write_text(
        textwrap.dedent(
            """\
            def tool(*, name):
                def decorator(func):
                    return func
                return decorator

            @tool(name="aaa.tool")
            def aaa_tool():
                return None
            """
        ),
        encoding="utf-8",
    )
    result = inventory.build_action_inventory(tmp_path)
    assert [a.action_id for a in result.actions] == ["aaa.tool", "zzz.tool"]


# ---------------------------------------------------------------------------
# discover_python_tools / discover_policy_files
# ---------------------------------------------------------------------------


def test_discover_python_tools_recognizes_function_tool_and_kernel_function(
    tmp_path: Path,
) -> None:
    (tmp_path / "mixed.py").write_text(
        textwrap.dedent(
            """\
            def function_tool(*, name=None):
                def decorator(func):
                    return func
                return decorator

            def kernel_function(*, name=None):
                def decorator(func):
                    return func
                return decorator

            def register_tool(*, name, func=None):
                return None

            @function_tool(name="alpha.action")
            def alpha():
                return None

            @kernel_function
            def beta_action():
                return None

            register_tool(name="gamma.action", func=None)
            """
        ),
        encoding="utf-8",
    )
    tool_ids = inventory.discover_python_tools(tmp_path)
    assert tool_ids == {"alpha.action", "beta_action", "gamma.action"}


def test_discover_policy_files_only_governance_and_policies_globs(
    tmp_path: Path,
) -> None:
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "risk.json").write_text("{}", encoding="utf-8")
    (tmp_path / "policies" / "nested").mkdir(parents=True)
    (tmp_path / "policies" / "nested" / "approval.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "ignored.json").write_text("{}", encoding="utf-8")

    paths = inventory.discover_policy_files(tmp_path)
    assert paths == (
        Path("governance/risk.json"),
        Path("policies/nested/approval.yaml"),
    )


# ---------------------------------------------------------------------------
# SAFE declarations: missing declaration is not-verified, never inferred pass
# ---------------------------------------------------------------------------


def test_missing_safe_declaration_is_not_verified_never_inferred_pass(
    tmp_path: Path,
) -> None:
    write_spec(
        tmp_path,
        textwrap.dedent(
            """\
            - [x] authorization
            - [x] approval
            """
        ),
    )
    result = inventory.build_action_inventory(tmp_path)
    safe = dict(result.safe_requirements)
    assert safe["authorization"] == "pass"
    assert safe["approval"] == "pass"
    assert safe["idempotency-or-transaction"] == "not-verified"
    assert safe["output-mediation"] == "not-verified"
    assert safe["audit"] == "not-verified"


def test_no_spec_file_leaves_all_safe_requirements_not_verified(tmp_path: Path) -> None:
    result = inventory.build_action_inventory(tmp_path)
    assert set(dict(result.safe_requirements).values()) == {"not-verified"}


# ---------------------------------------------------------------------------
# parse_spec_section_8: only the level-2 "8" heading, stops at next
# ---------------------------------------------------------------------------


def test_parse_spec_section_8_extracts_only_backtick_action_tokens(
    tmp_path: Path,
) -> None:
    spec_path = write_spec(
        tmp_path,
        "Requires approval: `payments.refund`. Not an action: `Hello World`. "
        "Also not: `just_one_segment`.\n\n"
        "## 9. Next\n\nMentions `should.not.count` here.",
    )
    action_ids, digest = inventory.parse_spec_section_8(spec_path)
    assert action_ids == {"payments.refund"}
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_parse_spec_section_8_returns_empty_set_when_missing(tmp_path: Path) -> None:
    action_ids, digest = inventory.parse_spec_section_8(tmp_path / "specs" / "SPEC.md")
    assert action_ids == set()
    assert digest.startswith("sha256:")


# ---------------------------------------------------------------------------
# InventoryResult is a frozen dataclass with the exact contracted shape
# ---------------------------------------------------------------------------


def test_inventory_result_is_frozen(fixture_root: Path) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    with pytest.raises(FrozenInstanceError):
        result.actions = ()  # type: ignore[misc]


def test_inventory_result_fields_have_contracted_types(fixture_root: Path) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    assert isinstance(result.actions, tuple)
    assert all(isinstance(a, contracts.ActionRecord) for a in result.actions)
    assert isinstance(result.findings, tuple)
    assert all(isinstance(f, contracts.Finding) for f in result.findings)
    assert isinstance(result.spec_section_sha256, str)
    assert isinstance(result.policy_paths, tuple)
    assert all(isinstance(p, Path) for p in result.policy_paths)


# ---------------------------------------------------------------------------
# Never invent: registry-only action (no Python implementation) is drift,
# not a silently-accepted "pass".
# ---------------------------------------------------------------------------


def test_registry_only_read_action_without_implementation_is_should_fix(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [{"id": "docs.readonly", "consequence": "read", "execution_modes": ["interactive"]}],
    )
    result = inventory.build_action_inventory(tmp_path)
    act_002 = [f for f in result.findings if f.finding_id == "ACT-002"]
    assert len(act_002) == 1
    assert act_002[0].status == "should-fix"


def test_registry_only_irreversible_action_without_implementation_is_must_fix(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "payments.refund3",
                "consequence": "irreversible",
                "execution_modes": ["interactive"],
            }
        ],
    )
    result = inventory.build_action_inventory(tmp_path)
    act_002 = [f for f in result.findings if f.finding_id == "ACT-002"]
    assert len(act_002) == 1
    assert act_002[0].status == "must-fix"


# ---------------------------------------------------------------------------
# SPEC-declared action IDs must join the union, not disappear (fix 1).
# A SPEC-only action (named in section 8 but declared in neither a
# registry nor Python) still gets exactly one inventory entry and the
# expected ACT-001 (unclassified) + ACT-002 (source-mismatch) findings.
# ---------------------------------------------------------------------------


def test_spec_only_action_appears_exactly_once_with_findings(tmp_path: Path) -> None:
    write_spec(tmp_path, "Requires approval: `reports.export`.")
    result = inventory.build_action_inventory(tmp_path)

    assert [action.action_id for action in result.actions] == ["reports.export"]
    action = result.actions[0]
    assert action.consequence is None
    assert action.approval_required is True
    assert action.source == "spec"
    assert action.declaration_refs == ()
    assert action.implementation_refs == ()

    # No SAFE checklist section is present in this fixture's SPEC, so every
    # SAFE key defaults to not-verified (never inferred as a pass), adding
    # the single aggregate ACT-001 SAFE finding (affected_actions=(), since
    # SAFE declarations are a property of section 8, not of one action)
    # alongside the two per-action findings for the undeclared,
    # unclassified `reports.export` action.
    assert {(f.finding_id, f.status) for f in result.findings} == {
        ("ACT-001", "must-fix"),
        ("ACT-002", "must-fix"),
        ("ACT-001", "not-verified"),
    }
    assert len(result.findings) == 3
    per_action_findings = [f for f in result.findings if f.affected_actions]
    assert len(per_action_findings) == 2
    for finding in per_action_findings:
        assert finding.affected_actions == ("reports.export",)


def test_spec_only_action_never_disappears_when_merged_with_other_sources(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [{"id": "alpha.only", "consequence": "read", "execution_modes": ["interactive"]}],
    )
    write_fixture_with_python_tool(tmp_path, "beta.only")
    write_spec(tmp_path, "Requires approval: `gamma.only`.")

    result = inventory.build_action_inventory(tmp_path)

    assert [action.action_id for action in result.actions] == [
        "alpha.only",
        "beta.only",
        "gamma.only",
    ]
    by_id = {action.action_id: action for action in result.actions}
    assert by_id["alpha.only"].source == "registry"
    assert by_id["beta.only"].source == "python"
    assert by_id["gamma.only"].source == "spec"


# ---------------------------------------------------------------------------
# A discovered SPEC/Python ID that matches a registry alias resolves to the
# canonical action ID (fix 2) — no phantom alias action, no false "missing
# implementation" finding for the canonical action.
# ---------------------------------------------------------------------------


def test_python_tool_registered_under_alias_resolves_to_canonical_action(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "orders.cancel",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            }
        ],
    )
    write_fixture_with_python_tool(tmp_path, "orders.remove")

    result = inventory.build_action_inventory(tmp_path)

    assert [action.action_id for action in result.actions] == ["orders.cancel"]
    action = result.actions[0]
    assert action.implementation_refs == ("app_agent.py",)
    assert action.source == "python+registry"
    # No SPEC.md is written by this fixture, so every SAFE key defaults to
    # not-verified — the resolved action itself is still clean, but the
    # single aggregate ACT-001 SAFE finding still fires for the agent.
    assert {(f.finding_id, f.status, f.reason_code) for f in result.findings} == {
        ("ACT-001", "not-verified", "safe-declaration-not-verified"),
    }


def test_spec_mention_of_alias_resolves_approval_to_canonical_action(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "orders.cancel",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            }
        ],
    )
    write_fixture_with_python_tool(tmp_path, "orders.cancel")
    write_spec(tmp_path, "Requires approval: `orders.remove`.")

    result = inventory.build_action_inventory(tmp_path)

    assert [action.action_id for action in result.actions] == ["orders.cancel"]
    action = result.actions[0]
    assert action.approval_required is True
    assert action.source == "python+registry+spec"


def test_ambiguous_alias_is_not_silently_resolved_to_either_action(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "orders.cancel",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            },
            {
                "id": "orders.delete",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            },
        ],
    )
    write_fixture_with_python_tool(tmp_path, "orders.remove")

    result = inventory.build_action_inventory(tmp_path)

    # The alias is ambiguous (claimed by two actions), so the discovered
    # "orders.remove" implementation is never guessed onto either one —
    # it surfaces as its own action instead of vanishing or being
    # misattributed.
    assert {action.action_id for action in result.actions} == {
        "orders.cancel",
        "orders.delete",
        "orders.remove",
    }
    by_id = {action.action_id: action for action in result.actions}
    assert by_id["orders.cancel"].implementation_refs == ()
    assert by_id["orders.delete"].implementation_refs == ()
    assert by_id["orders.remove"].implementation_refs == ("app_agent.py",)
    assert by_id["orders.remove"].consequence is None

    duplicate_alias_findings = [
        f for f in result.findings if f.reason_code == "duplicate-alias"
    ]
    assert len(duplicate_alias_findings) == 1
    assert set(duplicate_alias_findings[0].affected_actions) == {
        "orders.cancel",
        "orders.delete",
    }


# ---------------------------------------------------------------------------
# Missing/not-verified SAFE declarations emit a single, deterministic
# ACT-001 ``not-verified`` finding (never an inferred pass) — distinct from
# the per-action ACT-001 ``must-fix`` used for an unclassified consequence.
# ---------------------------------------------------------------------------


def test_missing_safe_declaration_emits_act_001_not_verified_finding(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [{"id": "docs.readonly", "consequence": "read", "execution_modes": ["interactive"]}],
    )
    write_fixture_with_python_tool(tmp_path, "docs.readonly")
    # Only some SAFE checklist items are declared/checked; the rest are
    # missing entirely, and one is present but unchecked.
    write_spec(
        tmp_path,
        "\n".join(
            [
                "- [x] authorization",
                "- [ ] approval",
                "- [x] idempotency-or-transaction",
            ]
        ),
    )
    result = inventory.build_action_inventory(tmp_path)

    safe_findings = [
        f
        for f in result.findings
        if f.finding_id == "ACT-001" and f.status == "not-verified"
    ]
    assert len(safe_findings) == 1
    finding = safe_findings[0]
    assert finding.reason_code == "safe-declaration-not-verified"
    # Deterministic: every not-verified SAFE key is named, in the fixed
    # catalog order, regardless of dict/set iteration order.
    assert "approval" in finding.summary
    assert "output-mediation" in finding.summary
    assert "audit" in finding.summary
    assert "authorization" not in finding.summary
    assert "idempotency-or-transaction" not in finding.summary


def test_all_safe_declarations_pass_emits_no_act_001_safe_finding(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [{"id": "docs.readonly", "consequence": "read", "execution_modes": ["interactive"]}],
    )
    write_fixture_with_python_tool(tmp_path, "docs.readonly")
    write_spec(
        tmp_path,
        "\n".join(
            [
                "- [x] authorization",
                "- [x] approval",
                "- [x] idempotency-or-transaction",
                "- [x] output-mediation",
                "- [x] audit",
            ]
        ),
    )
    result = inventory.build_action_inventory(tmp_path)
    assert not any(
        f.finding_id == "ACT-001" and f.status == "not-verified" for f in result.findings
    )


def test_conformant_fixture_declares_all_safe_requirements_and_has_no_findings(
    fixture_root: Path,
) -> None:
    result = inventory.build_action_inventory(fixture_root / "conformant-maf")
    assert dict(result.safe_requirements) == {
        "authorization": "pass",
        "approval": "pass",
        "idempotency-or-transaction": "pass",
        "output-mediation": "pass",
        "audit": "pass",
    }
    assert result.findings == ()


def test_undeclared_action_with_no_spec_reports_safe_not_verified_too(
    tmp_path: Path,
) -> None:
    """Regression guard: without any ``specs/SPEC.md`` at all, every SAFE
    key defaults to ``not-verified`` (never an inferred pass), so the
    single aggregate ACT-001 SAFE finding fires alongside the two
    action-specific findings for an undeclared, unclassified action."""
    write_fixture_with_python_tool(tmp_path, "mail.send")
    result = inventory.build_action_inventory(tmp_path)
    assert {(f.finding_id, f.status, f.reason_code) for f in result.findings} == {
        ("ACT-001", "must-fix", "unclassified-consequence"),
        ("ACT-002", "must-fix", "source-mismatch"),
        ("ACT-001", "not-verified", "safe-declaration-not-verified"),
    }


# ---------------------------------------------------------------------------
# Duplicate-alias detection normalizes alias keys, so case-only variants
# claimed by different actions are still reliably flagged as ambiguous
# (ACT-002 duplicate-alias) and remain unresolved.
# ---------------------------------------------------------------------------


def test_case_variant_duplicate_alias_is_normalized_and_flagged(
    tmp_path: Path,
) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "orders.cancel",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["Orders.Remove"],
            },
            {
                "id": "orders.delete",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            },
        ],
    )
    result = inventory.build_action_inventory(tmp_path)
    duplicate_alias_findings = [
        f for f in result.findings if f.reason_code == "duplicate-alias"
    ]
    assert len(duplicate_alias_findings) == 1
    assert set(duplicate_alias_findings[0].affected_actions) == {
        "orders.cancel",
        "orders.delete",
    }


def test_case_variant_ambiguous_alias_is_never_resolved(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "orders.cancel",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["Orders.Remove"],
            },
            {
                "id": "orders.delete",
                "consequence": "write",
                "execution_modes": ["interactive"],
                "aliases": ["orders.remove"],
            },
        ],
    )
    write_fixture_with_python_tool(tmp_path, "orders.remove")
    result = inventory.build_action_inventory(tmp_path)
    assert {action.action_id for action in result.actions} == {
        "orders.cancel",
        "orders.delete",
        "orders.remove",
    }
    by_id = {action.action_id: action for action in result.actions}
    assert by_id["orders.cancel"].implementation_refs == ()
    assert by_id["orders.delete"].implementation_refs == ()
    assert by_id["orders.remove"].implementation_refs == ("app_agent.py",)


# ---------------------------------------------------------------------------
# Direct coverage: action IDs normalize to lowercase; policy IDs are
# sorted and deduplicated (never lowercased, per the plan's explicit
# distinction between action-ID and policy-ID normalization).
# ---------------------------------------------------------------------------


def test_registry_action_id_is_normalized_to_lowercase(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "Payments.REFUND",
                "consequence": "irreversible",
                "execution_modes": ["interactive"],
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    assert set(registry) == {"payments.refund"}
    assert registry["payments.refund"].action_id == "payments.refund"


def test_python_tool_name_is_normalized_to_lowercase(tmp_path: Path) -> None:
    module = tmp_path / "app_agent.py"
    module.write_text(
        textwrap.dedent(
            """\
            def tool(*, name):
                def decorator(func):
                    return func
                return decorator


            @tool(name="Mail.SEND")
            def mail_send():
                return None
            """
        ),
        encoding="utf-8",
    )
    assert inventory.discover_python_tools(tmp_path) == {"mail.send"}


def test_policy_ids_are_sorted_and_deduplicated(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            {
                "id": "payments.refund",
                "consequence": "irreversible",
                "execution_modes": ["interactive"],
                "policy_ids": [
                    "policy.write",
                    "policy.audit",
                    "policy.write",
                    "policy.approval",
                ],
            }
        ],
    )
    registry = inventory.parse_action_registries(tmp_path)
    assert registry["payments.refund"].policy_ids == (
        "policy.approval",
        "policy.audit",
        "policy.write",
    )
