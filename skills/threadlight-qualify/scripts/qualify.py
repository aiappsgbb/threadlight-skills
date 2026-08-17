#!/usr/bin/env python3
"""
threadlight-qualify — Cowork-safe pre-sales qualification & sizing.

Turns a **declared interview profile** (no Azure / Bicep / az / azd / Docker,
no customer credentials) into a deterministic sizing package:

    <output_dir>/qualification/
      sizing.md            human-readable sizing summary
      sizing-manifest.json machine-readable, normalized load profile + sizings
      discovery.md         qualification inputs + open questions (no live probe)
      roi.md               ONLY when both current-cost inputs are supplied

It derives monthly volumes from the interview, builds an MVP and a production
profile, and projects both through the **shared** ``cost_api.project_profile``
(the same projection API the deployed pipeline uses). Citadel hub sizing comes
from a dated, source-bearing ``references/citadel-sizing.json`` and is kept
strictly separate from per-application sizing.

Every assumption carries a provenance of ``user-supplied | derived | fixture |
live``. A pinned ``generated_at`` makes the serialized bytes byte-deterministic.

Runs identically in-repo (imports the normal tree) and inside Cowork (the
vendored ``cost-runtime.zip`` is on ``sys.path`` and ``vendor/model-catalog.json``
is handed to the same projection API). Stdlib only; no network required.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Import resolution (repo tree OR Cowork vendored runtime zip)
# ---------------------------------------------------------------------------

def _ensure_runtime_on_path() -> None:
    try:
        import cost_api  # noqa: F401
        return
    except Exception:
        pass
    here = Path(__file__).resolve()
    # Repo layout: skills/threadlight-qualify/scripts/qualify.py
    #  → skills/threadlight-consumption-iq/scripts
    candidate = here.parents[2] / "threadlight-consumption-iq" / "scripts"
    if candidate.exists():
        sys.path.insert(0, str(candidate))
    # Cowork layout: vendored runtime zip sits next to the skill root.
    for rel in ("vendor/cost-runtime.zip", "../vendor/cost-runtime.zip"):
        zip_path = (here.parent / rel).resolve()
        if zip_path.exists():
            sys.path.insert(0, str(zip_path))


_ensure_runtime_on_path()

from cost_api import project_profile  # noqa: E402
from pricing_client import PricingClient  # noqa: E402

try:
    from model_catalog import load_model_catalog, ModelCatalogError
except Exception:  # pragma: no cover
    load_model_catalog = None  # type: ignore
    ModelCatalogError = Exception  # type: ignore


class QualificationError(ValueError):
    """Raised when the interview profile is missing/invalid. Writes nothing."""


TOOL_VERSION = "0.1.0"
SCHEMA_ID = "threadlight.sizing-manifest/v1"

PROVENANCE_VALUES = frozenset({"user-supplied", "derived", "fixture", "live"})

REQUIRED_FIELDS = (
    "customer_brief",
    "workload_class",
    "annual_transaction_volume",
    "transaction_unit",
    "pages_per_transaction",
    "document_origin",
    "turns_per_conversation",
    "tokens_per_turn_estimate",
    "peak_concurrency",
    "business_hours_only",
    "sites_or_entities",
    "data_residency",
    "pinned_region",
)

# Derived-assumption constants (each surfaced in the assumptions ledger).
_MONTHS_PER_YEAR = 12
_MVP_FRACTION = 0.1  # pilot models 10% of production volume
_EMBEDDING_TOKENS_PER_PAGE = 800
_AUTOMATION_RATE = 0.7  # share of handling time removed
_LABOR_HOURLY_USD = 30.0  # fully-loaded agent hour
_CITADEL_SIZING_PATH = Path(__file__).resolve().parent.parent / "references" / "citadel-sizing.json"

_REPO_MODEL_CATALOG = (
    Path(__file__).resolve().parents[2]
    / "threadlight-consumption-iq"
    / "references"
    / "model-catalog.json"
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        raise QualificationError("profile must be an object/dict")
    missing = [f for f in REQUIRED_FIELDS if profile.get(f) in (None, "")]
    if missing:
        raise QualificationError(
            "qualification profile missing required field(s): " + ", ".join(missing)
        )
    numeric = (
        "annual_transaction_volume",
        "pages_per_transaction",
        "turns_per_conversation",
        "tokens_per_turn_estimate",
        "peak_concurrency",
        "sites_or_entities",
    )
    for field in numeric:
        value = profile.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QualificationError(f"field {field!r} must be a number, got {value!r}")
        if value < 0:
            raise QualificationError(f"field {field!r} must be >= 0, got {value!r}")
    if profile.get("annual_transaction_volume", 0) <= 0:
        raise QualificationError("annual_transaction_volume must be > 0")


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def _seconds_per_month(business_hours_only: bool) -> int:
    return 8 * 3600 * 22 if business_hours_only else 24 * 3600 * 30


def _derive(profile: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (derived_values, assumptions) — deterministic, no side effects."""
    assumptions: list[dict[str, Any]] = []

    def user(field: str, basis: str = "interview input") -> None:
        assumptions.append(
            {"field": field, "value": profile.get(field), "provenance": "user-supplied", "basis": basis}
        )

    def derived(field: str, value: Any, basis: str) -> None:
        assumptions.append(
            {"field": field, "value": value, "provenance": "derived", "basis": basis}
        )

    for f in ("annual_transaction_volume", "pages_per_transaction", "turns_per_conversation",
              "tokens_per_turn_estimate", "peak_concurrency", "business_hours_only", "sites_or_entities"):
        user(f)

    annual = float(profile["annual_transaction_volume"])
    monthly_tx = annual / _MONTHS_PER_YEAR
    derived("monthly_transactions", round(monthly_tx, 4), f"annual_transaction_volume / {_MONTHS_PER_YEAR}")

    pages_per_tx = float(profile["pages_per_transaction"])
    pages_per_month = monthly_tx * pages_per_tx
    derived("pages_per_month", round(pages_per_month, 4), "monthly_transactions * pages_per_transaction")

    embed_tokens = pages_per_month * _EMBEDDING_TOKENS_PER_PAGE
    derived(
        "embedding_tokens_per_month",
        round(embed_tokens, 4),
        f"pages_per_month * {_EMBEDDING_TOKENS_PER_PAGE} tokens/page",
    )

    turns = float(profile["turns_per_conversation"])
    seconds = _seconds_per_month(bool(profile["business_hours_only"]))
    peak_rps = (monthly_tx * turns) / seconds if seconds else 0.0
    derived(
        "peak_requests_per_second",
        round(peak_rps, 6),
        "monthly_transactions * turns_per_conversation / seconds_per_month",
    )

    search_requests = monthly_tx * turns
    derived(
        "search_requests_per_month",
        round(search_requests, 4),
        "monthly_transactions * turns_per_conversation (one retrieval per turn)",
    )
    derived("mvp_fraction", _MVP_FRACTION, "pilot models 10% of production volume")

    return (
        {
            "monthly_transactions": monthly_tx,
            "pages_per_month": pages_per_month,
            "embedding_tokens_per_month": embed_tokens,
            "peak_requests_per_second": peak_rps,
            "search_requests_per_month": search_requests,
            "turns": turns,
        },
        assumptions,
    )


def _normalized_load_profile(
    profile: dict[str, Any], derived: dict[str, Any], monthly_tx: float
) -> dict[str, Any]:
    scale = monthly_tx / derived["monthly_transactions"] if derived["monthly_transactions"] else 1.0
    return {
        "workload_class": profile["workload_class"],
        "business_hours_only": bool(profile["business_hours_only"]),
        "monthly_transactions": round(monthly_tx, 4),
        "peak_requests_per_second": round(derived["peak_requests_per_second"] * scale, 6),
        "peak_concurrent_sessions": profile["peak_concurrency"],
        "avg_tokens_per_request": profile["tokens_per_turn_estimate"],
        "pages_per_month": round(derived["pages_per_month"] * scale, 4),
        "embedding_tokens_per_month": round(derived["embedding_tokens_per_month"] * scale, 4),
        "search_requests_per_month": round(derived["search_requests_per_month"] * scale, 4),
        "pinned_region": profile["pinned_region"],
        "data_residency": profile["data_residency"],
        "ptu_model": "gpt-4o",
    }


def _selectors(profile: dict[str, Any]) -> dict[str, Any]:
    pages = float(profile["pages_per_transaction"])
    sites = float(profile["sites_or_entities"])
    selectors: dict[str, Any] = {}
    if pages > 0:
        selectors["content_understanding"] = {"enabled": True, "tier": "standard"}
        selectors["document_intelligence"] = {"enabled": True, "model": "prebuilt-layout"}
        selectors["embeddings"] = {"enabled": True, "model": "text-embedding-3-small"}
    if sites > 0:
        selectors["search_semantic"] = {"enabled": True}
    return selectors


def _resources(profile: dict[str, Any], load_profile: dict[str, Any]) -> list[dict[str, Any]]:
    # A single AOAI chat deployment carries the LLM token cost; its rate-based
    # projector consumes peak_requests_per_second + avg_tokens_per_request, which
    # we derived so they reconcile with the declared annual volume.
    return [
        {
            "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
            "resource_id": "declared/aoai-chat",
            "logical_name": "aoai-chat",
            "region": profile["pinned_region"],
            "current_sku": {
                "name": "gpt-4o",
                "tier": "PAYG",
                "region": profile["pinned_region"],
                "capacity": None,
                "extra": {"io_split": [0.65, 0.35]},
            },
        }
    ]


# ---------------------------------------------------------------------------
# Citadel hub sizing (separate, estate-billed)
# ---------------------------------------------------------------------------

def _load_citadel_sizing() -> dict[str, Any]:
    return json.loads(_CITADEL_SIZING_PATH.read_text(encoding="utf-8"))


def _citadel_hub_sizing(
    monthly_tx: float, assumptions: list[dict[str, Any]]
) -> dict[str, Any]:
    data = _load_citadel_sizing()
    tiers = data.get("tiers", {})
    pilot = tiers.get("pilot", {})
    tier_name = "pilot"
    tier = pilot
    if monthly_tx > float(pilot.get("max_monthly_transactions", 0)):
        tier_name = "enterprise"
        tier = tiers.get("enterprise", pilot)
    assumptions.append(
        {
            "field": "citadel_hub_tier",
            "value": tier_name,
            "provenance": "fixture",
            "basis": f"references/citadel-sizing.json ({data.get('checked_at')})",
        }
    )
    return {
        "kind": "citadel-hub",
        "tier": tier_name,
        "label": tier.get("label"),
        "apim_sku": tier.get("apim_sku"),
        "monthly_usd_estimate": tier.get("monthly_usd_estimate"),
        "components": tier.get("components", []),
        "estate_billed": True,
        "source": data.get("source"),
        "checked_at": data.get("checked_at"),
    }


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

def _make_pricing() -> PricingClient:
    # Offline: qualification never calls the live pricing API.
    return PricingClient(cache_path=Path(".threadlight") / "qualify-cache.json", offline=True)


def _model_catalog_path() -> str | None:
    # Prefer a Cowork-vendored catalog; fall back to the repo catalog.
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent / "vendor" / "model-catalog.json",
        _REPO_MODEL_CATALOG,
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _record_catalog_provenance(assumptions: list[dict[str, Any]], catalog_path: str | None) -> None:
    if not catalog_path or load_model_catalog is None:
        return
    try:
        catalog = load_model_catalog(catalog_path)
    except ModelCatalogError:
        return
    stale = catalog.is_stale()
    assumptions.append(
        {
            "field": "model_catalog",
            "value": {"checked_at": catalog.checked_at.isoformat(), "stale": stale},
            "provenance": "fixture",
            "basis": catalog.source or "model-catalog.json",
        }
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_qualification(
    profile: dict[str, Any],
    *,
    output_dir: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Validate, size, and write the qualification package.

    Raises :class:`QualificationError` (writing nothing) on invalid input.
    Returns a summary dict. A pinned ``generated_at`` yields deterministic bytes.
    """
    _validate_profile(profile)  # BEFORE any filesystem write

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    derived, assumptions = _derive(profile)

    catalog_path = _model_catalog_path()
    _record_catalog_provenance(assumptions, catalog_path)

    pricing = _make_pricing()
    selectors = _selectors(profile)

    app_sizings: list[dict[str, Any]] = []
    for stage, monthly_tx in (
        ("mvp", derived["monthly_transactions"] * _MVP_FRACTION),
        ("production", derived["monthly_transactions"]),
    ):
        load_profile = _normalized_load_profile(profile, derived, monthly_tx)
        resources = _resources(profile, load_profile)
        cost_manifest = project_profile(
            load_profile=load_profile,
            resources=resources,
            selectors=selectors,
            pricing=pricing,
            transaction_unit=profile["transaction_unit"],
            monthly_transactions=round(monthly_tx, 4),
            generated_at=generated_at,
            model_catalog_path=catalog_path,
        )
        app_sizings.append(
            {
                "kind": "threadlight-application",
                "stage": stage,
                "monthly_transactions": round(monthly_tx, 4),
                "cost_manifest": cost_manifest,
            }
        )

    hub_sizing = _citadel_hub_sizing(derived["monthly_transactions"], assumptions)

    normalized = _normalized_load_profile(profile, derived, derived["monthly_transactions"])
    status = "complete" if all(
        s["cost_manifest"]["totals"]["complete"] for s in app_sizings
    ) else "partial"

    manifest: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "version": TOOL_VERSION,
        "generated_at": generated_at,
        "status": status,
        "customer_brief": profile["customer_brief"],
        "transaction_unit": profile["transaction_unit"],
        "data_residency": profile["data_residency"],
        "pinned_region": profile["pinned_region"],
        "load_profile": normalized,
        "sizings": app_sizings + [hub_sizing],
        "assumptions": assumptions,
    }

    outdir = Path(output_dir) / "qualification"
    outdir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(outdir / "sizing-manifest.json", manifest)
    _write_text(outdir / "sizing.md", _render_sizing_md(manifest))
    _write_text(outdir / "discovery.md", _render_discovery_md(profile, manifest))

    roi_written = False
    if (
        profile.get("current_annual_cost_usd") is not None
        and profile.get("current_handling_minutes_per_transaction") is not None
    ):
        _write_text(outdir / "roi.md", _render_roi_md(profile, manifest))
        roi_written = True

    return {
        "output_dir": str(outdir),
        "status": status,
        "roi_written": roi_written,
        "sizing_manifest": manifest,
    }


# ---------------------------------------------------------------------------
# Deterministic writers
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            tmp = Path(handle.name)
            json.dump(obj, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _fmt_usd(value: Any) -> str:
    if value is None:
        return "n/a (incomplete)"
    return f"${value:,.2f}"


def _app_cost_line(sizing: dict[str, Any]) -> str:
    totals = sizing["cost_manifest"]["totals"]
    coverage = sizing["cost_manifest"]["meter_coverage"]["status"]
    if totals["complete"]:
        return (
            f"{_fmt_usd(totals['monthly_cost_current_usd'])}/mo "
            f"(≈ {_fmt_usd(totals['cost_per_transaction_usd'])}/txn, coverage {coverage})"
        )
    return (
        f"incomplete — known subtotal {_fmt_usd(totals['monthly_cost_known_usd'])}/mo, "
        f"coverage {coverage}"
    )


def _render_sizing_md(manifest: dict[str, Any]) -> str:
    lines = [
        "# Qualification sizing",
        "",
        f"> Generated `{manifest['generated_at']}`. Status: `{manifest['status']}`. "
        "All figures are planning ESTIMATES at public list prices — not a quote.",
        "",
        f"**Customer brief:** {manifest['customer_brief']}",
        "",
        f"- Transaction unit: `{manifest['transaction_unit']}`",
        f"- Data residency: `{manifest['data_residency']}` (pinned region `{manifest['pinned_region']}`)",
        "",
        "## Application sizing (per-workload)",
        "",
        "| Stage | Monthly transactions | Estimated cost |",
        "| --- | --- | --- |",
    ]
    for sizing in manifest["sizings"]:
        if sizing["kind"] != "threadlight-application":
            continue
        lines.append(
            f"| {sizing['stage']} | {sizing['monthly_transactions']:,.0f} | {_app_cost_line(sizing)} |"
        )
    lines += ["", "## Citadel hub sizing (shared, estate-billed)", ""]
    for sizing in manifest["sizings"]:
        if sizing["kind"] != "citadel-hub":
            continue
        lines += [
            f"- Tier: **{sizing['tier']}** — {sizing.get('label')}",
            f"- Estimated hub cost: **{_fmt_usd(sizing.get('monthly_usd_estimate'))}/mo** "
            "(billed once across the estate, NOT per application)",
            f"- Components: {', '.join(sizing.get('components') or [])}",
            f"- Source: {sizing.get('source')} (`{sizing.get('checked_at')}`)",
        ]
    lines += ["", "## Assumptions", "", "| Field | Value | Provenance | Basis |", "| --- | --- | --- | --- |"]
    for a in manifest["assumptions"]:
        lines.append(
            f"| `{a['field']}` | {a['value']} | `{a['provenance']}` | {a.get('basis', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_discovery_md(profile: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        "# Discovery notes",
        "",
        "> This qualification performed **no live discovery** — no Azure, Bicep, "
        "az, azd or customer credentials were used. Everything below is declared "
        "interview input or a derived planning assumption.",
        "",
        "## Declared interview inputs",
        "",
    ]
    for field in REQUIRED_FIELDS:
        lines.append(f"- `{field}`: {profile.get(field)}")
    lines += [
        "",
        "## Open questions for the next conversation",
        "",
        "- Confirm the declared monthly volume against the customer's actuals.",
        "- Confirm document origin / page counts drive the extraction + embedding meters.",
        "- Confirm data residency vs. the pinned region and any Citadel hub placement.",
        "- Validate model choice and PTU-vs-PAYG break-even before committing capacity.",
        "",
    ]
    return "\n".join(lines)


def _render_roi_md(profile: dict[str, Any], manifest: dict[str, Any]) -> str:
    current_annual = float(profile["current_annual_cost_usd"])
    handling_min = float(profile["current_handling_minutes_per_transaction"])
    annual_volume = float(profile["annual_transaction_volume"])

    labor_hours_saved = (handling_min / 60.0) * annual_volume * _AUTOMATION_RATE
    labor_savings_usd = labor_hours_saved * _LABOR_HOURLY_USD

    prod = next(
        s for s in manifest["sizings"]
        if s["kind"] == "threadlight-application" and s["stage"] == "production"
    )
    totals = prod["cost_manifest"]["totals"]
    monthly = totals.get("monthly_cost_current_usd")
    projected_annual = monthly * 12 if monthly is not None else None

    lines = [
        "# ROI (planning estimate)",
        "",
        "> Estimate only. Assumes a fully-loaded agent hour of "
        f"${_LABOR_HOURLY_USD:.0f} and a {_AUTOMATION_RATE:.0%} automation rate "
        "(both derived assumptions — tune with the customer).",
        "",
        f"- Current annual cost (user-supplied): {_fmt_usd(current_annual)}",
        f"- Handling time removed: {labor_hours_saved:,.0f} hours/yr "
        f"→ labor savings {_fmt_usd(labor_savings_usd)}/yr",
    ]
    if projected_annual is None:
        lines += [
            "",
            "**Projected solution cost is incomplete** (a detected line is "
            "not-priceable or not-verified), so a net ROI is not asserted. "
            "Close the coverage gap, then re-run.",
            "",
        ]
        return "\n".join(lines)

    net = (current_annual + labor_savings_usd) - projected_annual
    verdict = "positive" if net > 0 else "negative"
    lines += [
        f"- Projected annual solution cost: {_fmt_usd(projected_annual)}",
        "",
        f"**Net annual benefit: {_fmt_usd(net)} → ROI is {verdict}.**",
        "",
        "Net = (current annual cost + labor savings) − projected annual solution cost.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="qualify")
    parser.add_argument("--profile", type=Path, required=True, help="Interview profile JSON.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write qualification/ under.")
    parser.add_argument("--generated-at", help="Pin the timestamp for deterministic output.")
    args = parser.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    try:
        result = run_qualification(
            profile, output_dir=args.output_dir, generated_at=args.generated_at
        )
    except QualificationError as exc:
        print(f"qualification invalid: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {result['output_dir']} (status={result['status']}, roi={result['roi_written']})")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
