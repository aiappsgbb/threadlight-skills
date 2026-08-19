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
use (`_read_cost_reconciliation`): schemas, canonical-JSON digests of the
forecast and actuals, the raw `specs/SPEC.md` anchor, verdict-after-evidence
timestamps and today's staleness re-check. `specs/cost-manifest.json` is a
projection — a cost-per-interaction number in it is a *plan*, so it can never
satisfy KPI-003 on its own. (COST-005/006/007 keep consuming that forecast; this
leg does not.)

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
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
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
        manifest={},
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

def test_report_renders_outcome_kpi_section() -> None:
    scorecard = {
        "latency_declared": True,
        "cost_per_interaction_declared": True,
        "success_rate_declared": True,
        "deviation_alert_present": False,
        "traces_emit": True,
        "eval_pass_rate": 0.97,
        "cost_per_interaction_usd": 0.1083,
    }
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
    # joined values must surface
    assert "97" in md            # pass-rate %
    assert "0.1083" in md        # measured cost per successful interaction
    # the source row names the reconciled actuals, never the forecast
    assert "| Cost per successful interaction |" in md
    assert "`specs/cost-reconciliation-manifest.json` (threadlight-consumption-iq actuals)" in md
    kpi_section = md.split("## 8. Outcome KPI scorecard", 1)[1].split("\n## ", 1)[0]
    assert "specs/cost-manifest.json" not in kpi_section, \
        "the KPI scorecard must not claim the forecast manifest as its cost source"


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
