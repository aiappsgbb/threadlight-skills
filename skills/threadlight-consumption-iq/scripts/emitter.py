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

from discount import apply_discount, discount_manifest  # noqa: E402


SCHEMA_VERSION = "1.0"
PRESALES_SCHEMA_VERSION = "1.1"


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


# ===========================================================================
# Pre-sales phased estimate (schema 1.1)
# ===========================================================================
#
# Where the v1 path above models ONE deployed load, the pre-sales path models N
# adoption phases (land-and-expand). Each phase is projected at its own load +
# posture; we recompute every total here (never trust upstream) and mirror the
# CURRENT phase's totals into top-level `totals.*` so the downstream
# production-ready COST gates keep reading a meaningful number. Additive only —
# no v1 field is removed, so old readers still work.


def emit_presales_artefacts(
    phases: list[dict[str, Any]],
    rollout_profile: dict[str, Any],
    report_path: Path,
    manifest_path: Path,
    deploy_ref: str = "pre-sales",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build + write the phased manifest and the phased markdown report.

    Returns the manifest dict (so an orchestrator can also render a one-pager
    from it without rebuilding).
    """
    manifest = build_presales_manifest(
        phases, rollout_profile, deploy_ref=deploy_ref, generated_at=generated_at
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_presales_markdown(manifest))
    return manifest


def build_presales_manifest(
    phases: list[dict[str, Any]],
    rollout_profile: dict[str, Any],
    deploy_ref: str = "pre-sales",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the phased pre-sales manifest from already-projected phases.

    Each `phases[i]` is `{id, label, posture, audience, resources[],
    hardening_delta[], recommendations[], benchmark?}` — i.e. the output of the
    `estimate` orchestrator. Totals are recomputed here so the manifest always
    reconciles.
    """
    current_phase_id = rollout_profile.get("current_phase")
    out_phases: list[dict[str, Any]] = []

    for ph in phases:
        resources = ph.get("resources") or []
        hardening = ph.get("hardening_delta") or []
        recs = ph.get("recommendations") or []

        res_total = float(sum((r.get("monthly_cost_usd") or 0.0) for r in resources))
        hard_total = float(sum((ln.get("monthly_cost_usd") or 0.0) for ln in hardening))
        hard_shared = float(sum(
            (ln.get("monthly_cost_usd") or 0.0)
            for ln in hardening
            if ln.get("shared_platform_billed")
        ))
        current = res_total + hard_total
        savings = float(sum((rec.get("monthly_savings_usd") or 0.0) for rec in recs))
        recommended = current - savings

        phase_obj: dict[str, Any] = {
            "id": ph["id"],
            "label": ph["label"],
            "posture": ph["posture"],
            "audience": ph.get("audience", "internal"),
            "resources": resources,
            "hardening_delta": hardening,
            "recommendations": recs,
            "totals": {
                "monthly_cost_resources_usd": round(res_total, 2),
                "monthly_cost_hardening_usd": round(hard_total, 2),
                "monthly_cost_hardening_shared_usd": round(hard_shared, 2),
                "monthly_cost_current_usd": round(current, 2),
                "monthly_cost_recommended_usd": round(recommended, 2),
                "monthly_savings_potential_usd": round(current - recommended, 2),
            },
        }
        if ph.get("benchmark"):
            phase_obj["benchmark"] = ph["benchmark"]
        out_phases.append(phase_obj)

    if not current_phase_id and out_phases:
        current_phase_id = out_phases[0]["id"]
    current_obj = next(
        (p for p in out_phases if p["id"] == current_phase_id),
        out_phases[0] if out_phases else None,
    )
    top_totals = dict(current_obj["totals"]) if current_obj else {}

    manifest: dict[str, Any] = {
        "schema_version": PRESALES_SCHEMA_VERSION,
        "pre_sales": True,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "deploy_ref": deploy_ref,
        "currency": rollout_profile.get("currency", "USD"),
        "customer": rollout_profile.get("customer", "Generic Pilot"),
        "price_basis": "retail",
        "current_phase": current_phase_id,
        "phases": out_phases,
        "totals": top_totals,
    }
    if rollout_profile.get("benchmark"):
        manifest["benchmark"] = rollout_profile["benchmark"]

    discount = rollout_profile.get("discount")
    if discount:
        manifest = _apply_presales_discount(manifest, discount)
    else:
        manifest["discount"] = {
            "basis": "retail",
            "multiplier": 1.0,
            "applied": False,
            "caveats": [],
        }
    return manifest


def _apply_presales_discount(manifest: dict[str, Any], discount: dict[str, Any]) -> dict[str, Any]:
    """Apply an EA/MCA multiplier to top-level AND per-phase current totals."""
    basis = discount.get("basis", "ea")
    multiplier = float(discount.get("multiplier", 1.0))

    # Reuse the tested top-level discounter (adds discounted siblings to the
    # top totals + the discount{} block + caveats + price_basis).
    manifest = discount_manifest(manifest, basis, multiplier)

    if manifest["discount"]["applied"]:
        for ph in manifest["phases"]:
            totals = ph["totals"]
            # Mirror the top-level discounter: add a discounted sibling for
            # EVERY retail `_usd` key so top-level `totals` (a copy of the
            # current phase) stays a faithful mirror of `phases[current]`.
            for key in list(totals.keys()):
                if key.endswith("_usd") and not key.endswith("_discounted_usd"):
                    disc_key = key.replace("_usd", "_discounted_usd")
                    totals[disc_key] = round(apply_discount(totals[key], multiplier), 2)
    return manifest


# ---------- pre-sales markdown ----------------------------------------------


def render_presales_markdown(manifest: dict[str, Any]) -> str:
    parts: list[str] = [
        _render_presales_header(manifest),
        _render_presales_headline(manifest),
        _render_phase_matrix(manifest),
        _render_hardening_delta(manifest),
        _render_presales_footer(manifest),
    ]
    return "\n".join(p for p in parts if p)


def _render_presales_header(manifest: dict[str, Any]) -> str:
    discount = manifest.get("discount") or {}
    basis_note = ""
    if discount.get("applied"):
        basis_note = (
            f" Discounted figures apply a "
            f"{(1 - discount['multiplier']) * 100:.0f}% {discount['basis'].upper()} "
            f"multiplier (an internal assumption, not a quote)."
        )
    bench = manifest.get("benchmark")
    bench_note = ""
    if bench:
        val = bench.get("value")
        val_str = f"{val:,}" if isinstance(val, (int, float)) else str(val)
        bench_note = f" Anchored to benchmark `{bench.get('metric')} = {val_str}`."
    return (
        "# Cost estimate — phased pre-sales projection\n\n"
        f"> Generated `{manifest['generated_at']}` for `{manifest['customer']}`.\n"
        f"> Currency: `{manifest['currency']}`. Price basis: `{manifest['price_basis']}`."
        f"{basis_note}{bench_note}\n"
        ">\n"
        "> **All figures are planning ESTIMATES at public list prices for a single "
        "generic pilot — not a quote.** They frame a conversation; they do not commit "
        "a number.\n"
    )


def _render_presales_headline(manifest: dict[str, Any]) -> str:
    totals = manifest.get("totals") or {}
    current = totals.get("monthly_cost_current_usd", 0.0)
    cur_id = manifest.get("current_phase")
    line = (
        "## Headline (current phase)\n\n"
        f"Current phase: **`{cur_id}`**. Estimated monthly cost: "
        f"**${current:,.2f}** (estimate)."
    )
    disc = totals.get("monthly_cost_current_discounted_usd")
    if disc is not None:
        line += f" After discount: **${disc:,.2f}** (estimate)."
    return line + "\n"


def _render_phase_matrix(manifest: dict[str, Any]) -> str:
    phases = manifest.get("phases") or []
    if not phases:
        return ""
    applied = bool((manifest.get("discount") or {}).get("applied"))
    cur_id = manifest.get("current_phase")

    header = "| Phase | Posture | Resources (est.) | Hardening Δ (est.) | Phase total (est.) |"
    sep = "| --- | --- | --- | --- | --- |"
    if applied:
        header += " EA total (est.) |"
        sep += " --- |"
    rows = ["## Cost by adoption phase\n", header, sep]
    for ph in phases:
        t = ph["totals"]
        marker = " ⭐" if ph["id"] == cur_id else ""
        cells = (
            f"| {ph['label']}{marker} | `{ph['posture']}` | "
            f"${t['monthly_cost_resources_usd']:,.2f} | "
            f"${t['monthly_cost_hardening_usd']:,.2f} | "
            f"**${t['monthly_cost_current_usd']:,.2f}** |"
        )
        if applied:
            disc = t.get("monthly_cost_current_discounted_usd")
            cells += f" ${disc:,.2f} |" if disc is not None else " — |"
        rows.append(cells)
    rows.append("")
    return "\n".join(rows)


def _render_hardening_delta(manifest: dict[str, Any]) -> str:
    phases = manifest.get("phases") or []
    blocks: list[str] = []
    for ph in phases:
        lines = ph.get("hardening_delta") or []
        if not lines:
            continue
        rows = [
            f"### {ph['label']} — `{ph['posture']}`\n",
            "| Component | Category | Monthly (est.) | Estate-billed? | Rationale |",
            "| --- | --- | --- | --- | --- |",
        ]
        for ln in lines:
            shared = "yes" if ln.get("shared_platform_billed") else "no"
            rows.append(
                "| {comp} | {cat} | ${cost:,.2f} | {shared} | {why} |".format(
                    comp=ln.get("component", "?"),
                    cat=ln.get("category", "?"),
                    cost=float(ln.get("monthly_cost_usd") or 0.0),
                    shared=shared,
                    why=_oneline(ln.get("rationale") or ""),
                )
            )
        shared_total = float((ph.get("totals") or {}).get("monthly_cost_hardening_shared_usd") or 0.0)
        rows.append("")
        if shared_total > 0:
            rows.append(
                f"_Of which ${shared_total:,.2f}/mo is **shared platform** "
                "billed once across the estate — the customer may already pay "
                "it, so treat it as an upper bound for this workload._\n"
            )
        blocks.append("\n".join(rows))
    if not blocks:
        return ""
    return (
        "## Production-hardening & estate delta\n\n"
        "_Additional SKUs that appear as the workload leaves pilot and enters "
        "regulated production. `Estate-billed` items are amortised once across "
        "the whole estate, not per app. All ESTIMATES._\n\n"
        + "\n".join(blocks)
    )


def _render_presales_footer(manifest: dict[str, Any]) -> str:
    return (
        "---\n\n"
        "> **Estimates only.** Public list prices for one generic pilot, not a "
        "quote. Validate against the Azure Pricing Calculator and the customer's "
        "agreement before sharing externally. This skill does not provision or "
        "mutate any infrastructure.\n"
    )
