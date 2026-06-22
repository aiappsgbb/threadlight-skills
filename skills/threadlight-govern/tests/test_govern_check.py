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


class WiredFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = gc.evaluate(WIRED, freshness_days=3650)
        self.man = gc.manifest(WIRED, self.caps, "auto", 3650)

    def test_middleware_detected(self):
        self.assertEqual(self.caps["middleware_wired_at_boundary"]["status"], "pass")

    def test_policy_present_and_versioned(self):
        self.assertEqual(self.caps["policy_artefact_present"]["status"], "pass")
        self.assertEqual(self.caps["policy_versioned"]["status"], "pass")

    def test_rai_block_detected(self):
        self.assertEqual(self.caps["rai_policy_present"]["status"], "pass")

    def test_verifier_present(self):
        self.assertEqual(self.caps["verifier_artefact_present"]["status"], "pass")

    def test_asi_reference(self):
        self.assertEqual(self.caps["asi_reference_present"]["status"], "pass")

    def test_verdict_no_must_fix(self):
        self.assertEqual(self.man["must_fix"], [])
        self.assertIn(self.man["verdict"], ("wired", "partial"))


class BareFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = gc.evaluate(BARE, freshness_days=90)
        self.man = gc.manifest(BARE, self.caps, "auto", 90)

    def test_middleware_missing_is_must_fix(self):
        self.assertEqual(self.caps["middleware_wired_at_boundary"]["status"], "must-fix")

    def test_policy_missing_is_must_fix(self):
        self.assertEqual(self.caps["policy_artefact_present"]["status"], "must-fix")

    def test_verdict_not_wired(self):
        self.assertEqual(self.man["verdict"], "not-wired")
        self.assertIn("policy_artefact_present", self.man["must_fix"])


class ManifestShapeTests(unittest.TestCase):
    def test_schema_and_keys(self):
        caps = gc.evaluate(WIRED, 3650)
        man = gc.manifest(WIRED, caps, "auto", 3650)
        self.assertEqual(man["schema"], gc.MANIFEST_SCHEMA)
        for key in ("verdict", "must_fix", "should_fix", "capabilities",
                    "captured_at", "tool_version"):
            self.assertIn(key, man)

    def test_gate_exit_code(self):
        # bare fixture must trip the gate
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
        finally:
            gc.evaluate = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
