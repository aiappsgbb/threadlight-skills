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
import reconciliation_emitter as emitter  # noqa: E402
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
# A later instant, used wherever a reconciliation is computed over evidence
# that was collected earlier — which is every re-reconciliation.
PINNED_RECONCILED_NOW = datetime(2026, 8, 12, 9, 15, 0, tzinfo=timezone.utc)
PINNED_RECONCILED_AT = "2026-08-12T09:15:00Z"


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
        "token_query_issued": True,
        "interaction_result": _interaction_doc(),
        "interaction_query_issued": True,
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
        "--expect-subscription", "sub-1",
        "--expect-resource-group", "rg-pilot",
        "--reconciliation-manifest", "reconciliation.json",
        "--report", "report.md",
    ])
    assert str(args.forecast) == "forecast.json"
    assert str(args.actuals_manifest) == "actuals.json"
    assert str(args.spec) == "SPEC.md"
    assert args.expect_subscription == "sub-1"
    assert args.expect_resource_group == "rg-pilot"
    assert str(args.reconciliation_manifest) == "reconciliation.json"
    assert str(args.report) == "report.md"


def test_parser_reconcile_defaults_use_module_constants() -> None:
    args = consumption_iq.build_parser().parse_args(["reconcile"])
    assert args.forecast == consumption_iq.DEFAULT_OUTPUT_MANIFEST
    assert args.actuals_manifest == consumption_iq.DEFAULT_ACTUALS_MANIFEST
    assert args.spec == consumption_iq.DEFAULT_SPEC_PATH
    assert args.expect_subscription is None
    assert args.expect_resource_group is None
    assert (
        args.reconciliation_manifest
        == consumption_iq.DEFAULT_RECONCILIATION_MANIFEST
    )
    assert args.report == consumption_iq.DEFAULT_RECONCILIATION_REPORT
    assert args.cost_history == consumption_iq.DEFAULT_COST_HISTORY


def test_module_docstring_describes_optional_standalone_reconcile_scope_assertions() -> None:
    doc = consumption_iq.__doc__ or ""
    assert "--expect-subscription" in doc
    assert "--expect-resource-group" in doc
    assert "future work" not in doc.lower()


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


def test_pre_sales_with_actuals_exits_2(monkeypatch, capsys) -> None:
    """A pre-sales estimate has no deployment, so it has no actuals."""
    monkeypatch.setattr(
        consumption_iq,
        "_phase_estimate",
        lambda args: pytest.fail("validation must run before any estimate"),
    )
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("no Azure call may be attempted"),
    )
    rc = consumption_iq.main([
        "run", "--all", "--pre-sales", "--with-actuals",
        "--rollout", "pilot",
        "--start", START, "--end", END,
        "--subscription", SUB, "--resource-group", RG,
    ])
    assert rc == 2
    assert "--pre-sales" in capsys.readouterr().err


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
        lambda args, actuals=None: calls.append("reconcile") or {"status": "pass"},
    )
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    monkeypatch.setattr(
        consumption_iq, "_emit_reconciliation", lambda args, result, actuals=None: None
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
        lambda args, actuals=None: {"status": "not-verified"},
    )
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result, actuals=None: emitted.append(result),
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


def test_interaction_query_failure_returns_exit_5_for_reconcile(
    monkeypatch, tmp_path
) -> None:
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args, actuals=None: {
            "status": "not-verified",
            "unit_economics": {"status": "not-verified"},
        },
    )
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result, actuals=None: emitted.append(result),
    )
    assert consumption_iq.main(_reconcile_argv(_reconcile_args(tmp_path))) == 5
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


def test_incomplete_policy_emits_before_exiting_5(monkeypatch, tmp_path) -> None:
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
        lambda args, actuals=None: {
            "status": "not-verified",
            "policy_errors": ["cost.baseline is missing"],
        },
    )
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result, actuals=None: emitted.append(result),
    )
    assert consumption_iq.main(_reconcile_argv(_reconcile_args(tmp_path))) == 5
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
    # Issued flags are read straight off the bundle the Azure boundary
    # returned — never re-derived from whether a `kql` string was passed.
    assert provenance["token_query_issued"] is True
    assert provenance["interaction_query_issued"] is True

    # No raw evidence document, token value or credential material may travel
    # into a committed artifact.
    serialized = json.dumps(provenance)
    for leaked in ("PreTaxCost", "rows", "columns", "tenantId", "InputTokens", "1200"):
        assert leaked not in serialized


def test_phase_actuals_cost_only_omits_both_optional_ids_honestly(
    monkeypatch, tmp_path
) -> None:
    """When neither `--monitor-resource-id` nor `--workspace-resource-id` is
    supplied, the mandatory Cost Management evidence still yields a `pass`
    manifest, but both optional rows must degrade to an honest
    `not-verified` alongside the two distinct omission warnings
    `collect_sources` appends — never a silent, unexplained absence."""
    collector = _FakeCollector(
        _bundle(
            token_doc=None,
            token_source_resource_id=None,
            token_query_issued=False,
            interaction_result=None,
            interaction_query_issued=False,
            warnings=[
                "model token attribution not verified because monitor "
                "resource id not supplied",
                "interaction evidence not verified because workspace "
                "resource id not supplied",
            ],
        )
    )
    monkeypatch.setattr(consumption_iq, "collect_sources", collector)
    _pin_now(monkeypatch)
    document = consumption_iq._phase_actuals(
        _actuals_args(tmp_path, _write_spec(tmp_path))
    )

    assert document["status"] == "pass"
    assert document["cost"]["period_total_usd"] == 12.5
    assert document["usage"]["interaction_status"] == "not-verified"
    assert document["usage"]["model_attribution_status"] == "not-verified"
    assert document["usage"]["total_interactions"] is None
    assert document["usage"]["models"] == []

    assert any(
        "monitor resource id not supplied" in warning.casefold()
        for warning in document["warnings"]
    )
    assert any(
        "workspace resource id not supplied" in warning.casefold()
        for warning in document["warnings"]
    )

    provenance = document["provenance"]
    assert provenance["token_query_issued"] is False
    assert provenance["interaction_query_issued"] is False


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


def test_emit_actuals_delegates_to_the_shared_durable_writer(
    monkeypatch, tmp_path
) -> None:
    """One durable writer, not two.

    A second hand-rolled `open`/`replace` in the CLI is how the standalone
    half quietly drifts from the pair half — different validation, different
    fsync discipline, different permissions. `_emit_actuals` must therefore
    be a thin call into the emitter that already owns that contract.
    """
    calls = []
    monkeypatch.setattr(
        consumption_iq,
        "emit_actuals_document",
        lambda document, path: calls.append((document, path)),
    )
    destination = tmp_path / "specs" / "cost-actuals-manifest.json"
    document = _minimal_actuals()
    returned = consumption_iq._emit_actuals(_emit_args(destination), document)

    assert calls == [(document, destination)]
    assert returned == destination
    assert not destination.exists()


def test_cli_owns_no_private_artifact_writer() -> None:
    source = Path(consumption_iq.__file__).read_text(encoding="utf-8")
    assert "def _write_atomic" not in source
    assert "def _canonical_text" not in source
    assert "NamedTemporaryFile" not in source


def test_emit_actuals_is_durable_through_the_shared_writer(
    tmp_path, monkeypatch
) -> None:
    """Delegation is only worth anything if the real thing still fsyncs and
    renames, so exercise the undelegated path end to end."""
    if os.name == "nt":
        pytest.skip("directory descriptors do not exist on Windows")
    events = []
    real_replace = emitter.os.replace
    real_fsync_directory = emitter._fsync_directory

    def replace(source, destination):
        events.append(("publish", str(destination)))
        return real_replace(source, destination)

    def fsync_directory(path):
        events.append(("dirsync", str(path)))
        return real_fsync_directory(path)

    monkeypatch.setattr(emitter.os, "replace", replace)
    monkeypatch.setattr(emitter, "_fsync_directory", fsync_directory)
    destination = tmp_path / "specs" / "cost-actuals-manifest.json"
    consumption_iq._emit_actuals(_emit_args(destination), _minimal_actuals())

    assert ("publish", str(destination)) in events
    assert ("dirsync", str(destination.parent)) in events


def test_emit_actuals_rejects_a_symlinked_destination(tmp_path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    (specs / "cost-actuals-manifest.json").symlink_to(target)
    with pytest.raises(EmissionValidationError):
        consumption_iq._emit_actuals(
            _emit_args(specs / "cost-actuals-manifest.json"), _minimal_actuals()
        )
    assert target.read_text(encoding="utf-8") == "{}"


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


def _reconcile_argv(args) -> list[str]:
    """The same paths `_reconcile_args` parsed, back as an argv for `main`."""
    return [
        "reconcile",
        "--forecast", str(args.forecast),
        "--actuals-manifest", str(args.actuals_manifest),
        "--spec", str(args.spec),
        "--reconciliation-manifest", str(args.reconciliation_manifest),
        "--report", str(args.report),
        "--cost-history", str(args.cost_history),
    ]


def test_phase_reconcile_forwards_optional_expect_scope(monkeypatch, tmp_path) -> None:
    captured = {}
    args = _reconcile_args(tmp_path)
    args.expect_subscription = "sub-a"
    args.expect_resource_group = "rg-a"

    def fake_reconcile_costs(*call_args, **kwargs):
        captured.update(kwargs)
        return {"schema": "threadlight-cost-reconciliation/v1", "status": "pass", "warnings": []}

    monkeypatch.setattr(consumption_iq, "reconcile_costs", fake_reconcile_costs)
    consumption_iq._phase_reconcile(args)

    assert captured["expected_subscription_id"] == "sub-a"
    assert captured["expected_resource_group"] == "rg-a"


def test_reconcile_expect_subscription_mismatch_exits_2(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("reconcile must issue no Azure call"),
    )
    args = _reconcile_args(tmp_path)
    argv = _reconcile_argv(args) + ["--expect-subscription", "sub-b"]
    assert consumption_iq.main(argv) == 2


def test_reconcile_expect_resource_group_mismatch_exits_2(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("reconcile must issue no Azure call"),
    )
    args = _reconcile_args(tmp_path)
    argv = _reconcile_argv(args) + ["--expect-resource-group", "rg-b"]
    assert consumption_iq.main(argv) == 2


def test_phase_reconcile_never_calls_a_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("reconcile must issue no Azure call"),
    )
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    result = consumption_iq._phase_reconcile(_reconcile_args(tmp_path))
    assert result["schema"] == "threadlight-cost-reconciliation/v1"
    assert result["status"] in {"pass", "not-verified"}


def test_phase_reconcile_hashes_raw_spec_bytes_and_both_documents(
    monkeypatch, tmp_path
) -> None:
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    result = consumption_iq._phase_reconcile(args)

    assert result["policy_ref"]["spec_sha256"] == hashlib.sha256(
        Path(args.spec).read_bytes()
    ).hexdigest()
    assert result["forecast_ref"]["sha256"] == sha256_json(_forecast())
    assert result["actuals_ref"]["sha256"] == sha256_json(_minimal_actuals())


def test_phase_reconcile_records_the_paths_it_actually_read(
    monkeypatch, tmp_path
) -> None:
    """Provenance names the files this run opened, not the canonical
    defaults: a pilot that passes `--actuals-manifest` elsewhere would
    otherwise publish a reference nobody can resolve."""
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    result = consumption_iq._phase_reconcile(args)

    assert result["forecast_ref"]["path"] == str(args.forecast)
    assert result["actuals_ref"]["path"] == str(args.actuals_manifest)
    assert result["policy_ref"]["path"] == str(args.spec)
    # Still the digests of the bytes, unchanged by where they live.
    assert result["actuals_ref"]["sha256"] == sha256_json(_minimal_actuals())


def test_phase_reconcile_stamps_the_computation_instant_not_the_collection(
    monkeypatch, tmp_path
) -> None:
    """`reconciliation.generated_at` is when the verdict was computed.

    Copying the actuals' instant would claim the re-projection happened when
    the bill was read, and would make every later re-reconciliation of the
    same evidence collide in immutable history.
    """
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    result = consumption_iq._phase_reconcile(args)

    assert result["generated_at"] == PINNED_RECONCILED_AT
    assert result["generated_at"] != PINNED_GENERATED_AT
    assert json.loads(Path(args.actuals_manifest).read_text())[
        "generated_at"
    ] == PINNED_GENERATED_AT


def test_phase_reconcile_carries_policy_errors(monkeypatch, tmp_path) -> None:
    broken = SPEC_SECTION_14.replace("max_forecast_variance_pct: 0.20", "")
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
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


def test_emit_reconciliation_publishes_the_pair_and_history(
    monkeypatch, tmp_path
) -> None:
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    result = consumption_iq._phase_reconcile(args)
    consumption_iq._emit_reconciliation(args, result)

    manifest = json.loads(Path(args.reconciliation_manifest).read_text())
    assert manifest["schema"] == "threadlight-cost-reconciliation/v1"
    assert Path(args.report).exists()
    history = list(Path(args.cost_history).rglob("reconciliation.json"))
    assert len(history) == 1
    # The snapshot is keyed by when the verdict was computed, not by when the
    # evidence underneath it was collected.
    assert history[0].parent.name == "2026-08-12T091500Z"


def test_reconcile_command_emits_then_reports_the_verdict(
    monkeypatch, tmp_path
) -> None:
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    rc = consumption_iq.main(_reconcile_argv(args))
    # The fixture is a complete, mature pilot: it must reconcile clean.
    assert rc == 0
    assert Path(args.reconciliation_manifest).exists()
    assert Path(args.report).exists()


def test_reconcile_over_the_same_actuals_writes_a_new_snapshot(
    monkeypatch, tmp_path
) -> None:
    """A pricing refresh must be re-reconcilable without re-collecting.

    Nothing is stubbed below except the clock and the Azure boundary: the
    real reconciliation core and the real emitter run twice over the SAME
    collected actuals, with a changed forecast in between. The second run
    must succeed and add its own immutable snapshot rather than colliding
    with the first one.
    """
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("reconcile must issue no Azure call"),
    )
    args = _reconcile_args(tmp_path)
    actuals_before = json.loads(Path(args.actuals_manifest).read_text())

    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    first = consumption_iq._phase_reconcile(args)
    consumption_iq._emit_reconciliation(args, first)

    Path(args.forecast).write_text(json.dumps(_forecast(555.0)), encoding="utf-8")
    later = datetime(2026, 8, 13, 11, 45, 0, tzinfo=timezone.utc)
    _pin_now(monkeypatch, later)
    second = consumption_iq._phase_reconcile(args)
    consumption_iq._emit_reconciliation(args, second)

    assert json.loads(Path(args.actuals_manifest).read_text()) == actuals_before
    assert first["actuals_ref"]["sha256"] == second["actuals_ref"]["sha256"]
    assert first["forecast_ref"]["sha256"] != second["forecast_ref"]["sha256"]

    window = Path(args.cost_history) / "2026-08-01--2026-08-08"
    assert sorted(path.name for path in window.iterdir()) == [
        "2026-08-12T091500Z",
        "2026-08-13T114500Z",
    ]
    published = json.loads(Path(args.reconciliation_manifest).read_text())
    assert published["forecast_ref"]["sha256"] == second["forecast_ref"]["sha256"]


def test_reconcile_command_succeeds_twice_over_unchanged_actuals(
    monkeypatch, tmp_path
) -> None:
    """The same journey through `main`, exit code included."""
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("reconcile must issue no Azure call"),
    )
    args = _reconcile_args(tmp_path)
    argv = _reconcile_argv(args)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    assert consumption_iq.main(argv) == 0

    Path(args.forecast).write_text(json.dumps(_forecast(555.0)), encoding="utf-8")
    _pin_now(monkeypatch, datetime(2026, 8, 13, 11, 45, 0, tzinfo=timezone.utc))
    assert consumption_iq.main(argv) == 0

    window = Path(args.cost_history) / "2026-08-01--2026-08-08"
    assert len(list(window.iterdir())) == 2


# ---------------------------------------------------------------------------
# The actuals manifest is read once, and the bytes that were hashed are the
# bytes that get published
# ---------------------------------------------------------------------------


def _count_actuals_reads(monkeypatch, target: Path) -> list[str]:
    """Record every read of `target`, however it is spelled."""
    reads: list[str] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    resolved = os.path.abspath(target)

    def read_text(self, *args, **kwargs):
        if os.path.abspath(self) == resolved:
            reads.append("read_text")
        return real_read_text(self, *args, **kwargs)

    def read_bytes(self, *args, **kwargs):
        if os.path.abspath(self) == resolved:
            reads.append("read_bytes")
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return reads


def test_reconcile_command_reads_the_actuals_manifest_exactly_once(
    monkeypatch, tmp_path
) -> None:
    """Hashing one revision and publishing another is an audit hole.

    Reading the evidence twice — once to reconcile, once to emit — leaves a
    window in which a concurrent collection changes the file underneath the
    verdict. One read, one in-memory document, one published artefact.
    """
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    reads = _count_actuals_reads(monkeypatch, Path(args.actuals_manifest))
    rc = consumption_iq.main(_reconcile_argv(args))
    assert rc == 0
    assert reads == ["read_text"]


def test_reconcile_publishes_the_evidence_it_hashed(monkeypatch, tmp_path) -> None:
    """A re-collection landing mid-run must not be able to substitute itself
    for the evidence the verdict was computed from."""
    args = _reconcile_args(tmp_path)
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    hashed = _minimal_actuals()
    real_reconcile_costs = consumption_iq.reconcile_costs

    def reconcile_then_overwrite(*call_args, **kwargs):
        result = real_reconcile_costs(*call_args, **kwargs)
        Path(args.actuals_manifest).write_text(
            json.dumps(_minimal_actuals(status="not-verified")), encoding="utf-8"
        )
        return result

    monkeypatch.setattr(consumption_iq, "reconcile_costs", reconcile_then_overwrite)
    rc = consumption_iq.main(_reconcile_argv(args))

    assert rc == 0
    published = json.loads(Path(args.actuals_manifest).read_text())
    assert published == hashed
    manifest = json.loads(Path(args.reconciliation_manifest).read_text())
    assert manifest["actuals_ref"]["sha256"] == sha256_json(hashed)
    snapshot = list(Path(args.cost_history).rglob("actuals.json"))
    assert len(snapshot) == 1
    assert json.loads(snapshot[0].read_text()) == hashed


def test_run_with_actuals_never_reads_back_the_manifest_it_just_wrote(
    monkeypatch, tmp_path
) -> None:
    """`run --all --with-actuals` already holds the collected document in
    memory. Reading it back off disk would be both a wasted round trip and a
    chance to reconcile something other than what was collected."""
    specs = tmp_path / "specs"
    specs.mkdir()
    forecast_path = specs / "cost-manifest.json"
    forecast_path.write_text(json.dumps(_forecast()), encoding="utf-8")
    spec_path = _write_spec(tmp_path, SPEC_SECTION_14)
    actuals_path = specs / "cost-actuals-manifest.json"
    collected = _minimal_actuals()

    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    monkeypatch.setattr(consumption_iq, "_phase_actuals", lambda args: collected)
    monkeypatch.setattr(
        consumption_iq,
        "collect_sources",
        lambda *a, **k: pytest.fail("no Azure call may follow collection"),
    )
    _pin_now(monkeypatch, PINNED_RECONCILED_NOW)
    reads = _count_actuals_reads(monkeypatch, actuals_path)

    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", SUB, "--resource-group", RG,
        "--manifest", str(forecast_path),
        "--spec", str(spec_path),
        "--actuals-manifest", str(actuals_path),
        "--reconciliation-manifest", str(specs / "cost-reconciliation-manifest.json"),
        "--reconciliation-report", str(tmp_path / "docs" / "cost-reconciliation.md"),
        "--cost-history", str(specs / "cost-history"),
    ])

    assert rc == 0
    assert reads == []
    manifest = json.loads((specs / "cost-reconciliation-manifest.json").read_text())
    assert manifest["actuals_ref"]["sha256"] == sha256_json(collected)
    assert manifest["actuals_ref"]["path"] == str(actuals_path)


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


def test_io_failure_returns_exit_3(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args, actuals=None: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert consumption_iq.main(_reconcile_argv(_reconcile_args(tmp_path))) == 3


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
