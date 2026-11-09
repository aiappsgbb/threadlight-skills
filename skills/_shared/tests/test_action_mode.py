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
