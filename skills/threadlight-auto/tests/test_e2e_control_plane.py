"""Offline end-to-end smoke for the Discover/Protect/Govern control plane.

Reuses the per-leg "green" fixtures the way the live
``threadlight-e2e-foundry.yml`` workflow drives the real legs, but stays
fully offline + deterministic (no Azure, no network, no model calls) so it
can run for free as a CI gate.

The chain mirrors the spine order in ``threadlight-auto``:

    invoke (skipped — needs a live agent)
      -> govern   (threadlight-govern  -> specs/govern-manifest.json)
      -> evals    (threadlight-evals   -> specs/evals-manifest.json)
      -> redteam  (threadlight-redteam -> specs/redteam-manifest.json)
      -> assess   (threadlight-production-ready reads all three +
                   joins the outcome-KPI scorecard)
      -> ship     (threadlight-cicd renders the eval + red-team gates)

It is the offline counterpart of the case-study Foundry E2E: instead of
deploying an agent it overlays a production-ready pilot fixture with the
three legs' green fixtures, runs each leg's real CLI, then asserts the
scorecard actually *consumes* and *joins* their manifests.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]

GOVERN = REPO / "skills/threadlight-govern/scripts/govern_check.py"
EVALS = REPO / "skills/threadlight-evals/scripts/evals_check.py"
REDTEAM = REPO / "skills/threadlight-redteam/scripts/redteam_check.py"
PRODREADY = REPO / "skills/threadlight-production-ready/scripts/production_ready.py"
CICD = REPO / "skills/threadlight-cicd/scripts/generate_pipeline.py"

PR_FIXTURE = REPO / "skills/threadlight-production-ready/references/fixtures/sample-pilot-citadel"
GOVERN_FIXTURE = REPO / "skills/threadlight-govern/references/fixtures/sample-wired"
EVALS_FIXTURE = REPO / "skills/threadlight-evals/references/fixtures/sample-scheduled"
REDTEAM_FIXTURE = REPO / "skills/threadlight-redteam/references/fixtures/sample-clean"

# What the scheduled evals fixture's latest run reports — the value the KPI
# scorecard must surface end-to-end (evals/runs/2026-01-01.json).
EXPECTED_EVAL_PASS_RATE = 0.91
SEEDED_COST_PER_INTERACTION = 0.0123


def _build_combined_repo(tmp: pathlib.Path) -> pathlib.Path:
    """Overlay a production-ready pilot with the three legs' green fixtures,
    the way a real onboarded pilot repo would carry all of them at once."""
    root = tmp / "pilot"
    shutil.copytree(PR_FIXTURE, root)
    for fixture in (GOVERN_FIXTURE, EVALS_FIXTURE, REDTEAM_FIXTURE):
        for child in fixture.iterdir():
            dest = root / child.name
            if child.is_dir():
                shutil.copytree(child, dest, dirs_exist_ok=True)
            else:
                shutil.copy(child, dest)
    # consumption-iq's artefact — present in a real pilot, seeded here so the
    # scorecard can join unit cost alongside eval quality.
    (root / "specs").mkdir(exist_ok=True)
    (root / "specs" / "cost-manifest.json").write_text(
        json.dumps({"cost_per_interaction_usd": SEEDED_COST_PER_INTERACTION}),
        encoding="utf-8",
    )
    return root


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True, text=True, timeout=180,
    )


def _emit_legs(root: pathlib.Path) -> None:
    for script in (GOVERN, EVALS, REDTEAM):
        r = _run(str(script), "--target", str(root), "--emit")
        assert r.returncode == 0, f"{script.name} failed:\n{r.stdout}\n{r.stderr}"


def _run_scorecard(root: pathlib.Path) -> dict:
    out = root / "tests" / "pr-manifest.json"
    report = root / "docs" / "pr-report.md"
    r = _run(
        str(PRODREADY),
        "--root", str(root),
        "--target", "citadel-spoke",
        "--static", "--no-rights-probe", "--quiet",
        "--accept-stale-safe-check",
        "--out", str(out),
        "--report", str(report),
    )
    assert r.returncode == 0, f"production-ready failed:\n{r.stdout}\n{r.stderr}"
    assert out.exists(), "scorecard manifest not written"
    return json.loads(out.read_text())


# --------------------------------------------------------------------------
# Leg stage: each leg's real CLI emits a fresh, passing manifest.
# --------------------------------------------------------------------------

def test_legs_emit_passing_manifests(tmp_path):
    root = _build_combined_repo(tmp_path)
    _emit_legs(root)

    govern = json.loads((root / "specs" / "govern-manifest.json").read_text())
    evals = json.loads((root / "specs" / "evals-manifest.json").read_text())
    redteam = json.loads((root / "specs" / "redteam-manifest.json").read_text())

    assert govern["verdict"] in ("wired", "partial")
    assert govern["must_fix"] == []
    assert evals["verdict"] in ("comprehensive", "partial")
    # the evals leg surfaces the latest run's pass-rate so the scorecard can
    # join eval quality (KPI-003)
    assert abs(evals["metrics"]["pass_rate"] - EXPECTED_EVAL_PASS_RATE) < 1e-6
    assert redteam["verdict"] == "hardened"


# --------------------------------------------------------------------------
# Assess stage: the scorecard consumes the leg manifests and joins the KPIs.
# --------------------------------------------------------------------------

def test_scorecard_joins_outcome_kpis(tmp_path):
    root = _build_combined_repo(tmp_path)
    _emit_legs(root)
    manifest = _run_scorecard(root)

    kpi = manifest.get("kpi_scorecard")
    assert kpi is not None, "kpi_scorecard block missing from manifest"
    # the two joined signals come from two different legs
    assert abs(kpi["eval_pass_rate"] - EXPECTED_EVAL_PASS_RATE) < 1e-6
    assert abs(kpi["cost_per_interaction_usd"] - SEEDED_COST_PER_INTERACTION) < 1e-6
    # baselines + deviation alert declared by the citadel pilot fixture
    assert kpi["deviation_alert_present"] is True

    report = (root / "docs" / "pr-report.md").read_text()
    assert "## 8. Outcome KPI scorecard" in report
    assert "91%" in report
    assert "$0.0123" in report


# --------------------------------------------------------------------------
# Ship stage: the generated production pipeline carries the two leg gates.
# --------------------------------------------------------------------------

def test_cicd_renders_eval_and_redteam_gates(tmp_path):
    framing = tmp_path / "framing.json"
    framing.write_text(json.dumps({
        "platform": "github-actions",
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "repo_full_name": "aiappsgbb/contoso-pilot",
        "env_name": "prod",
    }), encoding="utf-8")
    out = tmp_path / "rendered"
    r = _run(
        str(CICD),
        "--framing-file", str(framing),
        "--out", str(out),
        "--eval-gate", "hard",
    )
    assert r.returncode == 0, f"cicd generate failed:\n{r.stdout}\n{r.stderr}"
    wf = (out / ".github/workflows/azd-deploy-prod.yml").read_text()
    assert "eval-gate" in wf
    assert "red-team-gate" in wf


if __name__ == "__main__":
    import tempfile

    failures = 0
    for fn in (
        test_legs_emit_passing_manifests,
        test_scorecard_joins_outcome_kpis,
        test_cicd_renders_eval_and_redteam_gates,
    ):
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            fn(d)
            print(f"\u2705 {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"\u274c {fn.__name__}: {exc}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n=== {3 - failures}/3 passed ===")
    sys.exit(1 if failures else 0)
