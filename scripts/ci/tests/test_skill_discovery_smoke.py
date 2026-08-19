"""Tests for scripts/ci/skill-discovery-smoke.sh's expected-skill computation.

Root cause this guards against
-------------------------------
main #116 added `skills/_shared`: a support directory holding a shared
library (`manifest.py`) and its own `tests/`, but no `SKILL.md`. The smoke
script treated *every* immediate directory under `skills/` as a skill, so it
expected `_shared` to show up in the Copilot CLI's Skill-tool registry — but
Copilot correctly registers only directories that actually declare a skill
via `SKILL.md`. That mismatch was a false-negative regression in the E2E
smoke gate (run #32287231962), not a real discovery failure.

The fix: the expected set is every immediate `skills/<name>/SKILL.md`, not
every immediate `skills/<name>/` directory. This file proves that in two
ways, without needing `jq`, `copilot`, or a network connection:

1. Against the real repo, using the script's own `THREADLIGHT_DISCOVERY_EXPECTED_ONLY=1`
   list-only mode (a bash subprocess, no other dependency).
2. Against a synthetic fixture tree that includes both a generic support
   directory (no `SKILL.md` -> excluded) and a real underscore-named skill
   directory (has `SKILL.md` -> included), so the fix can't be a hardcoded
   `_shared` exclusion in disguise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "skill-discovery-smoke.sh"


def _run_list_only(cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["THREADLIGHT_DISCOVERY_EXPECTED_ONLY"] = "1"
    script = cwd / "scripts" / "ci" / "skill-discovery-smoke.sh"
    return subprocess.run(
        ["bash", str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _direct_skill_md_parents(skills_dir: Path) -> set[str]:
    """Parent directory names of every direct skills/*/SKILL.md."""
    return {p.parent.name for p in skills_dir.glob("*/SKILL.md")}


class TestRealRepoListOnlyMode:
    """Exercise the actual script/skills against this checkout."""

    def test_script_exists_and_is_executable(self):
        assert SCRIPT.is_file(), f"{SCRIPT} missing"

    def test_list_only_mode_needs_no_jq_or_copilot(self):
        # A PATH with the coreutils the expected-set computation needs
        # (find/dirname/xargs/basename/sort/bash) but deliberately missing
        # jq/copilot/timeout/gtimeout proves the list-only branch returns
        # before any of those are required.
        needed = ("bash", "find", "dirname", "xargs", "basename", "sort")
        tmp_bin = Path(tempfile.mkdtemp(prefix="skill-discovery-smoke-path-"))
        try:
            for name in needed:
                real = shutil.which(name)
                assert real, f"test host is missing {name}, cannot build fixture PATH"
                os.symlink(real, tmp_bin / name)

            env = dict(os.environ)
            env["THREADLIGHT_DISCOVERY_EXPECTED_ONLY"] = "1"
            env["PATH"] = str(tmp_bin)
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"list-only mode should not require jq/copilot/timeout on PATH; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        finally:
            shutil.rmtree(tmp_bin, ignore_errors=True)

    def test_expected_equals_direct_skill_md_parents(self):
        result = _run_list_only(REPO_ROOT)
        assert result.returncode == 0, result.stderr

        printed = {line for line in result.stdout.splitlines() if line.strip()}
        expected = _direct_skill_md_parents(REPO_ROOT / "skills")

        assert printed == expected
        assert printed, "expected set must not be empty"

    def test_shared_support_dir_is_not_expected(self):
        shared = REPO_ROOT / "skills" / "_shared"
        assert shared.is_dir(), "fixture assumption: skills/_shared must exist"
        assert not (shared / "SKILL.md").exists(), (
            "fixture assumption: skills/_shared must have no SKILL.md "
            "(otherwise this test no longer covers the regression)"
        )

        result = _run_list_only(REPO_ROOT)
        assert result.returncode == 0, result.stderr
        printed = {line for line in result.stdout.splitlines() if line.strip()}

        assert "_shared" not in printed


class TestSyntheticFixture:
    """Prove the rule is 'has SKILL.md', not 'name != _shared'.

    Builds a throwaway repo-shaped tree with its own copy of the script so
    the exclusion can't be a disguised hardcoded `_shared` check: a generic
    support dir (`_support_lib`, no SKILL.md) must be excluded, and an
    underscore-named directory that *does* have SKILL.md must still count.
    """

    @pytest.fixture()
    def fixture_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="skill-discovery-smoke-fixture-"))
        try:
            script_dir = tmp / "scripts" / "ci"
            script_dir.mkdir(parents=True)
            shutil.copy2(SCRIPT, script_dir / "skill-discovery-smoke.sh")
            os.chmod(script_dir / "skill-discovery-smoke.sh", 0o755)

            skills_dir = tmp / "skills"

            # Real skill, ordinary name.
            real_a = skills_dir / "real-skill-a"
            real_a.mkdir(parents=True)
            (real_a / "SKILL.md").write_text("---\nname: real-skill-a\n---\n")

            # Real skill, underscore-prefixed name -- must still count.
            real_underscore = skills_dir / "_underscore_skill"
            real_underscore.mkdir(parents=True)
            (real_underscore / "SKILL.md").write_text(
                "---\nname: _underscore_skill\n---\n"
            )

            # Generic support directory: library + tests, no SKILL.md.
            # Mirrors the real skills/_shared shape that caused the regression.
            support = skills_dir / "_support_lib"
            (support / "tests").mkdir(parents=True)
            (support / "manifest.py").write_text("# shared helper, not a skill\n")
            (support / "tests" / "test_manifest.py").write_text(
                "def test_noop():\n    assert True\n"
            )

            yield tmp
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_support_dir_excluded_and_underscore_skill_included(self, fixture_repo):
        result = _run_list_only(fixture_repo)
        assert result.returncode == 0, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        printed = {line for line in result.stdout.splitlines() if line.strip()}

        assert printed == {"real-skill-a", "_underscore_skill"}
        assert "_support_lib" not in printed

    def test_matches_python_computed_expected_set(self, fixture_repo):
        result = _run_list_only(fixture_repo)
        assert result.returncode == 0

        printed = {line for line in result.stdout.splitlines() if line.strip()}
        expected = _direct_skill_md_parents(fixture_repo / "skills")

        assert printed == expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
