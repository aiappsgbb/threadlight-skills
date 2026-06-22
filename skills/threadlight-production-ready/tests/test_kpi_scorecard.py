#!/usr/bin/env python3
"""Tests for the outcome-KPI scorecard (F7).

CAF's agent observability triad puts *baselines* (latency, cost-per-interaction,
success-rate) and *deviation alerts* under observability. This leg joins three
already-collected signals into one measurable outcome view:

  * eval pass-rate          (specs/evals-manifest.json — threadlight-evals)
  * cost-per-interaction    (specs/cost-manifest.json   — threadlight-consumption-iq)
  * traces emitting         (foundry-observability wiring in infra/src)

It emits three should-fix, tier-0 findings under the observability pillar
(KPI-001 baselines declared, KPI-002 deviation alert wired, KPI-003 scorecard
joinable) and renders an "Outcome KPI scorecard" section in the report.

Contract pinned here:
  * KPI findings are should-fix / tier-0 (never must-fix) — so they never force
    a recipe (test_recipe_catalog) and never flip a green pilot red on must-fix.
  * `_check_kpi_static` and `_kpi_signals` never raise (missing / garbage inputs
    degrade to gaps, not crashes).
  * Backward-compat: an empty repo still produces KPI-001..003 as gaps, never
    `pass`, never `must-fix`.

pytest-style (bare ``test_`` functions + ``assert``); stdlib only.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402


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
    m = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "recommendations": [],
    }
    if cpi is not None:
        m["cost_per_interaction_usd"] = cpi
    return m


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


def test_joined_scorecard_passes_kpi003() -> None:
    ctx = _make_ctx(
        src_text=_OBS_SRC,
        manifests={
            "evals-manifest.json": _evals_manifest(0.97),
            "cost-manifest.json": _cost_manifest(0.012),
        },
    )
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status == "pass", \
        f"KPI-003 expected pass when all three signals present, got {f['KPI-003'].status}: {f['KPI-003'].detail}"


def test_partial_scorecard_is_should_fix() -> None:
    # only cost present
    ctx = _make_ctx(manifests={"cost-manifest.json": _cost_manifest(0.012)})
    f = _by_id(pr._check_kpi_static(ctx))
    assert f["KPI-003"].status == "should-fix"


def test_no_signals_kpi003_not_verified() -> None:
    f = _by_id(pr._check_kpi_static(_make_ctx()))
    assert f["KPI-003"].status == "not-verified"


# ---------------------------------------------------------------------------
# _kpi_signals join helper
# ---------------------------------------------------------------------------

def test_kpi_signals_join_values() -> None:
    ctx = _make_ctx(
        spec_text=_SPEC_WITH_BASELINES,
        bicep_text=_BICEP_WITH_KPI_ALERT,
        src_text=_OBS_SRC,
        manifests={
            "evals-manifest.json": _evals_manifest(0.97),
            "cost-manifest.json": _cost_manifest(0.012),
        },
    )
    sig = pr._kpi_signals(ctx)
    assert sig["latency_declared"] is True
    assert sig["cost_per_interaction_declared"] is True
    assert sig["success_rate_declared"] is True
    assert sig["deviation_alert_present"] is True
    assert sig["traces_emit"] is True
    assert abs(sig["eval_pass_rate"] - 0.97) < 1e-9
    assert abs(sig["cost_per_interaction_usd"] - 0.012) < 1e-9


def test_kpi_signals_never_raises_on_garbage() -> None:
    ctx = _make_ctx(manifests={
        "evals-manifest.json": "{not json",
        "cost-manifest.json": "[]",  # wrong shape
    })
    sig = pr._kpi_signals(ctx)  # must not raise
    assert sig["eval_pass_rate"] is None
    assert sig["cost_per_interaction_usd"] is None


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def test_report_renders_outcome_kpi_section() -> None:
    ctx = _make_ctx(
        spec_text=_SPEC_WITH_BASELINES,
        src_text=_OBS_SRC,
        manifests={
            "evals-manifest.json": _evals_manifest(0.97),
            "cost-manifest.json": _cost_manifest(0.012),
        },
    )
    scorecard = pr._kpi_signals(ctx)
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
    assert "97" in md          # pass-rate %
    assert "0.012" in md       # cost-per-interaction


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    sys.exit(1 if failures else 0)
