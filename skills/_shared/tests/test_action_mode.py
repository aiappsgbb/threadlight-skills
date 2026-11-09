"""Recommend-only pilots (threadlight-design Fast-PoC): the agent proposes, a person records.

A recommend-only workspace declares ``"action_mode": "recommend-only"`` in
``specs/manifest.json``. Its SPEC exposes only read tools (SPEC § 6), records
governance as not selected (§ 11a ``mode: off``) and keeps every read unbound,
so no consequential tool runs without governance.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from skills._shared import action_mode  # noqa: E402

READ_TOOL = """  - id: {name}
    consequence: read
    policy_binding: none
    enforcement_path: none
    intervention_points: []
    safe_principles: [least-privilege]
    requires: []
"""

WRITE_TOOL = """  - id: returns_record_recommendation
    consequence: write
    policy_binding: returns-recommendation-write-v1
    enforcement_path: governed-tool-gateway
    intervention_points: [pre_tool_call]
    safe_principles: [scope, audit]
    requires: [authorization, audit]
"""


def _spec(tools=("returns_get_case", "return_policy_search"), *, mode="off", contract_tools=None,
          extra_section6=""):
    section6 = "".join(f"### `{name}`\n- **Description**: read.\n- **Side Effects**: none\n\n" for name in tools)
    listed = contract_tools if contract_tools is not None else "".join(READ_TOOL.format(name=n) for n in tools)
    return (
        "# SpecKit: Returns Triage\n\n## 6. Tool Contracts\n\n" + section6 + extra_section6
        + "## 7. Knowledge Sources\n\nnone\n\n## 11. Security\n\n### 11a. Runtime Governance Contract\n\n"
        "- **Action mode**: `recommend-only`\n\n"
        "```yaml\nframework: github-copilot-sdk\n\ngovernance:\n"
        f"  mode: {mode}\n  environment_modes:\n    development: evaluate_only\n    staging: evaluate_only\n"
        "    preproduction: enforce\n    production: enforce\n  lifecycle_bindings: []\n\ntools:\n"
        + listed + "```\n\n### 11b. Hub\n\nno\n"
    )


def _workspace(tmp_path: Path, spec: str, mode="recommend-only") -> Path:
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text(spec)
    manifest = {"name": "returns-triage", "mode": "fast-poc"}
    if mode is not None:
        manifest["action_mode"] = mode
    (tmp_path / "specs" / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_reads_the_declared_action_mode(tmp_path):
    assert action_mode.read_action_mode(_workspace(tmp_path, _spec())) == "recommend-only"


def test_absent_action_mode_is_not_recommend_only(tmp_path):
    workspace = _workspace(tmp_path, _spec(), mode=None)
    assert action_mode.read_action_mode(workspace) is None
    assert action_mode.recommend_only_gaps(workspace) == []


def test_unknown_action_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="action_mode"):
        action_mode.read_action_mode(_workspace(tmp_path, _spec(), mode="maybe"))


def test_consistent_recommend_only_spec_has_no_gaps(tmp_path):
    assert action_mode.recommend_only_gaps(_workspace(tmp_path, _spec())) == []


def test_write_tool_in_contract_or_section6_is_a_gap(tmp_path):
    spec = _spec(contract_tools=READ_TOOL.format(name="returns_get_case")
                 + READ_TOOL.format(name="return_policy_search") + WRITE_TOOL, mode="selective",
                 extra_section6="### `returns_record_recommendation`\n- **Side Effects**: writes.\n\n")
    gaps = action_mode.recommend_only_gaps(_workspace(tmp_path, spec))
    assert any("returns_record_recommendation" in gap for gap in gaps), gaps
    assert any("mode" in gap and "off" in gap for gap in gaps), gaps


def test_section6_tool_missing_from_contract_is_a_gap(tmp_path):
    spec = _spec(extra_section6="### `returns_apply_refund`\n- **Side Effects**: refunds.\n\n")
    gaps = action_mode.recommend_only_gaps(_workspace(tmp_path, spec))
    assert gaps == ["action-mode: recommend-only pilot exposes SPEC § 6 tool returns_apply_refund "
                    "that SPEC § 11a does not classify as a read"]


# --- SPEC § 6 is read with the deploy parser and fails closed (review 2026-11-09) ---
# The first version only saw backticked headings and rows: plain `### tool`
# headings (examples/returns-triage-governed) returned [], so a § 6 write passed.

def _spec_with_section6(section6: str, contract_reads=("returns_get_case",)) -> str:
    return (
        "# SpecKit\n\n## 6. Tool Contracts\n\n" + section6 + "\n## 7. Knowledge Sources\n\nnone\n\n"
        "### 11a. Runtime Governance Contract\n\n```yaml\nframework: github-copilot-sdk\n\ngovernance:\n"
        "  mode: off\n  environment_modes:\n    development: evaluate_only\n    staging: evaluate_only\n"
        "    preproduction: enforce\n    production: enforce\n  lifecycle_bindings: []\n\ntools:\n"
        + "".join(READ_TOOL.format(name=n) for n in contract_reads) + "```\n"
    )


def _gaps_for(tmp_path, section6, contract_reads=("returns_get_case",)):
    return action_mode.recommend_only_gaps(_workspace(tmp_path, _spec_with_section6(section6, contract_reads)))


def test_plain_heading_write_is_a_gap(tmp_path):
    section6 = ("### returns_get_case\n- **Description**: fetch a case.\n- **Inputs**: `rma_id: string (required)`\n"
                "- **Side Effects**: none\n\n"
                "### returns_apply_decision\n- **Description**: persist the outcome.\n"
                "- **Inputs**: `rma_id, decision`\n- **Side Effects**: writes the case.\n")
    gaps = _gaps_for(tmp_path, section6)
    assert any("returns_apply_decision" in g for g in gaps), gaps
    assert not any("returns_get_case" in g for g in gaps), gaps


def test_unticked_table_row_write_is_a_gap(tmp_path):
    section6 = ("| Tool | Inputs | Description |\n|---|---|---|\n"
                "| returns_get_case | rma_id | fetch a case |\n"
                "| returns_apply_decision | rma_id, decision | persist the outcome |\n")
    gaps = _gaps_for(tmp_path, section6)
    assert any("returns_apply_decision" in g for g in gaps), gaps
    assert not any("returns_get_case" in g for g in gaps), gaps


@pytest.mark.parametrize("heading", ["### `returns_get_case` / `returns_record_outcome`",
                                     "### returns_get_case / returns_record_outcome"])
def test_multi_name_heading_names_every_tool(tmp_path, heading):
    section6 = heading + "\n- **Description**: fetch a case, or record the outcome.\n- **Inputs**: `rma_id`\n"
    gaps = _gaps_for(tmp_path, section6)
    assert any("returns_record_outcome" in g for g in gaps), gaps
    assert not any("returns_get_case" in g for g in gaps), gaps


def test_read_prefixed_tool_with_side_effects_is_a_gap_even_if_contract_says_read(tmp_path):
    section6 = ("### `get_and_reserve_case`\n- **Description**: fetch a case and reserve it.\n"
                "- **Inputs**: `rma_id: string (required)`\n- **Side Effects**: marks the case reserved.\n")
    gaps = _gaps_for(tmp_path, section6, contract_reads=("get_and_reserve_case",))
    assert any("get_and_reserve_case" in g and "side effect" in g.lower() for g in gaps), gaps


def test_unparseable_section6_entry_fails_closed(tmp_path):
    section6 = ("### `returns_get_case`\n- **Description**: fetch a case.\n- **Inputs**: `rma_id`\n\n"
                "### Record the decision on the case\n- **Description**: stores the outcome.\n")
    gaps = _gaps_for(tmp_path, section6)
    assert any("Record the decision on the case" in g for g in gaps), gaps


def test_governed_example_section6_is_parsed():
    spec = (REPO / "examples" / "returns-triage-governed" / "specs" / "SPEC.md").read_text()
    names = action_mode.section6_tools(spec)
    assert {"oms_get_order", "returns_get_case", "returns_list_open", "customer_get_profile",
            "returns_apply_decision"} <= set(names), names


def test_deploy_uses_the_shared_section6_parser():
    sys.path.insert(0, str(REPO / "skills" / "threadlight-deploy" / "scripts"))
    import generate_only
    from skills._shared import spec_tool_contracts

    assert generate_only._tool_subsections is spec_tool_contracts._tool_subsections
    assert generate_only._consequences is spec_tool_contracts._consequences
