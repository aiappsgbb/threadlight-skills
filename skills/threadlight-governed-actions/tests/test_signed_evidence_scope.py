import json

import governed_actions


def test_old_generic_probe_cannot_prove_signed_evidence(tmp_path):
    document = {
        "framework": "microsoft-agent-framework",
        "governance": {
            "mode": "selective",
            "environment_modes": {"development": "evaluate_only", "staging": "evaluate_only",
                                  "preproduction": "enforce", "production": "enforce"},
            "lifecycle_bindings": [],
        },
        "tools": [{
            "id": "returns_apply_decision", "consequence": "write", "policy_binding": "safe",
            "enforcement_path": "governed-tool-gateway", "intervention_points": ["pre_tool_call"],
            "safe_principles": ["Safety"], "requires": ["signed-evidence"],
        }],
    }
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/governance-contract.json").write_text(json.dumps(document))
    findings = governed_actions._signed_evidence_findings(tmp_path, "pre-deploy")
    assert len(findings) == 1
    assert findings[0].finding_id == "ENF-001"
    assert findings[0].status == "not-verified"
    assert findings[0].affected_actions == ("returns_apply_decision",)
    assert findings[0].reason_code == "signed-evidence-proof-required"
    document["tools"][0]["requires"] = []
    (tmp_path / "specs/governance-contract.json").write_text(json.dumps(document))
    assert governed_actions._signed_evidence_findings(tmp_path, "pre-deploy") == ()
