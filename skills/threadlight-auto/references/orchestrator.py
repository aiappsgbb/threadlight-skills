#!/usr/bin/env python3
"""threadlight-auto orchestrator — decides which stages to skip / run / re-run.

This is NOT the worker. It's the state machine that the `threadlight-auto` SKILL
instructs the agent to run BEFORE each stage to figure out what to do next.

The orchestrator:
  1. Reads `.threadlight/auto-state.json` (if present)
  2. Checks each stage's artifact-freshness conditions
  3. Returns a JSON decision tree:
       - which stages will skip + why
       - which stages will run
       - any HARD STOPs that block forward progress
  4. With `--dry-run`, ONLY reports the decision tree (no side effects)
  5. With `--commit`, also writes the next-action JSON to
     `.threadlight/auto-next.json` for the agent to consume

Usage (from a workspace root):
  python3 .github/skills/threadlight-auto/references/orchestrator.py [--dry-run]
  python3 .github/skills/threadlight-auto/references/orchestrator.py --state-file <path>

Exit codes:
  0 — decision tree printed; agent should proceed
  1 — HARD STOP detected; agent must abort

Reference: skills/threadlight-auto/SKILL.md § Stage-to-skill mapping +
§ Smart-recovery table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Stage definitions — lockstep with SKILL.md § Resumption table
# -----------------------------------------------------------------------------

STAGES = ["preflight", "design", "deploy", "safe_check", "cost_projection", "invoke", "evals", "redteam", "govern"]

DEFAULT_STATE_PATH = ".threadlight/auto-state.json"
DEFAULT_NEXT_PATH = ".threadlight/auto-next.json"
PREFLIGHT_MARKER = ".threadlight/preflight-passed.json"

FRESHNESS_SECONDS = 24 * 60 * 60


@dataclass
class StageDecision:
    name: str
    decision: str  # "run" / "skip" / "hard_stop"
    reason: str
    artifacts_seen: list[str] = field(default_factory=list)
    artifacts_missing: list[str] = field(default_factory=list)
    hard_stop_signature: str | None = None


# -----------------------------------------------------------------------------
# Freshness probes — one per stage
# -----------------------------------------------------------------------------


def _file_age_seconds(p: Path) -> float | None:
    if not p.exists():
        return None
    return (datetime.now(timezone.utc) - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)).total_seconds()


def _sha256(p: Path) -> str | None:
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _check_preflight(workspace: Path, _: dict[str, Any]) -> StageDecision:
    marker = workspace / PREFLIGHT_MARKER
    age = _file_age_seconds(marker)
    if age is None:
        return StageDecision(
            "preflight",
            "run",
            "No preflight marker; bootstrap must run.",
            artifacts_missing=[str(PREFLIGHT_MARKER)],
        )
    if age > FRESHNESS_SECONDS:
        return StageDecision(
            "preflight",
            "run",
            f"Preflight marker is {int(age/3600)} h old (> 24 h); re-running.",
            artifacts_seen=[str(PREFLIGHT_MARKER)],
        )
    return StageDecision(
        "preflight",
        "skip",
        f"Preflight marker is {int(age/60)} m old (< 24 h).",
        artifacts_seen=[str(PREFLIGHT_MARKER)],
    )


def _check_design(workspace: Path, state: dict[str, Any]) -> StageDecision:
    spec = workspace / "specs" / "SPEC.md"
    if not spec.exists():
        return StageDecision(
            "design", "run", "specs/SPEC.md does not exist.", artifacts_missing=["specs/SPEC.md"]
        )

    text = spec.read_text(encoding="utf-8")
    if "[NEEDS CLARIFICATION:" in text:
        return StageDecision(
            "design",
            "hard_stop",
            "specs/SPEC.md contains unresolved [NEEDS CLARIFICATION:] markers.",
            artifacts_seen=["specs/SPEC.md"],
            hard_stop_signature="NEEDS CLARIFICATION marker in SPEC.md",
        )

    current_hash = _sha256(spec)
    last_hash = state.get("design", {}).get("artifact_hash")
    if last_hash and current_hash == last_hash:
        return StageDecision(
            "design",
            "skip",
            "specs/SPEC.md unchanged since last run (hash match).",
            artifacts_seen=["specs/SPEC.md"],
        )
    return StageDecision(
        "design",
        "skip" if last_hash is None else "run",
        (
            "specs/SPEC.md exists; no prior hash recorded — assume manual write, skip."
            if last_hash is None
            else f"specs/SPEC.md hash changed (was {last_hash[:8]}, now {current_hash[:8]}); re-run."
        ),
        artifacts_seen=["specs/SPEC.md"],
    )


def _check_deploy(workspace: Path, _: dict[str, Any]) -> StageDecision:
    main_bicep = workspace / "infra" / "main.bicep"
    azure_yaml = workspace / "azure.yaml"
    missing = []
    if not main_bicep.exists():
        missing.append("infra/main.bicep")
    if not azure_yaml.exists():
        missing.append("azure.yaml")
    if missing:
        return StageDecision(
            "deploy",
            "run",
            "Deploy artifacts missing; threadlight-deploy must scaffold + run azd up.",
            artifacts_missing=missing,
        )

    azure_dir = workspace / ".azure"
    if azure_dir.exists():
        env_files = list(azure_dir.glob("*/.env"))
        has_fqdn = any("AGENT_FQDN" in f.read_text(encoding="utf-8", errors="ignore") for f in env_files)
        if has_fqdn:
            return StageDecision(
                "deploy",
                "skip",
                "infra + azure.yaml exist + azd env has AGENT_FQDN. (azd ai agent show will verify status=active at run-time.)",
                artifacts_seen=["infra/main.bicep", "azure.yaml", ".azure/<env>/.env"],
            )
    return StageDecision(
        "deploy",
        "run",
        "infra + azure.yaml exist but no AGENT_FQDN in azd env — `azd up` hasn't completed.",
        artifacts_seen=["infra/main.bicep", "azure.yaml"],
    )


def _check_safe_check(workspace: Path, _: dict[str, Any]) -> StageDecision:
    safe_doc = workspace / "docs" / "safe-check-post.md"
    age = _file_age_seconds(safe_doc)
    if age is None:
        return StageDecision(
            "safe_check",
            "run",
            "docs/safe-check-post.md missing.",
            artifacts_missing=["docs/safe-check-post.md"],
        )
    if age > FRESHNESS_SECONDS:
        return StageDecision(
            "safe_check",
            "run",
            f"docs/safe-check-post.md is {int(age/3600)} h old (> 24 h); re-running.",
            artifacts_seen=["docs/safe-check-post.md"],
        )
    return StageDecision(
        "safe_check",
        "skip",
        f"docs/safe-check-post.md is {int(age/60)} m old (< 24 h).",
        artifacts_seen=["docs/safe-check-post.md"],
    )


def _check_invoke(workspace: Path, _: dict[str, Any]) -> StageDecision:
    invoke_doc = workspace / "docs" / "invoke-results.md"
    age = _file_age_seconds(invoke_doc)
    if age is None:
        return StageDecision(
            "invoke",
            "run",
            "docs/invoke-results.md missing — demo scenarios not yet run.",
            artifacts_missing=["docs/invoke-results.md"],
        )
    if age > FRESHNESS_SECONDS:
        return StageDecision(
            "invoke",
            "run",
            f"docs/invoke-results.md is {int(age/3600)} h old (> 24 h); re-running.",
            artifacts_seen=["docs/invoke-results.md"],
        )
    return StageDecision(
        "invoke",
        "skip",
        f"docs/invoke-results.md is {int(age/60)} m old (< 24 h).",
        artifacts_seen=["docs/invoke-results.md"],
    )


# NOTE: cost_projection phase sits between safe_check and invoke.
# It runs threadlight-consumption-iq to generate docs/cost-projection.md
# and specs/cost-manifest.json. It is advisory — exit codes other than 0
# are handled gracefully (see SKILL.md § cost-projection phase).
# Resumability: skip when SPEC § 12 load_profile{} is complete AND
# cost-manifest.json.generated_at > AZURE_LAST_DEPLOY_AT.

_LOAD_PROFILE_REQUIRED_KEYS = {
    "workload_class",
    "peak_concurrent_sessions",
    "avg_requests_per_session",
    "avg_tokens_per_request",
    "peak_requests_per_second",
    "business_hours_only",
    "cosmos_gb_year_one",
    "storage_gb_year_one",
    "ai_search_documents",
    "monthly_growth_rate",
}


def _load_profile_complete(spec_text: str) -> bool:
    """Return True if SPEC § 12 load_profile{} has all required keys filled in."""
    import re as _re
    m = _re.search(r"load_profile\s*:\s*\n((?:[ \t]+\S[^\n]*\n)*)", spec_text)
    if not m:
        return False
    block = m.group(1)
    found: set[str] = set()
    for line in block.splitlines():
        kv = line.strip()
        if ":" in kv and not kv.startswith("#"):
            key = kv.split(":")[0].strip()
            val = kv.split(":", 1)[1].strip()
            if val and not val.startswith("#") and "TBD" not in val.upper():
                found.add(key)
    return _LOAD_PROFILE_REQUIRED_KEYS.issubset(found)


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO-8601 UTC timestamp, return None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _check_cost_projection(workspace: Path, state: dict[str, Any]) -> StageDecision:
    cost_manifest = workspace / "specs" / "cost-manifest.json"
    spec_path = workspace / "specs" / "SPEC.md"

    # --- read relevant inputs ---
    spec_text = ""
    if spec_path.exists():
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
        except OSError:
            pass

    manifest_generated_at: datetime | None = None
    if cost_manifest.exists():
        try:
            import json as _json
            data = _json.loads(cost_manifest.read_text(encoding="utf-8"))
            manifest_generated_at = _parse_iso(data.get("generated_at", ""))
        except (OSError, ValueError):
            pass

    # --- resumability check ---
    # Skip if: load_profile{} complete AND manifest generated after last deploy
    last_deploy_ts: str = state.get("cost_projection", {}).get("last_deploy_at", "") or ""
    if not last_deploy_ts:
        # Fall back to reading azd env AZURE_LAST_DEPLOY_AT
        azure_dir = workspace / ".azure"
        if azure_dir.exists():
            for envfile in azure_dir.glob("*/.env"):
                for line in (envfile.read_text(encoding="utf-8", errors="ignore")).splitlines():
                    if line.startswith("AZURE_LAST_DEPLOY_AT="):
                        last_deploy_ts = line.split("=", 1)[1].strip().strip('"')
                        break

    last_deploy_dt = _parse_iso(last_deploy_ts)

    if (
        _load_profile_complete(spec_text)
        and manifest_generated_at is not None
        and last_deploy_dt is not None
        and manifest_generated_at >= last_deploy_dt
    ):
        passed_at = state.get("cost_projection", {}).get("passed_at", "")
        return StageDecision(
            "cost_projection",
            "skip",
            (
                f"cost-projection: load_profile complete + manifest fresh (generated_at "
                f"{manifest_generated_at.isoformat()} >= last_deploy {last_deploy_dt.isoformat()})."
                + (f" Previously passed at {passed_at}." if passed_at else "")
            ),
            artifacts_seen=["specs/SPEC.md", "specs/cost-manifest.json"],
        )

    # --- decide whether to run ---
    if not spec_path.exists():
        return StageDecision(
            "cost_projection",
            "run",
            "specs/SPEC.md missing — prerequisite for cost-projection. "
            "Missing prereq treated same as other prereq cases; stage will attempt and degrade.",
            artifacts_missing=["specs/SPEC.md"],
        )

    if not _load_profile_complete(spec_text):
        return StageDecision(
            "cost_projection",
            "run",
            "SPEC § 12 load_profile{} incomplete or absent — threadlight-consumption-iq wizard "
            "will prompt for values (exit 4 sets state cost-projection: needs-wizard; advisory, "
            "does not block chain).",
            artifacts_seen=["specs/SPEC.md"],
            artifacts_missing=["specs/SPEC.md#load_profile"],
        )

    return StageDecision(
        "cost_projection",
        "run",
        "specs/cost-manifest.json missing or stale — running threadlight-consumption-iq.",
        artifacts_seen=["specs/SPEC.md"],
        artifacts_missing=["specs/cost-manifest.json"],
    )


def _check_leg_manifest(workspace: Path, stage: str, manifest_rel: str, skill: str) -> StageDecision:
    """Generic resumability probe for a Discover/Protect leg.

    The leg is re-run when its manifest under specs/ is missing or older than
    the 24 h session-freshness window, and skipped when a fresh manifest is
    present. Mirrors the invoke/safe_check pattern so a re-deploy upstream
    cascades a fresh evaluation / scan / governance pass.
    """
    manifest = workspace / manifest_rel
    age = _file_age_seconds(manifest)
    if age is None:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} missing — {skill} has not produced its leg manifest yet.",
            artifacts_missing=[manifest_rel],
        )
    if age > FRESHNESS_SECONDS:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} is {int(age/3600)} h old (> 24 h); re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    return StageDecision(
        stage,
        "skip",
        f"{manifest_rel} is {int(age/60)} m old (< 24 h).",
        artifacts_seen=[manifest_rel],
    )


# Discover leg — offline + online (Foundry Continuous Evaluation) + A/B evals.
# Runs threadlight-evals; emits specs/evals-manifest.json consumed by
# production-ready pillar 6 (EVAL-001..004).
def _check_evals(workspace: Path, _: dict[str, Any]) -> StageDecision:
    return _check_leg_manifest(workspace, "evals", "specs/evals-manifest.json", "threadlight-evals")


# Discover leg — AI Red Teaming Agent adversarial scan. Runs threadlight-redteam;
# emits docs/redteam-report.md + specs/redteam-manifest.json mapped to
# production-ready pillar 7 (SAFE-101..106).
def _check_redteam(workspace: Path, _: dict[str, Any]) -> StageDecision:
    return _check_leg_manifest(workspace, "redteam", "specs/redteam-manifest.json", "threadlight-redteam")


# Protect leg — agent-runtime governance (AGT). Runs threadlight-govern; emits
# the verifier report + specs/govern-manifest.json consumed by production-ready
# pillar 2 (AGT-001..005) and pillar 7 (RAI-002/003).
def _check_govern(workspace: Path, _: dict[str, Any]) -> StageDecision:
    return _check_leg_manifest(workspace, "govern", "specs/govern-manifest.json", "threadlight-govern")


STAGE_PROBES = {
    "preflight": _check_preflight,
    "design": _check_design,
    "deploy": _check_deploy,
    "safe_check": _check_safe_check,
    "cost_projection": _check_cost_projection,
    "invoke": _check_invoke,
    "evals": _check_evals,
    "redteam": _check_redteam,
    "govern": _check_govern,
}


# -----------------------------------------------------------------------------
# Cascade — if an earlier stage runs, all later stages also run
# -----------------------------------------------------------------------------


def _cascade_invalidations(decisions: list[StageDecision]) -> list[StageDecision]:
    seen_run = False
    out: list[StageDecision] = []
    for d in decisions:
        if seen_run and d.decision == "skip":
            out.append(
                StageDecision(
                    d.name,
                    "run",
                    "Cascade re-run: an earlier stage will re-run; downstream stage must follow.",
                    artifacts_seen=d.artifacts_seen,
                    artifacts_missing=d.artifacts_missing,
                )
            )
        else:
            out.append(d)
        if d.decision in ("run", "hard_stop"):
            seen_run = True
    return out


# -----------------------------------------------------------------------------
# Main driver
# -----------------------------------------------------------------------------


def _read_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"WARN: {state_path} is not valid JSON ({exc}); treating as empty.", file=sys.stderr)
        return {}


def decide(workspace: Path, state_path: Path | None) -> dict[str, Any]:
    state = _read_state(state_path) if state_path else {}
    decisions: list[StageDecision] = []
    for stage in STAGES:
        probe = STAGE_PROBES[stage]
        decisions.append(probe(workspace, state))
    decisions = _cascade_invalidations(decisions)

    hard_stop = next((d for d in decisions if d.decision == "hard_stop"), None)

    return {
        "workspace": str(workspace),
        "state_file": str(state_path) if state_path else None,
        "decisions": [
            {
                "stage": d.name,
                "decision": d.decision,
                "reason": d.reason,
                "artifacts_seen": d.artifacts_seen,
                "artifacts_missing": d.artifacts_missing,
                "hard_stop_signature": d.hard_stop_signature,
            }
            for d in decisions
        ],
        "next_action": (
            {
                "type": "hard_stop",
                "stage": hard_stop.name,
                "signature": hard_stop.hard_stop_signature,
                "reason": hard_stop.reason,
            }
            if hard_stop
            else {
                "type": "run",
                "stages_to_run": [d.name for d in decisions if d.decision == "run"],
                "stages_to_skip": [d.name for d in decisions if d.decision == "skip"],
            }
        ),
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"=== threadlight-auto decision tree ({report['workspace']}) ===\n")
    for d in report["decisions"]:
        marker = {"skip": "⊘ SKIP", "run": "▶ RUN ", "hard_stop": "🛑 STOP"}[d["decision"]]
        print(f"  {marker}  {d['stage']:10} — {d['reason']}")
    print()
    na = report["next_action"]
    if na["type"] == "hard_stop":
        print(f"🛑 HARD STOP at stage '{na['stage']}': {na['signature'] or na['reason']}")
    else:
        skip = ", ".join(na["stages_to_skip"]) or "(none)"
        run = ", ".join(na["stages_to_run"]) or "(none — all stages complete)"
        print(f"Skip:  {skip}")
        print(f"Run:   {run}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", default=".", help="Workspace root (default: cwd)")
    p.add_argument(
        "--state-file",
        default=DEFAULT_STATE_PATH,
        help=f"Path to state JSON (default: {DEFAULT_STATE_PATH}; relative to workspace)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print the decision tree only")
    p.add_argument("--commit", action="store_true", help="Write auto-next.json to drive the agent's next step")
    p.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    args = p.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"ERROR: workspace {workspace} is not a directory", file=sys.stderr)
        return 1

    state_path = workspace / args.state_file
    report = decide(workspace, state_path)

    if args.output == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)

    if args.commit and not args.dry_run:
        next_path = workspace / DEFAULT_NEXT_PATH
        next_path.parent.mkdir(parents=True, exist_ok=True)
        next_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if args.output == "human":
            print(f"\nWrote {next_path}")

    if report["next_action"]["type"] == "hard_stop":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
