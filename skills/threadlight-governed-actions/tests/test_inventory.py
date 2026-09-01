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
    assert {(f.finding_id, f.status) for f in result.findings} == {
        ("ACT-001", "must-fix"),
        ("ACT-002", "must-fix"),
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
