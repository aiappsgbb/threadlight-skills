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


# --- Governed-write pilots (workshop: one governed write on the default GHCP runtime) ---
# "action_mode": "governed-write": every tool is an unbound read except exactly
# one write bound to the governed tool gateway (pre_tool_call, signed policy,
# decision receipt, idempotency). Approval is selected in the signed gateway
# registry (deferred, policy), so the contract never requires `approval`.

GW_WRITE = "returns_record_recommendation"
GW_REQUIRES = "[authorization, signed-policy-bundle, decision-receipt, idempotency-or-transaction]"


def _bound_write(name=GW_WRITE, *, requires=GW_REQUIRES, path="governed-tool-gateway", points="[pre_tool_call]",
                 binding="returns-recommendation-write-v1"):
    return (f"  - id: {name}\n    consequence: write\n    policy_binding: {binding}\n"
            f"    enforcement_path: {path}\n    intervention_points: {points}\n"
            f"    safe_principles: [scope, audit]\n    requires: {requires}\n")


GW_NOTES = (
    "- **Action mode**: `governed-write` (one write through the governed tool gateway; every other tool is an "
    "unbound read)\n"
    "- **Policy rules** (`returns-recommendation-write-v1`, implemented in Rego by threadlight-govern):\n"
    "  - `BR-001` allow: in-window return under EUR 250 with a cited RP clause.\n"
    "  - `BR-002` deny: outside the return window.\n"
    "  - `BR-003` escalate: refund above EUR 250 needs a supervisor (threadlight-review-action).\n"
    "- **Downstream service**: `POST /cases/{rma_id}/recommendation` (idempotency key `rma_id`) and "
    "`GET /cases/{rma_id}/recommendation` (outcome); it authorizes the gateway identity separately.\n"
    "- **Governance foundation**: the shared control plane, gateway, Key Vault signing key and Cosmos "
    "audit/approvals exist before deploy (deployed once per tenant by the facilitator).\n\n"
)


def _gw_spec(*, reads=("returns_get_case", "return_policy_search"), writes=(GW_WRITE,), contract=None, mode="selective",
             framework="github-copilot-sdk", notes=GW_NOTES, extra_section6="", write_effect="sets the recommendation",
             lifecycle="[]", production="enforce"):
    section6 = "".join(f"### `{n}`\n- **Description**: read.\n- **Side Effects**: none\n\n" for n in reads)
    section6 += "".join(f"### `{n}`\n- **Description**: write.\n"
                        + (f"- **Side Effects**: {write_effect}\n" if write_effect else "") + "\n" for n in writes)
    listed = contract if contract is not None else (
        "".join(READ_TOOL.format(name=n) for n in reads) + "".join(_bound_write(n) for n in writes))
    return (
        "# SpecKit: Returns Triage\n\n## 6. Tool Contracts\n\n" + section6 + extra_section6
        + "## 7. Knowledge Sources\n\nnone\n\n## 11. Security\n\n### 11a. Runtime Governance Contract\n\n" + notes
        + f"```yaml\nframework: {framework}\n\ngovernance:\n"
        f"  mode: {mode}\n  environment_modes:\n    development: evaluate_only\n    staging: evaluate_only\n"
        f"    preproduction: enforce\n    production: {production}\n  lifecycle_bindings: {lifecycle}\n\ntools:\n"
        + listed + "```\n\n### 11b. Hub\n\nno\n"
    )


def _gw_gaps(tmp_path, spec):
    return action_mode.governed_write_gaps(_workspace(tmp_path, spec, mode="governed-write"))


def test_governed_write_is_a_declared_action_mode(tmp_path):
    workspace = _workspace(tmp_path, _gw_spec(), mode="governed-write")
    assert action_mode.GOVERNED_WRITE == "governed-write"
    assert action_mode.read_action_mode(workspace) == "governed-write"
    assert action_mode.is_governed_write(workspace) and not action_mode.is_recommend_only(workspace)


def test_consistent_governed_write_spec_has_no_gaps(tmp_path):
    workspace = _workspace(tmp_path, _gw_spec(), mode="governed-write")
    assert action_mode.governed_write_gaps(workspace) == []
    assert action_mode.recommend_only_gaps(workspace) == []
    assert action_mode.governed_write_tool(workspace) == GW_WRITE


def test_maf_gateway_binding_is_accepted(tmp_path):
    assert _gw_gaps(tmp_path, _gw_spec(framework="microsoft-agent-framework")) == []


def test_other_action_modes_have_no_governed_write_gaps(tmp_path):
    for index, mode in enumerate(("recommend-only", "act", None)):
        root = tmp_path / str(index)
        root.mkdir()
        workspace = _workspace(root, _spec(), mode=mode)
        assert action_mode.governed_write_gaps(workspace) == []
        assert action_mode.governed_write_tool(workspace) is None


def test_governed_write_requires_selective_governance(tmp_path):
    spec = _gw_spec(mode="off", contract="".join(READ_TOOL.format(name=n) for n in ("returns_get_case",
                                                                                     "return_policy_search"))
                    + _bound_write().replace("policy_binding: returns-recommendation-write-v1", "policy_binding: none")
                    .replace("enforcement_path: governed-tool-gateway", "enforcement_path: none")
                    .replace("intervention_points: [pre_tool_call]", "intervention_points: []"))
    gaps = _gw_gaps(tmp_path, spec)
    assert any("mode: selective" in g for g in gaps), gaps


def test_comprehensive_mode_is_a_gap(tmp_path):
    gaps = _gw_gaps(tmp_path, _gw_spec(mode="comprehensive"))
    assert any("mode: selective" in g and "comprehensive" in g for g in gaps), gaps


def test_invalid_contract_fails_closed(tmp_path):
    gaps = _gw_gaps(tmp_path, _gw_spec(production="evaluate_only"))
    assert any("invalid SPEC § 11a" in g for g in gaps), gaps


def test_lifecycle_bindings_are_a_gap(tmp_path):
    lifecycle = ("\n    - lifecycle_point: input\n      policy_binding: input-v1\n"
                 "      enforcement_path: governed-tool-gateway\n      requires: [authorization]")
    gaps = _gw_gaps(tmp_path, _gw_spec(lifecycle=lifecycle))
    assert any("lifecycle bindings" in g for g in gaps), gaps


def test_two_bound_writes_are_a_gap(tmp_path):
    spec = _gw_spec(writes=(GW_WRITE, "returns_apply_refund"))
    gaps = _gw_gaps(tmp_path, spec)
    assert any("exactly one" in g and "returns_apply_refund" in g for g in gaps), gaps


def test_no_write_is_a_gap(tmp_path):
    gaps = _gw_gaps(tmp_path, _gw_spec(writes=()))
    assert any("exactly one" in g and "found none" in g for g in gaps), gaps


def test_unbound_write_is_a_gap(tmp_path):
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + (
        f"  - id: {GW_WRITE}\n    consequence: write\n    policy_binding: none\n    enforcement_path: none\n"
        "    intervention_points: []\n    safe_principles: [audit]\n    requires: []\n")
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any(GW_WRITE in g and "governed-tool-gateway" in g for g in gaps), gaps


@pytest.mark.parametrize("missing", ["authorization", "signed-policy-bundle", "decision-receipt",
                                     "idempotency-or-transaction"])
def test_bound_write_must_require_the_gateway_tokens(tmp_path, missing):
    tokens = [t for t in ("authorization", "signed-policy-bundle", "decision-receipt", "idempotency-or-transaction")
              if t != missing]
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + _bound_write(
        requires="[" + ", ".join(tokens) + "]")
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any(GW_WRITE in g and missing in g for g in gaps), gaps


@pytest.mark.parametrize("token", ["approval", "human-approval-record"])
def test_bound_write_must_not_require_contract_approval(tmp_path, token):
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + _bound_write(
        requires=GW_REQUIRES[:-1] + f", {token}]")
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any(GW_WRITE in g and "approval" in g and "deferred" in g for g in gaps), gaps


def test_bound_write_must_not_set_approval_required(tmp_path):
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + _bound_write(
    ) + "    approval_required: true\n"
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any(GW_WRITE in g and "approval_required" in g for g in gaps), gaps


def test_bound_write_with_unknown_consequence_is_a_gap(tmp_path):
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + _bound_write(
    ).replace("consequence: write", "consequence: unknown")
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any(GW_WRITE in g and "unknown" in g and "classif" in g for g in gaps), gaps


EXAMPLE = REPO / "examples" / "governed-write-ghcp"


def test_governed_write_reference_example_has_no_gaps():
    assert action_mode.read_action_mode(EXAMPLE) == "governed-write"
    assert action_mode.governed_write_gaps(EXAMPLE) == []
    assert action_mode.governed_write_tool(EXAMPLE) == "returns_apply_decision"
    from skills._shared.spec_tool_contracts import section6_inventory

    inventory = section6_inventory((EXAMPLE / "specs" / "SPEC.md").read_text())
    assert inventory["unparsed"] == []
    assert {t["name"]: [a["name"] for a in t["args"]] for t in inventory["tools"]} == {
        "returns_get_case": ["case_id"],
        "returns_apply_decision": ["case_id", "expected_etag", "decision", "reason"]}


def test_governed_write_reference_example_passes_the_design_validator():
    import subprocess

    validator = REPO / "skills" / "threadlight-design" / "scripts" / "validate_governance_contract.py"
    result = subprocess.run([sys.executable, str(validator), str(EXAMPLE)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "action_mode=governed-write" in result.stdout


def test_bound_write_needs_pre_tool_call(tmp_path):
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + _bound_write(
        points="[post_tool_call]")
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any(GW_WRITE in g and "pre_tool_call" in g for g in gaps), gaps


def test_local_hooks_binding_is_a_gap(tmp_path):
    contract = "".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search")) + _bound_write(
        path="local-agent-hooks")
    gaps = _gw_gaps(tmp_path, _gw_spec(framework="microsoft-agent-framework", contract=contract))
    assert any(GW_WRITE in g and "governed-tool-gateway" in g for g in gaps), gaps


def test_bound_read_is_a_gap(tmp_path):
    contract = (READ_TOOL.format(name="returns_get_case")
                + _bound_write("return_policy_search").replace("consequence: write", "consequence: read")
                + _bound_write())
    gaps = _gw_gaps(tmp_path, _gw_spec(contract=contract))
    assert any("return_policy_search" in g and "unbound read" in g for g in gaps), gaps


def test_section6_write_without_side_effect_is_a_gap(tmp_path):
    gaps = _gw_gaps(tmp_path, _gw_spec(write_effect=""))
    assert any(GW_WRITE in g and "side effect" in g for g in gaps), gaps


def test_bound_write_missing_from_section6_is_a_gap(tmp_path):
    spec = _gw_spec(writes=(), contract="".join(READ_TOOL.format(name=n) for n in ("returns_get_case",
                                                                                   "return_policy_search"))
                    + _bound_write())
    gaps = _gw_gaps(tmp_path, spec)
    assert any(GW_WRITE in g and "SPEC § 6" in g for g in gaps), gaps


def test_extra_section6_write_is_a_gap(tmp_path):
    spec = _gw_spec(extra_section6="### returns_apply_refund\n- **Description**: refund.\n"
                                   "- **Side Effects**: issues a refund.\n\n")
    gaps = _gw_gaps(tmp_path, spec)
    assert any("returns_apply_refund" in g for g in gaps), gaps


def test_read_with_side_effect_is_a_gap(tmp_path):
    spec = _gw_spec(extra_section6="### `get_and_reserve_case`\n- **Description**: fetch and reserve.\n"
                                   "- **Side Effects**: marks the case reserved.\n\n",
                    contract="".join(READ_TOOL.format(name=n) for n in ("returns_get_case", "return_policy_search",
                                                                        "get_and_reserve_case")) + _bound_write())
    gaps = _gw_gaps(tmp_path, spec)
    assert any("get_and_reserve_case" in g and "side effect" in g for g in gaps), gaps


def test_unparseable_section6_entry_fails_closed_for_governed_write(tmp_path):
    spec = _gw_spec(extra_section6="### Record the decision on the case\n- **Description**: stores it.\n\n")
    gaps = _gw_gaps(tmp_path, spec)
    assert any("Record the decision on the case" in g for g in gaps), gaps


@pytest.mark.parametrize("drop, expected", [
    ("  - `BR-003` escalate: refund above EUR 250 needs a supervisor (threadlight-review-action).\n", "escalate"),
    ("  - `BR-002` deny: outside the return window.\n", "deny"),
])
def test_policy_rules_must_cover_allow_deny_and_escalate(tmp_path, drop, expected):
    gaps = _gw_gaps(tmp_path, _gw_spec(notes=GW_NOTES.replace(drop, "")))
    assert any("Policy rules" in g and expected in g for g in gaps), gaps


def test_missing_policy_rules_is_a_gap(tmp_path):
    notes = GW_NOTES[:GW_NOTES.index("- **Policy rules**")] + GW_NOTES[GW_NOTES.index("- **Downstream service**"):]
    gaps = _gw_gaps(tmp_path, _gw_spec(notes=notes))
    assert any("Policy rules" in g for g in gaps), gaps


def test_downstream_service_needs_post_and_get(tmp_path):
    notes = GW_NOTES.replace("and `GET /cases/{rma_id}/recommendation` (outcome)", "")
    gaps = _gw_gaps(tmp_path, _gw_spec(notes=notes))
    assert any("Downstream service" in g and "GET" in g for g in gaps), gaps


def test_governance_foundation_must_be_recorded(tmp_path):
    notes = GW_NOTES[:GW_NOTES.index("- **Governance foundation**")] + "\n"
    gaps = _gw_gaps(tmp_path, _gw_spec(notes=notes))
    assert any("Governance foundation" in g for g in gaps), gaps


def test_governed_write_without_spec_is_a_gap(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "manifest.json").write_text(json.dumps({"action_mode": "governed-write"}))
    assert action_mode.governed_write_gaps(tmp_path) == ["action-mode: governed-write pilot has no specs/SPEC.md"]
