"""Tests for COST-007 — vNext cost-manifest meter coverage.

Three outcomes:
  * any certain detected resource/meter line pricing_status=not-priceable → must-fix
  * meter_coverage.status=not-verified (nothing not-priceable)            → not-verified
  * coverage complete and all lines priced                                → pass

COST-005/006 thresholds and outcomes must be unaffected by COST-007.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"

_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("production_ready", mod)
_spec.loader.exec_module(mod)


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_ctx(manifest: dict | None) -> "mod.RepoContext":  # type: ignore[name-defined]
    tmpdir = Path(tempfile.mkdtemp())
    docs = tmpdir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "cost-projection.md").write_text("# Cost Projection\n", encoding="utf-8")
    if manifest is not None:
        specs = tmpdir / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        (specs / "cost-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bg = mod.BicepGraph(resources=[], source_files=[])
    return mod.RepoContext(
        root=tmpdir, bicep_files=[], src_files=[], test_files=[], spec_text="",
        spec_12={}, spec_11b={}, azure_yaml_text="", docs_text="", azd_env={},
        manifest={}, bicep_text="", src_text="", bicep_graph=bg,
    )


def _findings_by_id(ctx):
    return {f.id: f for f in mod._check_cost_static(ctx)}


def _fresh_ts() -> str:
    return _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))


def _vnext(meter_lines, resource_lines=None, coverage_status="complete"):
    return {
        "schema_version": "2.0",
        "generated_at": _fresh_ts(),
        "status": "complete",
        "resources": resource_lines or [],
        "meters": meter_lines,
        "recommendations": [],
        "meter_coverage": {"status": coverage_status},
        "totals": {"complete": True, "cost_per_transaction_usd": 1.0, "monthly_cost_current_usd": 100.0},
    }


def test_cost007_must_fix_when_line_not_priceable():
    manifest = _vnext(
        meter_lines=[
            {"meter_kind": "web-grounding", "pricing_status": "not-priceable", "monthly_cost_usd": None},
        ],
    )
    f = _findings_by_id(_make_ctx(manifest))["COST-007"]
    assert f.status == "must-fix", f"got {f.status!r}: {f.detail}"
    assert "web-grounding" in f.detail


def test_cost007_must_fix_when_resource_not_priceable():
    manifest = _vnext(
        meter_lines=[{"meter_kind": "embeddings", "pricing_status": "priced", "monthly_cost_usd": 1.0}],
        resource_lines=[{"resource_kind": "X", "logical_name": "aoai", "pricing_status": "not-priceable", "monthly_cost_usd": None}],
    )
    f = _findings_by_id(_make_ctx(manifest))["COST-007"]
    assert f.status == "must-fix", f"got {f.status!r}: {f.detail}"


def test_cost007_not_verified_when_coverage_not_verified():
    manifest = _vnext(
        meter_lines=[{"meter_kind": "speech", "pricing_status": "priced", "monthly_cost_usd": None, "verified": False}],
        coverage_status="not-verified",
    )
    f = _findings_by_id(_make_ctx(manifest))["COST-007"]
    assert f.status == "not-verified", f"got {f.status!r}: {f.detail}"


def test_cost007_pass_when_complete_and_priced():
    manifest = _vnext(
        meter_lines=[
            {"meter_kind": "embeddings", "pricing_status": "priced", "monthly_cost_usd": 1.0},
            {"meter_kind": "content-understanding-extraction", "pricing_status": "priced", "monthly_cost_usd": 2.0},
        ],
        coverage_status="complete",
    )
    f = _findings_by_id(_make_ctx(manifest))["COST-007"]
    assert f.status == "pass", f"got {f.status!r}: {f.detail}"


def test_cost007_not_verified_when_no_meter_coverage_key():
    # v1 manifest (no meter_coverage) → not-verified, and COST-005/006 unaffected.
    manifest = {"schema_version": "1.0", "generated_at": _fresh_ts(), "recommendations": []}
    findings = _findings_by_id(_make_ctx(manifest))
    assert findings["COST-007"].status == "not-verified"
    # COST-005 still passes (fresh v1 manifest), COST-006 passes (empty recs)
    assert findings["COST-005"].status == "pass"
    assert findings["COST-006"].status == "pass"


def test_cost007_must_fix_takes_precedence_over_not_verified():
    manifest = _vnext(
        meter_lines=[{"meter_kind": "web-grounding", "pricing_status": "not-priceable", "monthly_cost_usd": None}],
        coverage_status="not-verified",
    )
    f = _findings_by_id(_make_ctx(manifest))["COST-007"]
    assert f.status == "must-fix"


def test_cost007_in_catalog_and_cost_pillar():
    assert "COST-007" in mod.FINDING_CATALOG
    assert mod.FINDING_CATALOG["COST-007"]["pillar"] == "cost"


def test_cost005_006_thresholds_unchanged_by_cost007():
    # A vNext manifest with a >$100 recommendation still drives COST-006 must-fix.
    manifest = _vnext(
        meter_lines=[{"meter_kind": "embeddings", "pricing_status": "priced", "monthly_cost_usd": 1.0}],
    )
    manifest["recommendations"] = [{"logical_name": "gpt4o", "monthly_savings_usd": 260.0}]
    findings = _findings_by_id(_make_ctx(manifest))
    assert findings["COST-006"].status == "must-fix"
    assert findings["COST-007"].status == "pass"
