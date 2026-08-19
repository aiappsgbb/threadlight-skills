"""Tests for COST-102 / COST-103 — reconciled Azure cost consumption.

`threadlight-production-ready` is a *consumer* of the reconciliation artifact
published by `threadlight-consumption-iq` (`reconcile.py` +
`reconciliation_emitter.py`). It never re-derives variance, never applies a
second threshold and never re-evaluates maturity: it verifies that the
artifact bundle it was handed is internally consistent (schemas, digests,
timestamps, current staleness) and then reports the verdicts the reconciler
already computed.

Everything here is fail-closed: a missing, garbage, mis-schema'd, tampered,
stale or immature bundle produces `not-verified` on both findings — never a
`pass`, never a `must-fix`, and never an exception.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402


NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = "2026-08-08T00:00:00Z"
COLLECTED_AT = "2026-08-08T06:00:00Z"
RECONCILED_AT = "2026-08-08T06:30:00Z"

SPEC_TEXT = (
    "# SPEC\n\n## 10. Cost\nPricing plan: PAYG\nBudget: $500\n"
    "Cost owner: finops@example.com\n\n## 14. Value Model\n"
    "max_forecast_variance_pct: 0.25\n"
)


def _canonical_hash(data: dict) -> str:
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _forecast() -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-08-01T00:00:00Z",
        "totals": {"monthly_cost_current_usd": 500.0},
        "recommendations": [],
    }


def _actuals(*, window_end: str = WINDOW_END, generated_at: str = COLLECTED_AT) -> dict:
    return {
        "schema": "threadlight-cost-actuals/v1",
        "generated_at": generated_at,
        "status": "pass",
        "scope": {"subscription_id": "sub-1", "resource_group": "rg-pilot"},
        "window": {
            "start": "2026-08-01T00:00:00Z",
            "end": window_end,
            "complete_days": 7,
            "settlement_age_hours": 48,
            "window_end_age_days": 0,
        },
        "cost": {"basis": "usage-pretax", "period_total_usd": 130.0},
        "usage": {"interaction_status": "pass", "successful_interactions": 1200},
        "warnings": [],
    }


def _reconciliation(
    forecast: dict,
    actuals: dict,
    spec_text: str,
    *,
    status: str = "pass",
    maturity: str = "pass",
    variance_status: str = "pass",
    variance_pct: object = 0.12,
    max_forecast_variance_pct: object = 0.25,
    max_window_end_age_days: object = 3,
    payg_status: str = "pass",
    threshold_field: object = "max_token_volume_variance_pct",
    threshold_pct: object = 0.30,
    generated_at: str = RECONCILED_AT,
    drivers: object = None,
) -> dict:
    payg = {
        "status": payg_status,
        "observed_volume_variance_pct": 0.10,
        "forecast_monthly_tokens": 1000000,
        "observed_monthly_tokens": 1100000,
        "threshold_field": threshold_field,
        "threshold_pct": threshold_pct,
        "detail": "observed volume within declared band",
    }
    return {
        "schema": "threadlight-cost-reconciliation/v1",
        "generated_at": generated_at,
        "status": status,
        "variance_status": variance_status,
        "forecast_ref": {
            "path": "specs/cost-manifest.json",
            "sha256": _canonical_hash(forecast),
        },
        "actuals_ref": {
            "path": "specs/cost-actuals-manifest.json",
            "sha256": _canonical_hash(actuals),
        },
        "policy_ref": {
            "path": "specs/SPEC.md",
            "section": 14,
            "spec_sha256": hashlib.sha256(spec_text.encode("utf-8")).hexdigest(),
        },
        "policy_snapshot": {
            "max_forecast_variance_pct": max_forecast_variance_pct,
            "max_token_volume_variance_pct": 0.30,
            "max_window_end_age_days": max_window_end_age_days,
            "min_projection_attribution_coverage_pct": 0.95,
            "actual_billing_price_basis": "retail",
            "forecast_price_basis": "retail",
        },
        "policy_errors": [],
        "maturity": {"status": maturity, "checks": []},
        "totals": {
            "forecast_monthly_usd": 500.0,
            "actual_window_usd": 130.0,
            "variance_pct": variance_pct,
        },
        "unit_economics": {"status": "pass", "target_status": "pass"},
        "coverage": {
            "projection_attribution_coverage_pct": 1.0,
            "source_resource_id_coverage_pct": 1.0,
        },
        "drivers": {"payg_ptu": payg} if drivers is None else drivers,
        "warnings": [],
    }


def _make_ctx(root: Path) -> "pr.RepoContext":
    return pr.RepoContext(
        root=root,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text=(root / "specs" / "SPEC.md").read_text(encoding="utf-8")
        if (root / "specs" / "SPEC.md").exists()
        else "",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest={},
        bicep_text="",
        src_text="",
        bicep_graph=pr.BicepGraph(resources=[], source_files=[]),
    )


def _write_bundle(
    tmp_path: Path,
    *,
    spec_text: str | None = SPEC_TEXT,
    forecast: dict | None = None,
    actuals: dict | None = None,
    reconciliation: dict | None | str = None,
    **recon_kwargs: object,
) -> "pr.RepoContext":
    """Write a hash-consistent (forecast, actuals, reconciliation, SPEC) bundle."""
    specs = tmp_path / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    if spec_text is not None:
        (specs / "SPEC.md").write_text(spec_text, encoding="utf-8")
    forecast = _forecast() if forecast is None else forecast
    actuals = _actuals() if actuals is None else actuals
    (specs / "cost-manifest.json").write_text(json.dumps(forecast), encoding="utf-8")
    (specs / "cost-actuals-manifest.json").write_text(
        json.dumps(actuals), encoding="utf-8"
    )
    if isinstance(reconciliation, str):
        (specs / "cost-reconciliation-manifest.json").write_text(
            reconciliation, encoding="utf-8"
        )
    else:
        doc = (
            reconciliation
            if reconciliation is not None
            else _reconciliation(forecast, actuals, spec_text or "", **recon_kwargs)  # type: ignore[arg-type]
        )
        (specs / "cost-reconciliation-manifest.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )
    return _make_ctx(tmp_path)


def _freeze(monkeypatch, now: datetime = NOW) -> None:
    monkeypatch.setattr(pr, "_cost_reconciliation_now", lambda: now)


def _findings(ctx) -> dict[str, "pr.Finding"]:
    out = pr._check_cost_reconciliation_static(ctx)
    assert [f.id for f in out] == ["COST-102", "COST-103"]
    return {f.id: f for f in out}


def _both_not_verified(ctx) -> dict[str, "pr.Finding"]:
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified", found["COST-102"].detail
    assert found["COST-103"].status == "not-verified", found["COST-103"].detail
    return found


def _cost102_only_not_verified(ctx) -> dict[str, "pr.Finding"]:
    """A malformed *cost* value degrades COST-102 alone.

    The PAYG/PTU driver is a token-volume verdict computed from separate
    evidence in the same hash-bound document; degrading it too would report a
    volume question as unknown because a money field was unusable.
    """
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified", found["COST-102"].detail
    assert found["COST-103"].status == "pass", found["COST-103"].detail
    return found


# --------------------------------------------------------------------------
# catalog contract
# --------------------------------------------------------------------------


def test_catalog_entries_declare_tolerance_without_a_hardcoded_number() -> None:
    cost102 = pr.FINDING_CATALOG["COST-102"]
    assert cost102["title"] == "Live actuals vs forecast within declared tolerance"
    assert cost102["severity"] == "should-fix"
    assert cost102["tier"] == 3
    assert cost102["experimental"] is True
    cost103 = pr.FINDING_CATALOG["COST-103"]
    assert cost103["severity"] == "should-fix"
    assert cost103["tier"] == 3
    assert cost103["experimental"] is True
    for meta in (cost102, cost103):
        assert "20%" not in meta["title"]
        assert "20" not in meta["title"]


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


def test_valid_bundle_passes_both_findings(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path))
    assert found["COST-102"].status == "pass"
    assert found["COST-103"].status == "pass"
    assert "12.0%" in found["COST-102"].detail
    assert "25.0%" in found["COST-102"].detail
    assert "1,100,000" in found["COST-103"].detail
    assert "1,000,000" in found["COST-103"].detail
    assert "30.0%" in found["COST-103"].detail


def test_no_finding_text_hardcodes_twenty_percent(tmp_path, monkeypatch) -> None:
    """The tolerance is per-workload SPEC §14 policy, never a baked-in 20%."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        max_forecast_variance_pct=0.60,
        variance_pct=0.50,
        variance_status="pass",
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "pass"
    assert "60.0%" in found["COST-102"].detail
    assert "50.0%" in found["COST-102"].detail
    for finding in found.values():
        assert "20%" not in finding.title
        assert "20%" not in finding.detail


def test_cost103_detail_never_carries_a_dollar_figure(tmp_path, monkeypatch) -> None:
    """A PTU sizing decision is a token-throughput question, never a spend one."""
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path))
    assert "$" not in found["COST-103"].detail
    assert "usd" not in found["COST-103"].detail.lower()


def test_cost102_should_fix_outside_declared_tolerance(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, variance_status="should-fix", variance_pct=0.42)
    found = _findings(ctx)
    assert found["COST-102"].status == "should-fix"
    assert "42.0%" in found["COST-102"].detail
    assert "25.0%" in found["COST-102"].detail
    assert found["COST-103"].status == "pass"


def test_cost102_never_emits_must_fix(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for variance_status in ("pass", "should-fix", "not-verified", "fail", None):
        ctx = _write_bundle(tmp_path, variance_status=variance_status)  # type: ignore[arg-type]
        for finding in _findings(ctx).values():
            assert finding.status in ("pass", "should-fix", "not-verified")


# --------------------------------------------------------------------------
# maturity / envelope gates
# --------------------------------------------------------------------------


def test_immature_reconciliation_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, status="not-verified", maturity="not-verified")
    _both_not_verified(ctx)


def test_maturity_not_pass_under_a_pass_envelope_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """`status` mirrors `maturity.status`; a bundle where they disagree is
    tampered or produced by a non-conforming writer — never trusted."""
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, status="pass", maturity="not-verified"))


def test_missing_maturity_block_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc.pop("maturity")
    _both_not_verified(
        _write_bundle(tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc)
    )


# --------------------------------------------------------------------------
# artifact presence / schema
# --------------------------------------------------------------------------


def test_missing_reconciliation_manifest_is_actionable_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC.md").write_text(SPEC_TEXT, encoding="utf-8")
    (specs / "cost-manifest.json").write_text(json.dumps(_forecast()), encoding="utf-8")
    found = _both_not_verified(_make_ctx(tmp_path))
    for finding in found.values():
        assert "threadlight-consumption-iq" in finding.detail
        assert "actuals" in finding.detail
        assert "reconcile" in finding.detail


def test_garbage_reconciliation_manifest_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, reconciliation="{ not json"))


def test_non_object_reconciliation_manifest_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, reconciliation="[1, 2, 3]"))


def test_wrong_reconciliation_schema_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc["schema"] = "threadlight-cost-reconciliation/v2"
    _both_not_verified(
        _write_bundle(tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc)
    )


def test_wrong_actuals_schema_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    actuals = _actuals()
    actuals["schema"] = "threadlight-cost-actuals/v2"
    _both_not_verified(_write_bundle(tmp_path, actuals=actuals))


def test_missing_actuals_manifest_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    (tmp_path / "specs" / "cost-actuals-manifest.json").unlink()
    _both_not_verified(ctx)


def test_forecast_schema_version_below_one_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    forecast = _forecast()
    forecast["schema_version"] = "0.9"
    _both_not_verified(_write_bundle(tmp_path, forecast=forecast))


def test_forecast_schema_version_two_is_accepted(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    forecast = _forecast()
    forecast["schema_version"] = "2.0"
    found = _findings(_write_bundle(tmp_path, forecast=forecast))
    assert found["COST-102"].status == "pass"


def test_garbage_forecast_manifest_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    (tmp_path / "specs" / "cost-manifest.json").write_text("{{{", encoding="utf-8")
    _both_not_verified(ctx)


# --------------------------------------------------------------------------
# digest binding — all three refs
# --------------------------------------------------------------------------


def test_actuals_hash_mismatch_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    tampered = _actuals()
    tampered["cost"]["period_total_usd"] = 9999.0
    (tmp_path / "specs" / "cost-actuals-manifest.json").write_text(
        json.dumps(tampered), encoding="utf-8"
    )
    _both_not_verified(ctx)


def test_forecast_hash_mismatch_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    tampered = _forecast()
    tampered["totals"]["monthly_cost_current_usd"] = 1.0
    (tmp_path / "specs" / "cost-manifest.json").write_text(
        json.dumps(tampered), encoding="utf-8"
    )
    _both_not_verified(ctx)


def test_spec_hash_mismatch_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    (tmp_path / "specs" / "SPEC.md").write_text(
        SPEC_TEXT + "\nmax_forecast_variance_pct: 0.90\n", encoding="utf-8"
    )
    _both_not_verified(ctx)


def test_missing_spec_never_raises_and_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    (tmp_path / "specs" / "SPEC.md").unlink()
    _both_not_verified(ctx)


def test_placeholder_spec_anchor_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc["policy_ref"]["spec_sha256"] = "TBD"
    _both_not_verified(
        _write_bundle(tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc)
    )


def test_non_hex_spec_anchor_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc["policy_ref"]["spec_sha256"] = "z" * 64
    _both_not_verified(
        _write_bundle(tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc)
    )


def test_missing_ref_block_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc.pop("actuals_ref")
    _both_not_verified(
        _write_bundle(tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc)
    )


# --------------------------------------------------------------------------
# path handling — refs are provenance, never a read instruction
# --------------------------------------------------------------------------


def test_ref_paths_are_never_followed(tmp_path, monkeypatch) -> None:
    """`*_ref.path` is provenance only. A traversal-shaped path must neither
    redirect the read nor invalidate a bundle whose canonical files are sound."""
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc["actuals_ref"]["path"] = "../../../../etc/passwd"
    doc["forecast_ref"]["path"] = "/etc/shadow"
    doc["policy_ref"]["path"] = "../../../../etc/hosts"
    ctx = _write_bundle(
        tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "pass"
    assert found["COST-103"].status == "pass"


def test_ref_path_pointing_at_a_decoy_file_is_ignored(tmp_path, monkeypatch) -> None:
    """A decoy that would satisfy the digest must not be read: only the
    canonical `specs/` names are hashed, so a broken canonical actuals stays
    broken no matter where a ref points."""
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc["actuals_ref"]["path"] = "specs/decoy-actuals.json"
    ctx = _write_bundle(
        tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc
    )
    (tmp_path / "specs" / "decoy-actuals.json").write_text(
        json.dumps(actuals), encoding="utf-8"
    )
    (tmp_path / "specs" / "cost-actuals-manifest.json").write_text(
        json.dumps({"schema": "threadlight-cost-actuals/v1"}), encoding="utf-8"
    )
    _both_not_verified(ctx)


# --------------------------------------------------------------------------
# timestamps
# --------------------------------------------------------------------------


def test_reconciled_before_collected_is_not_verified(tmp_path, monkeypatch) -> None:
    """A verdict cannot predate the evidence it judges."""
    _freeze(monkeypatch)
    _both_not_verified(
        _write_bundle(tmp_path, generated_at="2026-08-08T05:00:00Z")
    )


def test_reconciled_equal_to_collected_is_accepted(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path, generated_at=COLLECTED_AT))
    assert found["COST-102"].status == "pass"


def test_malformed_reconciled_at_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, generated_at="last tuesday"))


def test_naive_reconciled_at_is_not_verified(tmp_path, monkeypatch) -> None:
    """A timestamp with no offset is not a UTC instant; it is an assumption."""
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, generated_at="2026-08-08T06:30:00"))


def test_malformed_collected_at_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    actuals = _actuals(generated_at="not-a-timestamp")
    _both_not_verified(_write_bundle(tmp_path, actuals=actuals))


# --------------------------------------------------------------------------
# current staleness (re-evaluated now, not at reconcile time)
# --------------------------------------------------------------------------


def test_stale_window_end_is_not_verified_today(tmp_path, monkeypatch) -> None:
    """A bundle that was mature when written goes stale on the wall clock."""
    ctx = _write_bundle(tmp_path, max_window_end_age_days=3)
    _freeze(monkeypatch, NOW + timedelta(days=30))
    found = _both_not_verified(ctx)
    for finding in found.values():
        assert "stale" in finding.detail.lower()
        assert "threadlight-consumption-iq" in finding.detail


def test_stale_loader_still_returns_the_document_with_a_reason(
    tmp_path, monkeypatch
) -> None:
    ctx = _write_bundle(tmp_path, max_window_end_age_days=3)
    _freeze(monkeypatch, NOW + timedelta(days=30))
    data = pr._read_cost_reconciliation(ctx)
    assert isinstance(data, dict)
    assert data.get("_stale_reason")


def test_window_end_age_exactly_at_the_declared_limit_is_accepted(
    tmp_path, monkeypatch
) -> None:
    ctx = _write_bundle(tmp_path, max_window_end_age_days=3)
    _freeze(monkeypatch, datetime(2026, 8, 11, 0, 0, 0, tzinfo=timezone.utc))
    assert _findings(ctx)["COST-102"].status == "pass"


def test_missing_max_window_end_age_days_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, max_window_end_age_days=None))


def test_non_numeric_max_window_end_age_days_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, max_window_end_age_days="three"))


def test_malformed_window_end_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, actuals=_actuals(window_end="soon")))


def test_window_end_in_the_future_is_not_verified(tmp_path, monkeypatch) -> None:
    ctx = _write_bundle(tmp_path)
    _freeze(monkeypatch, datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc))
    _both_not_verified(ctx)


# --------------------------------------------------------------------------
# COST-102 value validation — the verdict is consumed, never recomputed
# --------------------------------------------------------------------------


def test_boolean_variance_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _cost102_only_not_verified(_write_bundle(tmp_path, variance_pct=True))


def test_string_variance_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _cost102_only_not_verified(_write_bundle(tmp_path, variance_pct="12%"))


def test_nan_variance_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _cost102_only_not_verified(_write_bundle(tmp_path, variance_pct=float("nan")))


def test_infinite_variance_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _cost102_only_not_verified(_write_bundle(tmp_path, variance_pct=float("inf")))


def test_null_variance_pct_cannot_produce_a_verdict(tmp_path, monkeypatch) -> None:
    """`variance_pct` is `null` against a zero or unknown baseline; a verdict
    claiming a tolerance comparison on an uncomputable ratio is not evidence."""
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path, variance_pct=None))
    assert found["COST-102"].status == "not-verified"


def test_unknown_variance_status_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    assert (
        _findings(_write_bundle(tmp_path, variance_status="fail"))["COST-102"].status
        == "not-verified"
    )


def test_reconciler_not_verified_variance_status_is_relayed(
    tmp_path, monkeypatch
) -> None:
    """Basis mismatch (and every other degrade) is already folded into
    `variance_status` by the reconciler; this consumer never re-derives it."""
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT, variance_status="not-verified")
    doc["policy_snapshot"]["actual_billing_price_basis"] = "ea"
    doc["policy_snapshot"]["forecast_price_basis"] = "retail"
    doc["warnings"] = ["price basis mismatch: ea vs retail"]
    ctx = _write_bundle(
        tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified"
    # The driver verdict is independent evidence and survives the cost degrade.
    assert found["COST-103"].status == "pass"


def test_out_of_range_tolerance_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for bad in (-0.1, 1.5, True, "0.2", None, float("nan")):
        found = _findings(_write_bundle(tmp_path, max_forecast_variance_pct=bad))
        assert found["COST-102"].status == "not-verified", bad


# --------------------------------------------------------------------------
# COST-103 — PAYG/PTU driver
# --------------------------------------------------------------------------


def test_driver_should_fix_asks_for_a_rerun_at_observed_volume(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path, payg_status="should-fix"))
    assert found["COST-103"].status == "should-fix"
    assert "rerun" in found["COST-103"].detail.lower()
    assert "observed volume" in found["COST-103"].detail.lower()
    assert found["COST-102"].status == "pass"


def test_driver_not_verified_is_relayed(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path, payg_status="not-verified"))
    assert found["COST-103"].status == "not-verified"
    assert found["COST-102"].status == "pass"


def test_unknown_driver_status_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    assert (
        _findings(_write_bundle(tmp_path, payg_status="fail"))["COST-103"].status
        == "not-verified"
    )


def test_missing_drivers_block_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path, drivers={}))
    assert found["COST-103"].status == "not-verified"
    assert found["COST-102"].status == "pass"


def test_malformed_driver_block_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    found = _findings(_write_bundle(tmp_path, drivers={"payg_ptu": "pass"}))
    assert found["COST-103"].status == "not-verified"


def test_cost_threshold_field_on_the_driver_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """The driver must declare the *token volume* threshold. A cost tolerance
    on a volume question is the wrong number for the unit."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, threshold_field="max_forecast_variance_pct")
    assert _findings(ctx)["COST-103"].status == "not-verified"


def test_missing_threshold_field_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, threshold_field=None)
    assert _findings(ctx)["COST-103"].status == "not-verified"


def test_missing_threshold_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, threshold_pct=None)
    assert _findings(ctx)["COST-103"].status == "not-verified"


def test_out_of_range_threshold_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    for bad in (-0.2, 2.0, True, "0.3", float("inf")):
        ctx = _write_bundle(tmp_path, threshold_pct=bad)
        assert _findings(ctx)["COST-103"].status == "not-verified", bad


def test_driver_detail_names_the_token_volume_threshold(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    detail = _findings(_write_bundle(tmp_path))["COST-103"].detail
    assert "max_token_volume_variance_pct" in detail
    assert "max_forecast_variance_pct" not in detail


def test_driver_verdict_without_token_volumes_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """A verdict with no measured volumes behind it cannot be shown to a reader."""
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    for bad in (None, "many", float("nan"), True):
        doc = _reconciliation(forecast, actuals, SPEC_TEXT)
        doc["drivers"]["payg_ptu"]["observed_monthly_tokens"] = bad
        ctx = _write_bundle(
            tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc
        )
        assert _findings(ctx)["COST-103"].status == "not-verified", bad


# --------------------------------------------------------------------------
# integration: static emits once, live emits neither
# --------------------------------------------------------------------------


def test_static_cost_pillar_emits_each_finding_exactly_once(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    ids = [f.id for f in pr._check_cost_static(ctx)]
    assert ids.count("COST-102") == 1
    assert ids.count("COST-103") == 1


def test_live_probe_emits_neither_reconciliation_finding(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    monkeypatch.setattr(pr, "_az_json", lambda *args: [])
    for tiers in ({3: True}, {3: False}):
        live, _ = pr._check_cost_live(ctx, tiers, "sub-1", "rg-pilot")
        ids = [f.id for f in live]
        assert "COST-102" not in ids
        assert "COST-103" not in ids
        assert ids.count("COST-101") == 1
        assert ids.count("COST-104") == 1
        assert ids.count("COST-105") == 1


def test_combined_static_and_live_carry_exactly_one_of_each(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    monkeypatch.setattr(pr, "_az_json", lambda *args: [])
    for tiers in ({3: True}, {3: False}):
        live, _ = pr._check_cost_live(ctx, tiers, "sub-1", "rg-pilot")
        ids = [f.id for f in pr._check_cost_static(ctx)] + [f.id for f in live]
        assert ids.count("COST-102") == 1
        assert ids.count("COST-103") == 1
        assert ids.count("COST-101") == 1


def test_cost_007_meter_coverage_is_unaffected(tmp_path, monkeypatch) -> None:
    """The #116 meter-coverage verdict reads the forecast manifest only."""
    _freeze(monkeypatch)
    forecast = _forecast()
    forecast["meter_coverage"] = {"status": "complete"}
    forecast["resources"] = [{"logical_name": "aca", "pricing_status": "priced"}]
    ctx = _write_bundle(tmp_path, forecast=forecast)
    by_id = {f.id: f for f in pr._check_cost_static(ctx)}
    assert by_id["COST-007"].status == "pass"
    assert by_id["COST-102"].status == "pass"

    forecast_bad = _forecast()
    forecast_bad["meter_coverage"] = {"status": "complete"}
    forecast_bad["resources"] = [
        {"logical_name": "aca", "pricing_status": "not-priceable"}
    ]
    ctx_bad = _write_bundle(tmp_path / "bad", forecast=forecast_bad)
    by_id_bad = {f.id: f for f in pr._check_cost_static(ctx_bad)}
    assert by_id_bad["COST-007"].status == "must-fix"
    assert by_id_bad["COST-102"].status == "pass"


def test_cost_005_and_006_still_read_the_forecast_manifest(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    forecast = _forecast()
    forecast["generated_at"] = pr._utc_now()
    forecast["recommendations"] = [
        {"logical_name": "aoai", "monthly_savings_usd": 250}
    ]
    ctx = _write_bundle(tmp_path, forecast=forecast)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "cost-projection.md").write_text("# Cost\n", encoding="utf-8")
    by_id = {f.id: f for f in pr._check_cost_static(ctx)}
    assert by_id["COST-005"].status == "pass"
    assert by_id["COST-006"].status == "must-fix"


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------


def test_loader_never_raises_on_hostile_documents(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    hostile = [
        "",
        "null",
        "[]",
        '"a string"',
        "{}",
        json.dumps({"schema": "threadlight-cost-reconciliation/v1"}),
        json.dumps(
            {
                "schema": "threadlight-cost-reconciliation/v1",
                "actuals_ref": [],
                "forecast_ref": None,
                "policy_ref": 7,
                "policy_snapshot": "nope",
                "maturity": [],
                "totals": None,
                "drivers": 3,
                "generated_at": None,
            }
        ),
        json.dumps({"schema": "threadlight-cost-reconciliation/v1", "totals": []}),
    ]
    for payload in hostile:
        ctx = _write_bundle(tmp_path, reconciliation=payload)
        assert pr._read_cost_reconciliation(ctx) is None or isinstance(
            pr._read_cost_reconciliation(ctx), dict
        )
        _both_not_verified(ctx)


def test_non_utf8_artifacts_never_raise(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    (tmp_path / "specs" / "cost-reconciliation-manifest.json").write_bytes(
        b"\xff\xfe\x00binary"
    )
    _both_not_verified(ctx)


def test_loader_leaves_the_artifacts_on_disk_untouched(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    specs = tmp_path / "specs"
    before = {p.name: p.read_bytes() for p in specs.iterdir()}
    pr._check_cost_reconciliation_static(ctx)
    after = {p.name: p.read_bytes() for p in specs.iterdir()}
    assert before == after


def test_default_time_helper_returns_aware_utc() -> None:
    now = pr._cost_reconciliation_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert not math.isnan(now.timestamp())
