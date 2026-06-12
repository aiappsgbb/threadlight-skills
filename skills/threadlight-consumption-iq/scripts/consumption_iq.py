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

Exit codes:
  0  artefacts produced (per-finding statuses live inside the report)
  2  missing prerequisite (no SPEC § 12, stale safe-check, etc.)
  3  I/O failure OR Azure-pricing MCP unavailable AND no fixture fallback
     for at least one required SKU
  4  load_profile{} incomplete after wizard (interactive mode required)

Single-file CLI dispatcher; stdlib only. Per-phase logic lives in:
  scripts/discover.py
  scripts/load_profile_wizard.py
  scripts/pricing_client.py
  scripts/projectors/<resource>.py
  scripts/recommender.py
  scripts/emitter.py

Mirrors the dependency posture of `threadlight-safe-check` and
`threadlight-production-ready`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Resolve sibling modules without requiring a package install.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from discover import discover_resources  # noqa: E402
from emitter import emit_artefacts  # noqa: E402
from load_profile_wizard import load_or_prompt_profile, ProfileIncompleteError  # noqa: E402
from pricing_client import PricingClient, PricingUnavailableError  # noqa: E402
from recommender import score_and_rank  # noqa: E402
from projectors import project_resource  # noqa: E402

DEFAULT_CACHE_PATH = Path(".threadlight/cost-cache.json")
DEFAULT_OUTPUT_REPORT = Path("docs/cost-projection.md")
DEFAULT_OUTPUT_MANIFEST = Path("specs/cost-manifest.json")
DEFAULT_SPEC_PATH = Path("specs/SPEC.md")
DEFAULT_DEPLOYMENT_MANIFEST = Path("specs/manifest.json")
DEFAULT_BICEP_ENTRYPOINT = Path("infra/main.bicep")


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


# ---------- argument parsing -------------------------------------------------


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

    return parser


# ---------- dispatch ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

        if args.phase == "run":
            if not args.all:
                print("run requires --all in v1", file=sys.stderr)
                return 2
            resources = _phase_discover(args)
            profile = _phase_load_profile(args)
            pricing = PricingClient(cache_path=args.cache)
            projected = _phase_project(
                resources, profile, pricing, only=getattr(args, "only", None)
            )
            recs = _phase_recommend(projected, profile)
            _phase_emit(projected, recs, profile, args)
            if args.verbose:
                print(
                    f"emitted {args.report} and {args.manifest}",
                    file=sys.stderr,
                )
            return 0

        print(f"unknown phase: {args.phase}", file=sys.stderr)
        return 2

    except FileNotFoundError as exc:
        print(f"prerequisite missing: {exc}", file=sys.stderr)
        return 2
    except ProfileIncompleteError as exc:
        print(f"load profile incomplete: {exc}", file=sys.stderr)
        return 4
    except PricingUnavailableError as exc:
        print(f"pricing unavailable: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
