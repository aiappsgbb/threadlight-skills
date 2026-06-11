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


class CustomerOverridesParserStrict(unittest.TestCase):
    """W2 — strict-mode rejections to protect the audit trail.

    The mini-YAML parser is intentionally not feature-complete. Rather than
    silently degrade (e.g. swallow a `reason: |` block scalar's body), it
    rejects loudly so an operator can't accidentally apply an override
    whose justification text was lost.
    """

    def test_load_rejects_block_scalar(self):
        with self.assertRaises(ValueError) as cm:
            prod._load_customer_overrides(FIXTURES / "customer-overrides-block-scalar.yaml")
        self.assertIn("block scalar", str(cm.exception).lower())

    def test_load_rejects_tab_indentation(self):
        with self.assertRaises(ValueError) as cm:
            prod._load_customer_overrides(FIXTURES / "customer-overrides-tab-indent.yaml")
        self.assertIn("tab", str(cm.exception).lower())

    def test_load_rejects_unquoted_inline_comment(self):
        with self.assertRaises(ValueError) as cm:
            prod._load_customer_overrides(FIXTURES / "customer-overrides-inline-comment.yaml")
        self.assertIn("#", str(cm.exception))
        self.assertIn("quote", str(cm.exception).lower())

    def test_load_accepts_quoted_value_containing_hash(self):
        """A `#` inside a properly quoted value is fine — only unquoted
        ` #` is ambiguous."""
        import tempfile
        text = (
            'customer: acme-corp\n'
            'overrides:\n'
            '  - recipe_id: SEC-103\n'
            '    status: pass\n'
            '    reason: "stale # 2025-04-22 — review by Q3"\n'
        )
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(text)
            path = f.name
        try:
            ov = prod._load_customer_overrides(path)
            self.assertIn("#", ov["overrides"][0]["reason"])
        finally:
            pathlib.Path(path).unlink()

    def test_load_rejects_duplicate_top_level_key(self):
        with self.assertRaises(ValueError) as cm:
            prod._load_customer_overrides(FIXTURES / "customer-overrides-duplicate-top-key.yaml")
        self.assertIn("duplicate", str(cm.exception).lower())
        self.assertIn("customer", str(cm.exception))

    def test_load_rejects_duplicate_recipe_id(self):
        with self.assertRaises(ValueError) as cm:
            prod._load_customer_overrides(FIXTURES / "customer-overrides-duplicate-recipe-id.yaml")
        self.assertIn("duplicate recipe_id", str(cm.exception).lower())
        self.assertIn("SEC-103", str(cm.exception))

    def test_load_rejects_unknown_top_level_key(self):
        with self.assertRaises(ValueError) as cm:
            prod._load_customer_overrides(FIXTURES / "customer-overrides-unknown-top-key.yaml")
        self.assertIn("unknown top-level key", str(cm.exception).lower())
        self.assertIn("typo_field", str(cm.exception))

    def test_validate_rejects_unknown_override_item_key(self):
        ov = prod._load_customer_overrides(FIXTURES / "customer-overrides-unknown-item-key.yaml")
        with self.assertRaises(ValueError) as cm:
            prod._validate_customer_overrides(ov)
        self.assertIn("unknown", str(cm.exception).lower())
        self.assertIn("severity", str(cm.exception))


class CustomerOverridesPathRestriction(unittest.TestCase):
    """W3 — --customer-overrides must reject combinations where the
    overrides would be silently dropped (i.e. anything other than the
    v0.3.0 assess codepath actually loads + applies them)."""

    def _run(self, *flags):
        args = [sys.executable, str(SCRIPT), *flags, "--quiet"]
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)

    def test_rejects_combination_with_onboard(self):
        proc = self._run(
            "--customer-overrides", str(FIXTURES / "customer-overrides-valid.yaml"),
            "--onboard",
            "--framing-file", str(SAMPLE_PILOT / "framing.json") if (SAMPLE_PILOT / "framing.json").is_file()
                                else str(FIXTURES / "customer-overrides-valid.yaml"),
            "--root", str(SAMPLE_PILOT),
        )
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        combined = (proc.stderr + proc.stdout).lower()
        self.assertIn("--customer-overrides", proc.stderr + proc.stdout)
        self.assertIn("--onboard", proc.stderr + proc.stdout)

    def test_rejects_combination_with_remediate(self):
        proc = self._run(
            "--customer-overrides", str(FIXTURES / "customer-overrides-valid.yaml"),
            "--remediate", "SEC-001",
        )
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        self.assertIn("--customer-overrides", proc.stderr + proc.stdout)
        self.assertIn("--remediate", proc.stderr + proc.stdout)

    def test_rejects_standalone_scaffold_without_manifest(self):
        import tempfile
        # An empty cwd has no specs/manifest.json, so --scaffold-cicd takes
        # the standalone branch.
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(
                "--customer-overrides", str(FIXTURES / "customer-overrides-valid.yaml"),
                "--scaffold-cicd",
                "--framing-file", str(FIXTURES.parent.parent / "references" / "fixtures" /
                                       "sample-pilot-restricted" / "framing.json"),
                "--repo-full-name", "octocat/demo",
                "--root", tmp,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        self.assertIn("--customer-overrides", proc.stderr + proc.stdout)
        self.assertIn("--scaffold-cicd", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
