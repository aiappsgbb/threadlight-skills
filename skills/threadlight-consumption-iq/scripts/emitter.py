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
    # TODO(emitter): render per-resource section with current_sku +
    #                side-by-side alternatives table, the top-N
    #                recommendations table, and a mermaid donut of
    #                cost share by resource.
    return (
        "# Cost projection\n\n"
        f"> Generated `{manifest['generated_at']}` against deploy `{manifest['deploy_ref']}`.\n\n"
        "_See `specs/cost-manifest.json` for the full machine-readable manifest._\n\n"
        "## Totals\n\n"
        f"- **Monthly cost (current):** ${manifest['totals']['monthly_cost_current_usd']:.2f}\n"
        f"- **Monthly cost (recommended):** ${manifest['totals']['monthly_cost_recommended_usd']:.2f}\n"
        f"- **Monthly savings potential:** ${manifest['totals']['monthly_savings_potential_usd']:.2f}\n\n"
        "## Per-resource breakdown\n\n"
        "_TODO(emitter): render per-resource sections from manifest.resources[]._\n\n"
        "## Recommendations\n\n"
        f"_TODO(emitter): render manifest.recommendations[] ({len(manifest['recommendations'])} item(s))._\n"
    )
