"""
Pre-sales orchestrator (`estimate.py`).

The post-deploy chain projects ONE deployed load. This module is the pre-sales
front-end it lacks: given a **rollout profile** (N adoption phases, each its own
load + posture) and a **resource topology**, it walks every phase and:

  1. projects every resource at that phase's `load_profile` (reusing the v1
     per-resource projectors — observability included, since the Log Analytics
     workspace is just another resource_kind in the registry);
  2. appends the production-hardening / estate **delta** for that phase's posture;
  3. scores SKU recommendations on the **current** phase only (the others are
     forward-looking topologies, not things you'd right-size today);

then hands the projected phases to the emitter, which recomputes totals, mirrors
the current phase into top-level `totals.*`, applies any EA/MCA discount, and
writes the phased manifest + markdown. Optionally renders the seller one-pager.

Importable in-process (mirrors `test_e2e.py`) so the golden test can call
`run_presales(...)` directly. Stdlib only; the CLI `estimate` subcommand is a
thin wrapper over `emit_presales`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from emitter import build_presales_manifest, emit_presales_artefacts  # noqa: E402
from hardening import project_hardening  # noqa: E402
from onepager import write_onepager  # noqa: E402
from projectors import project_resource  # noqa: E402
from recommender import score_and_rank  # noqa: E402
from rollout import phase_resources  # noqa: E402


def project_phases(
    rollout_profile: dict[str, Any],
    resources: list[dict[str, Any]] | None,
    pricing: Any,
) -> list[dict[str, Any]]:
    """Project every resource + hardening delta for each phase of the rollout.

    Topology per phase is resolved (phase override > rollout top-level >
    `resources` arg) so a pre-sales estimate can be fully self-contained — no
    repo discovery required — and can step SKUs across phases (e.g. AI Search
    Basic in the POC, S2 once it's business-wide).
    """
    current_phase_id = rollout_profile.get("current_phase")
    out: list[dict[str, Any]] = []

    for phase in rollout_profile.get("phases", []):
        load_profile = phase["load_profile"]
        topology = phase_resources(rollout_profile, phase, default=resources or [])
        projected = [project_resource(r, load_profile, pricing) for r in topology]
        hardening = project_hardening(phase["posture"], load_profile, pricing)
        is_current = phase["id"] == current_phase_id
        recommendations = score_and_rank(projected, load_profile) if is_current else []

        phase_obj: dict[str, Any] = {
            "id": phase["id"],
            "label": phase["label"],
            "posture": phase["posture"],
            "audience": phase.get("audience", "internal"),
            "resources": projected,
            "hardening_delta": hardening,
            "recommendations": recommendations,
        }
        if phase.get("benchmark"):
            phase_obj["benchmark"] = phase["benchmark"]
        out.append(phase_obj)
    return out


def run_presales(
    rollout_profile: dict[str, Any],
    resources: list[dict[str, Any]],
    pricing: Any,
    *,
    deploy_ref: str = "pre-sales",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Project all phases and return the phased pre-sales manifest (schema 1.1)."""
    phases = project_phases(rollout_profile, resources, pricing)
    return build_presales_manifest(
        phases, rollout_profile, deploy_ref=deploy_ref, generated_at=generated_at
    )


def emit_presales(
    rollout_profile: dict[str, Any],
    resources: list[dict[str, Any]],
    pricing: Any,
    *,
    report_path: Path,
    manifest_path: Path,
    onepager_path: Path | None = None,
    audience: str | None = None,
    pdf: bool = False,
    deploy_ref: str = "pre-sales",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Project + emit the phased manifest, markdown, and (optionally) one-pager.

    Returns {manifest, report_path, manifest_path, onepager}. When `audience` is
    not given, the one-pager defaults to the **current phase's** audience.
    """
    phases = project_phases(rollout_profile, resources, pricing)
    manifest = emit_presales_artefacts(
        phases=phases,
        rollout_profile=rollout_profile,
        report_path=Path(report_path),
        manifest_path=Path(manifest_path),
        deploy_ref=deploy_ref,
        generated_at=generated_at,
    )

    result: dict[str, Any] = {
        "manifest": manifest,
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "onepager": None,
    }
    if onepager_path:
        aud = audience or _current_phase_audience(rollout_profile, manifest)
        result["onepager"] = write_onepager(
            manifest, Path(onepager_path), audience=aud, pdf=pdf
        )
    return result


def _current_phase_audience(rollout_profile: dict[str, Any], manifest: dict[str, Any]) -> str:
    current = manifest.get("current_phase") or rollout_profile.get("current_phase")
    for phase in manifest.get("phases", []):
        if phase["id"] == current:
            return phase.get("audience", "internal")
    return "internal"
