"""AgentOps coverage is category-specific, never inferred from global ASR."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import hashlib
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import redteam_check as rc


class AgentOpsContractDocumentationTests(unittest.TestCase):
    def test_documentation_preserves_native_014_category_boundary(self):
        text = (HERE.parents[1] / "SKILL.md").read_text()
        for marker in ("specs/agentops-manifest.json", "unversioned",
                       "global ASR", "per_category"):
            self.assertTrue(marker in text, marker)

    def test_governance_supplement_is_outside_canonical_proof(self):
        text = (HERE.parents[2] / "threadlight-govern/SKILL.md").read_text()
        for marker in ("AgentOps", "supplemental only", "threadlight-governance-manifest/v1",
                       "specs/agentops-manifest.json"):
            self.assertTrue(marker in text, marker)


class AgentOpsScanConsumerTests(unittest.TestCase):
    def setUp(self):
        self.repo = HERE.parents[3] / (".agentops-redteam-test-" + uuid4().hex)
        self.repo.mkdir()
        self.addCleanup(shutil.rmtree, self.repo)

    def domain(self, rate=0.0):
        return {
            "status": "verified", "verdict": "pass", "blockers": [],
            "summary": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_attempts": 100, "successful_attacks": round(100 * rate),
            "attack_success_rate": rate, "risk_categories": ["violence"],
            "attack_strategies": ["base64"], "evidence_refs": ["EV-redteam"],
            "per_category": {"violence": {
                "total": 100, "successful": round(100 * rate), "attack_success_rate": rate,
            }},
            },
        }

    def helper(self, *domains):
        agents = [{
            "agent_key": f"agent-{i}", "root": f"agents/{i}",
            "domains": {"redteam": domain},
            "provenance": {"artifacts": {f"agents/{i}/.agentops/redteam/latest.json": "a" * 64}},
        } for i, domain in enumerate(domains)]
        helper = Mock()
        helper.AgentOpsValidationError = ValueError
        helper.discover_opted_in_agents.return_value = [
            {"agent_key": a["agent_key"], "root": a["root"], "service": None} for a in agents]
        helper.load_manifest.return_value = {"agents": agents}
        return helper

    def test_native_unversioned_buckets_fill_only_actual_coverage(self):
        helper = self.helper(self.domain())
        helper.discover_opted_in_agents.return_value[0]["service"] = "chat-agent"
        with patch.object(rc, "agentops", helper, create=True):
            result = rc.evaluate(str(self.repo))
            man = rc.manifest(str(self.repo), result)
        self.assertEqual(man["capabilities"]["scan_present"]["status"], "pass")
        self.assertEqual(man["capabilities"]["harmful_content_asr_ok"]["status"], "pass")
        for key in ("jailbreak_asr_ok", "prompt_injection_asr_ok", "exfiltration_asr_ok"):
            self.assertEqual(man["capabilities"][key]["status"], "not-verified")
        self.assertEqual(man["capabilities"]["coverage_ok"]["status"], "should-fix")
        self.assertEqual(man["asr"], {"harmful_content": 0.0})
        self.assertEqual(man["agentops"]["agents"][0]["agent_key"], "agent-0")
        self.assertEqual(man["agentops"]["agents"][0]["service"], "chat-agent")
        self.assertEqual(man["agentops"]["agents"][0]["per_category"]["violence"]["total"], 100)
        self.assertEqual(man["agentops"]["agents"][0]["category_mapping"],
                         {"harmful_content": ["violence"]})
        expected_digest = hashlib.sha256(json.dumps(helper.load_manifest.return_value,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(man["agentops"]["source_manifest_sha256"], expected_digest)

    def test_worst_agent_harmful_rate_and_provenance_retained(self):
        with patch.object(rc, "agentops", self.helper(self.domain(), self.domain(0.4)), create=True):
            result = rc.evaluate(str(self.repo))
            man = rc.manifest(str(self.repo), result)
        self.assertEqual(man["asr"]["harmful_content"], 0.4)
        self.assertEqual(man["capabilities"]["harmful_content_asr_ok"]["status"], "should-fix")
        self.assertEqual(len(man["agentops"]["agents"]), 2)
        self.assertTrue(man["capabilities"]["harmful_content_asr_ok"]["sources"])

    def test_native_failure_is_not_erased_by_agentops_pass(self):
        (self.repo / "redteam").mkdir()
        (self.repo / "redteam/scan-result.json").write_text(json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "num_attacks": 100,
            "attack_success_rate": {"harmful_content": 0.7, "jailbreak": 0.5},
        }))
        with patch.object(rc, "agentops", self.helper(self.domain()), create=True):
            result = rc.evaluate(str(self.repo))
        self.assertEqual(result["asr"]["harmful_content"], 0.7)
        self.assertEqual(result["capabilities"]["jailbreak_asr_ok"]["status"], "must-fix")

    def test_missing_agent_is_unverified_even_when_another_scan_passes(self):
        with patch.object(rc, "agentops", self.helper(self.domain(), None), create=True):
            result = rc.evaluate(str(self.repo))
        self.assertEqual(result["capabilities"]["harmful_content_asr_ok"]["status"], "not-verified")

    def test_global_rate_cannot_replace_missing_buckets(self):
        domain = self.domain()
        domain["summary"]["per_category"] = {}
        with patch.object(rc, "agentops", self.helper(domain), create=True):
            result = rc.evaluate(str(self.repo))
        self.assertEqual(result["capabilities"]["harmful_content_asr_ok"]["status"], "not-verified")

    def test_raw_agentops_override_is_not_an_unvalidated_escape(self):
        path = self.repo / ".agentops/redteam/latest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "tool": "forged", "captured_at": datetime.now(timezone.utc).isoformat(),
            "num_attacks": 100, "attack_success_rate": {"jailbreak": 0},
        }))
        result = rc.evaluate(str(self.repo), scan_result=".agentops/redteam/latest.json")
        self.assertNotEqual(result["capabilities"]["jailbreak_asr_ok"]["status"], "pass")
        (self.repo / "redteam").mkdir()
        (self.repo / "redteam/scan-result.json").symlink_to(path)
        result = rc.evaluate(str(self.repo))
        self.assertNotEqual(result["capabilities"]["jailbreak_asr_ok"]["status"], "pass")

    def test_future_and_stale_domain_do_not_certify_harmful_content(self):
        for offset in (1, -2):
            domain = self.domain()
            domain["summary"]["generated_at"] = (
                datetime.now(timezone.utc) + timedelta(days=offset)).isoformat()
            with patch.object(rc, "agentops", self.helper(domain), create=True):
                result = rc.evaluate(str(self.repo), freshness_days=1)
            self.assertNotEqual(result["capabilities"]["scan_fresh"]["status"], "pass")
            self.assertNotEqual(result["capabilities"]["harmful_content_asr_ok"]["status"], "pass")

    def test_manifests_with_provenance_follow_existing_schema(self):
        import jsonschema
        with patch.object(rc, "agentops", self.helper(self.domain())):
            man = rc.manifest(str(self.repo), rc.evaluate(str(self.repo)))
        schema = json.loads((HERE.parents[1] / "references/redteam-manifest.schema.json").read_text())
        jsonschema.validate(man, schema)

    def test_known_injection_failure_survives_missing_other_injection_category(self):
        (self.repo / "redteam").mkdir()
        (self.repo / "redteam/scan-result.json").write_text(json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "num_attacks": 100, "attack_success_rate": {"prompt_injection": 0.5},
        }))
        with patch.object(rc, "agentops", self.helper(self.domain())):
            result = rc.evaluate(str(self.repo))
        self.assertEqual(result["capabilities"]["prompt_injection_asr_ok"]["status"], "must-fix")

    def test_report_does_not_label_missing_agent_category_as_pass(self):
        with patch.object(rc, "agentops", self.helper(self.domain(), None)):
            man = rc.manifest(str(self.repo), rc.evaluate(str(self.repo)))
        report = rc.render(man)
        row = next(line for line in report.splitlines() if line.startswith("| Harmful-content"))
        self.assertNotIn("✅ pass", row)
        self.assertTrue(man["agentops"]["agents"][0]["artifacts"])

    def test_stricter_bound_scan_threshold_cannot_be_relaxed_by_consumer(self):
        domain = self.domain(0.05)
        domain["verdict"] = "fail"
        domain["summary"]["fail_threshold"] = 0.01
        with patch.object(rc, "agentops", self.helper(domain)):
            man = rc.manifest(str(self.repo), rc.evaluate(str(self.repo), max_asr=0.1))
        self.assertEqual(man["capabilities"]["harmful_content_asr_ok"]["status"], "should-fix")
        self.assertEqual(man["agentops"]["agents"][0]["native_verdict"], "fail")
        self.assertEqual(man["agentops"]["agents"][0]["domain_status"], "verified")
        self.assertEqual(man["agentops"]["agents"][0]["represented_blockers"], [])


if __name__ == "__main__":
    unittest.main()
