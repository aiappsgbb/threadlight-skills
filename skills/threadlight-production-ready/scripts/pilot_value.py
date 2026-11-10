#!/usr/bin/env python3
"""Pilot value page: one post-pilot view for the process owner and sponsor.

Reads only the evidence other Threadlight steps already wrote and renders:

* ``docs/pilot-value.html`` — self-contained page (no script, no network);
* ``docs/pilot-value.md``   — the same content as Markdown;
* ``specs/pilot-value.json`` — summary (``threadlight-pilot-value/v1``) the
  Lifecycle Canvas shows next to "Go-live and value".

Every item carries exactly one evidence kind:

``measured``      observed on the deployed pilot (Foundry eval run, recorded
                  scenario run, safety check, reconciled billing data);
``calculated``    arithmetic on measured numbers (shown with its formula);
``estimate``      a planning or forecast figure, never observed;
``target``        what the team set out to achieve (SPEC);
``not-measured``  no evidence yet — rendered "Not measured yet", never filled.

The measured cost per successful interaction (production-ready KPI-003) comes
from ``production_ready._cost_evidence_summary``: the same digest-pinned,
re-derived reconciliation reader the readiness review uses. A forecast in
``specs/cost-manifest.json`` is never shown as measured.

CLI::

    python <threadlight-skills>/skills/threadlight-production-ready/scripts/pilot_value.py \\
        --workspace <pilot-root> [--generated-at 2026-11-10T12:00:00+00:00]

Exit 0 after writing the three files, 2 when the workspace does not exist.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "threadlight-pilot-value/v1"
PAGE = "docs/pilot-value.html"
MARKDOWN = "docs/pilot-value.md"
SUMMARY = "specs/pilot-value.json"

KINDS = ("measured", "calculated", "estimate", "target", "not-measured")
KIND_LABELS = {
    "measured": "Measured",
    "calculated": "Calculated",
    "estimate": "Estimate",
    "target": "Target",
    "not-measured": "Not measured yet",
}
KIND_MEANING = {
    "measured": "Observed on the deployed pilot: an evaluation run, a recorded scenario run, a safety check or billing data.",
    "calculated": "Arithmetic on measured numbers. The formula is shown.",
    "estimate": "A planning or forecast figure. Nobody has observed it yet.",
    "target": "What the team set out to achieve, from the pilot specification.",
    "not-measured": "No evidence yet. The page leaves it empty rather than guess.",
}
NOT_MEASURED = "Not measured yet"

SOURCES = (
    ("specs/SPEC.md", "Pilot specification: process, targets and business KPIs"),
    ("specs/foundry-package-manifest.json", "Foundry agent, tracing and evaluation run"),
    ("tests/killer-prompts.md", "Showcase scenarios"),
    ("docs/invoke-results.md", "Recorded run of the showcase scenarios"),
    ("tests/postdeploy-manifest.json", "Post-deploy safety check"),
    ("tests/production-readiness-manifest.json", "Production-readiness review"),
    ("specs/cost-manifest.json", "Running-cost estimate"),
    ("specs/cost-actuals-manifest.json", "Collected billing data"),
    ("specs/cost-reconciliation-manifest.json", "Reconciled cost per successful interaction"),
    ("qualification/roi.md", "Business case from qualification"),
)

NEXT_STEPS = {
    "eval": "Run the Foundry evaluation against the deployed agent (evals/run_evals.py).",
    "foundry-package": "Record the deployed agent and its trace (scripts/record_deploy_evidence.py).",
    "scenarios": "Run the showcase scenarios against the live agent and record one PASS or FAIL per scenario in docs/invoke-results.md.",
    "safety-check": "Run the post-deploy safety check (threadlight-safe-check).",
    "readiness": "Run the production-readiness review (threadlight-production-ready).",
    "measured-cost": "After a period of real use, collect actual cost and reconcile it (threadlight-consumption-iq actuals and reconciliation).",
    "estimated-cost": "Estimate the running cost (threadlight-consumption-iq).",
    "business-case": "Add today's annual cost and handling minutes per case to the qualification (threadlight-qualify), which produces the business case.",
    "kpis": "No Threadlight step measures these business KPIs yet. Measure them from your own systems during a shadow run.",
}


# ---------------------------------------------------------------------------
# Small readers (never raise on content)
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path) -> tuple[str, Any]:
    text = _read_text(path)
    if text is None:
        return ("missing" if not path.exists() else "invalid", None)
    try:
        return ("present", json.loads(text))
    except (ValueError, RecursionError):
        return ("invalid", None)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _usd(value: float, places: int = 2) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{places}f}"


def _plain_number(value: float) -> str:
    return f"{value:g}"


def _date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return f"{parsed.astimezone(timezone.utc):%d %b %Y}".lstrip("0")


def _item(item_id: str, label: str, kind: str, value: str, *, detail: str = "",
          source: str | None = None, as_of: str | None = None,
          status: str = "neutral", measured: str | None = None) -> dict[str, Any]:
    assert kind in KINDS
    return {
        "id": item_id, "label": label, "kind": kind, "value": value, "detail": detail,
        "source": source, "as_of": as_of, "status": status, "measured": measured,
    }


def _not_measured(item_id: str, label: str, *, detail: str = "", source: str | None = None) -> dict[str, Any]:
    return _item(item_id, label, "not-measured", NOT_MEASURED, detail=detail, source=source)


# ---------------------------------------------------------------------------
# SPEC
# ---------------------------------------------------------------------------

def _spec_field(spec: str, name: str) -> str | None:
    lines = spec.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf"\s*\*\*{re.escape(name)}\*\*\s*:\s*(.*)$", line)
        if not match:
            continue
        parts = [match.group(1).strip()]
        for follow in lines[index + 1:]:
            if (not follow.strip() or follow.lstrip().startswith(("#", "-", "|", ">"))
                    or re.match(r"\s*\*\*[^*]+\*\*\s*:", follow)):
                break
            parts.append(follow.strip())
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", " ".join(part for part in parts if part))
        return text or None
    return None


def _pilot_identity(root: Path, spec: str) -> dict[str, str | None]:
    name = _spec_field(spec, "Name")
    if not name:
        heading = re.search(r"^#\s+(.+)$", spec, re.M)
        if heading:
            name = re.sub(r"^SpecKit:\s*", "", heading.group(1).strip())
    description = _spec_field(spec, "Description")
    if description:
        description = description.replace("`", "")
    return {"name": name or root.resolve().name, "description": description}


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        rows.append(cells)
    return rows


_METRIC_WORDS = {"pct": "(%)", "p50": "(median)", "p95": "(95th percentile)", "p99": "(99th percentile)"}


def _humanize_metric(name: str) -> str:
    words = name.strip().strip("`").replace("_", " ").split()
    text = " ".join(_METRIC_WORDS.get(word.lower(), word) for word in words)
    return text[:1].upper() + text[1:] if text else text


def _business_kpis(spec: str) -> list[dict[str, Any]]:
    lines = spec.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.match(r"^#{2,4}\s+.*Business KPIs", line)), None)
    if start is None:
        return []
    body = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        body.append(line)
    # The SPEC template puts an "industry exemplars" table first; use the
    # first table under the heading that has both a KPI and a Target column.
    rows: list[list[str]] = []
    name_col = target_col = -1
    offset = 0
    while offset < len(body):
        table_start = next((i for i in range(offset, len(body)) if body[i].strip().startswith("|")), None)
        if table_start is None:
            break
        table_end = next((i for i in range(table_start, len(body)) if not body[i].strip().startswith("|")), len(body))
        candidate = _table_rows(body[table_start:table_end])
        offset = table_end
        if len(candidate) < 2:
            continue
        header = [cell.lower() for cell in candidate[0]]
        kpi = next((i for i, cell in enumerate(header) if "kpi" in cell), None)
        if kpi is not None and "target" in header:
            rows, name_col, target_col = candidate, kpi, header.index("target")
            break
    if not rows:
        return []
    items = []
    for index, row in enumerate(rows[1:], start=1):
        if len(row) <= max(name_col, target_col) or not row[name_col]:
            continue
        label = _humanize_metric(row[name_col])
        target = row[target_col].replace("`", "") or "Not stated"
        items.append(_item(
            f"kpi-{index}", label, "target", target,
            source="specs/SPEC.md", measured=None,
        ))
    return items


# ---------------------------------------------------------------------------
# Quality: Foundry evaluation and showcase scenarios
# ---------------------------------------------------------------------------

_EVAL_LABEL = "Evaluation of the deployed agent"
_PACKAGE_LABEL = "Running on Microsoft Foundry"


def _foundry_checker() -> tuple[Any, Any] | None:
    repo = Path(__file__).resolve().parents[3]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from skills._shared.foundry_package import FoundryPackageError, evaluate
    except Exception:  # pragma: no cover - standalone copy without skills/_shared
        return None
    return FoundryPackageError, evaluate


def _eval_item(manifest: dict[str, Any], missing: list[str], source: str) -> dict[str, Any]:
    # A score is evidence only when the record that carries it is sound: the
    # agent is real (no fallback), the run targets it, and the rubric is complete.
    # A rubric below its bar is a result to show, not a broken record.
    blocking = [m for m in missing
                if m.startswith(("agent", "evaluation", "continuous_evaluation"))
                and "score below threshold" not in m]
    if blocking:
        return _not_measured("eval", _EVAL_LABEL, source=source,
                             detail="No sound evaluation run of the deployed agent is recorded.")
    evaluation = manifest["evaluation"]
    scored = []
    for rubric in evaluation.get("custom_rubric_evaluators") or []:
        score, threshold = _number(rubric.get("score")), _number(rubric.get("threshold"))
        if score is not None and threshold is not None:
            scored.append((score - threshold, score, threshold, rubric))
    if not scored:
        return _not_measured("eval", _EVAL_LABEL, source=source,
                             detail="No executed evaluation run with a score is recorded.")
    _, score, threshold, rubric = min(scored, key=lambda entry: entry[0])
    criteria = rubric.get("derived_from_acceptance_criteria")
    count = len(criteria) if isinstance(criteria, list) else 0
    met = all(entry[1] >= entry[2] for entry in scored)
    scope = f"{count} business rules and test scenarios" if count else "the business rules"
    lowest = f" This is the lowest of {len(scored)} scored rubrics." if len(scored) > 1 else ""
    return _item(
        "eval", _EVAL_LABEL, "measured", f"{score:.1f} out of 5",
        detail=(f"Microsoft Foundry scored the deployed agent's answers against {scope} "
                f"from the specification, against a bar of {_plain_number(threshold)}. "
                + ("It met the bar." if met else "It is below the bar.") + lowest),
        source=source, status="good" if met else "attention",
    )


def _foundry_items(root: Path) -> list[dict[str, Any]]:
    source = "specs/foundry-package-manifest.json"
    state, manifest = _read_json(root / source)
    if state == "missing":
        return [_not_measured("eval", _EVAL_LABEL), _not_measured("foundry-package", _PACKAGE_LABEL)]
    checker = _foundry_checker()
    if checker is None:  # pragma: no cover - standalone copy without skills/_shared
        detail = "The delivery checker (skills/_shared/foundry_package.py) is unavailable."
        return [_not_measured("eval", _EVAL_LABEL, detail=detail, source=source),
                _not_measured("foundry-package", _PACKAGE_LABEL, detail=detail, source=source)]
    error_type, evaluate = checker
    try:
        if state != "present":
            raise error_type("unreadable")
        result = evaluate(manifest)
    except (error_type, OverflowError, RecursionError, TypeError, ValueError):
        detail = f"{source} is not a valid delivery record."
        return [_not_measured("eval", _EVAL_LABEL, detail=detail, source=source),
                _not_measured("foundry-package", _PACKAGE_LABEL, detail=detail, source=source)]

    missing = [str(m) for m in result.get("missing") or []]
    items = [_eval_item(manifest, missing, source)]
    if result.get("status") == "COMPLETE":
        items.append(_item(
            "foundry-package", _PACKAGE_LABEL, "measured", "Deployed, traced and evaluated",
            detail="A real Foundry agent is recorded, with a trace in Application Insights and an executed evaluation run.",
            source=source, status="good",
        ))
    else:
        shown = "; ".join(missing[:3]) + (f"; and {len(missing) - 3} more" if len(missing) > 3 else "")
        items.append(_item(
            "foundry-package", _PACKAGE_LABEL, "measured", "Delivery evidence incomplete",
            detail=f"Missing: {shown}." if shown else "", source=source, status="attention",
        ))
    return items


_RANK_RE = re.compile(r"\bK(\d+)\b")
_VERDICT_RE = re.compile(r"\b(PASS|PASSED|FAIL|FAILED)\b|(✅)|(❌)")


def _invoke_outcomes(text: str) -> dict[str, str]:
    """One verdict per scenario: the first upper-case PASS/FAIL (or ✅/❌) after
    its ``K<n>`` and before the next ``K<n>`` on the same line."""
    outcomes: dict[str, str] = {}
    for line in text.splitlines():
        ranks = list(_RANK_RE.finditer(line))
        for index, match in enumerate(ranks):
            end = ranks[index + 1].start() if index + 1 < len(ranks) else len(line)
            verdict = _VERDICT_RE.search(line, match.end(), end)
            if not verdict:
                continue
            failed = verdict.group(3) or (verdict.group(1) or "").startswith("FAIL")
            key = f"K{match.group(1)}"
            if outcomes.get(key) != "fail":
                outcomes[key] = "fail" if failed else "pass"
    return outcomes


def _scenario_items(root: Path) -> list[dict[str, Any]]:
    source = "tests/killer-prompts.md"
    text = _read_text(root / source)
    rows = _table_rows(text.splitlines()) if text else []
    if len(rows) < 2:
        return [_not_measured("scenario-none", "Showcase scenarios",
                              detail="No showcase scenarios are defined yet.")]
    header = [cell.lower() for cell in rows[0]]

    def column(name: str) -> int | None:
        return next((i for i, cell in enumerate(header) if name in cell), None)

    rank_col, prompt_col, wow_col = column("rank"), column("prompt"), column("wow")
    if rank_col is None or prompt_col is None:
        return [_not_measured("scenario-none", "Showcase scenarios", source=source,
                              detail=f"{source} has no Rank and Prompt columns.")]

    invoke_source = "docs/invoke-results.md"
    invoke_text = _read_text(root / invoke_source)
    outcomes = _invoke_outcomes(invoke_text) if invoke_text else {}
    items = []
    for row in rows[1:]:
        if len(row) <= max(rank_col, prompt_col):
            continue
        rank = row[rank_col].strip()
        prompt = row[prompt_col].strip()
        wow = row[wow_col].strip().strip('"“”') if wow_col is not None and len(row) > wow_col else ""
        outcome = outcomes.get(rank)
        label = f"{rank}: {prompt}"
        if outcome:
            items.append(_item(
                f"scenario-{rank}", label, "measured",
                "Passed" if outcome == "pass" else "Failed",
                detail=wow, source=invoke_source,
                status="good" if outcome == "pass" else "attention",
            ))
        elif invoke_text is not None:
            items.append(_item(
                f"scenario-{rank}", label, "not-measured",
                "Run recorded, result not stated",
                detail=wow, source=invoke_source,
            ))
        else:
            items.append(_item(
                f"scenario-{rank}", label, "not-measured",
                "Not run against the live agent yet",
                detail=wow, source=source,
            ))
    return items or [_not_measured("scenario-none", "Showcase scenarios", source=source)]


# ---------------------------------------------------------------------------
# Running safely
# ---------------------------------------------------------------------------

def _safety_item(root: Path) -> dict[str, Any]:
    source = "tests/postdeploy-manifest.json"
    state, manifest = _read_json(root / source)
    label = "Post-deploy safety check"
    if state != "present" or not isinstance(manifest, dict):
        return _not_measured(label="Post-deploy safety check", item_id="safety-check",
                             source=source if state == "invalid" else None)
    if manifest.get("phase") != "post-deploy":
        return _not_measured("safety-check", label, source=source,
                             detail="The check has only been prepared; it has not run against the live deployment.")
    gaps = manifest.get("gaps")
    checked = _date(manifest.get("checked_at"))
    if isinstance(gaps, list) and not gaps and checked:
        return _item("safety-check", label, "measured", "Passed",
                     detail="The live deployment matches what was designed, with no open gaps.",
                     source=source, as_of=checked, status="good")
    count = len(gaps) if isinstance(gaps, list) else 0
    return _item("safety-check", label, "measured",
                 f"{count} gap{'s' if count != 1 else ''} open" if count else "Not confirmed",
                 detail="The live deployment does not yet fully match the design.",
                 source=source, as_of=checked, status="attention")


def _readiness_item(root: Path) -> dict[str, Any]:
    source = "tests/production-readiness-manifest.json"
    state, manifest = _read_json(root / source)
    label = "Production-readiness review"
    if state != "present" or not isinstance(manifest, dict):
        return _not_measured("readiness", label)
    recommendation = manifest.get("go_live_recommendation")
    as_of = _date(manifest.get("checked_at")) or _date(manifest.get("generated_at"))
    confirm = "Confirm the full proof chain in the Lifecycle Canvas before acting on it."
    if recommendation == "ready":
        return _item("readiness", label, "measured", "Review recommends going live",
                     detail=confirm, source=source, as_of=as_of, status="good")
    qualified = {
        "ready_with_waivers": "waivers",
        "ready_with_residual_risk": "residual risk",
        "ready_with_unverified_risk": "unverified risk",
    }
    if recommendation in qualified:
        return _item("readiness", label, "measured",
                     f"Review recommends going live with {qualified[recommendation]}",
                     detail=f"Its report lists the {qualified[recommendation]} and their owners. {confirm}",
                     source=source, as_of=as_of, status="attention")
    words = str(recommendation).replace("_", " ") if isinstance(recommendation, str) else "not stated"
    return _item("readiness", label, "measured", "Not ready yet",
                 detail=f"The review's recommendation: {words}. Its report lists what is missing.",
                 source=source, as_of=as_of, status="attention")


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

_PRODUCTION_READY: Any = None


def production_ready_module() -> Any:
    """The sibling readiness module this page reads cost evidence through (cached)."""
    global _PRODUCTION_READY
    if _PRODUCTION_READY is None:
        scripts = Path(__file__).resolve().parent
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import production_ready

        _PRODUCTION_READY = production_ready
    return _PRODUCTION_READY


def _measured_cost_summary(root: Path, spec: str) -> dict[str, Any] | None:
    try:
        pr = production_ready_module()
    except Exception:  # pragma: no cover - missing sibling module
        return None
    _, manifest = _read_json(root / "specs" / "manifest.json")
    azd_env = {key: value for key, value in pr._load_azd_env(root).items()
               if key in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "RESOURCE_GROUP_NAME")}
    ctx = pr.RepoContext(
        root=root, bicep_files=[], src_files=[], test_files=[], spec_text=spec,
        spec_12={}, spec_11b={}, azure_yaml_text="", docs_text="", azd_env=azd_env,
        manifest=manifest if isinstance(manifest, dict) else {},
        bicep_graph=pr.BicepGraph(resources=[], source_files=[]),
    )
    try:
        summary = pr._cost_evidence_summary(ctx)
    except Exception:  # never let one reader break the page
        return None
    return summary if isinstance(summary, dict) else None


def _window_days(start: Any, end: Any) -> int | None:
    try:
        first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        last = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        days = round((last - first).total_seconds() / 86400)
    except TypeError:
        return None
    return days if days > 0 else None


def _cost_items(root: Path, spec: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    recon_source = "specs/cost-reconciliation-manifest.json"
    summary = _measured_cost_summary(root, spec) if (root / recon_source).exists() else None
    unit_cost = _number(summary.get("cost_per_successful_interaction_usd")) if summary and summary.get("status") == "pass" else None

    if unit_cost is not None:
        _, recon = _read_json(root / recon_source)
        unit = recon.get("unit_economics") if isinstance(recon, dict) and isinstance(recon.get("unit_economics"), dict) else {}
        successes = unit.get("successful_interactions")
        days = _window_days(summary.get("actuals_window_start"), summary.get("actuals_window_end"))
        window = f" over {days} days" if days else ""
        count = f"{successes:,} successful interactions" if isinstance(successes, int) and not isinstance(successes, bool) else "successful interactions"
        as_of = _date(summary.get("actuals_window_end"))
        items.append(_item(
            "measured-cost", "Cost per successful interaction", "measured",
            f"{_usd(unit_cost, 4)} per successful interaction",
            detail=f"Actual billed cost divided by {count}{window}, from reconciled billing data.",
            source=recon_source, as_of=as_of,
        ))
        target = _number(unit.get("target_usd"))
        if target is not None and unit.get("target_status") in ("pass", "should-fix"):
            within = unit.get("target_status") == "pass"
            items.append(_item(
                "cost-target", "Target cost per successful interaction", "target",
                f"{_usd(target, 4)} per successful interaction",
                detail="The measured cost is within this target." if within else "The measured cost is above this target.",
                source="specs/SPEC.md", status="good" if within else "attention",
                measured=f"{_usd(unit_cost, 4)} per successful interaction",
            ))
        spend = _number(summary.get("actual_window_usd"))
        if spend is not None:
            items.append(_item(
                "measured-spend", "Actual spend in the measured period", "measured",
                f"{_usd(spend)}{window}", detail="Billed cost for the pilot's resources.",
                source="specs/cost-actuals-manifest.json", as_of=as_of,
            ))
            if days:
                items.append(_item(
                    "run-rate", "Monthly cost at the measured pace", "calculated",
                    f"{_usd(spend * 30 / days)} a month",
                    detail=f"= actual spend × 30 ÷ {days} days. Real traffic may differ.",
                    source="specs/cost-actuals-manifest.json",
                ))
    else:
        detail = ""
        if summary and summary.get("status") != "pass" and isinstance(summary.get("detail"), str):
            detail = "Reconciled cost evidence is not verified yet."
        items.append(_not_measured("measured-cost", "Cost per successful interaction", detail=detail,
                                   source=recon_source if (root / recon_source).exists() else None))

    source = "specs/cost-manifest.json"
    state, forecast = _read_json(root / source)
    totals = forecast.get("totals") if isinstance(forecast, dict) and isinstance(forecast.get("totals"), dict) else None
    label = "Estimated running cost"
    if totals is None:
        items.append(_not_measured("estimated-cost", label, source=source if state == "invalid" else None))
        return items
    as_of = _date(forecast.get("generated_at"))
    monthly = _number(totals.get("monthly_cost_current_usd"))
    if totals.get("complete") is True and monthly is not None:
        per = _number(totals.get("cost_per_transaction_usd"))
        detail = "Forecast from the planned load and retail prices, not observed."
        if per is not None:
            detail = f"About {_usd(per, 4)} per interaction. " + detail
        items.append(_item("estimated-cost", label, "estimate", f"About {_usd(monthly)} a month",
                           detail=detail, source=source, as_of=as_of))
    else:
        known = _number(totals.get("monthly_cost_known_usd"))
        value = f"Partial estimate: {_usd(known)} a month priced so far" if known is not None else "Partial estimate"
        items.append(_item("estimated-cost", label, "estimate", value,
                           detail="Some resources are not priced yet, so the total is incomplete.",
                           source=source, as_of=as_of, status="attention"))
    return items


# ---------------------------------------------------------------------------
# Business case
# ---------------------------------------------------------------------------

_MONEY = r"(-?\$-?[\d,]+(?:\.\d+)?)"


def _business_case_item(root: Path) -> dict[str, Any]:
    source = "qualification/roi.md"
    label = "Net annual benefit"
    text = _read_text(root / source)
    if text is None:
        detail = ""
        if (root / "qualification" / "sizing-manifest.json").exists():
            detail = "Sizing exists, but today's annual cost and handling time were not supplied."
        return _not_measured("business-case", label, detail=detail)
    current = re.search(r"Current annual cost[^:]*:\s*" + _MONEY, text)
    projected = re.search(r"Projected annual solution cost:\s*" + _MONEY, text)
    net = re.search(r"Net annual benefit:\s*" + _MONEY, text)
    if current and net and projected:
        return _item(
            "business-case", label, "estimate", f"{net.group(1)} a year",
            detail=(f"Planning estimate: today's annual cost of {current.group(1)} (supplied by your team) "
                    f"minus a projected solution cost of {projected.group(1)} a year. "
                    "Labour savings are not added in."),
            source=source,
        )
    if current and "incomplete" in text.lower():
        return _item(
            "business-case", label, "estimate", "Partial estimate",
            detail=(f"Today's annual cost is {current.group(1)} (supplied by your team), but the projected "
                    "solution cost is not fully priced, so no net benefit is stated."),
            source=source, status="attention",
        )
    return _not_measured("business-case", label, source=source,
                         detail=f"{source} is present but its figures could not be read.")


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _scenario_summary(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [s for s in scenarios if s["kind"] == "measured"]
    if not measured:
        return _item("scenarios", "Showcase scenarios", "not-measured",
                     NOT_MEASURED if scenarios and scenarios[0]["id"] == "scenario-none"
                     else f"{len(scenarios)} defined, none run against the live agent yet")
    passed = sum(1 for s in measured if s["value"] == "Passed")
    return _item("scenarios", "Showcase scenarios", "measured",
                 f"{passed} of {len(scenarios)} passed",
                 status="good" if passed == len(scenarios) else "attention")


def build_report(root: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    root = Path(root)
    spec = _read_text(root / "specs" / "SPEC.md") or ""
    identity = _pilot_identity(root, spec)

    foundry = _foundry_items(root)
    eval_item = next(i for i in foundry if i["id"] == "eval")
    package_item = next(i for i in foundry if i["id"] == "foundry-package")
    scenarios = _scenario_items(root)
    safety = _safety_item(root)
    readiness = _readiness_item(root)
    cost = _cost_items(root, spec)
    case = _business_case_item(root)
    kpis = _business_kpis(spec)

    sections = [
        {"id": "quality", "title": "Does it do the job right?",
         "intro": "How well the deployed agent handles the process, scored and run against the agreed rules.",
         "items": [eval_item, *scenarios]},
        {"id": "live", "title": "Is it running safely?",
         "intro": "Whether the pilot is really deployed on Microsoft Foundry and checked after deployment.",
         "items": [package_item, safety, readiness]},
        {"id": "cost", "title": "What does it cost to run?",
         "intro": "Measured cost comes from billing data; estimates are forecasts made before real use.",
         "items": cost},
        {"id": "case", "title": "Is it worth continuing?",
         "intro": "The business case and the targets the team agreed before the pilot.",
         "items": [case, *kpis]},
    ]

    measured_cost = next(i for i in cost if i["id"] == "measured-cost")
    estimated_cost = next(i for i in cost if i["id"] == "estimated-cost")
    cost_answer = measured_cost if measured_cost["kind"] != "not-measured" else (
        estimated_cost if estimated_cost["kind"] != "not-measured" else measured_cost)
    answers = [
        {"question": "Does it handle cases correctly?", "item": eval_item},
        {"question": "Did the showcase scenarios work live?", "item": _scenario_summary(scenarios)},
        {"question": "Is it running on Microsoft Foundry?", "item": package_item},
        {"question": "What does a successful interaction cost?", "item": cost_answer},
        {"question": "Is the business case there?", "item": case},
    ]

    all_items = [item for section in sections for item in section["items"]]
    counts = {kind.replace("-", "_"): sum(1 for i in all_items if i["kind"] == kind) for kind in KINDS}

    open_steps: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in all_items:
        key = "scenarios" if item["id"].startswith("scenario-") else "kpis" if item["id"].startswith("kpi-") else item["id"]
        pending = item["kind"] == "not-measured" or (key == "kpis" and item["measured"] is None)
        if pending and key in NEXT_STEPS and key not in seen:
            seen.add(key)
            label = "Showcase scenarios" if key == "scenarios" else "Business KPIs" if key == "kpis" else item["label"]
            open_steps.append({"label": label, "step": NEXT_STEPS[key]})

    sources = [{"path": path, "what": what, "present": (root / path).is_file()} for path, what in SOURCES]
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or _now(),
        "pilot": identity,
        "page": PAGE,
        "counts": counts,
        "answers": answers,
        "sections": sections,
        "open": open_steps,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def _md_value(item: dict[str, Any]) -> str:
    if item["kind"] == "target":
        return f"Target {item['value']}; measured: {item['measured'] or NOT_MEASURED}"
    return item["value"]


def render_markdown(report: dict[str, Any]) -> str:
    pilot = report["pilot"]
    lines = [f"# {pilot['name']}: pilot value", ""]
    if pilot.get("description"):
        lines += [pilot["description"], ""]
    lines += [
        f"Generated {_date(report['generated_at']) or report['generated_at']} from the pilot's own evidence files. "
        "Nothing here is typed in by hand; anything without evidence says **Not measured yet**.",
        "",
        "## The short answer",
        "",
        "| Question | What we know | Evidence |",
        "|---|---|---|",
    ]
    for answer in report["answers"]:
        item = answer["item"]
        lines.append(f"| {_md(answer['question'])} | {_md(item['value'])} | {KIND_LABELS[item['kind']]} |")
    lines += ["", "## How to read the evidence labels", ""]
    for kind in KINDS:
        lines.append(f"- **{KIND_LABELS[kind]}**: {KIND_MEANING[kind]}")
    for section in report["sections"]:
        lines += ["", f"## {section['title']}", "", section["intro"], "",
                  "| Item | Result | Evidence | Detail | Source |", "|---|---|---|---|---|"]
        for item in section["items"]:
            source = f"`{item['source']}`" if item["source"] else ""
            if item.get("as_of"):
                source = f"{source} ({item['as_of']})" if source else item["as_of"]
            lines.append(
                f"| {_md(item['label'])} | {_md(_md_value(item))} | {KIND_LABELS[item['kind']]} | "
                f"{_md(item['detail'])} | {source} |")
    if report["open"]:
        lines += ["", "## Still to find out", ""]
        for step in report["open"]:
            lines.append(f"- **{step['label']}**: {step['step']}")
    lines += ["", "## Sources", "", "| File | What it holds | Found |", "|---|---|---|"]
    for source in report["sources"]:
        lines.append(f"| `{source['path']}` | {source['what']} | {'yes' if source['present'] else 'no'} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _e(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


_ICON_PATHS = {
    "measured": '<circle cx="8" cy="8" r="6.25"/><path d="M5.2 8.2l1.9 1.9 3.8-4"/>',
    "calculated": '<circle cx="8" cy="8" r="6.25"/><path d="M5.25 6.6h5.5M5.25 9.4h5.5"/>',
    "estimate": '<circle cx="8" cy="8" r="6.25"/><path d="M4.9 8.7c.9-1.4 1.9-1.4 3.1-.5s2.2.9 3.1-.5"/>',
    "target": '<circle cx="8" cy="8" r="6.25"/><circle cx="8" cy="8" r="2.6"/>',
    "not-measured": '<circle cx="8" cy="8" r="6.25" stroke-dasharray="2.2 2.1"/>',
}


def _icon(kind: str) -> str:
    return ('<svg class="icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" '
            'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
            f'stroke-linejoin="round">{_ICON_PATHS[kind]}</svg>')


def _badge(kind: str) -> str:
    return f'<span class="kind k-{kind}">{_icon(kind)}{_e(KIND_LABELS[kind])}</span>'


def _value_html(item: dict[str, Any]) -> str:
    status = f" st-{item['status']}" if item["kind"] != "not-measured" else " st-none"
    if item["kind"] == "target":
        measured = item["measured"] or NOT_MEASURED
        return (f'<span class="val{status}">{_e(item["value"])}</span>'
                f'<span class="measured-line">Measured: {_e(measured)}</span>')
    return f'<span class="val{status}">{_e(item["value"])}</span>'


_CSS = """
:root{--paper:#fbfaf7;--paper-2:#f3f1ec;--ink:#14161c;--ink-2:#454b5a;--ink-3:#5f6577;--rule:#dcd8cf;--rule-2:#c9c4b8;
--accent:#3a55e0;--good:#1f7a43;--good-bg:#e5f3e9;--warn:#9a5b00;--warn-bg:#fbefd9;--est:#6a4bc2;--est-bg:#efeafb;
--serif:"Source Serif 4","Source Serif Pro","Iowan Old Style",Georgia,serif;--sans:"Inter","Segoe UI",system-ui,-apple-system,BlinkMacSystemFont,sans-serif}
*{box-sizing:border-box}
html{background:var(--paper);color:var(--ink);font:16px/1.6 var(--sans);-webkit-text-size-adjust:100%}
body{margin:0}
::selection{background:#d6ddff;color:var(--ink)}
a{color:var(--accent);text-underline-offset:3px}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
.wrap{max-width:1040px;margin:0 auto;padding:56px 32px 80px}
header.top{border-bottom:1px solid var(--rule);padding-bottom:32px;margin-bottom:40px}
h1,h2,h3{font-family:var(--serif);font-weight:500;letter-spacing:-.015em;color:var(--ink);text-wrap:balance}
h1{font-size:clamp(2rem,4.4vw,3.1rem);line-height:1.08;margin:0 0 16px}
h2{font-size:clamp(1.45rem,2.6vw,1.9rem);line-height:1.2;margin:56px 0 8px}
.lede{font-family:var(--serif);font-size:clamp(1.08rem,1.6vw,1.25rem);line-height:1.55;color:var(--ink-2);max-width:68ch;margin:0 0 16px}
.meta{color:var(--ink-3);font-size:.9rem;max-width:72ch;margin:0}
.intro{color:var(--ink-2);max-width:68ch;margin:0 0 20px}
.kind{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;font-weight:600;letter-spacing:.01em;white-space:nowrap;
padding:3px 10px 3px 7px;border-radius:999px;border:1px solid transparent}
.icon{flex:none}
.k-measured{color:var(--good);background:var(--good-bg)}
.k-calculated{color:#1d5b8a;background:#e3eef8}
.k-estimate{color:var(--est);background:var(--est-bg)}
.k-target{color:#2c3a8f;background:#e7eafb}
.k-not-measured{color:var(--ink-3);background:transparent;border-color:var(--rule-2);border-style:dashed}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th{font-size:.78rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3);text-align:left;padding:0 16px 10px 0;border-bottom:1px solid var(--rule-2)}
td{padding:16px 16px 16px 0;border-bottom:1px solid var(--rule);vertical-align:top}
td:last-child,th:last-child{padding-right:0}
.answer td.q{font-family:var(--serif);font-size:1.12rem;width:38%;color:var(--ink)}
.answer td.a{width:40%}
.val{display:block;font-weight:600;font-size:1.02rem;color:var(--ink)}
.val.st-good{color:var(--good)}
.val.st-attention{color:var(--warn)}
.val.st-none{color:var(--ink-3);font-weight:500}
.measured-line{display:block;color:var(--ink-3);font-size:.88rem;margin-top:2px}
.items td.label{width:32%;font-weight:600;color:var(--ink)}
.items td.result{width:30%}
.detail{display:block;color:var(--ink-2);font-size:.92rem;margin-top:6px;max-width:62ch}
.src{display:block;color:var(--ink-3);font-size:.8rem;margin-top:6px;font-family:ui-monospace,"Cascadia Mono",Menlo,monospace;word-break:break-all}
.legend{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:20px 28px;margin:28px 0 0;padding:20px 0 0;border-top:1px solid var(--rule)}
.legend div{display:flex;flex-direction:column;gap:6px}
.legend dt{margin:0}.legend dd{margin:0;color:var(--ink-2);font-size:.88rem;line-height:1.45}
ol.open{margin:0;padding:0;list-style:none;counter-reset:step;max-width:76ch}
ol.open li{counter-increment:step;display:grid;grid-template-columns:2rem 1fr;gap:4px 12px;padding:14px 0;border-bottom:1px solid var(--rule)}
ol.open li::before{content:counter(step);font-family:var(--serif);font-size:1.25rem;color:var(--accent);line-height:1.3}
ol.open strong{display:block}
ol.open span{color:var(--ink-2)}
.sources td{font-size:.88rem;padding:10px 16px 10px 0}
.sources code{font-family:ui-monospace,"Cascadia Mono",Menlo,monospace;font-size:.84rem;word-break:break-all}
.found-no{color:var(--ink-3)}
footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--rule);color:var(--ink-3);font-size:.85rem}
@media (max-width:960px){.legend{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:720px){
.wrap{padding:36px 20px 64px}
table.answer thead,table.items thead{position:absolute;left:-9999px}
table.answer tr,table.items tr{display:block;padding:16px 0;border-bottom:1px solid var(--rule)}
table.answer td,table.items td{display:block;width:auto!important;border:0;padding:0 0 8px}
.answer td.q{font-size:1.05rem}
.sources td:nth-child(2){display:none}
}
@media print{
html{background:#fff;font-size:12px}.wrap{max-width:none;padding:0}
h2{break-after:avoid}tr,li{break-inside:avoid}
.kind{border:1px solid currentColor}
}
"""


def render_html(report: dict[str, Any]) -> str:
    pilot = report["pilot"]
    generated = _date(report["generated_at"]) or report["generated_at"]
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(pilot['name'])}: pilot value</title>",
        '<meta name="color-scheme" content="light">',
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<main class="wrap">',
        '<header class="top">',
        f"<h1>{_e(pilot['name'])}: what the pilot showed</h1>",
    ]
    if pilot.get("description"):
        parts.append(f'<p class="lede">{_e(pilot["description"])}</p>')
    parts.append(
        f'<p class="meta">Generated {_e(generated)} from the pilot\'s own evidence files. '
        "Nothing on this page is typed in by hand: anything without evidence says "
        "<strong>Not measured yet</strong>.</p>")
    parts.append('<dl class="legend" aria-label="Evidence labels">')
    for kind in KINDS:
        parts.append(f"<div><dt>{_badge(kind)}</dt><dd>{_e(KIND_MEANING[kind])}</dd></div>")
    parts += ["</dl>", "</header>"]

    parts += [
        '<section aria-labelledby="short">',
        '<h2 id="short">The short answer</h2>',
        '<table class="answer"><thead><tr><th scope="col">Question</th>'
        '<th scope="col">What we know</th><th scope="col">Evidence</th></tr></thead><tbody>',
    ]
    for answer in report["answers"]:
        item = answer["item"]
        parts.append(
            f'<tr><td class="q">{_e(answer["question"])}</td><td class="a">{_value_html(item)}</td>'
            f"<td>{_badge(item['kind'])}</td></tr>")
    parts += ["</tbody></table>", "</section>"]

    for section in report["sections"]:
        sid = _e(section["id"])
        parts += [
            f'<section aria-labelledby="s-{sid}">',
            f'<h2 id="s-{sid}">{_e(section["title"])}</h2>',
            f'<p class="intro">{_e(section["intro"])}</p>',
            '<table class="items"><thead><tr><th scope="col">Item</th><th scope="col">Result</th>'
            '<th scope="col">Evidence and detail</th></tr></thead><tbody>',
        ]
        for item in section["items"]:
            source = ""
            if item["source"] or item.get("as_of"):
                text = " · ".join(filter(None, [item["source"], item.get("as_of")]))
                source = f'<span class="src">{_e(text)}</span>'
            detail = f'<span class="detail">{_e(item["detail"])}</span>' if item["detail"] else ""
            parts.append(
                f'<tr><td class="label">{_e(item["label"])}</td><td class="result">{_value_html(item)}</td>'
                f"<td>{_badge(item['kind'])}{detail}{source}</td></tr>")
        parts += ["</tbody></table>", "</section>"]

    if report["open"]:
        parts += ['<section aria-labelledby="open">', '<h2 id="open">Still to find out</h2>',
                  '<p class="intro">Each open item and the step that produces its evidence.</p>',
                  '<ol class="open">']
        for step in report["open"]:
            parts.append(f"<li><div><strong>{_e(step['label'])}</strong><span>{_e(step['step'])}</span></div></li>")
        parts += ["</ol>", "</section>"]

    parts += ['<section aria-labelledby="sources">', '<h2 id="sources">Sources</h2>',
              '<table class="sources"><thead><tr><th scope="col">File</th><th scope="col">What it holds</th>'
              '<th scope="col">Found</th></tr></thead><tbody>']
    for source in report["sources"]:
        found = "Yes" if source["present"] else '<span class="found-no">No</span>'
        parts.append(f"<tr><td><code>{_e(source['path'])}</code></td><td>{_e(source['what'])}</td><td>{found}</td></tr>")
    parts += ["</tbody></table>", "</section>",
              "<footer>Produced by the Threadlight pilot value page. It reads the pilot's evidence files and changes nothing.</footer>",
              "</main>", "</body>", "</html>"]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Writing and CLI
# ---------------------------------------------------------------------------

def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generated_at": report["generated_at"],
        "pilot": report["pilot"]["name"],
        "page": PAGE,
        "markdown": MARKDOWN,
        "counts": report["counts"],
        "answers": [
            {"question": a["question"], "value": a["item"]["value"], "kind": a["item"]["kind"]}
            for a in report["answers"]
        ],
        "open": [step["label"] for step in report["open"]],
    }


def write_report(root: Path, *, generated_at: str | None = None) -> list[Path]:
    root = Path(root)
    report = build_report(root, generated_at=generated_at)
    outputs = {
        root / MARKDOWN: render_markdown(report),
        root / PAGE: render_html(report),
        root / SUMMARY: json.dumps(_summary(report), indent=2, ensure_ascii=False) + "\n",
    }
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(outputs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pilot_value", description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", type=Path, default=Path("."), help="Pilot workspace root.")
    parser.add_argument("--generated-at", help="Pin the timestamp (RFC 3339) for reproducible output.")
    args = parser.parse_args(argv)
    if not args.workspace.is_dir():
        print(f"pilot_value: workspace not found: {args.workspace}", file=sys.stderr)
        return 2
    paths = write_report(args.workspace, generated_at=args.generated_at)
    summary = json.loads((args.workspace / SUMMARY).read_text(encoding="utf-8"))
    counts = summary["counts"]
    print(f"Pilot value page: {PAGE}")
    print("  " + ", ".join(f"{counts[k.replace('-', '_')]} {KIND_LABELS[k].lower()}" for k in KINDS))
    for path in paths:
        print(f"  wrote {path.relative_to(args.workspace).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
