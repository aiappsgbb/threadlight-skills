"""Governed-write pilots through generate_only and safe-check (workshop: one governed write).

``"action_mode": "governed-write"`` in specs/manifest.json: every SPEC § 6 tool is
an unbound read except exactly one write bound to the governed tool gateway
(SPEC § 11a ``mode: selective``). generate_only packages it as selected
governance today (the governance package is not built here) and never serves
the bound write from the mock MCP server: the write is routed to the governed
gateway once the foundation, signed policy and binding exist. An inconsistent
governed-write SPEC is refused (exit 6, nothing written).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from test_generate_only import GENERATOR, HARNESS, _run
from test_live_deploy_fixes import FIXTURES, SAFE_CHECK, _pytest

READS = ("returns_get_case", "returns_list_open", "customer_get_profile", "return_policy_search")
WRITE = "returns_record_recommendation"
REQUIRES = "[authorization, signed-policy-bundle, decision-receipt, idempotency-or-transaction]"
NOTES = (
    "- **Action mode**: `governed-write` (one write through the governed tool gateway; every other tool is "
    "an unbound read)\n"
    "- **Policy rules** (`returns-recommendation-write-v1`, implemented in Rego by threadlight-govern):\n"
    "  - `BR-001` allow: in-window return under EUR 250 with a cited RP clause.\n"
    "  - `BR-002` deny: outside the return window.\n"
    "  - `BR-003` escalate: refund above EUR 250 needs a supervisor (threadlight-review-action).\n"
    "- **Downstream service**: `POST /cases/{rma_id}/recommendation` (idempotency key `rma_id`) and "
    "`GET /cases/{rma_id}/recommendation` (outcome); it authorizes the gateway identity separately.\n"
    "- **Governance foundation**: the shared control plane, gateway, Key Vault signing key and Cosmos "
    "audit/approvals exist before deploy (deployed once per tenant by the facilitator).\n\n"
)


def _contract(*, write="bound", extra_write=False) -> str:
    entries = "".join(
        f"  - id: {t}\n    consequence: read\n    policy_binding: none\n    enforcement_path: none\n"
        "    intervention_points: []\n    safe_principles: [least-privilege]\n    requires: []\n" for t in READS)
    names = [WRITE] + (["returns_apply_refund"] if extra_write else [])
    for name in names:
        if write == "bound":
            entries += (f"  - id: {name}\n    consequence: write\n    policy_binding: {name}-v1\n"
                        "    enforcement_path: governed-tool-gateway\n    intervention_points: [pre_tool_call]\n"
                        f"    safe_principles: [scope, audit]\n    requires: {REQUIRES}\n")
        else:
            entries += (f"  - id: {name}\n    consequence: write\n    policy_binding: none\n"
                        "    enforcement_path: none\n    intervention_points: []\n    safe_principles: [audit]\n"
                        "    requires: []\n")
    mode = "selective" if write == "bound" else "off"
    return ("## 11a. Runtime Governance Contract\n\n" + NOTES + "```yaml\nframework: github-copilot-sdk\n\n"
            f"governance:\n  mode: {mode}\n  environment_modes:\n    development: evaluate_only\n"
            "    staging: evaluate_only\n    preproduction: enforce\n    production: enforce\n"
            "  lifecycle_bindings: []\n\ntools:\n" + entries + "```\n\n")


def _governed_write(project: Path, **kwargs) -> None:
    spec_path = project / "specs" / "SPEC.md"
    spec = spec_path.read_text()
    if kwargs.get("extra_write"):
        spec = spec.replace("### `return_policy_search`", "### `returns_apply_refund`\n- **Description**: refund.\n"
                            "- **Inputs**: `rma_id: string (required)`\n- **Side Effects**: issues a refund\n\n"
                            "### `return_policy_search`")
    start, end = spec.index("## 11a."), spec.index("## 11c.")
    spec_path.write_text(spec[:start] + _contract(**kwargs) + spec[end:])
    manifest_path = project / "specs" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update({"mode": "fast-poc", "action_mode": "governed-write"})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _project(tmp_path: Path, **kwargs) -> Path:
    project = tmp_path / "governed-write"
    shutil.copytree(FIXTURES / "design-owned-mcp", project)
    _governed_write(project, **kwargs)
    return project


def _generate(project: Path, tmp_path: Path):
    return _run([str(GENERATOR), "--project", str(project)], tmp_path)


def test_governed_write_pilot_generates_and_routes_the_write_to_the_gateway(tmp_path):
    project = _project(tmp_path)
    result = _generate(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    entry = (project / "src" / "mcp" / "threadlight_mcp_entry.py").read_text()
    served = json.loads(re.search(r"^DESIGN_TOOLS: list\[str\] = (\[.*\])$", entry, re.M).group(1))
    # The mock MCP server never serves the bound write as a directly callable effect.
    assert WRITE not in served and set(served) <= set(READS)
    plan = json.loads((project / "specs" / "project-tools.json").read_text())
    assert WRITE not in [t["name"] for t in plan["tools"]]
    assert plan["governed_gateway_tools"] == [WRITE]
    postdeploy = json.loads((project / "tests" / "postdeploy-manifest.json").read_text())
    assert WRITE not in postdeploy["expected_tools"]
    assert any("governed-write" in g and WRITE in g for g in postdeploy["gaps"]), postdeploy["gaps"]
    assert any("governance selected but not packaged" in g for g in postdeploy["gaps"]), postdeploy["gaps"]
    deferred = re.sub(r"\s+", " ", (project / "specs" / "deferred-steps.md").read_text())
    deploy_md = re.sub(r"\s+", " ", (project / "DEPLOY.md").read_text())
    for text in (deferred, deploy_md):
        assert "governed-write" in text and WRITE in text and "Selected governance" in text
        for marker in ("governance foundation", "signed policy", "threadlight-govern"):
            assert marker in text.lower(), marker
    assert "recommend-only" not in deferred.lower()


def test_governed_write_generated_tests_and_harness_pass(tmp_path):
    project = _project(tmp_path)
    assert _generate(project, tmp_path).returncode == 0
    result = _pytest(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    out = tmp_path / "project-tools-report.json"
    harness = _run([str(HARNESS), "--project", str(project), "--report", str(out)], tmp_path)
    assert harness.returncode == 0, harness.stdout + harness.stderr
    assert WRITE not in {c["tool"] for c in json.loads(out.read_text())["calls"]}


def test_governed_write_spec_with_an_unbound_write_is_refused(tmp_path):
    project = _project(tmp_path, write="unbound")
    result = _generate(project, tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "governed-write" in result.stderr and "mode: selective" in result.stderr
    assert not (project / "azure.yaml").exists() and not (project / "DEPLOY.md").exists()


def test_governed_write_spec_with_two_writes_is_refused(tmp_path):
    project = _project(tmp_path, extra_write=True)
    result = _generate(project, tmp_path)
    assert result.returncode == 6, result.stdout + result.stderr
    assert "exactly one" in result.stderr and "returns_apply_refund" in result.stderr
    assert not (project / "azure.yaml").exists()


def test_safe_check_reports_the_unpackaged_binding_but_no_action_mode_gap(tmp_path):
    project = _project(tmp_path)
    assert _generate(project, tmp_path).returncode == 0
    _run([str(SAFE_CHECK), "--phase", "pre-deploy", "--quiet"], tmp_path, cwd=project)
    report = json.loads((project / "tests" / "safe-check-predeploy-manifest.json").read_text())
    assert not [g for g in report["gaps"] if g.startswith("action-mode")], report["gaps"]
    # Invalid or unpackaged selected configuration is not off: the governance gap stays.
    assert [g for g in report["gaps"] if g.startswith("governance")], report["gaps"]


def test_governed_write_pilot_with_an_opted_in_voice_front_end(tmp_path):
    """One pilot, text + voice + the governed write: design -> deploy without hand edits."""
    import yaml
    project = _project(tmp_path)
    spec_path = project / "specs" / "SPEC.md"
    spec = spec_path.read_text()
    assert spec.count("agent_type: hosted\n") >= 1
    spec_path.write_text(spec.replace("agent_type: hosted\n", (
        "agent_type: hosted\nvoice_frontend: hosted-voice\nvoice_opt_in_source: user\n"
        'voice_opt_in_evidence: "Facilitator: callers speak to the governed returns agent (preview accepted)"\n'
        "preview_acknowledged: true\n"), 1))
    result = _generate(project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    services = yaml.safe_load((project / "azure.yaml").read_text())["services"]
    agent = next(s for s in services.values() if s.get("kind") == "hosted")
    wrapper = next(s for s in services.values() if s.get("kind") == "voice")
    assert wrapper["conversationEngine"] == {"type": "hosted_agent", "name": agent["name"]}
    assert ("invocations_ws", "1.0.0") in {(p["protocol"], p["version"]) for p in agent["protocols"]}
    entry = (project / "src" / "mcp" / "threadlight_mcp_entry.py").read_text()
    assert WRITE not in json.loads(re.search(r"^DESIGN_TOOLS: list\[str\] = (\[.*\])$", entry, re.M).group(1))
    deploy_md = re.sub(r"\s+", " ", (project / "DEPLOY.md").read_text())
    assert "governed-write" in deploy_md and "Voice front-end" in deploy_md
    assert "governance_pending" in deploy_md and "host record" in deploy_md.lower()
