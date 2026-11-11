"""
Per-use-case and per-agent cost across several pilots in one subscription
(`threadlight-cost-portfolio/v1`).

`actuals` answers "what did this pilot's resource group cost?". A customer running
several pilots, or several agents on one Foundry account, also asks "what does each
use case and each agent cost, and where is it heading?". This module answers that
from the same read-only sources:

* **Cost Management Query** at subscription scope, daily, grouped by
  `ResourceGroupName` and `ServiceName` (usage pre-tax cost, not an invoice);
* **resource-group tags** (`az group list`) mapping each resource group to a use
  case through an ordered list of tag keys (for example `workload`, `process`);
  untagged groups stay in an explicit *unattributed* bucket so totals stay complete;
* optionally, **Application Insights / Log Analytics** `invoke_agent` spans
  (`gen_ai.agent.name`, `microsoft.foundry.project.id`) giving successful
  invocations per agent. A resource group serving several agents is allocated by
  each agent's share of successful invocations: a calculation, labelled as such;
* **Cost Management Forecast** per use case (resource-group filter). When the
  service has too little history it answers `FailedDependency`; the use case then
  gets a run-rate projection labelled `run-rate`, never presented as a forecast.

Cost per successful invocation is computed on agent invocations from telemetry. It
is not the SPEC § 14 business success event: the verified per-pilot KPI-003 stays
the job of `reconcile`.

Read-only. Every `az` call goes through an injected runner (argv list, no shell),
requires `AZURE_CONFIG_DIR`, asserts the active subscription first and never logs
in or switches context. Cost Management failures fail closed; telemetry and
forecast failures degrade with a warning.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import actuals_sources as az  # noqa: E402

SCHEMA = "threadlight-cost-portfolio/v1"
DEFAULT_MANIFEST = Path("specs/cost-portfolio-manifest.json")
DEFAULT_REPORT = Path("docs/cost-portfolio.md")
DEFAULT_TAG_KEYS = ("workload", "process", "purpose", "azd-env-name")
# Fixed so a forecast and a run-rate in the same column always cover the same period.
FORECAST_DAYS = 30

BOUNDARY = {
    "in_scope": (
        "Azure usage billed to this subscription, including Copilot Studio and Microsoft 365 Copilot "
        "pay-as-you-go meters when their billing plan is linked to this subscription: Microsoft documents "
        "that those meters are billed to the Azure subscription and visible in Azure Cost Management "
        "(https://learn.microsoft.com/power-platform/admin/pay-as-you-go-usage-costs)."
    ),
    "out_of_scope": (
        "Per-user seat licences (Microsoft 365 Copilot, Copilot Studio capacity packs) and prepaid Copilot "
        "Credit capacity are not Azure usage rows. Microsoft Graph exposes Copilot Credit consumption per "
        "security group as a beta API (https://learn.microsoft.com/microsoft-365/copilot/extensibility/api/"
        "admin-settings/cost-management/costmanagementgroupusagerecord-list), not per agent and not "
        "supported for production use; this report does not read it."
    ),
    "cost_basis": (
        "Cost Management usage pre-tax cost: not an invoice; taxes, credits and purchases excluded "
        "(https://learn.microsoft.com/azure/cost-management-billing/costs/understand-cost-mgt-data)."
    ),
}

_REQUIRED_COLUMNS = ("PreTaxCost", "UsageDate", "ResourceGroupName", "ServiceName", "Currency")
_RG_IN_PROJECT_RE = re.compile(r"/resourceGroups/([^/]+)/", re.IGNORECASE)


class PortfolioError(ValueError):
    """Evidence that cannot be turned into a complete, honest total."""


# --------------------------------------------------------------------------- queries

def cost_query_body(start: date, end: date) -> dict[str, Any]:
    start, end = az._validate_window(start, end)
    return {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {"from": az._utc_midnight(start), "to": az._inclusive_utc_end(end)},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceGroupName"},
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }


def forecast_body(resource_groups: list[str], today: date) -> dict[str, Any]:
    end = today + timedelta(days=FORECAST_DAYS)
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": az._utc_midnight(today), "to": az._inclusive_utc_end(end)},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "filter": {"dimensions": {"name": "ResourceGroupName", "operator": "In",
                                      "values": sorted(resource_groups)}},
        },
        "includeActualCost": False,
        "includeFreshPartialCost": False,
    }


def agent_invocations_kql(start: date, end: date) -> str:
    """Fixed-shape KQL: successful `invoke_agent` spans per agent and Foundry project."""
    start, end = az._validate_window(start, end)
    return (
        "union isfuzzy=true AppRequests, AppDependencies"
        f" | where TimeGenerated >= datetime({az._utc_midnight(start)})"
        f" and TimeGenerated < datetime({az._utc_midnight(end)})"
        ' | where tostring(Properties["gen_ai.operation.name"]) == "invoke_agent"'
        ' | extend agent = tostring(Properties["gen_ai.agent.name"]),'
        ' foundry_project = tostring(Properties["microsoft.foundry.project.id"]), span_table = Type'
        " | where isnotempty(agent) and isnotempty(foundry_project)"
        " | summarize total = count(), successful = countif(Success == true)"
        " by agent, foundry_project, span_table"
    )


def _sub_url(subscription_id: str, endpoint: str) -> str:
    return (f"https://{az.ARM_HOST}/subscriptions/{subscription_id}"
            f"/providers/Microsoft.CostManagement/{endpoint}?api-version={az.COST_API_VERSION}")


def _validate_sub_next_link(link: object, subscription_id: str, seen: set[str]) -> str:
    from urllib.parse import urlsplit

    if not isinstance(link, str) or not link.strip() or link in seen:
        raise az.ActualsSourceError("Cost Management nextLink is unusable or repeats a page")
    parts = urlsplit(link)
    expected = f"/subscriptions/{subscription_id}/providers/microsoft.costmanagement/query".casefold()
    if parts.scheme != "https" or parts.netloc.casefold() != az.ARM_HOST or parts.path.casefold() != expected:
        raise az.ActualsSourceError("Cost Management nextLink leaves the requested scope or host")
    az._validate_next_link_query(parts.query)
    return link


# --------------------------------------------------------------------------- collection (Azure boundary)

def collect(subscription_id: str, start: date, end: date, *, tag_keys: list[str],
            workspace_resource_ids: list[str] | None = None,
            forecast_top: int = 10, today: date | None = None, runner: Optional[az.Runner] = None,
            sleep: Optional[Callable[[float], Any]] = None) -> dict[str, Any]:
    az._require_azure_config_dir()
    subscription_id = az._validate_subscription_id(subscription_id)
    start, end = az._validate_window(start, end)
    run = az._resolve_runner(runner)
    az.assert_azure_context(subscription_id, runner=run)
    warnings: list[str] = []

    completed = az._invoke(run, ["az", "group", "list", "--subscription", subscription_id,
                                 "--query", "[].{name:name,tags:tags}", "-o", "json"], context="az group list")
    if completed.returncode != 0:
        raise az.ActualsSourceError(f"az group list failed: {az._sanitize_stderr(completed.stderr)}")
    groups = az._load_json(completed.stdout, context="az group list")
    if not isinstance(groups, list):
        raise az.ActualsSourceError("az group list returned a non-list document")

    body = json.dumps(cost_query_body(start, end), separators=(",", ":"), sort_keys=True)
    url, seen, pages = _sub_url(subscription_id, "query"), set(), []
    while True:
        page = az._fetch_cost_page(run, url, body, sleep=sleep)
        pages.append(page)
        seen.add(url)
        props = page.get("properties") if isinstance(page.get("properties"), dict) else {}
        link = props.get("nextLink")
        if link is None:
            break
        if len(pages) >= az.MAX_COST_PAGES:
            raise az.ActualsSourceError("Cost Management pagination exceeded the page ceiling")
        url = _validate_sub_next_link(link, subscription_id, seen)

    # Forecast only the largest use cases: one Forecast call per use case, and Cost
    # Management throttles bursts (429 seen live with ~50 sequential calls).
    rows = _parse_cost_rows(pages, start, end)
    by_rg: dict[str, float] = {}
    for r in rows:
        by_rg[r["rg"]] = by_rg.get(r["rg"], 0.0) + r["cost"]
    ucs = _use_case_groups(groups, tag_keys)
    ranked = sorted(ucs, key=lambda u: (-sum(by_rg.get(rg, 0.0) for rg in ucs[u]), u))
    chosen = set(ranked[:max(forecast_top, 0)])
    forecasts: dict[str, dict[str, Any]] = {}
    today = today or datetime.now(timezone.utc).date()
    for use_case in sorted(ucs):
        if use_case not in chosen:
            forecasts[use_case] = {"status": "not-requested",
                                   "reason": f"outside the top {forecast_top} use cases by measured cost"}
            continue
        fbody = json.dumps(forecast_body(ucs[use_case], today), separators=(",", ":"), sort_keys=True)
        try:
            doc = az._fetch_cost_page(run, _sub_url(subscription_id, "forecast"), fbody, sleep=sleep)
            forecasts[use_case] = _parse_forecast(doc)
        except (az.ActualsSourceError, PortfolioError) as exc:
            forecasts[use_case] = {"status": "not-available", "reason": str(exc)}

    agent_rows: list[dict[str, Any]] = []
    for ws in workspace_resource_ids or []:
        customer_id = az.resolve_workspace_customer_id(ws, runner=run)
        doc = az._query_workspace(customer_id, agent_invocations_kql(start, end), runner=run) if customer_id else None
        if doc is None:
            warnings.append(f"agent invocations unavailable for workspace {ws.rsplit('/', 1)[-1]}")
            continue
        agent_rows.extend(parse_agent_rows(doc))
    return {"cost_pages": pages, "groups": groups, "forecasts": forecasts,
            "agent_rows": agent_rows, "warnings": warnings}


# --------------------------------------------------------------------------- pure parsing

def _parse_forecast(doc: Any) -> dict[str, Any]:
    """Sum the `Forecast` rows; anything unusable raises so the caller falls back
    to a labelled run-rate instead of reporting zero or crashing the run."""
    props = doc.get("properties") if isinstance(doc, dict) else None
    if not isinstance(props, dict) or not isinstance(props.get("rows"), list):
        raise PortfolioError("forecast response has no rows")
    cols = [c.get("name") for c in props.get("columns") or [] if isinstance(c, dict)]
    if "Cost" not in cols or "CostStatus" not in cols:
        raise PortfolioError("forecast response has no Cost/CostStatus columns")
    ci, si = cols.index("Cost"), cols.index("CostStatus")
    cur = cols.index("Currency") if "Currency" in cols else None
    total, count, currencies = 0.0, 0, set()
    try:
        for row in props["rows"]:
            if row[si] != "Forecast":
                continue
            total += float(row[ci])
            count += 1
            if cur is not None:
                currencies.add(row[cur])
    except (IndexError, TypeError, ValueError, KeyError) as exc:
        raise PortfolioError("forecast row is malformed") from exc
    if not count:
        raise PortfolioError("forecast returned no forecast rows")
    if currencies - {"USD"}:
        raise PortfolioError(f"forecast currency {sorted(currencies)} is not USD")
    return {"status": "pass", "next_30_days_usd": round(total, 4)}


def parse_agent_rows(doc: Any) -> list[dict[str, Any]]:
    tables = doc.get("tables") if isinstance(doc, dict) else None
    if not tables:
        return []
    table = tables[0]
    cols = [c.get("name") for c in table.get("columns") or []]
    # A call can be recorded as a server span (AppRequests) and as the caller's client
    # span (AppDependencies). Count server spans; use client spans only for an agent
    # that has no server span, so one invocation is never counted twice.
    spans: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for row in table.get("rows") or []:
        rec = dict(zip(cols, row))
        match = _RG_IN_PROJECT_RE.search(str(rec.get("foundry_project") or ""))
        try:
            total, ok = int(rec.get("total")), int(rec.get("successful"))
        except (TypeError, ValueError):
            continue
        if not match or not rec.get("agent"):
            continue
        key = (str(rec["agent"]), match.group(1).lower())
        kind = str(rec.get("span_table") or "AppRequests")
        prev = spans.setdefault(key, {}).get(kind, (0, 0))
        spans[key][kind] = (prev[0] + total, prev[1] + ok)
    out = []
    for (agent, rg), kinds in spans.items():
        total, ok = kinds.get("AppRequests") or kinds.get("AppDependencies") or (0, 0)
        out.append({"agent": agent, "resource_group": rg, "total": total, "successful": ok})
    return out


def _parse_cost_rows(pages: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    rows = []
    for page in pages:
        props = page.get("properties") if isinstance(page, dict) else None
        if not isinstance(props, dict):
            raise PortfolioError("Cost Management page has no properties")
        cols = [c.get("name") for c in props.get("columns") or [] if isinstance(c, dict)]
        missing = [c for c in _REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise PortfolioError(f"Cost Management page lacks columns: {', '.join(missing)}")
        idx = {c: cols.index(c) for c in _REQUIRED_COLUMNS}
        for row in props.get("rows") or []:
            try:
                day = datetime.strptime(str(row[idx["UsageDate"]]), "%Y%m%d").date()
                cost = float(row[idx["PreTaxCost"]])
            except (ValueError, TypeError, IndexError) as exc:
                raise PortfolioError("Cost Management row is malformed") from exc
            if not start <= day < end:
                raise PortfolioError(f"Cost Management row dated {day} is outside the window")
            rows.append({"day": day, "cost": cost, "rg": str(row[idx["ResourceGroupName"]]).lower(),
                         "service": str(row[idx["ServiceName"]]), "currency": str(row[idx["Currency"]])})
    currencies = {r["currency"] for r in rows}
    if len(currencies) > 1:
        raise PortfolioError(f"mixed currencies {sorted(currencies)}; refusing to add them up")
    if currencies - {"USD"}:
        # Same contract as threadlight-cost-actuals/v1: amounts are reported in USD only.
        raise PortfolioError(f"billing currency {sorted(currencies)} is not USD; the portfolio is USD-only")
    return rows


def _resolve(groups: list[dict[str, Any]], tag_keys: list[str]) -> dict[str, tuple[str, str]]:
    out = {}
    for g in groups:
        tags = g.get("tags") if isinstance(g.get("tags"), dict) else {}
        lowered = {str(k).lower(): v for k, v in tags.items()}
        for key in tag_keys:
            value = lowered.get(key.lower())
            if isinstance(value, str) and value.strip():
                out[str(g.get("name")).lower()] = (value.strip(), key)
                break
    return out


def _use_case_groups(groups: list[dict[str, Any]], tag_keys: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rg, (uc, _) in _resolve(groups, tag_keys).items():
        out.setdefault(uc, []).append(rg)
    return out


# --------------------------------------------------------------------------- portfolio

def _r(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(value, places)


def build_portfolio(*, cost_pages, groups, tag_keys, agent_rows, forecasts, window, generated_at,
                    subscription_id, warnings: list[str] | None = None) -> dict[str, Any]:
    start, end = window
    days = (end - start).days
    rows = _parse_cost_rows(cost_pages, start, end)
    mapping = _resolve(groups, tag_keys)
    by_rg: dict[str, float] = {}
    services: dict[str, dict[str, float]] = {}
    for r in rows:
        by_rg[r["rg"]] = by_rg.get(r["rg"], 0.0) + r["cost"]
        services.setdefault(r["rg"], {})
        services[r["rg"]][r["service"]] = services[r["rg"]].get(r["service"], 0.0) + r["cost"]

    agents_by_rg: dict[str, list[dict[str, Any]]] = {}
    for a in agent_rows:
        agents_by_rg.setdefault(a["resource_group"], []).append(a)

    use_cases: dict[str, dict[str, Any]] = {}
    unattributed = {"actual_usd": 0.0, "resource_groups": 0}
    for rg, cost in sorted(by_rg.items()):
        if rg not in mapping:
            unattributed["actual_usd"] += cost
            unattributed["resource_groups"] += 1
            continue
        name, key = mapping[rg]
        uc = use_cases.setdefault(name, {"use_case": name, "tag_key": key, "resource_groups": [],
                                         "actual_usd": 0.0, "services": {}, "agents": [],
                                         "_ok": 0, "_seen": False})
        uc["resource_groups"].append(rg)
        uc["actual_usd"] += cost
        for svc, value in services[rg].items():
            uc["services"][svc] = uc["services"].get(svc, 0.0) + value
        agents = agents_by_rg.get(rg, [])
        ok_total = sum(a["successful"] for a in agents)
        for a in agents:
            uc["_seen"] = True
            share = a["successful"] / ok_total if ok_total else None
            allocated = cost * share if share is not None else None
            uc["agents"].append({
                "agent": a["agent"], "resource_group": rg, "invocations": a["total"],
                "successful_invocations": a["successful"],
                "allocated_usd": _r(allocated), "allocation_basis": "successful-invocation-share",
                "cost_per_successful_invocation_usd": _r(allocated / a["successful"]) if allocated is not None and a["successful"] else None,
            })
        uc["_ok"] += ok_total

    out_ucs = []
    for name in sorted(use_cases):
        uc = use_cases[name]
        seen, ok_count = uc.pop("_seen"), uc.pop("_ok")
        ok = ok_count if seen else None
        fc = (forecasts or {}).get(name) or {}
        if fc.get("status") == "pass":
            forecast = {"source": "cost-management-forecast", "next_30_days_usd": fc["next_30_days_usd"]}
        else:
            why = "not requested" if fc.get("status") == "not-requested" else "not available"
            forecast = {"source": "run-rate", "next_30_days_usd": _r(uc["actual_usd"] * 30 / days) if days else None,
                        "note": f"Cost Management forecast {why}"
                                + (f" ({fc['reason']})" if fc.get("reason") else "")
                                + "; actual cost x 30 / window days."}
        out_ucs.append(uc | {
            "actual_usd": _r(uc["actual_usd"]),
            "services": {k: _r(v) for k, v in sorted(uc["services"].items())},
            "successful_invocations": ok,
            "cost_per_successful_invocation_usd": _r(uc["actual_usd"] / ok) if ok else None,
            "kpi_basis": "agent-invocations",
            "forecast": forecast,
        })

    total = sum(by_rg.values())
    attributed = sum(u["actual_usd"] for u in out_ucs)
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "scope": {"subscription_id": subscription_id, "tag_keys": list(tag_keys)},
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "currency": next(iter({r["currency"] for r in rows}), None),
        "use_cases": out_ucs,
        "unattributed": {"actual_usd": _r(unattributed["actual_usd"]),
                         "resource_groups": unattributed["resource_groups"]},
        "totals": {"subscription_actual_usd": _r(total),
                   "complete": abs(attributed + unattributed["actual_usd"] - total) < 0.01},
        "boundary": BOUNDARY,
        "warnings": list(warnings or []),
    }


# --------------------------------------------------------------------------- report

def _usd(v: float | None, places: int = 2) -> str:
    return "not measured" if v is None else f"${v:,.{places}f}"


def render_markdown(doc: dict[str, Any]) -> str:
    w = doc["window"]
    lines = [
        "# Cost per use case and per agent", "",
        f"Subscription scope, window {w['start']} to {w['end']} (end exclusive, {w['days']} days), "
        f"generated {doc['generated_at']}. Use cases come from resource-group tags "
        f"({', '.join(doc['scope']['tag_keys'])}, first match wins).", "",
        "**Measured**: Cost Management usage cost. **Calculated**: allocation and unit cost derived from "
        "measured cost and telemetry. **Forecast**: Cost Management forecast; a run-rate is labelled as such.", "",
        "| Use case | Measured cost | Successful agent invocations | Calculated cost per successful invocation | Next 30 days | Forecast source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for u in doc["use_cases"]:
        lines.append(f"| {u['use_case']} | {_usd(u['actual_usd'])} | "
                     f"{u['successful_invocations'] if u['successful_invocations'] is not None else 'no telemetry'} | "
                     f"{_usd(u['cost_per_successful_invocation_usd'], 4)} | {_usd(u['forecast']['next_30_days_usd'])} | "
                     f"{'Forecast (Cost Management)' if u['forecast']['source'] == 'cost-management-forecast' else 'Run-rate (not a forecast)'} |")
    ua = doc["unattributed"]
    lines += [f"| Unattributed ({ua['resource_groups']} resource groups without a matching tag) | "
              f"{_usd(ua['actual_usd'])} | | | | |",
              f"| **Subscription total** | **{_usd(doc['totals']['subscription_actual_usd'])}** | | | | |", ""]
    agents = [(u["use_case"], a) for u in doc["use_cases"] for a in u["agents"]]
    if agents:
        lines += ["## Agents sharing a resource group", "",
                  "Calculated: the resource group's measured cost split by each agent's share of successful "
                  "`invoke_agent` spans. It assumes a similar cost per invocation; token-weighted allocation "
                  "needs `gen_ai.usage.*` attributes on the spans.", "",
                  "| Use case | Agent | Successful / total invocations | Allocated cost | Cost per successful invocation |",
                  "| --- | --- | --- | --- | --- |"]
        lines += [f"| {uc} | {a['agent']} | {a['successful_invocations']} / {a['invocations']} | "
                  f"{_usd(a['allocated_usd'])} | {_usd(a['cost_per_successful_invocation_usd'], 4)} |" for uc, a in agents]
        lines.append("")
    lines += [
        "## What these numbers are not", "",
        "- Cost per successful invocation counts agent invocations from telemetry, not the SPEC § 14 success "
        "event; the verified per-pilot KPI-003 comes from `consumption_iq.py reconcile`.",
        f"- {doc['boundary']['cost_basis']}",
        f"- In scope: {doc['boundary']['in_scope']}",
        f"- Out of scope: {doc['boundary']['out_of_scope']}",
    ]
    lines += [f"- Warning: {m}" for m in doc["warnings"]]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI

def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="consumption_iq portfolio",
                                description="Read-only cost per use case and per agent across a subscription.")
    p.add_argument("--subscription", required=True)
    p.add_argument("--start", type=_date, required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument("--end", type=_date, required=True, help="YYYY-MM-DD, exclusive")
    p.add_argument("--use-case-tag", action="append", dest="tag_keys",
                   help=f"Resource-group tag key naming the use case; repeatable, first match wins "
                        f"(default: {', '.join(DEFAULT_TAG_KEYS)})")
    p.add_argument("--workspace-resource-id", action="append", default=[],
                   help="Log Analytics workspace ARM ID holding agent telemetry; repeatable.")
    p.add_argument("--forecast-top", type=int, default=10,
                   help="Request a Cost Management forecast for the N most costly use cases (default 10).")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = p.parse_args(argv)
    tag_keys = args.tag_keys or list(DEFAULT_TAG_KEYS)
    try:
        bundle = collect(args.subscription, args.start, args.end, tag_keys=tag_keys,
                         workspace_resource_ids=args.workspace_resource_id, forecast_top=args.forecast_top)
        doc = build_portfolio(**bundle, tag_keys=tag_keys, window=(args.start, args.end),
                              generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              subscription_id=args.subscription)
    except (az.ActualsSourceError, PortfolioError) as exc:
        print(f"portfolio: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"portfolio: {exc}", file=sys.stderr)
        return 2
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(render_markdown(doc), encoding="utf-8")
    for m in doc["warnings"]:
        print(f"warning: {m}", file=sys.stderr)
    print(f"portfolio: {len(doc['use_cases'])} use cases, total {_usd(doc['totals']['subscription_actual_usd'])} "
          f"-> {args.manifest}, {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
