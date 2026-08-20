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

The unit-cost side of that join is a **measured** cost per successful
interaction, so the pilot is seeded with a hash-chained
forecast + actuals + reconciliation bundle (`threadlight-consumption-iq`'s
`actuals` then `reconcile`). A forecast-only `specs/cost-manifest.json` is a
plan, not an outcome, and is asserted here to be ignored.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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
# The scorecard's unit-cost signal is a *measured* cost per successful
# interaction, so it can only come from a reconciled actuals bundle — a
# forecast number in specs/cost-manifest.json is a plan and is ignored.
SEEDED_COST_PER_INTERACTION = 0.0123
SEEDED_SUCCESSFUL_INTERACTIONS = 10000


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_sha256(doc: dict) -> str:
    """Digest matching threadlight-consumption-iq's `reconcile.sha256_json`."""
    payload = json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _seed_reconciled_cost(root: pathlib.Path) -> None:
    """Write the consumption-iq forecast + actuals + reconciliation bundle.

    The three documents are hash-chained the way `reconcile.py` emits them
    (canonical-JSON digests of the forecast and actuals, the raw specs/SPEC.md
    bytes as the § 14 policy anchor) and dated against the current clock, so the
    assessor's staleness re-check still passes when this runs months from now.
    """
    specs = root / "specs"
    specs.mkdir(exist_ok=True)
    deployment_manifest = json.loads(
        (specs / "manifest.json").read_text(encoding="utf-8")
    )[
        "deployment_manifest"
    ]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    forecast = {
        "schema_version": "1.0",
        "generated_at": _stamp(now - timedelta(days=7)),
        "totals": {"monthly_cost_current_usd": 500.0},
        "recommendations": [],
    }
    actuals = {
        "schema": "threadlight-cost-actuals/v1",
        "generated_at": _stamp(now - timedelta(hours=1)),
        "status": "pass",
        "scope": {
            "subscription_id": deployment_manifest["subscription_id"],
            "resource_group": deployment_manifest["resource_group"],
        },
        "window": {
            "start": _stamp(now - timedelta(days=8)),
            "end": _stamp(now - timedelta(days=1)),
            "complete_days": 7,
            "settlement_age_hours": 48,
            "window_end_age_days": 1,
        },
        "cost": {"basis": "usage-pretax", "period_total_usd": 123.0},
        "usage": {
            "interaction_status": "pass",
            "successful_interactions": SEEDED_SUCCESSFUL_INTERACTIONS,
        },
        "warnings": [],
    }
    reconciliation = {
        "schema": "threadlight-cost-reconciliation/v1",
        "generated_at": _stamp(now),
        "status": "pass",
        "variance_status": "pass",
        "forecast_ref": {
            "path": "specs/cost-manifest.json",
            "sha256": _canonical_sha256(forecast),
        },
        "actuals_ref": {
            "path": "specs/cost-actuals-manifest.json",
            "sha256": _canonical_sha256(actuals),
        },
        "policy_ref": {
            "path": "specs/SPEC.md",
            "section": 14,
            "spec_sha256": hashlib.sha256(
                (specs / "SPEC.md").read_bytes()).hexdigest(),
        },
        "policy_snapshot": {
            "max_forecast_variance_pct": 0.25,
            "max_token_volume_variance_pct": 0.30,
            "max_window_end_age_days": 30,
            "min_projection_attribution_coverage_pct": 0.95,
            "actual_billing_price_basis": "retail",
            "forecast_price_basis": "retail",
        },
        "policy_errors": [],
        "maturity": {"status": "pass", "checks": []},
        "totals": {
            "forecast_monthly_usd": 500.0,
            "actual_window_usd": 123.0,
            "variance_pct": 0.12,
        },
        "unit_economics": {
            "status": "pass",
            "successful_interactions": SEEDED_SUCCESSFUL_INTERACTIONS,
            "cost_per_successful_interaction_usd": SEEDED_COST_PER_INTERACTION,
            "target_usd": 0.02,
            "target_status": "pass",
        },
        "coverage": {
            "projection_attribution_coverage_pct": 1.0,
            "source_resource_id_coverage_pct": 1.0,
        },
        "drivers": {
            "payg_ptu": {
                "status": "pass",
                "observed_volume_variance_pct": 0.10,
                "forecast_monthly_tokens": 1000000,
                "observed_monthly_tokens": 1100000,
                "threshold_field": "max_token_volume_variance_pct",
                "threshold_pct": 0.30,
                "detail": "observed volume within declared band",
            }
        },
        "warnings": [],
    }
    for name, doc in (
        ("cost-manifest.json", forecast),
        ("cost-actuals-manifest.json", actuals),
        ("cost-reconciliation-manifest.json", reconciliation),
    ):
        (specs / name).write_text(json.dumps(doc), encoding="utf-8")


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
    # consumption-iq's artefacts — present in a real pilot, seeded here so the
    # scorecard can join the measured unit cost alongside eval quality.
    _seed_reconciled_cost(root)
    return root


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True, text=True, timeout=180,
    )


def _emit_legs(root: pathlib.Path) -> None:
    # A wide freshness window keeps the green fixtures time-stable: the redteam
    # scan and evals run carry fixed capture dates, so without this the verdict
    # would drift to "partial" once the fixture aged past each leg's default
    # window. Mirrors how the per-leg unit suites pin freshness.
    for script in (GOVERN, EVALS, REDTEAM):
        r = _run(str(script), "--target", str(root), "--emit", "--freshness-days", "36500")
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

    # govern's verdict vocabulary is ungoverned / partial / governed
    # (renamed from "wired" in #95 — AGT realignment).
    assert govern["verdict"] in ("governed", "partial")
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
    # the unit cost is the *measured* one, read out of the reconciled actuals
    # bundle, not the forecast in specs/cost-manifest.json
    assert abs(kpi["cost_per_interaction_usd"] - SEEDED_COST_PER_INTERACTION) < 1e-6
    # baselines + deviation alert declared by the citadel pilot fixture
    assert kpi["deviation_alert_present"] is True

    report = (root / "docs" / "pr-report.md").read_text()
    assert "## 8. Outcome KPI scorecard" in report
    assert "91%" in report
    assert "$0.0123" in report
    assert "`specs/cost-reconciliation-manifest.json` (threadlight-consumption-iq actuals)" in report


def test_scorecard_ignores_a_forecast_only_unit_cost(tmp_path):
    """A projected cost-per-interaction never satisfies the outcome KPI.

    Without a provable reconciliation bundle the unit-cost signal is unmeasured,
    even though the forecast carries the number in both shapes the retired
    forecast reader used to accept.
    """
    root = _build_combined_repo(tmp_path)
    _emit_legs(root)
    (root / "specs" / "cost-reconciliation-manifest.json").unlink()
    (root / "specs" / "cost-manifest.json").write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at": _stamp(datetime.now(timezone.utc).replace(microsecond=0)),
        "cost_per_interaction_usd": SEEDED_COST_PER_INTERACTION,
        "unit_economics": {"cost_per_interaction_usd": SEEDED_COST_PER_INTERACTION},
        "recommendations": [],
    }), encoding="utf-8")

    kpi = _run_scorecard(root)["kpi_scorecard"]
    assert kpi["cost_per_interaction_usd"] is None
    assert abs(kpi["eval_pass_rate"] - EXPECTED_EVAL_PASS_RATE) < 1e-6


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
        test_scorecard_ignores_a_forecast_only_unit_cost,
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
    print(f"\n=== {4 - failures}/4 passed ===")
    sys.exit(1 if failures else 0)
