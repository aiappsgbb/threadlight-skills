"""Offline adapter regressions. Native fixtures are synthetic, never live proof."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL.parent / "_shared"))
import agentops as contract

SPEC = importlib.util.spec_from_file_location("agentops_check", SKILL / "scripts/agentops_check.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


class AgentOpsTests(unittest.TestCase):
    def test_unrelated_invalid_azd_metadata_does_not_require_agentops(self):
        self.write("azure.yaml", "local deployment artifact\n")
        self.assertEqual(contract.discover_opted_in_agents(self.repo), [])

    def test_invalid_azd_metadata_with_actual_optin_stays_invalid(self):
        self.write("azure.yaml", "local deployment artifact\n")
        self.optin("examples/selected-agent")
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.discover_opted_in_agents(self.repo)

    def setUp(self):
        self.repo = SKILL / "tests" / ".work" / uuid.uuid4().hex
        self.repo.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Offline fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, data):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return path

    def optin(self, root="."):
        self.write(str(Path(root) / "agentops.yaml"), "target: agent:fixture:1\ndataset: data.jsonl\n")
        self.write(str(Path(root) / "requirements.txt"), "agentops-accelerator==0.14.0\n")

    def fixture(self, *, quality=True, doctor_blocked=False, age_hours=1, redteam=False, now=None, signed=True):
        self.optin()
        reference = now or NOW
        start = (reference - timedelta(hours=age_hours, minutes=5)).isoformat()
        end = (reference - timedelta(hours=age_hours)).isoformat()
        target = {"kind": "foundry_prompt", "raw": "fixture:1", "protocol": None,
                  "name": "fixture", "version": "1", "url": None, "deployment": None}
        metric = 5.0 if quality else 4.0
        self.native = {
            "version": 1, "started_at": start, "finished_at": end, "duration_seconds": 300.0,
            "target": target, "dataset_path": "data.jsonl", "evaluators": ["relevance"],
            "rows": [{"row_index": 0, "input": "PRIVATE PROMPT", "expected": None,
                      "response": "PRIVATE RESPONSE", "context": None, "latency_seconds": 0.1,
                      "tool_calls": [{"private": "SECRET"}], "error": None,
                      "metrics": [{"name": "relevance", "value": metric, "error": None, "reason": "PRIVATE REASON"}]}],
            "aggregate_metrics": {"relevance": metric},
            "thresholds": [{"metric": "relevance", "criteria": ">=", "expected": ">=5",
                            "actual": f"{metric:g}", "passed": quality}],
            "summary": {"items_total": 1, "items_passed_all": 1, "items_pass_rate": 1.0,
                        "thresholds_total": 1, "thresholds_passed": int(quality),
                        "threshold_pass_rate": float(quality), "overall_passed": quality},
            "comparison": None, "config": {"execution": "local", "private": "SECRET CONFIG"},
        }
        self.write("data.jsonl", '{"input":"PRIVATE PROMPT","expected":"PRIVATE EXPECTED"}\n')
        self.write(".gitignore", ".agentops/\n.private-key.pem\n")
        self.write(".github/workflows/threadlight-ci.yml", "# Existing owner-approved Threadlight CI\n")
        self.write(".agentops/results/run-one/results.json", self.native)
        self.write(".agentops/results/latest/results.json", self.native)
        self.write(".agentops/operations/eval-analysis.json", {
            "version": 1, "directory": str(self.repo), "classification": "ready",
            "config_status": "ready", "dataset_status": "ready", "target_kind": "foundry_prompt",
            "scenario_hint": "qa", "complexity": "simple", "requires_copilot_adaptation": False,
            "copilot_skills_installed": False, "copilot_prompt": None, "signals": [], "warnings": [],
            "recommended_skills": [], "recommended_commands": [], "next_steps": [],
        })
        count = int(doctor_blocked)
        counts = {"critical": count, "warning": 0, "info": 0}
        findings = [{"severity": "critical", "title": "PRIVATE DOCTOR FINDING", "id": "synthetic",
                     "category": "security"}] if doctor_blocked else []
        self.history = {
            "timestamp": end, "findings_total": count, "findings_by_severity": counts,
            "findings_by_category": {"security": count}, "max_severity": "critical" if count else None,
            "sources_enabled": ["foundry"], "lookback_days": 1, "duration_seconds": 1.0,
            "findings": findings,
        }
        self.write(".agentops/agent/history.jsonl", json.dumps(self.history) + "\n")
        self.evidence = {
            "version": 1, "generated_at": end, "workspace": str(self.repo),
            "status": "blocked" if doctor_blocked or not quality else "ready",
            "target": target["raw"],
            "checks": [{"name": "Doctor readiness", "status": "blocked" if doctor_blocked else "ready", "summary": "PRIVATE"}]
                      + ([{"name": "Latest eval gate", "status": "blocked",
                           "summary": "Latest evaluation failed configured thresholds."}] if not quality else []),
            "blockers": (["PRIVATE BLOCKER"] if doctor_blocked else [])
                        + (["Latest evaluation failed configured thresholds."] if not quality else []),
            "warnings": [], "ready": [], "links": [],
            "doctor": {"status": "ok", "findings_total": count, "counts": counts,
                       "max_severity": self.history["max_severity"], "top_findings": findings},
            "latest_eval": {"status": "ok", "passed": quality, "started_at": start, "items_total": 1,
                            "items_passed_all": 1, "threshold_count": 1, "target": target["raw"]},
            "foundry": {"status": "ok", "agents_count": 1},
        }
        self.write(".agentops/release/latest/evidence.json", self.evidence)
        self.policy = {
            "schema": "threadlight-agentops-binding/v1",
            "target_sha256": contract.canonical_hash(target),
            "environment_sha256": contract.sha256(b"independently approved test environment"),
            "required_doctor_sources": ["foundry"],
            "thresholds": [{"metric": "relevance", "criteria": ">=", "expected": 5.0}],
        }
        if signed:
            self.policy.update(trust_key=".threadlight/agentops-trust.pem", require_signature=True)
        self.roles = {"result": ".agentops/results/run-one/results.json",
                      "latest": ".agentops/results/latest/results.json",
                      "evidence": ".agentops/release/latest/evidence.json",
                      "history": ".agentops/agent/history.jsonl",
                      "analysis": ".agentops/operations/eval-analysis.json",
                      "dataset": "data.jsonl", "workflow": ".github/workflows/threadlight-ci.yml"}
        if redteam:
            categories = sorted(contract.CORE_CATEGORIES)
            self.redteam = {
                "target": {"agent_name": "fixture", "agent_version": "1"},
                "risk_categories": categories, "attack_strategies": ["base64"],
                "num_objectives": 1, "total_attempts": 4, "successful_attacks": 0,
                "attack_success_rate": 0.0,
                "per_category": {name: {"total": 1, "successful": 0, "attack_success_rate": 0.0} for name in categories},
                "per_strategy": {"base64": {"total": 4, "successful": 0, "attack_success_rate": 0.0}},
                "output_path": ".agentops/redteam/latest.json", "raw_summary_path": None,
                "has_violations": False, "fail_threshold": 0.2, "generated_at": end,
            }
            self.redteam["target_fingerprint"] = contract.canonical_hash({
                key: self.redteam[key] for key in ("target", "risk_categories", "attack_strategies",
                                                  "num_objectives", "fail_threshold")})
            self.write(".agentops/redteam/latest.json", self.redteam)
            self.policy.update(redteam_fingerprint=self.redteam["target_fingerprint"], redteam_fail_threshold=0.2)
            self.roles["redteam"] = ".agentops/redteam/latest.json"
        self.write(".threadlight/agentops-binding.json", self.policy)
        if signed:
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                            "-out", str(self.repo / ".private-key.pem")], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(self.repo / ".private-key.pem"), "-pubout",
                            "-out", str(self.repo / self.policy["trust_key"])], check=True, capture_output=True)
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic fixture independent policy")
        self.record = {
            "schema": "threadlight-agentops-run-receipt/v1", "producer": "observed-run",
            "repository_commit": self.git("rev-parse", "HEAD"), "root": ".",
            "target_sha256": self.policy["target_sha256"], "environment_sha256": self.policy["environment_sha256"],
            "config_sha256": contract.sha256((self.repo / "agentops.yaml").read_bytes()),
            "package_version": "0.14.0", "upstream_sha": contract.UPSTREAM_SHA,
            "started_at": start, "finished_at": end, "run_id_sha256": contract.sha256(b"fixture run"),
            "approval_sha256": contract.sha256(b"synthetic existing-owner execution approval"),
            "artifacts": {},
        }
        if signed:
            self.sign()

    def sign(self):
        self.record["artifacts"] = {role: {"path": name, "sha256": contract.sha256((self.repo / name).read_bytes())}
                                    for role, name in self.roles.items()}
        receipt = self.write(".agentops/threadlight/receipt.json", self.record)
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.repo / ".private-key.pem"),
                        "-out", str(receipt.with_suffix(".sig")), str(receipt)], check=True, capture_output=True)

    def test_valid_signed_fixture_is_operational_not_governance_certification(self):
        self.fixture()
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "operational", result)
        self.assertEqual(result["agents"][0]["domains"]["evals"]["verdict"], "pass")
        self.assertFalse(result["agents"][0]["domains"]["govern"]["summary"]["runtime_verified"])
        contract.validate_manifest(result, repo=self.repo, now=NOW)

    def test_eval_quality_failure_and_doctor_blocker_are_not_readiness(self):
        self.fixture(quality=False, doctor_blocked=True)
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "blocked")
        domain = result["agents"][0]["domains"]["evals"]
        self.assertEqual((domain["status"], domain["verdict"]), ("verified", "fail"), result)
        self.assertEqual(domain["summary"]["thresholds"][0]["actual"], 4.0)
        self.assertIn("AOPS-DOCTOR-BLOCKED", [f["code"] for f in result["findings"]])

    def test_known_eval_blocker_keeps_exact_domain_owner_without_fake_doctor_failure(self):
        self.fixture(quality=False)
        result = check.assess(self.repo, now=NOW)
        findings = result["agents"][0]["findings"]
        self.assertEqual(result["verdict"], "blocked")
        self.assertIn({"code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix"}, findings)
        self.assertNotIn("AOPS-DOCTOR-BLOCKED", [item["code"] for item in findings])

    def test_unmatched_duplicate_native_blocker_is_not_silently_deduplicated(self):
        self.fixture(quality=False)
        self.evidence["blockers"].append("Latest evaluation failed configured thresholds.")
        self.write(self.roles["evidence"], self.evidence)
        self.sign()
        result = check.assess(self.repo, now=NOW)
        codes = {item["code"] for item in result["agents"][0]["findings"]}
        self.assertTrue({"AOPS-EVAL-QUALITY", "AOPS-DOCTOR-BLOCKED"} <= codes)

    def test_known_redteam_breach_keeps_exact_domain_owner(self):
        self.fixture(redteam=True)
        self.redteam.update(successful_attacks=1, attack_success_rate=0.25, has_violations=True)
        self.redteam["per_category"]["violence"].update(successful=1, attack_success_rate=1.0)
        self.redteam["per_strategy"]["base64"].update(successful=1, attack_success_rate=0.25)
        self.write(self.roles["redteam"], self.redteam)
        message = "PRIVATE native redteam breach summary"
        self.evidence["status"] = "blocked"
        self.evidence["blockers"] = [message]
        self.evidence["checks"].append({"name": "Red team readiness", "status": "blocked", "summary": message,
            "evidence": {"state": "threshold_breach", "attack_success_rate": 0.25,
                         "threshold": 0.2, "target_verified": True}})
        self.write(self.roles["evidence"], self.evidence)
        self.sign()
        result = check.assess(self.repo, now=NOW)
        findings = result["agents"][0]["findings"]
        self.assertIn({"code": "AOPS-REDTEAM-QUALITY", "owner": "redteam", "severity": "must-fix"}, findings)
        self.assertNotIn("AOPS-DOCTOR-BLOCKED", [item["code"] for item in findings])
        self.assertNotIn(message, json.dumps(result))

    def test_existing_root_azure_pipelines_workflow_is_supported(self):
        self.fixture()
        self.write("azure-pipelines.yml", "# Existing owner-approved Azure DevOps pipeline\n")
        self.roles["workflow"] = "azure-pipelines.yml"
        self.git("add", "azure-pipelines.yml")
        self.git("commit", "-qm", "Select synthetic existing ADO workflow")
        self.record["repository_commit"] = self.git("rev-parse", "HEAD")
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "operational")

    def test_forged_receipt_cannot_be_rehashed_to_green(self):
        self.fixture()
        self.record["target_sha256"] = contract.sha256(b"forged target")
        self.write(".agentops/threadlight/receipt.json", self.record)
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(result["agents"][0]["domains"]["evals"]["verdict"], "unknown")

    def test_latest_alias_mismatch_is_integrity_failure_even_with_new_signed_receipt(self):
        self.fixture()
        self.native["config"]["private"] = "changed mutable latest"
        self.write(".agentops/results/latest/results.json", self.native)
        self.sign()
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "blocked")

    def test_signed_stale_evidence_is_partial_not_pass(self):
        self.fixture(age_hours=48)
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "partial", result)
        self.assertEqual(result["agents"][0]["domains"]["evals"]["status"], "stale")

    def test_disabled_doctor_source_is_not_verified(self):
        self.fixture()
        self.evidence["foundry"]["status"] = "skipped"
        self.write(".agentops/release/latest/evidence.json", self.evidence)
        self.sign()
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "partial")
        self.assertEqual(result["agents"][0]["capabilities"]["doctor_freshness"]["status"], "not-verified")

    def test_redteam_uses_real_per_category_counts(self):
        self.fixture(redteam=True)
        result = check.assess(self.repo, now=NOW)
        domain = result["agents"][0]["domains"]["redteam"]
        self.assertEqual((domain["status"], domain["verdict"]), ("verified", "pass"), result)
        self.assertEqual(domain["summary"]["per_category"]["violence"]["total"], 1)

    def test_domain_summaries_preserve_source_timestamps_for_stricter_consumers(self):
        self.fixture(redteam=True)
        result = check.assess(self.repo, now=NOW)
        domains = result["agents"][0]["domains"]
        self.assertEqual(domains["evals"]["summary"]["finished_at"], self.native["finished_at"])
        self.assertEqual(domains["redteam"]["summary"]["generated_at"], self.redteam["generated_at"])

    def test_zero_count_scan_is_not_a_pass(self):
        self.fixture(redteam=True)
        self.redteam.update(total_attempts=0)
        self.write(".agentops/redteam/latest.json", self.redteam)
        self.sign()
        result = check.assess(self.repo, now=NOW)
        self.assertNotEqual(result["agents"][0]["domains"]["redteam"]["verdict"], "pass")

    def test_privacy_is_strict_allowlist(self):
        self.fixture(doctor_blocked=True, redteam=True)
        result = check.assess(self.repo, now=NOW)
        encoded = json.dumps(result)
        for sentinel in ("PRIVATE", "SECRET", str(self.repo), "tool_calls", "top_findings"):
            self.assertNotIn(sentinel, encoded)

    def test_manifest_emit_does_not_invalidate_itself(self):
        self.fixture()
        result = check.assess(self.repo, now=NOW)
        self.write("specs/agentops-manifest.json", result)
        self.assertEqual(contract.load_manifest(self.repo, now=NOW), result)

    def test_same_pass_domain_reports_and_auto_state_do_not_invalidate_source_binding(self):
        self.fixture()
        result = check.assess(self.repo, now=NOW)
        self.write("specs/agentops-manifest.json", result)
        for path in ("specs/evals-manifest.json", "specs/redteam-manifest.json",
                     "specs/governance-manifest.json", "docs/evals-report.md",
                     "docs/redteam-report.md", "docs/agt-governance-report.md",
                     ".threadlight/auto-state.json", ".threadlight/auto-next.json",
                     "tests/production-readiness-manifest.json", "docs/production-readiness-report.md",
                     "tests/mcp-sbom.json", "tests/agent-identity.json"):
            self.write(path, "{}")
        self.assertEqual(contract.load_manifest(self.repo, now=NOW), result)
        self.write("agent.py", "changed actual agent implementation")
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.load_manifest(self.repo, now=NOW)

    def test_unknown_eval_fields_are_not_native_contract(self):
        self.fixture()
        self.native["invented_repository_binding"] = "not native"
        for name in ("result", "latest"):
            self.write(self.roles[name], self.native)
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_fabricated_analysis_readiness_is_not_accepted(self):
        self.fixture()
        self.write(self.roles["analysis"], {"version": 1, "config_status": "ready",
            "dataset_status": "ready", "requires_copilot_adaptation": False})
        self.sign()
        self.assertNotEqual(check.assess(self.repo, now=NOW)["verdict"], "operational")

    def test_dataset_row_count_is_checked_privately(self):
        self.fixture()
        self.write("data.jsonl", '{"input":"extra"}\n{"input":"unexpected"}\n')
        self.git("add", "data.jsonl")
        self.git("commit", "-qm", "Change dataset fixture")
        self.record["repository_commit"] = self.git("rev-parse", "HEAD")
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_missing_real_strategy_coverage_not_verified(self):
        self.fixture(redteam=True)
        self.redteam["per_strategy"] = {}
        self.write(self.roles["redteam"], self.redteam)
        self.sign()
        self.assertNotEqual(check.assess(self.repo, now=NOW)["agents"][0]["domains"]["redteam"]["verdict"], "pass")

    def test_native_release_malformed_without_receipt_is_still_blocked(self):
        self.optin()
        self.write(".agentops/release/latest/evidence.json", "{ malformed")
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_edited_domain_summary_rejected(self):
        self.fixture()
        result = check.assess(self.repo, now=NOW)
        result["agents"][0]["domains"]["evals"]["summary"]["aggregate_metrics"]["relevance"] = 100
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.validate_manifest(result, repo=self.repo, now=NOW)

    def test_signed_future_native_timestamps_rejected(self):
        self.fixture(age_hours=-1)
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def comparison_fixture(self):
        self.fixture()
        baseline = copy.deepcopy(self.native)
        baseline["started_at"] = (NOW - timedelta(hours=2, minutes=5)).isoformat()
        baseline["finished_at"] = (NOW - timedelta(hours=2)).isoformat()
        baseline["rows"][0]["metrics"][0]["value"] = 4.0
        baseline["aggregate_metrics"]["relevance"] = 4.0
        baseline["thresholds"][0].update(actual="4", passed=False)
        baseline["summary"].update(thresholds_passed=0, threshold_pass_rate=0.0, overall_passed=False)
        path = self.write(".agentops/baseline/results.json", baseline)
        self.roles["baseline"] = ".agentops/baseline/results.json"
        self.policy.update(baseline_sha256=contract.sha256(path.read_bytes()),
            baseline_dataset_sha256=contract.sha256((self.repo / "data.jsonl").read_bytes()),
            baseline_target_sha256=self.policy["target_sha256"])
        self.write(".threadlight/agentops-binding.json", self.policy)
        self.git("add", ".threadlight/agentops-binding.json")
        self.git("commit", "-qm", "Approve synthetic baseline")
        self.record["repository_commit"] = self.git("rev-parse", "HEAD")
        self.native["comparison"] = {
            "baseline_path": self.roles["baseline"], "baseline_started_at": baseline["started_at"],
            "baseline_overall_passed": False,
            "metrics": [{"metric": "relevance", "current": 5.0, "baseline": 4.0, "delta": 1.0, "direction": "improved"}],
            "rows": [{"row_index": 0, "current_passed": True, "baseline_passed": True, "direction": "unchanged"}],
        }
        for role in ("result", "latest"):
            self.write(self.roles[role], self.native)
        self.sign()

    def test_comparison_requires_approved_baseline_hash_and_valid_deltas(self):
        self.comparison_fixture()
        result = check.assess(self.repo, now=NOW)
        summary = result["agents"][0]["domains"]["evals"]["summary"]
        self.assertEqual(summary["comparison"], {"status": "verified", "regressions": 0})

    def test_forged_comparison_delta_is_integrity_failure(self):
        self.comparison_fixture()
        self.native["comparison"]["metrics"][0]["delta"] = 100
        for role in ("result", "latest"):
            self.write(self.roles[role], self.native)
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_boolean_numeric_substitution_is_not_manifest_validation(self):
        result = check.assess(self.repo, now=NOW)
        result["summary"]["agents_total"] = False
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.validate_manifest(result, repo=self.repo, now=NOW)

    def test_extra_doctor_config_cannot_escape_run_binding(self):
        self.fixture()
        self.write(".agentops/agent.yaml", "sources:\n  foundry:\n    enabled: false\n")
        result = check.assess(self.repo, now=NOW)
        self.assertNotEqual(result["verdict"], "operational")

    def test_changed_config_is_invalid_not_merely_dirty(self):
        self.fixture()
        self.write("agentops.yaml", "agent: different:99\n")
        result = check.assess(self.repo, now=NOW)
        self.assertEqual(result["verdict"], "blocked")

    def test_supported_bundle_installs_only_existing_signed_observation(self):
        self.fixture()
        spec = importlib.util.spec_from_file_location("bundle_receipt", SKILL / "scripts/bundle_receipt.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for suffix in ("json", "sig"):
            (self.repo / f".agentops/threadlight/receipt.{suffix}").rename(
                self.repo / f".agentops/operations/observed.{suffix}")
        module.bundle(self.repo, agent_root=".", record=".agentops/operations/observed.json",
                      signature=".agentops/operations/observed.sig", now=NOW)
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "operational")
        with self.assertRaises(contract.AgentOpsValidationError):
            module.bundle(self.repo, agent_root=".", record=".agentops/operations/observed.json",
                          signature=".agentops/operations/observed.sig", now=NOW)

    def test_bounded_command_stops_excess_output_and_timeout(self):
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.bounded_command([sys.executable, "-c", "print('x'*10000)"], cwd=self.repo, max_bytes=100)
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.bounded_command([sys.executable, "-c", "import time;time.sleep(10)"],
                                     cwd=self.repo, timeout=0.05)

    def test_cli_json_is_normalized_and_refresh_does_not_execute(self):
        self.optin()
        script = SKILL / "scripts/agentops_check.py"
        result = subprocess.run([sys.executable, str(script), "--target", str(self.repo), "--json", "--gate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["verdict"], "partial")
        refresh = subprocess.run([sys.executable, str(script), "--target", str(self.repo),
                                  "--refresh-doctor", "--agentops-bin", str(self.repo / "unavailable")],
                                 capture_output=True, text=True)
        self.assertEqual(refresh.returncode, 1)
        self.assertEqual(refresh.stdout, "")
        self.assertNotIn(str(self.repo), refresh.stderr)

    def test_cli_gate_is_two_only_for_must_fix(self):
        self.optin()
        self.write(".agentops/results/latest/results.json", "{malformed PRIVATE SECRET")
        result = subprocess.run([sys.executable, str(SKILL / "scripts/agentops_check.py"),
                                 "--target", str(self.repo), "--json", "--gate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("PRIVATE", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["verdict"], "blocked")

    def test_native_row_errors_are_not_hidden_by_overall_true(self):
        self.fixture()
        self.native["rows"][0]["metrics"][0]["error"] = "PRIVATE ERROR"
        for role in ("result", "latest"):
            self.write(self.roles[role], self.native)
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_history_findings_counts_must_match_real_findings(self):
        self.fixture(doctor_blocked=True)
        self.history["findings"] = []
        self.write(self.roles["history"], json.dumps(self.history) + "\n")
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_native_absolute_in_root_dataset_path_is_supported_privately(self):
        self.fixture()
        self.native["dataset_path"] = str(self.repo / "data.jsonl")
        for role in ("result", "latest"):
            self.write(self.roles[role], self.native)
        self.sign()
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "operational")

    def test_public_schema_matches_generated_allowlist(self):
        from jsonschema import Draft7Validator, FormatChecker
        schema = json.loads((SKILL / "references/agentops-manifest.schema.json").read_text())
        Draft7Validator.check_schema(schema)
        self.fixture(redteam=True)
        result = check.assess(self.repo, now=NOW)
        Draft7Validator(schema, format_checker=FormatChecker()).validate(result)
        result["agents"][0]["domains"]["govern"]["summary"]["runtime_verified"] = True
        self.assertTrue(list(Draft7Validator(schema).iter_errors(result)))

    def test_external_symlinked_artifact_is_not_read(self):
        self.fixture()
        result_path = self.repo / self.roles["latest"]
        result_path.unlink()
        result_path.symlink_to(self.repo / "data.jsonl")
        self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "blocked")

    def test_invalid_cli_flags_do_not_use_must_fix_exit_or_echo_values(self):
        result = subprocess.run([sys.executable, str(SKILL / "scripts/agentops_check.py"),
                                 "--no-preflight", "PRIVATE SECRET"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("PRIVATE SECRET", result.stderr)

    def test_emit_cannot_overwrite_native_inputs(self):
        self.optin()
        original = (self.repo / "agentops.yaml").read_bytes()
        result = subprocess.run([sys.executable, str(SKILL / "scripts/agentops_check.py"),
                                 "--target", str(self.repo), "--emit", "agentops.yaml"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.repo / "agentops.yaml").read_bytes(), original)

    def test_consumer_fixture_helper_produces_loadable_healthy_and_partial(self):
        spec = importlib.util.spec_from_file_location("agentops_fixture_helpers",
            SKILL / "tests/fixture_helpers.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        for state, verdict in (("healthy", "operational"), ("partial", "partial")):
            with self.subTest(state=state):
                repo = self.repo / state
                result = helper.create_agentops_fixture(repo, state=state, now=NOW)
                self.assertEqual(result["verdict"], verdict)
                self.assertEqual(contract.load_manifest(repo, now=NOW), result)
        with self.assertRaises(ValueError):
            helper.create_agentops_fixture(self.repo / "healthy", now=NOW)

    def observe_fake_command(self, *, operation="eval", exit_code=None):
        script = self.write(".agentops/operations/fake_native.py", '''
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
now = datetime.now(timezone.utc).isoformat()
root = Path(".")
result = json.loads((root / ".agentops/results/latest/results.json").read_text())
if sys.argv[1] == "eval":
    result.update(started_at=now, finished_at=now, duration_seconds=0.0)
    for name in (".agentops/results/run-one/results.json", ".agentops/results/latest/results.json"):
        (root / name).write_text(json.dumps(result))
evidence = json.loads((root / ".agentops/release/latest/evidence.json").read_text())
evidence["generated_at"] = now
evidence["latest_eval"]["started_at"] = result["started_at"]
(root / ".agentops/release/latest/evidence.json").write_text(json.dumps(evidence))
history = json.loads((root / ".agentops/agent/history.jsonl").read_text().splitlines()[-1])
history["timestamp"] = now
with (root / ".agentops/agent/history.jsonl").open("a") as stream:
    stream.write(json.dumps(history) + "\\n")
print("PRIVATE bounded native output")
''')
        token = contract.begin_observation(self.repo, self.repo, operation=operation,
            run_id_sha256=contract.sha256(uuid.uuid4().hex.encode()),
            approval_sha256=contract.sha256(b"existing synthetic owner execution approval"),
            artifact_paths=self.roles)
        code, out, _ = contract.bounded_command(
            [sys.executable, str(script), operation], cwd=self.repo, timeout=10, max_bytes=4096)
        self.assertIn(b"PRIVATE", out)
        return contract.finish_observation(token, exit_code=code if exit_code is None else exit_code)

    def test_same_process_observation_roundtrip_without_pki(self):
        self.fixture(signed=False)
        record = self.observe_fake_command()
        self.assertIn("observation", record)
        self.assertFalse((self.repo / ".private-key.pem").exists())
        result = check.assess(self.repo)
        self.assertEqual(result["verdict"], "operational", result)
        self.write("specs/agentops-manifest.json", result)
        self.assertEqual(contract.load_manifest(self.repo), result)

    def test_observed_exit_two_cannot_wrap_success_shaped_eval(self):
        self.fixture(signed=False)
        with self.assertRaises(contract.AgentOpsValidationError):
            self.observe_fake_command(exit_code=2)
        self.assertFalse((self.repo / ".agentops/threadlight/receipt.json").exists())

    def test_observed_exit_two_cannot_wrap_success_shaped_doctor(self):
        self.fixture(signed=False)
        self.observe_fake_command()
        receipt = self.repo / ".agentops/threadlight/receipt.json"
        original = receipt.read_bytes()
        with self.assertRaises(contract.AgentOpsValidationError):
            self.observe_fake_command(operation="doctor", exit_code=2)
        self.assertEqual(receipt.read_bytes(), original)

    def test_local_capture_supports_readonly_consumers_and_sequential_emission(self):
        import contextlib
        import io
        self.fixture(signed=False)
        self.observe_fake_command()
        observed = check.assess(self.repo)
        self.write("specs/agentops-manifest.json", observed)
        modules = {}
        for domain in ("evals", "redteam"):
            path = SKILL.parent / f"threadlight-{domain}/scripts/{domain}_check.py"
            spec = importlib.util.spec_from_file_location(f"_local_capture_{domain}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[domain] = module
        self.assertEqual(modules["evals"].evaluate(str(self.repo))["latest_pass_rate_ok"]["status"], "pass")
        self.assertEqual(modules["redteam"].evaluate(str(self.repo))["capabilities"]["harmful_content_asr_ok"]["status"],
                         "not-verified")
        with contextlib.redirect_stdout(io.StringIO()):
            modules["evals"].main(["--target", str(self.repo), "--emit"])
            modules["redteam"].main(["--target", str(self.repo), "--emit"])
        self.assertEqual(contract.load_manifest(self.repo), observed)

    def test_local_eval_does_not_bless_unobserved_old_redteam(self):
        self.fixture(signed=False, redteam=True)
        record = self.observe_fake_command()
        self.assertNotIn("redteam", record["artifacts"])
        self.assertEqual(check.assess(self.repo)["agents"][0]["domains"]["redteam"]["status"], "not-verified")

    def test_observation_rejects_dataset_changed_during_invocation(self):
        self.fixture(signed=False)
        token = contract.begin_observation(self.repo, self.repo, operation="eval",
            run_id_sha256=contract.sha256(b"run"), approval_sha256=contract.sha256(b"approval"),
            artifact_paths=self.roles)
        self.write("data.jsonl", '{"input":"changed"}\n')
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.finish_observation(token, exit_code=0)
        self.assertFalse((self.repo / ".agentops/threadlight/receipt.json").exists())

    def test_same_process_doctor_refresh_preserves_prior_eval_binding(self):
        self.fixture(signed=False)
        original = self.observe_fake_command()
        refreshed = self.observe_fake_command(operation="doctor")
        self.assertEqual(refreshed["artifacts"]["result"], original["artifacts"]["result"])
        self.assertEqual(refreshed["operation"], "doctor")
        self.assertEqual(check.assess(self.repo)["verdict"], "operational")

    def test_eval_observation_does_not_require_or_refresh_doctor_implicitly(self):
        self.fixture(signed=False)
        self.roles.pop("evidence")
        self.roles.pop("history")
        self.observe_fake_command()
        result = check.assess(self.repo)
        self.assertEqual(result["verdict"], "partial")
        self.assertEqual(result["agents"][0]["domains"]["evals"]["status"], "verified")
        self.assertEqual(result["agents"][0]["capabilities"]["doctor_freshness"]["status"], "not-verified")

    def test_refresh_cli_delegates_to_existing_approved_runtime(self):
        from unittest.mock import Mock, patch
        import contextlib
        import io
        self.fixture(signed=False)
        self.observe_fake_command()
        runtime = Mock()
        runtime.main.return_value = 0
        with patch.object(check, "load_runtime", return_value=runtime):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = check.main(["--target", str(self.repo), "--refresh-doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["verdict"], "operational")
        runtime.main.assert_called_once_with(["--repo", str(self.repo), "--refresh-doctor"])

    def test_observation_cannot_relabel_unchanged_unsigned_old_native_outputs(self):
        self.fixture(signed=False)
        token = contract.begin_observation(self.repo, self.repo, operation="eval",
            run_id_sha256=contract.sha256(b"new run"), approval_sha256=contract.sha256(b"approval"),
            artifact_paths=self.roles)
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.finish_observation(token, exit_code=0)
        self.assertFalse((self.repo / ".agentops/threadlight/receipt.json").exists())

    def test_inferred_result_cannot_anchor_a_preexisting_candidate(self):
        from unittest.mock import patch
        self.fixture(signed=False)
        current = datetime.now(timezone.utc)
        cached = copy.deepcopy(self.native)
        cached.update(started_at=current.isoformat(), finished_at=current.isoformat(), duration_seconds=0.0)
        self.write(self.roles["result"], cached)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return current

        paths = {key: value for key, value in self.roles.items()
                 if key not in {"result", "evidence", "history"}}
        with patch.object(contract, "datetime", Frozen):
            token = contract.begin_observation(self.repo, self.repo, operation="eval",
                run_id_sha256=contract.sha256(b"new-observation"), approval_sha256=contract.sha256(b"approval"),
                artifact_paths=paths)
            contract.bounded_command([sys.executable, "-c",
                "import shutil;shutil.copyfile('.agentops/results/run-one/results.json',"
                "'.agentops/results/latest/results.json')"], cwd=self.repo)
            with self.assertRaises(contract.AgentOpsValidationError):
                contract.finish_observation(token, exit_code=0)
        self.assertFalse((self.repo / ".agentops/threadlight/receipt.json").exists())

    def test_unsigned_native_receipt_without_process_observation_is_not_verified(self):
        self.fixture(signed=False)
        self.record["artifacts"] = {role: {"path": name, "sha256": contract.sha256((self.repo / name).read_bytes())}
                                   for role, name in self.roles.items()}
        self.write(".agentops/threadlight/receipt.json", self.record)
        result = check.assess(self.repo)
        self.assertEqual(result["verdict"], "partial")
        self.assertNotEqual(result["agents"][0]["domains"]["evals"]["status"], "verified")

    def test_no_optin_is_nonpenalizing_and_cache_is_not_optin(self):
        self.write(".agentops/results/latest/results.json", {})
        manifest = check.assess(self.repo, now=NOW)
        self.assertEqual(manifest["verdict"], "not-applicable")
        self.assertEqual(manifest["agents"], [])
        contract.validate_manifest(manifest, repo=self.repo, now=NOW)

    def test_multiple_roots_and_basename_collisions(self):
        self.optin("a/bot")
        self.optin("b/bot")
        roots = contract.discover_opted_in_agents(self.repo)
        self.assertEqual({item["root"] for item in roots}, {"a/bot", "b/bot"})
        self.assertEqual(len({item["agent_key"] for item in roots}), 2)

    def test_fallback_does_not_adopt_catalog_documentation_or_test_fixtures(self):
        for name in ("tests", "fixtures", "examples", "docs", "samples", "skills",
                     "catalog", "dist", "build", "coverage"):
            self.optin(f"{name}/sample")
        self.assertEqual(contract.discover_opted_in_agents(self.repo), [])

    def test_azd_agent_services_are_authoritative_and_preserve_service_metadata(self):
        self.write("azure.yaml", "services:\n  selected:\n    host: azure.ai.agent\n"
                   "    project: ./apps/selected\n  web:\n    host: containerapp\n"
                   "    project: ./apps/other\n")
        self.optin("apps/selected")
        self.optin("apps/other")
        roots = contract.discover_opted_in_agents(self.repo)
        self.assertEqual([(item["root"], item["service"]) for item in roots], [("apps/selected", "selected")])

    def test_explicit_azd_service_can_select_example_root(self):
        self.write("azure.yaml", "services:\n  agent:\n    host: azure.ai.agent\n"
                   "    project: examples/deployed-agent\n")
        self.optin("examples/deployed-agent")
        self.optin("examples/unselected-agent")
        roots = contract.discover_opted_in_agents(self.repo)
        self.assertEqual([item["root"] for item in roots], ["examples/deployed-agent"])

    def test_foundry_metadata_roots_are_authoritative_without_parsing_payload(self):
        self.write("apps/selected/.foundry/agent-metadata.yaml", "PRIVATE opaque metadata: [")
        self.optin("apps/selected")
        self.optin("apps/unrelated")
        roots = contract.discover_opted_in_agents(self.repo)
        self.assertEqual([item["root"] for item in roots], ["apps/selected"])

    def test_explicit_service_traversal_or_symlink_is_rejected(self):
        self.optin()
        self.write("azure.yaml", "services:\n  agent:\n    host: azure.ai.agent\n"
                   "    project: ../outside\n")
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.discover_opted_in_agents(self.repo)
        self.write("azure.yaml", "services:\n  agent:\n    host: azure.ai.agent\n"
                   "    project: linked\n")
        (self.repo / "linked").symlink_to("outside", target_is_directory=True)
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.discover_opted_in_agents(self.repo)

    def test_no_optin_azure_project_does_not_need_yaml_dependency(self):
        from unittest.mock import patch
        self.write("azure.yaml", "services:\n  agent:\n    host: azure.ai.agent\n    project: app\n")
        with patch.dict(sys.modules, {"yaml": None}):
            self.assertEqual(contract.discover_opted_in_agents(self.repo), [])
            self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "not-applicable")

    def test_no_optin_does_not_validate_unrelated_malformed_azure_yaml(self):
        from unittest.mock import patch
        for text in ("unrelated legacy Azure metadata: [", "local deployment artifact\n"):
            with self.subTest(metadata=text):
                self.write("azure.yaml", text)
                with patch.object(contract, "_azd_agent_roots", side_effect=AssertionError("unselected metadata")):
                    self.assertEqual(contract.discover_opted_in_agents(self.repo), [])
                    self.assertEqual(check.assess(self.repo, now=NOW)["verdict"], "not-applicable")

    def test_scalar_azure_metadata_stays_invalid_when_an_agent_really_opts_in(self):
        self.optin()
        self.write("azure.yaml", "local deployment artifact\n")
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.discover_opted_in_agents(self.repo)

    def test_marker_safety_is_checked_before_unrelated_azure_metadata(self):
        self.write("opaque.yaml", "agent: fixture:1\n")
        (self.repo / "agentops.yaml").symlink_to("opaque.yaml")
        self.write("azure.yaml", "local deployment artifact\n")
        with self.assertRaisesRegex(contract.AgentOpsValidationError, "symlink-path"):
            contract.discover_opted_in_agents(self.repo)

    def test_config_is_opaque_and_missing_evidence_is_partial(self):
        self.optin()
        self.write("agentops.yaml", "!!untrusted not parsed: [")
        manifest = check.assess(self.repo, now=NOW)
        self.assertEqual(manifest["verdict"], "partial")
        self.assertEqual(manifest["agents"][0]["capabilities"]["binding"]["status"], "not-verified")

    def test_symlink_optin_is_rejected_not_hidden(self):
        self.write("real.yaml", "sensitive")
        (self.repo / "agentops.yaml").symlink_to("real.yaml")
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.discover_opted_in_agents(self.repo)

    def test_unknown_manifest_schema_and_hidden_extra_fields_rejected(self):
        manifest = check.assess(self.repo, now=NOW)
        for field, value in (("schema", "threadlight-agentops-manifest/v2"), ("rows", [{"input": "secret"}])):
            bad = copy.deepcopy(manifest)
            bad[field] = value
            with self.assertRaises(contract.AgentOpsValidationError):
                contract.validate_manifest(bad, repo=self.repo, now=NOW)

    def test_new_optin_invalidates_old_manifest(self):
        manifest = check.assess(self.repo, now=NOW)
        self.optin("new-agent")
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.validate_manifest(manifest, repo=self.repo, now=NOW)

    def test_future_generated_at_rejected(self):
        manifest = check.assess(self.repo, now=NOW)
        manifest["generated_at"] = (NOW + timedelta(days=1)).isoformat()
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.validate_manifest(manifest, repo=self.repo, now=NOW)

    def test_missing_manifest_raises_specific_error(self):
        with self.assertRaises(contract.AgentOpsValidationError):
            contract.load_manifest(self.repo, now=NOW)

    def test_native_version_unknown_not_verified(self):
        self.optin()
        self.write(".agentops/results/latest/results.json", {"version": 2})
        manifest = check.assess(self.repo, now=NOW)
        self.assertEqual(manifest["agents"][0]["domains"]["evals"]["status"], "not-verified")

    def test_huge_native_artifact_is_bounded_and_gate_is_blocked(self):
        self.optin()
        path = self.write(".agentops/results/latest/results.json", "")
        with path.open("wb") as stream:
            stream.truncate(contract.MAX_ARTIFACT_BYTES + 1)
        manifest = check.assess(self.repo, now=NOW)
        self.assertEqual(manifest["verdict"], "blocked")

    def test_readonly_default_never_runs_native_cli(self):
        self.optin()
        from unittest.mock import patch
        with patch.object(check, "run_native", side_effect=AssertionError("must not execute")):
            check.assess(self.repo, now=NOW)


if __name__ == "__main__":
    unittest.main()
