#!/usr/bin/env python3
"""Tests for `--remediate FID` (v0.3.0).

`_emit_remediation(root, fid)` reads `references/remediation-recipes.yaml`
and prints the recipe block for a given finding ID to stdout. On a hit
it returns 0; on a miss (or missing recipes file) it returns 2.

The renderer is intentionally simple — split-on-`^- id:` — so the test
suite needs to lock in the contract (case-insensitive ID match, exit
codes, recipes-file location resolution).

stdlib-only; no pytest.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
RECIPES = SKILL_DIR / "references" / "remediation-recipes.yaml"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402

FAILURES: list[str] = []


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# 1) Hit: known recipe ID returns 0 and prints a block that contains the ID
#    and a `# remediation recipe —` header.
# ---------------------------------------------------------------------------


def t_known_recipe_prints_block() -> None:
    print("\nt_known_recipe_prints_block")
    if not RECIPES.exists():
        expect(False, "recipes: file present")
        return
    # Pick a real ID from the recipes file.
    buf = io.StringIO()
    err = io.StringIO()
    # Root that doesn't have a recipes file -> exercise the fallback path
    # ("relative to this script's parent/parent/references/").
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "NET-001")
    expect(rc == 0, f"hit: exit 0 (got {rc}; stderr: {err.getvalue()[:200]})")
    out = buf.getvalue()
    expect("# remediation recipe — NET-001" in out,
           "hit: header line printed with finding ID")
    expect("- id: NET-001" in out, "hit: original block id line present")


def t_case_insensitive_lookup() -> None:
    print("\nt_case_insensitive_lookup")
    buf = io.StringIO()
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "net-001")  # lowercased
    expect(rc == 0, f"case-insensitive: exit 0 (got {rc})")
    expect("NET-001" in buf.getvalue(), "case-insensitive: normalized to upper")


def t_unknown_id_exits_2() -> None:
    print("\nt_unknown_id_exits_2")
    buf = io.StringIO()
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "XYZ-NEVER-9999")
    expect(rc == 2, f"miss: exit 2 (got {rc})")
    expect("no remediation recipe found" in err.getvalue().lower(),
           "miss: stderr message present")


# ---------------------------------------------------------------------------
# 2) Missing recipes file (both root-local and skill-local missing) -> exit 2
#    with a stderr hint pointing at the canonical path.
# ---------------------------------------------------------------------------


def t_missing_recipes_file() -> None:
    print("\nt_missing_recipes_file")
    # Build an isolated tree that DOES contain skills/threadlight-production-ready
    # but with NO recipes file at either location.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "skills" / "threadlight-production-ready" / "references").mkdir(parents=True)
        # Monkey-patch __file__ resolution by calling against a stand-in
        # `production_ready.py` that lives under root and has no sibling
        # references/. We do this by importing a shim module.
        shim_script = root / "skills" / "threadlight-production-ready" / "scripts" / "production_ready.py"
        shim_script.parent.mkdir(parents=True)
        shim_script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(shim_script),
             "--root", str(root),
             "--remediate", "NET-001"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        expect(proc.returncode == 2,
               f"missing-file: exit 2 (got {proc.returncode}; "
               f"stderr: {proc.stderr[:200]})")
        expect("not found" in (proc.stderr or "").lower()
               or "no remediation" in (proc.stderr or "").lower(),
               "missing-file: stderr surfaces the gap")


# ---------------------------------------------------------------------------
# 3) End-to-end CLI: `python production_ready.py --remediate NET-001` returns 0
#    and prints the recipe to stdout.
# ---------------------------------------------------------------------------


def t_cli_end_to_end() -> None:
    print("\nt_cli_end_to_end")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--remediate", "NET-001"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    expect(proc.returncode == 0,
           f"cli: exit 0 (got {proc.returncode}; stderr: {proc.stderr[:200]})")
    expect("NET-001" in proc.stdout,
           "cli: ID echoed in stdout")


# ---------------------------------------------------------------------------
# 4) Per-file recipe catalog (pytest-collected).
#
#    The legacy yaml holds only ~12 finding IDs, but the maintained catalog is
#    `references/remediation-recipes/{FID}.md` (70+ recipes). `--remediate`
#    must resolve those too — yaml stays authoritative for the IDs it defines
#    (they ship ready-to-run bash), with a fall-back to the per-file `.md`.
#
#    These are `test_*` functions so `pytest` actually collects them (the
#    `t_*` runner above is bridged into pytest by
#    `test_legacy_remediate_runner_passes`).
# ---------------------------------------------------------------------------


def test_per_file_only_recipe_is_reachable() -> None:
    """A finding ID that exists only as references/remediation-recipes/{FID}.md
    (never in the legacy yaml) resolves and exits 0."""
    buf, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "NET-502")
    assert rc == 0, f"expected 0, got {rc}; stderr={err.getvalue()[:300]}"
    out = buf.getvalue()
    assert "# remediation recipe — NET-502" in out, out[:300]
    # marker unique to NET-502.md's body
    assert "citadel-spoke-onboarding" in out, out[:400]


def test_per_file_recipe_case_insensitive() -> None:
    buf, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "net-502")  # lowercased
    assert rc == 0, f"expected 0, got {rc}; stderr={err.getvalue()[:300]}"
    assert "NET-502" in buf.getvalue()


def test_yaml_recipe_still_authoritative() -> None:
    """A yaml-only ID must still resolve after the per-file fall-back is added."""
    buf, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "GOV-101")
    assert rc == 0, f"expected 0, got {rc}; stderr={err.getvalue()[:300]}"
    assert "# remediation recipe — GOV-101" in buf.getvalue()


def test_unknown_recipe_still_exits_2() -> None:
    buf, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as td, \
            redirect_stdout(buf), redirect_stderr(err):
        rc = pr._emit_remediation(Path(td), "ZZZ-9999")
    assert rc == 2
    assert "no remediation recipe" in err.getvalue().lower()


def test_legacy_remediate_runner_passes() -> None:
    """Bridge the stdlib `t_*` runner into pytest so its contract is
    actually CI-enforced (pytest does not collect `t_*` functions)."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__))],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    tests = [
        t_known_recipe_prints_block,
        t_case_insensitive_lookup,
        t_unknown_id_exits_2,
        t_missing_recipes_file,
        t_cli_end_to_end,
    ]
    print(f"Running {len(tests)} --remediate tests")
    for t in tests:
        try:
            t()
        except Exception as exc:  # pragma: no cover
            FAILURES.append(t.__name__)
            print(f"  [FAIL] {t.__name__} raised: {type(exc).__name__}: {exc}")
    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return len(FAILURES)
    print(f"✅ All {len(tests)} test(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
