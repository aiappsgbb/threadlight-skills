"""Gate that the assessor never ingests its own outputs (issue #30).

The repro: 1st assessor run writes docs/production-readiness-report.md.
On a 2nd run, RepoContext.from_repo's _glob_repo("docs/**/*.md", "README.md")
finds the report and folds it into docs_text. That makes the 2nd run drift.

The fix: EXCLUDE_GLOBS tuple + filter in _glob_repo, so files matching
production-readiness-*.{md,json,csv} are skipped.

stdlib-only unit test — no subprocess, no fixtures.
"""
import importlib.util
import pathlib
import shutil
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


class IdempotentAssess(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="threadlight-idemp-"))
        (self.tmp / "docs").mkdir()
        # Simulate a 1st-run artifact left in the repo:
        (self.tmp / "docs" / "production-readiness-report.md").write_text(
            "# Stale assessor output from a prior run\n", encoding="utf-8"
        )
        (self.tmp / "docs" / "intro.md").write_text("# Real docs\n", encoding="utf-8")
        (self.tmp / "README.md").write_text("# Real readme\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_glob_repo_excludes_self_outputs(self):
        results = mod._glob_repo(self.tmp, "docs/**/*.md", "README.md")
        names = sorted(p.name for p in results)
        self.assertNotIn(
            "production-readiness-report.md",
            names,
            "_glob_repo must skip its own outputs. See #30.",
        )
        # Real docs are still included.
        self.assertEqual(names, ["README.md", "intro.md"])

    def test_glob_repo_excludes_all_assessor_artifacts(self):
        """All 4 assessor artifact names are skipped, not just the report."""
        (self.tmp / "tests").mkdir()
        for name in (
            "production-readiness-report.md",
            "production-readiness-report.json",
            "production-readiness-findings.csv",
            "production-readiness-findings.md",
        ):
            (self.tmp / "tests" / name).write_text("stale\n", encoding="utf-8")
            (self.tmp / "docs" / name).write_text("stale\n", encoding="utf-8")
        results = mod._glob_repo(self.tmp, "docs/**/*.md", "tests/**/*.md", "tests/**/*.json", "tests/**/*.csv", "README.md")
        names = sorted(p.name for p in results)
        for forbidden in (
            "production-readiness-report.md",
            "production-readiness-report.json",
            "production-readiness-findings.csv",
            "production-readiness-findings.md",
        ):
            self.assertNotIn(forbidden, names, f"_glob_repo leaked {forbidden}")

    def test_exclude_globs_constant_exists(self):
        """The EXCLUDE_GLOBS tuple is the contract — gate its content."""
        self.assertTrue(hasattr(mod, "EXCLUDE_GLOBS"))
        self.assertIn("production-readiness-report.md", mod.EXCLUDE_GLOBS)
        self.assertIn("production-readiness-report.json", mod.EXCLUDE_GLOBS)
        self.assertIn("production-readiness-findings.csv", mod.EXCLUDE_GLOBS)
        self.assertIn("production-readiness-findings.md", mod.EXCLUDE_GLOBS)


if __name__ == "__main__":
    unittest.main()
