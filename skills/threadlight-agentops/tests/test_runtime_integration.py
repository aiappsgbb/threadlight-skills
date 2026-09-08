"""Real bounded synthetic CLI through the single CI-owned runtime, without PKI."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class RuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = load(SKILL.parent / "threadlight-cicd/scripts/agentops_runtime.py", "_runtime_integration")
        self.observer = self.runtime._observer()
        self.assertEqual(Path(self.observer.__file__).resolve(), SKILL / "scripts/native_observer.py")
        fixtures = load(SKILL / "tests/test_agentops_check.py", "_runtime_native_fixtures")
        self.fixture = fixtures.AgentOpsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.fixture(signed=False)
        self.repo = self.fixture.repo
        self.fixture.write(".agentops/agent.yaml", "sources:\n  foundry:\n    enabled: true\n")
        self.fixture.policy["artifact_paths"] = {
            "dataset": "data.jsonl", "workflow": ".github/workflows/threadlight-ci.yml"}
        self.fixture.write(".threadlight/agentops-binding.json", self.fixture.policy)
        self.fixture.git("add", ".threadlight/agentops-binding.json")
        self.fixture.git("commit", "-qm", "Approve synthetic runtime artifact paths")
        cli, azd = self.repo / ".agentops/credentials/cli", self.repo / ".agentops/credentials/azd"
        cli.mkdir(parents=True)
        azd.mkdir(parents=True)
        environment = patch.dict(os.environ, {
            "PATH": os.environ.get("PATH", ""), "HOME": str(self.repo),
            "AZURE_CONFIG_DIR": str(cli), "AZD_CONFIG_DIR": str(azd),
            "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential",
            "THREADLIGHT_AGENTOPS_RUN_ID": "synthetic-run",
            "GITHUB_STEP_SUMMARY": str(self.repo / "MUST-NOT-WRITE"),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        versions = patch.object(self.observer, "package_version", side_effect=lambda name:
            "0.14.0" if name == "agentops-accelerator" else "1.25.3")
        versions.start()
        self.addCleanup(versions.stop)
        self.executable = self.fixture.write(".agentops/fake-agentops", f"#!{sys.executable}\n" + '''
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
assert "GITHUB_STEP_SUMMARY" not in os.environ
root = Path(".")
now = datetime.now(timezone.utc).isoformat()
if sys.argv[1:3] == ["eval", "analyze"]:
    print((root / ".agentops/operations/eval-analysis.json").read_text())
elif sys.argv[1:3] == ["eval", "run"]:
    result = json.loads((root / ".agentops/results/latest/results.json").read_text())
    result.update(started_at=now, finished_at=now, duration_seconds=0.0)
    if "--baseline" in sys.argv and not (root / ".agentops/omit-comparison").exists():
        baseline_path = Path(sys.argv[sys.argv.index("--baseline") + 1]).as_posix()
        baseline = json.loads((root / baseline_path).read_text())
        result["comparison"] = {
            "baseline_path": baseline_path, "baseline_started_at": baseline["started_at"],
            "baseline_overall_passed": baseline["summary"]["overall_passed"],
            "metrics": [{"metric": name, "current": value, "baseline": baseline["aggregate_metrics"][name],
                         "delta": value - baseline["aggregate_metrics"][name],
                         "direction": "unchanged" if value == baseline["aggregate_metrics"][name] else
                                      "improved" if value > baseline["aggregate_metrics"][name] else "regressed"}
                        for name, value in result["aggregate_metrics"].items()],
            "rows": [{"row_index": row["row_index"], "current_passed": True, "baseline_passed": True,
                      "direction": "unchanged"} for row in result["rows"]],
        }
    elif (root / ".agentops/omit-comparison").exists():
        result["comparison"] = None
    out = (root / sys.argv[sys.argv.index("--output") + 1]
           if "--output" in sys.argv else root / ".agentops/results/current-observed-run")
    out.mkdir(parents=True, exist_ok=True)
    for path in (out / "results.json", root / ".agentops/results/latest/results.json"):
        path.write_text(json.dumps(result))
    print("PRIVATE eval payload")
    sys.exit(0 if result["summary"]["overall_passed"] else 2)
elif sys.argv[1] == "doctor":
    result = json.loads((root / ".agentops/results/latest/results.json").read_text())
    evidence = json.loads((root / ".agentops/release/latest/evidence.json").read_text())
    evidence["generated_at"] = now
    evidence["latest_eval"]["started_at"] = result["started_at"]
    path = root / ".agentops/agent/history.jsonl"
    history = json.loads(path.read_text().splitlines()[-1])
    history["timestamp"] = now
    floor = sys.argv[sys.argv.index("--severity-fail") + 1] if "--severity-fail" in sys.argv else "critical"
    finding_gate = floor in {"warning", "info"}
    if finding_gate:
        findings = [{"severity": floor, "title": "PRIVATE finding", "id": "synthetic", "category": "security"}]
        counts = {"critical": 0, "warning": 0, "info": 0}
        counts[floor] = 1
        evidence.update(status="ready_with_warnings" if floor == "warning" else "ready",
                        warnings=["PRIVATE warning"] if floor == "warning" else [],
                        checks=[{"name": "Doctor readiness", "status": "warning" if floor == "warning" else "ready",
                                 "summary": "PRIVATE"}])
        evidence["doctor"].update(findings_total=1, counts=counts, max_severity=floor, top_findings=findings)
        history.update(findings_total=1, findings_by_severity=counts, findings_by_category={"security": 1},
                       max_severity=floor, findings=findings)
    (root / ".agentops/release/latest/evidence.json").write_text(json.dumps(evidence))
    with path.open("a") as stream:
        stream.write(json.dumps(history) + "\\n")
    print("PRIVATE Doctor payload")
    if finding_gate:
        sys.exit(2)
else:
    sys.exit(3)
''')
        self.executable.chmod(0o700)
        binary = patch.object(self.observer, "_native_executable", return_value=self.executable)
        binary.start()
        self.addCleanup(binary.stop)

    def approve(self, operation):
        c = self.observer.contract
        now = datetime.now(timezone.utc)
        approval = {
            "schema": "threadlight-agentops-runtime-approval/v1", "approved": True,
            "operation": operation, "root": ".", "repository_commit": c.repository_state(self.repo)["commit"],
            "config_sha256": c.sha256(c.read_bytes(self.repo, "agentops.yaml")),
            "target_sha256": self.fixture.policy["target_sha256"],
            "environment_sha256": self.fixture.policy["environment_sha256"],
            "run_id_sha256": self.observer.run_identity(operation),
            "context_sha256": self.observer.execution_context_hash(self.repo, self.repo, dict(os.environ)),
            "not_before": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "telemetry_scope_approved": True, "raw_artifacts": {"scope": ".agentops", "retention_hours": 24},
        }
        self.fixture.write(f".agentops/threadlight/approvals/{operation}.json", approval)
        return approval

    def test_runtime_eval_then_doctor_without_signatures(self):
        self.approve("eval")
        evaluated = self.runtime.observe(self.repo, operation="eval")
        self.assertEqual(evaluated["agents"][0]["domains"]["evals"]["verdict"], "pass")
        self.assertEqual(evaluated["agents"][0]["capabilities"]["doctor_freshness"]["status"], "not-verified")
        self.approve("doctor")
        refreshed = self.runtime.observe(self.repo, operation="doctor")
        self.assertEqual(refreshed["verdict"], "operational", refreshed)
        self.assertFalse((self.repo / ".private-key.pem").exists())
        self.assertFalse((self.repo / "MUST-NOT-WRITE").exists())
        self.assertNotIn("PRIVATE", json.dumps(refreshed))

    def test_runtime_preserves_actual_quality_exit_two(self):
        result = self.fixture.native
        result["aggregate_metrics"]["relevance"] = 4.0
        result["rows"][0]["metrics"][0]["value"] = 4.0
        result["thresholds"][0].update(actual="4", passed=False)
        result["summary"].update(thresholds_passed=0, threshold_pass_rate=0.0, overall_passed=False)
        for role in ("result", "latest"):
            self.fixture.write(self.fixture.roles[role], result)
        self.approve("eval")
        observed = self.runtime.observe(self.repo, operation="eval")
        self.assertEqual(self.runtime.operation_exit_code(observed, "eval"), 2)

    def test_cli_refresh_then_readonly_consumers_roundtrip(self):
        import contextlib
        import io
        self.approve("eval")
        self.runtime.observe(self.repo, operation="eval")
        self.approve("doctor")
        checker = load(SKILL / "scripts/agentops_check.py", "_observer_cli_roundtrip")
        with patch.object(checker, "load_runtime", return_value=self.runtime):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = checker.main(["--target", str(self.repo), "--refresh-doctor", "--emit", "--json", "--gate"])
        self.assertEqual(code, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(document["verdict"], "operational")
        with contextlib.redirect_stdout(io.StringIO()):
            for domain in ("evals", "redteam"):
                consumer = load(SKILL.parent / f"threadlight-{domain}/scripts/{domain}_check.py",
                                f"_approved_observer_consumer_{domain}")
                consumer.main(["--target", str(self.repo), "--emit"])
        self.assertEqual(self.observer.contract.load_manifest(self.repo), document)

    def test_missing_approval_never_invokes_fake_native(self):
        with patch.object(self.observer.contract, "bounded_command",
                          wraps=self.observer.contract.bounded_command) as runner:
            with self.assertRaises(ValueError):
                self.runtime.observe(self.repo, operation="eval")
        self.assertFalse(any(str(self.executable) in call.args[0] for call in runner.call_args_list))

    def test_reusable_native_runner_fixture_closes_observe_load_roundtrip(self):
        helpers = load(SKILL / "tests/fixture_helpers.py", "_reusable_observer_fixture")
        with helpers.native_observer_fixture() as fixture:
            fixture.approve("eval")
            observed = fixture.observer.observe(fixture.repo, operation="eval",
                                                run_command=fixture.run_command)
            self.assertEqual(observed["agents"][0]["domains"]["evals"]["verdict"], "pass")
            checker = load(SKILL / "scripts/agentops_check.py", "_fixture_readonly_checker")
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(checker.main(["--target", str(fixture.repo), "--emit"]), 0)
            self.assertEqual(fixture.observer.contract.load_manifest(fixture.repo)["agents"], observed["agents"])

    def test_doctor_preserves_observed_warning_floor_exit_two(self):
        self.approve("eval")
        self.assertEqual(self.runtime.main(["--repo", str(self.repo), "--run-eval"]), 0)
        approval = self.approve("doctor")
        approval["doctor_severity"] = "warning"
        self.fixture.write(".agentops/threadlight/approvals/doctor.json", approval)
        self.assertEqual(self.runtime.main(["--repo", str(self.repo), "--refresh-doctor"]), 2)
        result = self.observer.contract.load_manifest(self.repo)
        self.assertEqual(result["verdict"], "partial")
        self.assertEqual(self.observer.contract.read_json(
            self.repo, ".agentops/threadlight/receipt.json")["observation"]["exit_code"], 2)

    def test_doctor_preserves_info_floor_exit_two_despite_operational_summary(self):
        self.approve("eval")
        self.assertEqual(self.runtime.main(["--repo", str(self.repo), "--run-eval"]), 0)
        approval = self.approve("doctor")
        approval["doctor_severity"] = "info"
        self.fixture.write(".agentops/threadlight/approvals/doctor.json", approval)
        self.assertEqual(self.runtime.main(["--repo", str(self.repo), "--refresh-doctor"]), 2)
        result = self.observer.contract.load_manifest(self.repo)
        self.assertEqual(result["verdict"], "operational")
        self.assertEqual(self.observer.contract.read_json(
            self.repo, ".agentops/threadlight/receipt.json")["observation"]["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
