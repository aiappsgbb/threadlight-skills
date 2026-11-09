"""Recommend-only pilots (threadlight-design Fast-PoC): no governance gap, no consequential tool.

``"action_mode": "recommend-only"`` means the agent proposes and a person records,
with SPEC 11a governance not selected (``mode: off``) and reads unbound. safe-check
reports no governance gap for it, and an ``action-mode`` gap if a consequential
tool is exposed anyway (it would run unbound).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for directory in ("threadlight-safe-check/scripts", "threadlight-safe-check/references"):
    sys.path.insert(0, str(ROOT / "skills" / directory))

import safe_check  # noqa: E402

READ = ("  - id: {0}\n    consequence: read\n    policy_binding: none\n    enforcement_path: none\n"
        "    intervention_points: []\n    safe_principles: [least-privilege]\n    requires: []\n")
WRITE = ("  - id: returns_record_recommendation\n    consequence: write\n    policy_binding: none\n"
         "    enforcement_path: none\n    intervention_points: []\n    safe_principles: [audit]\n    requires: []\n")


def _project(tmp_path, *, write=False):
    tools = ["returns_get_case", "return_policy_search"]
    spec = ("# SpecKit\n\n## 6. Tool Contracts\n\n" + "".join(f"### `{t}`\n- **Side Effects**: none\n\n" for t in tools)
            + "## 11. Security\n\n### 11a. Runtime Governance Contract\n\n```yaml\nframework: github-copilot-sdk\n"
            "governance:\n  mode: off\n  environment_modes: {development: evaluate_only, staging: evaluate_only, "
            "preproduction: enforce, production: enforce}\n  lifecycle_bindings: []\ntools:\n"
            + "".join(READ.format(t) for t in tools) + (WRITE if write else "") + "```\n")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text(spec)
    manifest = {"name": "returns-triage", "mode": "fast-poc", "action_mode": "recommend-only",
                "deployment_manifest": {"module_selectors": {}, "services": [], "expected_resource_types": []}}
    path = tmp_path / "specs" / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def _gaps(tmp_path, path):
    output = tmp_path / "predeploy.json"
    safe_check.phase_predeploy(tmp_path, path, output)
    return json.loads(output.read_text())["gaps"]


def test_recommend_only_pilot_has_no_governance_or_action_mode_gap(tmp_path):
    gaps = _gaps(tmp_path, _project(tmp_path))
    assert not [g for g in gaps if g.startswith(("governance", "action-mode"))], gaps


def test_recommend_only_pilot_exposing_a_write_is_an_action_mode_gap(tmp_path):
    gaps = _gaps(tmp_path, _project(tmp_path, write=True))
    assert [g for g in gaps if g.startswith("action-mode") and "returns_record_recommendation" in g], gaps
    assert not [g for g in gaps if g.startswith("governance")], gaps


def test_predeploy_report_does_not_record_an_absolute_workspace_path(tmp_path):
    """The report is written into the pilot repo; an absolute home path trips the
    generate-only self-check secret scan (live run 2026-11-09)."""
    path = _project(tmp_path)
    output = tmp_path / "tests" / "safe-check-predeploy-manifest.json"
    output.parent.mkdir()
    safe_check.phase_predeploy(tmp_path, path, output)
    text = output.read_text()
    assert str(tmp_path) not in text
    assert json.loads(text)["repo"] == ".."
