"""Selective AgentOps evidence must not masquerade as continuous evaluation."""
import json
import copy
import hashlib
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone
from uuid import uuid4

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import evals_check as ec


class AgentOpsConsumerTests(unittest.TestCase):
    def setUp(self):
        self.repo = ROOT / (".agentops-evals-test-" + uuid4().hex)
        self.repo.mkdir()
        self.addCleanup(shutil.rmtree, self.repo)

    def write(self, name, data):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)

    def test_raw_payload_and_unvalidated_manifest_never_satisfy_wiring(self):
        self.write("agentops.yaml", "target: http://example.invalid\n")
        self.write(".agentops/results/latest/results.json", {
            "config": {"eval": "alert", "source":
                       "create_agent_evaluation(app_insights_connection_string='private')"},
        })
        self.write("specs/agentops-manifest.json", {
            "schema": "threadlight-agentops-manifest/v1",
            "status": "ready", "threshold": 0.8,
            "native_text": "champion challenger eval alert",
        })
        caps = ec.evaluate(str(self.repo))
        for key in ("online_eval_wired", "thresholds_declared",
                    "alert_wired", "ab_comparison_present"):
            self.assertNotEqual(caps[key]["status"], "pass", key)

    def test_symlink_cannot_make_raw_agentops_payload_wiring_evidence(self):
        self.write(".agentops/raw.py",
                   "create_agent_evaluation(app_insights_connection_string='private')")
        (self.repo / "source.py").symlink_to(".agentops/raw.py")
        self.assertNotEqual(ec.evaluate(str(self.repo))["online_eval_wired"]["status"], "pass")

    def test_vendored_threadlight_tools_cannot_self_prove_live_wiring(self):
        self.write(".threadlight/skills/threadlight-evals/scripts/evals_check.py",
                   "create_agent_evaluation(app_insights_connection_string='sample')\n"
                   "# eval alert")
        caps = ec.evaluate(str(self.repo))
        self.assertNotEqual(caps["online_eval_wired"]["status"], "pass")
        self.assertNotEqual(caps["alert_wired"]["status"], "pass")

    def test_documentation_selects_existing_validated_batch_only(self):
        text = (HERE.parents[1] / "SKILL.md").read_text()
        for marker in ("specs/agentops-manifest.json", "Do not rerun the same batch",
                       "execution pass rate"):
            self.assertTrue(marker in text, marker)

    def domain(self, passed=True):
        return {
            "status": "verified", "verdict": "pass" if passed else "fail", "blockers": [],
            "summary": {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "items_total": 10, "items_passed_all": 10, "items_pass_rate": 1.0,
                "thresholds_total": 1, "thresholds_passed": int(passed),
                "threshold_pass_rate": float(passed), "overall_passed": passed,
                "aggregate_metrics": {"groundedness": 0.9 if passed else 0.4},
                "thresholds": [{"metric": "groundedness", "passed": passed}],
                "comparison": {"status": "not-verified", "regressions": 0},
            },
        }

    def helper(self, *domains):
        agents = [{
            "agent_key": f"agent-{i}", "root": f"agents/{i}", "service": None,
            "domains": {"evals": domain},
            "provenance": {"artifacts": {f"agents/{i}/.agentops/results/latest/results.json": "a" * 64}},
        } for i, domain in enumerate(domains)]
        helper = Mock()
        helper.AgentOpsValidationError = ValueError
        helper.discover_opted_in_agents.return_value = [
            {key: a[key] for key in ("agent_key", "root", "service")} for a in agents]
        helper.load_manifest.return_value = {"agents": agents}
        return helper

    def test_validated_batch_fills_only_selective_capabilities(self):
        with patch.object(ec, "agentops", self.helper(self.domain()), create=True):
            caps = ec.evaluate(str(self.repo))
            man = ec.manifest(str(self.repo), caps)
        for key in ("run_history_present", "latest_eval_run_fresh",
                    "thresholds_declared", "latest_pass_rate_ok"):
            self.assertEqual(caps[key]["status"], "pass", key)
        for key in ("schedule_present", "alert_wired", "online_eval_wired",
                    "ab_comparison_present", "dataset_shape_ok"):
            self.assertNotEqual(caps[key]["status"], "pass", key)
        self.assertIsNone(man["metrics"]["pass_rate"])
        self.assertEqual(man["metrics"]["latest_run"], "specs/agentops-manifest.json")
        self.assertEqual(man["agentops"]["agents"][0]["execution_pass_rate"], 1.0)

    def test_failed_agent_is_retained_not_hidden_by_best_execution_rate(self):
        with patch.object(ec, "agentops", self.helper(self.domain(), self.domain(False)), create=True):
            caps = ec.evaluate(str(self.repo))
            man = ec.manifest(str(self.repo), caps)
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "should-fix")
        self.assertEqual(len(man["agentops"]["agents"]), 2)
        self.assertIsNone(man["metrics"]["pass_rate"])

    def test_valid_native_quality_failure_cannot_be_overridden(self):
        self.write("evals/runs/current.json", {"pass_rate": 0.4})
        self.write("evals/threshold.yaml", "min_pass_rate: 0.8\n")
        with patch.object(ec, "agentops", self.helper(self.domain()), create=True):
            caps = ec.evaluate(str(self.repo))
            man = ec.manifest(str(self.repo), caps)
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "should-fix")
        self.assertEqual(man["metrics"]["pass_rate"], 0.4)
        self.assertTrue(caps["latest_pass_rate_ok"]["sources"])

    def test_missing_agent_domain_cannot_be_filled_by_another_agent(self):
        with patch.object(ec, "agentops", self.helper(self.domain(), None), create=True):
            caps = ec.evaluate(str(self.repo))
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "not-verified")

    def test_invalid_manifest_does_not_borrow_native_pass_for_opted_agents(self):
        helper = self.helper(self.domain())
        helper.load_manifest.side_effect = ValueError("private native text")
        self.write("evals/runs/current.json", {"pass_rate": 0.99})
        self.write("evals/threshold.yaml", "min_pass_rate: 0.8\n")
        with patch.object(ec, "agentops", helper, create=True):
            caps = ec.evaluate(str(self.repo))
            man = ec.manifest(str(self.repo), caps)
        self.assertNotEqual(caps["latest_pass_rate_ok"]["status"], "pass")
        self.assertNotIn("private native text", json.dumps(man))

    def test_shared_loader_rejects_tampered_future_expired_and_missing_agent(self):
        self.write("agentops.yaml", "target: agent:synthetic:1\n")
        self.write("evals/runs/current.json", {"pass_rate": 0.99})
        self.write("evals/threshold.yaml", "min_pass_rate: 0.8\n")
        document = ec.agentops.assess_repository(self.repo)
        variants = [None, {"schema": "wrong"}, copy.deepcopy(document),
                    copy.deepcopy(document), copy.deepcopy(document), copy.deepcopy(document)]
        variants[2]["generated_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        variants[3]["generated_at"] = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        variants[4]["agents"] = []
        variants[5]["agents"][0]["domains"]["evals"] = self.domain()
        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                if variant is not None:
                    self.write("specs/agentops-manifest.json", variant)
                caps = ec.evaluate(str(self.repo))
                self.assertNotEqual(caps["latest_pass_rate_ok"]["status"], "pass")
        self.write("specs/agentops-manifest.json", document)
        caps = ec.evaluate(str(self.repo))
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "not-verified")

    def test_partial_rows_stay_negative(self):
        partial = self.domain()
        partial["summary"]["items_passed_all"] = 9
        partial["summary"]["items_pass_rate"] = 0.9
        with patch.object(ec, "agentops", self.helper(partial)):
            caps = ec.evaluate(str(self.repo))
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "should-fix")

    def test_native_comparison_direction_is_not_automatic_quality_policy(self):
        regression = self.domain()
        regression["summary"]["comparison"] = {"status": "verified", "regressions": 1}
        with patch.object(ec, "agentops", self.helper(regression)):
            caps = ec.evaluate(str(self.repo))
            man = ec.manifest(str(self.repo), caps)
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "pass")
        self.assertEqual(man["agentops"]["agents"][0]["comparison"]["regressions"], 1)
        self.assertNotEqual(caps["ab_comparison_present"]["status"], "pass")

    def test_verified_summary_without_normalized_quality_pass_stays_unknown(self):
        domain = self.domain()
        domain["verdict"] = "unknown"
        with patch.object(ec, "agentops", self.helper(domain)):
            caps = ec.evaluate(str(self.repo))
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "not-verified")

    def test_stricter_consumer_freshness_cannot_accept_old_or_future_quality(self):
        for offset in (-2, 1):
            domain = self.domain()
            domain["summary"]["finished_at"] = (
                datetime.now(timezone.utc) + timedelta(days=offset)).isoformat()
            with patch.object(ec, "agentops", self.helper(domain)):
                caps = ec.evaluate(str(self.repo), freshness_days=1)
            self.assertNotEqual(caps["latest_eval_run_fresh"]["status"], "pass")
            self.assertNotEqual(caps["latest_pass_rate_ok"]["status"], "pass")

    def test_positive_legacy_kpi_does_not_hide_an_agentops_quality_failure(self):
        self.write("evals/runs/current.json", {"pass_rate": 0.99})
        self.write("evals/threshold.yaml", "min_pass_rate: 0.8\n")
        with patch.object(ec, "agentops", self.helper(self.domain(False))):
            man = ec.manifest(str(self.repo), ec.evaluate(str(self.repo)))
        self.assertIsNone(man["metrics"]["pass_rate"])
        self.assertTrue(man["agentops"]["agents"][0]["artifacts"])

    def test_only_mapped_quality_blocker_is_represented_for_dedup(self):
        domain = self.domain(False)
        domain["blockers"] = [
            {"code": "AOPS-EVAL-QUALITY", "severity": "must-fix", "owner": "evals"},
            {"code": "AOPS-UNMAPPED", "severity": "must-fix", "owner": "evals"},
        ]
        helper = self.helper(domain)
        with patch.object(ec, "agentops", helper):
            man = ec.manifest(str(self.repo), ec.evaluate(str(self.repo)))
        agent = man["agentops"]["agents"][0]
        self.assertEqual(agent["domain_status"], "verified")
        self.assertEqual(agent["represented_blockers"], ["AOPS-EVAL-QUALITY"])
        self.assertEqual(man["capabilities"]["latest_pass_rate_ok"]["status"], "must-fix")
        expected_digest = hashlib.sha256(json.dumps(helper.load_manifest.return_value,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(man["agentops"]["source_manifest_sha256"], expected_digest)

    def test_generated_manifest_schema_and_packaged_helper_origin(self):
        import jsonschema
        with patch.object(ec, "agentops", self.helper(self.domain())):
            man = ec.manifest(str(self.repo), ec.evaluate(str(self.repo)))
        schema = json.loads((HERE.parents[1] / "references/evals-manifest.schema.json").read_text())
        jsonschema.validate(man, schema)
        self.assertEqual(Path(ec.agentops.__file__).resolve(), ROOT / "skills/_shared/agentops.py")

    def test_unambiguous_service_identity_is_preserved(self):
        helper = self.helper(self.domain())
        helper.discover_opted_in_agents.return_value[0]["service"] = "chat-agent"
        helper.load_manifest.return_value["agents"][0]["service"] = "chat-agent"
        with patch.object(ec, "agentops", helper):
            man = ec.manifest(str(self.repo), ec.evaluate(str(self.repo)))
        self.assertEqual(man["agentops"]["agents"][0]["service"], "chat-agent")

    def test_signed_native_evidence_and_sequential_domain_emission(self):
        # Reuse the producer's synthetic native fixture; runtime consumers never import it.
        path = ROOT / "skills/threadlight-agentops/tests/fixture_helpers.py"
        spec = importlib.util.spec_from_file_location("_domain_native_fixture", path)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        document = fixture.create_agentops_fixture(self.repo, now=datetime.now(timezone.utc))
        sys.path.insert(0, str(ROOT / "skills/threadlight-redteam/scripts"))
        import redteam_check as rc
        caps = ec.evaluate(str(self.repo))
        self.assertEqual(caps["latest_pass_rate_ok"]["status"], "pass")
        self.assertEqual(caps["latest_eval_run_fresh"]["status"], "pass")
        scan = rc.evaluate(str(self.repo))
        self.assertEqual(scan["capabilities"]["harmful_content_asr_ok"]["status"], "pass")
        self.assertEqual(scan["capabilities"]["jailbreak_asr_ok"]["status"], "not-verified")
        with contextlib.redirect_stdout(io.StringIO()):
            ec.main(["--target", str(self.repo), "--emit"])
            rc.main(["--target", str(self.repo), "--emit"])
        self.assertEqual(ec.agentops.load_manifest(self.repo), document)
        for relative in ("specs/evals-manifest.json", "specs/redteam-manifest.json"):
            text = (self.repo / relative).read_text()
            for sentinel in ("PRIVATE", "SECRET", "tool_calls"):
                self.assertNotIn(sentinel, text)

    def test_observed_native_receipt_without_pki_preserves_quality_and_scan_gaps(self):
        path = ROOT / "skills/threadlight-agentops/tests/test_agentops_check.py"
        spec = importlib.util.spec_from_file_location("_domain_observer_fixture", path)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        for quality in (True, False):
            with self.subTest(quality=quality):
                case = fixture.AgentOpsTests(methodName="runTest")
                case.repo = self.repo / ("healthy" if quality else "failed")
                case.repo.mkdir()
                case.git("init", "-q")
                case.git("config", "user.email", "fixture@example.invalid")
                case.git("config", "user.name", "Offline fixture")
                case.fixture(quality=quality, signed=False, redteam=True)
                record = case.observe_fake_command()
                self.assertIn("observation", record)
                self.assertFalse((case.repo / ".private-key.pem").exists())
                self.assertFalse((case.repo / ".agentops/threadlight/receipt.sig").exists())
                self.assertNotIn("redteam", record["artifacts"])
                document = ec.agentops.assess_repository(case.repo)
                case.write("specs/agentops-manifest.json", document)
                caps = ec.evaluate(str(case.repo))
                man = ec.manifest(str(case.repo), caps)
                self.assertEqual(caps["latest_pass_rate_ok"]["status"], "pass" if quality else "must-fix")
                self.assertIsNone(man["metrics"]["pass_rate"])
                self.assertEqual(man["agentops"]["agents"][0]["represented_blockers"],
                                 [] if quality else ["AOPS-EVAL-QUALITY"])
                sys.path.insert(0, str(ROOT / "skills/threadlight-redteam/scripts"))
                import redteam_check as rc
                scan = rc.evaluate(str(case.repo))
                self.assertEqual(scan["capabilities"]["harmful_content_asr_ok"]["status"], "not-verified")
                with contextlib.redirect_stdout(io.StringIO()):
                    ec.main(["--target", str(case.repo), "--emit"])
                    rc.main(["--target", str(case.repo), "--emit"])
                self.assertEqual(ec.agentops.load_manifest(case.repo), document)


if __name__ == "__main__":
    unittest.main()
