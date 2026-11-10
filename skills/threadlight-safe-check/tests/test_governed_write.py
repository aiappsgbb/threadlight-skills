"""Governed-write pilots: one write bound to the governed tool gateway, every other tool an unbound read.

``"action_mode": "governed-write"`` selects governance (SPEC 11a ``mode: selective``)
for exactly one write. safe-check reports an ``action-mode`` gap when the design
is not consistent (an unbound or second write, missing gateway requirements) and
keeps its ``governance:`` gaps for the selected binding: invalid or unpackaged
selected configuration is never treated as off.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for directory in ("threadlight-safe-check/scripts", "threadlight-safe-check/references"):
    sys.path.insert(0, str(ROOT / "skills" / directory))

import safe_check  # noqa: E402

READ = ("  - id: {0}\n    consequence: read\n    policy_binding: none\n    enforcement_path: none\n"
        "    intervention_points: []\n    safe_principles: [least-privilege]\n    requires: []\n")
BOUND = ("  - id: {0}\n    consequence: write\n    policy_binding: {0}-v1\n    enforcement_path: governed-tool-gateway\n"
         "    intervention_points: [pre_tool_call]\n    safe_principles: [scope, audit]\n"
         "    requires: [authorization, signed-policy-bundle, decision-receipt, idempotency-or-transaction]\n")
NOTES = (
    "- **Policy rules** (`returns_record_recommendation-v1`):\n"
    "  - `BR-001` allow: in-window return.\n  - `BR-002` deny: outside the window.\n"
    "  - `BR-003` escalate: above EUR 250.\n"
    "- **Downstream service**: `POST /recommendations` and `GET /recommendations/{rma_id}`.\n"
    "- **Governance foundation**: shared control plane, gateway, Key Vault key and Cosmos audit exist before deploy.\n\n"
)


def _project(tmp_path, *, writes=("returns_record_recommendation",)):
    reads = ["returns_get_case", "return_policy_search"]
    spec = ("# SpecKit\n\n## 6. Tool Contracts\n\n"
            + "".join(f"### `{t}`\n- **Side Effects**: none\n\n" for t in reads)
            + "".join(f"### `{t}`\n- **Side Effects**: writes the case\n\n" for t in writes)
            + "## 11. Security\n\n### 11a. Runtime Governance Contract\n\n" + NOTES
            + "```yaml\nframework: github-copilot-sdk\ngovernance:\n  mode: selective\n  environment_modes: "
            "{development: evaluate_only, staging: evaluate_only, preproduction: enforce, production: enforce}\n"
            "  lifecycle_bindings: []\ntools:\n" + "".join(READ.format(t) for t in reads)
            + "".join(BOUND.format(t) for t in writes) + "```\n")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text(spec)
    manifest = {"name": "returns-triage", "mode": "fast-poc", "action_mode": "governed-write",
                "deployment_manifest": {"module_selectors": {}, "services": [], "expected_resource_types": []}}
    path = tmp_path / "specs" / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def _gaps(tmp_path, path):
    output = tmp_path / "predeploy.json"
    safe_check.phase_predeploy(tmp_path, path, output)
    return json.loads(output.read_text())["gaps"]


def test_consistent_governed_write_has_no_action_mode_gap_but_keeps_governance_gaps(tmp_path):
    gaps = _gaps(tmp_path, _project(tmp_path))
    assert not [g for g in gaps if g.startswith("action-mode")], gaps
    assert [g for g in gaps if g.startswith("governance")], gaps


def test_second_write_is_an_action_mode_gap(tmp_path):
    gaps = _gaps(tmp_path, _project(tmp_path, writes=("returns_record_recommendation", "returns_apply_refund")))
    assert [g for g in gaps if g.startswith("action-mode: governed-write") and "returns_apply_refund" in g], gaps
