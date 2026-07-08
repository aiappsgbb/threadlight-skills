"""stdlib unittest suite for evals_check.py (no pytest)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import evals_check as ec  # noqa: E402

FIXTURES = os.path.join(SKILL, "references", "fixtures")
SCHEDULED = os.path.join(FIXTURES, "sample-scheduled")
MANUAL = os.path.join(FIXTURES, "sample-manual")


class ScheduledFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = ec.evaluate(SCHEDULED, freshness_days=3650)
        self.man = ec.manifest(SCHEDULED, self.caps, 3650)

    def test_core_evals_detected(self):
        for key in (
            "eval_scenarios_present",
            "eval_datasets_present",
            "dataset_shape_ok",
            "thresholds_declared",
            "schedule_present",
            "run_history_present",
        ):
            self.assertEqual(self.caps[key]["status"], "pass", key)

    def test_online_and_ab_are_detected(self):
        self.assertEqual(self.caps["online_eval_wired"]["status"], "pass")
        self.assertEqual(self.caps["ab_comparison_present"]["status"], "pass")

    def test_alert_detected_and_no_must_fix(self):
        self.assertEqual(self.caps["alert_wired"]["status"], "pass")
        self.assertEqual(self.man["must_fix"], [])
        self.assertIn(self.man["verdict"], ("comprehensive", "partial"))


class ManualFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = ec.evaluate(MANUAL, freshness_days=3650)
        self.man = ec.manifest(MANUAL, self.caps, 3650)

    def test_manual_fixture_has_required_gaps(self):
        self.assertEqual(self.caps["schedule_present"]["status"], "must-fix")
        self.assertEqual(self.caps["alert_wired"]["status"], "must-fix")
        self.assertIn("schedule_present", self.man["must_fix"])
        self.assertIn("alert_wired", self.man["must_fix"])

    def test_manual_fixture_is_not_comprehensive(self):
        self.assertNotEqual(self.man["verdict"], "comprehensive")
        self.assertIn(self.man["verdict"], ("none", "offline-only"))

    def test_generated_report_does_not_satisfy_alert(self):
        docs = os.path.join(MANUAL, "docs")
        report = os.path.join(docs, "evals-report.md")
        os.makedirs(docs, exist_ok=True)
        try:
            with open(report, "w", encoding="utf-8") as fh:
                fh.write("alert_wired EVAL-104 pass metric alert eval threshold")
            caps = ec.evaluate(MANUAL, freshness_days=3650)
            self.assertEqual(caps["alert_wired"]["status"], "must-fix")
        finally:
            if os.path.exists(report):
                os.remove(report)
            try:
                os.rmdir(docs)
            except OSError:
                pass


class MetricsBlockTests(unittest.TestCase):
    """The manifest must surface the latest run's pass-rate so the
    production-ready KPI scorecard can join eval quality (KPI-003)."""

    def test_scheduled_manifest_surfaces_pass_rate(self):
        caps = ec.evaluate(SCHEDULED, 3650)
        man = ec.manifest(SCHEDULED, caps, 3650)
        self.assertIn("metrics", man)
        metrics = man["metrics"]
        self.assertAlmostEqual(metrics["pass_rate"], 0.91, places=4)
        self.assertIsInstance(metrics["threshold"], float)
        self.assertTrue(metrics["latest_run"].startswith("evals/runs/"))

    def test_manual_manifest_pass_rate_is_none_when_no_run(self):
        caps = ec.evaluate(MANUAL, 3650)
        man = ec.manifest(MANUAL, caps, 3650)
        self.assertIn("metrics", man)
        self.assertIsNone(man["metrics"]["pass_rate"])


class ManifestShapeTests(unittest.TestCase):
    def test_schema_and_required_keys(self):
        caps = ec.evaluate(SCHEDULED, 3650)
        man = ec.manifest(SCHEDULED, caps, 3650)
        self.assertEqual(man["schema"], ec.MANIFEST_SCHEMA)
        for key in (
            "tool_version",
            "captured_at",
            "verdict",
            "must_fix",
            "should_fix",
            "not_verified",
            "capabilities",
            "freshness_window_days",
            "metrics",
        ):
            self.assertIn(key, man)

    def test_capability_keys_are_contract_keys(self):
        caps = ec.evaluate(SCHEDULED, 3650)
        self.assertEqual(set(caps), set(ec.CAPABILITY_ORDER))

    def test_gate_exit_code_for_manual(self):
        rc = ec.main(["--target", MANUAL, "--gate", "--freshness-days", "3650"])
        self.assertEqual(rc, 2)

    def test_gate_exit_code_for_scheduled(self):
        rc = ec.main(["--target", SCHEDULED, "--gate", "--freshness-days", "3650"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
