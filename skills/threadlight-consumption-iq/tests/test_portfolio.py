"""Per-use-case and per-agent cost across several pilots in one subscription.

`portfolio.py` reads Cost Management at subscription scope (read-only), maps resource
groups to use cases through resource-group tags, splits a resource group shared by
several agents by each agent's share of successful invocations from telemetry, and
adds a Cost Management forecast per use case. Tests inject a fake runner and never
call Azure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import portfolio  # noqa: E402

SUB = "00000000-0000-0000-0000-000000000001"
START, END = date(2026, 11, 1), date(2026, 11, 11)
NOW = "2026-11-11T12:00:00Z"


@pytest.fixture(autouse=True)
def _no_real_az(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("tests must not call Azure")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")


def _cost_page(rows):
    return {"properties": {"columns": [
        {"name": "PreTaxCost"}, {"name": "UsageDate"}, {"name": "ResourceGroupName"},
        {"name": "ServiceName"}, {"name": "Currency"}], "rows": rows, "nextLink": None}}


GROUPS = [
    {"name": "rg-returns", "tags": {"workload": "returns-triage"}},
    {"name": "rg-claims", "tags": {"process": "claims-intake"}},
    {"name": "rg-other", "tags": {}},
]
COST_ROWS = [
    [6.0, 20261101, "rg-returns", "Foundry Models", "USD"],
    [4.0, 20261102, "rg-returns", "Azure Container Apps", "USD"],
    [3.0, 20261101, "RG-CLAIMS", "Foundry Models", "USD"],
    [2.5, 20261101, "rg-other", "Storage", "USD"],
]
AGENTS = [
    {"agent": "returns-text", "resource_group": "rg-returns", "total": 30, "successful": 30},
    {"agent": "returns-voice", "resource_group": "rg-returns", "total": 12, "successful": 10},
]


def _build(**kw):
    args = dict(cost_pages=[_cost_page(COST_ROWS)], groups=GROUPS, tag_keys=["workload", "process"],
                agent_rows=AGENTS, forecasts={}, window=(START, END), generated_at=NOW,
                subscription_id=SUB)
    args.update(kw)
    return portfolio.build_portfolio(**args)


def test_use_cases_come_from_resource_group_tags_and_totals_stay_complete():
    doc = _build()
    uc = {u["use_case"]: u for u in doc["use_cases"]}
    assert uc["returns-triage"]["actual_usd"] == pytest.approx(10.0)
    assert uc["returns-triage"]["tag_key"] == "workload"
    assert uc["claims-intake"]["actual_usd"] == pytest.approx(3.0)  # case-insensitive RG match
    assert doc["unattributed"]["actual_usd"] == pytest.approx(2.5)
    assert doc["totals"]["subscription_actual_usd"] == pytest.approx(15.5)
    assert doc["totals"]["complete"] is True
    assert sum(u["actual_usd"] for u in doc["use_cases"]) + doc["unattributed"]["actual_usd"] == pytest.approx(15.5)


def test_shared_resource_group_is_allocated_by_successful_invocation_share():
    uc = {u["use_case"]: u for u in _build()["use_cases"]}["returns-triage"]
    agents = {a["agent"]: a for a in uc["agents"]}
    assert agents["returns-text"]["allocated_usd"] == pytest.approx(7.5)
    assert agents["returns-voice"]["allocated_usd"] == pytest.approx(2.5)
    assert agents["returns-text"]["allocation_basis"] == "successful-invocation-share"
    assert agents["returns-voice"]["cost_per_successful_invocation_usd"] == pytest.approx(0.25)
    assert uc["successful_invocations"] == 40
    assert uc["cost_per_successful_invocation_usd"] == pytest.approx(0.25)
    assert uc["kpi_basis"] == "agent-invocations"


def test_no_telemetry_means_no_unit_cost_never_zero():
    uc = {u["use_case"]: u for u in _build(agent_rows=[])["use_cases"]}
    assert uc["claims-intake"]["successful_invocations"] is None
    assert uc["claims-intake"]["cost_per_successful_invocation_usd"] is None
    assert uc["claims-intake"]["agents"] == []
    assert not [k for u in uc.values() for k in u if k.startswith("_")]


def test_forecast_prefers_cost_management_and_falls_back_to_labelled_run_rate():
    forecasts = {"returns-triage": {"status": "pass", "next_30_days_usd": 42.0},
                 "claims-intake": {"status": "not-available", "reason": "FailedDependency: not enough data"}}
    uc = {u["use_case"]: u for u in _build(forecasts=forecasts)["use_cases"]}
    assert uc["returns-triage"]["forecast"] == {"source": "cost-management-forecast", "next_30_days_usd": 42.0}
    run_rate = uc["claims-intake"]["forecast"]
    assert run_rate["source"] == "run-rate"
    assert run_rate["next_30_days_usd"] == pytest.approx(3.0 * 30 / 10)
    assert "not enough data" in run_rate["note"]


def test_mixed_currency_is_refused():
    rows = COST_ROWS + [[1.0, 20261101, "rg-claims", "Storage", "EUR"]]
    with pytest.raises(portfolio.PortfolioError):
        _build(cost_pages=[_cost_page(rows)])


def test_out_of_window_or_malformed_rows_fail_closed():
    with pytest.raises(portfolio.PortfolioError):
        _build(cost_pages=[_cost_page([[1.0, 20261115, "rg-returns", "Storage", "USD"]])])
    with pytest.raises(portfolio.PortfolioError):
        _build(cost_pages=[{"properties": {"columns": [{"name": "PreTaxCost"}], "rows": [[1.0]]}}])


def test_cross_product_boundary_is_recorded():
    doc = _build()
    text = json.dumps(doc["boundary"])
    assert "Copilot Studio" in text and "seat" in text
    assert "https://learn.microsoft.com/power-platform/admin/pay-as-you-go-usage-costs" in text


def test_agent_kql_is_fixed_and_window_bound():
    kql = portfolio.agent_invocations_kql(START, END)
    assert 'datetime(2026-11-01T00:00:00Z)' in kql and 'datetime(2026-11-11T00:00:00Z)' in kql
    assert "invoke_agent" in kql and "gen_ai.agent.name" in kql and "microsoft.foundry.project.id" in kql
    # `project` is a KQL operator: using it as a column alias is a syntax error (seen live).
    assert " project =" not in kql and "by agent, project" not in kql


PROJ = "/subscriptions/x/resourceGroups/rg-Returns/providers/Microsoft.CognitiveServices/accounts/ai/projects/p"


def test_parse_agent_rows_maps_project_to_resource_group_and_prefers_requests():
    cols = [{"name": n} for n in ("agent", "foundry_project", "span_table", "total", "successful")]
    doc = {"tables": [{"columns": cols, "rows": [
        ["a1", PROJ, "AppRequests", "5", "4"],
        ["a1", PROJ, "AppDependencies", "5", "5"],   # client span of the same calls: not added
        ["a2", PROJ, "AppDependencies", "3", "3"],   # only client spans: used
    ]}]}
    assert portfolio.parse_agent_rows(doc) == [
        {"agent": "a1", "resource_group": "rg-returns", "total": 5, "successful": 4},
        {"agent": "a2", "resource_group": "rg-returns", "total": 3, "successful": 3}]


def test_collect_issues_only_read_only_commands():
    calls = []

    def runner(args):
        calls.append(args)
        joined = " ".join(args)
        if args[:3] == ["az", "account", "show"]:
            out = json.dumps({"id": SUB, "tenantId": "t"})
        elif args[:3] == ["az", "group", "list"]:
            out = json.dumps(GROUPS)
        elif "/forecast?" in joined:
            out = json.dumps({"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"},
                              {"name": "CostStatus"}, {"name": "Currency"}], "rows": [[1.0, 20261112, "Forecast", "USD"]]}})
        elif "/query?" in joined:
            out = json.dumps(_cost_page(COST_ROWS))
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, out, "")

    bundle = portfolio.collect(SUB, START, END, tag_keys=["workload", "process"], runner=runner,
                               today=date(2026, 11, 11))
    assert bundle["forecasts"]["returns-triage"]["status"] == "pass"
    for args in calls:
        assert args[0] == "az"
        assert args[1] in ("account", "group", "rest")
        if args[1] == "rest":
            assert args[args.index("--method") + 1] == "post"
            url = args[args.index("--url") + 1]
            assert "/providers/Microsoft.CostManagement/" in url
    assert bundle["agent_rows"] == []  # no workspace named, no telemetry query issued
    doc = portfolio.build_portfolio(**bundle, window=(START, END), generated_at=NOW,
                                    subscription_id=SUB, tag_keys=["workload", "process"])
    assert doc["schema"] == "threadlight-cost-portfolio/v1"


def test_missing_azure_config_dir_fails_before_any_call(monkeypatch):
    monkeypatch.delenv("AZURE_CONFIG_DIR")
    with pytest.raises(Exception):
        portfolio.collect(SUB, START, END, tag_keys=["workload"], runner=lambda a: pytest.fail("called"))


def test_markdown_labels_measured_calculated_and_forecast():
    md = portfolio.render_markdown(_build(forecasts={"returns-triage": {"status": "pass", "next_30_days_usd": 42.0}}))
    for marker in ("Measured", "Calculated", "Forecast", "returns-voice", "Unattributed",
                   "not the SPEC § 14 success event", "Copilot Studio"):
        assert marker in md, marker


def test_forecast_is_requested_only_for_top_use_cases_and_retries_throttling():
    calls = {"forecast": 0}
    throttled = {"done": False}

    def runner(args):
        joined = " ".join(args)
        if args[:3] == ["az", "account", "show"]:
            out = json.dumps({"id": SUB, "tenantId": "t"})
        elif args[:3] == ["az", "group", "list"]:
            out = json.dumps(GROUPS)
        elif "/forecast?" in joined:
            calls["forecast"] += 1
            if not throttled["done"]:
                throttled["done"] = True
                return subprocess.CompletedProcess(args, 1, "", "ERROR: Too Many Requests (429)")
            out = json.dumps({"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"},
                              {"name": "CostStatus"}, {"name": "Currency"}], "rows": [[2.0, 20261112, "Forecast", "USD"]]}})
        else:
            out = json.dumps(_cost_page(COST_ROWS))
        return subprocess.CompletedProcess(args, 0, out, "")

    bundle = portfolio.collect(SUB, START, END, tag_keys=["workload", "process"], runner=runner,
                               forecast_top=1, today=date(2026, 11, 11), sleep=lambda s: None)
    assert calls["forecast"] == 2  # one throttled attempt, one retry, only for the top use case
    assert bundle["forecasts"]["returns-triage"]["status"] == "pass"
    assert bundle["forecasts"]["claims-intake"]["status"] == "not-requested"
    doc = portfolio.build_portfolio(**bundle, window=(START, END), generated_at=NOW,
                                    subscription_id=SUB, tag_keys=["workload", "process"])
    claims = {u["use_case"]: u for u in doc["use_cases"]}["claims-intake"]["forecast"]
    assert claims["source"] == "run-rate" and "top 1" in claims["note"]


def test_consumption_iq_cli_delegates_portfolio_subcommand(monkeypatch):
    import consumption_iq

    seen = {}
    monkeypatch.setattr(portfolio, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    assert consumption_iq.main(["portfolio", "--subscription", SUB]) == 0
    assert seen["argv"] == ["--subscription", SUB]


def test_non_usd_billing_is_refused_like_actuals():
    rows = [[1.0, 20261101, "rg-returns", "Storage", "EUR"]]
    with pytest.raises(portfolio.PortfolioError, match="USD"):
        _build(cost_pages=[_cost_page(rows)])


@pytest.mark.parametrize("doc", [
    {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "CostStatus"}], "rows": []}},
    {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "CostStatus"}],
                    "rows": [[1.0, 20261101, "Actual"]]}},
    {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "CostStatus"}], "rows": [[None, 1, "Forecast"]]}},
    {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "CostStatus"}], "rows": [["x"]]}},
    {"properties": {"columns": [{"name": "Cost"}, {"name": "CostStatus"}], "rows": "nope"}},
])
def test_unusable_forecast_falls_back_instead_of_reporting_zero_or_crashing(doc):
    with pytest.raises(portfolio.PortfolioError):
        portfolio._parse_forecast(doc)


def test_forecast_horizon_is_fixed_at_30_days():
    body = portfolio.forecast_body(["rg-a"], date(2026, 11, 11))
    assert body["timePeriod"]["to"] == "2026-12-10T23:59:59Z"
    with pytest.raises(SystemExit):
        portfolio.main(["--subscription", SUB, "--start", "2026-11-01", "--end", "2026-11-11", "--forecast-days", "90"])
