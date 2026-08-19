"""
CLI tests for the opt-in `actuals` / `reconcile` commands and
`run --all --with-actuals`.

Two contracts are under test, and they pull in opposite directions:

1. **The default projection never changes.** `run --all` must still call
   exactly one helper (`_run_projection`), produce byte-identical forecast
   artefacts, write no actuals/reconciliation sidecars, and never reach
   Azure. Every assertion about that lives in "projection compatibility"
   below.
2. **Actuals are real when asked for.** `_phase_actuals` /
   `_phase_reconcile` / `_emit_actuals` are exercised against a fake source
   bundle and real temp files rather than being stubbed out, so the wiring
   (safe KQL, token identity injection, provenance hygiene, hashing,
   atomic writes) is covered rather than assumed.

Nothing here touches Azure: `collect_sources` is the single boundary and is
always monkeypatched or asserted un-called.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import consumption_iq  # noqa: E402
from actuals_sources import ActualsSourceError  # noqa: E402
from cost_actuals import ActualsEvidenceError  # noqa: E402
from emitter import emit_artefacts  # noqa: E402
from load_profile_wizard import load_or_prompt_profile  # noqa: E402
from projectors import project_resource  # noqa: E402
from recommender import score_and_rank  # noqa: E402
from reconcile import ReconciliationInputError, sha256_json  # noqa: E402
from reconciliation_emitter import EmissionValidationError  # noqa: E402
from value_model import ValueModelResult  # noqa: E402


SUB = "00000000-0000-0000-0000-000000000000"
RG = "rg-pilot"
RID = (
    f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/"
    "Microsoft.App/containerApps/agent"
)
AOAI_ACCOUNT = (
    f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/"
    "Microsoft.CognitiveServices/accounts/aoai-pilot"
)
AOAI_ACCOUNT_2 = (
    f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/"
    "Microsoft.CognitiveServices/accounts/aoai-ptu"
)
WORKSPACE_ID = (
    f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/"
    "Microsoft.OperationalInsights/workspaces/log-pilot"
)
START = "2026-08-01"
END = "2026-08-08"
PINNED_NOW = datetime(2026, 8, 10, 6, 30, 0, tzinfo=timezone.utc)
PINNED_GENERATED_AT = "2026-08-10T06:30:00Z"


SPEC_SECTION_14 = """\
## 13. Assumptions

Something.

---

## 14. Value Model

```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days: 7
      min_successful_interactions: 100
      min_cost_settlement_age_hours: 48
      max_window_end_age_days: 14
      min_projection_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied]
    baseline:
      target_cost_per_successful_interaction_usd: 1.0
      max_forecast_variance_pct: 0.20
      max_token_volume_variance_pct: 0.25
    accounting:
      actual_cost_basis: usage-pretax
      actual_billing_price_basis: retail
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```
"""


def _write_spec(tmp_path: Path, text: str = SPEC_SECTION_14) -> Path:
    spec = tmp_path / "SPEC.md"
    spec.write_text(text, encoding="utf-8")
    return spec


def _cost_page(rows: list[list[object]] | None = None) -> dict[str, object]:
    return {
        "properties": {
            "columns": [
                {"name": "PreTaxCost"},
                {"name": "UsageDate"},
                {"name": "ResourceId"},
                {"name": "ResourceType"},
                {"name": "ServiceName"},
                {"name": "Currency"},
            ],
            "rows": rows
            if rows is not None
            else [
                [
                    12.5,
                    20260801,
                    RID,
                    "microsoft.app/containerapps",
                    "Azure Container Apps",
                    "USD",
                ]
            ],
        }
    }


def _token_doc(deployment: str = "chat", model: str = "gpt-4o") -> dict[str, object]:
    def _metric(name: str, total: int) -> dict[str, object]:
        return {
            "name": {"value": name},
            "timeseries": [
                {
                    "metadatavalues": [
                        {"name": {"value": "ModelDeploymentName"}, "value": deployment},
                        {"name": {"value": "ModelName"}, "value": model},
                    ],
                    "data": [{"total": total}],
                }
            ],
        }

    return {"value": [_metric("InputTokens", 1200), _metric("OutputTokens", 340)]}


def _interaction_doc(total: int = 120, successful: int = 110) -> dict[str, object]:
    return {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [
                    {"name": "total_interactions", "type": "long"},
                    {"name": "successful_interactions", "type": "long"},
                ],
                "rows": [[total, successful]],
            }
        ]
    }


def _bundle(**overrides: object) -> dict[str, object]:
    bundle: dict[str, object] = {
        "subscription_id": SUB,
        "resource_group": RG,
        "window": {"start": START, "end": END},
        "azure_context": {"id": SUB, "tenantId": "t", "name": "pilot"},
        "cost_pages": [_cost_page()],
        "token_doc": _token_doc(),
        "token_source_resource_id": AOAI_ACCOUNT,
        "interaction_result": _interaction_doc(),
        "warnings": [],
    }
    bundle.update(overrides)
    return bundle


class _FakeCollector:
    """Records the kwargs `_phase_actuals` passes to the Azure boundary."""

    def __init__(self, bundle: dict[str, object] | None = None) -> None:
        self.bundle = bundle if bundle is not None else _bundle()
        self.calls: list[dict[str, object]] = []

    def __call__(self, subscription_id, resource_group, start, end, **kwargs):
        self.calls.append(
            {
                "subscription_id": subscription_id,
                "resource_group": resource_group,
                "start": start,
                "end": end,
                **kwargs,
            }
        )
        return self.bundle


def _pin_now(monkeypatch, instant: datetime = PINNED_NOW) -> None:
    monkeypatch.setattr(consumption_iq, "_utc_now", lambda: instant)


def _actuals_args(tmp_path: Path, spec: Path, **extra: str) -> object:
    argv = [
        "actuals",
        "--start", START,
        "--end", END,
        "--subscription", SUB,
        "--resource-group", RG,
        "--spec", str(spec),
        "--actuals-manifest", str(tmp_path / "specs" / "cost-actuals-manifest.json"),
    ]
    for flag, value in extra.items():
        argv.extend([f"--{flag.replace('_', '-')}", value])
    return consumption_iq.build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------


def test_parser_accepts_actuals_window_and_scope() -> None:
    args = consumption_iq.build_parser().parse_args([
        "actuals",
        "--start", "2026-08-01",
        "--end", "2026-08-08",
        "--subscription", "sub-1",
        "--resource-group", "rg-pilot",
        "--spec", "SPEC.md",
        "--actuals-manifest", "actuals.json",
    ])
    assert args.phase == "actuals"
    assert args.start == date(2026, 8, 1)
    assert args.end == date(2026, 8, 8)
    assert args.subscription == "sub-1"
    assert args.resource_group == "rg-pilot"
    assert str(args.spec) == "SPEC.md"
    assert str(args.actuals_manifest) == "actuals.json"
    # `--workspace-resource-id` is optional and was not passed above.
    assert args.workspace_resource_id is None


def test_parser_accepts_monitor_resource_id() -> None:
    args = consumption_iq.build_parser().parse_args([
        "actuals",
        "--start", "2026-08-01",
        "--end", "2026-08-08",
        "--subscription", "sub-1",
        "--resource-group", "rg-pilot",
        "--monitor-resource-id", AOAI_ACCOUNT,
        "--workspace-resource-id", WORKSPACE_ID,
    ])
    assert args.monitor_resource_id == AOAI_ACCOUNT
    assert args.workspace_resource_id == WORKSPACE_ID


def test_parser_monitor_resource_id_defaults_to_none() -> None:
    args = consumption_iq.build_parser().parse_args([
        "actuals", "--start", START, "--end", END,
        "--subscription", SUB, "--resource-group", RG,
    ])
    assert args.monitor_resource_id is None


def test_actuals_requires_start_and_end() -> None:
    with pytest.raises(SystemExit) as exc:
        consumption_iq.build_parser().parse_args([
            "actuals",
            "--subscription", "sub-1",
            "--resource-group", "rg-pilot",
        ])
    assert exc.value.code == 2


def test_actuals_subscription_and_resource_group_default_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-from-env")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-from-env")
    args = consumption_iq.build_parser().parse_args([
        "actuals", "--start", "2026-08-01", "--end", "2026-08-08",
    ])
    assert args.subscription == "sub-from-env"
    assert args.resource_group == "rg-from-env"
    # Defaults are per-parser-build; every command still gets the canonical
    # spec/manifest paths without needing to repeat them on the CLI.
    assert args.spec == consumption_iq.DEFAULT_SPEC_PATH
    assert args.actuals_manifest == consumption_iq.DEFAULT_ACTUALS_MANIFEST


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"AZURE_SUBSCRIPTION_ID": None, "AZURE_RESOURCE_GROUP": "rg-from-env"},
        {"AZURE_SUBSCRIPTION_ID": "sub-from-env", "AZURE_RESOURCE_GROUP": None},
        {"AZURE_SUBSCRIPTION_ID": None, "AZURE_RESOURCE_GROUP": None},
    ],
)
def test_actuals_missing_scope_after_env_resolution_exits_2(
    monkeypatch, env_overrides
) -> None:
    """Neither `--subscription`/`--resource-group` nor their environment
    fallbacks were provided; the command must exit 2 before attempting any
    Azure call, not raise or silently proceed with a `None` scope."""
    for key, value in env_overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("no Azure call may be attempted"),
    )
    rc = consumption_iq.main([
        "actuals", "--start", "2026-08-01", "--end", "2026-08-08",
    ])
    assert rc == 2


def test_parser_accepts_reconcile_paths() -> None:
    args = consumption_iq.build_parser().parse_args([
        "reconcile",
        "--forecast", "forecast.json",
        "--actuals-manifest", "actuals.json",
        "--spec", "SPEC.md",
        "--reconciliation-manifest", "reconciliation.json",
        "--report", "report.md",
    ])
    assert str(args.forecast) == "forecast.json"
    assert str(args.actuals_manifest) == "actuals.json"
    assert str(args.spec) == "SPEC.md"
    assert str(args.reconciliation_manifest) == "reconciliation.json"
    assert str(args.report) == "report.md"


def test_parser_reconcile_defaults_use_module_constants() -> None:
    args = consumption_iq.build_parser().parse_args(["reconcile"])
    assert args.forecast == consumption_iq.DEFAULT_OUTPUT_MANIFEST
    assert args.actuals_manifest == consumption_iq.DEFAULT_ACTUALS_MANIFEST
    assert args.spec == consumption_iq.DEFAULT_SPEC_PATH
    assert (
        args.reconciliation_manifest
        == consumption_iq.DEFAULT_RECONCILIATION_MANIFEST
    )
    assert args.report == consumption_iq.DEFAULT_RECONCILIATION_REPORT
    assert args.cost_history == consumption_iq.DEFAULT_COST_HISTORY


def test_parser_run_keeps_projection_paths_and_adds_sidecar_flags() -> None:
    args = consumption_iq.build_parser().parse_args(["run", "--all"])
    assert args.with_actuals is False
    # The projection outputs keep their own, unchanged flags…
    assert args.report == consumption_iq.DEFAULT_OUTPUT_REPORT
    assert args.manifest == consumption_iq.DEFAULT_OUTPUT_MANIFEST
    # …and the actuals/reconciliation sidecars get separate ones.
    assert args.actuals_manifest == consumption_iq.DEFAULT_ACTUALS_MANIFEST
    assert args.reconciliation_report == consumption_iq.DEFAULT_RECONCILIATION_REPORT
    assert (
        args.reconciliation_manifest
        == consumption_iq.DEFAULT_RECONCILIATION_MANIFEST
    )
    assert args.cost_history == consumption_iq.DEFAULT_COST_HISTORY


def test_existing_commands_still_parse() -> None:
    for phase in ("discover", "load-profile", "price", "project", "recommend", "emit"):
        args = consumption_iq.build_parser().parse_args([phase])
        assert args.phase == phase
    estimate = consumption_iq.build_parser().parse_args(
        ["estimate", "--rollout", "r.json"]
    )
    assert estimate.manifest == consumption_iq.DEFAULT_ESTIMATE_MANIFEST


# ---------------------------------------------------------------------------
# Scope / pre-deploy validation
# ---------------------------------------------------------------------------


def test_run_all_with_actuals_requires_start_and_end(monkeypatch) -> None:
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ])
    assert rc == 2


def test_pre_deploy_with_actuals_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_run_projection",
        lambda args: pytest.fail("validation must run before projection"),
    )
    rc = consumption_iq.main([
        "run", "--all", "--pre-deploy", "--with-actuals",
        "--start", START, "--end", END,
        "--subscription", SUB, "--resource-group", RG,
    ])
    assert rc == 2


def test_scope_is_validated_before_any_projection_or_azure_call(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
    monkeypatch.setattr(
        consumption_iq,
        "_run_projection",
        lambda args: pytest.fail("scope must be validated before projection"),
    )
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("no Azure call may be attempted"),
    )
    assert consumption_iq.main([
        "run", "--all", "--with-actuals", "--start", START, "--end", END,
    ]) == 2


# ---------------------------------------------------------------------------
# Dispatch order / projection compatibility
# ---------------------------------------------------------------------------


def test_run_all_default_does_not_call_actuals(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        consumption_iq, "_run_projection", lambda args: calls.append("projection")
    )
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: (_ for _ in ()).throw(AssertionError("actuals must be opt-in")),
    )
    assert consumption_iq.main(["run", "--all"]) == 0
    assert calls == ["projection"]


def test_run_all_verbose_prints_emitted_paths(monkeypatch, capsys) -> None:
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    rc = consumption_iq.main(["run", "--all", "--verbose"])
    assert rc == 0
    assert "emitted" in capsys.readouterr().err


def test_run_all_with_actuals_calls_projection_then_actuals(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        consumption_iq, "_run_projection", lambda args: calls.append("projection")
    )
    monkeypatch.setattr(
        consumption_iq, "_phase_actuals", lambda args: calls.append("actuals")
    )
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: calls.append("reconcile") or {"status": "pass"},
    )
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    monkeypatch.setattr(
        consumption_iq, "_emit_reconciliation", lambda args, result: None
    )
    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ])
    assert rc == 0
    assert calls == ["projection", "actuals", "reconcile"]


def test_incomplete_maturity_returns_exit_5_after_emit(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    monkeypatch.setattr(consumption_iq, "_phase_actuals", lambda args: {})
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: {"status": "not-verified"},
    )
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result: emitted.append(result),
    )
    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ])
    assert rc == 5
    assert emitted == [{"status": "not-verified"}]


def test_interaction_query_failure_still_returns_exit_0_for_actuals(monkeypatch) -> None:
    """A failed workspace interaction query does not invalidate the cost
    artifact (RFC §7.2/§11): the `actuals` command emitted a valid
    `status: pass` manifest with `usage.interaction_status: not-verified`, so
    it returns 0."""
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {
            "status": "pass",
            "usage": {"interaction_status": "not-verified"},
            "warnings": ["logs forbidden"],
        },
    )
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 0


def test_interaction_query_failure_returns_exit_5_for_reconcile(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: {
            "status": "not-verified",
            "unit_economics": {"status": "not-verified"},
        },
    )
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result: emitted.append(result),
    )
    assert consumption_iq.main(["reconcile"]) == 5
    # Emit happens first; the non-zero exit only reports the verdict.
    assert emitted and emitted[0]["status"] == "not-verified"


def test_unverified_actuals_status_returns_exit_5_after_emit(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {"status": "not-verified", "warnings": ["cost parse failed"]},
    )
    monkeypatch.setattr(
        consumption_iq, "_emit_actuals", lambda args, result: emitted.append(result)
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 5
    assert emitted


def test_incomplete_policy_emits_before_exiting_5(monkeypatch) -> None:
    """RFC §12 / Task 5: an incomplete or invalid section 14 is no longer an
    early exit. The policy errors flow into `reconcile_costs`, a
    `not-verified` manifest is written, and only then is 5 returned."""
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_load_policy",
        lambda args: ValueModelResult(policy={}, errors=["cost.baseline is missing"]),
    )
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: {
            "status": "not-verified",
            "policy_errors": ["cost.baseline is missing"],
        },
    )
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result: emitted.append(result),
    )
    assert consumption_iq.main(["reconcile"]) == 5
    assert emitted[0]["policy_errors"] == ["cost.baseline is missing"]


def test_unsafe_success_identifier_skips_the_query_without_aborting(monkeypatch) -> None:
    """An unsafe identifier is a policy validation error, so the interaction
    query is skipped — but raw actuals are still collected and emitted."""
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_load_policy",
        lambda args: ValueModelResult(
            policy={},
            errors=['cost.success_event.success_values[0] invalid'],
        ),
    )
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {
            "status": "pass",
            "usage": {"interaction_status": "not-verified"},
            "warnings": ["success event identifier rejected; query skipped"],
        },
    )
    monkeypatch.setattr(
        consumption_iq, "_emit_actuals", lambda args, result: emitted.append(result)
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 0
    assert emitted[0]["status"] == "pass"


# ---------------------------------------------------------------------------
# `_phase_actuals` — real wiring against a fake source bundle
# ---------------------------------------------------------------------------


def test_phase_actuals_builds_a_pass_manifest(monkeypatch, tmp_path) -> None:
    collector = _FakeCollector()
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    spec = _write_spec(tmp_path)

    document = consumption_iq._phase_actuals(
        _actuals_args(
            tmp_path,
            spec,
            monitor_resource_id=AOAI_ACCOUNT,
            workspace_resource_id=WORKSPACE_ID,
        )
    )

    assert document["schema"] == "threadlight-cost-actuals/v1"
    assert document["status"] == "pass"
    assert document["generated_at"] == PINNED_GENERATED_AT
    assert document["window"]["start"] == "2026-08-01T00:00:00Z"
    assert document["window"]["end"] == "2026-08-08T00:00:00Z"
    assert document["window"]["complete_days"] == 7
    assert document["cost"]["period_total_usd"] == 12.5
    assert document["usage"]["interaction_status"] == "pass"
    assert document["usage"]["total_interactions"] == 120
    assert document["usage"]["model_attribution_status"] == "pass"

    call = collector.calls[0]
    assert call["subscription_id"] == SUB
    assert call["resource_group"] == RG
    assert call["start"] == date(2026, 8, 1)
    assert call["end"] == date(2026, 8, 8)
    assert call["monitor_resource_id"] == AOAI_ACCOUNT
    assert call["workspace_resource_id"] == WORKSPACE_ID


def test_phase_actuals_scope_records_dedicated_resource_group(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(consumption_iq, "collect_sources", _FakeCollector())
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, _write_spec(tmp_path))
    )
    assert document["scope"] == {
        "subscription_id": SUB,
        "resource_group": RG,
        "dedicated_to_workload": True,
    }


def test_phase_actuals_scope_is_unknown_when_policy_is_invalid(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(consumption_iq, "collect_sources", _FakeCollector())
    _pin_now(monkeypatch)
    spec = _write_spec(tmp_path, "## 14. Value Model\n\nNo yaml here.\n")
    document = consumption_iq._phase_actuals(_actuals_args(tmp_path, spec))
    assert document["scope"]["dedicated_to_workload"] is None


def test_phase_actuals_builds_a_safe_success_kql(monkeypatch, tmp_path) -> None:
    collector = _FakeCollector()
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    consumption_iq._phase_actuals(
        _actuals_args(
            tmp_path, _write_spec(tmp_path), workspace_resource_id=WORKSPACE_ID
        )
    )
    kql = collector.calls[0]["kql"]
    assert "AppTraces" in kql
    assert 'Message == "return_decision_completed"' in kql
    assert "datetime(2026-08-01T00:00:00Z)" in kql
    assert "datetime(2026-08-08T00:00:00Z)" in kql
    assert '"approved", "denied"' in kql


def test_phase_actuals_skips_the_query_when_the_success_event_is_unsafe(
    monkeypatch, tmp_path
) -> None:
    collector = _FakeCollector(_bundle(interaction_result=None))
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    spec = _write_spec(
        tmp_path, SPEC_SECTION_14.replace("name: return_decision_completed", "name: 'a b'")
    )
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, spec, workspace_resource_id=WORKSPACE_ID)
    )
    assert collector.calls[0]["kql"] is None
    assert document["status"] == "pass"
    assert document["usage"]["interaction_status"] == "not-verified"
    assert any("interaction" in warning for warning in document["warnings"])


def test_phase_actuals_collects_cost_even_when_maturity_policy_is_invalid(
    monkeypatch, tmp_path
) -> None:
    """A broken maturity/baseline/accounting block is a `reconcile` concern;
    it must not suppress the interaction query or the cost collection."""
    collector = _FakeCollector()
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    spec = _write_spec(
        tmp_path, SPEC_SECTION_14.replace("min_complete_days: 7", "min_complete_days: -3")
    )
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, spec, workspace_resource_id=WORKSPACE_ID)
    )
    assert collector.calls[0]["kql"] is not None
    assert document["status"] == "pass"
    assert document["usage"]["interaction_status"] == "pass"


def test_phase_actuals_injects_the_token_account_identity(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(consumption_iq, "collect_sources", _FakeCollector())
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, _write_spec(tmp_path), monitor_resource_id=AOAI_ACCOUNT)
    )
    models = document["usage"]["models"]
    assert models and all(
        row["account_resource_id"] == AOAI_ACCOUNT and row["resource_id"] == AOAI_ACCOUNT
        for row in models
    )
    assert models[0]["deployment"] == "chat"
    assert models[0]["model"] == "gpt-4o"
    assert models[0]["input_tokens"] == 1200


def test_phase_actuals_scopes_token_rows_per_account(monkeypatch, tmp_path) -> None:
    """Multi-account PAYG/PTU: the same deployment name observed on a second
    account must not be attributed to the first."""
    _pin_now(monkeypatch)
    spec = _write_spec(tmp_path)
    documents = []
    for account in (AOAI_ACCOUNT, AOAI_ACCOUNT_2):
        monkeypatch.setattr(
            consumption_iq,
            "collect_sources",
            _FakeCollector(_bundle(token_source_resource_id=account)),
        )
        documents.append(
            consumption_iq._phase_actuals(
                _actuals_args(tmp_path, spec, monitor_resource_id=account)
            )
        )
    first, second = (doc["usage"]["models"][0] for doc in documents)
    assert first["deployment"] == second["deployment"] == "chat"
    assert first["resource_id"] == AOAI_ACCOUNT
    assert second["resource_id"] == AOAI_ACCOUNT_2


def test_phase_actuals_degrades_on_unparsable_token_evidence(
    monkeypatch, tmp_path
) -> None:
    collector = _FakeCollector(_bundle(token_doc={"value": "not-a-list"}))
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, _write_spec(tmp_path), monitor_resource_id=AOAI_ACCOUNT)
    )
    assert document["status"] == "pass"
    assert document["cost"]["period_total_usd"] == 12.5
    assert document["usage"]["model_attribution_status"] == "not-verified"
    assert document["usage"]["models"] == []
    assert any("token" in warning for warning in document["warnings"])


def test_phase_actuals_degrades_on_unparsable_interaction_evidence(
    monkeypatch, tmp_path
) -> None:
    collector = _FakeCollector(_bundle(interaction_result={"tables": []}))
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, _write_spec(tmp_path), workspace_resource_id=WORKSPACE_ID)
    )
    assert document["status"] == "pass"
    assert document["usage"]["interaction_status"] == "not-verified"
    assert document["usage"]["total_interactions"] is None
    assert any("interaction" in warning for warning in document["warnings"])


def test_phase_actuals_keeps_source_warnings(monkeypatch, tmp_path) -> None:
    collector = _FakeCollector(
        _bundle(token_doc=None, warnings=["token metrics unavailable: forbidden"])
    )
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, _write_spec(tmp_path))
    )
    assert "token metrics unavailable: forbidden" in document["warnings"]


def test_phase_actuals_provenance_is_safe_evidence_only(monkeypatch, tmp_path) -> None:
    collector = _FakeCollector()
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(
            tmp_path,
            _write_spec(tmp_path),
            monitor_resource_id=AOAI_ACCOUNT,
            workspace_resource_id=WORKSPACE_ID,
        )
    )
    provenance = document["provenance"]
    assert provenance["query_api_version"] == "2025-03-01"
    assert provenance["monitor_resource_id"] == AOAI_ACCOUNT
    assert provenance["workspace_resource_id"] == WORKSPACE_ID
    assert provenance["collected_at"] == PINNED_GENERATED_AT
    assert provenance["window"] == {"start": "2026-08-01", "end": "2026-08-08"}

    # No raw evidence document, token value or credential material may travel
    # into a committed artifact.
    serialized = json.dumps(provenance)
    for leaked in ("PreTaxCost", "rows", "columns", "tenantId", "InputTokens", "1200"):
        assert leaked not in serialized


def test_phase_actuals_propagates_a_cost_source_failure(monkeypatch, tmp_path) -> None:
    def _boom(*args, **kwargs):
        raise ActualsSourceError("Cost Management query forbidden")

    monkeypatch.setattr(consumption_iq, "collect_sources", _boom)
    _pin_now(monkeypatch)
    with pytest.raises(ActualsSourceError):
        consumption_iq._phase_actuals(_actuals_args(tmp_path, _write_spec(tmp_path)))


# ---------------------------------------------------------------------------
# `_emit_actuals` — strict, atomic, no history
# ---------------------------------------------------------------------------


def _emit_args(destination: Path):
    return consumption_iq.build_parser().parse_args([
        "actuals",
        "--start", START, "--end", END,
        "--subscription", SUB, "--resource-group", RG,
        "--actuals-manifest", str(destination),
    ])


def _minimal_actuals(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": "threadlight-cost-actuals/v1",
        "generated_at": PINNED_GENERATED_AT,
        "status": "pass",
        "scope": {"subscription_id": SUB, "resource_group": RG},
        "window": {
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
            "complete_days": 7,
            "settlement_age_hours": 54,
            "window_end_age_days": 2,
        },
        "cost": {
            "basis": "usage-pretax",
            "cost_column": "PreTaxCost",
            "currency": "USD",
            "period_total_usd": 12.5,
            "resources": [
                {
                    "resource_id": RID,
                    "resource_type": "microsoft.app/containerapps",
                    "service_name": "Azure Container Apps",
                    "period_cost_usd": 12.5,
                }
            ],
            "unattributed_usd": 0.0,
            "resource_id_coverage_pct": 1.0,
        },
        "usage": {
            "interaction_status": "pass",
            "model_attribution_status": "not-verified",
            "total_interactions": 120,
            "successful_interactions": 110,
            "success_predicate_ref": "SPEC.md#section-14-value-model",
            "models": [],
        },
        "provenance": {"query_api_version": "2025-03-01"},
        "warnings": [],
    }
    document.update(overrides)
    return document


def test_emit_actuals_writes_canonical_json(tmp_path) -> None:
    destination = tmp_path / "specs" / "cost-actuals-manifest.json"
    document = _minimal_actuals()
    consumption_iq._emit_actuals(_emit_args(destination), document)

    text = destination.read_text(encoding="utf-8")
    assert text == json.dumps(
        document, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False
    ) + "\n"
    assert json.loads(text) == document


def test_emit_actuals_leaves_no_temp_files_and_sets_mode(tmp_path) -> None:
    destination = tmp_path / "specs" / "cost-actuals-manifest.json"
    consumption_iq._emit_actuals(_emit_args(destination), _minimal_actuals())
    assert [p.name for p in destination.parent.iterdir()] == [destination.name]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644


def test_emit_actuals_writes_no_history(tmp_path) -> None:
    destination = tmp_path / "specs" / "cost-actuals-manifest.json"
    consumption_iq._emit_actuals(_emit_args(destination), _minimal_actuals())
    assert not (tmp_path / "specs" / "cost-history").exists()
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [
        "cost-actuals-manifest.json"
    ]


@pytest.mark.parametrize(
    "document",
    [
        ["not", "a", "mapping"],
        None,
        {"schema": "threadlight-cost-actuals/v2"},
        {},
    ],
)
def test_emit_actuals_rejects_a_non_publishable_document(tmp_path, document) -> None:
    destination = tmp_path / "cost-actuals-manifest.json"
    with pytest.raises(EmissionValidationError):
        consumption_iq._emit_actuals(_emit_args(destination), document)
    assert not destination.exists()


def test_emit_actuals_rejects_non_finite_numbers_without_corrupting(tmp_path) -> None:
    destination = tmp_path / "cost-actuals-manifest.json"
    consumption_iq._emit_actuals(_emit_args(destination), _minimal_actuals())
    original = destination.read_bytes()

    poisoned = _minimal_actuals()
    poisoned["cost"]["period_total_usd"] = float("nan")
    with pytest.raises(EmissionValidationError):
        consumption_iq._emit_actuals(_emit_args(destination), poisoned)

    assert destination.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == [destination.name]


# ---------------------------------------------------------------------------
# `_phase_reconcile` / `_emit_reconciliation` — offline, hashed, auditable
# ---------------------------------------------------------------------------


def _forecast(total: float = 300.0) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "price_basis": "retail",
        "totals": {"monthly_cost_current_usd": total},
        "resources": [
            {
                "resource_id": RID,
                "resource_kind": "Microsoft.App/containerApps",
                "monthly_cost_usd": total,
            }
        ],
        "recommendations": [],
    }


def _reconcile_args(tmp_path: Path, spec_text: str = SPEC_SECTION_14):
    specs = tmp_path / "specs"
    specs.mkdir(exist_ok=True)
    forecast_path = specs / "cost-manifest.json"
    actuals_path = specs / "cost-actuals-manifest.json"
    spec_path = _write_spec(tmp_path, spec_text)
    forecast_path.write_text(json.dumps(_forecast()), encoding="utf-8")
    actuals_path.write_text(json.dumps(_minimal_actuals()), encoding="utf-8")
    return consumption_iq.build_parser().parse_args([
        "reconcile",
        "--forecast", str(forecast_path),
        "--actuals-manifest", str(actuals_path),
        "--spec", str(spec_path),
        "--reconciliation-manifest", str(specs / "cost-reconciliation-manifest.json"),
        "--report", str(tmp_path / "docs" / "cost-reconciliation.md"),
        "--cost-history", str(specs / "cost-history"),
    ])


def test_phase_reconcile_never_calls_a_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("reconcile must issue no Azure call"),
    )
    result = consumption_iq._phase_reconcile(_reconcile_args(tmp_path))
    assert result["schema"] == "threadlight-cost-reconciliation/v1"
    assert result["status"] in {"pass", "not-verified"}


def test_phase_reconcile_hashes_raw_spec_bytes_and_both_documents(tmp_path) -> None:
    args = _reconcile_args(tmp_path)
    result = consumption_iq._phase_reconcile(args)

    assert result["policy_ref"]["spec_sha256"] == hashlib.sha256(
        Path(args.spec).read_bytes()
    ).hexdigest()
    assert result["forecast_ref"]["sha256"] == sha256_json(_forecast())
    assert result["actuals_ref"]["sha256"] == sha256_json(_minimal_actuals())
    # The reconciliation is published beside the actuals it reconciles, so it
    # carries that document's instant, not a fresh one.
    assert result["generated_at"] == PINNED_GENERATED_AT


def test_phase_reconcile_carries_policy_errors(tmp_path) -> None:
    broken = SPEC_SECTION_14.replace("max_forecast_variance_pct: 0.20", "")
    result = consumption_iq._phase_reconcile(_reconcile_args(tmp_path, broken))
    assert result["policy_errors"]
    assert result["status"] == "not-verified"


def test_phase_reconcile_rejects_stale_actuals_evidence(tmp_path) -> None:
    args = _reconcile_args(tmp_path)
    stale = _minimal_actuals(status="not-verified")
    Path(args.actuals_manifest).write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(ReconciliationInputError):
        consumption_iq._phase_reconcile(args)


def test_phase_reconcile_rejects_a_foreign_schema(tmp_path) -> None:
    args = _reconcile_args(tmp_path)
    foreign = _minimal_actuals(schema="threadlight-cost-actuals/v2")
    Path(args.actuals_manifest).write_text(json.dumps(foreign), encoding="utf-8")
    with pytest.raises(ReconciliationInputError):
        consumption_iq._phase_reconcile(args)


def test_emit_reconciliation_publishes_the_pair_and_history(tmp_path) -> None:
    args = _reconcile_args(tmp_path)
    result = consumption_iq._phase_reconcile(args)
    consumption_iq._emit_reconciliation(args, result)

    manifest = json.loads(Path(args.reconciliation_manifest).read_text())
    assert manifest["schema"] == "threadlight-cost-reconciliation/v1"
    assert Path(args.report).exists()
    history = list(Path(args.cost_history).rglob("reconciliation.json"))
    assert len(history) == 1


def test_reconcile_command_emits_then_reports_the_verdict(tmp_path) -> None:
    args = _reconcile_args(tmp_path)
    rc = consumption_iq.main([
        "reconcile",
        "--forecast", str(args.forecast),
        "--actuals-manifest", str(args.actuals_manifest),
        "--spec", str(args.spec),
        "--reconciliation-manifest", str(args.reconciliation_manifest),
        "--report", str(args.report),
        "--cost-history", str(args.cost_history),
    ])
    assert rc in {0, 5}
    assert Path(args.reconciliation_manifest).exists()
    assert Path(args.report).exists()


# ---------------------------------------------------------------------------
# Projection compatibility — byte-for-byte, no sidecars, no Azure
# ---------------------------------------------------------------------------


FIXTURE_DIR = HERE.parent / "references" / "fixtures" / "sample-pilot-consumption"
PINNED_TIMESTAMP = "2026-06-12T12:00:00+00:00"


class _DeterministicPricing:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_price(self, resource_kind, sku):
        return {
            "unit_price_usd": None,
            "unit": None,
            "price_source": "fallback",
            "fetched_at": None,
            "azure_meter_id": None,
            "raw": {},
        }

    def warm(self, resource):
        return None


class _PinnedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat(PINNED_TIMESTAMP)


_PROJECTION_RESOURCES = [
    {
        "resource_kind": "Microsoft.App/containerApps",
        "resource_id": RID,
        "logical_name": "agent",
        "region": "eastus2",
        "current_sku": {
            "name": "Consumption",
            "tier": "Consumption",
            "region": "eastus2",
            "extra": {
                "vcpu": 0.5,
                "memory_gib": 1.0,
                "min_replicas": 1,
                "max_replicas": 10,
            },
        },
    }
]


def _reference_projection(spec: Path, report: Path, manifest: Path) -> None:
    profile = load_or_prompt_profile(spec_path=spec, non_interactive=True)
    pricing = _DeterministicPricing()
    projected = [
        project_resource(resource, profile, pricing)
        for resource in _PROJECTION_RESOURCES
    ]
    with patch("emitter.datetime", _PinnedDatetime):
        emit_artefacts(
            projected=projected,
            recommendations=score_and_rank(projected, profile),
            load_profile=profile,
            report_path=report,
            manifest_path=manifest,
            deploy_ref=consumption_iq._resolve_deploy_ref(False),
            pre_deploy=False,
        )


def test_run_all_forecast_is_byte_identical_and_writes_no_sidecars(
    monkeypatch, tmp_path
) -> None:
    spec = FIXTURE_DIR / "specs" / "SPEC.md"
    monkeypatch.setenv("AZURE_ENV_NAME", "pinned-env")
    monkeypatch.setenv("AZURE_DEPLOYMENT_ID", "pinned-deployment")
    monkeypatch.setattr(
        consumption_iq, "_phase_discover", lambda args: list(_PROJECTION_RESOURCES)
    )
    monkeypatch.setattr(consumption_iq, "PricingClient", _DeterministicPricing)
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("the default projection must not reach Azure"),
    )

    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    _reference_projection(
        spec, reference_dir / "cost-projection.md", reference_dir / "cost-manifest.json"
    )

    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()
    with patch("emitter.datetime", _PinnedDatetime):
        rc = consumption_iq.main([
            "run", "--all",
            "--spec", str(spec),
            "--report", str(cli_dir / "cost-projection.md"),
            "--manifest", str(cli_dir / "cost-manifest.json"),
        ])
    assert rc == 0

    for name in ("cost-projection.md", "cost-manifest.json"):
        assert (cli_dir / name).read_bytes() == (reference_dir / name).read_bytes()
    assert sorted(p.name for p in cli_dir.iterdir()) == [
        "cost-manifest.json",
        "cost-projection.md",
    ]


def test_no_module_import_touches_azure(monkeypatch) -> None:
    """Importing the CLI must not shell out; `collect_sources` is the only
    Azure boundary and it is never reached without `--with-actuals`."""
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: pytest.fail("no subprocess may run on import/dispatch"),
    )
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    assert consumption_iq.main(["run", "--all"]) == 0


# ---------------------------------------------------------------------------
# Exception → exit-code mapping
# ---------------------------------------------------------------------------


def test_cost_source_failure_returns_exit_3(monkeypatch) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: (_ for _ in ()).throw(ActualsSourceError("forbidden")),
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 3


def test_unusable_cost_evidence_returns_exit_3(monkeypatch) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: (_ for _ in ()).throw(ActualsEvidenceError("no pages")),
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 3


def test_emitter_failure_returns_exit_3(monkeypatch) -> None:
    monkeypatch.setattr(consumption_iq, "_phase_actuals", lambda args: {})
    monkeypatch.setattr(
        consumption_iq,
        "_emit_actuals",
        lambda args, result: (_ for _ in ()).throw(
            EmissionValidationError("not publishable")
        ),
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 3


def test_io_failure_returns_exit_3(monkeypatch) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert consumption_iq.main(["reconcile"]) == 3


def test_missing_local_input_returns_exit_2(tmp_path) -> None:
    assert consumption_iq.main([
        "reconcile",
        "--forecast", str(tmp_path / "nope.json"),
        "--actuals-manifest", str(tmp_path / "nope-actuals.json"),
        "--spec", str(tmp_path / "nope-spec.md"),
    ]) == 2


def test_invalid_local_json_returns_exit_2(tmp_path) -> None:
    forecast = tmp_path / "cost-manifest.json"
    forecast.write_text("{ not json", encoding="utf-8")
    actuals = tmp_path / "cost-actuals-manifest.json"
    actuals.write_text(json.dumps(_minimal_actuals()), encoding="utf-8")
    assert consumption_iq.main([
        "reconcile",
        "--forecast", str(forecast),
        "--actuals-manifest", str(actuals),
        "--spec", str(_write_spec(tmp_path)),
    ]) == 2


def test_invalid_actuals_evidence_returns_exit_2(tmp_path) -> None:
    forecast = tmp_path / "cost-manifest.json"
    forecast.write_text(json.dumps(_forecast()), encoding="utf-8")
    actuals = tmp_path / "cost-actuals-manifest.json"
    actuals.write_text(json.dumps({"schema": "threadlight-cost-actuals/v1"}), encoding="utf-8")
    assert consumption_iq.main([
        "reconcile",
        "--forecast", str(forecast),
        "--actuals-manifest", str(actuals),
        "--spec", str(_write_spec(tmp_path)),
    ]) == 2


def test_no_broad_exception_handler_in_dispatch() -> None:
    source = (SCRIPTS / "consumption_iq.py").read_text(encoding="utf-8")
    assert "except Exception" not in source
    assert "except BaseException" not in source


def test_environment_default_lookup_does_not_leak_between_parsers(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-env")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-env")
    args = consumption_iq.build_parser().parse_args(["run", "--all"])
    assert args.subscription == "sub-env"
    assert args.resource_group == "rg-env"
    assert args.with_actuals is False


def test_run_with_actuals_end_to_end_writes_every_artifact(monkeypatch, tmp_path) -> None:
    """The opt-in path, wired for real: projection stubbed, sources faked,
    every downstream artifact actually written."""
    spec = FIXTURE_DIR / "specs" / "SPEC.md"
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    monkeypatch.setattr(consumption_iq, "collect_sources", _FakeCollector())
    _pin_now(monkeypatch)

    specs = tmp_path / "specs"
    specs.mkdir()
    forecast_path = specs / "cost-manifest.json"
    forecast_path.write_text(json.dumps(_forecast()), encoding="utf-8")

    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--start", START, "--end", END,
        "--subscription", SUB, "--resource-group", RG,
        "--spec", str(spec),
        "--manifest", str(forecast_path),
        "--report", str(tmp_path / "docs" / "cost-projection.md"),
        "--actuals-manifest", str(specs / "cost-actuals-manifest.json"),
        "--reconciliation-manifest", str(specs / "cost-reconciliation-manifest.json"),
        "--reconciliation-report", str(tmp_path / "docs" / "cost-reconciliation.md"),
        "--cost-history", str(specs / "cost-history"),
    ])

    assert rc in {0, 5}
    actuals_document = json.loads((specs / "cost-actuals-manifest.json").read_text())
    assert actuals_document["status"] == "pass"
    assert (specs / "cost-reconciliation-manifest.json").exists()
    assert (tmp_path / "docs" / "cost-reconciliation.md").exists()
    assert list((specs / "cost-history").rglob("actuals.json"))
