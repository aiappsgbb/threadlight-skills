"""
Phase 7 — emitter.

Writes the two output artefacts:

  * docs/cost-projection.md       (human-readable)
  * specs/cost-manifest.json      (machine-readable, strict v1 schema)

The manifest is the source of truth — the markdown is rendered from it.
This keeps the golden-file test simple (manifest == expected JSON,
markdown == expected markdown).

See references/cost-manifest-schema.md for the full schema. Totals are
recomputed here (not trusted from upstream) so the manifest always
reconciles.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


def emit_artefacts(
    projected: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    load_profile: dict[str, Any],
    report_path: Path,
    manifest_path: Path,
    deploy_ref: str,
    pre_deploy: bool = False,
) -> None:
    manifest = _build_manifest(
        projected=projected,
        recommendations=recommendations,
        load_profile=load_profile,
        deploy_ref=deploy_ref,
        pre_deploy=pre_deploy,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    markdown = _render_markdown(manifest)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown)


def _build_manifest(
    projected: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    load_profile: dict[str, Any],
    deploy_ref: str,
    pre_deploy: bool,
) -> dict[str, Any]:
    monthly_current = sum(
        (r.get("monthly_cost_usd") or 0) for r in projected
    )
    monthly_recommended = monthly_current - sum(
        (rec.get("monthly_savings_usd") or 0) for rec in recommendations
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deploy_ref": deploy_ref,
        "pre_deploy": pre_deploy,
        "load_profile_ref": "specs/SPEC.md#section-12-load-profile",
        "currency": "USD",
        "price_basis": "retail",
        "resources": projected,
        "recommendations": recommendations,
        "totals": {
            "monthly_cost_current_usd": round(monthly_current, 2),
            "monthly_cost_recommended_usd": round(monthly_recommended, 2),
            "monthly_savings_potential_usd": round(
                monthly_current - monthly_recommended, 2
            ),
        },
    }


def _render_markdown(manifest: dict[str, Any]) -> str:
    """Render the human-readable report from the manifest."""
    parts: list[str] = []
    parts.append(_render_header(manifest))
    parts.append(_render_totals(manifest))
    parts.append(_render_cost_share(manifest))
    parts.append(_render_recommendations(manifest))
    parts.append(_render_per_resource(manifest))
    parts.append(_render_footer(manifest))
    return "\n".join(p for p in parts if p)


def _render_header(manifest: dict[str, Any]) -> str:
    pre = " (pre-deploy preview)" if manifest.get("pre_deploy") else ""
    return (
        "# Cost projection\n\n"
        f"> Generated `{manifest['generated_at']}` against deploy "
        f"`{manifest['deploy_ref']}`{pre}.\n"
        f"> Load profile: `{manifest['load_profile_ref']}`. "
        f"Currency: `{manifest['currency']}`. Price basis: `{manifest['price_basis']}`.\n"
        "\n"
        "_Authoritative machine-readable manifest:_ "
        "[`specs/cost-manifest.json`](../specs/cost-manifest.json).\n"
    )


def _render_totals(manifest: dict[str, Any]) -> str:
    totals = manifest["totals"]
    current = totals["monthly_cost_current_usd"]
    recommended = totals["monthly_cost_recommended_usd"]
    savings = totals["monthly_savings_potential_usd"]
    pct = (savings / current * 100) if current else 0.0
    return (
        "## Totals\n\n"
        "| Metric | USD / month |\n"
        "| --- | --- |\n"
        f"| Monthly cost (current) | ${current:,.2f} |\n"
        f"| Monthly cost (after applying recommendations) | ${recommended:,.2f} |\n"
        f"| **Monthly savings potential** | **${savings:,.2f} ({pct:.1f}%)** |\n"
    )


def _render_cost_share(manifest: dict[str, Any]) -> str:
    resources = manifest.get("resources") or []
    if not resources:
        return ""
    by_kind: dict[str, float] = {}
    for r in resources:
        kind = _short_kind(r["resource_kind"])
        by_kind[kind] = by_kind.get(kind, 0.0) + (r.get("monthly_cost_usd") or 0.0)
    if not any(v > 0 for v in by_kind.values()):
        return ""
    lines = ["## Cost share by resource kind", "", "```mermaid", "pie title Monthly cost share"]
    for kind in sorted(by_kind, key=lambda k: by_kind[k], reverse=True):
        if by_kind[kind] <= 0:
            continue
        lines.append(f'  "{kind}" : {by_kind[kind]:.2f}')
    lines.append("```\n")
    return "\n".join(lines)


def _render_recommendations(manifest: dict[str, Any]) -> str:
    recs = manifest.get("recommendations") or []
    if not recs:
        return (
            "## Recommendations\n\n"
            "_None._ Every deployed SKU is the cheapest constraint-satisfying option for the declared load.\n"
        )
    rows = [
        "## Recommendations\n",
        f"_{len(recs)} resource(s) have a cheaper SKU that satisfies declared constraints. Sorted by monthly savings descending._\n",
        "| Priority | Resource | Current SKU | Recommended SKU | Monthly savings | % | Rationale |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in recs:
        rows.append(
            "| {prio} | `{name}` ({kind}) | {cur} | {rec} | ${sav:,.2f} | {pct:.1f}% | {why} |".format(
                prio=_priority_badge(r["priority"]),
                name=r.get("logical_name") or "?",
                kind=_short_kind(r["resource_kind"]),
                cur=_sku_short(r["current_sku"]),
                rec=_sku_short(r["recommended_sku"]),
                sav=r["monthly_savings_usd"],
                pct=r["monthly_savings_pct"] * 100,
                why=_oneline(r.get("rationale") or ""),
            )
        )
    rows.append("")
    return "\n".join(rows)


def _render_per_resource(manifest: dict[str, Any]) -> str:
    resources = manifest.get("resources") or []
    if not resources:
        return ""
    out: list[str] = ["## Per-resource breakdown\n"]
    for r in sorted(
        resources,
        key=lambda x: (x.get("monthly_cost_usd") or 0.0),
        reverse=True,
    ):
        out.append(_render_one_resource(r))
    return "\n".join(out)


def _render_one_resource(r: dict[str, Any]) -> str:
    kind = _short_kind(r["resource_kind"])
    name = r.get("logical_name") or "?"
    region = r.get("region") or "?"
    current_cost = r.get("monthly_cost_usd") or 0.0
    price_source = r.get("price_source") or "?"
    sku = r["current_sku"]

    header = f"### `{name}` — {kind} ({region})\n"
    summary = (
        f"- **Current cost:** ${current_cost:,.2f}/month "
        f"({_sku_short(sku)}, `price_source: {price_source}`)\n"
    )
    units = r.get("monthly_units_consumed") or {}
    if units:
        unit_lines = ["- **Monthly units consumed:**"]
        for k, v in units.items():
            unit_lines.append(f"  - `{k}`: {_fmt_units(v)}")
        summary += "\n".join(unit_lines) + "\n"

    alternatives = r.get("alternatives") or []
    if not alternatives:
        return header + summary + "\n_No alternatives evaluated._\n"

    table = [
        "",
        "| Variant | Monthly cost | Δ vs current | Satisfies constraints? | Caveats |",
        "| --- | --- | --- | --- | --- |",
        f"| **Current** ({_sku_short(sku)}) | ${current_cost:,.2f} | — | — | — |",
    ]
    for alt in sorted(
        alternatives,
        key=lambda a: a.get("monthly_cost_usd") if a.get("monthly_cost_usd") is not None else float("inf"),
    ):
        delta_usd = alt.get("delta_usd")
        delta_pct = alt.get("delta_pct")
        delta_str = (
            f"${delta_usd:+,.2f} ({delta_pct * 100:+.1f}%)"
            if delta_usd is not None and delta_pct is not None
            else "?"
        )
        ok = "✅" if alt.get("satisfies_constraints", True) else "⚠️"
        caveats = "; ".join(alt.get("caveats") or []) or "—"
        cost = alt.get("monthly_cost_usd")
        cost_cell = f"${cost:,.2f}" if cost is not None else "N/A"
        table.append(
            "| {variant} | {cost} | {delta} | {ok} | {caveats} |".format(
                variant=_sku_short(alt["sku"]),
                cost=cost_cell,
                delta=delta_str,
                ok=ok,
                caveats=_oneline(caveats),
            )
        )
    table.append("")
    return header + summary + "\n".join(table)


def _render_footer(manifest: dict[str, Any]) -> str:
    return (
        "---\n\n"
        "> **Advisory only.** This skill does not mutate Bicep. To act on a "
        "recommendation, update `infra/main.bicep` (or the relevant module) "
        "and re-run `threadlight-deploy`. The next `threadlight-consumption-iq` "
        "run will re-score against the new SKU.\n"
    )


# ---------- formatting helpers ----------------------------------------------


def _short_kind(resource_kind: str) -> str:
    # "Microsoft.CognitiveServices/accounts/deployments" -> "CognitiveServices/deployments"
    if not resource_kind:
        return "?"
    parts = resource_kind.split("/")
    head = parts[0].removeprefix("Microsoft.")
    tail = parts[-1] if len(parts) > 1 else ""
    return f"{head}/{tail}" if tail else head


def _sku_short(sku: dict[str, Any] | None) -> str:
    if not sku:
        return "?"
    name = sku.get("name") or "?"
    tier = sku.get("tier")
    capacity = sku.get("capacity")
    bits = [name]
    if tier and tier != name:
        bits.append(f"tier={tier}")
    if capacity:
        bits.append(f"capacity={capacity}")
    return " ".join(bits)


def _priority_badge(priority: str) -> str:
    return {"high": "🔴 high", "med": "🟡 med", "low": "⚪ low"}.get(priority, priority)


def _fmt_units(v: Any) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if v >= 1000 else f"{v}"
    return str(v)


def _oneline(text: str) -> str:
    # Tables break on newlines and pipes.
    return text.replace("\n", " ").replace("|", "\\|")
