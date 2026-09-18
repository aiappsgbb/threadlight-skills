"""Offline contract/process tests, never hosted or business acceptance evidence."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def adapter():
    spec = importlib.util.spec_from_file_location("returns_release", SCRIPTS / "returns_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def behavior():
    return {
        "source_package_sha256": "a" * 64,
        "instructions_sha256": "b" * 64,
        "model": {"name": "model", "version": "2026-01-01"},
        "policy_code_sha256": "c" * 64,
        "tools": {
            "returns_get_case": {"policy_binding": "none", "schema_sha256": "d" * 64},
            "returns_apply_decision": {"policy_binding": "returns-safe", "schema_sha256": "e" * 64},
        },
    }


def external(phase):
    return {
        "signing_key_id": "https://example.vault.azure.net/keys/policy/version",
        "model_deployment": {"resource_id": phase + "-model", "revision": "1"},
        "connections": {
            name: {"resource_id": phase + "-" + name, "revision": "1"}
            for name in ("model", "gateway", "outlook")
        },
        "services": {
            name: {"resource_id": phase + "-" + name, "image_digest": "sha256:" + "a" * 64,
                   "revision": "1"}
            for name in ("control_plane", "gateway", "backend")
        },
        "cosmos": {"account_id": phase + "-cosmos", "database": "returns",
                   "containers": {"cases": "cases", "read_audit": "read-audit",
                                  "central_audit": "central-audit", "operations": "operations"}},
        "outlook": {"workflow_id": phase + "-workflow", "version": "1",
                    "definition_sha256": "f" * 64, "connection_id": phase + "-outlook",
                    "recipient_sha256": "1" * 64},
        "role_map": {
            "agent": [{"scope": phase + "-gateway", "role": "invoke"},
                      {"scope": phase + "-backend", "role": "read"}],
            "gateway": [{"scope": phase + "-backend", "role": "execute"}],
            "backend": [{"scope": phase + "-cosmos", "role": "write"}],
            "control_plane": [{"scope": phase + "-workflow", "role": "read"}],
            "human_reviewer": [{"scope": phase + "-workflow", "role": "approve"}],
        },
    }


def observation(phase="validation"):
    result = {
        "schema": "threadlight-deployment-observation/v1",
        "target_id": phase + "-agent", "environment": phase, "source_sha": "f" * 40,
        "image_digest": "sha256:" + "a" * 64, "version": "2",
        "observed_at": "2026-09-17T10:00:00+00:00",
    }
    result["application_contract"] = {
        "schema": "threadlight-returns-application/v1",
        "behavior": behavior(), "external": external(phase),
        "binding": {
            "agent_id": result["target_id"], "agent_version": result["version"],
            "image_digest": result["image_digest"], "environment": result["environment"],
            "envelope_sha256": "2" * 64, "policy_digest": "sha256:" + "3" * 64,
            "config_digest": "sha256:" + "4" * 64,
            "key_id": "https://example.vault.azure.net/keys/policy/version",
            "principal": phase + "-agent-principal", "client_id": phase + "-agent-client",
        },
        "identities": {name: phase + "-" + name + "-principal" for name in
                       ("agent", "gateway", "backend", "control_plane", "human_reviewer")},
    }
    return result


def configuration():
    return {"behavior": behavior(),
            "targets": {phase: {"external": external(phase)} for phase in ("validation", "production")}}


def test_contract_accepts_selected_two_tool_reference_not_image_only():
    adapter().check_observation(configuration(), "validation", observation())


@pytest.mark.parametrize("mutation", [
    "model-version", "policy", "third-tool", "bound-read", "connection", "role",
    "cosmos", "outlook", "binding-image", "binding-version", "agent-cosmos-identity",
    "missing-contract", "missing-binding", "missing-key-version", "foreign-signing-key",
])
def test_external_behavior_and_binding_drift_fail_closed(mutation):
    value = observation()
    contract = value["application_contract"]
    if mutation == "model-version":
        contract["behavior"]["model"]["version"] = "new"
    elif mutation == "policy":
        contract["behavior"]["policy_code_sha256"] = "9" * 64
    elif mutation == "third-tool":
        contract["behavior"]["tools"]["payments"] = {}
    elif mutation == "bound-read":
        contract["behavior"]["tools"]["returns_get_case"]["policy_binding"] = "returns-safe"
    elif mutation == "connection":
        contract["external"]["connections"]["gateway"]["revision"] = "new"
    elif mutation == "role":
        contract["external"]["role_map"]["agent"].append({"scope": "cosmos", "role": "write"})
    elif mutation == "cosmos":
        contract["external"]["cosmos"]["database"] = "production"
    elif mutation == "outlook":
        contract["external"]["outlook"]["definition_sha256"] = "0" * 64
    elif mutation == "binding-image":
        contract["binding"]["image_digest"] = "sha256:" + "9" * 64
    elif mutation == "binding-version":
        contract["binding"]["agent_version"] = "1"
    elif mutation == "agent-cosmos-identity":
        contract["identities"]["backend"] = contract["identities"]["agent"]
    elif mutation == "missing-contract":
        value.pop("application_contract")
    elif mutation == "missing-binding":
        contract.pop("binding")
    elif mutation == "missing-key-version":
        contract["binding"]["key_id"] = "https://example.vault.azure.net/keys/policy"
    else:
        contract["binding"]["key_id"] = "https://foreign.vault.azure.net/keys/policy/version"
    with pytest.raises(ValueError):
        adapter().check_observation(configuration(), "validation", value)


def test_phase_specific_resources_are_not_copied_from_validation():
    config = configuration()
    value = observation("production")
    adapter().check_observation(config, "production", value)
    value["application_contract"]["external"] = external("validation")
    with pytest.raises(ValueError):
        adapter().check_observation(config, "production", value)


def test_unknown_remote_operation_never_retries_dispatch():
    calls = []

    def operator(name, context):
        calls.append(name)
        if name == "admission":
            return {"state": "closed", "target_id": "production-agent"}
        return {"operation_id": "op", "state": "unknown"}

    with pytest.raises(ValueError, match="reconcil"):
        adapter().promote({"target": {"target_id": "production-agent"}, "operation_id": "op"},
                          operator)
    assert calls == ["stop", "admission", "operation"]


def test_promotion_passes_exact_candidate_and_keeps_admission_closed():
    calls = []
    candidate = observation()
    context = {"candidate": candidate, "target": {"target_id": "production-agent"},
               "operation_id": "op"}

    def operator(name, request):
        calls.append(name)
        assert request["candidate"] == candidate
        if name == "admission":
            return {"state": "closed", "target_id": "production-agent"}
        if name == "operation":
            return {"operation_id": "op", "state": "absent"}
        if name == "promote":
            return {"operation_id": "op", "state": "prepared",
                    "image_digest": candidate["image_digest"]}
        return {}

    adapter().promote(context, operator)
    assert calls == ["stop", "admission", "operation", "promote", "admission"]
    assert "admit" not in calls


def test_recovered_prepared_operation_is_read_only_not_redeployed():
    calls = []
    context = {"candidate": observation(), "target": {"target_id": "production-agent"},
               "operation_id": "op"}

    def operator(name, request):
        calls.append(name)
        if name == "admission":
            return {"state": "closed", "target_id": "production-agent"}
        return {"operation_id": "op", "state": "prepared",
                "image_digest": context["candidate"]["image_digest"]}

    adapter().promote(context, operator)
    assert calls == ["stop", "admission", "operation", "admission"]


def test_postchecks_cannot_be_empty_or_omit_human_outlook_evidence():
    module = adapter()
    context = {"candidate": observation(), "production_observation": observation("production"),
               "target": {"target_id": "production-agent"}, "operation_id": "op",
               "postcheck_started_at": datetime.now(timezone.utc).isoformat()}
    with pytest.raises(ValueError):
        module.check_postcheck({}, context)
    result = {"operation_id": "op", "target_id": "production-agent",
              "observed_at": datetime.now(timezone.utc).isoformat(),
              "application_contract_sha256": module.release_runner.digest(
                  context["production_observation"]["application_contract"]),
              "checks": {name: {"status": "pass", "evidence_sha256": "1" * 64}
                         for name in module.POSTCHECKS}}
    module.check_postcheck(result, context)
    old = dict(result, observed_at="2025-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):
        module.check_postcheck(old, context)
    result["checks"].pop("outlook_authority")
    with pytest.raises(ValueError):
        module.check_postcheck(result, context)


def test_runner_has_selected_reference_hooks_without_a_second_engine():
    import release_runner
    assert callable(release_runner.application)
    assert callable(release_runner.complete_application)


def test_missing_or_incomplete_real_operator_configuration_blocks_preflight(tmp_path):
    (tmp_path / "returns.json").write_text(json.dumps(configuration()))
    with pytest.raises(ValueError):
        adapter().configuration(tmp_path, "returns.json")


def test_producer_refuses_old_unbound_or_assessment_only_execution():
    module = adapter()
    started = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    finished = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    request = {"ci": {"run_id": "1"}, "candidate": observation(),
               "producer_started_at": started}
    with pytest.raises(ValueError):
        module.check_run({}, request)
    raw = {"provider": "approved-native-scanner", "run_id": "provider-run-1",
           "started_at": started, "finished_at": finished,
           "release_binding": {"ci": request["ci"], "candidate": request["candidate"]}}
    module.check_run(raw, request)
    raw["release_binding"]["candidate"] = observation("production")
    with pytest.raises(ValueError):
        module.check_run(raw, request)


def test_documentation_distinguishes_offline_slice_from_real_operator_integration():
    text = (Path(__file__).resolve().parents[3] / "docs/reference-release.md").read_text()
    for required in ("returns_mcp_agent.py", "returns_mcp_backend.py", "package_returns_mcp.py",
                     "returns-mcp-demo.md", "not live", "azd down", "unknown",
                     "agentops-accelerator==0.14.0", "Outlook", "Cosmos"):
        assert required in text


def test_reference_generation_reuses_existing_pipeline_and_vendors_real_assessors(tmp_path):
    import generate_pipeline
    paths = generate_pipeline.generate({
        "platform": "github-actions", "central_env_required": False,
        "reference_application": "returns-mcp/v1",
    }, tmp_path)
    assert (tmp_path / ".threadlight/skills/threadlight-cicd/scripts/returns_release.py") in paths
    assert (tmp_path / ".threadlight/skills/threadlight-evals/scripts/evals_check.py").exists()
    assert (tmp_path / ".threadlight/skills/threadlight-redteam/scripts/redteam_check.py").exists()
    policy = json.loads((tmp_path / "specs/release-policy.example.json").read_text())
    assert policy["application"] == {"profile": "returns-mcp/v1", "configuration": "specs/returns-release.json"}
    assert policy["production"]["promote"][2] == "promote"
    assert (tmp_path / "specs/returns-release.example.json").exists()
    assert not (tmp_path / "specs/returns-release.json").exists()


def test_unknown_reference_is_not_silently_disabled(tmp_path):
    import generate_pipeline
    with pytest.raises(ValueError):
        generate_pipeline.generate({"reference_application": "unknown"}, tmp_path)
