#!/usr/bin/env python3
"""Smoke test for `threadlight-auto` orchestrator decisions.

Runs the orchestrator against each fixture under `tests/fixtures/` and asserts
the `next_action.type` matches expectations.

Run locally: `python3 skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
Exit codes: 0 = all green; N = failures.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_orchestrator():
    """Load orchestrator.py as a module (with the sys.modules registration the
    Python 3.14 dataclass + importlib combination requires)."""
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("threadlight_auto_orchestrator", str(ORCH))
    mod = _ilu.module_from_spec(spec)
    sys.modules["threadlight_auto_orchestrator"] = mod
    spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator()


def _leg_envelope(schema: str, status: str) -> str:
    return json.dumps({
        "schema": schema,
        "tool_version": "0.1.0",
        "generated_at": "2026-08-18T10:00:00Z",
        "freshness": {"valid_for_hours": 24, "source_oldest_at": None},
        "status": status,
        "findings": [],
    })


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_postdeploy_fixture(tmp_path: Path, payload: dict) -> None:
    deployment_manifest = {
        "subscription_id": "sub-1",
        "resource_group": "rg-pilot",
    }
    _write_json(tmp_path / "specs" / "manifest.json", {"deployment_manifest": deployment_manifest})
    merged = {
        "deployment_manifest": deployment_manifest,
        **payload,
    }
    _write_json(tmp_path / "tests" / "postdeploy-manifest.json", merged)


# ---------------------------------------------------------------------------
# Manual-handoff projection (Task 7): the four live legs are advisory only.
# pytest-collected; also exercised from main() below for the standalone runner.
# ---------------------------------------------------------------------------

def test_new_live_legs_are_manual_handoffs_not_auto_stages(tmp_path):
    decision = orch.decide(tmp_path)
    handoffs = decision["manual_handoffs"]
    # Ordered exactly connect -> ground -> loadtest -> upgrade.
    assert [h["skill"] for h in handoffs] == [
        "threadlight-connect",
        "threadlight-ground",
        "threadlight-loadtest",
        "threadlight-upgrade",
    ]
    # An empty workspace has no leg manifests -> every handoff is 'ready'.
    assert all(h["status"] == "ready" for h in handoffs)
    # Each handoff names its skill for a manual, advisory chat invocation.
    for h in handoffs:
        assert h["skill"] in h["next_intent"]
        assert h["manifest"].startswith("specs/")
    # The live legs are NEVER auto-stages.
    assert not {"connect", "ground", "loadtest", "upgrade"}.intersection(decision["stages"])
    assert not {"connect", "ground", "loadtest", "upgrade"}.intersection(orch.STAGES)
    # `stages` echoes the automatic stage runner exactly.
    assert decision["stages"] == list(orch.STAGES)


def test_manual_handoff_status_reflects_validated_envelope(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "connect-manifest.json").write_text(
        _leg_envelope("threadlight-connect-manifest/v1", "complete"), encoding="utf-8")
    (specs / "ground-manifest.json").write_text(
        _leg_envelope("threadlight.ground/v1", "partial"), encoding="utf-8")
    (specs / "load-manifest.json").write_text(
        _leg_envelope("threadlight.load/v1", "aborted"), encoding="utf-8")
    # An unrecognized / malformed manifest must degrade to 'partial', never 'complete'.
    (specs / "upgrade-manifest.json").write_text("{ not valid json", encoding="utf-8")

    decision = orch.decide(tmp_path)
    by_skill = {h["skill"]: h["status"] for h in decision["manual_handoffs"]}
    assert by_skill == {
        "threadlight-connect": "complete",
        "threadlight-ground": "partial",
        "threadlight-loadtest": "aborted",
        "threadlight-upgrade": "partial",
    }


def test_govern_reruns_when_required_capabilities_are_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "govern-manifest.json",
        {
            "schema": "threadlight-govern-manifest/v2",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "governed",
            "capabilities": {
                "policy_artefact_present": {"status": "pass"},
                "policy_schema_valid": {"status": "pass"},
            },
        },
    )

    decision = orch._check_govern(tmp_path, {})

    assert decision.decision == "run"
    assert "missing capabilities" in decision.reason


def test_invalid_envelope_never_reports_complete(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    # A dict with status 'complete' but missing required envelope keys is not a
    # valid envelope -> partial, not complete.
    (specs / "connect-manifest.json").write_text(
        json.dumps({"status": "complete"}), encoding="utf-8")
    decision = orch.decide(tmp_path)
    connect = next(h for h in decision["manual_handoffs"] if h["skill"] == "threadlight-connect")
    assert connect["status"] == "partial"


def test_deploy_requires_a_real_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "# AGENT_FQDN=commented-out.example.com\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "AGENT_FQDN" not in decision.reason or "hasn't completed" in decision.reason


def test_deploy_requires_a_non_empty_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"


def test_deploy_rejects_a_quoted_empty_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        'AGENT_FQDN=""\n',
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"


def test_deploy_rejects_an_inline_comment_after_empty_agent_fqdn_assignment(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=   # placeholder until deployed\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"


def test_deploy_requires_an_unambiguous_single_azd_env(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    (tmp_path / ".azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=threadlight-dev.example.com\n",
        encoding="utf-8",
    )
    (tmp_path / ".azure" / "prod").mkdir(parents=True)
    (tmp_path / ".azure" / "prod" / ".env").write_text(
        "AGENT_FQDN=threadlight-prod.example.com\n",
        encoding="utf-8",
    )

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "multiple azd envs" in decision.reason


def test_deploy_treats_an_azd_env_without_dot_env_as_incomplete_evidence(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "AGENT_FQDN" in decision.reason


def test_deploy_rejects_a_symlinked_azd_root(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / "shadow-azure" / "dev").mkdir(parents=True)
    (tmp_path / "shadow-azure" / "dev" / ".env").write_text(
        "AGENT_FQDN=shadow.example.com\n",
        encoding="utf-8",
    )
    os.symlink(tmp_path / "shadow-azure", tmp_path / ".azure", target_is_directory=True)

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "symlinked" in decision.reason


def test_deploy_rejects_a_symlinked_azd_env_directory(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure").mkdir(parents=True)
    (tmp_path / "shadow-env").mkdir(parents=True)
    (tmp_path / "shadow-env" / ".env").write_text(
        "AGENT_FQDN=shadow.example.com\n",
        encoding="utf-8",
    )
    os.symlink(tmp_path / "shadow-env", tmp_path / ".azure" / "dev", target_is_directory=True)

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "symlinked" in decision.reason


def test_deploy_rejects_a_broken_symlinked_dot_env(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure" / "dev").mkdir(parents=True)
    os.symlink(tmp_path / "missing.env", tmp_path / ".azure" / "dev" / ".env")

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "symlinked" in decision.reason


def test_deploy_treats_a_regular_file_dot_azure_as_missing_evidence(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (tmp_path / ".azure").write_text("not a directory\n", encoding="utf-8")

    decision = orch._check_deploy(tmp_path, {})

    assert decision.decision == "run"
    assert "AGENT_FQDN" in decision.reason


def test_live_legs_never_added_to_stage_runner():
    assert set(orch.MANUAL_HANDOFFS) == {
        "threadlight-connect", "threadlight-ground",
        "threadlight-loadtest", "threadlight-upgrade",
    }
    assert "connect" not in orch.STAGES
    assert "ground" not in orch.STAGES
    assert "loadtest" not in orch.STAGES
    assert "upgrade" not in orch.STAGES
    assert orch.STAGE_PROBES.keys() == set(orch.STAGES)


def test_safe_check_requires_green_postdeploy_manifest_even_with_fresh_doc(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "safe-check-post.md").write_text("# green\n", encoding="utf-8")

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "tests/postdeploy-manifest.json" in decision.artifacts_missing


def test_safe_check_non_green_postdeploy_manifest_with_fresh_doc_runs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "safe-check-post.md").write_text("# green\n", encoding="utf-8")
    # Create a postdeploy manifest that reports unresolved gaps
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "postdeploy-manifest.json").write_text(
        json.dumps({"phase": "post-deploy", "gaps": [{"id": "g1", "reason": "issue"}]}),
        encoding="utf-8",
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    # Reason should mention gaps and that we need to re-run the safe-check
    assert "gaps" in decision.reason


def test_safe_check_requires_postdeploy_manifest_to_match_current_deployment_manifest(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "safe-check-post.md").write_text("# green\n", encoding="utf-8")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "manifest.json").write_text(
        json.dumps(
            {
                "deployment_manifest": {
                    "subscription_id": "sub-1",
                    "resource_group": "rg-current",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "postdeploy-manifest.json").write_text(
        json.dumps(
            {
                "phase": "post-deploy",
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "gaps": [],
                "deployment_manifest": {
                    "subscription_id": "sub-1",
                    "resource_group": "rg-old",
                },
            }
        ),
        encoding="utf-8",
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "no longer matches" in decision.reason.lower()
    assert "re-running" in decision.reason.lower()


def test_safe_check_green_postdeploy_manifest_skips_without_doc(tmp_path):
    _write_postdeploy_fixture(tmp_path, {"checked_at": _iso_now(), "phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "skip"
    assert "tests/postdeploy-manifest.json" in decision.artifacts_seen


def test_safe_check_manifest_without_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(tmp_path, {"phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_timezone_less_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-08-06T08:00:00", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_space_separated_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-08-06 08:00:00+00:00", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_future_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2099-01-01T00:00:00Z", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "future" in decision.reason.lower()


def test_safe_check_manifest_with_invalid_rfc3339_checked_at_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-08-05T24:00:00Z", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_impossible_calendar_date_runs(tmp_path):
    _write_postdeploy_fixture(
        tmp_path,
        {"checked_at": "2026-02-30T08:00:00Z", "phase": "post-deploy", "gaps": []},
    )

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert "checked_at" in decision.reason


def test_safe_check_manifest_with_lowercase_z_skips(tmp_path):
    checked_at = _iso_now().replace("Z", "z")
    _write_postdeploy_fixture(tmp_path, {"checked_at": checked_at, "phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "skip"
    assert "tests/postdeploy-manifest.json" in decision.artifacts_seen


def test_safe_check_manifest_exactly_24_hours_old_runs(tmp_path):
    checked_at = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write_postdeploy_fixture(tmp_path, {"checked_at": checked_at, "phase": "post-deploy", "gaps": []})

    decision = orch._check_safe_check(tmp_path, {})

    assert decision.decision == "run"
    assert ">= 24 h" in decision.reason


def test_leg_manifest_requires_expected_schema_captured_at_and_known_verdict(tmp_path):
    manifest = tmp_path / "specs" / "evals-manifest.json"
    bad_payloads = (
        {"schema": "threadlight-evals-manifest/v999", "captured_at": _iso_now(), "verdict": "comprehensive"},
        {"schema": "threadlight-evals-manifest/v1", "verdict": "comprehensive"},
        {"schema": "threadlight-evals-manifest/v1", "captured_at": _iso_now(), "verdict": "mystery"},
    )

    for payload in bad_payloads:
        _write_json(manifest, payload)
        decision = orch._check_evals(tmp_path, {})
        assert decision.decision == "run"


def test_leg_manifest_reruns_when_capabilities_are_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "redteam-manifest.json",
        {
            "schema": "threadlight-redteam-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "hardened",
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "run"
    assert "capabilities" in decision.reason


def test_leg_manifest_skips_fresh_non_passing_known_verdict_with_reason(tmp_path):
    manifest = tmp_path / "specs" / "redteam-manifest.json"
    _write_json(
        manifest,
        {
            "schema": "threadlight-redteam-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "vulnerable",
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["redteam"]["required_capabilities"]
            },
            "asr": {
                "jailbreak": 0.0,
                "prompt_injection": 0.0,
                "indirect_attack": 0.0,
                "exfiltration": 0.0,
                "harmful_content": 0.0,
            },
            "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "skip"
    assert "verdict=vulnerable" in decision.reason


def test_leg_manifest_reruns_when_govern_capability_status_is_invalid(tmp_path):
    _write_json(
        tmp_path / "specs" / "govern-manifest.json",
        {
            "schema": "threadlight-govern-manifest/v2",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "governed",
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["govern"]["required_capabilities"]
            }
            | {"policy_schema_valid": {"status": "bogus"}},
        },
    )

    decision = orch._check_govern(tmp_path, {})

    assert decision.decision == "run"
    assert "invalid status" in decision.reason


def test_leg_manifest_reruns_when_evals_check_id_is_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "evals-manifest.json",
        {
            "schema": "threadlight-evals-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "comprehensive",
            "capabilities": {
                capability: {"status": "pass", "check_id": f"eval-{index:03d}"}
                for index, capability in enumerate(
                    sorted(orch.LEG_CONTRACTS["evals"]["required_capabilities"]),
                    start=1,
                )
            }
            | {"eval_scenarios_present": {"status": "pass"}},
        },
    )

    decision = orch._check_evals(tmp_path, {})

    assert decision.decision == "run"
    assert "check_id" in decision.reason


def test_leg_manifest_reruns_when_redteam_capability_has_unsupported_fields(tmp_path):
    _write_json(
        tmp_path / "specs" / "redteam-manifest.json",
        {
            "schema": "threadlight-redteam-manifest/v1",
            "tool_version": "1.0",
            "captured_at": _iso_now(),
            "verdict": "vulnerable",
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["redteam"]["required_capabilities"]
            }
            | {"scan_present": {"status": "pass", "bogus": 123}},
            "asr": {
                "jailbreak": 0.0,
                "prompt_injection": 0.0,
                "indirect_attack": 0.0,
                "exfiltration": 0.0,
                "harmful_content": 0.0,
            },
            "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "run"
    assert "unsupported fields" in decision.reason


def test_leg_manifest_reruns_when_redteam_tool_version_is_missing(tmp_path):
    _write_json(
        tmp_path / "specs" / "redteam-manifest.json",
        {
            "schema": "threadlight-redteam-manifest/v1",
            "captured_at": _iso_now(),
            "verdict": "hardened",
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "capabilities": {
                capability: {"status": "pass"}
                for capability in orch.LEG_CONTRACTS["redteam"]["required_capabilities"]
            },
            "asr": {
                "jailbreak": 0.0,
                "prompt_injection": 0.0,
                "indirect_attack": 0.0,
                "exfiltration": 0.0,
                "harmful_content": 0.0,
            },
            "thresholds": {"max_asr": 0.1, "freshness_days": 7, "min_attacks": 10},
        },
    )

    decision = orch._check_redteam(tmp_path, {})

    assert decision.decision == "run"
    assert "tool_version" in decision.reason


def test_cost_projection_requires_1x_schema_before_trusting_generated_at(tmp_path):
    spec = tmp_path / "specs" / "SPEC.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "\n".join(
            [
                "load_profile:",
                "  workload_class: steady",
                "  peak_concurrent_sessions: 10",
                "  avg_requests_per_session: 4",
                "  avg_tokens_per_request: 800",
                "  peak_requests_per_second: 3",
                "  business_hours_only: true",
                "  cosmos_gb_year_one: 1",
                "  storage_gb_year_one: 1",
                "  ai_search_documents: 10",
                "  monthly_growth_rate: 0.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "specs" / "cost-manifest.json",
        {
            "schema_version": "2.0",
            "generated_at": _iso_now(),
        },
    )

    decision = orch._check_cost_projection(
        tmp_path,
        {"cost_projection": {"last_deploy_at": "2026-01-01T00:00:00Z"}},
    )

    assert decision.decision == "run"


def test_cost_projection_resumability_requires_strictly_newer_than_last_deploy(tmp_path):
    """Regression: resume check should require manifest.generated_at > last_deploy_at.

    If generated_at equals the recorded last deploy instant, the planner must
    re-run cost-projection (decision "run").
    """
    spec = tmp_path / "specs" / "SPEC.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "\n".join(
            [
                "load_profile:",
                "  workload_class: steady",
                "  peak_concurrent_sessions: 10",
                "  avg_requests_per_session: 4",
                "  avg_tokens_per_request: 800",
                "  peak_requests_per_second: 3",
                "  business_hours_only: true",
                "  cosmos_gb_year_one: 1",
                "  storage_gb_year_one: 1",
                "  ai_search_documents: 10",
                "  monthly_growth_rate: 0.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Use a trusted 1.x schema_version and set generated_at exactly equal to last_deploy
    last_deploy = "2026-08-20T12:34:56Z"
    _write_json(
        tmp_path / "specs" / "cost-manifest.json",
        {
            "schema_version": "1.0",
            "generated_at": last_deploy,
        },
    )

    decision = orch._check_cost_projection(
        tmp_path,
        {"cost_projection": {"last_deploy_at": last_deploy}},
    )

    # Expect RUN because generated_at must be strictly newer than last_deploy to reuse
    assert decision.decision == "run"


# ---------------------------------------------------------------------------
# Governed-actions lifecycle (Task 14): recommendation-only, never a stage.
#
# `threadlight-governed-actions` owns consequential-action assessment,
# enforcement scaffolding, and every high-impact change. `threadlight-auto`
# may only *recommend* the explicit lifecycle steps and summarise an already
# committed manifest. It never runs the skill, never adds it to the stage
# runner, and never emits a scaffold / policy-application / canary / merge /
# deploy command of its own.
# ---------------------------------------------------------------------------

decide = orch.decide
STAGES = orch.STAGES
LEG_CONTRACTS = orch.LEG_CONTRACTS

GOVERNED_ACTIONS_MANIFEST_REL = "tests/governed-actions-manifest.json"
GOLDEN_GOVERNED_ACTIONS_MANIFEST = (
    REPO
    / "skills"
    / "threadlight-governed-actions"
    / "tests"
    / "golden"
    / "conformant-manifest.json"
)
GOLDEN_COMMIT = "0123456789abcdef0123456789abcdef01234567"
# Inside the golden's own freshness window (2026-09-01T12:00Z .. 2026-09-02T12:00Z).
GOLDEN_FRESH_NOW = datetime(2026, 9, 1, 18, 0, 0, tzinfo=timezone.utc)

EXPECTED_HANDOFF = {
    "execution": "manual-explicit",
    "design": (
        "run threadlight-governed-actions --phase design "
        "after threadlight-design"
    ),
    "pre_deploy": (
        "run threadlight-governed-actions --phase pre-deploy before deploy"
    ),
    "post_deploy": (
        "run threadlight-governed-actions --phase post-deploy "
        "against staging only"
    ),
    "manifest": "tests/governed-actions-manifest.json",
}

# Privileged verbs auto must never emit. `deploy`/`design` on their own are
# deliberately absent: the approved lifecycle wording contains them, and this
# guard is about *commands* auto could be read as authorising.
FORBIDDEN_COMMAND_TOKENS = (
    "--scaffold",
    "scaffold",
    "--apply",
    "apply-plan",
    "apply the policy",
    "policy apply",
    "canary",
    "rollout",
    "merge",
    "azd up",
    "azd deploy",
    "az deployment",
    "gh pr",
    "git push",
)


def _golden_governed_actions_manifest() -> dict:
    return json.loads(GOLDEN_GOVERNED_ACTIONS_MANIFEST.read_text(encoding="utf-8"))


def make_context(
    tmp_path: Path,
    *,
    consequential_actions: bool = False,
    manifest: dict | None = None,
) -> Path:
    """Build a workspace and return it (the orchestrator's only input).

    `consequential_actions=True` writes the smallest honest signal auto can
    read on its own: a root tool registry declaring a non-`read` action.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    if consequential_actions:
        _write_json(
            tmp_path / "tool-registry.json",
            {
                "actions": [
                    {"id": "issue_refund", "consequence": "financial"},
                    {"id": "read_claim", "consequence": "read"},
                ]
            },
        )
    if manifest is not None:
        _write_json(tmp_path / "tests" / "governed-actions-manifest.json", manifest)
    return tmp_path


def test_auto_recommends_explicit_governed_actions_lifecycle_handoffs(tmp_path):
    decision = decide(make_context(tmp_path, consequential_actions=True))
    assert decision["governed_actions"] == EXPECTED_HANDOFF
    assert orch.GOVERNED_ACTIONS_HANDOFF == EXPECTED_HANDOFF


def test_auto_never_schedules_governed_actions_or_rollout():
    assert "governed_actions" not in STAGES
    assert all(
        leg.get("skill") != "threadlight-governed-actions"
        for leg in LEG_CONTRACTS.values()
    )
    # Not a stage probe, and not one of the four advisory live legs either.
    assert "governed_actions" not in orch.STAGE_PROBES
    assert "threadlight-governed-actions" not in orch.MANUAL_HANDOFFS


def test_auto_omits_governed_actions_handoff_without_signal(tmp_path):
    decision = decide(make_context(tmp_path))
    assert decision["governed_actions"] is None
    assert decision["governed_actions_manifest"] is None


def test_auto_recommends_handoff_when_only_a_manifest_exists(tmp_path):
    workspace = make_context(tmp_path, manifest=_golden_governed_actions_manifest())
    decision = decide(workspace)
    assert decision["governed_actions"] == EXPECTED_HANDOFF


def test_auto_summarizes_a_schema_valid_governed_actions_manifest(tmp_path):
    workspace = make_context(
        tmp_path,
        consequential_actions=True,
        manifest=_golden_governed_actions_manifest(),
    )
    summary = orch.summarize_governed_actions_manifest(
        workspace / GOVERNED_ACTIONS_MANIFEST_REL,
        GOLDEN_COMMIT,
        now=GOLDEN_FRESH_NOW,
    )
    assert summary["status"] == "summarized"
    assert summary["trusted"] is True
    assert summary["phase"] == "pre-deploy"
    assert summary["verdict"] == "governed"
    assert summary["counts"] == {
        "pass": 1,
        "must_fix": 0,
        "should_fix": 0,
        "not_verified": 0,
        "not_applicable": 0,
    }
    assert summary["recommendation"] == []


def test_auto_recommends_rerun_for_a_stale_governed_actions_manifest(tmp_path):
    workspace = make_context(tmp_path, manifest=_golden_governed_actions_manifest())
    summary = orch.summarize_governed_actions_manifest(
        workspace / GOVERNED_ACTIONS_MANIFEST_REL,
        GOLDEN_COMMIT,
        now=GOLDEN_FRESH_NOW + timedelta(days=3),
    )
    assert summary["status"] == "rerun-recommended"
    assert summary["trusted"] is False
    assert "expired" in summary["reason"]
    assert summary["counts"] == {
        "pass": 0,
        "must_fix": 0,
        "should_fix": 0,
        "not_verified": 0,
        "not_applicable": 0,
    }
    # Rerun wording is exactly the approved lifecycle recommendation.
    assert summary["recommendation"] == [EXPECTED_HANDOFF["pre_deploy"]]


def test_auto_recommends_rerun_for_an_invalid_governed_actions_manifest(tmp_path):
    cases: dict[str, dict] = {}

    wrong_schema = _golden_governed_actions_manifest()
    wrong_schema["schema"] = "threadlight-governed-actions-manifest/v2"
    cases["schema"] = wrong_schema

    wrong_commit = _golden_governed_actions_manifest()
    wrong_commit["source"] = dict(wrong_commit["source"], commit="f" * 40)
    cases["commit"] = wrong_commit

    dirty = _golden_governed_actions_manifest()
    dirty["source"] = dict(dirty["source"], dirty=True)
    cases["dirty"] = dirty

    miscounted = _golden_governed_actions_manifest()
    miscounted["summary"] = {
        **miscounted["summary"],
        "pass": [],
        "must_fix": ["OPS-001"],
    }
    cases["summary"] = miscounted

    for label, manifest in cases.items():
        workspace = make_context(tmp_path / label, manifest=manifest)
        summary = orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL,
            GOLDEN_COMMIT,
            now=GOLDEN_FRESH_NOW,
        )
        assert summary["status"] == "rerun-recommended", label
        assert summary["trusted"] is False, label
        assert summary["verdict"] is None, label
        assert summary["recommendation"], label
        assert all(
            rec in EXPECTED_HANDOFF.values() for rec in summary["recommendation"]
        ), label

    # Unparseable JSON is untrusted too, and never crashes the orchestrator.
    broken = tmp_path / "broken"
    (broken / "tests").mkdir(parents=True)
    (broken / "tests" / "governed-actions-manifest.json").write_text(
        "{ not json", encoding="utf-8"
    )
    summary = orch.summarize_governed_actions_manifest(
        broken / GOVERNED_ACTIONS_MANIFEST_REL, GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW
    )
    assert summary["status"] == "rerun-recommended"
    assert summary["recommendation"] == [
        EXPECTED_HANDOFF["design"],
        EXPECTED_HANDOFF["pre_deploy"],
        EXPECTED_HANDOFF["post_deploy"],
    ]


def test_auto_recommends_rerun_for_a_non_string_finding_status(tmp_path):
    # An otherwise schema-valid, commit-bound, fresh manifest must still be
    # untrusted when a finding's status is untrusted JSON that isn't a
    # string (e.g. an array or object) rather than raising inside the
    # findings loop's `status not in observed` membership check.
    non_string_statuses: dict[str, object] = {
        "list": ["pass"],
        "object": {"value": "pass"},
    }

    for label, status in non_string_statuses.items():
        manifest = _golden_governed_actions_manifest()
        manifest["findings"][0]["status"] = status
        workspace = make_context(tmp_path / label, manifest=manifest)
        summary = orch.summarize_governed_actions_manifest(
            workspace / GOVERNED_ACTIONS_MANIFEST_REL,
            GOLDEN_COMMIT,
            now=GOLDEN_FRESH_NOW,
        )
        assert summary["status"] == "rerun-recommended", label
        assert summary["trusted"] is False, label
        assert "unknown id or status" in summary["reason"], label
        assert summary["verdict"] is None, label
        assert summary["recommendation"] == [EXPECTED_HANDOFF["pre_deploy"]], label


def test_auto_decision_surfaces_the_manifest_summary(tmp_path):
    manifest = _golden_governed_actions_manifest()
    workspace = make_context(tmp_path, consequential_actions=True, manifest=manifest)
    decision = decide(workspace)
    summary = decision["governed_actions_manifest"]
    assert summary is not None
    # The committed golden is long expired against wall-clock now, so auto must
    # recommend a rerun rather than believe it.
    assert summary["status"] == "rerun-recommended"
    assert summary["manifest"] == GOVERNED_ACTIONS_MANIFEST_REL


def test_auto_output_carries_no_privileged_commands(tmp_path):
    """The governed-actions surface authors recommendations, never commands.

    Scoped to that surface on purpose: auto legitimately drives `azd up` at its
    own deploy stage, and this guard is about what the governed-actions handoff
    hands an agent, not about the pilot driver's existing stage prose.
    """
    workspace = make_context(
        tmp_path,
        consequential_actions=True,
        manifest=_golden_governed_actions_manifest(),
    )
    decision = decide(workspace)
    blob = json.dumps(
        {
            "governed_actions": decision["governed_actions"],
            "governed_actions_manifest": decision["governed_actions_manifest"],
        }
    )
    for approved in EXPECTED_HANDOFF.values():
        blob = blob.replace(approved, "")
    low = blob.casefold()
    for token in FORBIDDEN_COMMAND_TOKENS:
        assert token not in low, token


def test_auto_never_imports_or_executes_the_governed_actions_skill():
    source = Path(orch.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "threadlight-governed-actions/scripts",
        '"threadlight-governed-actions"',
        "threadlight_governed_actions",
        "import probes",
        "import render",
        "import scaffold",
        "import governed_actions",
    ):
        assert forbidden not in source, forbidden
    # The summary is a pure read of committed JSON: no producer module is
    # imported as a side effect of running it.
    before = set(sys.modules)
    orch.summarize_governed_actions_manifest(
        Path("does-not-exist.json"), GOLDEN_COMMIT, now=GOLDEN_FRESH_NOW
    )
    assert not {
        name
        for name in set(sys.modules) - before
        if name in {"probes", "render", "scaffold", "governed_actions", "mediation"}
    }


def test_auto_human_output_prints_recommendations_only(tmp_path, capsys):
    workspace = make_context(
        tmp_path,
        consequential_actions=True,
        manifest=_golden_governed_actions_manifest(),
    )
    orch._print_human(decide(workspace))
    printed = capsys.readouterr().out
    for key in ("design", "pre_deploy", "post_deploy"):
        assert EXPECTED_HANDOFF[key] in printed
    assert "execution: manual-explicit" in printed
    # Scoped to the governed-actions block: the pilot driver's own deploy
    # stage legitimately says it will run `azd up`.
    block = printed[printed.index("Governed actions") :]
    for approved in EXPECTED_HANDOFF.values():
        block = block.replace(approved, "")
    low = block.casefold()
    for token in ("--scaffold", "--apply", "--canary", "azd up", "gh pr merge"):
        assert token not in low, token


def test_auto_human_output_stays_silent_without_a_signal(tmp_path, capsys):
    orch._print_human(decide(make_context(tmp_path)))
    assert "governed actions" not in capsys.readouterr().out.casefold()


def run(workspace: Path) -> dict:
    out = subprocess.run(
        [sys.executable, str(ORCH), "--workspace", str(workspace), "--dry-run", "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if not out.stdout.strip():
        raise RuntimeError(f"orchestrator emitted no JSON for {workspace}; stderr={out.stderr!r}")
    return json.loads(out.stdout)


def _standalone_manual_handoff_checks() -> int:
    """Manual-handoff assertions for the standalone runner (no pytest fixtures)."""
    failures = 0
    with tempfile.TemporaryDirectory(prefix="threadlight-handoffs-") as tmp:
        ws = Path(tmp)
        decision = orch.decide(ws)
        order = [h["skill"] for h in decision["manual_handoffs"]]
        expected = [
            "threadlight-connect", "threadlight-ground",
            "threadlight-loadtest", "threadlight-upgrade",
        ]
        if order != expected:
            print(f"❌ manual_handoffs order: expected {expected}, got {order}")
            failures += 1
        elif {"connect", "ground", "loadtest", "upgrade"}.intersection(decision["stages"]):
            print("❌ live legs leaked into stages")
            failures += 1
        else:
            print("✅ manual handoffs ordered + excluded from stages")

        specs = ws / "specs"
        specs.mkdir()
        (specs / "connect-manifest.json").write_text(
            _leg_envelope("threadlight-connect-manifest/v1", "complete"), encoding="utf-8")
        (specs / "load-manifest.json").write_text(
            _leg_envelope("threadlight.load/v1", "aborted"), encoding="utf-8")
        by_skill = {h["skill"]: h["status"] for h in orch.decide(ws)["manual_handoffs"]}
        if by_skill["threadlight-connect"] != "complete" or by_skill["threadlight-loadtest"] != "aborted":
            print(f"❌ manual handoff status mismatch: {by_skill}")
            failures += 1
        else:
            print("✅ manual handoff status reflects envelope")
    return failures


def main() -> int:
    cases = [
        ("blank",        "run",       {"preflight", "design", "deploy", "safe_check", "cost_projection", "invoke", "evals", "redteam", "govern"}, set()),
        # NOTE: all-complete fixture predates cost_projection + the discover/protect
        # legs; no cost-manifest.json → cost_projection runs, and the cascade plus
        # absent leg manifests make evals/redteam/govern run too.
        ("all-complete", "run",       {"cost_projection", "invoke", "evals", "redteam", "govern"},                   {"preflight", "design", "deploy", "safe_check"}),
        ("hard-stop",    "hard_stop", None,                                                                         None),
        ("spec-edited",  "run",       None,                                                                         None),
    ]
    failures = 0
    for fixture_name, expected_type, expected_run, expected_skip in cases:
        fixture = FIXTURES / fixture_name
        if not fixture.exists():
            print(f"❌ {fixture_name}: fixture dir missing")
            failures += 1
            continue
        try:
            fixture_to_run = fixture
            with tempfile.TemporaryDirectory(prefix=f"threadlight-{fixture_name}-") as tmp:
                if fixture_name == "all-complete":
                    fixture_to_run = Path(tmp) / fixture_name
                    shutil.copytree(fixture, fixture_to_run)
                    for rel in (
                        ".threadlight/preflight-passed.json",
                        "docs/invoke-results.md",
                    ):
                        os.utime(fixture_to_run / rel)
                    postdeploy = fixture_to_run / "tests" / "postdeploy-manifest.json"
                    postdeploy_data = json.loads(postdeploy.read_text(encoding="utf-8"))
                    postdeploy_data["checked_at"] = _iso_now()
                    postdeploy.write_text(json.dumps(postdeploy_data), encoding="utf-8")
                report = run(fixture_to_run)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ {fixture_name}: orchestrator crashed: {exc!r}")
            failures += 1
            continue
        actual_type = report["next_action"]["type"]
        if actual_type != expected_type:
            print(f"❌ {fixture_name}: expected next_action.type={expected_type!r}, got {actual_type!r}")
            failures += 1
            continue
        if expected_run is not None:
            actual_run = set(report["next_action"].get("stages_to_run", []))
            if actual_run != expected_run:
                print(f"❌ {fixture_name}: stages_to_run mismatch; expected={sorted(expected_run)} actual={sorted(actual_run)}")
                failures += 1
                continue
        if expected_skip is not None:
            actual_skip = set(report["next_action"].get("stages_to_skip", []))
            if actual_skip != expected_skip:
                print(f"❌ {fixture_name}: stages_to_skip mismatch; expected={sorted(expected_skip)} actual={sorted(actual_skip)}")
                failures += 1
                continue
        if fixture_name == "spec-edited":
            if "design" not in set(report["next_action"].get("stages_to_run", [])):
                print(f"❌ spec-edited: expected 'design' in stages_to_run after hash mismatch; got {report['next_action'].get('stages_to_run')}")
                failures += 1
                continue
        print(f"✅ {fixture_name}: next_action.type={actual_type}")

    # --- extra: assert cost_projection is in STAGES between safe_check and invoke ---
    import importlib.util as _ilu, sys as _sys
    _s = _ilu.spec_from_file_location("_orch_check", str(ORCH))
    _m = _ilu.module_from_spec(_s)
    _sys.modules["_orch_check"] = _m
    _s.loader.exec_module(_m)
    stages = _m.STAGES
    if "cost_projection" not in stages:
        print("❌ STAGES: cost_projection not in STAGES list")
        failures += 1
    else:
        cp_idx = stages.index("cost_projection")
        sc_idx = stages.index("safe_check")
        inv_idx = stages.index("invoke")
        if not (sc_idx < cp_idx < inv_idx):
            print(f"❌ STAGES: cost_projection at index {cp_idx} not between safe_check ({sc_idx}) and invoke ({inv_idx})")
            failures += 1
        else:
            print(f"✅ STAGES order: safe_check({sc_idx}) < cost_projection({cp_idx}) < invoke({inv_idx})")

    # A fresh marker is reusable only while it remains bound to the exact
    # Foundation that passed runtime-policy validation.
    with tempfile.TemporaryDirectory(prefix="threadlight-foundation-created-") as tmp:
        workspace = Path(tmp)
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": "1.0.0", "foundation_sha256": None}), encoding="utf-8")
        foundation = workspace / "specs" / "foundation.md"
        foundation.parent.mkdir(parents=True)
        foundation.write_text("# Foundation\n", encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ foundation-created-after-preflight: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-created-after-preflight: preflight invalidated")

    with tempfile.TemporaryDirectory(prefix="threadlight-legacy-marker-") as tmp:
        workspace = Path(tmp)
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ legacy-marker-without-foundation-hash: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ legacy-marker-without-foundation-hash: preflight invalidated")

    with tempfile.TemporaryDirectory(prefix="threadlight-foundation-matching-") as tmp:
        workspace = Path(tmp)
        foundation = workspace / "specs" / "foundation.md"
        foundation.parent.mkdir(parents=True)
        foundation.write_text("# Foundation\n", encoding="utf-8")
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps({"version": "1.0.0", "foundation_sha256": _m._sha256(foundation)}),
            encoding="utf-8",
        )
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "skip":
            print(f"❌ foundation-hash-matches: expected skip, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-hash-matches: fresh preflight reused")

        foundation.write_text("# Foundation\n\nedited: true\n", encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ foundation-edited-after-preflight: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-edited-after-preflight: preflight invalidated")

    # --- extra: assert the discover/protect legs follow invoke in STAGES ---
    for leg in ("evals", "redteam", "govern"):
        if leg not in stages:
            print(f"❌ STAGES: {leg} not in STAGES list")
            failures += 1
        elif stages.index(leg) <= stages.index("invoke"):
            print(f"❌ STAGES: {leg} at index {stages.index(leg)} not after invoke ({stages.index('invoke')})")
            failures += 1
        else:
            print(f"✅ STAGES order: invoke({stages.index('invoke')}) < {leg}({stages.index(leg)})")

    # --- extra: manual-handoff projection (Task 7) ---
    failures += _standalone_manual_handoff_checks()

    print(f"\n=== {len(cases) + 1 - failures}/{len(cases) + 1} passed ===")
    return failures


if __name__ == "__main__":
    sys.exit(main())
