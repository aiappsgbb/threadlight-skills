"""Recommend-only pilots (threadlight-design Fast-PoC) through generate_only and safe-check.

A Fast-PoC declares ``"action_mode": "recommend-only"`` in specs/manifest.json:
SPEC § 6 lists read tools only, § 11a records governance as not selected
(``mode: off``) with every read unbound, and the agent proposes the decision
while a person records it. generate_only packages it without a governance gap
and refuses (exit 6, nothing written) a recommend-only SPEC that still exposes
a write tool or selects a binding: that would deploy an unbound or unpackaged
consequential write.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from test_generate_only import GENERATOR, _run
from test_live_deploy_fixes import FIXTURES, SAFE_CHECK, _pytest

READS = ("returns_get_case", "returns_list_open", "customer_get_profile", "return_policy_search")
WRITE = "returns_record_recommendation"


def _contract(tools, *, mode="off", write=None) -> str:
    entries = "".join(
        f"  - id: {t}\n    consequence: read\n    policy_binding: none\n    enforcement_path: none\n"
        "    intervention_points: []\n    safe_principles: [least-privilege]\n    requires: []\n" for t in tools)
    if write == "unbound":
        entries += (f"  - id: {WRITE}\n    consequence: write\n    policy_binding: none\n    enforcement_path: none\n"
                    "    intervention_points: []\n    safe_principles: [audit]\n    requires: []\n")
    elif write == "bound":
        entries += (f"  - id: {WRITE}\n    consequence: write\n    policy_binding: returns-recommendation-write-v1\n"
                    "    enforcement_path: governed-tool-gateway\n    intervention_points: [pre_tool_call]\n"
                    "    safe_principles: [scope, audit]\n    requires: [authorization, audit, idempotency]\n")
    return ("## 11a. Runtime Governance Contract\n\n- **Action mode**: `recommend-only` (governance not "
            "selected; reads unbound; no consequential tool is exposed)\n\n```yaml\nframework: github-copilot-sdk\n\n"
            f"governance:\n  mode: {mode}\n  environment_modes:\n    development: evaluate_only\n"
            "    staging: evaluate_only\n    preproduction: enforce\n    production: enforce\n"
            "  lifecycle_bindings: []\n\ntools:\n" + entries + "```\n\n")


def _recommend_only(project: Path, *, write=None, mode="off") -> None:
    spec_path = project / "specs" / "SPEC.md"
    spec = spec_path.read_text()
    if write is None:
        start = spec.index(f"### `{WRITE}`")
        spec = spec[:start] + spec[spec.index("### `return_policy_search`"):]
    start, end = spec.index("## 11a."), spec.index("## 11c.")
    spec_path.write_text(spec[:start] + _contract(READS, mode=mode, write=write) + spec[end:])
    if write is None:
        server = project / "src" / "mcp" / "server.py"
        text = server.read_text()
        start = text.index(f"def {WRITE}(")
        text = text[:start] + text[text.index("TOOLS = ", start):]
        server.write_text(text.replace(f", {WRITE}]", "]"))
    manifest_path = project / "specs" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update({"mode": "fast-poc", "action_mode": "recommend-only"})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _project(tmp_path: Path, **kwargs) -> Path:
    project = tmp_path / "recommend-only"
    shutil.copytree(FIXTURES / "design-owned-mcp", project)
    _recommend_only(project, **kwargs)
    return project


def _generate(project: Path, tmp_path: Path):
    return _run([str(GENERATOR), "--project", str(project)], tmp_path)


def test_recommend_only_pilot_generates_without_force_and_without_a_governance_gap(tmp_path):
    project = _project(tmp_path)
    result = _generate(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    entry = (project / "src" / "mcp" / "threadlight_mcp_entry.py").read_text()
    served = json.loads(re.search(r"^DESIGN_TOOLS: list\[str\] = (\[.*\])$", entry, re.M).group(1))
    assert WRITE not in served and set(served) <= set(READS)
    deploy_md = (project / "DEPLOY.md").read_text()
    deferred = (project / "specs" / "deferred-steps.md").read_text()
    for text in (deploy_md, deferred):
        assert "recommend-only" in text.lower() and "Selected governance" not in text
    assert re.search(r"a person records", deferred)
    gaps = json.loads((project / "tests" / "postdeploy-manifest.json").read_text())["gaps"]
    assert not [g for g in gaps if "governance" in g.lower()], gaps


def test_recommend_only_generated_tests_pass(tmp_path):
    project = _project(tmp_path)
    assert _generate(project, tmp_path).returncode == 0
    result = _pytest(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_safe_check_reports_no_governance_or_action_mode_gap_for_a_recommend_only_pilot(tmp_path):
    project = _project(tmp_path)
    assert _generate(project, tmp_path).returncode == 0
    _run([str(SAFE_CHECK), "--phase", "pre-deploy", "--quiet"], tmp_path, cwd=project)
    report = json.loads((project / "tests" / "safe-check-predeploy-manifest.json").read_text())
    assert not [g for g in report["gaps"] if g.startswith(("governance", "action-mode"))], report["gaps"]


def test_recommend_only_spec_with_an_unbound_write_is_refused(tmp_path):
    project = _project(tmp_path, write="unbound")
    result = _generate(project, tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert WRITE in result.stderr + result.stdout and "recommend-only" in result.stderr + result.stdout
    assert not (project / "azure.yaml").exists() and not (project / "DEPLOY.md").exists()


def test_recommend_only_spec_that_selects_a_binding_is_refused(tmp_path):
    project = _project(tmp_path, write="bound", mode="selective")
    result = _generate(project, tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "recommend-only" in result.stderr + result.stdout
    assert not (project / "azure.yaml").exists()


def test_safe_check_flags_a_recommend_only_manifest_that_exposes_a_write(tmp_path):
    project = _project(tmp_path)
    assert _generate(project, tmp_path).returncode == 0
    # Drift after packaging: the SPEC gained an unbound write but still claims recommend-only.
    _recommend_only(project, write="unbound")
    _run([str(SAFE_CHECK), "--phase", "pre-deploy", "--quiet"], tmp_path, cwd=project)
    report = json.loads((project / "tests" / "safe-check-predeploy-manifest.json").read_text())
    assert any(g.startswith("action-mode") and WRITE in g for g in report["gaps"]), report["gaps"]
