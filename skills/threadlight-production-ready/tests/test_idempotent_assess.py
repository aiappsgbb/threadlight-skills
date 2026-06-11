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
        """All assessor artifacts (basename match) are skipped, not just the report."""
        (self.tmp / "tests").mkdir()
        for name in mod.EXCLUDE_GLOBS:
            (self.tmp / "tests" / name).write_text("stale\n", encoding="utf-8")
            (self.tmp / "docs" / name).write_text("stale\n", encoding="utf-8")
        results = mod._glob_repo(
            self.tmp,
            "docs/**/*.md",
            "tests/**/*.md",
            "tests/**/*.json",
            "tests/**/*.csv",
            "README.md",
        )
        names = sorted(p.name for p in results)
        for forbidden in mod.EXCLUDE_GLOBS:
            self.assertNotIn(forbidden, names, f"_glob_repo leaked {forbidden}")

    def test_exclude_globs_matches_argparse_defaults(self):
        """EXCLUDE_GLOBS must be the basenames of every assessor output the
        argparse defaults emit. PR #34 review caught a drift where the tuple
        listed fictional filenames (`production-readiness-report.json`,
        `production-readiness-findings.csv`, `production-readiness-findings.md`)
        that no codepath ever writes — and was missing the actual outputs
        (`production-readiness-manifest.json`, `production-readiness-trend.csv`,
        `production-readiness-apply-plan.json`).
        """
        import argparse
        # Spin up the parser; pull defaults for the 4 output args.
        parser = argparse.ArgumentParser()
        mod._add_arguments(parser) if hasattr(mod, "_add_arguments") else None
        # The parser-building helper isn't always factored out; derive
        # defaults by scanning module source for the canonical assignments.
        import re
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        wanted_flags = ("--out", "--report", "--trend-csv", "--apply-plan-out")
        expected_basenames = set()
        for flag in wanted_flags:
            m = re.search(
                rf'add_argument\("{re.escape(flag)}".*?default="([^"]+)"',
                src, re.DOTALL,
            )
            if m and m.group(1):  # --apply-plan-out default is None; skip
                expected_basenames.add(pathlib.Path(m.group(1)).name)
        # Apply-plan basename is computed at runtime (production-readiness-apply-plan.json);
        # cite explicitly so the gate covers it even though argparse default is None.
        expected_basenames.add("production-readiness-apply-plan.json")
        actual = set(mod.EXCLUDE_GLOBS)
        self.assertEqual(
            actual, expected_basenames,
            "EXCLUDE_GLOBS drifted from argparse output defaults. "
            f"Expected {sorted(expected_basenames)}, got {sorted(actual)}.",
        )

    def test_exclude_globs_constant_exists(self):
        """The EXCLUDE_GLOBS tuple is the contract — gate its presence + shape."""
        self.assertTrue(hasattr(mod, "EXCLUDE_GLOBS"))
        self.assertIsInstance(mod.EXCLUDE_GLOBS, tuple)
        self.assertTrue(
            all(isinstance(x, str) for x in mod.EXCLUDE_GLOBS),
            "EXCLUDE_GLOBS must be a tuple of strings",
        )
        # Sanity: each entry is a bare basename (no slashes, no globs).
        for entry in mod.EXCLUDE_GLOBS:
            self.assertNotIn("/", entry, f"{entry!r}: EXCLUDE_GLOBS is basename-only")
            self.assertNotIn("*", entry, f"{entry!r}: EXCLUDE_GLOBS does not glob")


if __name__ == "__main__":
    unittest.main()
