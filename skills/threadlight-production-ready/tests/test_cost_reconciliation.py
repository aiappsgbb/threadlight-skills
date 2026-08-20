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
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / "scripts" / "production_ready.py"
REPO_ROOT = TEST_DIR.parent.parent.parent

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

# The reconciler's measured unit economics: 130.00 USD over 1200 successful
# interactions. `status` is the evidence gate, `target_status` the separate
# comparison against the SPEC § 14 declared target — see
# threadlight-consumption-iq's `reconcile._unit_economics`. Shared with
# test_kpi_scorecard, which consumes this block as the KPI-003 cost signal.
UNIT_ECONOMICS: dict[str, object] = {
    "status": "pass",
    "successful_interactions": 1200,
    "cost_per_successful_interaction_usd": 0.1083,
    "target_usd": 0.15,
    "target_status": "pass",
}


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


def _actuals(
    *,
    window_end: str = WINDOW_END,
    generated_at: str = COLLECTED_AT,
    period_total_usd: object = 130.0,
    interaction_status: object = "pass",
    successful_interactions: object = 1200,
) -> dict:
    """The collected Azure actuals — the canonical, digest-pinned cost evidence.

    `cost.period_total_usd` and `usage.successful_interactions` are the two
    numbers the reconciler divides to obtain
    `unit_economics.cost_per_successful_interaction_usd`, and they are the two
    the KPI scorecard re-derives that unit cost from (see
    `_read_cost_per_interaction`). They are overridable here so a test can
    restate the observed cost/usage and have `_write_bundle` re-chain the
    `actuals_ref` digest, rather than tampering with a document the loader
    would then reject outright.
    """
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
        "cost": {"basis": "usage-pretax", "period_total_usd": period_total_usd},
        "usage": {
            "interaction_status": interaction_status,
            "successful_interactions": successful_interactions,
        },
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
    max_token_volume_variance_pct: object = 0.30,
    observed_volume_variance_pct: object = 0.10,
    generated_at: str = RECONCILED_AT,
    drivers: object = None,
    unit_economics: object = None,
    forecast_window_usd: object = 120.0,
    projection_attribution_coverage_pct: object = 1.0,
    source_resource_id_coverage_pct: object = 1.0,
    unmodeled_actual_usd: object = 12.3,
) -> dict:
    payg = {
        "status": payg_status,
        "observed_volume_variance_pct": observed_volume_variance_pct,
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
            "max_token_volume_variance_pct": max_token_volume_variance_pct,
            "max_window_end_age_days": max_window_end_age_days,
            "min_projection_attribution_coverage_pct": 0.95,
            "actual_billing_price_basis": "retail",
            "forecast_price_basis": "retail",
        },
        "policy_errors": [],
        "maturity": {"status": maturity, "checks": []},
        "totals": {
            "forecast_monthly_usd": 500.0,
            "forecast_window_usd": forecast_window_usd,
            "actual_window_usd": 130.0,
            "variance_pct": variance_pct,
        },
        "unit_economics": (
            unit_economics if unit_economics is not None else dict(UNIT_ECONOMICS)
        ),
        "coverage": {
            "projection_attribution_coverage_pct": projection_attribution_coverage_pct,
            "source_resource_id_coverage_pct": source_resource_id_coverage_pct,
            "unmodeled_actual_usd": unmodeled_actual_usd,
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
# cost_evidence summary
# --------------------------------------------------------------------------


def test_cost_evidence_summary_relays_verified_bundle_fields(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    ctx.manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-pilot",
        }
    }
    assert hasattr(pr, "_cost_evidence_summary"), (
        "production_ready must publish a cost_evidence summary helper")

    summary = pr._cost_evidence_summary(ctx)

    assert summary["status"] == "pass"
    assert summary["source_paths"] == {
        "forecast": "specs/cost-manifest.json",
        "actuals": "specs/cost-actuals-manifest.json",
        "reconciliation": "specs/cost-reconciliation-manifest.json",
    }
    assert summary["actuals_window_start"] == "2026-08-01T00:00:00Z"
    assert summary["actuals_window_end"] == WINDOW_END
    assert summary["actuals_subscription_id"] == "sub-1"
    assert summary["actuals_resource_group"] == "rg-pilot"
    assert summary["forecast_window_usd"] == 120.0
    assert summary["forecast_monthly_usd"] == 500.0
    assert summary["actual_window_usd"] == 130.0
    assert summary["variance_pct"] == 0.12
    assert summary["projection_attribution_coverage_pct"] == 1.0
    assert summary["source_resource_id_coverage_pct"] == 1.0
    assert summary["unallocated_actual_cost_usd"] == 12.3
    assert summary["cost_per_successful_interaction_usd"] == 0.1083


def test_cost_evidence_summary_is_not_verified_when_target_scope_is_incomplete(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    assert hasattr(pr, "_cost_evidence_summary"), (
        "production_ready must publish a cost_evidence summary helper")

    for deployment_manifest in (
        {},
        {"subscription_id": "sub-1"},
        {"resource_group": "rg-pilot"},
    ):
        ctx.manifest = {"deployment_manifest": deployment_manifest}
        summary = pr._cost_evidence_summary(ctx)

        assert summary["status"] == "not-verified"
        assert "target" in summary["detail"].lower()
        assert summary["source_paths"] == {
            "forecast": "specs/cost-manifest.json",
            "actuals": "specs/cost-actuals-manifest.json",
            "reconciliation": "specs/cost-reconciliation-manifest.json",
        }
        for key in (
            "actuals_window_start",
            "actuals_window_end",
            "actuals_subscription_id",
            "actuals_resource_group",
            "forecast_window_usd",
            "forecast_monthly_usd",
            "actual_window_usd",
            "variance_pct",
            "projection_attribution_coverage_pct",
            "source_resource_id_coverage_pct",
            "unallocated_actual_cost_usd",
            "cost_per_successful_interaction_usd",
        ):
            assert summary.get(key) is None, f"{key} must stay absent when target scope is incomplete"


def test_cost_evidence_summary_is_not_verified_on_target_scope_mismatch(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    ctx.manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-other",
        }
    }
    assert hasattr(pr, "_cost_evidence_summary"), (
        "production_ready must publish a cost_evidence summary helper")

    summary = pr._cost_evidence_summary(ctx)

    assert summary["status"] == "not-verified"
    assert "scope" in summary["detail"].lower()
    assert summary["source_paths"] == {
        "forecast": "specs/cost-manifest.json",
        "actuals": "specs/cost-actuals-manifest.json",
        "reconciliation": "specs/cost-reconciliation-manifest.json",
    }
    for key in (
        "actuals_window_start",
        "actuals_window_end",
        "actuals_subscription_id",
        "actuals_resource_group",
        "forecast_window_usd",
        "forecast_monthly_usd",
        "actual_window_usd",
        "variance_pct",
        "projection_attribution_coverage_pct",
        "source_resource_id_coverage_pct",
        "unallocated_actual_cost_usd",
        "cost_per_successful_interaction_usd",
    ):
        assert summary.get(key) is None, f"{key} must stay absent when scope is unverified"


# --------------------------------------------------------------------------
# committed exemplar pairing — COST-102/COST-103 wording must not drift
# --------------------------------------------------------------------------

# COST-102/COST-103 are `experimental: True` (see FINDING_CATALOG above), so
# `_render_manifest` filters them out of the scored JSON manifest by default —
# they never appear under `pillars[].findings` there. The markdown report
# renders from the *unfiltered* pillar findings, though, so both findings do
# show up in it twice: once in the "5. Pillar deep-dives" table (with a
# " (tier: ...)" suffix appended for any tier > 0 finding) and once in the
# "10. Appendix / What was not verified" table (no suffix, raw `f.detail`).
# This test derives ground truth by constructing a real `RepoContext` for
# each fixture and calling `_check_cost_reconciliation_static` directly —
# the same function `main()` calls — rather than trusting either committed
# artifact, then asserts both rendered rows match that ground truth exactly.
_ROW_RE_TMPL = (
    r"^\|\s*`{fid}`\s*\|\s*[^|]+?\s*\|\s*[^|]+?\s*\|\s*(.+?)\s*\|\s*$")


def _row_detail(text: str, fid: str) -> str | None:
    r"""Last matching `| \`{fid}\` | ... | ... | detail |` row's detail column."""
    pattern = re.compile(_ROW_RE_TMPL.format(fid=re.escape(fid)))
    detail: str | None = None
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            detail = m.group(1)
    return detail


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def _tracked_production_readiness_pairs() -> list[tuple[Path, Path]]:
    """Every TRACKED production-readiness manifest, paired with its sibling report.

    Derived from `git ls-files` — not a hand-maintained list — so a newly
    committed exemplar is picked up automatically, and one that is gitignored
    (like `sample-pilot`'s own pair, which the fixture regenerates on every
    run and never commits) drops out on its own. There is no per-pair
    "if missing, skip" escape hatch: every manifest this derivation finds
    MUST have its sibling report tracked too, or the derivation itself raises
    — a manifest without its report (or vice versa) is a broken pair, not a
    skippable one.
    """
    tracked = _tracked_files()
    tracked_set = set(tracked)
    manifests = sorted(
        p for p in tracked
        if p.endswith("production-readiness-manifest.json")
        or p.endswith("production-readiness.json"))
    pairs: list[tuple[Path, Path]] = []
    for rel_manifest in manifests:
        manifest_path = REPO_ROOT / rel_manifest
        # tests/production-readiness-manifest.json -> docs/production-readiness-report.md
        # specs/production-readiness.json          -> docs/production-readiness-report.md
        fixture_root = manifest_path.parent.parent
        rel_report = (fixture_root / "docs" / "production-readiness-report.md").relative_to(REPO_ROOT).as_posix()
        assert rel_report in tracked_set, (
            f"{rel_manifest} is a tracked production-readiness manifest but its sibling "
            f"report {rel_report} is not tracked — a manifest/report exemplar pair must be "
            "committed or dropped together, never half of one.")
        pairs.append((manifest_path, fixture_root / "docs" / "production-readiness-report.md"))
    return pairs


def test_committed_cost_reconciliation_fixtures_match_current_generation() -> None:
    """Every TRACKED manifest/report pair must show today's COST-102/COST-103 wording.

    None of the three committed exemplars (`examples/returns-triage-governed`,
    `sample-pilot-broken`, `sample-pilot-citadel`) ship a
    `specs/cost-reconciliation-manifest.json`, so `_check_cost_reconciliation_static`
    always degrades both findings to `not-verified` with the "No provable
    specs/cost-reconciliation-manifest.json ..." detail for all three — never
    the retired "Skipped — running in --static mode" static-tier stub. This
    would have caught the exemplars going stale when the artifact-driven
    rewrite landed without failing any other test.
    """
    pairs = _tracked_production_readiness_pairs()
    assert len(pairs) == 3, (
        f"expected exactly 3 tracked production-readiness exemplar pairs, found "
        f"{len(pairs)}: {[str(m) for m, _ in pairs]} — update this test if the set of "
        "committed exemplars has intentionally changed.")
    checked = 0
    for manifest_path, report_path in pairs:
        assert manifest_path.exists(), f"tracked manifest missing on disk: {manifest_path}"
        assert report_path.exists(), f"tracked report missing on disk: {report_path}"
        fixture_root = manifest_path.parent.parent
        ctx = pr.RepoContext.from_repo(fixture_root, {})
        ground_truth = {f.id: f for f in pr._check_cost_reconciliation_static(ctx)}
        report_text = report_path.read_text(encoding="utf-8")

        # `title` never appears in the report for a not-verified finding (the
        # numbered gap list only lists must-fix/should-fix rows), so the
        # catalog itself is the only place these two are ever stated to a
        # reader — pin it here too, not just in the dedicated catalog test
        # above, so this test alone fails if either drifts.
        assert pr.FINDING_CATALOG["COST-102"]["title"] == (
            "Live actuals vs forecast within declared tolerance")
        assert pr.FINDING_CATALOG["COST-103"]["title"] == (
            "PAYG vs PTU recommendation matches observed usage")

        for fid in ("COST-102", "COST-103"):
            checked += 1
            finding = ground_truth[fid]
            assert finding.status == "not-verified", (
                f"{fixture_root}: expected {fid} not-verified (no reconciliation artifact "
                f"is committed in this fixture), got {finding.status!r}")
            assert "No provable specs/cost-reconciliation-manifest.json" in finding.detail
            assert "Skipped" not in finding.detail, (
                f"{fixture_root}: {fid} regressed to the retired static-mode skip stub")

            main_table_text = report_text.split("## 10. Appendix", 1)[0]
            appendix_text = report_text.split("## 10. Appendix", 1)[1]

            main_detail = _row_detail(main_table_text, fid)
            assert main_detail is not None, (
                f"no {fid} row in the pillar deep-dives table of {report_path}")
            tier_label = pr.TIER_TO_LABEL[pr.FINDING_CATALOG[fid]["tier"]]
            assert main_detail == f"{finding.detail} (tier: {tier_label})", (
                f"{report_path} pillar-deep-dive {fid} detail drifted from live "
                f"generation:\n  report: {main_detail!r}\n  live:   "
                f"{finding.detail + f' (tier: {tier_label})'!r}")

            appendix_detail = _row_detail(appendix_text, fid)
            assert appendix_detail is not None, (
                f"no {fid} row in the appendix 'What was not verified' table of {report_path}")
            assert appendix_detail == finding.detail, (
                f"{report_path} appendix {fid} detail drifted from live generation:\n"
                f"  report: {appendix_detail!r}\n  live:   {finding.detail!r}")
    assert checked == len(pairs) * 2, "every pair must yield both COST-102 and COST-103"


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
    assert "docs/cost-reconciliation.md" in found["COST-102"].detail
    assert "docs/cost-reconciliation-report.md" not in found["COST-102"].detail
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


# ---------------------------------------------------------------------------
# The bundle loader — all three proved documents, read once
# ---------------------------------------------------------------------------


def test_bundle_loader_returns_all_three_proved_documents(
    tmp_path, monkeypatch
) -> None:
    """The digests prove forecast AND actuals, so callers may read both.

    `_read_cost_reconciliation` returns only the verdict document, which is all
    COST-102/COST-103 need. A consumer that must re-derive a number (KPI-003's
    unit cost) needs the canonical actuals the digest already pinned — handing
    it back here means it is never re-read, re-hashed or re-proved elsewhere.
    """
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    bundle = pr._read_cost_reconciliation_bundle(ctx)
    assert bundle is not None
    reconciliation, actuals, forecast = bundle
    assert reconciliation["schema"] == pr._COST_RECONCILIATION_SCHEMA
    assert actuals == _actuals()
    assert forecast == _forecast()
    assert reconciliation.get("_stale_reason") is None


def test_bundle_loader_and_wrapper_agree(tmp_path, monkeypatch) -> None:
    """`_read_cost_reconciliation` stays the wrapper it always was."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    bundle = pr._read_cost_reconciliation_bundle(ctx)
    assert bundle is not None
    assert pr._read_cost_reconciliation(ctx) == bundle[0]

    # ...and every rejection is a rejection on both.
    bad = _write_bundle(tmp_path / "tampered", actuals=_actuals(period_total_usd=999.0),
                        reconciliation=_reconciliation(
                            _forecast(), _actuals(), SPEC_TEXT))
    assert pr._read_cost_reconciliation_bundle(bad) is None
    assert pr._read_cost_reconciliation(bad) is None


def test_bundle_loader_carries_the_stale_reason(tmp_path, monkeypatch) -> None:
    ctx = _write_bundle(tmp_path, max_window_end_age_days=3)
    _freeze(monkeypatch, NOW + timedelta(days=30))
    bundle = pr._read_cost_reconciliation_bundle(ctx)
    assert bundle is not None
    assert bundle[0].get("_stale_reason")


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
# COST-102 verdict integrity — the relayed verdict must match its own numbers
# --------------------------------------------------------------------------


CONTRADICTION_PHRASE = "reconciliation verdict contradicts its numeric variance/tolerance"


def test_forged_pass_far_outside_the_declared_tolerance_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """A `pass` on 400% variance against a 5% tolerance is not a verdict this
    consumer may relay — the artifact contradicts itself, so it is unusable."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        variance_status="pass",
        variance_pct=4.0,
        max_forecast_variance_pct=0.05,
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified"
    assert CONTRADICTION_PHRASE in found["COST-102"].detail
    assert "400.0%" in found["COST-102"].detail
    assert "5.0%" in found["COST-102"].detail
    # An unusable cost verdict never degrades the independent volume verdict.
    assert found["COST-103"].status == "pass"


def test_forged_should_fix_inside_the_declared_tolerance_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """The contradiction gate is symmetric: a `should-fix` that its own numbers
    do not support is just as untrustworthy as a forged `pass`."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        variance_status="should-fix",
        variance_pct=0.01,
        max_forecast_variance_pct=0.25,
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified"
    assert CONTRADICTION_PHRASE in found["COST-102"].detail


def test_variance_exactly_at_the_declared_tolerance_is_a_pass(
    tmp_path, monkeypatch
) -> None:
    """`<=` — the declared tolerance is inclusive, matching the reconciler."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        variance_status="pass",
        variance_pct=0.25,
        max_forecast_variance_pct=0.25,
    )
    assert _findings(ctx)["COST-102"].status == "pass"


def test_should_fix_exactly_at_the_declared_tolerance_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        variance_status="should-fix",
        variance_pct=0.25,
        max_forecast_variance_pct=0.25,
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified"
    assert CONTRADICTION_PHRASE in found["COST-102"].detail


def test_variance_just_outside_the_declared_tolerance_must_be_should_fix(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        variance_status="should-fix",
        variance_pct=0.2501,
        max_forecast_variance_pct=0.25,
    )
    assert _findings(ctx)["COST-102"].status == "should-fix"
    forged = _write_bundle(
        tmp_path / "forged",
        variance_status="pass",
        variance_pct=0.2501,
        max_forecast_variance_pct=0.25,
    )
    assert _findings(forged)["COST-102"].status == "not-verified"


def test_underspend_is_measured_by_magnitude_not_sign(tmp_path, monkeypatch) -> None:
    """A workload that costs 60% *less* than forecast has left the declared band
    just as much as one that costs 60% more — the model was wrong either way."""
    _freeze(monkeypatch)
    inside = _write_bundle(
        tmp_path,
        variance_status="pass",
        variance_pct=-0.10,
        max_forecast_variance_pct=0.25,
    )
    assert _findings(inside)["COST-102"].status == "pass"
    outside = _write_bundle(
        tmp_path / "outside",
        variance_status="should-fix",
        variance_pct=-0.60,
        max_forecast_variance_pct=0.25,
    )
    assert _findings(outside)["COST-102"].status == "should-fix"
    forged = _write_bundle(
        tmp_path / "forged",
        variance_status="pass",
        variance_pct=-0.60,
        max_forecast_variance_pct=0.25,
    )
    found = _findings(forged)
    assert found["COST-102"].status == "not-verified"
    assert CONTRADICTION_PHRASE in found["COST-102"].detail


def test_not_verified_variance_status_is_never_reclassified(
    tmp_path, monkeypatch
) -> None:
    """The consistency gate only ever *withholds* a verdict. A reconciler that
    already declined to decide stays declined, whatever the numbers say."""
    _freeze(monkeypatch)
    for variance in (0.01, 4.0):
        ctx = _write_bundle(
            tmp_path,
            variance_status="not-verified",
            variance_pct=variance,
            max_forecast_variance_pct=0.25,
        )
        found = _findings(ctx)
        assert found["COST-102"].status == "not-verified", variance
        assert CONTRADICTION_PHRASE not in found["COST-102"].detail


def test_contradiction_gate_never_fires_on_unusable_numbers(
    tmp_path, monkeypatch
) -> None:
    """A bool / NaN / infinite / string variance is malformed evidence, and a
    missing tolerance is no policy — those are reported as such, never as a
    contradiction (and never crash the comparison)."""
    _freeze(monkeypatch)
    for variance in (True, float("nan"), float("inf"), "12%"):
        ctx = _write_bundle(tmp_path, variance_status="pass", variance_pct=variance)
        found = _findings(ctx)
        assert found["COST-102"].status == "not-verified", variance
        assert CONTRADICTION_PHRASE not in found["COST-102"].detail
    ctx = _write_bundle(
        tmp_path,
        variance_status="pass",
        variance_pct=4.0,
        max_forecast_variance_pct=None,
    )
    found = _findings(ctx)
    assert found["COST-102"].status == "not-verified"
    assert CONTRADICTION_PHRASE not in found["COST-102"].detail


# --------------------------------------------------------------------------
# COST-103 — PAYG/PTU driver
# --------------------------------------------------------------------------


def test_driver_should_fix_asks_for_a_rerun_at_observed_volume(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    found = _findings(
        _write_bundle(
            tmp_path, payg_status="should-fix", observed_volume_variance_pct=0.55
        )
    )
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


# --------------------------------------------------------------------------
# COST-103 driver threshold integrity — threshold_pct must be the same
# producer value as policy_snapshot.max_token_volume_variance_pct
# --------------------------------------------------------------------------


INTERNAL_INCONSISTENCY_PHRASE = "Internal inconsistency"


def test_driver_threshold_mismatched_with_snapshot_cannot_pass(
    tmp_path, monkeypatch
) -> None:
    """A driver free to declare its own threshold independent of the SPEC-
    anchored snapshot could report `pass` at 99% band width while the SPEC
    only ever declared 30% — that must never be relayed as evidence."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        payg_status="pass",
        threshold_pct=0.99,
        max_token_volume_variance_pct=0.30,
        observed_volume_variance_pct=0.10,
    )
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert INTERNAL_INCONSISTENCY_PHRASE in found["COST-103"].detail
    assert "99.0%" in found["COST-103"].detail
    assert "30.0%" in found["COST-103"].detail
    # An unusable driver verdict never degrades the independent cost verdict.
    assert found["COST-102"].status == "pass"


def test_driver_threshold_equal_to_snapshot_boundary_passes(
    tmp_path, monkeypatch
) -> None:
    """Same producer value on both sides — including a value other than the
    fixture default — must not be withheld as a mismatch."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        payg_status="pass",
        threshold_pct=0.15,
        max_token_volume_variance_pct=0.15,
        observed_volume_variance_pct=0.15,
    )
    assert _findings(ctx)["COST-103"].status == "pass"


def test_driver_threshold_snapshot_missing_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, max_token_volume_variance_pct=None)
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert INTERNAL_INCONSISTENCY_PHRASE in found["COST-103"].detail


def test_driver_threshold_snapshot_boolean_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, max_token_volume_variance_pct=True)
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert INTERNAL_INCONSISTENCY_PHRASE in found["COST-103"].detail


def test_driver_threshold_snapshot_huge_int_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """A 400-digit JSON integer is a legal `int` but not a usable ratio — the
    equality check must reject it, never raise `OverflowError`."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, max_token_volume_variance_pct=10**400)
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert INTERNAL_INCONSISTENCY_PHRASE in found["COST-103"].detail


def test_driver_threshold_snapshot_out_of_range_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    for bad in (-0.1, 1.5, float("nan"), float("inf"), "0.3"):
        ctx = _write_bundle(tmp_path, max_token_volume_variance_pct=bad)
        found = _findings(ctx)
        assert found["COST-103"].status == "not-verified", bad
        assert INTERNAL_INCONSISTENCY_PHRASE in found["COST-103"].detail, bad


def test_driver_threshold_malformed_snapshot_block_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """`policy_snapshot` itself is not an object — no producer value at all.

    A non-object `policy_snapshot` also strips `max_window_end_age_days`, so
    the staleness gate in the loader withholds both findings before either
    per-finding check ever runs — still `not-verified`, never a crash."""
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    doc = _reconciliation(forecast, actuals, SPEC_TEXT)
    doc["policy_snapshot"] = "not an object"
    ctx = _write_bundle(tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc)
    _both_not_verified(ctx)


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
# COST-103 verdict integrity — the driver verdict must match its own numbers
# --------------------------------------------------------------------------


def test_driver_forged_pass_outside_the_declared_band_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        payg_status="pass",
        observed_volume_variance_pct=0.80,
        threshold_pct=0.30,
    )
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert CONTRADICTION_PHRASE in found["COST-103"].detail
    assert "80.0%" in found["COST-103"].detail
    assert "30.0%" in found["COST-103"].detail
    # The cost verdict is separate evidence and survives the driver degrade.
    assert found["COST-102"].status == "pass"


def test_driver_forged_should_fix_inside_the_declared_band_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        payg_status="should-fix",
        observed_volume_variance_pct=0.10,
        threshold_pct=0.30,
    )
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert CONTRADICTION_PHRASE in found["COST-103"].detail


def test_driver_volume_variance_exactly_at_the_band_is_a_pass(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        payg_status="pass",
        observed_volume_variance_pct=0.30,
        threshold_pct=0.30,
    )
    assert _findings(ctx)["COST-103"].status == "pass"
    forged = _write_bundle(
        tmp_path / "forged",
        payg_status="should-fix",
        observed_volume_variance_pct=0.30,
        threshold_pct=0.30,
    )
    assert _findings(forged)["COST-103"].status == "not-verified"


def test_driver_volume_shortfall_is_measured_by_magnitude(
    tmp_path, monkeypatch
) -> None:
    """Half the forecast volume invalidates a reserved-capacity recommendation
    as surely as double it does."""
    _freeze(monkeypatch)
    inside = _write_bundle(
        tmp_path,
        payg_status="pass",
        observed_volume_variance_pct=-0.20,
        threshold_pct=0.30,
    )
    assert _findings(inside)["COST-103"].status == "pass"
    outside = _write_bundle(
        tmp_path / "outside",
        payg_status="should-fix",
        observed_volume_variance_pct=-0.50,
        threshold_pct=0.30,
    )
    assert _findings(outside)["COST-103"].status == "should-fix"
    forged = _write_bundle(
        tmp_path / "forged",
        payg_status="pass",
        observed_volume_variance_pct=-0.50,
        threshold_pct=0.30,
    )
    assert _findings(forged)["COST-103"].status == "not-verified"


def test_driver_without_a_usable_observed_volume_variance_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    """Without the ratio the verdict claims to sit inside, nothing can be shown
    — and the missing number is reported as missing, not as a contradiction."""
    _freeze(monkeypatch)
    forecast, actuals = _forecast(), _actuals()
    for bad in (None, "10%", float("nan"), float("inf"), True):
        doc = _reconciliation(
            forecast, actuals, SPEC_TEXT, observed_volume_variance_pct=bad
        )
        ctx = _write_bundle(
            tmp_path, forecast=forecast, actuals=actuals, reconciliation=doc
        )
        found = _findings(ctx)
        assert found["COST-103"].status == "not-verified", bad
        assert CONTRADICTION_PHRASE not in found["COST-103"].detail, bad


def test_driver_not_verified_status_is_never_reclassified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        payg_status="not-verified",
        observed_volume_variance_pct=0.80,
        threshold_pct=0.30,
    )
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert CONTRADICTION_PHRASE not in found["COST-103"].detail


def test_driver_detail_reports_the_observed_volume_variance(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    passing = _findings(_write_bundle(tmp_path))["COST-103"].detail
    assert "10.0%" in passing
    drifting = _findings(
        _write_bundle(
            tmp_path / "drift",
            payg_status="should-fix",
            observed_volume_variance_pct=0.55,
        )
    )["COST-103"].detail
    assert "55.0%" in drifting


# --------------------------------------------------------------------------
# integration: the pillar emits each finding exactly once, live emits neither
# --------------------------------------------------------------------------


def _cost_pillar(ctx, *, static_only: bool, tiers: dict) -> list:
    findings, _evidence = pr._run_pillar(
        "cost", ctx, static_only=static_only, tiers=tiers,
        sub="sub-1", rg="rg-pilot", resolved_posture="", agt_profile="none",
        quick=False,
    )
    return findings


def test_static_cost_pillar_emits_each_finding_exactly_once(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    ids = [f.id for f in _cost_pillar(ctx, static_only=True, tiers={0: True})]
    assert ids.count("COST-102") == 1
    assert ids.count("COST-103") == 1


def test_cost_static_analyzer_no_longer_emits_the_reconciliation_findings(
    tmp_path, monkeypatch
) -> None:
    """They are produced by `_run_pillar` ahead of the ARM-shape analyzer, so
    the analyzer itself must not restate them (that would double-count)."""
    _freeze(monkeypatch)
    ids = [f.id for f in pr._check_cost_static(_write_bundle(tmp_path))]
    assert "COST-102" not in ids
    assert "COST-103" not in ids


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
    for tiers in ({0: True, 3: True}, {0: True, 3: False}):
        ids = [f.id for f in _cost_pillar(ctx, static_only=False, tiers=tiers)]
        assert ids.count("COST-102") == 1
        assert ids.count("COST-103") == 1
        assert ids.count("COST-101") == 1


def test_quick_mode_still_carries_exactly_one_of_each(tmp_path, monkeypatch) -> None:
    """`--quick` truncates the *live* leg; the artifact-driven findings are not
    part of it, so they are neither dropped nor duplicated."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    monkeypatch.setattr(pr, "_az_json", lambda *args: [])
    findings, _ = pr._run_pillar(
        "cost", ctx, static_only=False, tiers={0: True, 3: True},
        sub="sub-1", rg="rg-pilot", resolved_posture="", agt_profile="none",
        quick=True,
    )
    ids = [f.id for f in findings]
    assert ids.count("COST-102") == 1
    assert ids.count("COST-103") == 1
    by_id = {f.id: f for f in findings}
    assert by_id["COST-102"].status == "pass"
    assert by_id["COST-103"].status == "pass"


# --------------------------------------------------------------------------
# the reconciliation findings survive a crashing cost static analyzer
# --------------------------------------------------------------------------


def _break_cost_static(monkeypatch) -> None:
    """Induce a caught JSON-shape error inside `_check_cost_static`, at a point
    it already reaches before it ever produced COST-102/103."""

    def _boom(_manifest_data):
        raise TypeError("'str' object is not subscriptable")

    monkeypatch.setattr(pr, "_check_cost_007", _boom)


def test_reconciliation_findings_survive_a_crashing_cost_static_analyzer(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    _break_cost_static(monkeypatch)
    findings = _cost_pillar(ctx, static_only=True, tiers={0: True})
    ids = [f.id for f in findings]
    assert ids.count("COST-102") == 1
    assert ids.count("COST-103") == 1
    by_id = {f.id: f for f in findings}
    # Statuses still come from the artifact, not from the crash.
    assert by_id["COST-102"].status == "pass"
    assert by_id["COST-103"].status == "pass"
    assert "TypeError" not in by_id["COST-102"].detail
    assert "TypeError" not in by_id["COST-103"].detail


def test_a_crashing_cost_static_analyzer_still_fails_tier0_closed(
    tmp_path, monkeypatch
) -> None:
    """The reconciliation findings surviving must not weaken the fail-closed
    sweep: every tier-0 cost control still degrades to a gating must-fix."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    _break_cost_static(monkeypatch)
    findings = _cost_pillar(ctx, static_only=True, tiers={0: True})
    tier0 = [f for f in findings if f.pillar == "cost" and f.tier == 0]
    assert tier0
    assert all(f.status == "must-fix" for f in tier0), [
        (f.id, f.status) for f in tier0
    ]
    assert any("TypeError" in f.detail for f in tier0)
    assert pr._hard_gate_would_fail(findings) is True


def test_reconciliation_findings_survive_the_crash_on_the_live_path(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    monkeypatch.setattr(pr, "_az_json", lambda *args: [])
    _break_cost_static(monkeypatch)
    for tiers in ({0: True, 3: True}, {0: True, 3: False}):
        findings = _cost_pillar(ctx, static_only=False, tiers=tiers)
        ids = [f.id for f in findings]
        assert ids.count("COST-102") == 1, tiers
        assert ids.count("COST-103") == 1, tiers
        assert ids.count("COST-101") == 1, tiers
        by_id = {f.id: f for f in findings}
        assert by_id["COST-102"].status == "pass", tiers
        assert by_id["COST-103"].status == "pass", tiers


def test_crash_survivors_report_the_artifact_verdict_not_a_default(
    tmp_path, monkeypatch
) -> None:
    """With an unusable bundle the surviving findings are `not-verified` with
    the artifact's own reason — the crash never authors their verdict."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, status="not-verified", maturity="not-verified")
    _break_cost_static(monkeypatch)
    by_id = {f.id: f for f in _cost_pillar(ctx, static_only=True, tiers={0: True})}
    assert by_id["COST-102"].status == "not-verified"
    assert by_id["COST-103"].status == "not-verified"
    assert "maturity" in by_id["COST-102"].detail


def test_a_raising_artifact_reader_never_aborts_the_cost_pillar(
    tmp_path, monkeypatch
) -> None:
    """The reader runs outside the pillar-level guard now, so it carries its
    own: an unexpected shape degrades these two to `not-verified` and every
    other cost control still runs."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)

    def _boom(_ctx):
        raise TypeError("'int' object is not iterable")

    monkeypatch.setattr(pr, "_check_cost_reconciliation_static", _boom)
    findings = _cost_pillar(ctx, static_only=True, tiers={0: True})
    by_id = {f.id: f for f in findings}
    ids = [f.id for f in findings]
    assert ids.count("COST-102") == 1
    assert ids.count("COST-103") == 1
    assert by_id["COST-102"].status == "not-verified"
    assert by_id["COST-103"].status == "not-verified"
    assert "TypeError" in by_id["COST-102"].detail
    # The static analyzer still ran: COST-001 carries a real verdict, not the
    # fail-closed sweep's crash text.
    assert by_id["COST-001"].status == "pass"


def test_cost_007_meter_coverage_is_unaffected(tmp_path, monkeypatch) -> None:
    """The #116 meter-coverage verdict reads the forecast manifest only."""
    _freeze(monkeypatch)
    forecast = _forecast()
    forecast["meter_coverage"] = {"status": "complete"}
    forecast["resources"] = [{"logical_name": "aca", "pricing_status": "priced"}]
    ctx = _write_bundle(tmp_path, forecast=forecast)
    by_id = {f.id: f for f in _cost_pillar(ctx, static_only=True, tiers={0: True})}
    assert by_id["COST-007"].status == "pass"
    assert by_id["COST-102"].status == "pass"

    forecast_bad = _forecast()
    forecast_bad["meter_coverage"] = {"status": "complete"}
    forecast_bad["resources"] = [
        {"logical_name": "aca", "pricing_status": "not-priceable"}
    ]
    ctx_bad = _write_bundle(tmp_path / "bad", forecast=forecast_bad)
    by_id_bad = {
        f.id: f for f in _cost_pillar(ctx_bad, static_only=True, tiers={0: True})
    }
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


# --------------------------------------------------------------------------
# hostile giant-int / OverflowError robustness — a 400-digit JSON integer is
# a legal Python `int` that `float()` cannot represent (`OverflowError`, not
# a normal comparison). Every ratio/count conversion in this module is gated
# through `_is_finite_number`, which must reject such a value rather than
# raise — degrading only the affected finding to `not-verified`, never
# aborting `_run_pillar`.
# --------------------------------------------------------------------------


GIANT_INT = 10**400


def test_is_finite_number_never_raises_on_a_giant_int() -> None:
    assert pr._is_finite_number(GIANT_INT) is False
    assert pr._is_finite_number(-GIANT_INT) is False
    assert pr._is_finite_number(float("nan")) is False
    assert pr._is_finite_number(float("inf")) is False
    assert pr._is_finite_number(True) is False
    assert pr._is_finite_number(False) is False
    assert pr._is_finite_number(None) is False
    assert pr._is_finite_number("1e400") is False
    assert pr._is_finite_number(0.5) is True
    assert pr._is_finite_number(1) is True


def test_is_declared_ratio_never_raises_on_a_giant_int() -> None:
    assert pr._is_declared_ratio(GIANT_INT) is False
    assert pr._is_declared_ratio(-GIANT_INT) is False


def test_fmt_helpers_never_raise_on_a_giant_int() -> None:
    assert pr._fmt_ratio_pct(GIANT_INT) == "unknown"
    assert pr._fmt_token_volume(GIANT_INT) == "unknown"


def test_giant_variance_pct_degrades_cost102_only(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    _cost102_only_not_verified(_write_bundle(tmp_path, variance_pct=GIANT_INT))


def test_giant_forecast_tolerance_degrades_cost102_only(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    _cost102_only_not_verified(
        _write_bundle(tmp_path, max_forecast_variance_pct=GIANT_INT)
    )


def test_giant_observed_volume_variance_degrades_cost103_only(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    found = _findings(
        _write_bundle(tmp_path, observed_volume_variance_pct=GIANT_INT)
    )
    assert found["COST-103"].status == "not-verified"
    assert found["COST-102"].status == "pass"


def test_giant_driver_threshold_pct_is_not_verified(tmp_path, monkeypatch) -> None:
    """The driver's own threshold_pct as a 400-digit int — rejected before it
    ever reaches the snapshot equality check or the band comparison."""
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, threshold_pct=GIANT_INT)
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert found["COST-102"].status == "pass"


def test_giant_snapshot_token_threshold_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path, max_token_volume_variance_pct=GIANT_INT)
    found = _findings(ctx)
    assert found["COST-103"].status == "not-verified"
    assert INTERNAL_INCONSISTENCY_PHRASE in found["COST-103"].detail


def test_giant_max_window_end_age_days_is_not_verified(
    tmp_path, monkeypatch
) -> None:
    _freeze(monkeypatch)
    _both_not_verified(_write_bundle(tmp_path, max_window_end_age_days=GIANT_INT))


def test_giant_ints_in_every_reconciled_field_never_abort_the_full_run(
    tmp_path, monkeypatch
) -> None:
    """All five hostile fields at once, driven through the real pillar
    dispatcher (`_run_pillar`) rather than the finding functions directly —
    the full assessment must complete, degrading only the artifact-driven
    findings, never raising `OverflowError`/`ArithmeticError` out of the
    pillar."""
    _freeze(monkeypatch)
    ctx = _write_bundle(
        tmp_path,
        variance_pct=GIANT_INT,
        max_forecast_variance_pct=GIANT_INT,
        threshold_pct=GIANT_INT,
        max_token_volume_variance_pct=GIANT_INT,
        observed_volume_variance_pct=GIANT_INT,
        max_window_end_age_days=GIANT_INT,
    )
    findings = _cost_pillar(ctx, static_only=True, tiers={0: True})
    by_id = {f.id: f for f in findings}
    assert by_id["COST-102"].status == "not-verified"
    assert by_id["COST-103"].status == "not-verified"
    # The rest of the pillar still ran to completion (proof nothing aborted).
    assert "COST-001" in by_id


def test_default_time_helper_returns_aware_utc() -> None:
    now = pr._cost_reconciliation_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert not math.isnan(now.timestamp())
