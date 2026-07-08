"""stdlib unittest suite for govern_check.py (no pytest)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import govern_check as gc  # noqa: E402

FIXTURES = os.path.join(SKILL, "references", "fixtures")
WIRED = os.path.join(FIXTURES, "sample-wired")
BARE = os.path.join(FIXTURES, "sample-bare")

# The real, v2 capability set. Keep in lockstep with govern_check + the
# production-ready pillar-02 map.
V2_CAPS = {
    "policy_artefact_present",
    "policy_schema_valid",
    "policy_versioned",
    "policy_default_deny",
    "sensitive_action_rules_present",
    "policy_tests_present",
    "ci_gate_present",
    "attestation_present",
    "attestation_fresh",
    "asi_reference_present",
}
# Capabilities that were part of the old fictional runtime-middleware model and
# must no longer exist.
REMOVED_CAPS = {"middleware_wired_at_boundary", "sidecar_pattern",
                "rai_policy_present", "verifier_artefact_present",
                "verifier_fresh"}


class GovernedFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = gc.evaluate(WIRED, freshness_days=3650)
        self.man = gc.manifest(WIRED, self.caps, "auto", 3650)

    def test_exact_capability_set(self):
        self.assertEqual(set(self.caps), V2_CAPS)

    def test_no_fictional_caps(self):
        self.assertEqual(set(self.caps) & REMOVED_CAPS, set())

    def test_policy_present_schema_valid_versioned(self):
        self.assertEqual(self.caps["policy_artefact_present"]["status"], "pass")
        self.assertEqual(self.caps["policy_schema_valid"]["status"], "pass")
        self.assertEqual(self.caps["policy_versioned"]["status"], "pass")

    def test_default_deny_detected(self):
        self.assertEqual(self.caps["policy_default_deny"]["status"], "pass")

    def test_sensitive_action_rules_detected(self):
        self.assertEqual(
            self.caps["sensitive_action_rules_present"]["status"], "pass")

    def test_policy_tests_present(self):
        self.assertEqual(self.caps["policy_tests_present"]["status"], "pass")

    def test_ci_gate_present(self):
        self.assertEqual(self.caps["ci_gate_present"]["status"], "pass")

    def test_attestation_present_and_fresh(self):
        self.assertEqual(self.caps["attestation_present"]["status"], "pass")
        self.assertEqual(self.caps["attestation_fresh"]["status"], "pass")

    def test_asi_reference(self):
        self.assertEqual(self.caps["asi_reference_present"]["status"], "pass")

    def test_verdict_governed(self):
        self.assertEqual(self.man["must_fix"], [])
        self.assertEqual(self.man["should_fix"], [])
        self.assertEqual(self.man["verdict"], "governed")


class UngovernedFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = gc.evaluate(BARE, freshness_days=90)
        self.man = gc.manifest(BARE, self.caps, "auto", 90)

    def test_policy_missing_is_must_fix(self):
        self.assertEqual(self.caps["policy_artefact_present"]["status"], "must-fix")

    def test_schema_valid_missing_is_must_fix(self):
        self.assertEqual(self.caps["policy_schema_valid"]["status"], "must-fix")

    def test_verdict_ungoverned(self):
        self.assertEqual(self.man["verdict"], "ungoverned")
        self.assertIn("policy_artefact_present", self.man["must_fix"])

    def test_no_fictional_caps(self):
        self.assertEqual(set(self.caps) & REMOVED_CAPS, set())


class ManifestShapeTests(unittest.TestCase):
    def test_schema_is_v2(self):
        self.assertEqual(gc.MANIFEST_SCHEMA, "threadlight-govern-manifest/v2")

    def test_schema_and_keys(self):
        caps = gc.evaluate(WIRED, 3650)
        man = gc.manifest(WIRED, caps, "auto", 3650)
        self.assertEqual(man["schema"], gc.MANIFEST_SCHEMA)
        for key in ("verdict", "must_fix", "should_fix", "capabilities",
                    "captured_at", "tool_version"):
            self.assertIn(key, man)

    def test_verdict_enum_values(self):
        for target in (WIRED, BARE):
            caps = gc.evaluate(target, 3650)
            man = gc.manifest(target, caps, "auto", 3650)
            self.assertIn(man["verdict"], ("governed", "partial", "ungoverned"))

    def test_gate_exit_code(self):
        rc = gc.main(["--target", BARE, "--gate"])
        self.assertEqual(rc, 2)

    def test_clean_target_passes_gate(self):
        rc = gc.main(["--target", WIRED, "--gate", "--freshness-days", "3650"])
        self.assertEqual(rc, 0)


class GracefulDegradationTests(unittest.TestCase):
    """An unexpected evaluate() failure must still emit a valid manifest
    (verdict present, no fabricated must-fix) so the live E2E leg-assert
    never hard-fails on a manifest that was simply never written."""

    def test_emit_writes_manifest_when_evaluate_raises(self):
        import json
        import tempfile

        def _boom(*_a, **_k):
            raise RuntimeError("synthetic validator failure")

        original = gc.evaluate
        gc.evaluate = _boom
        try:
            with tempfile.TemporaryDirectory() as d:
                code = gc.main(["--target", d, "--emit"])
                self.assertEqual(code, 0)
                path = os.path.join(d, "specs", "govern-manifest.json")
                self.assertTrue(os.path.isfile(path))
                man = json.loads(open(path, encoding="utf-8").read())
                self.assertEqual(man["verdict"], "partial")
                self.assertEqual(man["must_fix"], [])
                self.assertEqual(set(man["capabilities"]), V2_CAPS)
        finally:
            gc.evaluate = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
