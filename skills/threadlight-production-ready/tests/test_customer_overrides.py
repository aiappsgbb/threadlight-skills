#!/usr/bin/env python3
"""Gate per-customer policy overrides (Bucket 4 / SPEC §12)."""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
prod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = prod
_spec.loader.exec_module(prod)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SAMPLE_PILOT = pathlib.Path(__file__).resolve().parents[1] / "references" / "fixtures" / "sample-pilot"


class CustomerOverridesLoader(unittest.TestCase):
    def test_load_returns_none_when_path_is_none(self):
        self.assertIsNone(prod._load_customer_overrides(None))

    def test_load_returns_dict_for_valid_yaml(self):
        ov = prod._load_customer_overrides(FIXTURES / "customer-overrides-valid.yaml")
        self.assertEqual(ov["customer"], "acme-corp")
        self.assertEqual(len(ov["overrides"]), 2)
        self.assertEqual(ov["overrides"][0]["recipe_id"], "SEC-103")

    def test_load_raises_on_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            prod._load_customer_overrides(FIXTURES / "missing-customer-overrides.yaml")


class CustomerOverridesValidator(unittest.TestCase):
    def test_validate_passes_on_valid_payload(self):
        ov = prod._load_customer_overrides(FIXTURES / "customer-overrides-valid.yaml")
        prod._validate_customer_overrides(ov)

    def test_validate_rejects_missing_customer_field(self):
        with self.assertRaises(ValueError) as cm:
            prod._validate_customer_overrides({"overrides": []})
        self.assertIn("customer", str(cm.exception).lower())

    def test_validate_rejects_unknown_status_value(self):
        bad = {"customer": "x", "overrides": [{"recipe_id": "X", "status": "skip", "reason": "r"}]}
        with self.assertRaises(ValueError):
            prod._validate_customer_overrides(bad)

    def test_validate_requires_reason_string(self):
        bad = {"customer": "x", "overrides": [{"recipe_id": "X", "status": "pass"}]}
        with self.assertRaises(ValueError):
            prod._validate_customer_overrides(bad)


class CustomerOverridesApplier(unittest.TestCase):
    def test_apply_flips_status_pass_to_fail(self):
        findings = [{"recipe_id": "NET-201", "status": "pass", "severity": "warn"}]
        ov = {"customer": "x", "overrides": [{"recipe_id": "NET-201", "status": "fail", "reason": "r"}]}
        out = prod._apply_customer_overrides(findings, ov)
        self.assertEqual(out[0]["status"], "fail")
        self.assertEqual(out[0]["override_customer"], "x")
        self.assertEqual(out[0]["override_reason"], "r")
        self.assertEqual(findings[0]["status"], "pass")

    def test_apply_leaves_unmatched_findings_alone(self):
        findings = [{"recipe_id": "OTHER", "status": "pass", "severity": "warn"}]
        ov = {"customer": "x", "overrides": [{"recipe_id": "NET-201", "status": "fail", "reason": "r"}]}
        out = prod._apply_customer_overrides(findings, ov)
        self.assertEqual(out[0]["status"], "pass")
        self.assertNotIn("override_reason", out[0])


class CustomerOverridesMustFixRejection(unittest.TestCase):
    def test_apply_raises_when_override_targets_must_fix(self):
        findings = [{"recipe_id": "SEC-001", "status": "fail", "severity": "must-fix"}]
        ov = {"customer": "x", "overrides": [{"recipe_id": "SEC-001", "status": "pass", "reason": "r"}]}
        with self.assertRaises(SystemExit) as cm:
            prod._apply_customer_overrides(findings, ov)
        self.assertEqual(cm.exception.code, 2)

    def test_cli_exits_2_on_must_fix_override(self):
        args = [
            sys.executable, str(SCRIPT),
            "--root", str(SAMPLE_PILOT),
            "--static",
            "--accept-stale-safe-check",
            "--in-postdeploy", "tests/postdeploy-manifest.json",
            "--out", "tests/production-readiness-manifest.json",
            "--report", "docs/production-readiness-report.md",
            "--customer-overrides", str(FIXTURES / "customer-overrides-must-fix-override.yaml"),
            "--quiet",
        ]
        proc = subprocess.run(args, capture_output=True, text=True, check=False, timeout=180)
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        self.assertIn("must-fix", (proc.stderr + proc.stdout).lower())
        self.assertIn("SEC-001", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
