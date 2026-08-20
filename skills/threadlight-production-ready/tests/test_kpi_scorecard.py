#!/usr/bin/env python3
"""Tests for the outcome-KPI scorecard (F7).

CAF's agent observability triad puts *baselines* (latency, cost-per-interaction,
success-rate) and *deviation alerts* under observability. This leg joins three
already-collected signals into one measurable outcome view:

  * eval pass-rate                    (specs/evals-manifest.json — threadlight-evals)
  * cost per successful interaction   (specs/cost-reconciliation-manifest.json —
                                       threadlight-consumption-iq actuals)
  * traces emitting                   (foundry-observability wiring in infra/src)

The cost signal is an **actual**, never a forecast. It is read only out of the
reconciliation artifact bundle, through the same strict loader COST-102/COST-103
use (`_read_cost_reconciliation_bundle`): schemas, canonical-JSON digests of the
forecast and actuals, the raw `specs/SPEC.md` anchor, verdict-after-evidence
timestamps and today's staleness re-check. `specs/cost-manifest.json` is a
projection — a cost-per-interaction number in it is a *plan*, so it can never
satisfy KPI-003 on its own. (COST-005/006/007 keep consuming that forecast; this
leg does not.)

It is also a *re-derived* actual: the relayed
`unit_economics.cost_per_successful_interaction_usd` is only reported when it
matches `actuals.cost.period_total_usd / actuals.usage.successful_interactions`
recomputed at the reconciler's own precision, out of the digest-pinned actuals
document. A reconciliation that quotes a unit cost (or a success count) its own
canonical evidence does not support is withheld, not relayed.

It emits three should-fix, tier-0 findings under the observability pillar
(KPI-001 baselines declared, KPI-002 deviation alert wired, KPI-003 scorecard
joinable) and renders an "Outcome KPI scorecard" section in the report.

Contract pinned here:
  * KPI findings are should-fix / tier-0 (never must-fix) — so they never force
    a recipe (test_recipe_catalog) and never flip a green pilot red on must-fix.
  * `_check_kpi_static`, `_kpi_signals` and `_read_cost_per_interaction` never
    raise (missing / garbage inputs degrade to gaps, not crashes).
  * Backward-compat: an empty repo still produces KPI-001..003 as gaps, never
    `pass`, never `must-fix`.

pytest-style (bare ``test_`` functions + ``assert``); stdlib only. The bundle
tests take pytest fixtures (``tmp_path`` / ``monkeypatch``) and are skipped by
the ``__main__`` fallback runner below.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(TEST_DIR))
import production_ready as pr  # noqa: E402

# The reconciliation bundle builder is shared, not re-implemented: one place
# knows how the forecast / actuals / SPEC digests are chained, so a KPI test can
# never accidentally assert against a bundle shape the COST leg would reject.
from test_cost_reconciliation import (  # noqa: E402
    SPEC_TEXT as RECON_SPEC_TEXT,
    UNIT_ECONOMICS,
    _actuals,
    _forecast,
    _reconciliation,
    _write_bundle,
)

# Frozen against *this* module's `production_ready` object. Sibling suites load
# the script through `importlib.spec_from_file_location`, so `sys.modules` can
# hold a different module instance by the time the full suite runs — patching
# the imported helper's `pr` would then silently miss ours and every bundle
# would age out on the real clock.
NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def _freeze(monkeypatch, now: datetime = NOW) -> None:
    monkeypatch.setattr(pr, "_cost_reconciliation_now", lambda: now)


def _make_ctx(
    *,
    spec_text: str = "",
    bicep_text: str = "",
    src_text: str = "",
    docs_text: str = "",
    manifests: dict[str, dict] | None = None,
) -> "pr.RepoContext":
    tmpdir = Path(tempfile.mkdtemp())
    specs = tmpdir / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    for name, data in (manifests or {}).items():
        # allow a raw string to be written (for garbage-manifest tests)
        if isinstance(data, str):
            (specs / name).write_text(data, encoding="utf-8")
        else:
            (specs / name).write_text(json.dumps(data), encoding="utf-8")
    bg = pr.BicepGraph(resources=[], source_files=[])
    return pr.RepoContext(
        root=tmpdir,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text=spec_text,
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text=docs_text,
        azd_env={},
        manifest={},
        bicep_text=bicep_text,
        src_text=src_text,
        bicep_graph=bg,
    )


def _by_id(findings) -> dict[str, "pr.Finding"]:
    return {f.id: f for f in findings}


def _evals_manifest(pass_rate: float | None = 0.97) -> dict:
    m = {
        "schema": "threadlight-evals-manifest/v1",
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "verdict": "comprehensive",
        "capabilities": {},
    }
    if pass_rate is not None:
        m["metrics"] = {"pass_rate": pass_rate}
    return m


def _cost_manifest(cpi: float | None = 0.012) -> dict:
    """A *forecast* cost projection — `threadlight-consumption-iq` v1 shape.

    A cost-per-interaction here is a planned number, not a measured one, so it
    must never reach the KPI scorecard. Kept (with the CPI keys) precisely so
    the tests below can prove the forecast reader is gone.
    """
    m = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "recommendations": [],
    }
    if cpi is not None:
        m["cost_per_interaction_usd"] = cpi
        m["unit_economics"] = {"cost_per_interaction_usd": cpi}
    return m


def _kpi_ctx(
    tmp_path: Path,
    *,
    src_text: str = "",
    spec_text: str | None = RECON_SPEC_TEXT,
    bicep_text: str = "",
    evals: dict | str | None = None,
    **bundle_kwargs: object,
) -> "pr.RepoContext":
    """Write a hash-consistent reconciliation bundle plus the eval signal.

    Delegates the forecast / actuals / SPEC digest chaining to
    `test_cost_reconciliation._write_bundle`, then layers on the two signals
    that bundle knows nothing about (eval pass-rate, OTel wiring in src).
    """
    _write_bundle(tmp_path, spec_text=spec_text, **bundle_kwargs)  # type: ignore[arg-type]
    specs = tmp_path / "specs"
    if evals is not None:
        payload = evals if isinstance(evals, str) else json.dumps(evals)
        (specs / "evals-manifest.json").write_text(payload, encoding="utf-8")
    actuals = bundle_kwargs.get("actuals")
    if not isinstance(actuals, dict):
        actuals = _actuals()
    actual_scope = actuals.get("scope") if isinstance(actuals, dict) else None
    manifest = {}
    if isinstance(actual_scope, dict):
        sub = actual_scope.get("subscription_id")
        rg = actual_scope.get("resource_group")
        if isinstance(sub, str) and isinstance(rg, str):
            manifest = {
                "deployment_manifest": {
                    "subscription_id": sub,
                    "resource_group": rg,
                }
            }
    return pr.RepoContext(
        root=tmp_path,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text=spec_text or "",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest=manifest,
        bicep_text=bicep_text,
        src_text=src_text,
        bicep_graph=pr.BicepGraph(resources=[], source_files=[]),
    )


def _unit(**overrides: object) -> dict:
    """The reconciler's `unit_economics` block with targeted overrides."""
    block = dict(UNIT_ECONOMICS)
    block.update(overrides)
    return block


_OBS_SRC = "from azure.monitor.opentelemetry import configure_azure_monitor\n"

_SPEC_WITH_BASELINES = """
## 11. KPIs and outcome baselines
- Target p95 latency: 2500 ms
- Target cost-per-interaction: $0.02
- Target task success rate: 95%
"""

_BICEP_WITH_KPI_ALERT = """
resource latencyAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'agent-latency-deviation'
  properties: {
    description: 'Alert when request duration drifts above baseline'
  }
}
"""


# ---------------------------------------------------------------------------
# Catalog contract
# ---------------------------------------------------------------------------

def test_kpi_finding_ids_in_catalog() -> None:
    for fid in ("KPI-001", "KPI-002", "KPI-003"):
        assert fid in pr.FINDING_CATALOG, f"{fid} missing from FINDING_CATALOG"
        meta = pr.FINDING_CATALOG[fid]
        assert meta["pillar"] == "observability", f"{fid} must be under observability"
        assert meta["severity"] == "should-fix", f"{fid} must be should-fix"
        assert meta["tier"] == 0, f"{fid} must be tier 0 (static)"


def test_kpi003_title_names_the_actual_unit_cost() -> None:
    """The catalog title is what the uplift list and report tables echo.

    KPI-003's cost input is a measured actual per *successful* interaction, so
    a title reading "cost/interaction" understates what it takes to pass and
    reads like the forecast KPI-001 asks teams to declare.
    """
    title = pr.FINDING_CATALOG["KPI-003"]["title"]
    assert "actual cost/successful interaction" in title, title


# ---------------------------------------------------------------------------
# _check_kpi_static
# ---------------------------------------------------------------------------

def test_empty_repo_emits_gaps_never_pass_or_mustfix() -> None:
    findings = pr._check_kpi_static(_make_ctx())
    f = _by_id(findings)
    for fid in ("KPI-001", "KPI-002", "KPI-003"):
        assert fid in f, f"{fid} not emitted on empty repo"
        assert f[fid].status in ("should-fix", "not-verified"), \
            f"{fid} should be a gap on empty repo, got {f[fid].status}"
        assert f[fid].status not in ("pass", "must-fix")


def test_baselines_declared_passes_kpi001() -> None:
    ctx = _make_ctx(spec_text=_SPEC_WITH_BASELINES)
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-001"].status == "pass", \
        f"KPI-001 expected pass with all baselines declared, got {f['KPI-001'].status}: {f['KPI-001'].detail}"


def test_missing_one_baseline_keeps_kpi001_gap() -> None:
    spec = "## KPIs\n- Target p95 latency: 2500 ms\n- Target cost-per-interaction: $0.02\n"
    ctx = _make_ctx(spec_text=spec)  # no success-rate
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-001"].status == "should-fix"
    assert "success" in f["KPI-001"].detail.lower()


def test_deviation_alert_passes_kpi002() -> None:
    ctx = _make_ctx(bicep_text=_BICEP_WITH_KPI_ALERT)
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-002"].status == "pass", \
        f"KPI-002 expected pass with a KPI deviation alert, got {f['KPI-002'].status}: {f['KPI-002'].detail}"


def test_joined_scorecard_passes_kpi003(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, src_text=_OBS_SRC, evals=_evals_manifest(0.97))
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status == "pass", \
        f"KPI-003 expected pass when all three signals present, got {f['KPI-003'].status}: {f['KPI-003'].detail}"
    assert "0.1083" in f["KPI-003"].detail


def test_partial_scorecard_is_should_fix(tmp_path, monkeypatch) -> None:
    # only the actual unit cost present — no evals, no traces
    _freeze(monkeypatch)
    f = _by_id(pr._check_kpi_static(_kpi_ctx(tmp_path)))
    assert f["KPI-003"].status == "should-fix"


def test_no_signals_kpi003_not_verified() -> None:
    f = _by_id(pr._check_kpi_static(_make_ctx()))
    assert f["KPI-003"].status == "not-verified"


# ---------------------------------------------------------------------------
# The cost signal is an ACTUAL, not a forecast
# ---------------------------------------------------------------------------

def test_forecast_cost_manifest_alone_is_not_a_kpi_signal() -> None:
    """A projected cost-per-interaction is a plan, never a measured outcome.

    `specs/cost-manifest.json` carries the number in both shapes the retired
    forecast reader used to accept. Neither may reach KPI-003, or a pilot that
    never billed a single interaction could report a green unit-cost outcome.
    """
    ctx = _make_ctx(
        src_text=_OBS_SRC,
        manifests={
            "evals-manifest.json": _evals_manifest(0.97),
            "cost-manifest.json": _cost_manifest(0.012),
        },
    )
    assert pr._read_cost_per_interaction(ctx) is None
    sig = pr._kpi_signals(ctx)
    assert sig["cost_per_interaction_usd"] is None
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status != "pass"
    assert f["KPI-003"].status == "should-fix"


def test_actual_unit_cost_read_from_reconciliation(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path)
    value = pr._read_cost_per_interaction(ctx)
    assert value is not None and abs(value - 0.1083) < 1e-9


def test_off_target_actuals_withhold_cost_evidence_and_kpi_measurement(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        spec_text=RECON_SPEC_TEXT + _SPEC_WITH_BASELINES,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
    )
    ctx.manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-other",
        }
    }

    summary = pr._cost_evidence_summary(ctx)
    assert summary["status"] == "not-verified"
    assert "scope" in summary["detail"].lower()

    assert pr._read_cost_per_interaction(ctx) is None
    assert pr._kpi_signals(ctx)["cost_per_interaction_usd"] is None

    findings = _by_id(pr._check_kpi_static(ctx))
    assert findings["KPI-003"].status != "pass"
    assert findings["KPI-003"].status == "should-fix"


def test_target_scope_casefold_match_accepts_cost_evidence_and_kpi_measurement(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    actuals = _actuals()
    actuals["scope"] = {
        "subscription_id": "A0B1C2D3-E4F5-6789-ABCD-EF0123456789",
        "resource_group": "RG-Pilot",
    }
    ctx = _kpi_ctx(
        tmp_path,
        spec_text=RECON_SPEC_TEXT + _SPEC_WITH_BASELINES,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
        actuals=actuals,
    )
    ctx.manifest = {
        "deployment_manifest": {
            "subscription_id": "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
            "resource_group": "rg-pilot",
        }
    }

    summary = pr._cost_evidence_summary(ctx)
    assert summary["status"] == "pass"
    assert summary["actuals_subscription_id"] == "A0B1C2D3-E4F5-6789-ABCD-EF0123456789"
    assert summary["actuals_resource_group"] == "RG-Pilot"
    assert summary["cost_per_successful_interaction_usd"] == 0.1083

    value = pr._read_cost_per_interaction(ctx)
    assert value is not None and abs(value - 0.1083) < 1e-9

    findings = _by_id(pr._check_kpi_static(ctx))
    assert findings["KPI-003"].status == "pass"


def test_forecast_cpi_never_shadows_a_rejected_reconciliation(tmp_path, monkeypatch) -> None:
    """A hash-valid bundle that failed its own gate must not fall back."""
    _freeze(monkeypatch)
    forecast = _forecast()
    forecast["cost_per_interaction_usd"] = 0.002
    ctx = _kpi_ctx(tmp_path, forecast=forecast, maturity="not-verified", status="not-verified")
    assert pr._read_cost_per_interaction(ctx) is None


# ---------------------------------------------------------------------------
# Fail-closed: every gate the loader and the unit-economics block impose
# ---------------------------------------------------------------------------

def test_immature_reconciliation_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, status="not-verified", maturity="not-verified")
    assert pr._read_cost_per_interaction(ctx) is None


def test_top_status_not_verified_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, status="not-verified", maturity="pass")
    assert pr._read_cost_per_interaction(ctx) is None


def test_unit_economics_status_not_verified_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, unit_economics=_unit(status="not-verified"))
    assert pr._read_cost_per_interaction(ctx) is None


def test_unit_economics_target_not_verified_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    """`target_status: not-verified` means the § 14 target was unusable.

    The measurement and the comparison share one evidence gate in the
    reconciler, so a not-verified comparison alongside a `pass` measurement is
    an internally inconsistent artifact — withhold rather than relay it.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, unit_economics=_unit(target_status="not-verified"))
    assert pr._read_cost_per_interaction(ctx) is None


def test_unknown_target_status_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, unit_economics=_unit(target_status="must-fix"))
    assert pr._read_cost_per_interaction(ctx) is None


def test_target_should_fix_still_reports_the_actual_cost(tmp_path, monkeypatch) -> None:
    """The scorecard measures signal presence, not target compliance.

    An observed unit cost over the declared target is still a *measured* unit
    cost. Suppressing it would hide the very number the overspend is about; the
    gap itself is carried by the COST findings, not by KPI-003.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
        # $1080.00 over 1200 successes = $0.90 — an overspend the actuals back.
        actuals=_actuals(period_total_usd=1080.0),
        unit_economics=_unit(
            target_status="should-fix",
            cost_per_successful_interaction_usd=0.9,
            target_usd=0.15,
        ),
    )
    value = pr._read_cost_per_interaction(ctx)
    assert value is not None and abs(value - 0.9) < 1e-9
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status == "pass"


def test_tampered_actuals_digest_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path)
    actuals = _actuals()
    actuals["cost"]["period_total_usd"] = 999.0  # no longer matches actuals_ref
    (tmp_path / "specs" / "cost-actuals-manifest.json").write_text(
        json.dumps(actuals), encoding="utf-8")
    assert pr._read_cost_per_interaction(ctx) is None


def test_spec_edited_after_reconcile_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path)
    (tmp_path / "specs" / "SPEC.md").write_text(
        RECON_SPEC_TEXT + "\nmax_forecast_variance_pct: 0.90\n", encoding="utf-8")
    assert pr._read_cost_per_interaction(ctx) is None


def test_stale_window_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    """Mature on the day it was reconciled, aged out on today's clock."""
    _freeze(monkeypatch, datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc))
    ctx = _kpi_ctx(tmp_path, src_text=_OBS_SRC, evals=_evals_manifest(0.97))
    assert pr._read_cost_per_interaction(ctx) is None
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status == "should-fix"


def test_garbage_reconciliation_has_no_cost_signal(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, reconciliation="{not json")
    assert pr._read_cost_per_interaction(ctx) is None


def test_missing_reconciliation_has_no_cost_signal() -> None:
    assert pr._read_cost_per_interaction(_make_ctx()) is None


# ---------------------------------------------------------------------------
# Numeric domain of the measured unit cost
# ---------------------------------------------------------------------------

def test_zero_cost_with_successes_is_a_valid_measurement(tmp_path, monkeypatch) -> None:
    """$0.00 over real successes is a measurement (free tier), not a gap."""
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
        actuals=_actuals(period_total_usd=0.0),
        unit_economics=_unit(cost_per_successful_interaction_usd=0.0),
    )
    assert pr._read_cost_per_interaction(ctx) == 0.0
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status == "pass"


def test_zero_successes_is_not_a_measurement(tmp_path, monkeypatch) -> None:
    """No successful interaction means nothing was divided by — no unit cost."""
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        unit_economics=_unit(
            successful_interactions=0, cost_per_successful_interaction_usd=0.0),
    )
    assert pr._read_cost_per_interaction(ctx) is None


def test_missing_success_count_is_not_a_measurement(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    block = _unit()
    block.pop("successful_interactions")
    ctx = _kpi_ctx(tmp_path, unit_economics=block)
    assert pr._read_cost_per_interaction(ctx) is None


def test_non_numeric_or_out_of_domain_costs_are_rejected(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for bad in (True, False, float("nan"), float("inf"), float("-inf"),
                -0.01, "0.10", None, [0.1], {"usd": 0.1}, 10 ** 400):
        ctx = _kpi_ctx(
            tmp_path, unit_economics=_unit(cost_per_successful_interaction_usd=bad))
        assert pr._read_cost_per_interaction(ctx) is None, f"accepted {bad!r}"


def test_non_positive_or_non_int_success_counts_are_rejected(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for bad in (True, False, -5, 0, 1.5, "1200", None, float("nan")):
        ctx = _kpi_ctx(tmp_path, unit_economics=_unit(successful_interactions=bad))
        assert pr._read_cost_per_interaction(ctx) is None, f"accepted {bad!r}"


def test_reader_never_raises_on_hostile_shapes(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for block in ([], "pass", 7, {}, {"status": ["pass"]},
                  {"status": "pass", "successful_interactions": {"n": 1}}):
        ctx = _kpi_ctx(tmp_path, unit_economics=block)
        assert pr._read_cost_per_interaction(ctx) is None
        pr._kpi_signals(ctx)          # must not raise
        pr._check_kpi_static(ctx)     # must not raise


# ---------------------------------------------------------------------------
# The unit cost is re-derived from the DIGEST-PINNED actuals
# ---------------------------------------------------------------------------
#
# `unit_economics` is a self-report: the reconciliation states its own unit
# cost and its own success count in the same block, so those two agreeing with
# each other proves nothing. The canonical numbers live in
# `cost-actuals-manifest.json`, which the loader has already pinned by
# canonical-JSON digest — editing it in place invalidates the whole bundle, and
# restating it honestly re-chains the digest and moves the measurement. So the
# unit cost is only reported when it is re-derivable from that document.


def test_relayed_unit_cost_must_match_the_pinned_actuals(tmp_path, monkeypatch) -> None:
    """A reconciliation quoting a unit cost its own actuals deny is withheld.

    $130.00 over 1200 successes is $0.1083. A block claiming $0.05 — internally
    consistent, hash-valid bundle, every reconciler gate `pass` — is a number
    nothing in the evidence supports, so KPI-003 must not relay it.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
        unit_economics=_unit(cost_per_successful_interaction_usd=0.05),
    )
    assert pr._read_cost_per_interaction(ctx) is None
    assert pr._kpi_signals(ctx)["cost_per_interaction_usd"] is None
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status != "pass"
    assert "0.05" not in f["KPI-003"].detail, \
        "a contradicted unit cost must never be relayed into the report"


def test_relayed_success_count_must_match_the_pinned_actuals(
    tmp_path, monkeypatch
) -> None:
    """Halving the divisor doubles the unit cost — both self-reported.

    $130.00 over *600* successes really is $0.2167, so this block is perfectly
    self-consistent. The actuals observed 1200, and that is the count the
    reconciler divided by, so the pair is still a fabrication.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        unit_economics=_unit(
            successful_interactions=600,
            cost_per_successful_interaction_usd=0.2167,
        ),
    )
    assert pr._read_cost_per_interaction(ctx) is None


def test_restated_actual_total_moves_the_measurement(tmp_path, monkeypatch) -> None:
    """Re-chaining the actuals digest is honest — and changes the answer."""
    _freeze(monkeypatch)
    # Same reconciliation numbers, actuals restated to $260.00: 260/1200 is
    # $0.2167, so the relayed $0.1083 no longer holds.
    stale_claim = _kpi_ctx(tmp_path / "stale-claim", actuals=_actuals(period_total_usd=260.0))
    assert pr._read_cost_per_interaction(stale_claim) is None
    # Restated end-to-end, the new unit cost is measured again.
    restated = _kpi_ctx(
        tmp_path / "restated",
        actuals=_actuals(period_total_usd=260.0),
        unit_economics=_unit(cost_per_successful_interaction_usd=0.2167),
    )
    value = pr._read_cost_per_interaction(restated)
    assert value is not None and abs(value - 0.2167) < 1e-9


def test_in_place_actuals_edit_is_rejected_by_the_loader(tmp_path, monkeypatch) -> None:
    """The cheap attack — edit the actuals to fit the claim — never loads."""
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, unit_economics=_unit(cost_per_successful_interaction_usd=0.05))
    actuals = _actuals()
    actuals["cost"]["period_total_usd"] = 60.0  # 60/1200 = 0.05 — but unhashed
    (tmp_path / "specs" / "cost-actuals-manifest.json").write_text(
        json.dumps(actuals), encoding="utf-8")
    assert pr._read_cost_reconciliation(ctx) is None, "digest must reject the edit"
    assert pr._read_cost_per_interaction(ctx) is None


def test_unusable_actual_total_is_not_a_measurement(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for i, bad in enumerate(
            (None, "130.00", float("nan"), float("inf"), -1.0, True, 10 ** 400)):
        ctx = _kpi_ctx(tmp_path / f"total-{i}", actuals=_actuals(period_total_usd=bad))
        assert pr._read_cost_per_interaction(ctx) is None, f"accepted total {bad!r}"


def test_unobserved_interaction_count_is_not_a_measurement(
    tmp_path, monkeypatch
) -> None:
    """`usage.interaction_status` is the actuals' own gate on the divisor."""
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, actuals=_actuals(interaction_status="not-verified"))
    assert pr._read_cost_per_interaction(ctx) is None


def test_unusable_actual_success_count_is_not_a_measurement(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    for i, bad in enumerate((None, 0, -5, 1.5, "1200", True, {"n": 1200})):
        ctx = _kpi_ctx(tmp_path / f"count-{i}",
                       actuals=_actuals(successful_interactions=bad))
        assert pr._read_cost_per_interaction(ctx) is None, f"accepted count {bad!r}"


def test_recompute_rounds_half_up_like_the_reconciler(tmp_path, monkeypatch) -> None:
    """$0.25 over 1000 successes is exactly $0.00025 — a rounding tie.

    `reconcile._rate` quantizes to 4 dp with ROUND_HALF_UP, so the reconciler
    publishes $0.0003. Banker's rounding would publish $0.0002; accepting that
    here would mean the recompute silently disagreed with the producer on every
    tie. Both directions are pinned.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path / "half-up",
        actuals=_actuals(period_total_usd=0.25, successful_interactions=1000),
        unit_economics=_unit(
            successful_interactions=1000, cost_per_successful_interaction_usd=0.0003),
    )
    value = pr._read_cost_per_interaction(ctx)
    assert value is not None and abs(value - 0.0003) < 1e-12

    half_even = _kpi_ctx(
        tmp_path / "half-even",
        actuals=_actuals(period_total_usd=0.25, successful_interactions=1000),
        unit_economics=_unit(
            successful_interactions=1000, cost_per_successful_interaction_usd=0.0002),
    )
    assert pr._read_cost_per_interaction(half_even) is None


def test_last_place_rounding_difference_is_not_a_contradiction(
    tmp_path, monkeypatch
) -> None:
    """$130.00/1200 is $0.108333…; $0.10833 is the same measurement.

    The gate exists to catch fabricated numbers, not to re-litigate the last
    published digit — anything inside half of the 4-dp step is accepted.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, unit_economics=_unit(
        cost_per_successful_interaction_usd=0.10833))
    value = pr._read_cost_per_interaction(ctx)
    assert value is not None and abs(value - 0.10833) < 1e-12


def test_sub_cent_actual_total_matches_the_reconciler(tmp_path, monkeypatch) -> None:
    """The reconciler normalizes the period total to cents before dividing.

    `reconcile` quantizes `period_total_usd` to CENT precision, so $10.005 over
    a single success is published as $10.0100. Re-deriving only from the raw
    total would call the producer's own arithmetic a contradiction.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        actuals=_actuals(period_total_usd=10.005, successful_interactions=1),
        unit_economics=_unit(
            successful_interactions=1, cost_per_successful_interaction_usd=10.01),
    )
    value = pr._read_cost_per_interaction(ctx)
    assert value is not None and abs(value - 10.01) < 1e-9


def test_sub_cent_division_accepts_only_the_producer_formula(
    tmp_path, monkeypatch
) -> None:
    """$0.005 over 2 successes: cent-normalize first, *then* divide.

    Cent-normalizing $0.005 (ROUND_HALF_UP, 2 dp) gives $0.01; dividing that by
    2 successes and quantizing to 4 dp gives $0.0050 — the only value
    `reconcile` can ever publish for this input. Dividing the raw,
    un-quantized $0.005 by 2 gives $0.0025, which is arithmetically exact but
    not a formula the reconciler runs, so it must be rejected rather than
    accepted as an alternate reading of the same evidence.
    """
    _freeze(monkeypatch)
    actuals = _actuals(period_total_usd=0.005, successful_interactions=2)

    expected = pr._expected_unit_cost(actuals, 2)
    assert expected is not None
    assert expected == Decimal("0.0050")

    accepted = _kpi_ctx(
        tmp_path / "accepted",
        actuals=actuals,
        unit_economics=_unit(
            successful_interactions=2, cost_per_successful_interaction_usd=0.005),
    )
    value = pr._read_cost_per_interaction(accepted)
    assert value is not None and abs(value - 0.005) < 1e-9

    rejected = _kpi_ctx(
        tmp_path / "rejected",
        actuals=actuals,
        unit_economics=_unit(
            successful_interactions=2, cost_per_successful_interaction_usd=0.0025),
    )
    assert pr._read_cost_per_interaction(rejected) is None


def test_contradiction_warns_without_dumping_the_artifact(
    tmp_path, monkeypatch, capsys
) -> None:
    """A withheld number must be explained — briefly, and on stderr."""
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, unit_economics=_unit(
        cost_per_successful_interaction_usd=0.05))
    assert pr._read_cost_per_interaction(ctx) is None
    err = capsys.readouterr().err
    assert "[warn]" in err
    assert len(err) < 500, "the warning must be bounded, not an artifact dump"


# ---------------------------------------------------------------------------
# _kpi_signals join helper
# ---------------------------------------------------------------------------

def test_kpi_signals_join_values(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        spec_text=RECON_SPEC_TEXT + _SPEC_WITH_BASELINES,
        bicep_text=_BICEP_WITH_KPI_ALERT,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
    )
    sig = pr._kpi_signals(ctx)
    assert sig["latency_declared"] is True
    assert sig["cost_per_interaction_declared"] is True
    assert sig["success_rate_declared"] is True
    assert sig["deviation_alert_present"] is True
    assert sig["traces_emit"] is True
    assert abs(sig["eval_pass_rate"] - 0.97) < 1e-9
    assert abs(sig["cost_per_interaction_usd"] - 0.1083) < 1e-9


def test_kpi_signals_never_raises_on_garbage() -> None:
    ctx = _make_ctx(manifests={
        "evals-manifest.json": "{not json",
        "cost-manifest.json": "[]",  # wrong shape
        "cost-reconciliation-manifest.json": "[]",
        "cost-actuals-manifest.json": "null",
    })
    sig = pr._kpi_signals(ctx)  # must not raise
    assert sig["eval_pass_rate"] is None
    assert sig["cost_per_interaction_usd"] is None


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def test_report_renders_outcome_kpi_section(tmp_path, monkeypatch) -> None:
    """Render the section from a REAL joined scorecard, not a hand-built dict.

    A literal scorecard here would keep rendering happily after
    `_kpi_signals` stopped producing that shape (or that number). This drives
    the section from the same bundle the assessment does, so the value in the
    report is the value the loader actually re-derived from the pinned actuals.
    """
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        spec_text=RECON_SPEC_TEXT + _SPEC_WITH_BASELINES,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
    )
    scorecard = pr._kpi_signals(ctx)
    assert abs(scorecard["cost_per_interaction_usd"] - 0.1083) < 1e-9
    manifest = {
        "checked_at": "2025-01-01T00:00:00+00:00",
        "mode": "static",
        "agt_profile": "none",
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "include_experimental": False,
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "verification_debt": {"total": 0, "by_pillar": {}},
        "score": {"raw_percent": 100, "with_waivers_percent": 100},
        "permission_tiers": {"0": True},
        "warnings": [],
        "safe_check_reference": {},
        "pillars": [],
        "evidence_register": [],
        "evidence_freshness": {},
        "waivers": [],
        "not_verified_count": 0,
        "kpi_scorecard": scorecard,
    }
    md = pr._render_report(manifest, {"declared": "x", "detected": None, "resolved": "x"},
                           {}, [], {}, [])
    assert "Outcome KPI scorecard" in md
    assert "Joins the three outcome signals a production review needs (eval quality + measured unit cost + live telemetry):" in md
    assert "CAF asks teams to measure" not in md
    # joined values must surface
    assert "97" in md            # pass-rate %
    assert "0.1083" in md        # measured cost per successful interaction
    # the source row names the reconciled actuals, never the forecast
    assert "| Actual cost / successful interaction |" in md
    assert "`specs/cost-reconciliation-manifest.json` (threadlight-consumption-iq actuals)" in md
    kpi_section = md.split("## 8. Outcome KPI scorecard", 1)[1].split("\n## ", 1)[0]
    assert "specs/cost-manifest.json" not in kpi_section, \
        "the KPI scorecard must not claim the forecast manifest as its cost source"


def test_report_renders_a_withheld_unit_cost_as_not_verified(
    tmp_path, monkeypatch
) -> None:
    """A contradicted unit cost leaves a gap in the report, never a number."""
    _freeze(monkeypatch)
    ctx = _kpi_ctx(tmp_path, src_text=_OBS_SRC, evals=_evals_manifest(0.97),
                   unit_economics=_unit(cost_per_successful_interaction_usd=0.05))
    scorecard = pr._kpi_signals(ctx)
    assert scorecard["cost_per_interaction_usd"] is None
    manifest = {
        "checked_at": "2025-01-01T00:00:00+00:00", "mode": "static",
        "agt_profile": "none", "go_live_recommendation": "ready",
        "would_fail_hard_gate": False, "include_experimental": False,
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "verification_debt": {"total": 0, "by_pillar": {}},
        "score": {"raw_percent": 100, "with_waivers_percent": 100},
        "permission_tiers": {"0": True}, "warnings": [], "safe_check_reference": {},
        "pillars": [], "evidence_register": [], "evidence_freshness": {},
        "waivers": [], "not_verified_count": 0, "kpi_scorecard": scorecard,
    }
    md = pr._render_report(manifest, {"declared": "x", "detected": None, "resolved": "x"},
                           {}, [], {}, [])
    kpi_section = md.split("## 8. Outcome KPI scorecard", 1)[1].split("\n## ", 1)[0]
    assert "0.05" not in kpi_section
    assert "not-verified" in kpi_section


def test_report_renders_cost_evidence_summary_fields(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        spec_text=RECON_SPEC_TEXT + _SPEC_WITH_BASELINES,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
    )
    ctx.manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-pilot",
        }
    }
    assert hasattr(pr, "_cost_evidence_summary"), (
        "production_ready must publish a cost_evidence summary helper")
    manifest = {
        "checked_at": "2025-01-01T00:00:00+00:00",
        "mode": "static",
        "agt_profile": "none",
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "include_experimental": False,
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "verification_debt": {"total": 0, "by_pillar": {}},
        "score": {"raw_percent": 100, "with_waivers_percent": 100},
        "permission_tiers": {"0": True},
        "warnings": [],
        "safe_check_reference": {},
        "pillars": [],
        "evidence_register": [],
        "evidence_freshness": {},
        "waivers": [],
        "not_verified_count": 0,
        "kpi_scorecard": pr._kpi_signals(ctx),
        "cost_evidence": pr._cost_evidence_summary(ctx),
    }
    md = pr._render_report(
        manifest, {"declared": "x", "detected": None, "resolved": "x"}, {}, [], {}, []
    )
    cost_section = md.split("## 7. Cost projection", 1)[1].split("\n## 8. ", 1)[0]
    for expected in (
        "- Forecast manifest: `specs/cost-manifest.json`.",
        "- Reconciled actuals bundle: `specs/cost-reconciliation-manifest.json` + `specs/cost-actuals-manifest.json`.",
        "- Actuals window: `2026-08-01T00:00:00Z → 2026-08-08T00:00:00Z`.",
        "- Actuals scope: subscription `sub-1`, resource group `rg-pilot`.",
        "- Window totals vs forecast: actual `$130.00` vs forecast `$120.00` (`+12.0%` variance).",
        "- Coverage: projection attribution `100.0%`; source resource IDs `100.0%`.",
        "- Unallocated actual cost: `$12.30`.",
        "- Measured cost / successful interaction: `$0.1083`.",
    ):
        assert expected in cost_section


def test_report_cost_section_is_forecast_only_when_target_scope_is_unknown(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _kpi_ctx(
        tmp_path,
        spec_text=RECON_SPEC_TEXT + _SPEC_WITH_BASELINES,
        src_text=_OBS_SRC,
        evals=_evals_manifest(0.97),
    )
    ctx.manifest = {"deployment_manifest": {}}
    manifest = {
        "checked_at": "2025-01-01T00:00:00+00:00",
        "mode": "static",
        "agt_profile": "none",
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "include_experimental": False,
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "verification_debt": {"total": 0, "by_pillar": {}},
        "score": {"raw_percent": 100, "with_waivers_percent": 100},
        "permission_tiers": {"0": True},
        "warnings": [],
        "safe_check_reference": {},
        "pillars": [],
        "evidence_register": [],
        "evidence_freshness": {},
        "waivers": [],
        "not_verified_count": 0,
        "kpi_scorecard": pr._kpi_signals(ctx),
        "cost_evidence": pr._cost_evidence_summary(ctx),
    }
    md = pr._render_report(
        manifest, {"declared": "x", "detected": None, "resolved": "x"}, {}, [], {}, []
    )
    cost_section = md.split("## 7. Cost projection", 1)[1].split("\n## 8. ", 1)[0]
    assert "- Forecast-only / actuals not verified:" in cost_section
    assert "Actuals scope:" not in cost_section
    assert "Window totals vs forecast:" not in cost_section
    assert "Measured cost / successful interaction" not in cost_section


def test_report_cost_section_calls_out_forecast_only_when_actuals_are_not_verified(
) -> None:
    manifest = {
        "checked_at": "2025-01-01T00:00:00+00:00",
        "mode": "static",
        "agt_profile": "none",
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "include_experimental": False,
        "verification_coverage": {"verified": 1, "total_scoreable": 1, "percent": 100},
        "verification_debt": {"total": 0, "by_pillar": {}},
        "score": {"raw_percent": 100, "with_waivers_percent": 100},
        "permission_tiers": {"0": True},
        "warnings": [],
        "safe_check_reference": {},
        "pillars": [],
        "evidence_register": [],
        "evidence_freshness": {},
        "waivers": [],
        "not_verified_count": 0,
        "kpi_scorecard": {},
        "cost_evidence": {
            "status": "not-verified",
            "detail": "scope mismatch",
            "source_paths": {
                "forecast": "specs/cost-manifest.json",
                "actuals": "specs/cost-actuals-manifest.json",
                "reconciliation": "specs/cost-reconciliation-manifest.json",
            },
        },
    }
    md = pr._render_report(
        manifest, {"declared": "x", "detected": None, "resolved": "x"}, {}, [], {}, []
    )
    cost_section = md.split("## 7. Cost projection", 1)[1].split("\n## 8. ", 1)[0]
    assert "- Forecast manifest: `specs/cost-manifest.json`." in cost_section
    assert "- Reconciled actuals bundle: `specs/cost-reconciliation-manifest.json` + `specs/cost-actuals-manifest.json`." in cost_section
    assert "Forecast-only / actuals not verified: scope mismatch." in cost_section
    assert "Measured cost / successful interaction" not in cost_section


# ---------------------------------------------------------------------------
# Committed exemplar pairing — KPI-003 wording must not drift
# ---------------------------------------------------------------------------

# Every tracked (non-gitignored) manifest/report pair this repo ships as a
# worked example. `sample-pilot`'s own pair is excluded on purpose: its
# `.gitignore` marks it regenerate-on-run, so a clean checkout never has it on
# disk and asserting on it here would make the suite depend on local state.
_KPI003_FIXTURE_PAIRS = [
    (REPO_ROOT / "examples" / "returns-triage-governed" / "specs" / "production-readiness.json",
     REPO_ROOT / "examples" / "returns-triage-governed" / "docs" / "production-readiness-report.md"),
    (SKILL_DIR / "references" / "fixtures" / "sample-pilot-broken" / "tests"
     / "production-readiness-manifest.json",
     SKILL_DIR / "references" / "fixtures" / "sample-pilot-broken" / "docs"
     / "production-readiness-report.md"),
    (SKILL_DIR / "references" / "fixtures" / "sample-pilot-citadel" / "tests"
     / "production-readiness-manifest.json",
     SKILL_DIR / "references" / "fixtures" / "sample-pilot-citadel" / "docs"
     / "production-readiness-report.md"),
]

# `| \`KPI-003\` | <severity> | <status> | <detail> |` — the pillar findings table.
_KPI003_ROW_RE = re.compile(
    r"^\|\s*`KPI-003`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$")
# `N. **KPI-003** — <title>. See: ...` — the not-yet-passing recipe gap list.
_KPI003_LIST_RE = re.compile(
    r"^\d+\.\s+\*\*KPI-003\*\*\s+—\s+(.+?)\.\s+See:\s+.+$")


def _kpi003_finding_from_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for pillar in data.get("pillars", []):
        for finding in pillar.get("findings", []):
            if finding.get("id") == "KPI-003":
                return finding
    raise AssertionError(f"no KPI-003 finding in {path}")


def _kpi003_report_lines(path: Path) -> tuple[str | None, str | None]:
    """Return the report's (title, detail) as stated for KPI-003, if present."""
    row_detail: str | None = None
    list_title: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        row = _KPI003_ROW_RE.match(line)
        if row:
            row_detail = row.group(3)
        item = _KPI003_LIST_RE.match(line)
        if item:
            list_title = item.group(1)
    return list_title, row_detail


def test_committed_kpi003_fixtures_match_current_wording_and_their_own_report() -> None:
    """Committed manifest/report pairs must state KPI-003 the same way `pr` does.

    `KPI-003`'s title is a fixed string in `FINDING_CATALOG`; a manifest whose
    title has drifted from it — or from the title/detail its own paired
    markdown report shows for the same finding — would be teaching a reviewer
    the wrong contract for the outcome-KPI scorecard. This is what would have
    caught the exemplars going stale after the unit-cost wording changed
    without failing any other test.
    """
    checked = 0
    for manifest_path, report_path in _KPI003_FIXTURE_PAIRS:
        if not manifest_path.exists() or not report_path.exists():
            continue
        checked += 1
        finding = _kpi003_finding_from_manifest(manifest_path)
        assert finding["title"] == pr.FINDING_CATALOG["KPI-003"]["title"], (
            f"{manifest_path} KPI-003 title drifted from FINDING_CATALOG: "
            f"{finding['title']!r}")

        report_title, report_detail = _kpi003_report_lines(report_path)
        assert report_title is not None, f"no KPI-003 gap-list entry in {report_path}"
        assert report_detail is not None, f"no KPI-003 findings-table row in {report_path}"
        assert report_title == finding["title"], (
            f"{report_path} KPI-003 title does not match its manifest "
            f"{manifest_path}")
        assert report_detail == finding["detail"], (
            f"{report_path} KPI-003 detail does not match its manifest "
            f"{manifest_path}")
    assert checked >= 2, "expected at least the broken/citadel exemplars to be checked"


if __name__ == "__main__":
    # Fixture-taking tests are pytest-only; this fallback runs the rest.
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)
           and not inspect.signature(v).parameters]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed (pytest runs the fixture-based tests)")
    sys.exit(1 if failures else 0)
