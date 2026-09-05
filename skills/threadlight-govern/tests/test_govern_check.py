"""Offline inventory is not runtime enforcement, including legacy markers."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/threadlight-govern/scripts"))
import govern_check as gc
from skills._shared.governance import validate_governance_manifest


def contract(framework="microsoft-agent-framework"):
    return {
        "framework": framework,
        "governance": {
            "mode": "selective",
            "environment_modes": {
                "development": "evaluate_only", "staging": "evaluate_only",
                "preproduction": "enforce", "production": "enforce",
            },
            "lifecycle_bindings": [],
        },
        "tools": [
            {
                "id": "returns_apply_decision", "consequence": "write",
                "policy_binding": "returns-safe", "enforcement_path": "governed-tool-gateway",
                "intervention_points": ["pre_tool_call", "post_tool_call"],
                "safe_principles": ["scope", "audit"], "requires": [],
            },
            "search_catalog",
        ],
    }


def write_contract(root, document=None):
    (root / "specs").mkdir()
    (root / "specs/governance-contract.json").write_text(
        json.dumps(document or contract()), encoding="utf-8"
    )


@pytest.mark.parametrize("fixture", ["sample-wired", "sample-bare"])
def test_static_fixture_never_proves_governance(fixture):
    root = ROOT / "skills/threadlight-govern/references/fixtures" / fixture
    man = gc.manifest(str(root), gc.evaluate(str(root), 90), "auto", 90)
    assert man["schema"] == "threadlight-governance-manifest/v1"
    assert man["coverage"]["tools_enforced"] == 0
    assert man["coverage"]["tools_observed"] == 0
    assert man["live_probes"] == []
    assert "verdict" not in man and "governed" not in man
    assert man["offline_evidence"]
    assert validate_governance_manifest(man) == man


@pytest.mark.parametrize("framework", ["github-copilot-sdk", "microsoft-agent-framework"])
def test_bound_missing_engine_is_unverified_without_framework_switch(tmp_path, framework):
    write_contract(tmp_path, contract(framework))
    man = gc.evaluate(str(tmp_path), 90)
    assert man["agent"]["runtime"] == framework
    assert man["coverage"]["tools_bound"] == 1
    assert man["coverage"]["tools_unverified"] == 1
    assert man["coverage"]["tools_unbound"] == 1
    assert {b["status"] for b in man["bindings"]} == {"unverified", "unbound"}
    assert man["policy_bundle"] is None
    assert man["agent"]["image_digest"] is None
    assert man["enforcement"]["acs_artifact_sha256"] is None
    assert man["gaps"]
    validate_governance_manifest(man)


def test_governance_off_is_unbound_and_still_emits_report(tmp_path, capsys):
    doc = contract()
    doc["governance"]["mode"] = "off"
    doc["tools"] = ["search_catalog"]
    write_contract(tmp_path, doc)
    assert gc.main(["--target", str(tmp_path), "--profile", "none", "--emit", "--json"]) == 0
    man = json.loads(capsys.readouterr().out)
    assert man["coverage"]["tools_unbound"] == 1
    assert (tmp_path / "specs/govern-manifest.json").exists()
    assert man["coverage"]["tools_enforced"] == 0


def test_spec_yaml_unquoted_off_preserves_explicit_governance_mode(tmp_path):
    import yaml
    doc = contract()
    doc["governance"]["mode"] = "off"
    doc["tools"] = ["search_catalog"]
    (tmp_path / "specs").mkdir()
    block = yaml.safe_dump(doc).replace("mode: 'off'", "mode: off")
    assert "mode: off" in block
    (tmp_path / "specs/SPEC.md").write_text(f"```yaml\n{block}```\n")
    man = gc.evaluate(str(tmp_path))
    assert man["agent"]["runtime"] == doc["framework"]
    assert man["coverage"]["tools_unbound"] == 1
    assert man["coverage"]["tools_enforced"] == 0
    assert man["offline_evidence"][0]["reason_code"] == "contract-declared-only"


@pytest.mark.parametrize("mode", ["true", "false"])
def test_spec_yaml_boolean_is_not_a_governance_mode(tmp_path, mode):
    import yaml
    doc = contract()
    (tmp_path / "specs").mkdir()
    block = yaml.safe_dump(doc).replace("mode: selective", f"mode: {mode}")
    (tmp_path / "specs/SPEC.md").write_text(f"```yaml\n{block}```\n")
    with pytest.raises(ValueError):
        gc.evaluate(str(tmp_path))


def test_legacy_boolean_contract_does_not_pass_gate(tmp_path):
    write_contract(tmp_path, {"governed": True})
    assert gc.main(["--target", str(tmp_path), "--gate"]) == 2


def test_offline_gate_never_passes_legacy_wired_fixture():
    root = ROOT / "skills/threadlight-govern/references/fixtures/sample-wired"
    assert gc.main(["--target", str(root), "--gate"]) == 2


def test_failure_emits_explicit_unverified_manifest(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic validator failure")
    monkeypatch.setattr(gc, "evaluate", boom)
    assert gc.main(["--target", str(tmp_path), "--emit", "--gate"]) == 2
    man = json.loads((tmp_path / "specs/govern-manifest.json").read_text())
    assert man["coverage"]["tools_enforced"] == 0
    assert any(e["reason_code"] == "validator-error" for e in man["offline_evidence"])
    validate_governance_manifest(man)


def test_report_is_explicit_about_proof_boundaries(tmp_path):
    write_contract(tmp_path)
    text = gc.render(gc.evaluate(str(tmp_path), 90))
    assert "offline" in text.lower() and "not deployment enforcement" in text.lower()
    assert "returns_apply_decision" in text and "unverified" in text


def test_rejects_symlink_contract(tmp_path):
    target = tmp_path / "actual.json"
    target.write_text(json.dumps(contract()))
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/governance-contract.json").symlink_to(target)
    assert gc.main(["--target", str(tmp_path), "--gate"]) == 2


def test_new_manifest_does_not_fall_back_to_legacy_consumer_pass(tmp_path):
    import importlib
    import shutil
    sys.path.insert(0, str(ROOT / "skills/threadlight-production-ready/scripts"))
    pr = importlib.import_module("production_ready")
    shutil.copytree(ROOT / "skills/threadlight-govern/references/fixtures/sample-wired",
                    tmp_path, dirs_exist_ok=True)
    gc.main(["--target", str(tmp_path), "--emit"])
    ctx = pr.RepoContext(
        root=tmp_path, bicep_files=[], src_files=[], test_files=[], spec_text="",
        spec_12={}, spec_11b={}, azure_yaml_text="", docs_text="", azd_env={},
        manifest={}, bicep_text="", src_text="",
        bicep_graph=pr.BicepGraph(resources=[], source_files=[]),
    )
    findings = pr._check_agt_static(ctx, "auto") + pr._check_rai_static(ctx)
    for finding in findings:
        if finding.id in {"AGT-001", "AGT-002", "AGT-003", "AGT-004", "AGT-005", "RAI-002"}:
            assert finding.status == "not-verified", (finding.id, finding.status)
