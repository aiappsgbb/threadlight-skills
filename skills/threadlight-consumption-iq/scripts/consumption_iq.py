#!/usr/bin/env python3
"""
threadlight-consumption-iq CLI

Post-deploy cost projection + SKU diff for threadlight pilots.

Runs after `threadlight-safe-check --phase post-deploy` returns green and
before `threadlight-production-ready`. Reads the deployed Bicep + `azd env`
+ SPEC § 12 `load_profile{}`, hits the Azure Retail Prices API via the
`Azure-pricing` MCP, projects monthly cost for every deployed resource,
compares against 2-3 alternative SKUs per resource, and emits:

  * docs/cost-projection.md       (human-readable scorecard)
  * specs/cost-manifest.json      (strict v1 schema, consumed by
                                   threadlight-production-ready COST-005/006)
  * specs/SPEC.md § 12 load_profile{}  (back-filled if wizard ran)

Soft-advisory: never mutates Bicep. Recommendations are flagged for the
next `threadlight-deploy` run to act on.

The projection above is the default and is entirely offline as far as a
customer subscription is concerned. Two opt-in commands — `actuals` and
`reconcile` — additionally read (never write) live Azure Cost Management,
Azure Monitor and Log Analytics evidence and reconcile it against the
forecast. They are reached only by naming them explicitly, or by passing
`run --all --with-actuals`; `run --all` on its own behaves exactly as it
did before they existed, calls `_run_projection` and nothing else, and
never touches Azure.

Exit codes:
  0  artefacts produced (per-finding statuses live inside the report)
  2  missing prerequisite (no SPEC § 12, stale safe-check, unresolved
     `--subscription`/`--resource-group`, unreadable/invalid local JSON)
  3  I/O failure OR Azure-pricing MCP unavailable AND no fixture fallback
     for at least one required SKU OR a mandatory cost source could not be
     collected or published
  4  load_profile{} incomplete after wizard (interactive mode required)
  5  advisory: the reconciliation (or the actuals manifest) is
     `not-verified`. Always returned AFTER every artefact has been written
     — the evidence an operator needs to fix the gap is exactly what an
     early exit would destroy.

Single-file CLI dispatcher; stdlib only. Per-phase logic lives in:
  scripts/discover.py
  scripts/load_profile_wizard.py
  scripts/pricing_client.py
  scripts/projectors/<resource>.py
  scripts/recommender.py
  scripts/emitter.py
  scripts/actuals_sources.py      (the only Azure boundary)
  scripts/cost_actuals.py
  scripts/token_evidence.py
  scripts/value_model.py
  scripts/reconcile.py
  scripts/reconciliation_emitter.py

Mirrors the dependency posture of `threadlight-safe-check` and
`threadlight-production-ready`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Resolve sibling modules without requiring a package install.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from actuals_sources import ActualsSourceError, COST_API_VERSION, collect_sources  # noqa: E402
from cost_actuals import (  # noqa: E402
    ActualsEvidenceError,
    build_actuals_manifest,
    build_success_kql,
    parse_interaction_counts,
)
from discover import discover_resources  # noqa: E402
from discount import DiscountError  # noqa: E402
from cost_api import project_profile  # noqa: E402
from emitter import emit_artefacts  # noqa: E402
from estimate import emit_presales  # noqa: E402
from load_profile_wizard import load_or_prompt_profile, ProfileIncompleteError  # noqa: E402
from pricing_client import PricingClient, PricingUnavailableError  # noqa: E402
from recommender import score_and_rank  # noqa: E402
from reconcile import ReconciliationInputError, reconcile_costs  # noqa: E402
from reconciliation_emitter import (  # noqa: E402
    ACTUALS_SCHEMA,
    ARTIFACT_MODE,
    EmissionValidationError,
    HistoryConflictError,
    emit_reconciliation,
)
from rollout import load_rollout_profile, RolloutProfileError, has_declared_topology  # noqa: E402
from token_evidence import TokenEvidenceError, parse_token_series  # noqa: E402
from value_model import ValueModelResult, parse_value_model  # noqa: E402
from projectors import project_resource  # noqa: E402

DEFAULT_CACHE_PATH = Path(".threadlight/cost-cache.json")
DEFAULT_OUTPUT_REPORT = Path("docs/cost-projection.md")
DEFAULT_OUTPUT_MANIFEST = Path("specs/cost-manifest.json")
DEFAULT_ESTIMATE_REPORT = Path("docs/cost-estimate.md")
DEFAULT_ESTIMATE_MANIFEST = Path("specs/cost-estimate-manifest.json")
DEFAULT_SPEC_PATH = Path("specs/SPEC.md")
DEFAULT_DEPLOYMENT_MANIFEST = Path("specs/manifest.json")
DEFAULT_BICEP_ENTRYPOINT = Path("infra/main.bicep")
DEFAULT_ACTUALS_MANIFEST = Path("specs/cost-actuals-manifest.json")
DEFAULT_RECONCILIATION_MANIFEST = Path("specs/cost-reconciliation-manifest.json")
DEFAULT_RECONCILIATION_REPORT = Path("docs/cost-reconciliation.md")
DEFAULT_COST_HISTORY = Path("specs/cost-history")


def _phase_discover(args: argparse.Namespace) -> list[dict[str, Any]]:
    resources = discover_resources(
        bicep_entrypoint=args.bicep,
        deployment_manifest=args.deployment_manifest,
        use_azd_env=not args.pre_deploy,
    )
    if args.verbose:
        print(f"discover: {len(resources)} resource(s) found", file=sys.stderr)
    return resources


def _phase_load_profile(args: argparse.Namespace) -> dict[str, Any]:
    profile = load_or_prompt_profile(
        spec_path=args.spec,
        non_interactive=args.non_interactive,
    )
    return profile


def _phase_project(
    resources: list[dict[str, Any]],
    load_profile: dict[str, Any],
    pricing: PricingClient,
    only: str | None = None,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for resource in resources:
        if only and resource.get("resource_kind") != only:
            continue
        projected.append(project_resource(resource, load_profile, pricing))
    return projected


def _phase_recommend(
    projected: list[dict[str, Any]],
    load_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    return score_and_rank(projected, load_profile)


def _phase_emit(
    projected: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    load_profile: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    emit_artefacts(
        projected=projected,
        recommendations=recommendations,
        load_profile=load_profile,
        report_path=args.report,
        manifest_path=args.manifest,
        deploy_ref=_resolve_deploy_ref(args.pre_deploy),
        pre_deploy=args.pre_deploy,
    )


def _resolve_deploy_ref(pre_deploy: bool) -> str:
    if pre_deploy:
        return "pre-deploy"
    env = os.environ.get("AZURE_ENV_NAME") or "unknown-env"
    deployment_id = os.environ.get("AZURE_DEPLOYMENT_ID") or "unknown-deployment"
    return f"{env}/{deployment_id}"


# ---------- pre-sales estimate ----------------------------------------------


def _rollout_with_cli_overrides(rollout: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Layer CLI `--discount` / `--discount-basis` over the rollout's own block."""
    discount_arg = getattr(args, "discount", None)
    if discount_arg is not None:
        rollout = dict(rollout)
        rollout["discount"] = {
            "basis": getattr(args, "discount_basis", None) or "ea",
            "multiplier": float(discount_arg),
        }
    return rollout


def _phase_estimate(args: argparse.Namespace) -> dict[str, Any]:
    # --from-profile is the stable, no-discovery path: it reads a fully-declared
    # profile (load_profile + resources + selectors) and projects it through the
    # shared cost_api. It NEVER touches Bicep / azd / Azure discovery.
    if getattr(args, "from_profile", None):
        return _phase_estimate_from_profile(args)

    rollout = load_rollout_profile(args.rollout)
    rollout = _rollout_with_cli_overrides(rollout, args)
    # Pre-sales is repo-optional: if the rollout declares its own topology we
    # estimate straight from it (no Bicep / azd walk). Only fall back to
    # discovery when no topology is declared (reference-repo / expansion mode).
    if has_declared_topology(rollout):
        resources: list[dict[str, Any]] = rollout.get("resources") or []
    else:
        resources = _phase_discover(args)
    pricing = PricingClient(cache_path=args.cache)
    return emit_presales(
        rollout,
        resources,
        pricing,
        report_path=args.report,
        manifest_path=args.manifest,
        onepager_path=getattr(args, "onepager", None),
        audience=getattr(args, "audience", None),
        pdf=getattr(args, "pdf", False),
        deploy_ref=_resolve_deploy_ref(args.pre_deploy),
    )


def _phase_estimate_from_profile(args: argparse.Namespace) -> dict[str, Any]:
    """Project a fully-declared profile file into a vNext cost manifest.

    The profile file is JSON with keys: ``load_profile``, ``resources``,
    ``selectors``, ``transaction_unit``, ``monthly_transactions`` and an optional
    ``generated_at``. Discovery (``discover_resources``) is never invoked.
    """
    profile_path = Path(args.from_profile)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    load_profile = data.get("load_profile") or {}
    resources = data.get("resources") or []
    selectors = data.get("selectors") or {}
    transaction_unit = data.get("transaction_unit") or "transaction"
    monthly_transactions = data.get("monthly_transactions")
    generated_at = data.get("generated_at")

    pricing = PricingClient(cache_path=args.cache)
    manifest = project_profile(
        load_profile=load_profile,
        resources=resources,
        selectors=selectors,
        pricing=pricing,
        transaction_unit=transaction_unit,
        monthly_transactions=monthly_transactions,
        generated_at=generated_at,
    )
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_from_profile_report(manifest))
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "onepager": None,
    }


def _render_from_profile_report(manifest: dict[str, Any]) -> str:
    totals = manifest.get("totals") or {}
    complete = totals.get("complete")
    unit = manifest.get("transaction_unit") or "transaction"
    lines = [
        "# Cost estimate — from declared profile (no discovery)\n",
        f"> Generated `{manifest.get('generated_at')}`. Status: "
        f"`{manifest.get('status')}`. Meter coverage: "
        f"`{(manifest.get('meter_coverage') or {}).get('status')}`.\n",
    ]
    if complete:
        monthly = totals.get("monthly_cost_current_usd")
        cpt = totals.get("cost_per_transaction_usd")
        monthly_str = (
            f"${monthly:,.2f}" if isinstance(monthly, (int, float)) else "unavailable"
        )
        if isinstance(cpt, (int, float)):
            lines.append(
                f"Estimated monthly cost: **{monthly_str}** "
                f"(≈ ${cpt:,.6f}/{unit}).\n"
            )
        else:
            # Complete bill, but no positive `monthly_transactions` was declared,
            # so a per-unit figure can't be derived. Never render "$None/<unit>".
            lines.append(
                f"Estimated monthly cost: **{monthly_str}**. "
                f"Per-{unit} cost is unavailable because the profile declared no "
                "positive `monthly_transactions` volume to divide by.\n"
            )
    else:
        known = totals.get("monthly_cost_known_usd")
        known_str = (
            f"${known:,.2f}" if isinstance(known, (int, float)) else "unavailable"
        )
        lines.append(
            "**Incomplete total** — at least one detected line is not-priceable or "
            "not-verified, so no complete bill or per-transaction cost is presented. "
            f"Known-line subtotal: {known_str}.\n"
        )
    return "\n".join(lines)


# ---------- actuals + reconciliation (opt-in, the only Azure readers) --------


def _utc_now() -> datetime:
    """Whole-second UTC instant. Seam: tests pin this."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_policy(args: argparse.Namespace) -> ValueModelResult:
    """Parse SPEC § 14. Never raises on policy content — errors are carried."""
    return parse_value_model(Path(args.spec).read_text(encoding="utf-8"))


def _mapping(candidate: Any) -> dict[str, Any]:
    return candidate if isinstance(candidate, dict) else {}


def _success_kql(
    policy: ValueModelResult,
    start: datetime,
    end: datetime,
    warnings: list[str],
) -> Optional[str]:
    """Build the interaction query, or degrade with a warning.

    Only `cost.success_event` matters here — it is the sole part of the value
    model that shapes a query. A rejected maturity/baseline/accounting block
    must never suppress evidence collection.
    """
    event = _mapping(_mapping(policy.policy.get("cost")).get("success_event"))
    name = event.get("name")
    attribute = event.get("trace_attribute")
    values = event.get("success_values")
    if not isinstance(name, str) or not isinstance(attribute, str) or not isinstance(values, list):
        warnings.append(
            "interaction counts unavailable: SPEC § 14 cost.success_event is "
            "absent or was rejected, so no trace query was issued"
        )
        return None
    try:
        return build_success_kql(_iso_utc(start), _iso_utc(end), name, attribute, list(values))
    except ActualsEvidenceError as exc:
        warnings.append(f"interaction counts unavailable, no trace query was issued: {exc}")
        return None


def _attributed_token_series(doc: Any, resource_id: Any) -> list[dict[str, Any]]:
    """Stamp the owning account onto every model row.

    Token metrics are per-account; without the identity two accounts serving
    the same model name collapse into one row and a PAYG overspend can be
    netted off against a PTU underspend.
    """
    rows = parse_token_series(doc)
    if not isinstance(resource_id, str) or not resource_id:
        return rows
    return [
        {**row, "account_resource_id": resource_id, "resource_id": resource_id}
        for row in rows
    ]


def _actuals_scope(args: argparse.Namespace, policy: ValueModelResult) -> dict[str, Any]:
    scope_policy = _mapping(_mapping(policy.policy.get("cost")).get("accounting")).get(
        "scope_policy"
    )
    dedicated: Optional[bool]
    if scope_policy == "dedicated_resource_group":
        dedicated = True
    elif scope_policy == "tagged_allocation":
        dedicated = False
    else:
        dedicated = None
    return {
        "subscription_id": args.subscription,
        "resource_group": args.resource_group,
        "dedicated_to_workload": dedicated,
    }


def _actuals_provenance(
    args: argparse.Namespace,
    bundle: dict[str, Any],
    generated_at: datetime,
    kql: Optional[str],
) -> dict[str, Any]:
    """Names, identifiers and a timestamp — never a document, never a secret."""
    sources = ["cost-management-query"]
    if bundle.get("token_doc") is not None:
        sources.append("azure-monitor-metrics")
    if bundle.get("interaction_result") is not None:
        sources.append("log-analytics-query")
    return {
        "sources": sources,
        "query_api_version": COST_API_VERSION,
        "subscription_id": args.subscription,
        "resource_group": args.resource_group,
        "monitor_resource_id": getattr(args, "monitor_resource_id", None),
        "workspace_resource_id": getattr(args, "workspace_resource_id", None),
        "token_source_resource_id": bundle.get("token_source_resource_id"),
        "interaction_query_issued": kql is not None,
        "window": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "collected_at": _iso_utc(generated_at),
    }


def _phase_actuals(args: argparse.Namespace) -> dict[str, Any]:
    policy = _load_policy(args)
    start = _utc_midnight(args.start)
    end = _utc_midnight(args.end)
    generated_at = _utc_now()
    warnings: list[str] = []

    kql = _success_kql(policy, start, end, warnings)
    bundle = collect_sources(
        args.subscription,
        args.resource_group,
        args.start,
        args.end,
        monitor_resource_id=getattr(args, "monitor_resource_id", None),
        workspace_resource_id=getattr(args, "workspace_resource_id", None),
        kql=kql,
    )
    warnings.extend(bundle.get("warnings") or [])

    token_series: Optional[list[dict[str, Any]]] = None
    if bundle.get("token_doc") is not None:
        try:
            token_series = _attributed_token_series(
                bundle.get("token_doc"), bundle.get("token_source_resource_id")
            )
        except TokenEvidenceError as exc:
            warnings.append(f"token evidence unusable, model rows are unattributed: {exc}")

    interaction_counts = None
    if bundle.get("interaction_result") is not None:
        try:
            interaction_counts = parse_interaction_counts(bundle.get("interaction_result"))
        except ActualsEvidenceError as exc:
            warnings.append(f"interaction counts unusable: {exc}")

    return build_actuals_manifest(
        scope=_actuals_scope(args, policy),
        start=start,
        end=end,
        generated_at=generated_at,
        cost_pages=list(bundle.get("cost_pages") or []),
        token_series=token_series,
        interaction_counts=interaction_counts,
        provenance=_actuals_provenance(args, bundle, generated_at, kql),
        warnings=warnings,
    )


def _canonical_text(document: dict[str, Any]) -> str:
    try:
        return (
            json.dumps(
                document,
                sort_keys=True,
                indent=2,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise EmissionValidationError(f"actuals manifest is not serialisable: {exc}") from exc


def _write_atomic(destination: Path, text: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=str(destination.parent),
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    staged = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, ARTIFACT_MODE)
        os.replace(staged, destination)
    finally:
        try:
            staged.unlink()
        except FileNotFoundError:
            pass


def _emit_actuals(args: argparse.Namespace, document: Any) -> Path:
    """Publish the actuals manifest atomically. No history — that is the
    reconciliation emitter's job, and writing it twice would collide."""
    if not isinstance(document, dict):
        raise EmissionValidationError("actuals manifest must be a JSON object")
    if document.get("schema") != ACTUALS_SCHEMA:
        raise EmissionValidationError(
            f"actuals manifest schema must be {ACTUALS_SCHEMA!r}, got {document.get('schema')!r}"
        )
    destination = Path(args.actuals_manifest)
    _write_atomic(destination, _canonical_text(document))
    return destination


def _load_json_mapping(path: Path, label: str) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReconciliationInputError(f"{label} {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ReconciliationInputError(f"{label} {path} must be a JSON object")
    return document


def _forecast_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "forecast", None) or args.manifest)


def _reconciliation_report_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "reconciliation_report", None) or args.report)


def _require_reusable_actuals(document: dict[str, Any], args: argparse.Namespace) -> None:
    """Reconciliation must never silently compare against the wrong evidence."""
    if document.get("schema") != ACTUALS_SCHEMA:
        raise ReconciliationInputError(
            f"actuals manifest schema must be {ACTUALS_SCHEMA!r}, got {document.get('schema')!r}"
        )
    if document.get("status") != "pass":
        raise ReconciliationInputError(
            f"actuals manifest status must be 'pass', got {document.get('status')!r}"
        )
    window = _mapping(document.get("window"))
    start = window.get("start")
    end = window.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ReconciliationInputError("actuals manifest window is missing start/end")
    requested_start = getattr(args, "start", None)
    requested_end = getattr(args, "end", None)
    if requested_start is not None and not start.startswith(requested_start.isoformat()):
        raise ReconciliationInputError(
            f"actuals manifest window starts at {start}, requested {requested_start.isoformat()}"
        )
    if requested_end is not None and not end.startswith(requested_end.isoformat()):
        raise ReconciliationInputError(
            f"actuals manifest window ends at {end}, requested {requested_end.isoformat()}"
        )
    scope = _mapping(document.get("scope"))
    subscription = getattr(args, "subscription", None)
    resource_group = getattr(args, "resource_group", None)
    if subscription and scope.get("subscription_id") != subscription:
        raise ReconciliationInputError(
            "actuals manifest was collected for a different subscription"
        )
    if resource_group and scope.get("resource_group") != resource_group:
        raise ReconciliationInputError(
            "actuals manifest was collected for a different resource group"
        )


def _phase_reconcile(args: argparse.Namespace) -> dict[str, Any]:
    """Pure re-projection: local documents only, no source is ever contacted."""
    forecast = _load_json_mapping(_forecast_path(args), "forecast manifest")
    actuals = _load_json_mapping(Path(args.actuals_manifest), "actuals manifest")
    _require_reusable_actuals(actuals, args)
    spec_bytes = Path(args.spec).read_bytes()
    policy = _load_policy(args)
    return reconcile_costs(
        forecast,
        actuals,
        policy.policy,
        policy_errors=list(policy.errors),
        # When this verdict was COMPUTED, which is not when its evidence was
        # collected. The actuals keep their own `generated_at` (`collected_at`
        # in the report); copying it here would claim a re-projection ran days
        # before it did, and would make every re-reconciliation of unchanged
        # evidence collide with the first one in immutable history.
        generated_at=_iso_utc(_utc_now()),
        policy_spec_sha256=hashlib.sha256(spec_bytes).hexdigest(),
    )


def _emit_reconciliation(args: argparse.Namespace, reconciliation: dict[str, Any]) -> None:
    actuals = _load_json_mapping(Path(args.actuals_manifest), "actuals manifest")
    emit_reconciliation(
        actuals=actuals,
        reconciliation=reconciliation,
        report_path=_reconciliation_report_path(args),
        actuals_path=Path(args.actuals_manifest),
        reconciliation_path=Path(args.reconciliation_manifest),
        history_root=Path(args.cost_history),
    )


def _resolve_scope_or_exit(args: argparse.Namespace) -> Optional[int]:
    """Validate the Azure scope BEFORE any projection or network call."""
    if getattr(args, "phase", None) == "run":
        if not getattr(args, "with_actuals", False):
            return None
        if getattr(args, "pre_deploy", False):
            print(
                "--pre-deploy cannot be combined with --with-actuals: "
                "there are no actuals before a deployment exists",
                file=sys.stderr,
            )
            return 2
        if getattr(args, "pre_sales", False):
            print(
                "--pre-sales cannot be combined with --with-actuals",
                file=sys.stderr,
            )
            return 2
        if args.start is None or args.end is None:
            print("--with-actuals requires --start and --end", file=sys.stderr)
            return 2
    if not getattr(args, "subscription", None):
        print(
            "--subscription is required (or set AZURE_SUBSCRIPTION_ID)",
            file=sys.stderr,
        )
        return 2
    if not getattr(args, "resource_group", None):
        print(
            "--resource-group is required (or set AZURE_RESOURCE_GROUP)",
            file=sys.stderr,
        )
        return 2
    if args.end <= args.start:
        print("--end must be after --start", file=sys.stderr)
        return 2
    return None


# ---------- argument parsing -------------------------------------------------


def _add_scope_args(p: argparse.ArgumentParser, *, start_end_required: bool) -> None:
    p.add_argument("--start", type=date.fromisoformat, required=start_end_required)
    p.add_argument("--end", type=date.fromisoformat, required=start_end_required)
    p.add_argument(
        "--subscription",
        default=os.environ.get("AZURE_SUBSCRIPTION_ID"),
        help="Subscription ID (defaults to AZURE_SUBSCRIPTION_ID).",
    )
    p.add_argument(
        "--resource-group",
        default=os.environ.get("AZURE_RESOURCE_GROUP"),
        help="Resource group name (defaults to AZURE_RESOURCE_GROUP).",
    )
    p.add_argument(
        "--monitor-resource-id",
        help="ARM resource ID of the AI/Cognitive account to read token metrics from.",
    )
    p.add_argument(
        "--workspace-resource-id",
        help="ARM resource ID of the Log Analytics workspace holding traces.",
    )


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    p.add_argument(
        "--deployment-manifest",
        type=Path,
        default=DEFAULT_DEPLOYMENT_MANIFEST,
    )
    p.add_argument("--bicep", type=Path, default=DEFAULT_BICEP_ENTRYPOINT)
    p.add_argument("--report", type=Path, default=DEFAULT_OUTPUT_REPORT)
    p.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    p.add_argument(
        "--pre-deploy",
        action="store_true",
        help="Read Bicep only; skip azd env walk (use for pre-deploy review).",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail with exit 4 instead of prompting if SPEC § 12 load_profile is incomplete.",
    )
    p.add_argument("--verbose", "-v", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="consumption_iq")
    sub = parser.add_subparsers(dest="phase", required=True)

    for phase_name in (
        "discover",
        "load-profile",
        "price",
        "project",
        "recommend",
        "emit",
    ):
        p = sub.add_parser(phase_name)
        _common_args(p)
        if phase_name == "project":
            p.add_argument(
                "--only",
                help="Restrict projection to one resource_kind (e.g. Microsoft.ApiManagement/service).",
            )

    run = sub.add_parser("run")
    _common_args(run)
    run.add_argument(
        "--all", action="store_true", help="Run every phase end-to-end."
    )
    run.add_argument(
        "--only",
        help="Restrict projection to one resource_kind (project phase only).",
    )
    run.add_argument(
        "--pre-sales",
        action="store_true",
        help="Run the phased pre-sales estimate instead of the post-deploy projection (requires --rollout).",
    )
    _estimate_args(run)
    run.add_argument(
        "--with-actuals",
        action="store_true",
        help="After the projection, collect live cost actuals and reconcile them.",
    )
    _add_scope_args(run, start_end_required=False)
    run.add_argument("--actuals-manifest", type=Path, default=DEFAULT_ACTUALS_MANIFEST)
    run.add_argument(
        "--reconciliation-report", type=Path, default=DEFAULT_RECONCILIATION_REPORT
    )
    run.add_argument(
        "--reconciliation-manifest", type=Path, default=DEFAULT_RECONCILIATION_MANIFEST
    )
    run.add_argument("--cost-history", type=Path, default=DEFAULT_COST_HISTORY)

    actuals_p = sub.add_parser(
        "actuals",
        help="Collect live Cost Management / Monitor / Log Analytics evidence (read-only).",
    )
    _add_scope_args(actuals_p, start_end_required=True)
    actuals_p.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    actuals_p.add_argument(
        "--actuals-manifest", type=Path, default=DEFAULT_ACTUALS_MANIFEST
    )

    reconcile_p = sub.add_parser(
        "reconcile",
        help="Re-project an existing forecast against existing actuals (no Azure calls).",
    )
    reconcile_p.add_argument("--forecast", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    reconcile_p.add_argument(
        "--actuals-manifest", type=Path, default=DEFAULT_ACTUALS_MANIFEST
    )
    reconcile_p.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    reconcile_p.add_argument(
        "--reconciliation-manifest", type=Path, default=DEFAULT_RECONCILIATION_MANIFEST
    )
    reconcile_p.add_argument("--report", type=Path, default=DEFAULT_RECONCILIATION_REPORT)
    reconcile_p.add_argument("--cost-history", type=Path, default=DEFAULT_COST_HISTORY)

    estimate_p = sub.add_parser(
        "estimate",
        help="Pre-sales phased estimate from a rollout profile (no deploy required).",
    )
    _common_args(estimate_p)
    estimate_p.set_defaults(
        report=DEFAULT_ESTIMATE_REPORT,
        manifest=DEFAULT_ESTIMATE_MANIFEST,
    )
    _estimate_args(estimate_p, required_rollout=False)

    return parser


def _estimate_args(p: argparse.ArgumentParser, required_rollout: bool = False) -> None:
    p.add_argument(
        "--rollout",
        type=Path,
        required=required_rollout,
        help="Path to a rollout profile (.json or .yaml) describing the adoption phases.",
    )
    p.add_argument(
        "--from-profile",
        type=Path,
        dest="from_profile",
        help="Project a fully-declared profile file (load_profile + resources + selectors) "
        "through the stable cost_api. No Bicep/azd/Azure discovery is performed.",
    )
    p.add_argument(
        "--discount",
        type=float,
        help="EA/MCA discount multiplier in (0, 1], e.g. 0.85 for -15%%. Overrides the rollout's own discount block.",
    )
    p.add_argument(
        "--discount-basis",
        choices=("retail", "ea", "mca"),
        help="Price basis label for --discount (default ea).",
    )
    p.add_argument(
        "--audience",
        choices=("internal", "customer"),
        help="One-pager audience. Defaults to the current phase's audience.",
    )
    p.add_argument(
        "--onepager",
        type=Path,
        help="Also render a shareable seller one-pager (HTML) to this path.",
    )
    p.add_argument(
        "--pdf",
        action="store_true",
        help="Best-effort PDF alongside the one-pager (needs Chromium/Playwright; skips gracefully).",
    )


# ---------- dispatch ---------------------------------------------------------


def _verdict_exit(document: dict[str, Any], args: argparse.Namespace) -> int:
    """Advisory exit, always AFTER the artefacts have been published."""
    for message in document.get("policy_errors") or []:
        print(f"policy: {message}", file=sys.stderr)
    for message in document.get("warnings") or []:
        print(f"warning: {message}", file=sys.stderr)
    if document.get("status") == "pass":
        return 0
    print(
        f"{args.phase}: not verified — see the emitted artefacts for the gap",
        file=sys.stderr,
    )
    return 5


def _run_projection(args: argparse.Namespace) -> None:
    """The default end-to-end projection. Offline, unchanged, Azure-free."""
    resources = _phase_discover(args)
    profile = _phase_load_profile(args)
    pricing = PricingClient(cache_path=args.cache)
    projected = _phase_project(
        resources, profile, pricing, only=getattr(args, "only", None)
    )
    recs = _phase_recommend(projected, profile)
    _phase_emit(projected, recs, profile, args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.phase in ("actuals", "run"):
        scope_exit = _resolve_scope_or_exit(args)
        if scope_exit is not None:
            return scope_exit

    try:
        if args.phase == "discover":
            resources = _phase_discover(args)
            print(json.dumps(resources, indent=2))
            return 0

        if args.phase == "load-profile":
            profile = _phase_load_profile(args)
            print(json.dumps(profile, indent=2))
            return 0

        if args.phase == "price":
            resources = _phase_discover(args)
            pricing = PricingClient(cache_path=args.cache)
            for resource in resources:
                pricing.warm(resource)
            return 0

        if args.phase == "project":
            resources = _phase_discover(args)
            profile = _phase_load_profile(args)
            pricing = PricingClient(cache_path=args.cache)
            projected = _phase_project(
                resources, profile, pricing, only=getattr(args, "only", None)
            )
            print(json.dumps(projected, indent=2))
            return 0

        if args.phase == "recommend":
            resources = _phase_discover(args)
            profile = _phase_load_profile(args)
            pricing = PricingClient(cache_path=args.cache)
            projected = _phase_project(resources, profile, pricing)
            recs = _phase_recommend(projected, profile)
            print(json.dumps(recs, indent=2))
            return 0

        if args.phase == "emit":
            resources = _phase_discover(args)
            profile = _phase_load_profile(args)
            pricing = PricingClient(cache_path=args.cache)
            projected = _phase_project(resources, profile, pricing)
            recs = _phase_recommend(projected, profile)
            _phase_emit(projected, recs, profile, args)
            return 0

        if args.phase == "estimate":
            if not getattr(args, "from_profile", None) and not getattr(args, "rollout", None):
                print("estimate requires --rollout or --from-profile", file=sys.stderr)
                return 2
            result = _phase_estimate(args)
            if args.verbose:
                print(
                    f"emitted {result['report_path']} and {result['manifest_path']}",
                    file=sys.stderr,
                )
            return 0

        if args.phase == "run":
            if not args.all:
                print("run requires --all in v1", file=sys.stderr)
                return 2
            if getattr(args, "pre_sales", False):
                if not getattr(args, "rollout", None):
                    print("run --all --pre-sales requires --rollout", file=sys.stderr)
                    return 2
                result = _phase_estimate(args)
                if args.verbose:
                    print(
                        f"emitted {result['report_path']} and {result['manifest_path']}",
                        file=sys.stderr,
                    )
                return 0
            _run_projection(args)
            if args.verbose:
                print(
                    f"emitted {args.report} and {args.manifest}",
                    file=sys.stderr,
                )
            if not getattr(args, "with_actuals", False):
                return 0
            actuals = _phase_actuals(args)
            _emit_actuals(args, actuals)
            reconciliation = _phase_reconcile(args)
            _emit_reconciliation(args, reconciliation)
            return _verdict_exit(reconciliation, args)

        if args.phase == "actuals":
            actuals = _phase_actuals(args)
            _emit_actuals(args, actuals)
            return _verdict_exit(actuals, args)

        if args.phase == "reconcile":
            reconciliation = _phase_reconcile(args)
            _emit_reconciliation(args, reconciliation)
            return _verdict_exit(reconciliation, args)

        print(f"unknown phase: {args.phase}", file=sys.stderr)
        return 2

    except FileNotFoundError as exc:
        print(f"prerequisite missing: {exc}", file=sys.stderr)
        return 2
    except ReconciliationInputError as exc:
        print(f"reconciliation input invalid: {exc}", file=sys.stderr)
        return 2
    except RolloutProfileError as exc:
        print(f"rollout profile invalid: {exc}", file=sys.stderr)
        return 4
    except DiscountError as exc:
        print(f"discount invalid: {exc}", file=sys.stderr)
        return 4
    except ProfileIncompleteError as exc:
        print(f"load profile incomplete: {exc}", file=sys.stderr)
        return 4
    except PricingUnavailableError as exc:
        print(f"pricing unavailable: {exc}", file=sys.stderr)
        return 3
    except ActualsSourceError as exc:
        print(f"cost evidence unavailable: {exc}", file=sys.stderr)
        return 3
    except ActualsEvidenceError as exc:
        print(f"cost evidence unusable: {exc}", file=sys.stderr)
        return 3
    except TokenEvidenceError as exc:
        print(f"token evidence unusable: {exc}", file=sys.stderr)
        return 3
    except HistoryConflictError as exc:
        print(f"cost history conflict: {exc}", file=sys.stderr)
        return 3
    # EmissionValidationError subclasses ValueError, so it must be caught before
    # the generic declared-cost-input handler below or a rejected artefact would
    # be misreported as bad input (2) instead of a publication failure (3).
    except EmissionValidationError as exc:
        print(f"artefact rejected before publication: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        # Invalid declared cost input (e.g. non-positive monthly_transactions or
        # ptu_units rejected by cost_api). Surface cleanly instead of a traceback.
        print(f"invalid cost input: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"I/O failure: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
