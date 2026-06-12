"""Tests for COST-005 (tightened) and COST-006 (new) cost-pillar findings.

COST-005 now requires ALL of:
  - docs/cost-projection.md exists
  - specs/cost-manifest.json exists with schema_version >= "1.0"
  - cost-manifest.json.generated_at within 30 days of AZURE_LAST_DEPLOY_AT
    (or current time when env var absent)

COST-006 walks recommendations[] in cost-manifest.json:
  - monthly_savings_usd > 100 → must-fix
  - monthly_savings_usd > 25  → should-fix
  - ≤ 25                      → pass
  - manifest missing / recommendations key absent → not-verified
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_ctx(
    *,
    has_projection_md: bool = True,
    manifest_exists: bool = True,
    schema_version: str = "1.0",
    generated_at: str | None = None,
    azure_last_deploy_at: str | None = None,
    recommendations: list | None = None,
) -> "mod.RepoContext":  # type: ignore[name-defined]
    """Create a minimal RepoContext backed by a temporary directory."""
    tmpdir = Path(tempfile.mkdtemp())

    # docs/cost-projection.md
    if has_projection_md:
        docs = tmpdir / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "cost-projection.md").write_text("# Cost Projection\n", encoding="utf-8")

    # specs/cost-manifest.json
    if manifest_exists:
        specs = tmpdir / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        manifest: dict = {"schema_version": schema_version}
        if generated_at:
            manifest["generated_at"] = generated_at
        if recommendations is not None:
            manifest["recommendations"] = recommendations
        (specs / "cost-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # azd env — write AZURE_LAST_DEPLOY_AT if requested
    azd_env: dict[str, str] = {}
    if azure_last_deploy_at:
        azd_env["AZURE_LAST_DEPLOY_AT"] = azure_last_deploy_at

    # Minimal BicepGraph
    bg = mod.BicepGraph(resources=[], source_files=[])

    return mod.RepoContext(
        root=tmpdir,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text="",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env=azd_env,
        manifest={},
        bicep_text="",
        src_text="",
        bicep_graph=bg,
    )


def _findings_by_id(ctx) -> dict[str, "mod.Finding"]:  # type: ignore[name-defined]
    findings = mod._check_cost_static(ctx)
    return {f.id: f for f in findings}


# ---------------------------------------------------------------------------
# COST-005 tests
# ---------------------------------------------------------------------------


def test_cost005_fail_only_markdown_no_manifest():
    """COST-005 should-fix when docs/cost-projection.md exists but specs/cost-manifest.json is absent."""
    ctx = _make_ctx(has_projection_md=True, manifest_exists=False)
    f = _findings_by_id(ctx)["COST-005"]
    assert f.status == "should-fix", f"expected should-fix, got {f.status!r}: {f.detail}"


def test_cost005_fail_manifest_older_than_30_days():
    """COST-005 should-fix when manifest generated_at is > 30 days before reference time."""
    old_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=40))
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=old_ts,
        # No AZURE_LAST_DEPLOY_AT → reference is current time
    )
    f = _findings_by_id(ctx)["COST-005"]
    assert f.status == "should-fix", f"expected should-fix (stale manifest), got {f.status!r}: {f.detail}"


def test_cost005_pass_both_present_and_fresh():
    """COST-005 pass when docs/cost-projection.md + fresh specs/cost-manifest.json both exist."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
    )
    f = _findings_by_id(ctx)["COST-005"]
    assert f.status == "pass", f"expected pass, got {f.status!r}: {f.detail}"


def test_cost005_fail_missing_markdown():
    """COST-005 should-fix when docs/cost-projection.md is absent (even with valid manifest)."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    ctx = _make_ctx(
        has_projection_md=False,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
    )
    f = _findings_by_id(ctx)["COST-005"]
    assert f.status == "should-fix", f"expected should-fix, got {f.status!r}: {f.detail}"


def test_cost005_fail_schema_version_below_1():
    """COST-005 should-fix when schema_version < '1.0'."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="0.9",
        generated_at=recent_ts,
    )
    f = _findings_by_id(ctx)["COST-005"]
    assert f.status == "should-fix", f"expected should-fix (old schema), got {f.status!r}: {f.detail}"


def test_cost005_pass_with_azure_last_deploy_at():
    """COST-005 pass when manifest generated_at > AZURE_LAST_DEPLOY_AT (and within 30 days)."""
    deploy_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=5))
    # NOTE: generated_at is 3 days before now, 2 days AFTER deploy → fresh
    generated_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=3))
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=generated_ts,
        azure_last_deploy_at=deploy_ts,
    )
    f = _findings_by_id(ctx)["COST-005"]
    assert f.status == "pass", f"expected pass, got {f.status!r}: {f.detail}"


# ---------------------------------------------------------------------------
# COST-006 tests
# ---------------------------------------------------------------------------


def test_cost006_not_verified_when_manifest_missing():
    """COST-006 not-verified when specs/cost-manifest.json does not exist."""
    ctx = _make_ctx(manifest_exists=False)
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "not-verified", f"expected not-verified, got {f.status!r}: {f.detail}"
    assert "threadlight-consumption-iq" in f.detail.lower() or "cost-manifest" in f.detail.lower()


def test_cost006_must_fix_when_savings_over_100():
    """COST-006 must-fix when at least one recommendation has monthly_savings_usd > 100."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    recs = [
        {
            "logical_name": "gpt4o",
            "resource_id": "/subscriptions/sub/resourceGroups/rg/providers/foo",
            "monthly_savings_usd": 260.50,
        }
    ]
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
        recommendations=recs,
    )
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "must-fix", f"expected must-fix, got {f.status!r}: {f.detail}"
    assert "gpt4o" in f.detail or "260" in f.detail


def test_cost006_should_fix_when_savings_between_25_and_100():
    """COST-006 should-fix when recommendations have savings > 25 but <= 100."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    recs = [
        {
            "logical_name": "cosmos-db",
            "monthly_savings_usd": 60.0,
        }
    ]
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
        recommendations=recs,
    )
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "should-fix", f"expected should-fix, got {f.status!r}: {f.detail}"


def test_cost006_pass_when_all_savings_low():
    """COST-006 pass when all recommendations have monthly_savings_usd <= 25."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    recs = [
        {
            "logical_name": "storage-acct",
            "monthly_savings_usd": 10.0,
        }
    ]
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
        recommendations=recs,
    )
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "pass", f"expected pass (low savings), got {f.status!r}: {f.detail}"


def test_cost006_pass_when_recommendations_empty():
    """COST-006 pass when recommendations list is present but empty."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
        recommendations=[],
    )
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "pass", f"expected pass (no recs), got {f.status!r}: {f.detail}"


def test_cost006_not_verified_when_recommendations_key_absent():
    """COST-006 not-verified when manifest exists but has no 'recommendations' key."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    # NOTE: recommendations=None means the key is not written to the manifest JSON
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
        recommendations=None,  # key absent
    )
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "not-verified", f"expected not-verified, got {f.status!r}: {f.detail}"


def test_cost006_must_fix_priority_over_should_fix():
    """COST-006 emits must-fix when there is at least one >$100 rec even with lower-savings peers."""
    recent_ts = _utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    recs = [
        {"logical_name": "cheap-svc", "monthly_savings_usd": 30.0},
        {"logical_name": "expensive-svc", "monthly_savings_usd": 200.0},
    ]
    ctx = _make_ctx(
        has_projection_md=True,
        manifest_exists=True,
        schema_version="1.0",
        generated_at=recent_ts,
        recommendations=recs,
    )
    f = _findings_by_id(ctx)["COST-006"]
    assert f.status == "must-fix", f"expected must-fix (highest-severity wins), got {f.status!r}: {f.detail}"


# ---------------------------------------------------------------------------
# Catalog sanity checks
# ---------------------------------------------------------------------------


def test_cost005_in_catalog():
    assert "COST-005" in mod.FINDING_CATALOG


def test_cost006_in_catalog():
    assert "COST-006" in mod.FINDING_CATALOG


def test_cost005_pillar_is_cost():
    assert mod.FINDING_CATALOG["COST-005"]["pillar"] == "cost"


def test_cost006_pillar_is_cost():
    assert mod.FINDING_CATALOG["COST-006"]["pillar"] == "cost"
