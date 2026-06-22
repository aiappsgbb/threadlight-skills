"""stdlib unittest suite for redteam_check.py (no pytest)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import redteam_check as rc  # noqa: E402

FIXTURES = os.path.join(SKILL, "references", "fixtures")
CLEAN = os.path.join(FIXTURES, "sample-clean")
FINDINGS = os.path.join(FIXTURES, "sample-findings")


class CleanFixtureTests(unittest.TestCase):
    def setUp(self):
        self.result = rc.evaluate(CLEAN, freshness_days=36500)
        self.caps = self.result["capabilities"]
        self.man = rc.manifest(CLEAN, self.result, freshness_days=36500)

    def test_scan_present_and_fresh(self):
        self.assertEqual(self.caps["scan_present"]["status"], "pass")
        self.assertEqual(self.caps["scan_fresh"]["status"], "pass")

    def test_core_asr_capabilities_pass(self):
        self.assertEqual(self.caps["jailbreak_asr_ok"]["status"], "pass")
        self.assertEqual(self.caps["prompt_injection_asr_ok"]["status"], "pass")
        self.assertEqual(self.caps["exfiltration_asr_ok"]["status"], "pass")

    def test_advisory_capabilities_pass(self):
        self.assertEqual(self.caps["harmful_content_asr_ok"]["status"], "pass")
        self.assertEqual(self.caps["coverage_ok"]["status"], "pass")

    def test_verdict_hardened(self):
        self.assertEqual(self.man["must_fix"], [])
        self.assertEqual(self.man["verdict"], "hardened")


class FindingsFixtureTests(unittest.TestCase):
    def setUp(self):
        self.result = rc.evaluate(FINDINGS, freshness_days=36500)
        self.caps = self.result["capabilities"]
        self.man = rc.manifest(FINDINGS, self.result, freshness_days=36500)

    def test_must_fix_asr_capabilities(self):
        self.assertEqual(self.caps["jailbreak_asr_ok"]["status"], "must-fix")
        self.assertEqual(self.caps["prompt_injection_asr_ok"]["status"], "must-fix")
        self.assertEqual(self.caps["exfiltration_asr_ok"]["status"], "must-fix")

    def test_safe_findings_are_mapped(self):
        self.assertEqual(self.caps["jailbreak_asr_ok"]["finding_id"], "SAFE-101")
        self.assertEqual(self.caps["prompt_injection_asr_ok"]["finding_id"], "SAFE-102")
        self.assertEqual(self.caps["exfiltration_asr_ok"]["finding_id"], "SAFE-103")

    def test_verdict_vulnerable(self):
        self.assertEqual(self.man["verdict"], "vulnerable")
        self.assertIn("jailbreak_asr_ok", self.man["must_fix"])
        self.assertIn("prompt_injection_asr_ok", self.man["must_fix"])
        self.assertIn("exfiltration_asr_ok", self.man["must_fix"])


class ManifestShapeTests(unittest.TestCase):
    def test_schema_and_required_keys(self):
        result = rc.evaluate(CLEAN, freshness_days=36500)
        man = rc.manifest(CLEAN, result, freshness_days=36500)
        self.assertEqual(man["schema"], rc.MANIFEST_SCHEMA)
        for key in (
            "schema",
            "tool_version",
            "captured_at",
            "verdict",
            "must_fix",
            "should_fix",
            "not_verified",
            "capabilities",
            "asr",
            "thresholds",
        ):
            self.assertIn(key, man)

    def test_gate_exit_code_for_findings(self):
        rc_code = rc.main(["--target", FINDINGS, "--gate", "--freshness-days", "36500"])
        self.assertEqual(rc_code, 2)

    def test_clean_target_passes_gate(self):
        rc_code = rc.main(["--target", CLEAN, "--gate", "--freshness-days", "36500"])
        self.assertEqual(rc_code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
