#!/usr/bin/env python3
"""
threadlight-production-ready CLI

Advisory production-readiness checker for threadlight pilots.

Runs after `threadlight-safe-check --phase post-deploy` returns green.
Resolves the target production posture (Citadel-spoke / AGT / standard AI gateway),
walks 13 cross-cutting pillars (network, AGT, IAM, secrets, observability, evals,
RAI, HITL, supply-chain, cost, reliability, SRE handover, model lifecycle), and
emits:

  * tests/production-readiness-manifest.json  (machine-readable)
  * docs/production-readiness-report.md       (customer-facing)

Soft-advisory: never fails a build. Missing live-probe permissions => `not-verified`.

Exit codes:
  0  report produced (per-finding statuses live inside the report)
  2  missing prerequisite (no SPEC sec 12, stale safe-check, etc.)
  3  I/O failure or `az` not on PATH

Single-file by design. stdlib only. `az` CLI subprocess for live probes.
Mirrors the dependency posture of `threadlight-safe-check`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# region: apply_plan_schema (v0.4.0)
# ---------------------------------------------------------------------------

APPLY_PLAN_KINDS = {"repo-edit", "sibling-skill", "manual", "deferred-to-pipeline"}
APPLY_PLAN_SCHEMA_VERSION = 1


def build_apply_plan(*, manifest: dict, recipes: dict, framing: dict,
                     framing_path: str | None = None) -> dict:
    """Build an apply-plan from an assessor manifest + loaded recipe catalog.

    Walks `manifest["findings"]` if present, otherwise flattens
    `manifest["pillars"][].findings[]` (the v0.3.0 OUTPUT shape). For every
    finding whose status is `fail`, `warn`, or `not-verified`, emits an
    entry that either points at the registered recipe or falls back to a
    `kind: manual` placeholder. Pins `manifest_sha256` so the agent can
    detect a stale plan in Phase 2.

    Raises SystemExit if any recipe declares an unknown `kind`.
    """
    sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    findings = manifest.get("findings")
    if not findings:
        findings = [
            f for p in manifest.get("pillars", [])
            for f in p.get("findings", [])
        ]
    items: list[dict] = []
    for f in findings:
        if f.get("status") not in {"fail", "warn", "not-verified"}:
            continue
        rid = f.get("id")
        if not rid:
            continue
        recipe = recipes.get(rid, {
            "kind": "manual",
            "summary": f"No recipe registered for {rid}; consult the pillar reference.",
        })
        if recipe["kind"] not in APPLY_PLAN_KINDS:
            raise SystemExit(
                f"apply-plan: recipe for {rid} has unknown kind {recipe['kind']!r}; "
                f"expected one of {sorted(APPLY_PLAN_KINDS)}"
            )
        # In a restricted environment (e.g., central-team handoff) the agent
        # must not auto-mutate the user's repo. Demote any `repo-edit` recipe
        # to `manual` so the agent surfaces it to the user instead of editing.
        if framing.get("restricted_environment") and recipe["kind"] == "repo-edit":
            recipe = {
                **recipe,
                "kind": "manual",
                "summary": (
                    f"[demoted from repo-edit due to restricted_environment] "
                    f"{recipe.get('summary', '')}"
                ).strip(),
            }
        items.append({"finding_id": rid, **recipe})
    plan: dict = {
        "schema_version": APPLY_PLAN_SCHEMA_VERSION,
        "manifest_sha256": sha,
        "framing": framing,
        "items": items,
    }
    if framing_path is not None:
        plan["framing_path"] = framing_path
    return plan


def write_apply_plan(plan: dict, path) -> None:
    Path(path).write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


def _recipe_catalog_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "remediation-recipes"


def load_recipe_catalog(rdir) -> dict[str, dict]:
    """Parse `references/remediation-recipes/{ID}.md`.

    Each recipe has YAML front-matter (between two `---` lines) with at least
    a `kind` field. Files starting with `_` (e.g. `_template.md`) are skipped.

    Raises SystemExit on missing/invalid front-matter or unknown kind.
    """
    recipes: dict[str, dict] = {}
    for path in sorted(Path(rdir).glob("*.md")):
        if path.name.startswith("_"):
            continue
        rid = path.stem
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise SystemExit(f"recipe {rid}: missing YAML front-matter")
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            raise SystemExit(f"recipe {rid}: malformed front-matter")
        fm = parts[1]
        meta: dict[str, str] = {}
        for line in fm.strip().splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
        if meta.get("kind") not in APPLY_PLAN_KINDS:
            raise SystemExit(
                f"recipe {rid}: kind {meta.get('kind')!r} not in {sorted(APPLY_PLAN_KINDS)}"
            )
        recipes[rid] = {
            "kind": meta["kind"],
            "summary": meta.get("summary", ""),
            "target_file": meta.get("target_file"),
            "edit_type": meta.get("edit_type"),
            "sibling_skill": meta.get("sibling_skill"),
            "recipe_path": str(path),
        }
    return recipes


# endregion: apply_plan_schema


# ---------------------------------------------------------------------------
# region: rights_constants (v0.4.0)
# ---------------------------------------------------------------------------
#
# Rights classification + phase-2 decision constants. Used by Phase C's
# provisioning-rights probe and the phase decision banner.

RIGHTS_FULL = "full"
RIGHTS_CONSTRAINED = "constrained"
RIGHTS_NONE = "none"
RIGHTS_UNKNOWN = "unknown"

PHASE_SELF_SERVICE = "self-service"
PHASE_CENTRAL_HANDOFF = "central-team handoff"
PHASE_BLOCKED = "blocked — no RG access"

WRITE_ROLES = ("Owner", "Contributor", "User Access Administrator")
READ_ROLES = ("Reader", "Monitoring Reader", "Cost Management Reader")

# endregion: rights_constants


# ---------------------------------------------------------------------------
# region: rights_probe (v0.4.0)
# ---------------------------------------------------------------------------
#
# Live RBAC probe used by --onboard mode. Decides whether the operator
# can actually execute Phase 2 deployments in the target subscription.

def _classify_rights(roles):
    """Classify a list of role-definition names into one of:
    RIGHTS_FULL / RIGHTS_CONSTRAINED / RIGHTS_NONE.

    Write-capable roles win over read-only roles.
    """
    role_set = {r.strip() for r in roles if r}
    if role_set & set(WRITE_ROLES):
        return RIGHTS_FULL
    if role_set & set(READ_ROLES):
        return RIGHTS_CONSTRAINED
    return RIGHTS_NONE


def _probe_provisioning_rights(subscription_id, resource_group, skip=False):
    """Shell `az role assignment list --assignee @me --scope <RG-scope>`,
    classify the returned roles. Returns a dict suitable for the manifest.

    Never raises — on any error returns rights_class=unknown with the
    error string. When skip=True, returns rights_class=unknown with
    probe_skipped=True and no shell-out (used by --no-rights-probe and CI).
    """
    if skip:
        return {
            "rights_class": RIGHTS_UNKNOWN,
            "roles": [],
            "probe_skipped": True,
            "error": None,
        }
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    cmd = [
        "az", "role", "assignment", "list",
        "--assignee", "@me",
        "--scope", scope,
        "-o", "json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        return {
            "rights_class": RIGHTS_UNKNOWN,
            "roles": [],
            "probe_skipped": False,
            "error": f"az invocation failed: {e}",
        }
    if r.returncode != 0:
        return {
            "rights_class": RIGHTS_UNKNOWN,
            "roles": [],
            "probe_skipped": False,
            "error": (r.stderr or "").strip() or "az exited non-zero",
        }
    try:
        data = json.loads(r.stdout or "[]")
    except json.JSONDecodeError as e:
        return {
            "rights_class": RIGHTS_UNKNOWN,
            "roles": [],
            "probe_skipped": False,
            "error": f"json parse failed: {e}",
        }
    roles = [a.get("roleDefinitionName", "") for a in data]
    return {
        "rights_class": _classify_rights(roles),
        "roles": roles,
        "probe_skipped": False,
        "error": None,
    }

# endregion: rights_probe


# ---------------------------------------------------------------------------
# region: phase_decision (v0.4.0)
# ---------------------------------------------------------------------------
#
# Decides Phase 2 mode (self-service vs central-team handoff vs blocked)
# from the framing answers + live rights probe result. Pure function.

def _phase_decision(framing, rights_result):
    """Decide phase-2 mode from framing + live rights probe.
    Pure function — no I/O. Returns {phase2_mode, reason, warning}.

    Decision order:
      1. restricted_environment in framing → central handoff (regardless of rights)
      2. rights_class=none → blocked (operator needs an access request first)
      3. rights_class=full → self-service
      4. rights_class=unknown → central handoff + warning (assume constrained)
      5. otherwise (constrained) → central handoff
    """
    rclass = rights_result["rights_class"]
    if framing.get("restricted_environment"):
        return {
            "phase2_mode": PHASE_CENTRAL_HANDOFF,
            "reason": "Framing-Q6 marked restricted environment — handoff regardless of rights",
            "warning": None,
        }
    if rclass == RIGHTS_NONE:
        return {
            "phase2_mode": PHASE_BLOCKED,
            "reason": "Operator has no role assignments at target RG scope",
            "warning": "Request access (Contributor or Owner) before re-running",
        }
    if rclass == RIGHTS_FULL:
        return {
            "phase2_mode": PHASE_SELF_SERVICE,
            "reason": "Operator has write-capable role at RG scope",
            "warning": None,
        }
    if rclass == RIGHTS_UNKNOWN:
        return {
            "phase2_mode": PHASE_CENTRAL_HANDOFF,
            "reason": "Rights probe skipped or failed — assuming constrained",
            "warning": "Re-run without --no-rights-probe to confirm self-service eligibility",
        }
    return {
        "phase2_mode": PHASE_CENTRAL_HANDOFF,
        "reason": f"Operator rights classified as {rclass}",
        "warning": None,
    }


def _emit_phase_banner(framing, rights, decision, sink=None):
    """Render the v0.4.0 phase-decision banner. Side-effecting (writes to sink).
    Default sink is sys.stderr.
    """
    if sink is None:
        sink = sys.stderr
    bar = "━" * 64
    print(bar, file=sink)
    print("THREADLIGHT v0.4.0 — Production Onboarding", file=sink)
    print(
        f"  Target: {framing.get('target_subscription_id')}/"
        f"{framing.get('target_resource_group')}",
        file=sink,
    )
    print(f"  Posture: {framing.get('target_posture')}", file=sink)
    print(
        f"  Rights:  {rights['rights_class']}  "
        f"(roles: {', '.join(rights['roles']) or '—'})",
        file=sink,
    )
    print(f"  Phase 2: ⇒ {decision['phase2_mode']}", file=sink)
    print(f"           reason: {decision['reason']}", file=sink)
    if decision.get("warning"):
        print(f"  ⚠ warning: {decision['warning']}", file=sink)
    print(bar, file=sink)

# endregion: phase_decision


# ---------------------------------------------------------------------------
# region: onboard_runner (v0.4.0)
# ---------------------------------------------------------------------------
#
# Real implementations for the --onboard side-channel. Replaces the
# Phase A stubs (_run_assessment_for_onboard + _phase_decision_banner)
# with rights-probe-aware logic per Phase C6.

def _run_assessment_for_onboard(args, framing: dict) -> dict:
    """Run the assessor for --onboard and return the manifest dict.

    Loads the input manifest (assumed to have been built by an upstream
    v0.3.0-style assessment run, or a hand-authored fixture), probes the
    operator's RBAC against the target RG, decides Phase 2 mode, and
    attaches both `rights_probe` and `phase_decision` to the returned
    manifest. The banner is emitted to stderr as a side effect so the
    operator sees the decision before apply-plan.json is materialized.
    """
    root = Path(args.root) if getattr(args, "root", None) else Path.cwd()
    manifest_path = root / args.in_manifest
    if not manifest_path.is_file():
        raise SystemExit(
            f"--onboard: expected manifest at {manifest_path} (run the v0.3.0 "
            f"assessment first, or pass --in-manifest pointing at an existing "
            f"production-readiness-manifest.json)."
        )
    manifest = _load_manifest(manifest_path)

    sub = getattr(args, "target_sub", None) or framing.get("target_subscription_id")
    rg = getattr(args, "target_rg", None) or framing.get("target_resource_group")
    rights = _probe_provisioning_rights(
        sub, rg, skip=getattr(args, "no_rights_probe", False)
    )
    decision = _phase_decision(framing, rights)
    _emit_phase_banner(framing, rights, decision, sink=sys.stderr)
    manifest["rights_probe"] = rights
    manifest["phase_decision"] = decision
    return manifest

# endregion: onboard_runner


# ---------------------------------------------------------------------------
# region: cicd_scaffold (v0.4.0)
# ---------------------------------------------------------------------------
#
# Pure-stdlib template renderer + writer for Phase 3 (CI/CD handoff).
# Two templates live under references/cicd-templates/:
#   - azd-deploy-prod.yml.tmpl       (dev-facing GitHub Actions workflow)
#   - central-team-uami-readme.md.tmpl  (central platform team runbook)
# `{{TOKEN}}` placeholders are substituted from the framing dict.

def _render_template(path, context: dict) -> str:
    """Read a .tmpl file and substitute {{KEY}} markers from context.

    Unknown markers are left untouched so the operator can spot gaps.
    """
    text = Path(path).read_text()
    for k, v in context.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _cicd_context_from_framing(framing: dict, repo_full_name: str, env_name: str = "prod") -> dict:
    """Build the template-rendering context from framing + repo name."""
    import datetime
    sub = framing.get("target_subscription_id", "")
    rg = framing.get("target_resource_group", "")
    slug = repo_full_name.replace("/", "-")
    return {
        "TARGET_SUBSCRIPTION_ID": sub,
        "TARGET_RESOURCE_GROUP": rg,
        "TARGET_LOCATION": framing.get("target_location", "eastus"),
        "TARGET_POSTURE": framing.get("target_posture", ""),
        "REPO_FULL_NAME": repo_full_name,
        "REPO_SLUG": slug,
        "UAMI_NAME": f"uami-{slug}-prod-deploy",
        "UAMI_RESOURCE_GROUP": framing.get("central_platform_team_rg", rg),
        "UAMI_SUBSCRIPTION_ID": framing.get("central_platform_team_sub", sub),
        "AZURE_TENANT_ID": framing.get("azure_tenant_id", "<tenant-id>"),
        "AZURE_ENV_NAME": env_name,
        "ISO_TIMESTAMP": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }


def _scaffold_cicd(framing: dict, repo_full_name: str, out_root) -> list:
    """Render both CI/CD templates into out_root. Returns list of written Paths."""
    out_root = Path(out_root)
    tmpl_dir = Path(__file__).resolve().parent.parent / "references" / "cicd-templates"
    ctx = _cicd_context_from_framing(framing, repo_full_name)
    pairs = [
        (tmpl_dir / "azd-deploy-prod.yml.tmpl",
         out_root / ".github" / "workflows" / "azd-deploy-prod.yml"),
        (tmpl_dir / "central-team-uami-readme.md.tmpl",
         out_root / "docs" / "threadlight-cicd" / "central-team-uami-readme.md"),
    ]
    written = []
    for tmpl, dest in pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_render_template(tmpl, ctx))
        written.append(dest)
    return written


def _detect_repo_full_name(cwd: str):
    """Parse `git remote get-url origin` -> 'owner/repo' or None."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    import re
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _hint_pipeline_scaffold_if_needed(apply_plan: dict, scaffold_cicd_flag: bool) -> None:
    """If apply-plan has deferred-to-pipeline items and --scaffold-cicd was
    NOT passed, emit a stderr hint pointing the operator at the flag."""
    if scaffold_cicd_flag:
        return
    pipeline = [i for i in apply_plan.get("items", []) if i.get("kind") == "deferred-to-pipeline"]
    if not pipeline:
        return
    _eprint(
        f"hint: apply-plan contains {len(pipeline)} deferred-to-pipeline item(s). "
        f"Re-run with --scaffold-cicd to generate the GitHub Actions workflow + UAMI runbook."
    )

# endregion: cicd_scaffold


VERSION = "0.5.0"

# Files emitted by THIS assessor that must never be ingested by a subsequent run
# (issue #30 — assessor idempotency). _glob_repo filters these out.
EXCLUDE_GLOBS = (
    "production-readiness-report.md",
    "production-readiness-report.json",
    "production-readiness-findings.csv",
    "production-readiness-findings.md",
)

# ---------------------------------------------------------------------------
# region: framing_wizard (v0.4.0)
# ---------------------------------------------------------------------------

FRAMING_QUESTIONS = [
    {
        "id": "target_subscription_id",
        "prompt": "Azure subscription ID for the production target?",
        "kind": "text",
        "required": True,
    },
    {
        "id": "target_resource_group",
        "prompt": "Resource group name for the production target?",
        "kind": "text",
        "required": True,
    },
    {
        "id": "target_posture",
        "prompt": "Posture profile?",
        "kind": "choice",
        "choices": ["citadel-spoke", "standard-ai-gateway", "agt", "hybrid"],
        "required": True,
    },
    {
        "id": "provisioning_rights",
        "prompt": "Do you have Contributor or higher on the target RG?",
        "kind": "bool",
        "required": True,
    },
    {
        "id": "central_platform_team",
        "prompt": "Is there a central platform/Citadel team that owns shared infra (gateways, KV, networking)?",
        "kind": "bool",
        "required": True,
    },
    {
        "id": "restricted_environment",
        "prompt": "Is direct write access to the target restricted (i.e. all changes must go through CI/CD)?",
        "kind": "bool",
        "required": True,
    },
    {
        "id": "cicd_target",
        "prompt": "CI/CD target? (only github-actions in v0.4.0+v0.5.0; azure-devops + gitlab are deferred to v0.6.0+)",
        "kind": "choice",
        "choices": ["github-actions"],
        "required": True,
    },
    {
        "id": "azure_tenant_id",
        "prompt": "Azure tenant ID (UUID) where the production subscription lives",
        "help": "Find it via `az account show --query tenantId -o tsv`. UUID format required.",
        "kind": "text",
        "required": True,
    },
]


def _coerce_bool(s: str) -> bool | None:
    s = s.strip().lower()
    if s in {"y", "yes", "true", "1"}:
        return True
    if s in {"n", "no", "false", "0"}:
        return False
    return None


def run_framing_wizard(istream=None, ostream=None) -> dict[str, Any]:
    """TTY-driven 8-question framing wizard.

    Re-prompts on invalid choice / bool input. Raises SystemExit on EOF for
    required questions. Returns {question_id: answer} dict. Renders `help`
    text (if present on a question) once before the first prompt of that
    question so operators have format hints (e.g. "UUID required").
    """
    istream = istream if istream is not None else sys.stdin
    ostream = ostream if ostream is not None else sys.stdout
    answers: dict[str, Any] = {}
    for q in FRAMING_QUESTIONS:
        first_prompt = True
        while True:
            print(q["prompt"], file=ostream)
            if first_prompt and q.get("help"):
                print(f"  {q['help']}", file=ostream)
                first_prompt = False
            if q["kind"] == "choice":
                print(f"  choices: {', '.join(q['choices'])}", file=ostream)
            raw = istream.readline()
            if not raw and q["required"]:
                raise SystemExit(
                    f"framing wizard: EOF on required question {q['id']}"
                )
            raw = raw.strip()
            if q["kind"] == "bool":
                v = _coerce_bool(raw)
                if v is None:
                    continue
                answers[q["id"]] = v
                break
            if q["kind"] == "choice":
                if raw not in q["choices"]:
                    continue
                answers[q["id"]] = raw
                break
            # text
            if not raw and q["required"]:
                continue
            answers[q["id"]] = raw
            break
    return answers


def load_framing_file(path) -> dict[str, Any]:
    """Load + validate a framing-answers JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = [q["id"] for q in FRAMING_QUESTIONS if q["required"]]
    missing = [k for k in required if k not in data]
    if missing:
        raise SystemExit(f"framing file: missing required keys: {missing}")
    return data


# endregion: framing_wizard

PILLAR_IDS = [
    "network-posture",
    "agent-governance",
    "identity-access",
    "secrets",
    "observability",
    "continuous-evals",
    "responsible-ai",
    "hitl-audit",
    "supply-chain",
    "cost",
    "reliability",
    "sre-handover",
    "model-lifecycle",
]

PILLAR_TITLES = {
    "network-posture": "1. Network posture",
    "agent-governance": "2. Agent governance (AGT)",
    "identity-access": "3. Identity and access",
    "secrets": "4. Secrets",
    "observability": "5. Observability",
    "continuous-evals": "6. Continuous evals",
    "responsible-ai": "7. Responsible AI",
    "hitl-audit": "8. HITL and audit",
    "supply-chain": "9. Supply chain",
    "cost": "10. Cost",
    "reliability": "11. Reliability",
    "sre-handover": "12. SRE handover",
    "model-lifecycle": "13. Model lifecycle",
}

# Permission tier for each finding ID prefix-and-number range.
# Tier 0 == static (no Azure call). Tier 1..5 documented in
# references/live-probe-permissions.md.
TIER_TO_LABEL = {
    0: "static",
    1: "T1 Reader",
    2: "T2 Monitoring + LA Reader",
    3: "T3 Cost Management Reader",
    4: "T4 Key Vault Reader (control plane)",
    5: "T5 APIM Service Reader on hub",
}

# Finding catalog. Source-of-truth for IDs, titles, default severity, pillar,
# and tier. Lives alongside the markdown pillar references so the CLI can
# emit `not-verified` rows for unimplemented live probes honestly.
#
# severity: must-fix | should-fix | informational
# pillar:   one of PILLAR_IDS
# tier:     0 static / 1..5 live
FINDING_CATALOG: dict[str, dict[str, Any]] = {
    # ---- network-posture
    "NET-001": {"title": "infra references network module", "pillar": "network-posture", "severity": "must-fix", "tier": 0},
    "NET-002": {"title": "Private endpoints declared for Foundry account", "pillar": "network-posture", "severity": "must-fix", "tier": 0},
    "NET-003": {"title": "Public network access disabled on AI services", "pillar": "network-posture", "severity": "must-fix", "tier": 0},
    "NET-004": {"title": "Subnet delegation correct for ACA / Functions", "pillar": "network-posture", "severity": "should-fix", "tier": 0},
    "NET-101": {"title": "Foundry account publicNetworkAccess=Disabled (live)", "pillar": "network-posture", "severity": "must-fix", "tier": 1},
    "NET-102": {"title": "Private endpoint resources exist and approved", "pillar": "network-posture", "severity": "must-fix", "tier": 1},
    "NET-103": {"title": "NSG flow logs enabled on spoke subnets", "pillar": "network-posture", "severity": "should-fix", "tier": 1, "experimental": True},
    "NET-501": {"title": "Citadel APIM Access Contract present", "pillar": "network-posture", "severity": "must-fix", "tier": 5},
    "NET-502": {"title": "Foundry connection to Citadel hub reachable", "pillar": "network-posture", "severity": "must-fix", "tier": 5},
    "NET-503": {"title": "Hub-side product policy attached", "pillar": "network-posture", "severity": "should-fix", "tier": 5, "experimental": True},
    "POS-001": {"title": "Declared posture matches detected evidence", "pillar": "network-posture", "severity": "should-fix", "tier": 1},

    # ---- agent-governance (AGT)
    "AGT-001": {"title": "AGT middleware imported in src/", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-002": {"title": "policy.yaml present in repo", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-003": {"title": "OWASP ASI 2026 verifier referenced", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-004": {"title": "AGT version pinned (not floating)", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-005": {"title": "AGT policy covers tool calls + prompt shields", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-006": {"title": "AGT telemetry sink configured", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-101": {"title": "Workload identity scoped to AGT-required RBAC", "pillar": "agent-governance", "severity": "should-fix", "tier": 1},
    "AGT-102": {"title": "AGT denials visible in App Insights last 24h", "pillar": "agent-governance", "severity": "should-fix", "tier": 2, "experimental": True},
    # ---- agent-governance — v4-preview deep checks (gated to --agt-profile v4_preview)
    # See docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md for rationale.
    # These IDs are only emitted by _check_agt_static_v4 / _check_agt_live_v4; never by v3.7 paths.
    "AGT-V4-001": {"title": "AGT v4 distribution names declared in dependencies", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-V4-002": {"title": "AGT v4 policy uses ACS intervention_points schema", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-V4-003": {"title": "AGT v4 dynamic policy conditions (time/cost/quota) detected", "pillar": "agent-governance", "severity": "informational", "tier": 0},
    "AGT-V4-006": {"title": "AGT v4 composite GitHub Action pinned via toolkit-version", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-V4-007": {"title": "AGT v4 audit fields present in committed verifier JSON", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-V4-101": {"title": "AGT v4 denials carry v4-shaped policy_version in App Insights", "pillar": "agent-governance", "severity": "should-fix", "tier": 2, "experimental": True},

    # ---- identity-access
    "IAM-001": {"title": "No client secrets in repo (managed identity only)", "pillar": "identity-access", "severity": "must-fix", "tier": 0},
    "IAM-002": {"title": "User-assigned managed identity declared in Bicep", "pillar": "identity-access", "severity": "must-fix", "tier": 0},
    "IAM-003": {"title": "RBAC scopes declared in Bicep (not subscription-wide)", "pillar": "identity-access", "severity": "must-fix", "tier": 0},
    "IAM-004": {"title": "No long-lived SAS tokens in code", "pillar": "identity-access", "severity": "should-fix", "tier": 0},
    "IAM-005": {"title": "ACA / Functions auth enabled", "pillar": "identity-access", "severity": "should-fix", "tier": 0},
    "IAM-101": {"title": "Role assignments observed in-target match Bicep", "pillar": "identity-access", "severity": "must-fix", "tier": 1},
    "IAM-102": {"title": "No Owner/Contributor on workload identity", "pillar": "identity-access", "severity": "must-fix", "tier": 1},
    "IAM-103": {"title": "Conditional access / Entra policies considered", "pillar": "identity-access", "severity": "should-fix", "tier": 1, "experimental": True},

    # ---- secrets
    "SEC-001": {"title": "Key Vault declared in infra", "pillar": "secrets", "severity": "must-fix", "tier": 0},
    "SEC-002": {"title": "No literal secrets in repo (regex sweep)", "pillar": "secrets", "severity": "must-fix", "tier": 0},
    "SEC-003": {"title": "appsettings reference KV references, not raw values", "pillar": "secrets", "severity": "must-fix", "tier": 0},
    "SEC-004": {"title": "Secret rotation policy documented in SPEC", "pillar": "secrets", "severity": "should-fix", "tier": 0},
    "SEC-005": {"title": "Bicep declares soft-delete + purge protection", "pillar": "secrets", "severity": "must-fix", "tier": 0},
    "SEC-006": {"title": "RBAC (not access policies) on Key Vault", "pillar": "secrets", "severity": "should-fix", "tier": 0},
    "SEC-007": {"title": "No secrets in azd env files committed", "pillar": "secrets", "severity": "must-fix", "tier": 0},
    "SEC-101": {"title": "Live KV exists with soft-delete enabled", "pillar": "secrets", "severity": "must-fix", "tier": 4},
    "SEC-102": {"title": "Live KV has purge protection enabled", "pillar": "secrets", "severity": "must-fix", "tier": 4},
    "SEC-103": {"title": "Live KV publicNetworkAccess disabled", "pillar": "secrets", "severity": "must-fix", "tier": 4},
    "SEC-104": {"title": "Live KV has firewall / VNet rules", "pillar": "secrets", "severity": "should-fix", "tier": 4},
    "SEC-105": {"title": "Live KV access via RBAC (not policies)", "pillar": "secrets", "severity": "should-fix", "tier": 4},
    "SEC-106": {"title": "Live KV diagnostic settings to LA workspace", "pillar": "secrets", "severity": "should-fix", "tier": 4},

    # ---- observability
    "OBS-001": {"title": "App Insights declared in infra", "pillar": "observability", "severity": "must-fix", "tier": 0},
    "OBS-002": {"title": "Log Analytics workspace declared", "pillar": "observability", "severity": "must-fix", "tier": 0},
    "OBS-003": {"title": "OTel SDK wired in src/", "pillar": "observability", "severity": "must-fix", "tier": 0},
    "OBS-004": {"title": "Foundry observability emit configured", "pillar": "observability", "severity": "should-fix", "tier": 0},
    "OBS-005": {"title": "Workbook scaffold present", "pillar": "observability", "severity": "should-fix", "tier": 0},
    "OBS-101": {"title": "App Insights resource exists in target RG", "pillar": "observability", "severity": "must-fix", "tier": 1},
    "OBS-102": {"title": "Traces ingested in last 24h", "pillar": "observability", "severity": "must-fix", "tier": 2},
    "OBS-103": {"title": "Exceptions table populated in last 24h", "pillar": "observability", "severity": "should-fix", "tier": 2, "experimental": True},
    "OBS-104": {"title": "Alert rules wired in target RG", "pillar": "observability", "severity": "must-fix", "tier": 1},
    "OBS-105": {"title": "Action group with notification channel", "pillar": "observability", "severity": "must-fix", "tier": 1},
    "OBS-106": {"title": "Diagnostic settings on Foundry account -> LA", "pillar": "observability", "severity": "must-fix", "tier": 1},

    # ---- continuous-evals
    "EVAL-001": {"title": "SPEC sec 9 declares eval scenarios", "pillar": "continuous-evals", "severity": "must-fix", "tier": 0},
    "EVAL-002": {"title": "evals/ folder with foundry-evals run files", "pillar": "continuous-evals", "severity": "must-fix", "tier": 0},
    "EVAL-003": {"title": "Eval scheduling plan documented (A or B)", "pillar": "continuous-evals", "severity": "must-fix", "tier": 0},
    "EVAL-004": {"title": "Threshold values match SPEC sec 9", "pillar": "continuous-evals", "severity": "should-fix", "tier": 0},
    "EVAL-005": {"title": "Grader strategy named in SPEC", "pillar": "continuous-evals", "severity": "should-fix", "tier": 0},
    "EVAL-006": {"title": "Dataset versioning documented", "pillar": "continuous-evals", "severity": "should-fix", "tier": 0},
    "EVAL-101": {"title": "Latest eval run results retrievable", "pillar": "continuous-evals", "severity": "must-fix", "tier": 2},
    "EVAL-102": {"title": "Latest eval run meets SPEC thresholds", "pillar": "continuous-evals", "severity": "must-fix", "tier": 2},
    "EVAL-103": {"title": "Eval failure alert exists in target RG", "pillar": "continuous-evals", "severity": "should-fix", "tier": 2, "experimental": True},
    "EVAL-104": {"title": "Eval cadence schedule resource exists", "pillar": "continuous-evals", "severity": "should-fix", "tier": 1, "experimental": True},
    "EVAL-105": {"title": "Eval drift trend reviewed in last 30d", "pillar": "continuous-evals", "severity": "should-fix", "tier": 2, "experimental": True},

    # ---- responsible-ai
    "RAI-001": {"title": "Content filters declared on model deployments", "pillar": "responsible-ai", "severity": "must-fix", "tier": 0},
    "RAI-002": {"title": "AGT RAI policy section present", "pillar": "responsible-ai", "severity": "must-fix", "tier": 0},
    "RAI-003": {"title": "Prompt shields enabled in policy", "pillar": "responsible-ai", "severity": "must-fix", "tier": 0},
    "RAI-004": {"title": "PII redaction strategy documented", "pillar": "responsible-ai", "severity": "should-fix", "tier": 0},
    "RAI-005": {"title": "Groundedness check planned for RAG", "pillar": "responsible-ai", "severity": "should-fix", "tier": 0},
    "RAI-006": {"title": "RAI incident escalation owner named", "pillar": "responsible-ai", "severity": "should-fix", "tier": 0},
    "RAI-101": {"title": "Content filter resource present in target", "pillar": "responsible-ai", "severity": "must-fix", "tier": 1},
    "RAI-102": {"title": "AGT RAI denials observable in last 24h", "pillar": "responsible-ai", "severity": "should-fix", "tier": 2, "experimental": True},

    # ---- hitl-audit
    "HITL-001": {"title": "SPEC sec 8 declares HITL gates if user-facing", "pillar": "hitl-audit", "severity": "should-fix", "tier": 0},
    "HITL-002": {"title": "HITL gate implementations referenced in src/", "pillar": "hitl-audit", "severity": "must-fix", "tier": 0},
    "HITL-003": {"title": "Audit trail destination configured", "pillar": "hitl-audit", "severity": "must-fix", "tier": 0},
    "HITL-004": {"title": "Escalation channel reachable (Teams/email/webhook)", "pillar": "hitl-audit", "severity": "should-fix", "tier": 0},
    "HITL-005": {"title": "HITL decision SLA documented", "pillar": "hitl-audit", "severity": "should-fix", "tier": 0},
    "HITL-101": {"title": "Audit storage account / table exists", "pillar": "hitl-audit", "severity": "must-fix", "tier": 1},
    "HITL-102": {"title": "Audit storage has immutability policy", "pillar": "hitl-audit", "severity": "should-fix", "tier": 1},
    "HITL-103": {"title": "HITL audit rows in last 7d (if expected)", "pillar": "hitl-audit", "severity": "should-fix", "tier": 2, "experimental": True},

    # ---- supply-chain
    "SUP-001": {"title": "Container images pinned by digest", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-002": {"title": "Bicep modules pinned (no `latest`)", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-003": {"title": "Dependency manifest committed (lock file)", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-004": {"title": "SBOM generation step declared", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-005": {"title": "Vulnerability scan step declared", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-006": {"title": "ACR scoped to private network", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-007": {"title": "Provenance / attestation considered", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-101": {"title": "SUPPORT.md present at repo root", "pillar": "supply-chain", "severity": "must-fix", "tier": 1},
    "SUP-102": {"title": "ACR has public access disabled", "pillar": "supply-chain", "severity": "should-fix", "tier": 1},
    "SUP-103": {"title": "ACR has Microsoft Defender enabled", "pillar": "supply-chain", "severity": "should-fix", "tier": 1, "experimental": True},

    # ---- cost
    "COST-001": {"title": "SPEC sec 10 declares pricing plan (PAYG vs PTU)", "pillar": "cost", "severity": "must-fix", "tier": 0},
    "COST-002": {"title": "Budget thresholds declared", "pillar": "cost", "severity": "must-fix", "tier": 0},
    "COST-003": {"title": "Cost owner documented", "pillar": "cost", "severity": "should-fix", "tier": 0},
    "COST-004": {"title": "Idle scale-down configured for ACA / Functions", "pillar": "cost", "severity": "should-fix", "tier": 0},
    "COST-005": {"title": "Tagging strategy for cost allocation", "pillar": "cost", "severity": "should-fix", "tier": 0},
    "COST-101": {"title": "Live budget alert wired on target RG", "pillar": "cost", "severity": "must-fix", "tier": 3},
    "COST-102": {"title": "Live actuals vs forecast within 20%", "pillar": "cost", "severity": "should-fix", "tier": 3, "experimental": True},
    "COST-103": {"title": "PAYG vs PTU recommendation matches observed usage", "pillar": "cost", "severity": "should-fix", "tier": 3, "experimental": True},
    "COST-104": {"title": "No orphaned resources in target RG", "pillar": "cost", "severity": "should-fix", "tier": 3, "experimental": True},
    "COST-105": {"title": "Resource tags applied as per strategy", "pillar": "cost", "severity": "should-fix", "tier": 3},

    # ---- reliability
    "REL-001": {"title": "SPEC sec 12 declares RTO / RPO", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-002": {"title": "Multi-region plan documented if RTO < 4h", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-003": {"title": "Backup / restore runbook present", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-004": {"title": "Capacity host lifecycle understood", "pillar": "reliability", "severity": "should-fix", "tier": 0},
    "REL-005": {"title": "Failure modes catalogued in SPEC", "pillar": "reliability", "severity": "should-fix", "tier": 0},
    "REL-006": {"title": "Health probes configured for ACA / Functions", "pillar": "reliability", "severity": "should-fix", "tier": 0},
    "REL-101": {"title": "Zone redundancy enabled where supported", "pillar": "reliability", "severity": "should-fix", "tier": 1, "experimental": True},
    "REL-102": {"title": "Backup vault present if SPEC declares backups", "pillar": "reliability", "severity": "must-fix", "tier": 1},
    "REL-103": {"title": "ACA min-replica >= 1 in prod", "pillar": "reliability", "severity": "should-fix", "tier": 1},
    "REL-104": {"title": "Multi-region resources present if declared", "pillar": "reliability", "severity": "should-fix", "tier": 1, "experimental": True},
    "REL-105": {"title": "Capacity host status healthy", "pillar": "reliability", "severity": "should-fix", "tier": 1, "experimental": True},
    # ---- reliability — NEW v0.3.0: restore-drill freshness + RSV restore points
    "REL-007": {"title": "Restore drill artefact present and dated within 90 days", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-008": {"title": "Live Recovery Services Vault has restore points", "pillar": "reliability", "severity": "should-fix", "tier": 1},

    # ---- sre-handover
    "SRE-001": {"title": "SPEC sec 12 names incident owner / on-call", "pillar": "sre-handover", "severity": "must-fix", "tier": 0},
    "SRE-002": {"title": "Runbook present in docs/", "pillar": "sre-handover", "severity": "must-fix", "tier": 0},
    "SRE-003": {"title": "Azure SRE Agent integration considered", "pillar": "sre-handover", "severity": "should-fix", "tier": 0},
    "SRE-004": {"title": "Severity matrix documented", "pillar": "sre-handover", "severity": "should-fix", "tier": 0},
    "SRE-005": {"title": "Postmortem template referenced", "pillar": "sre-handover", "severity": "should-fix", "tier": 0},
    "SRE-101": {"title": "Action group routes to on-call rotation", "pillar": "sre-handover", "severity": "must-fix", "tier": 1},
    "SRE-102": {"title": "SRE Agent resource present if planned", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},
    "SRE-103": {"title": "SRE runbook present (docs/sre/runbook.md)", "pillar": "sre-handover", "severity": "must-fix", "tier": 1},
    "SRE-104": {"title": "Activity log alerts on RG present", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},
    # ---- sre-handover — NEW v0.3.0: Azure Policy compliance (GOV-201..203 + secure-score)
    "GOV-104": {"title": "Defender Secure Score above floor (default 60%)", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},
    "GOV-105": {"title": "Top 3 Defender recommendations enumerated", "pillar": "sre-handover", "severity": "informational", "tier": 1},
    "GOV-201": {"title": "Required Azure Policy assignments present", "pillar": "sre-handover", "severity": "must-fix", "tier": 1},
    "GOV-202": {"title": "No non-compliant resources for required policies", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},
    "GOV-203": {"title": "Sane-default initiatives assigned (ASB-v3 or equivalent)", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},

    # ---- model-lifecycle
    "MDL-001": {"title": "Model deployments pinned to specific version", "pillar": "model-lifecycle", "severity": "must-fix", "tier": 0},
    "MDL-002": {"title": "Deprecation plan referenced in SPEC", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-003": {"title": "Model upgrade canary process documented", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-004": {"title": "Capacity / quota considered for prod scale", "pillar": "model-lifecycle", "severity": "must-fix", "tier": 0},
    "MDL-005": {"title": "Fallback model strategy documented", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-006": {"title": "Rate limit handling in code", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-007": {"title": "Region-residency policy for models", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-008": {"title": "Knowledge index refresh cadence declared", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-101": {"title": "Live deployments use pinned version (not `latest`)", "pillar": "model-lifecycle", "severity": "must-fix", "tier": 1},
    "MDL-102": {"title": "Live deployments not in retiring / deprecated list", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 1},
    "MDL-103": {"title": "Live capacity matches plan capacity", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 1},
    "MDL-104": {"title": "Live rate-limit breaches in last 24h", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 2, "experimental": True},
    # ---- model-lifecycle — NEW v0.3.0: quota pre-flight + Foundry wiring + Defender for AI
    "MDL-009": {"title": "Project-level RBAC declared on Foundry account", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-010": {"title": "Knowledge index private-endpointed (if used)", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-011": {"title": "Agent thread retention/policy declared in SPEC", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 0},
    "MDL-110": {"title": "TPM headroom available for planned model load", "pillar": "model-lifecycle", "severity": "must-fix", "tier": 1},
    "MDL-111": {"title": "Foundry account capacity available in target region", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 1},
    "GOV-101": {"title": "Defender for AI Services plan enabled", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 1},

    # ---- secrets — NEW v0.3.0: Defender for Key Vault
    "GOV-102": {"title": "Defender for Key Vault plan enabled", "pillar": "secrets", "severity": "should-fix", "tier": 1},

    # ---- supply-chain — NEW v0.3.0: Defender for Servers / Containers
    "GOV-103": {"title": "Defender for Servers/Containers plan enabled", "pillar": "supply-chain", "severity": "should-fix", "tier": 1},
}

WAIVER_SCHEMA_FIELDS = ("owner", "expiry", "justification", "compensating_control", "accepted_risk")
WAIVER_BINDING_FIELDS = ("subscription_id", "resource_group", "deployment_manifest_sha256", "target_posture")
WAIVER_MIN_TEXT_LEN = 20
WAIVER_ID_RE = re.compile(r"^W-\d{3,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_FRESHNESS_HOURS = 24


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    id: str
    title: str
    pillar: str
    severity: str           # must-fix | should-fix | informational
    status: str             # pass | should-fix | must-fix | not-applicable | not-verified | waived
    tier: int
    detail: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    waiver_id: str | None = None


@dataclass
class EvidenceEntry:
    ref: str                # e.g. "E-01"
    pillar: str
    description: str
    command: str = ""
    scope: str = ""
    tier: int = 0
    captured_at: str = ""
    result: str = ""        # "ok" | "missing" | "error"
    notes: str = ""


@dataclass
class PillarResult:
    pillar: str
    title: str
    status: str             # green | amber | red | not-applicable
    findings: list[Finding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers (mirror safe_check.py conventions)
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def _az(*args: str, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run `az <args>` and return CompletedProcess.

    Mirrors safe_check._az exactly: shell=True so Windows resolves az.cmd, and
    SystemExit(3) on FileNotFoundError or CalledProcessError unless check=False.
    """
    cmd = "az " + " ".join(args)
    try:
        return subprocess.run(
            cmd,
            shell=True,
            capture_output=capture,
            text=True,
            check=check,
        )
    except FileNotFoundError:
        _eprint("error: `az` CLI not found on PATH")
        raise SystemExit(3)
    except subprocess.CalledProcessError as exc:
        if check:
            _eprint(f"error: `az` failed: {cmd}")
            if exc.stderr:
                _eprint(exc.stderr.strip())
            raise SystemExit(3)
        return exc  # type: ignore[return-value]


def _az_json(*args: str) -> Any | None:
    """Run az with -o json, return parsed json or None on any failure."""
    proc = _az(*args, "-o", "json", check=False)
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _print_active_context() -> None:
    try:
        proc = _az("account", "show", "--query", "\"{t:tenantId,s:name,sid:id}\"", "-o", "json", check=False)
    except SystemExit:
        return
    if proc.returncode != 0:
        return
    try:
        ctx = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return
    print(f"[ctx] tenant={ctx.get('t','?')} sub={ctx.get('s','?')} ({ctx.get('sid','?')})")


def _sha256_block(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _read_json(path: Path) -> Any | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _glob_repo(root: Path, *patterns: str) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(root.rglob(pat))
    return [
        p for p in out
        if ".git" not in p.parts
        and "node_modules" not in p.parts
        and p.name not in EXCLUDE_GLOBS
    ]


# ---------------------------------------------------------------------------
# Pre-flight: validate safe-check manifest
# ---------------------------------------------------------------------------


def _load_postdeploy(path: Path, accept_stale: bool, freshness_hours: int) -> tuple[dict, list[str]]:
    """Load tests/postdeploy-manifest.json and pre-flight it.

    Returns (postdeploy_dict, warnings). Exits 2 on hard failure unless
    accept_stale and the failure is freshness-only.
    """
    warnings: list[str] = []
    data = _read_json(path)
    if not isinstance(data, dict):
        _eprint(f"error: missing or invalid {path}")
        _eprint("run `python tests/safe_check.py --phase post-deploy` first")
        raise SystemExit(2)

    phase = data.get("phase")
    if phase != "post-deploy":
        _eprint(f"error: {path} phase={phase!r}, expected 'post-deploy'")
        raise SystemExit(2)

    checked_at = data.get("checked_at")
    if isinstance(checked_at, str):
        try:
            t = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - t
            if age > timedelta(hours=freshness_hours):
                msg = f"safe-check manifest is {age.total_seconds()/3600:.1f}h old (limit {freshness_hours}h)"
                if accept_stale:
                    warnings.append(msg + " — accepted via --accept-stale-safe-check")
                else:
                    _eprint(f"error: {msg}")
                    _eprint("re-run safe-check or pass --accept-stale-safe-check")
                    raise SystemExit(2)
        except ValueError:
            warnings.append(f"safe-check checked_at not ISO-8601: {checked_at!r}")
    else:
        warnings.append("safe-check checked_at missing")

    return data, warnings


def _load_manifest(path: Path) -> dict:
    data = _read_json(path)
    if not isinstance(data, dict):
        _eprint(f"error: missing or invalid {path}")
        raise SystemExit(2)
    dm = data.get("deployment_manifest")
    if not isinstance(dm, dict):
        _eprint(f"error: {path} missing 'deployment_manifest' block")
        raise SystemExit(2)
    return data


def _validate_manifest_binding(
    manifest: dict,
    postdeploy: dict,
    accept_stale: bool,
) -> list[str]:
    """Cross-check sub/RG/hash between current manifest and safe-check manifest.

    Returns warnings. Hard-exits 2 on mismatch unless accept_stale.
    """
    warnings: list[str] = []
    dm = manifest.get("deployment_manifest", {})
    pdm = postdeploy.get("deployment_manifest") or {}
    if not pdm:
        warnings.append("safe-check manifest has no embedded deployment_manifest snapshot")
        return warnings

    for key in ("subscription_id", "resource_group"):
        a = dm.get(key)
        b = pdm.get(key)
        if a and b and a != b:
            msg = f"deployment_manifest.{key} mismatch: current={a!r} safe-check={b!r}"
            if accept_stale:
                warnings.append(msg + " — accepted via --accept-stale-safe-check")
            else:
                _eprint("error: " + msg)
                _eprint("re-run safe-check or pass --accept-stale-safe-check")
                raise SystemExit(2)

    h_now = _sha256_block(dm)
    h_then = _sha256_block(pdm)
    if h_now != h_then:
        msg = f"deployment_manifest hash changed since safe-check (now={h_now[:12]} then={h_then[:12]})"
        if accept_stale:
            warnings.append(msg + " — accepted via --accept-stale-safe-check")
        else:
            _eprint("error: " + msg)
            _eprint("re-run safe-check or pass --accept-stale-safe-check")
            raise SystemExit(2)

    return warnings


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------


def _load_waivers(path: Path | None) -> tuple[dict[str, dict], dict, list[str]]:
    """Load and validate waivers file.

    Returns ({finding_id: waiver_record}, binding_block, errors). Bad waivers
    are dropped with an error message but do not hard-fail the run. The
    binding block (optional, top-level) is returned verbatim for
    _validate_waiver_binding to vet against the current run.
    """
    if path is None or not path.exists():
        return {}, {}, []
    data = _read_json(path)
    if data is None:
        return {}, {}, [f"waivers: {path} is not valid JSON — ignoring file"]
    if not isinstance(data, dict) or not isinstance(data.get("waivers"), list):
        return {}, {}, [f"waivers: {path} must be an object with a 'waivers' array"]
    binding = data.get("binding") if isinstance(data.get("binding"), dict) else {}

    out: dict[str, dict] = {}
    errors: list[str] = []
    seen_ids: set[str] = set()
    for idx, w in enumerate(data["waivers"]):
        if not isinstance(w, dict):
            errors.append(f"waivers[{idx}]: not an object — skipped")
            continue
        wid = w.get("id")
        finding_id = w.get("finding_id")
        if not isinstance(wid, str) or not WAIVER_ID_RE.match(wid):
            errors.append(f"waivers[{idx}]: missing/invalid id (expect W-### pattern) — skipped")
            continue
        if wid in seen_ids:
            errors.append(f"waivers[{idx}]: duplicate id {wid} — skipped")
            continue
        if not isinstance(finding_id, str) or finding_id not in FINDING_CATALOG:
            errors.append(f"waivers[{idx}] {wid}: unknown finding_id {finding_id!r} — skipped")
            continue
        bad = False
        for fname in WAIVER_SCHEMA_FIELDS:
            val = w.get(fname)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"waivers[{idx}] {wid}: missing required field {fname!r} — skipped")
                bad = True
                break
            if fname in ("justification", "compensating_control", "accepted_risk") and len(val.strip()) < WAIVER_MIN_TEXT_LEN:
                errors.append(f"waivers[{idx}] {wid}: {fname!r} too short (min {WAIVER_MIN_TEXT_LEN} chars) — skipped")
                bad = True
                break
            if fname == "owner" and not EMAIL_RE.match(val.strip()):
                errors.append(f"waivers[{idx}] {wid}: owner {val!r} is not an email — skipped")
                bad = True
                break
            if fname == "expiry":
                try:
                    exp = datetime.strptime(val.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    errors.append(f"waivers[{idx}] {wid}: expiry must be YYYY-MM-DD — skipped")
                    bad = True
                    break
                if exp < datetime.now(timezone.utc):
                    errors.append(f"waivers[{idx}] {wid}: expired on {val} — skipped")
                    bad = True
                    break
        if bad:
            continue
        seen_ids.add(wid)
        out[finding_id] = w
    return out, binding, errors


def _load_customer_overrides(path):
    """Load customer-overrides YAML with a minimal stdlib parser.

    Supports the limited shape:
        customer: <str>
        overrides:
          - recipe_id: <str>
            status: pass|fail
            reason: <str>

    Returns None if path is None. Raises FileNotFoundError if path is missing.
    """
    if path is None:
        return None
    p = Path(path)
    text = p.read_text(encoding="utf-8")

    out = {"customer": None, "overrides": []}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and ":" in line and not line.startswith("-"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "customer":
                out["customer"] = val
            elif key == "overrides":
                pass
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current is not None:
                out["overrides"].append(current)
            current = {}
            stripped = stripped[2:]
        if current is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            current[key.strip()] = val.strip().strip('"').strip("'")
    if current is not None:
        out["overrides"].append(current)
    return out


def _validate_customer_overrides(ov):
    """Validate a customer-overrides payload. Raises ValueError on invalid shape."""
    if not isinstance(ov, dict):
        raise ValueError("customer-overrides must be a mapping")
    if not ov.get("customer"):
        raise ValueError("customer-overrides missing required 'customer' field")
    overrides = ov.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError("'overrides' must be a list")
    for i, item in enumerate(overrides):
        if not isinstance(item, dict):
            raise ValueError(f"overrides[{i}] must be a mapping")
        if not item.get("recipe_id"):
            raise ValueError(f"overrides[{i}] missing 'recipe_id'")
        status = item.get("status")
        if status not in ("pass", "fail"):
            raise ValueError(
                f"overrides[{i}].status must be 'pass' or 'fail', got {status!r}"
            )
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError(
                f"overrides[{i}] requires a non-empty 'reason' string"
            )


def _finding_lookup_id(finding) -> str | None:
    if isinstance(finding, dict):
        return finding.get("recipe_id") or finding.get("id")
    return getattr(finding, "recipe_id", None) or getattr(finding, "id", None)


def _finding_severity(finding) -> str | None:
    if isinstance(finding, dict):
        return finding.get("severity")
    return getattr(finding, "severity", None)


def _finding_status(finding) -> str | None:
    if isinstance(finding, dict):
        return finding.get("status")
    return getattr(finding, "status", None)


def _apply_customer_overrides(findings, ov):
    """Apply customer overrides to findings. Status-flips only.

    A finding with severity == 'must-fix' may never be overridden — attempting
    to do so calls sys.exit(2) with a loud error to fail the deploy gate.
    """
    if ov is None:
        return findings
    index = {item["recipe_id"]: item for item in ov.get("overrides", [])}
    out = []
    for f in findings:
        rid = _finding_lookup_id(f)
        if rid in index:
            target_status = index[rid]["status"]
            if _finding_severity(f) == "must-fix":
                msg = (
                    "FATAL: customer-override on must-fix finding rejected.\n"
                    f"  recipe_id: {rid}\n"
                    f"  current status: {_finding_status(f)}\n"
                    f"  attempted override: {target_status}\n"
                    f"  reason given: {index[rid].get('reason')!r}\n"
                    "Must-fix findings cannot be silenced by customer overrides. "
                    "Either remediate the finding, or work with the threadlight "
                    "maintainers to demote it from must-fix in the next release."
                )
                print(msg, file=sys.stderr)
                sys.exit(2)
            if isinstance(f, dict):
                new_f = dict(f)
                new_f["status"] = target_status
                new_f["override_reason"] = index[rid]["reason"]
                new_f["override_customer"] = ov["customer"]
            else:
                new_f = Finding(**asdict(f))
                new_f.status = target_status
                setattr(new_f, "override_reason", index[rid]["reason"])
                setattr(new_f, "override_customer", ov["customer"])
            out.append(new_f)
        else:
            out.append(f)
    return out


def _validate_waiver_binding(
    binding: dict,
    sub: str | None,
    rg: str | None,
    deployment_manifest_sha256: str | None,
    resolved_posture: str,
) -> tuple[bool, list[str]]:
    """Decide whether waivers from this file apply to the current run.

    The binding block is OPTIONAL for backward compat: if absent or empty,
    waivers still apply but a loud warning is emitted (file is "unbound" —
    risk of MVP-to-MVP waiver leakage when teams reuse repos).

    When binding IS present, every populated field must match the current
    run. Mismatch → waivers do NOT apply and a prominent warning surfaces
    the mismatch so the operator can verify they grabbed the right file.

    Returns (apply: bool, messages: list[str]).
    """
    if not binding:
        return True, [
            "WAIVER-UNBOUND: waivers file has no `binding` block — waivers are "
            "applied but cannot be vouched for. Risk: MVP-to-MVP waiver leakage "
            "in industrialized delivery. Add a `binding` block (subscription_id, "
            "resource_group, deployment_manifest_sha256, target_posture) so each "
            "waiver file is anchored to the customer + deployment it was approved "
            "for. See references/waivers-schema.json."
        ]
    msgs: list[str] = []
    mismatches: list[str] = []
    expected = {
        "subscription_id": sub or "",
        "resource_group": rg or "",
        "deployment_manifest_sha256": deployment_manifest_sha256 or "",
        "target_posture": resolved_posture or "",
    }
    for field in WAIVER_BINDING_FIELDS:
        decl = binding.get(field)
        if decl is None or decl == "":
            continue  # unset binding field is OK — only what's declared must match
        if not isinstance(decl, str):
            mismatches.append(f"{field}: declared value not a string")
            continue
        actual = expected.get(field, "")
        if decl.strip() != (actual or "").strip():
            mismatches.append(f"{field}: waivers declare `{decl}`, current run is `{actual or '(unset)'}`")
    if mismatches:
        detail = "; ".join(mismatches)
        msgs.append(
            "WAIVER-BINDING-MISMATCH: waivers file is bound to a different "
            f"deployment — NOT APPLYING any waivers. Mismatch: {detail}. "
            "Either remove waivers/, copy the correct waivers file for this "
            "customer+deployment, or update the binding block if the same "
            "approver intentionally re-scoped the waivers."
        )
        return False, msgs
    # All declared binding fields match
    msgs.append(
        f"WAIVER-BOUND: waivers applied against binding "
        f"(sub={binding.get('subscription_id', '(any)')}, "
        f"rg={binding.get('resource_group', '(any)')}, "
        f"posture={binding.get('target_posture', '(any)')}, "
        f"sha256={(binding.get('deployment_manifest_sha256') or '(any)')[:12]}…)"
    )
    return True, msgs


# ---------------------------------------------------------------------------
# Posture resolver
# ---------------------------------------------------------------------------

POSTURE_CITADEL = "citadel-spoke"
POSTURE_AGT = "agt"
POSTURE_STANDARD = "standard-ai-gateway"
POSTURE_HYBRID = "hybrid"
POSTURE_DEFAULT = POSTURE_STANDARD

KNOWN_POSTURES = {POSTURE_CITADEL, POSTURE_AGT, POSTURE_STANDARD, POSTURE_HYBRID}

CITADEL_HINT_RE = re.compile(r"citadel|access[- ]contract|ai[- ]hub[- ]gateway", re.I)
AGT_HINT_RE = re.compile(r"\bagt\b|foundry[-_ ]agt|agent[-_ ]governance", re.I)
GATEWAY_HINT_RE = re.compile(r"\bapim\b|api[- ]management|ai[- ]gateway", re.I)


def _scan_spec_section_12(spec_text: str) -> dict[str, str | None]:
    """Extract target_posture and a few common fields from SPEC sec 12 prose.

    Tolerant: this is markdown, not yaml. We accept either lines like
    `target_posture: citadel-spoke` or `**Target posture:** citadel-spoke`.
    Returns dict with possibly-None values.
    """
    out: dict[str, str | None] = {
        "target_posture": None,
        "rto": None,
        "rpo": None,
        "sla": None,
        "incident_owner": None,
        "residency": None,
    }
    # Slice section 12 only. If §12 is missing, return all-None so the
    # caller's "no §12 declared" detection works correctly (whole-doc
    # fallback would pick up stray keys from §11b / appendices).
    m = re.search(r"##\s*12\.?\s+Production\s+Readiness(.+?)(?=\n##\s+\d|\Z)", spec_text, re.I | re.S)
    if not m:
        return out
    section = m.group(1)
    patterns = {
        "target_posture": r"(?:target[ _-]posture|target_posture)\s*[:=]\s*\*?\*?([a-z0-9_\-]+)",
        "rto": r"\brto\b\s*[:=]\s*\*?\*?([^\n*]+)",
        "rpo": r"\brpo\b\s*[:=]\s*\*?\*?([^\n*]+)",
        "sla": r"\bsla\b\s*[:=]\s*\*?\*?([^\n*]+)",
        "incident_owner": r"incident[ _-]owner\s*[:=]\s*\*?\*?([^\n*]+)",
        "residency": r"residency\s*[:=]\s*\*?\*?([^\n*]+)",
    }
    for k, pat in patterns.items():
        mm = re.search(pat, section, re.I)
        if mm:
            out[k] = mm.group(1).strip().strip("*").strip()
    return out


def _scan_spec_section_11b(spec_text: str) -> dict[str, str]:
    # Match either "## 11b. AI Governance Hub Posture" or "### 11b. ..." or plain "## 11b."
    m = re.search(r"#{2,3}\s*11b\.?[^\n]*\n(.+?)(?=\n#{2,3}\s+\d|\Z)", spec_text, re.I | re.S)
    section = m.group(1) if m else ""
    out: dict[str, str] = {}
    # tolerate either:
    #   yaml-style: governance_hub:\n  required: yes
    #   prose-style: **Governance hub spoke required**: `yes`
    # we accept either separator (space/underscore/hyphen) between "governance" and "hub".
    mm = re.search(
        r"governance[\s_-]?hub[\s\S]{0,200}?required\W{0,5}[:=]\s*[`*\"]?(yes|no|true|false)",
        section,
        re.I,
    )
    if mm:
        out["governance_hub_required"] = mm.group(1).lower() in ("yes", "true")
    return out


def _resolve_posture(
    cli_target: str | None,
    spec_12: dict[str, str | None],
    spec_11b: dict[str, str],
    evidence_apim_present: bool | None,
) -> tuple[str, str, str]:
    """Return (declared, detected, resolved).

    Priority:
      1. CLI --target
      2. SPEC sec 12 target_posture
      3. SPEC sec 11b governance_hub.required == yes  -> citadel-spoke
      4. Evidence (APIM Foundry connection present)   -> citadel-spoke
      5. Default                                      -> standard-ai-gateway
    """
    declared = spec_12.get("target_posture") or ""
    if declared and declared not in KNOWN_POSTURES:
        declared_clean = ""
    else:
        declared_clean = declared

    detected = ""
    if spec_11b.get("governance_hub_required") is True:
        detected = POSTURE_CITADEL
    elif evidence_apim_present is True:
        detected = POSTURE_CITADEL

    if cli_target:
        return declared_clean, detected, cli_target
    if declared_clean:
        return declared_clean, detected, declared_clean
    if detected:
        return declared_clean, detected, detected
    return declared_clean, detected, POSTURE_DEFAULT


# ---------------------------------------------------------------------------
# Permission tier prober
# ---------------------------------------------------------------------------


def _probe_tiers(subscription_id: str | None, resource_group: str | None, az_available: bool) -> dict[int, bool]:
    """Cheaply probe whether each permission tier is usable. Returns {tier: granted}."""
    tiers = {0: True, 1: False, 2: False, 3: False, 4: False, 5: False}
    if not az_available:
        return tiers
    # Tier 1: Reader on RG -> list resources
    if resource_group and subscription_id:
        proc = _az("resource", "list", "--resource-group", resource_group, "--subscription", subscription_id, "--query", "[].id", check=False)
        tiers[1] = proc.returncode == 0
    elif subscription_id:
        proc = _az("group", "list", "--subscription", subscription_id, "--query", "[].id", check=False)
        tiers[1] = proc.returncode == 0
    # Tier 2: Monitoring Reader: list activity log alerts in sub
    if subscription_id:
        proc = _az("monitor", "activity-log", "alert", "list", "--subscription", subscription_id, "-o", "json", check=False)
        tiers[2] = proc.returncode == 0
    # Tier 3: Cost Management Reader (best-effort - probe via consumption budget list)
    if subscription_id and resource_group:
        proc = _az("consumption", "budget", "list", "--resource-group", resource_group, "--subscription", subscription_id, "-o", "json", check=False)
        tiers[3] = proc.returncode == 0
    # Tier 4: KV control plane Reader: list vaults
    if subscription_id and resource_group:
        proc = _az("keyvault", "list", "--resource-group", resource_group, "--subscription", subscription_id, "-o", "json", check=False)
        tiers[4] = proc.returncode == 0
    # Tier 5: APIM Reader (we don't know the hub RG here; mark via separate hub probe)
    tiers[5] = False  # only set true if hub apim probe succeeds later
    return tiers


# ---------------------------------------------------------------------------
# Finding builders
# ---------------------------------------------------------------------------


def _mk_finding(
    fid: str,
    status: str,
    detail: str = "",
    evidence_refs: list[str] | None = None,
) -> Finding:
    catalog = FINDING_CATALOG[fid]
    return Finding(
        id=fid,
        title=catalog["title"],
        pillar=catalog["pillar"],
        severity=catalog["severity"],
        status=status,
        tier=catalog["tier"],
        detail=detail,
        evidence_refs=evidence_refs or [],
    )


def _not_verified(fid: str, reason: str) -> Finding:
    return _mk_finding(fid, status="not-verified", detail=reason)


def _all_pillar_findings_not_verified(pillar: str, reason: str) -> list[Finding]:
    return [
        _not_verified(fid, reason)
        for fid, meta in FINDING_CATALOG.items()
        if meta["pillar"] == pillar
    ]


# ---------------------------------------------------------------------------
# Bicep ARM-graph parser (v0.3.0 — closes the smoking gun)
# ---------------------------------------------------------------------------
#
# Before v0.3.0 the production-ready skill answered most "does this Bicep
# declare X?" questions with `re.search()` over concatenated raw Bicep text.
# That was wrong: a comment line `// virtualNetworks should be used` was
# enough to make NET-001 pass. v0.3.0 routes those questions through
# `BicepGraph` instead.
#
# BicepGraph shells out to `az bicep build --file <main.bicep> --stdout` to
# compile each top-level Bicep file to ARM JSON, then recursively walks any
# `Microsoft.Resources/deployments` nodes (these are nested templates emitted
# by `module foo 'foo.bicep'` references) so module-defined resources are
# flattened into the same lookup table.
#
# The `bicep` CLI is a HARD dependency in v0.3.0. If `az bicep build` is
# missing or the compile fails, `BicepGraph.from_repo()` raises
# `PrerequisiteError` and `main()` exits with code 2 telling the operator to
# install the CLI with `az bicep install`. There is no silent fallback to the
# old regex-over-text approach — that was the bug.


class PrerequisiteError(RuntimeError):
    """Raised when a hard external prerequisite is missing.

    `main()` catches this, prints a remediation hint to stderr, and exits 2.
    """


class BicepGraph:
    """Compile-once ARM-graph view over a repo's Bicep files.

    Use `by_type("Microsoft.Network/virtualNetworks")` to list all resources
    of a given ARM type that are *actually declared* (resolved through
    modules) — not just mentioned in a comment.

    Attributes
    ----------
    resources : list[dict]
        Flat list of ARM resources (modules expanded). Each entry has at
        least `type`, `apiVersion`, `name`, `properties`.
    source_files : list[Path]
        Top-level main.bicep files that were compiled.
    """

    def __init__(self, resources: list[dict], source_files: list[Path]) -> None:
        self.resources = resources
        self.source_files = source_files
        self._by_type: dict[str, list[dict]] = {}
        for r in resources:
            t = (r.get("type") or "").lower()
            if t:
                self._by_type.setdefault(t, []).append(r)

    @classmethod
    def from_repo(cls, root: Path) -> "BicepGraph":
        # Pick top-level main.bicep files. Prefer the ones a typical
        # azd-style layout uses: infra/main.bicep, then any *.bicep at root
        # depth ≤ 2. We deliberately don't try to compile every module
        # individually — `az bicep build` on a main.bicep expands its
        # `module` references for us.
        candidates = sorted(
            list(_glob_repo(root, "infra/main.bicep"))
            + list(_glob_repo(root, "main.bicep"))
            + list(_glob_repo(root, "infra/**/main.bicep"))
        )
        # De-dup while preserving order.
        seen: set[Path] = set()
        mains: list[Path] = []
        for p in candidates:
            if p not in seen:
                seen.add(p)
                mains.append(p)
        if not mains:
            # No main.bicep at all — return an empty graph. Callers will see
            # 0 resources of every type and the existing checks will fail
            # with "no X declared", which is the correct outcome.
            return cls([], [])
        all_resources: list[dict] = []
        first_error: str | None = None
        for main in mains:
            try:
                cp = subprocess.run(
                    ["az", "bicep", "build", "--file", str(main), "--stdout"],
                    capture_output=True, text=True, check=False, timeout=120,
                )
            except FileNotFoundError as e:
                raise PrerequisiteError(
                    "Azure CLI (`az`) not found on PATH — required to compile Bicep. "
                    "Install Azure CLI from https://learn.microsoft.com/cli/azure/install-azure-cli "
                    "and re-run."
                ) from e
            if cp.returncode != 0:
                stderr = (cp.stderr or "").lower()
                if "bicep cli not found" in stderr or "az bicep install" in stderr:
                    raise PrerequisiteError(
                        "Bicep CLI not installed. Run `az bicep install` and re-run "
                        "the production-readiness skill. v0.3.0 has a hard "
                        "dependency on the Bicep CLI — there is no regex fallback."
                    )
                # Compile error in the user's Bicep. Surface the first one
                # and keep going — we want to give them as much signal as
                # we can, but compile errors will cause empty resource
                # lists which will turn into must-fix findings downstream.
                if first_error is None:
                    first_error = f"{main}: {cp.stderr.strip()[:400]}"
                continue
            try:
                arm = json.loads(cp.stdout or "{}")
            except json.JSONDecodeError:
                continue
            all_resources.extend(cls._walk(arm.get("resources") or []))
        if first_error and not all_resources:
            # All mains failed to compile and we have no resources at all.
            # That's a degenerate state — surface it so the operator knows
            # they have a broken Bicep build, not just a missing resource.
            raise PrerequisiteError(
                f"`az bicep build` failed on every top-level main.bicep. "
                f"Fix the Bicep build before running the production-readiness skill. "
                f"First error: {first_error}"
            )
        return cls(all_resources, mains)

    @staticmethod
    def _walk(resources: list[dict]) -> list[dict]:
        out: list[dict] = []
        for r in resources:
            rtype = (r.get("type") or "").lower()
            if rtype == "microsoft.resources/deployments":
                # Nested template — recurse into properties.template.resources
                nested = (((r.get("properties") or {}).get("template") or {}).get("resources") or [])
                out.extend(BicepGraph._walk(nested))
            else:
                out.append(r)
                # Some resources nest children inline.
                kids = r.get("resources") or []
                if isinstance(kids, list):
                    out.extend(BicepGraph._walk(kids))
        return out

    def by_type(self, arm_type: str) -> list[dict]:
        return list(self._by_type.get(arm_type.lower(), []))

    def has_type(self, arm_type: str) -> bool:
        return bool(self._by_type.get(arm_type.lower()))

    def count(self, arm_type: str) -> int:
        return len(self._by_type.get(arm_type.lower(), []))

    def property_values(self, arm_type: str, dotted_path: str) -> list[Any]:
        """Return a flat list of values found at `dotted_path` across every
        resource of `arm_type`. Missing keys are skipped, not coerced to None.
        """
        out: list[Any] = []
        for r in self.by_type(arm_type):
            v: Any = r
            ok = True
            for part in dotted_path.split("."):
                if isinstance(v, dict) and part in v:
                    v = v[part]
                else:
                    ok = False
                    break
            if ok:
                out.append(v)
        return out


# ---------------------------------------------------------------------------
# Static repo analysis (pure file scanning - no Azure calls)
# ---------------------------------------------------------------------------


@dataclass
class RepoContext:
    root: Path
    bicep_files: list[Path]
    src_files: list[Path]
    test_files: list[Path]
    spec_text: str
    spec_12: dict[str, str | None]
    spec_11b: dict[str, Any]
    azure_yaml_text: str
    docs_text: str
    azd_env: dict[str, str]
    manifest: dict
    bicep_text: str = ""
    src_text: str = ""
    bicep_graph: BicepGraph | None = None
    resolved_posture: str = ""

    @classmethod
    def from_repo(cls, root: Path, manifest: dict) -> "RepoContext":
        bicep = _glob_repo(root, "**/*.bicep")
        src = _glob_repo(root, "src/**/*.py", "src/**/*.ts", "src/**/*.js", "src/**/*.cs", "src/**/Dockerfile", "src/**/*.dockerfile")
        tests = _glob_repo(root, "tests/**/*.py", "tests/**/*.json", "evals/**/*")
        spec_text = _read_text(root / "specs" / "SPEC.md") or ""
        azure_yaml = _read_text(root / "azure.yaml") or ""
        docs_text = "\n\n".join(
            (_read_text(p) or "") for p in _glob_repo(root, "docs/**/*.md", "README.md")
        )
        bicep_text = "\n".join((_read_text(p) or "") for p in bicep)
        src_text = "\n".join((_read_text(p) or "") for p in src)
        env_path = root / ".azure"
        azd_env: dict[str, str] = {}
        if env_path.exists():
            for envfile in env_path.rglob(".env"):
                for line in (_read_text(envfile) or "").splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        azd_env[k.strip()] = v.strip().strip('"')
        # BicepGraph: hard prerequisite in v0.3.0. May raise PrerequisiteError
        # which main() catches and surfaces with an install hint + exit 2.
        bicep_graph = BicepGraph.from_repo(root) if bicep else BicepGraph([], [])
        return cls(
            root=root,
            bicep_files=bicep,
            src_files=src,
            test_files=tests,
            spec_text=spec_text,
            spec_12=_scan_spec_section_12(spec_text),
            spec_11b=_scan_spec_section_11b(spec_text),
            azure_yaml_text=azure_yaml,
            docs_text=docs_text,
            azd_env=azd_env,
            manifest=manifest,
            bicep_text=bicep_text,
            src_text=src_text,
            bicep_graph=bicep_graph,
        )


# ---- pillar 1: network-posture ---------------------------------------------

def _check_network_static(ctx: RepoContext, resolved_posture: str) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    g = ctx.bicep_graph
    # NET-001: infra DECLARES a virtual network (graph-verified — comments don't count)
    vnets = g.by_type("Microsoft.Network/virtualNetworks") if g else []
    has_network = bool(vnets)
    out.append(_mk_finding("NET-001",
        status="pass" if has_network else "must-fix",
        detail=f"Bicep declares {len(vnets)} virtualNetworks resource(s)" if has_network
               else "No Microsoft.Network/virtualNetworks resource declared in compiled ARM (comments don't count)"))
    # NET-002: PE for Foundry account (graph-verified)
    pes = g.by_type("Microsoft.Network/privateEndpoints") if g else []
    has_pe = bool(pes)
    out.append(_mk_finding("NET-002",
        status="pass" if has_pe else "must-fix",
        detail=f"{len(pes)} privateEndpoints declared in compiled ARM" if has_pe
               else "No Microsoft.Network/privateEndpoints resource declared in compiled ARM"))
    # NET-003: publicNetworkAccess disabled on Foundry / AI accounts (graph-verified)
    cs_accounts = (g.by_type("Microsoft.CognitiveServices/accounts") if g else []) + \
                  (g.by_type("Microsoft.MachineLearningServices/workspaces") if g else [])
    pna_enabled_accts = [a for a in cs_accounts if (a.get("properties") or {}).get("publicNetworkAccess") == "Enabled"]
    pna_disabled_accts = [a for a in cs_accounts if (a.get("properties") or {}).get("publicNetworkAccess") == "Disabled"]
    if pna_enabled_accts:
        st, d = "must-fix", f"{len(pna_enabled_accts)}/{len(cs_accounts)} AI/Foundry accounts have publicNetworkAccess=Enabled — prod must be Disabled"
    elif pna_disabled_accts and len(pna_disabled_accts) == len(cs_accounts):
        st, d = "pass", f"All {len(cs_accounts)} AI/Foundry accounts have publicNetworkAccess=Disabled"
    elif cs_accounts:
        st, d = "should-fix", f"publicNetworkAccess not explicitly set on {len(cs_accounts) - len(pna_disabled_accts)}/{len(cs_accounts)} AI/Foundry accounts"
    elif resolved_posture in (POSTURE_CITADEL, POSTURE_STANDARD, POSTURE_AGT, POSTURE_HYBRID):
        # Posture demands an AI surface but none declared — escalate.
        st, d = "must-fix", f"Resolved posture is {resolved_posture} but no Microsoft.CognitiveServices/accounts or MachineLearningServices/workspaces declared in compiled ARM"
    else:
        st, d = "should-fix", "No AI/Foundry accounts declared in compiled ARM — cannot check publicNetworkAccess"
    out.append(_mk_finding("NET-003", status=st, detail=d))
    # NET-004: subnet delegation present (graph-verified — looks at subnet
    # `delegations` property on declared VNets, or presence of an ACA env /
    # serverFarms which require delegated subnets)
    has_delegation = False
    detail_extra = ""
    if g:
        for v in vnets:
            for sub in (v.get("properties") or {}).get("subnets") or []:
                if (sub.get("properties") or {}).get("delegations"):
                    has_delegation = True
                    detail_extra = f"subnet `{sub.get('name')}` has delegations"
                    break
            if has_delegation:
                break
        if not has_delegation and (g.has_type("Microsoft.App/managedEnvironments")
                                   or g.has_type("Microsoft.Web/serverfarms")):
            has_delegation = True
            detail_extra = "ACA managed env / App Service plan present (implies delegated subnet)"
    out.append(_mk_finding("NET-004",
        status="pass" if has_delegation else "should-fix",
        detail=f"Subnet delegation found: {detail_extra}" if has_delegation
               else "No subnet delegation found on any declared VNet (and no ACA env / App Service plan that would require one)"))
    # Citadel-specific are not-applicable when not Citadel
    if resolved_posture != POSTURE_CITADEL:
        for fid in ("NET-501", "NET-502", "NET-503"):
            out.append(_mk_finding(fid, status="not-applicable",
                detail=f"Resolved posture is {resolved_posture}; Citadel-spoke check skipped"))
    return out


def _check_network_live(ctx: RepoContext, tiers: dict[int, bool], resolved_posture: str,
                        sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if not tiers.get(1) or not sub or not rg:
        findings.append(_not_verified("NET-101", "Tier 1 Reader not available or sub/RG unknown"))
        findings.append(_not_verified("NET-102", "Tier 1 Reader not available or sub/RG unknown"))
        findings.append(_not_verified("NET-103", "Tier 1 Reader not available or sub/RG unknown"))
    else:
        # NET-101 publicNetworkAccess on Foundry account
        data = _az_json("cognitiveservices", "account", "list", "--resource-group", rg, "--subscription", sub)
        if data is None:
            findings.append(_not_verified("NET-101", "az cognitiveservices account list failed"))
        else:
            bad = [a for a in data if (a.get("properties") or {}).get("publicNetworkAccess") == "Enabled"]
            ok = bool(data) and not bad
            evidence.append(EvidenceEntry(
                ref="E-NET-101", pillar="network-posture",
                description="Foundry/Cognitive accounts publicNetworkAccess",
                command=f"az cognitiveservices account list -g {rg}",
                scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
                result="ok" if data else "missing",
                notes=f"{len(bad)} of {len(data)} accounts have publicNetworkAccess=Enabled" if data else "no accounts"))
            if not data:
                findings.append(_mk_finding("NET-101", status="should-fix",
                    detail="No Cognitive/Foundry accounts in target RG", evidence_refs=["E-NET-101"]))
            else:
                findings.append(_mk_finding("NET-101",
                    status="pass" if ok else "must-fix",
                    detail="All accounts have publicNetworkAccess=Disabled" if ok else f"{len(bad)} account(s) have publicNetworkAccess=Enabled",
                    evidence_refs=["E-NET-101"]))
        # NET-102 PE resources present and approved
        pe = _az_json("network", "private-endpoint", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(
            ref="E-NET-102", pillar="network-posture",
            description="Private endpoints in target RG",
            command=f"az network private-endpoint list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if pe is not None else "error",
            notes=f"{len(pe) if isinstance(pe, list) else 0} PEs"))
        if pe is None:
            findings.append(_not_verified("NET-102", "private-endpoint list failed"))
        else:
            findings.append(_mk_finding("NET-102",
                status="pass" if pe else "must-fix",
                detail=f"{len(pe)} private endpoint(s) in target RG" if pe else "No private endpoints in target RG",
                evidence_refs=["E-NET-102"]))
        # NET-103 NSG flow logs - best-effort, skip if not available
        findings.append(_not_verified("NET-103", "NSG flow log probe not implemented in v1 — review manually"))
    # Citadel checks (tier 5)
    if resolved_posture == POSTURE_CITADEL:
        # NET-501 — v0.3.0 wired: look up the Citadel hub APIM in the RG
        # named by TL_CITADEL_HUB_RG and verify an Access Contract product
        # exists. NET-502 now fail-closes to the sibling-skill recipe; NET-503 remains experimental.
        hub_rg = os.getenv("TL_CITADEL_HUB_RG")
        if not hub_rg:
            findings.append(_not_verified("NET-501",
                "TL_CITADEL_HUB_RG env var not set — cannot locate Citadel hub APIM. "
                "Set TL_CITADEL_HUB_RG=<hub_resource_group> and re-run (Tier 5)."))
        elif not tiers.get(5) or not sub:
            findings.append(_not_verified("NET-501",
                "Tier 5 APIM Service Reader unavailable for Citadel hub probe"))
        else:
            apim_list = _az_json("apim", "list", "--resource-group", hub_rg, "--subscription", sub)
            if apim_list is None:
                findings.append(_not_verified("NET-501",
                    f"`az apim list -g {hub_rg}` failed — verify hub RG name and APIM Service Reader role"))
            elif not apim_list:
                findings.append(_mk_finding("NET-501", status="must-fix",
                    detail=f"No APIM instance found in Citadel hub RG `{hub_rg}` — Access Contract cannot exist"))
            else:
                contract_found: list[str] = []
                for apim in apim_list:
                    apim_name = apim.get("name")
                    if not apim_name:
                        continue
                    products = _az_json("apim", "product", "list",
                                        "--resource-group", hub_rg,
                                        "--service-name", apim_name,
                                        "--subscription", sub) or []
                    for p in products:
                        pid = (p.get("name") or "").lower()
                        pdn = (p.get("displayName") or "").lower()
                        if "access-contract" in pid or "access contract" in pdn or pid.startswith("ac-"):
                            contract_found.append(f"{apim_name}/{p.get('name')}")
                evidence.append(EvidenceEntry(ref="E-NET-501", pillar="network-posture",
                    description="Citadel APIM Access Contract products",
                    command=f"az apim product list -g {hub_rg} --service-name <apim>",
                    scope=f"sub={sub} hub_rg={hub_rg}", tier=5, captured_at=_utc_now(),
                    result="ok", notes=f"{len(contract_found)} contract product(s) found"))
                if contract_found:
                    findings.append(_mk_finding("NET-501", status="pass",
                        detail=f"Citadel Access Contract(s) found: {contract_found}",
                        evidence_refs=["E-NET-501"]))
                else:
                    findings.append(_mk_finding("NET-501", status="must-fix",
                        detail=f"No Access Contract product found on any APIM in hub RG `{hub_rg}`",
                        evidence_refs=["E-NET-501"]))
        findings.append(_mk_finding("NET-502", status="must-fix",
            detail="Citadel-spoke connection check requires the `citadel-spoke-onboarding` sibling skill. Dispatch via the recipe at references/remediation-recipes/NET-502.md."))
        findings.append(_not_verified("NET-503",
            "Tier 5 Citadel/APIM product-policy probe remains experimental (set --include-experimental to enable)"))
    return findings, evidence


# ---- pillar 2: agent-governance (AGT) --------------------------------------

def _check_agt_static(ctx: RepoContext, agt_profile: str) -> list[Finding]:
    out: list[Finding] = []
    src = ctx.src_text
    # AGT-001 imported in src
    has_import = bool(re.search(r"foundry[-_]agt|from\s+agt\b|import\s+agt\b|@foundry/agt", src, re.I))
    out.append(_mk_finding("AGT-001",
        status="pass" if has_import else "must-fix",
        detail="AGT import found in src/" if has_import else "No AGT import in src/ — agent has no in-process governance"))
    # AGT-002 policy.yaml
    has_policy = any(p.name in ("policy.yaml", "agt-policy.yaml", "policy.yml") for p in _glob_repo(ctx.root, "**/policy*.y*ml"))
    out.append(_mk_finding("AGT-002",
        status="pass" if has_policy else "must-fix",
        detail="policy.yaml present" if has_policy else "No AGT policy.yaml file found"))
    # AGT-003 OWASP ASI verifier referenced
    has_owasp = bool(re.search(r"OWASP|ASI[- ]?2026|agent_security|asi[._-]verifier", src + "\n" + ctx.docs_text + "\n" + ctx.spec_text, re.I))
    out.append(_mk_finding("AGT-003",
        status="pass" if has_owasp else "should-fix",
        detail="OWASP ASI 2026 referenced" if has_owasp else "No OWASP ASI 2026 verifier reference found"))
    # AGT-004 pinned version
    has_pin = bool(re.search(r"foundry[-_]agt[^\n]*[=@~^]\s*\d+\.\d+", ctx.src_text + ctx.docs_text, re.I)) or any(
        "requirements" in p.name or "pyproject" in p.name or "package.json" in p.name
        for p in _glob_repo(ctx.root, "requirements*.txt", "pyproject.toml", "package.json")
    )
    out.append(_mk_finding("AGT-004",
        status="pass" if has_pin else "should-fix",
        detail="AGT version constraint detected" if has_pin else "Cannot find AGT pin — risk of unintended upgrade"))
    # AGT-005 policy covers tool calls + shields  (heuristic — check policy file content)
    pol_text = ""
    for p in _glob_repo(ctx.root, "**/policy*.y*ml"):
        pol_text += "\n" + (_read_text(p) or "")
    covers = bool(re.search(r"tool[_ ]?call|tools:", pol_text, re.I)) and bool(re.search(r"prompt[_ ]?shield|jailbreak", pol_text, re.I))
    out.append(_mk_finding("AGT-005",
        status="pass" if covers else "must-fix",
        detail="Policy covers tool calls + prompt shields" if covers else "Policy missing tool-call and/or prompt-shield clauses"))
    # AGT-006 telemetry sink
    has_telemetry = bool(re.search(r"telemetry|otel|opentelemetry|app[_ -]?insights", pol_text + src, re.I))
    out.append(_mk_finding("AGT-006",
        status="pass" if has_telemetry else "should-fix",
        detail="Telemetry sink wired" if has_telemetry else "No telemetry sink wired for AGT denials"))
    # Note unknown profile
    if agt_profile and agt_profile not in ("none", "auto", "v3_7", "v4_preview"):
        out.append(_not_verified("AGT-001", f"Unknown --agt-profile {agt_profile!r}; v4 migration considerations apply"))
    return out


def _check_agt_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if tiers.get(1) and sub and rg:
        # AGT-101 workload identity scoped
        ra = _az_json("role", "assignment", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-AGT-101", pillar="agent-governance",
            description="Role assignments on target RG", command=f"az role assignment list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if ra is not None else "error",
            notes=f"{len(ra) if isinstance(ra, list) else 0} assignments"))
        if ra is None:
            findings.append(_not_verified("AGT-101", "role assignment list failed"))
        else:
            broad = [a for a in ra if (a.get("roleDefinitionName") or "") in ("Owner", "Contributor")]
            findings.append(_mk_finding("AGT-101",
                status="should-fix" if broad else "pass",
                detail=f"{len(broad)} broad role assignment(s) found" if broad else "No Owner/Contributor on RG identities",
                evidence_refs=["E-AGT-101"]))
    else:
        findings.append(_not_verified("AGT-101", "Tier 1 Reader unavailable"))
    # AGT-102 needs LA query — not implemented in v1
    findings.append(_not_verified("AGT-102", "Tier 2 KQL probe for AGT denials not implemented in v1"))
    return findings, evidence


# ---- pillar 2: agent-governance — v4-preview deep checks --------------------
#
# All findings emitted by these functions are gated by _run_pillar to fire only
# when agt_profile == "v4_preview". They never emit when profile is v3_7, auto-
# resolved-to-v3_7, or none. See docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md
# for the recon evidence and design rationale.

# Grounded v4 detection regexes — anchored on verified upstream signals
# (microsoft/agent-governance-toolkit v4.1.0, CHANGELOG dated 2026-06-01).
V4_DIST_REGEX = re.compile(
    r"agent-governance-toolkit(?:-(?:core|runtime|sre|cli)|\[full\])",
    re.IGNORECASE,
)
V4_POLICY_REGEX = re.compile(
    r"^\s*agent_control_specification_version\s*:|^\s*intervention_points\s*:",
    re.MULTILINE,
)
V4_DYNAMIC_REGEX = re.compile(
    r"\btime_window\s*:|\bcost_per_window\s*:|\btoken_count_per_window\s*:|\bday_of_week\s*:|agent_os\.policies\.dynamic_context",
)
V3_7_DIST_REGEX = re.compile(
    # legacy umbrella names; if any of these appear and none of V4_DIST then v4-001 fails
    r"\bagent[-_]governance[-_]toolkit\s*(?:[=@~^<>!]|$)|\bfoundry[-_]agt\s*(?:[=@~^<>!]|$)",
    re.IGNORECASE,
)

# Canonical v4 intervention point keys (from policy-engine canonical-all-interventions.yaml)
_V4_INTERVENTION_KEYS = (
    "agent_startup", "input", "pre_model_call", "post_model_call",
    "pre_tool_call", "post_tool_call", "output",
)

# Canonical v4 audit fields (from CHANGELOG line 34 "expanded audit fields")
_V4_AUDIT_FIELDS = (
    "arguments_hash", "approver_did", "policy_version", "issued_at", "completed_at",
)


def _v4_scoped_files(root: Path) -> dict[str, list[Path]]:
    """Return v4-detection-scoped file groups.

    Critical: this MUST NOT include docs/**, README.md, *.md, or specs/SPEC.md —
    docs prose mentioning v4 must not flip auto-detection to v4_preview.
    """
    deps = _glob_repo(root, "requirements*.txt", "pyproject.toml", "package.json")
    # also include nested per-service deps (e.g. src/agent/requirements.txt)
    deps += _glob_repo(root, "src/**/requirements*.txt", "src/**/pyproject.toml", "src/**/package.json")
    policies = _glob_repo(root, "**/policy*.y*ml", "**/policies/**/*.y*ml", "**/agt-policy*.y*ml")
    workflows = _glob_repo(root, ".github/workflows/*.yml", ".github/workflows/*.yaml")
    src_python = _glob_repo(root, "src/**/*.py")
    verifier_json = _glob_repo(root, "tests/**/verifier*.json", "tests/**/agt-verifier*.json", "docs/**/agt-verifier*.json")
    return {
        "deps": [p for p in deps if "node_modules" not in p.parts],
        "policies": policies,
        "workflows": workflows,
        "src_python": src_python,
        "verifier_json": verifier_json,
    }


def _v4_signal_present(files: list[Path], pattern: re.Pattern[str]) -> tuple[bool, list[Path]]:
    """Return (any-match, list-of-files-that-matched). Empty list when no files."""
    matched: list[Path] = []
    for p in files:
        text = _read_text(p) or ""
        if pattern.search(text):
            matched.append(p)
    return (bool(matched), matched)


def _check_agt_static_v4(ctx: RepoContext) -> list[Finding]:
    """v4-preview deep checks. Caller MUST gate this to agt_profile == 'v4_preview'."""
    out: list[Finding] = []
    scoped = _v4_scoped_files(ctx.root)

    # AGT-V4-001 — v4 distribution names declared in deps
    # Tri-state: not-applicable if no AGT deps at all; pass if v4 names found;
    # must-fix only if AGT IS declared but only via v3.7-shape names.
    v4_in_deps, v4_dep_files = _v4_signal_present(scoped["deps"], V4_DIST_REGEX)
    v37_in_deps, v37_dep_files = _v4_signal_present(scoped["deps"], V3_7_DIST_REGEX)
    if v4_in_deps:
        out.append(_mk_finding("AGT-V4-001",
            status="pass",
            detail=f"v4 distribution name(s) found in {len(v4_dep_files)} dependency file(s)"))
    elif v37_in_deps:
        out.append(_mk_finding("AGT-V4-001",
            status="must-fix",
            detail=("AGT declared in deps but only v3.7-shape names found "
                    "(`agent-governance-toolkit` umbrella or `foundry-agt`); "
                    "v4 requires one of `agent-governance-toolkit-{core,runtime,sre,cli}` or `[full]`")))
    else:
        out.append(_mk_finding("AGT-V4-001",
            status="not-applicable",
            detail="No AGT dependency declared in repo — v4 distribution check skipped"))

    # AGT-V4-002 — ACS schema markers present in policy YAML
    # Presence-of-key heuristic (NOT exact ACS version match — beta version string will churn).
    if not scoped["policies"]:
        out.append(_mk_finding("AGT-V4-002",
            status="not-applicable",
            detail="No policy YAML files found — ACS schema check skipped"))
    else:
        acs_present = False
        intervention_keys_found: set[str] = set()
        acs_version_seen: str | None = None
        for p in scoped["policies"]:
            text = _read_text(p) or ""
            if re.search(r"^\s*agent_control_specification_version\s*:", text, re.MULTILINE):
                acs_present = True
                m = re.search(r"^\s*agent_control_specification_version\s*:\s*['\"]?([^'\"\n#]+)", text, re.MULTILINE)
                if m and not acs_version_seen:
                    acs_version_seen = m.group(1).strip()
            if re.search(r"^\s*intervention_points\s*:", text, re.MULTILINE):
                acs_present = True
                for key in _V4_INTERVENTION_KEYS:
                    if re.search(rf"^\s+{re.escape(key)}\s*:", text, re.MULTILINE):
                        intervention_keys_found.add(key)
        if acs_present and intervention_keys_found:
            out.append(_mk_finding("AGT-V4-002",
                status="pass",
                detail=(f"ACS schema present (version={acs_version_seen or 'unspecified'}); "
                        f"intervention keys: {sorted(intervention_keys_found)}")))
        elif acs_present:
            out.append(_mk_finding("AGT-V4-002",
                status="should-fix",
                detail=("ACS version key present but no `intervention_points:` block detected — "
                        "policy may be partial-v4; add intervention_points")))
        else:
            out.append(_mk_finding("AGT-V4-002",
                status="should-fix",
                detail=(f"Policy YAML found ({len(scoped['policies'])} file(s)) but no ACS schema markers "
                        "(`agent_control_specification_version:` / `intervention_points:`); upgrade to v4 schema")))

    # AGT-V4-003 — informational only: dynamic policy conditions detected anywhere in scoped surface
    dyn_in_policies, dyn_policy_files = _v4_signal_present(scoped["policies"], V4_DYNAMIC_REGEX)
    dyn_in_src, dyn_src_files = _v4_signal_present(scoped["src_python"], V4_DYNAMIC_REGEX)
    if dyn_in_policies or dyn_in_src:
        out.append(_mk_finding("AGT-V4-003",
            status="pass",
            detail=(f"Dynamic policy conditions detected: "
                    f"{len(dyn_policy_files)} policy file(s), {len(dyn_src_files)} source file(s)")))
    else:
        out.append(_mk_finding("AGT-V4-003",
            status="not-applicable",
            detail="No dynamic policy conditions (time_window / cost_per_window / token_count_per_window) detected — informational"))

    # AGT-V4-006 — composite GitHub Action pinned via toolkit-version (tri-state)
    action_uses: list[Path] = []
    action_pinned: list[Path] = []
    action_unpinned: list[Path] = []
    action_use_re = re.compile(r"uses\s*:\s*microsoft/agent-governance-toolkit/action@", re.IGNORECASE)
    for p in scoped["workflows"]:
        text = _read_text(p) or ""
        if action_use_re.search(text):
            action_uses.append(p)
            # check for toolkit-version: input within ~30 lines after each `uses:` for the AGT action.
            # heuristic: split into "uses:" blocks and check each.
            blocks = re.split(r"(?=\s*-\s*name\s*:|^\s*-\s*uses\s*:|^\s*uses\s*:)", text, flags=re.MULTILINE)
            for blk in blocks:
                if action_use_re.search(blk):
                    if re.search(r"\btoolkit-version\s*:", blk):
                        action_pinned.append(p)
                    else:
                        action_unpinned.append(p)
                    break
    if not action_uses:
        out.append(_mk_finding("AGT-V4-006",
            status="not-applicable",
            detail="No use of `microsoft/agent-governance-toolkit/action` in workflows — pin check skipped"))
    elif action_unpinned:
        out.append(_mk_finding("AGT-V4-006",
            status="must-fix",
            detail=(f"AGT composite action used in {len(action_unpinned)} workflow(s) without `toolkit-version:` input; "
                    "v4 BREAKING_CHANGES.md makes this input required")))
    else:
        out.append(_mk_finding("AGT-V4-006",
            status="pass",
            detail=f"AGT composite action used in {len(action_pinned)} workflow(s), all pinned via toolkit-version"))

    # AGT-V4-007 — v4 audit fields present in committed verifier JSON
    # Tri-state: not-verified if no JSON exists; pass if ≥3 of 5 fields found; should-fix otherwise.
    if not scoped["verifier_json"]:
        out.append(_not_verified("AGT-V4-007",
            "No committed verifier JSON artefact found — v4 audit field check requires JSON output (markdown verifier reports not in scope)"))
    else:
        fields_found: set[str] = set()
        for p in scoped["verifier_json"]:
            text = _read_text(p) or ""
            for field_name in _V4_AUDIT_FIELDS:
                if re.search(rf'["\']?{re.escape(field_name)}["\']?\s*:', text):
                    fields_found.add(field_name)
        if len(fields_found) >= 3:
            out.append(_mk_finding("AGT-V4-007",
                status="pass",
                detail=f"v4 audit fields present in verifier JSON: {sorted(fields_found)}"))
        else:
            out.append(_mk_finding("AGT-V4-007",
                status="should-fix",
                detail=(f"Verifier JSON exists ({len(scoped['verifier_json'])} file(s)) but missing v4 audit fields; "
                        f"found only: {sorted(fields_found) or 'none'}; expected ≥3 of {list(_V4_AUDIT_FIELDS)}")))

    # NOTE: AGT-V4-004 (modernized PII regex) and AGT-V4-005 (first-party CLI integration packages)
    # were deferred to a follow-up PR per rubber-duck critique — see
    # docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md § "What was deferred".

    return out


def _check_agt_live_v4(
    ctx: RepoContext,
    tiers: dict[int, bool],
    sub: str | None,
    rg: str | None,
) -> tuple[list[Finding], list[EvidenceEntry]]:
    """v4-preview live deep checks. Caller MUST gate this to agt_profile == 'v4_preview'.

    v1 of v4 deep checks ships only AGT-V4-101 as a `not-verified` stub — symmetric
    to AGT-102 in the version-agnostic live check. The KQL probe over the `policy_version`
    field in App Insights denial events is left for a follow-up PR when a real v4
    pilot is available to test against.
    """
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    findings.append(_not_verified(
        "AGT-V4-101",
        "Tier 2 KQL probe over v4 `policy_version` field in App Insights denial events not implemented in v1",
    ))
    return findings, evidence


# ---- pillar 3: identity-access ---------------------------------------------

SECRET_REGEX = re.compile(r"(?i)(client[_-]?secret|api[_-]?key|password|connection[_-]?string)\s*[:=]\s*['\"][^'\"]{8,}")
SAS_REGEX = re.compile(r"(?:sig=|sas[_-]?token\s*=)", re.I)


def _check_identity_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    src = ctx.src_text
    bicep = ctx.bicep_text
    # IAM-001 no client secrets in src/
    leaked = bool(SECRET_REGEX.search(src))
    out.append(_mk_finding("IAM-001",
        status="must-fix" if leaked else "pass",
        detail="Possible literal secret found in src/ — review" if leaked else "No literal client secrets in src/"))
    # IAM-002 user-assigned MI in Bicep (graph-verified)
    g = ctx.bicep_graph
    uamis = g.by_type("Microsoft.ManagedIdentity/userAssignedIdentities") if g else []
    has_uami = bool(uamis)
    out.append(_mk_finding("IAM-002",
        status="pass" if has_uami else "must-fix",
        detail=f"{len(uamis)} user-assigned managed identity resource(s) declared in compiled ARM" if has_uami
               else "No Microsoft.ManagedIdentity/userAssignedIdentities resource in compiled ARM (comments don't count)"))
    # IAM-003 RBAC scoped
    has_rbac_scoped = bool(re.search(r"Microsoft\.Authorization/roleAssignments", bicep, re.I))
    has_sub_scope = bool(re.search(r"scope:\s*subscription\(\)", bicep, re.I))
    if has_rbac_scoped and not has_sub_scope:
        out.append(_mk_finding("IAM-003", status="pass", detail="RBAC declared and not at subscription scope"))
    elif has_rbac_scoped and has_sub_scope:
        out.append(_mk_finding("IAM-003", status="must-fix",
            detail="Subscription-scope role assignments detected — narrow to RG/resource"))
    else:
        out.append(_mk_finding("IAM-003", status="should-fix",
            detail="No role assignments declared in Bicep — verify identity has what it needs via UI grants?"))
    # IAM-004 no SAS tokens
    has_sas = bool(SAS_REGEX.search(src))
    out.append(_mk_finding("IAM-004",
        status="must-fix" if has_sas else "pass",
        detail="SAS token usage found" if has_sas else "No SAS token usage detected"))
    # IAM-005 auth enabled (ACA / Functions) — graph-verified
    auth_configs = (g.by_type("Microsoft.Web/sites/config") if g else [])
    aca_apps = g.by_type("Microsoft.App/containerApps") if g else []
    aca_auth_configs = g.by_type("Microsoft.App/containerApps/authConfigs") if g else []
    has_easyauth = any((c.get("name") or "").endswith("/authsettings") or (c.get("name") or "").endswith("/authsettingsV2")
                       for c in auth_configs)
    aca_with_auth = [a for a in aca_apps if ((a.get("properties") or {}).get("configuration") or {}).get("ingress", {}).get("clientCertificateMode")
                     or any("auth" in (k.lower()) for k in ((a.get("properties") or {}).get("configuration") or {}).keys())]
    has_auth = has_easyauth or bool(aca_with_auth) or bool(aca_auth_configs)
    # Resolved-posture escalation: a citadel-spoke / standard-AI / AGT / hybrid
    # surface that ships any compute MUST front it with an auth gate.
    has_compute = bool(aca_apps) or (bool(g.by_type("Microsoft.Web/sites")) if g else False)
    needs_auth = ctx.resolved_posture in (POSTURE_CITADEL, POSTURE_STANDARD, POSTURE_AGT, POSTURE_HYBRID) and has_compute
    if not has_auth and needs_auth:
        status = "must-fix"
        detail = ("Posture demands authenticated AI surface but no EasyAuth / authConfigs / "
                  "Microsoft.App/containerApps/authConfigs on declared compute in compiled ARM")
    elif has_auth:
        status = "pass"
        detail = (f"Auth surface declared: easyauth={len(auth_configs)}, "
                  f"aca_with_auth={len(aca_with_auth)}, aca_authConfigs={len(aca_auth_configs)}")
    else:
        status = "should-fix"
        detail = "No EasyAuth / authConfigs on declared compute (ACA / Web sites) in compiled ARM"
    out.append(_mk_finding("IAM-005", status=status, detail=detail))
    return out


def _check_identity_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if not tiers.get(1) or not sub or not rg:
        findings.append(_not_verified("IAM-101", "Tier 1 Reader unavailable"))
        findings.append(_not_verified("IAM-102", "Tier 1 Reader unavailable"))
        findings.append(_not_verified("IAM-103", "Tier 1 Reader unavailable"))
        return findings, evidence
    ra = _az_json("role", "assignment", "list", "--resource-group", rg, "--subscription", sub)
    evidence.append(EvidenceEntry(ref="E-IAM-101", pillar="identity-access",
        description="Role assignments on target RG", command=f"az role assignment list -g {rg}",
        scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
        result="ok" if ra is not None else "error",
        notes=f"{len(ra) if isinstance(ra, list) else 0} assignments"))
    if ra is None:
        findings.append(_not_verified("IAM-101", "role assignment list failed"))
        findings.append(_not_verified("IAM-102", "role assignment list failed"))
    else:
        # IAM-101: presence of role assignments
        findings.append(_mk_finding("IAM-101",
            status="pass" if ra else "should-fix",
            detail=f"{len(ra)} role assignment(s) observed in RG" if ra else "No role assignments in target RG",
            evidence_refs=["E-IAM-101"]))
        # IAM-102: no Owner/Contributor on workload identities (heuristic)
        broad = [a for a in ra if (a.get("roleDefinitionName") or "") in ("Owner", "Contributor")]
        findings.append(_mk_finding("IAM-102",
            status="must-fix" if broad else "pass",
            detail=f"{len(broad)} Owner/Contributor assignment(s) on RG" if broad else "No Owner/Contributor on RG",
            evidence_refs=["E-IAM-101"]))
    findings.append(_not_verified("IAM-103", "Entra conditional-access policy probe not implemented in v1 — review manually"))
    return findings, evidence


# ---- pillar 4: secrets -----------------------------------------------------

def _check_secrets_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    src = ctx.src_text
    g = ctx.bicep_graph
    # SEC-001 — graph-verified Key Vault declaration
    kvs = g.by_type("Microsoft.KeyVault/vaults") if g else []
    has_kv = bool(kvs)
    out.append(_mk_finding("SEC-001",
        status="pass" if has_kv else "must-fix",
        detail=f"{len(kvs)} Key Vault resource(s) declared in compiled ARM" if has_kv
               else "No Microsoft.KeyVault/vaults resource declared in compiled ARM"))
    out.append(_mk_finding("SEC-002",
        status="must-fix" if SECRET_REGEX.search(src) else "pass",
        detail="Literal secrets in repo" if SECRET_REGEX.search(src) else "No literal secrets in repo"))
    has_kv_ref = bool(re.search(r"@Microsoft\.KeyVault\(|keyVaultReference|kv:\/\/", bicep + src, re.I))
    out.append(_mk_finding("SEC-003",
        status="pass" if has_kv_ref else "should-fix",
        detail="Key Vault references used" if has_kv_ref else "No Key Vault references detected — settings may use raw values"))
    has_rotation = bool(re.search(r"rotation|rotate", ctx.spec_text, re.I))
    out.append(_mk_finding("SEC-004",
        status="pass" if has_rotation else "should-fix",
        detail="Rotation strategy mentioned in SPEC" if has_rotation else "No secret rotation policy in SPEC"))
    # SEC-005 — graph-verified soft-delete + purge protection on declared KVs
    if not kvs:
        out.append(_mk_finding("SEC-005", status="must-fix",
            detail="No Key Vault declared in compiled ARM — cannot check soft-delete/purge"))
    else:
        bad_soft = [v for v in kvs if not ((v.get("properties") or {}).get("enableSoftDelete", False))]
        bad_purge = [v for v in kvs if not ((v.get("properties") or {}).get("enablePurgeProtection", False))]
        if not bad_soft and not bad_purge:
            out.append(_mk_finding("SEC-005", status="pass",
                detail=f"All {len(kvs)} KV(s) declare enableSoftDelete=true AND enablePurgeProtection=true"))
        else:
            out.append(_mk_finding("SEC-005", status="must-fix",
                detail=(f"{len(bad_soft)}/{len(kvs)} KV(s) missing enableSoftDelete, "
                        f"{len(bad_purge)}/{len(kvs)} missing enablePurgeProtection")))
    # SEC-006 — graph-verified RBAC on declared KVs
    if not kvs:
        out.append(_mk_finding("SEC-006", status="should-fix",
            detail="No Key Vault declared in compiled ARM — cannot check enableRbacAuthorization"))
    else:
        rbac_kvs = [v for v in kvs if (v.get("properties") or {}).get("enableRbacAuthorization", False)]
        if len(rbac_kvs) == len(kvs):
            out.append(_mk_finding("SEC-006", status="pass",
                detail=f"All {len(kvs)} KV(s) declare enableRbacAuthorization=true"))
        else:
            out.append(_mk_finding("SEC-006", status="should-fix",
                detail=f"{len(kvs) - len(rbac_kvs)}/{len(kvs)} KV(s) NOT using RBAC (legacy access policies)"))
    env_files_with_secrets = False
    for envf in _glob_repo(ctx.root, ".azure/**/.env"):
        if SECRET_REGEX.search(_read_text(envf) or ""):
            env_files_with_secrets = True; break
    out.append(_mk_finding("SEC-007",
        status="must-fix" if env_files_with_secrets else "pass",
        detail="Secret-like values in committed .azure env" if env_files_with_secrets else "No secrets in committed .azure envs"))
    return out


def _check_secrets_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    fids = ("SEC-101", "SEC-102", "SEC-103", "SEC-104", "SEC-105", "SEC-106")
    if not tiers.get(4) or not sub or not rg:
        for fid in fids:
            findings.append(_not_verified(fid, "Tier 4 Key Vault Reader (control plane) unavailable"))
        return findings, evidence
    kvs = _az_json("keyvault", "list", "--resource-group", rg, "--subscription", sub)
    evidence.append(EvidenceEntry(ref="E-SEC-101", pillar="secrets",
        description="Key Vault control-plane inventory",
        command=f"az keyvault list -g {rg}",
        scope=f"sub={sub} rg={rg}", tier=4, captured_at=_utc_now(),
        result="ok" if kvs is not None else "error",
        notes=f"{len(kvs) if isinstance(kvs, list) else 0} vaults"))
    if not kvs:
        for fid in fids:
            findings.append(_mk_finding(fid, status="must-fix",
                detail="No Key Vaults in target RG", evidence_refs=["E-SEC-101"]))
        return findings, evidence
    bad_soft = [v for v in kvs if not (v.get("properties") or {}).get("enableSoftDelete", False)]
    bad_purge = [v for v in kvs if not (v.get("properties") or {}).get("enablePurgeProtection", False)]
    bad_pna = [v for v in kvs if (v.get("properties") or {}).get("publicNetworkAccess") != "Disabled"]
    has_net_rules = [v for v in kvs if (v.get("properties") or {}).get("networkAcls")]
    rbac_v = [v for v in kvs if (v.get("properties") or {}).get("enableRbacAuthorization", False)]
    findings.append(_mk_finding("SEC-101",
        status="pass" if not bad_soft else "must-fix",
        detail="All KVs have soft-delete enabled" if not bad_soft else f"{len(bad_soft)} KV(s) missing soft-delete",
        evidence_refs=["E-SEC-101"]))
    findings.append(_mk_finding("SEC-102",
        status="pass" if not bad_purge else "must-fix",
        detail="All KVs have purge protection" if not bad_purge else f"{len(bad_purge)} KV(s) missing purge protection",
        evidence_refs=["E-SEC-101"]))
    findings.append(_mk_finding("SEC-103",
        status="pass" if not bad_pna else "must-fix",
        detail="All KVs have publicNetworkAccess=Disabled" if not bad_pna else f"{len(bad_pna)} KV(s) allow public network access",
        evidence_refs=["E-SEC-101"]))
    findings.append(_mk_finding("SEC-104",
        status="pass" if len(has_net_rules) == len(kvs) else "should-fix",
        detail=f"{len(has_net_rules)}/{len(kvs)} KV(s) have network ACLs",
        evidence_refs=["E-SEC-101"]))
    findings.append(_mk_finding("SEC-105",
        status="pass" if len(rbac_v) == len(kvs) else "should-fix",
        detail=f"{len(rbac_v)}/{len(kvs)} KV(s) use RBAC",
        evidence_refs=["E-SEC-101"]))
    # SEC-106 — v0.3.0 wired: every declared KV must have ≥1 diagnostic setting
    kv_diag_missing: list[str] = []
    for v in kvs:
        rid = v.get("id")
        if not rid:
            continue
        diags = _az_json("monitor", "diagnostic-settings", "list", "--resource", rid) or []
        if not (isinstance(diags, list) and diags):
            kv_diag_missing.append(v.get("name", "?"))
    evidence.append(EvidenceEntry(ref="E-SEC-106", pillar="secrets",
        description="Diagnostic settings on Key Vaults",
        command="az monitor diagnostic-settings list --resource <id> (per KV)",
        scope=f"sub={sub} rg={rg}", tier=4, captured_at=_utc_now(),
        result="ok", notes=f"{len(kv_diag_missing)} KV(s) missing diag settings"))
    if not kv_diag_missing:
        findings.append(_mk_finding("SEC-106", status="pass",
            detail=f"All {len(kvs)} KV(s) have ≥1 diagnostic setting",
            evidence_refs=["E-SEC-106"]))
    else:
        findings.append(_mk_finding("SEC-106", status="should-fix",
            detail=f"{len(kv_diag_missing)}/{len(kvs)} KV(s) have no diagnostic settings: {kv_diag_missing}",
            evidence_refs=["E-SEC-106"]))
    # ---- v0.3.0 NEW: GOV-102 Defender for Key Vault plan
    if tiers.get(1) and sub:
        pricings = _az_json("security", "pricing", "show", "--name", "KeyVaults", "--subscription", sub)
        tier = None
        if isinstance(pricings, dict):
            tier = (pricings.get("properties") or {}).get("pricingTier") or pricings.get("pricingTier")
        evidence.append(EvidenceEntry(ref="E-GOV-102", pillar="secrets",
            description="Defender for Key Vault plan",
            command=f"az security pricing show --name KeyVaults --subscription {sub}",
            scope=f"sub={sub}", tier=1, captured_at=_utc_now(),
            result="ok" if pricings is not None else "error",
            notes=f"tier={tier}"))
        if tier is None:
            findings.append(_not_verified("GOV-102", "Defender pricing for KeyVaults not returned"))
        else:
            findings.append(_mk_finding("GOV-102",
                status="pass" if str(tier).lower() == "standard" else "should-fix",
                detail=f"Defender for Key Vault pricingTier = {tier}",
                evidence_refs=["E-GOV-102"]))
    else:
        findings.append(_not_verified("GOV-102", "Tier 1 Reader unavailable"))
    return findings, evidence


# ---- pillar 5: observability ----------------------------------------------

def _check_observability_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    src = ctx.src_text
    g = ctx.bicep_graph
    # OBS-001 — graph-verified App Insights
    appins = g.by_type("Microsoft.Insights/components") if g else []
    has_ai = bool(appins)
    out.append(_mk_finding("OBS-001",
        status="pass" if has_ai else "must-fix",
        detail=f"{len(appins)} Application Insights component(s) declared in compiled ARM" if has_ai
               else "No Microsoft.Insights/components resource declared in compiled ARM"))
    # OBS-002 — graph-verified Log Analytics workspace
    laws = g.by_type("Microsoft.OperationalInsights/workspaces") if g else []
    has_la = bool(laws)
    out.append(_mk_finding("OBS-002",
        status="pass" if has_la else "must-fix",
        detail=f"{len(laws)} Log Analytics workspace(s) declared in compiled ARM" if has_la
               else "No Microsoft.OperationalInsights/workspaces resource declared in compiled ARM"))
    has_otel = bool(re.search(r"opentelemetry|azure[._-]monitor[._-]opentelemetry|@opentelemetry", src, re.I))
    out.append(_mk_finding("OBS-003",
        status="pass" if has_otel else "must-fix",
        detail="OTel SDK wired in src/" if has_otel else "No OTel SDK references in src/"))
    has_foundry_obs = bool(re.search(r"foundry[._-]observability|Microsoft\.CognitiveServices/accounts/projects.*diag", bicep + src, re.I))
    out.append(_mk_finding("OBS-004",
        status="pass" if has_foundry_obs else "should-fix",
        detail="Foundry observability emit wired" if has_foundry_obs else "No Foundry observability emit detected"))
    has_workbook = any("workbook" in p.name.lower() for p in _glob_repo(ctx.root, "infra/**/*.json", "infra/**/*.bicep", "docs/**/*"))
    out.append(_mk_finding("OBS-005",
        status="pass" if has_workbook else "should-fix",
        detail="Workbook scaffold present" if has_workbook else "No workbook scaffold found"))
    return out


def _check_observability_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if not tiers.get(1) or not sub or not rg:
        for fid in ("OBS-101", "OBS-104", "OBS-105", "OBS-106"):
            findings.append(_not_verified(fid, "Tier 1 Reader unavailable"))
    else:
        ai = _az_json("monitor", "app-insights", "component", "show", "--resource-group", rg, "--subscription", sub) or \
             _az_json("resource", "list", "--resource-group", rg, "--subscription", sub, "--resource-type", "Microsoft.Insights/components")
        evidence.append(EvidenceEntry(ref="E-OBS-101", pillar="observability",
            description="App Insights presence",
            command=f"az resource list -g {rg} --resource-type Microsoft.Insights/components",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if ai is not None else "error",
            notes=str(len(ai) if isinstance(ai, list) else (1 if ai else 0))))
        present = bool(ai) if isinstance(ai, list) else (ai is not None)
        findings.append(_mk_finding("OBS-101",
            status="pass" if present else "must-fix",
            detail="App Insights component present in target RG" if present else "No App Insights in target RG",
            evidence_refs=["E-OBS-101"]))
        # Alerts
        alerts = _az_json("monitor", "metrics", "alert", "list", "--resource-group", rg, "--subscription", sub)
        action_groups = _az_json("monitor", "action-group", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-OBS-104", pillar="observability",
            description="Alert rules + action groups",
            command=f"az monitor metrics alert list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if alerts is not None else "error",
            notes=f"{len(alerts) if isinstance(alerts, list) else 0} alerts, {len(action_groups) if isinstance(action_groups, list) else 0} action groups"))
        findings.append(_mk_finding("OBS-104",
            status="pass" if alerts else "must-fix",
            detail=f"{len(alerts) if alerts else 0} alert rule(s)" if alerts is not None else "alert list failed",
            evidence_refs=["E-OBS-104"]))
        findings.append(_mk_finding("OBS-105",
            status="pass" if action_groups else "must-fix",
            detail=f"{len(action_groups) if action_groups else 0} action group(s)" if action_groups is not None else "action group list failed",
            evidence_refs=["E-OBS-104"]))
        # OBS-106 — v0.3.0 wired: enumerate diagnostic settings on each
        # AI Services / Foundry account and verify at least one wires logs
        # to a LA workspace or storage account. Tier 1 (Reader).
        cs_accounts_live = _az_json("resource", "list", "--resource-group", rg, "--subscription", sub,
                                    "--resource-type", "Microsoft.CognitiveServices/accounts") or []
        diag_results: list[tuple[str, int]] = []
        for acct in cs_accounts_live:
            rid = acct.get("id")
            if not rid:
                continue
            diags = _az_json("monitor", "diagnostic-settings", "list", "--resource", rid) or []
            diag_results.append((acct.get("name", "?"), len(diags) if isinstance(diags, list) else 0))
        evidence.append(EvidenceEntry(ref="E-OBS-106", pillar="observability",
            description="Diagnostic settings on Foundry/AI accounts",
            command="az monitor diagnostic-settings list --resource <id> (per account)",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if cs_accounts_live or not diag_results else "ok",
            notes=f"{len(diag_results)} accounts inspected"))
        if not cs_accounts_live:
            findings.append(_mk_finding("OBS-106", status="not-applicable",
                detail="No Cognitive Services/Foundry accounts in target RG — nothing to wire diag settings on"))
        else:
            missing = [n for (n, c) in diag_results if c == 0]
            if not missing:
                findings.append(_mk_finding("OBS-106", status="pass",
                    detail=f"All {len(diag_results)} AI/Foundry account(s) have ≥1 diagnostic setting",
                    evidence_refs=["E-OBS-106"]))
            else:
                findings.append(_mk_finding("OBS-106", status="must-fix",
                    detail=f"{len(missing)}/{len(diag_results)} AI/Foundry account(s) have NO diagnostic settings: {missing}",
                    evidence_refs=["E-OBS-106"]))
    # OBS-102 — v0.3.0 wired: KQL probe for trace freshness in App
    # Insights. Tier 2 (Monitoring Reader). Looks for any `traces` rows in
    # the last 24h on the LA workspace bound to the first App Insights
    # component in the RG. The probe is opportunistic — if the workspace
    # ID can't be resolved we leave a not-verified.
    if not tiers.get(2) or not sub or not rg:
        findings.append(_not_verified("OBS-102", "Tier 2 Monitoring Reader unavailable for KQL"))
    else:
        ai_list = _az_json("resource", "list", "--resource-group", rg, "--subscription", sub,
                           "--resource-type", "Microsoft.Insights/components") or []
        if not ai_list:
            findings.append(_not_verified("OBS-102", "No Application Insights in target RG — cannot run KQL probe"))
        else:
            workspace_id: str | None = None
            for ai in ai_list:
                wid = (ai.get("properties") or {}).get("WorkspaceResourceId")
                if wid:
                    workspace_id = wid
                    break
            if not workspace_id:
                findings.append(_not_verified("OBS-102",
                    "App Insights present but workspace-based linkage missing — cannot run KQL"))
            else:
                # Get LA workspace customer-id for `az monitor log-analytics query`.
                la_show = _az_json("monitor", "log-analytics", "workspace", "show", "--ids", workspace_id) or {}
                customer_id = (la_show or {}).get("customerId")
                if not customer_id:
                    findings.append(_not_verified("OBS-102",
                        "LA workspace lookup failed — cannot run KQL"))
                else:
                    kql = "traces | where timestamp > ago(24h) | summarize n = count()"
                    out_q = _az_json("monitor", "log-analytics", "query",
                                     "--workspace", customer_id, "--analytics-query", kql)
                    rows = 0
                    if isinstance(out_q, list) and out_q:
                        first = out_q[0]
                        if isinstance(first, dict):
                            try:
                                rows = int(first.get("n") or first.get("Count") or 0)
                            except (TypeError, ValueError):
                                rows = 0
                    evidence.append(EvidenceEntry(ref="E-OBS-102", pillar="observability",
                        description="Traces in App Insights last 24h",
                        command="az monitor log-analytics query (traces | last 24h | count)",
                        scope=f"sub={sub} rg={rg}", tier=2, captured_at=_utc_now(),
                        result="ok", notes=f"{rows} rows"))
                    findings.append(_mk_finding("OBS-102",
                        status="pass" if rows > 0 else "should-fix",
                        detail=(f"{rows} `traces` rows in App Insights last 24h" if rows > 0
                                else "0 `traces` rows in App Insights last 24h — workload not emitting telemetry?"),
                        evidence_refs=["E-OBS-102"]))
    findings.append(_not_verified("OBS-103", "Tier 2 KQL exception probe not implemented in v1"))
    return findings, evidence


# ---- pillar 6: continuous-evals -------------------------------------------

def _check_evals_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    spec = ctx.spec_text
    m = re.search(r"##\s*9\.?\s+Eval", spec, re.I)
    sec9_present = bool(m)
    out.append(_mk_finding("EVAL-001",
        status="pass" if sec9_present else "must-fix",
        detail="SPEC sec 9 (Evals) present" if sec9_present else "SPEC sec 9 missing — no eval scenarios declared"))
    evals_dir = _glob_repo(ctx.root, "evals/**/*.json", "evals/**/*.yaml", "evals/**/*.yml")
    out.append(_mk_finding("EVAL-002",
        status="pass" if evals_dir else "must-fix",
        detail=f"{len(evals_dir)} eval file(s) under evals/" if evals_dir else "No evals/ folder with eval files"))
    has_sched = bool(re.search(r"schedul|cadence|nightly|hourly|cron", spec + ctx.docs_text, re.I))
    out.append(_mk_finding("EVAL-003",
        status="pass" if has_sched else "must-fix",
        detail="Eval scheduling plan referenced" if has_sched else "No eval scheduling plan documented"))
    has_thresholds = bool(re.search(r"threshold|>=\s*\d|\s+pass(?:ing)?\s+rate", spec, re.I))
    out.append(_mk_finding("EVAL-004",
        status="pass" if has_thresholds else "should-fix",
        detail="Eval thresholds present" if has_thresholds else "No eval thresholds declared in SPEC"))
    has_grader = bool(re.search(r"grader|judge|llm[-_ ]as[-_ ]a[-_ ]judge", spec, re.I))
    out.append(_mk_finding("EVAL-005",
        status="pass" if has_grader else "should-fix",
        detail="Grader strategy named" if has_grader else "No grader strategy named in SPEC"))
    has_versioning = bool(re.search(r"dataset.*version|v\d+\.\d+", spec, re.I))
    out.append(_mk_finding("EVAL-006",
        status="pass" if has_versioning else "should-fix",
        detail="Dataset versioning hinted" if has_versioning else "No dataset versioning documented"))
    return out


def _check_evals_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    findings.append(_not_verified("EVAL-101",
        "EVAL-101 requires a manual operator-team conversation (does the customer have any evaluation harness at all?). See references/remediation-recipes/EVAL-101.md."))
    findings.append(_not_verified("EVAL-102",
        "EVAL-102 requires a manual operator-team conversation (does the customer have a regression eval baseline?). See references/remediation-recipes/EVAL-102.md."))
    for fid in ("EVAL-103", "EVAL-104", "EVAL-105"):
        findings.append(_not_verified(fid, "Eval live probe requires Foundry API access and SDK — not implemented in v1"))
    return findings, evidence


# ---- pillar 7: responsible-ai ---------------------------------------------

def _check_rai_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    src = ctx.src_text
    spec = ctx.spec_text
    g = ctx.bicep_graph
    # RAI-001 — graph-verified content filter / RAI policy on Cognitive Services
    # AI Services deployments. Looks for either:
    #   - a Microsoft.CognitiveServices/accounts/raiPolicies child resource, OR
    #   - a `raiPolicyName` property on any /accounts/deployments resource.
    rai_policies = g.by_type("Microsoft.CognitiveServices/accounts/raiPolicies") if g else []
    deployments = g.by_type("Microsoft.CognitiveServices/accounts/deployments") if g else []
    deployments_with_policy = [d for d in deployments if (d.get("properties") or {}).get("raiPolicyName")]
    has_cf = bool(rai_policies) or (bool(deployments) and len(deployments_with_policy) == len(deployments))
    if has_cf:
        detail = (f"{len(rai_policies)} raiPolicies resource(s); "
                  f"{len(deployments_with_policy)}/{len(deployments)} model deployments bind a raiPolicyName")
    elif deployments:
        detail = (f"{len(deployments)} model deployment(s) declared but {len(deployments) - len(deployments_with_policy)} "
                  f"have no raiPolicyName and no raiPolicies child resource present")
    else:
        detail = ("No Microsoft.CognitiveServices/accounts/raiPolicies and no model deployments "
                  "with raiPolicyName declared in compiled ARM")
    out.append(_mk_finding("RAI-001",
        status="pass" if has_cf else "must-fix",
        detail=detail))
    pol_text = ""
    for p in _glob_repo(ctx.root, "**/policy*.y*ml"):
        pol_text += "\n" + (_read_text(p) or "")
    has_rai_pol = bool(re.search(r"\brai\b|responsible[_ ]?ai|content[_ ]?safety", pol_text, re.I))
    out.append(_mk_finding("RAI-002",
        status="pass" if has_rai_pol else "must-fix",
        detail="AGT policy has RAI section" if has_rai_pol else "AGT policy missing RAI/content-safety section"))
    has_shields = bool(re.search(r"prompt[_ ]?shield|jailbreak|indirect[_ ]?attack", pol_text, re.I))
    out.append(_mk_finding("RAI-003",
        status="pass" if has_shields else "must-fix",
        detail="Prompt shields configured" if has_shields else "Prompt shields not configured in policy"))
    has_pii = bool(re.search(r"pii|presidio|redact", spec + ctx.docs_text + pol_text, re.I))
    out.append(_mk_finding("RAI-004",
        status="pass" if has_pii else "should-fix",
        detail="PII redaction strategy documented" if has_pii else "No PII redaction strategy documented"))
    has_grounding = bool(re.search(r"groundedness|grounding|rag.*check", spec + ctx.docs_text, re.I))
    out.append(_mk_finding("RAI-005",
        status="pass" if has_grounding else "should-fix",
        detail="Groundedness check planned" if has_grounding else "No groundedness check planned"))
    has_owner = bool(re.search(r"rai[_ -]?owner|content[_ -]?safety[_ -]?owner|incident[_ -]?owner", spec, re.I))
    out.append(_mk_finding("RAI-006",
        status="pass" if has_owner else "should-fix",
        detail="RAI/incident owner named in SPEC" if has_owner else "No RAI incident owner named"))
    return out


def _check_rai_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if tiers.get(1) and sub and rg:
        cf = _az_json("cognitiveservices", "account", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-RAI-101", pillar="responsible-ai",
            description="Cognitive accounts inventory (proxy for content filter)",
            command=f"az cognitiveservices account list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if cf is not None else "error", notes=f"{len(cf) if isinstance(cf, list) else 0} accounts"))
        if cf is None:
            findings.append(_not_verified("RAI-101", "cognitive account list failed"))
        else:
            findings.append(_mk_finding("RAI-101",
                status="pass" if cf else "must-fix",
                detail=f"{len(cf)} Cognitive/Foundry account(s) present" if cf else "No Cognitive accounts to apply content filter",
                evidence_refs=["E-RAI-101"]))
    else:
        findings.append(_not_verified("RAI-101", "Tier 1 Reader unavailable"))
    findings.append(_not_verified("RAI-102", "Tier 2 KQL for RAI denials not implemented in v1"))
    return findings, evidence


# ---- pillar 8: hitl-audit -------------------------------------------------

def _check_hitl_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    spec = ctx.spec_text
    src = ctx.src_text
    bicep = ctx.bicep_text
    sec8_present = bool(re.search(r"##\s*8\.?\s+(HITL|Human|Gates)", spec, re.I))
    out.append(_mk_finding("HITL-001",
        status="pass" if sec8_present else "should-fix",
        detail="SPEC sec 8 (HITL) present" if sec8_present else "SPEC sec 8 (HITL) missing — fine if no gates intended"))
    has_hitl_code = bool(re.search(r"hitl|human[_ -]in[_ -]the[_ -]loop|approval[_ -]gate", src, re.I))
    if sec8_present:
        out.append(_mk_finding("HITL-002",
            status="pass" if has_hitl_code else "must-fix",
            detail="HITL implementation found" if has_hitl_code else "SPEC declares HITL but no implementation in src/"))
    else:
        out.append(_mk_finding("HITL-002", status="not-applicable",
            detail="No HITL declared in SPEC"))
    has_audit = bool(re.search(r"Microsoft\.Storage/storageAccounts|Microsoft\.Sql/servers|Microsoft\.DocumentDB", bicep, re.I))
    out.append(_mk_finding("HITL-003",
        status="pass" if has_audit else "must-fix",
        detail="Persistent storage for audit declared" if has_audit else "No durable storage for audit trail in infra"))
    has_channel = bool(re.search(r"teams|webhook|email|sendgrid|smtp", src + ctx.spec_text + ctx.docs_text, re.I))
    out.append(_mk_finding("HITL-004",
        status="pass" if has_channel else "should-fix",
        detail="Escalation channel referenced" if has_channel else "No escalation channel reference"))
    has_sla = bool(re.search(r"hitl[_ -]?sla|approval[_ -]?sla|response[_ -]?time", spec, re.I))
    out.append(_mk_finding("HITL-005",
        status="pass" if has_sla else "should-fix",
        detail="HITL decision SLA documented" if has_sla else "No HITL decision SLA documented"))
    return out


def _check_hitl_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if tiers.get(1) and sub and rg:
        st = _az_json("storage", "account", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-HITL-101", pillar="hitl-audit",
            description="Storage accounts in target RG",
            command=f"az storage account list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if st is not None else "error",
            notes=f"{len(st) if isinstance(st, list) else 0} storage accounts"))
        if st is None:
            findings.append(_not_verified("HITL-101", "storage list failed"))
            findings.append(_not_verified("HITL-102", "storage list failed"))
        else:
            findings.append(_mk_finding("HITL-101",
                status="pass" if st else "must-fix",
                detail=f"{len(st)} storage account(s) available for audit" if st else "No storage account for audit",
                evidence_refs=["E-HITL-101"]))
            immutable = [s for s in st if (s.get("immutableStorageWithVersioning") or {}).get("enabled")]
            findings.append(_mk_finding("HITL-102",
                status="pass" if immutable else "should-fix",
                detail=f"{len(immutable)} storage account(s) with immutability" if immutable else "No storage account with immutability policy",
                evidence_refs=["E-HITL-101"]))
    else:
        findings.append(_not_verified("HITL-101", "Tier 1 Reader unavailable"))
        findings.append(_not_verified("HITL-102", "Tier 1 Reader unavailable"))
    findings.append(_not_verified("HITL-103", "Tier 2 KQL for audit rows not implemented in v1"))
    return findings, evidence


# ---- pillar 9: supply-chain -----------------------------------------------

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}", re.I)
LATEST_TAG_RE = re.compile(r":latest\b|:main\b|:master\b", re.I)


def _check_supply_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    src = ctx.src_text
    # SUP-001 container images pinned by digest
    dockerfiles = list(_glob_repo(ctx.root, "**/Dockerfile", "**/*.dockerfile"))
    df_text = "\n".join((_read_text(p) or "") for p in dockerfiles)
    has_digest = bool(DIGEST_RE.search(df_text + bicep + ctx.azure_yaml_text))
    has_latest = bool(LATEST_TAG_RE.search(df_text + bicep))
    if has_latest and not has_digest:
        out.append(_mk_finding("SUP-001", status="must-fix",
            detail=":latest tags found and no digest pins — prod images must be pinned"))
    elif has_digest:
        out.append(_mk_finding("SUP-001", status="pass", detail="Container image digest pinning detected"))
    else:
        out.append(_mk_finding("SUP-001", status="should-fix",
            detail="No digest pin detected (may still be using tags) — review"))
    # SUP-002 bicep modules pinned
    floating = re.findall(r"br:[^']+:latest", bicep)
    out.append(_mk_finding("SUP-002",
        status="must-fix" if floating else "pass",
        detail=f"{len(floating)} module reference(s) using :latest" if floating else "No floating bicep module refs"))
    # SUP-003 dep manifest committed
    has_lock = bool(_glob_repo(ctx.root, "**/package-lock.json", "**/yarn.lock", "**/poetry.lock", "**/Pipfile.lock", "**/requirements*.txt", "**/pyproject.toml", "**/*.csproj"))
    out.append(_mk_finding("SUP-003",
        status="pass" if has_lock else "must-fix",
        detail="Dependency manifest / lock present" if has_lock else "No dependency lock files committed"))
    # SUP-004 SBOM
    has_sbom = bool(re.search(r"sbom|cyclonedx|spdx", ctx.docs_text + ctx.azure_yaml_text, re.I))
    out.append(_mk_finding("SUP-004",
        status="pass" if has_sbom else "should-fix",
        detail="SBOM generation referenced" if has_sbom else "No SBOM generation step documented"))
    # SUP-005 vuln scan
    has_scan = bool(re.search(r"trivy|grype|defender|microsoft[._-]defender|az\s+acr\s+task", ctx.docs_text + ctx.azure_yaml_text, re.I))
    out.append(_mk_finding("SUP-005",
        status="pass" if has_scan else "should-fix",
        detail="Vulnerability scanning referenced" if has_scan else "No vulnerability scan step documented"))
    # SUP-006 ACR scoped private — graph-verified
    g = ctx.bicep_graph
    acrs = g.by_type("Microsoft.ContainerRegistry/registries") if g else []
    if not acrs:
        out.append(_mk_finding("SUP-006", status="not-applicable",
            detail="No Microsoft.ContainerRegistry/registries declared in compiled ARM"))
    else:
        acr_private = [a for a in acrs if (a.get("properties") or {}).get("publicNetworkAccess") == "Disabled"]
        if len(acr_private) == len(acrs):
            out.append(_mk_finding("SUP-006", status="pass",
                detail=f"All {len(acrs)} ACR(s) declare publicNetworkAccess=Disabled"))
        else:
            out.append(_mk_finding("SUP-006", status="should-fix",
                detail=f"{len(acrs) - len(acr_private)}/{len(acrs)} ACR(s) NOT declared publicNetworkAccess=Disabled"))
    # SUP-007 provenance
    has_prov = bool(re.search(r"slsa|provenance|attestation|cosign|notary", ctx.docs_text, re.I))
    out.append(_mk_finding("SUP-007",
        status="pass" if has_prov else "should-fix",
        detail="Provenance/attestation referenced" if has_prov else "No provenance/attestation documented"))
    return out


def _check_supply_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if tiers.get(1) and sub and rg:
        acrs = _az_json("acr", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-SUP-102", pillar="supply-chain",
            description="ACR inventory", command=f"az acr list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if acrs is not None else "error",
            notes=f"{len(acrs) if isinstance(acrs, list) else 0} registries"))
        if acrs is None:
            findings.append(_not_verified("SUP-102", "acr list failed"))
            findings.append(_not_verified("SUP-103", "acr list failed"))
        elif not acrs:
            findings.append(_mk_finding("SUP-102", status="not-applicable",
                detail="No ACR in target RG", evidence_refs=["E-SUP-102"]))
            findings.append(_mk_finding("SUP-103", status="not-applicable",
                detail="No ACR in target RG", evidence_refs=["E-SUP-102"]))
        else:
            bad_pna = [r for r in acrs if r.get("publicNetworkAccess") != "Disabled"]
            findings.append(_mk_finding("SUP-102",
                status="pass" if not bad_pna else "should-fix",
                detail=f"{len(bad_pna)}/{len(acrs)} ACR(s) allow public access",
                evidence_refs=["E-SUP-102"]))
            findings.append(_not_verified("SUP-103", "Microsoft Defender for ACR check not implemented in v1"))
    else:
        findings.append(_not_verified("SUP-102", "Tier 1 Reader unavailable"))
        findings.append(_not_verified("SUP-103", "Tier 1 Reader unavailable"))
    support_md = ctx.root / "SUPPORT.md"
    support_present = support_md.is_file()
    findings.append(_mk_finding("SUP-101",
        status="pass" if support_present else "must-fix",
        detail=("SUPPORT.md present at repo root" if support_present
                else "SUPPORT.md missing at repo root — apply references/remediation-recipes/SUP-101.md")))
    # ---- v0.3.0 NEW: GOV-103 Defender for Servers / Containers
    if tiers.get(1) and sub:
        srv = _az_json("security", "pricing", "show", "--name", "Containers", "--subscription", sub)
        srv2 = _az_json("security", "pricing", "show", "--name", "VirtualMachines", "--subscription", sub)
        tiers_found = []
        for blob, label in ((srv, "Containers"), (srv2, "VirtualMachines")):
            if isinstance(blob, dict):
                t = (blob.get("properties") or {}).get("pricingTier") or blob.get("pricingTier")
                if t:
                    tiers_found.append(f"{label}={t}")
        evidence.append(EvidenceEntry(ref="E-GOV-103", pillar="supply-chain",
            description="Defender for Servers/Containers plans",
            command=f"az security pricing show --name Containers/VirtualMachines --subscription {sub}",
            scope=f"sub={sub}", tier=1, captured_at=_utc_now(),
            result="ok", notes=", ".join(tiers_found) or "missing"))
        if not tiers_found:
            findings.append(_not_verified("GOV-103", "Defender pricing for Servers/Containers not returned"))
        else:
            has_standard = any("Standard" in t for t in tiers_found)
            findings.append(_mk_finding("GOV-103",
                status="pass" if has_standard else "should-fix",
                detail=f"Defender plans: {tiers_found}",
                evidence_refs=["E-GOV-103"]))
    else:
        findings.append(_not_verified("GOV-103", "Tier 1 Reader unavailable"))
    return findings, evidence


# ---- pillar 10: cost ------------------------------------------------------

def _check_cost_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    spec = ctx.spec_text
    sec10 = bool(re.search(r"##\s*10\.?\s+(Cost|Pricing)", spec, re.I))
    out.append(_mk_finding("COST-001",
        status="pass" if sec10 else "must-fix",
        detail="SPEC sec 10 (Cost) present" if sec10 else "SPEC sec 10 (Cost) missing — pricing plan undocumented"))
    has_budget = bool(re.search(r"budget|cost[_ -]?alert|threshold[_ -]?\$|EUR\s+\d", spec + ctx.bicep_text, re.I))
    out.append(_mk_finding("COST-002",
        status="pass" if has_budget else "must-fix",
        detail="Budget threshold(s) referenced" if has_budget else "No budget threshold declared"))
    has_owner = bool(re.search(r"cost[_ -]?owner|finance[_ -]?owner|finops", spec, re.I))
    out.append(_mk_finding("COST-003",
        status="pass" if has_owner else "should-fix",
        detail="Cost owner documented" if has_owner else "No cost owner named in SPEC"))
    has_scale = bool(re.search(r"minReplicas\s*:\s*0|minReplica\s*:\s*0|scale[_ -]?to[_ -]?zero", ctx.bicep_text, re.I))
    out.append(_mk_finding("COST-004",
        status="pass" if has_scale else "should-fix",
        detail="Scale-to-zero configured" if has_scale else "No scale-to-zero / idle scale-down in compute"))
    # COST-005 — graph-verified tags applied on at least 60% of declared resources
    g = ctx.bicep_graph
    if g and g.resources:
        tagged = [r for r in g.resources if r.get("tags")]
        ratio = (len(tagged) * 100) // max(1, len(g.resources))
        if ratio >= 60:
            out.append(_mk_finding("COST-005", status="pass",
                detail=f"{len(tagged)}/{len(g.resources)} ARM resources carry tags ({ratio}%)"))
        elif ratio > 0:
            out.append(_mk_finding("COST-005", status="should-fix",
                detail=f"Only {len(tagged)}/{len(g.resources)} ARM resources carry tags ({ratio}%); target ≥60%"))
        else:
            out.append(_mk_finding("COST-005", status="should-fix",
                detail=f"0/{len(g.resources)} ARM resources carry tags — apply cost-allocation tag strategy"))
    else:
        out.append(_mk_finding("COST-005", status="should-fix",
            detail="No Bicep resources compiled — cannot evaluate tag coverage"))
    return out


def _check_cost_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if not tiers.get(3) or not sub or not rg:
        for fid in ("COST-101", "COST-102", "COST-103", "COST-104", "COST-105"):
            findings.append(_not_verified(fid, "Tier 3 Cost Management Reader unavailable"))
        return findings, evidence
    budgets = _az_json("consumption", "budget", "list", "--resource-group", rg, "--subscription", sub)
    evidence.append(EvidenceEntry(ref="E-COST-101", pillar="cost",
        description="Budgets on target RG", command=f"az consumption budget list -g {rg}",
        scope=f"sub={sub} rg={rg}", tier=3, captured_at=_utc_now(),
        result="ok" if budgets is not None else "error",
        notes=f"{len(budgets) if isinstance(budgets, list) else 0} budgets"))
    if budgets is None:
        findings.append(_not_verified("COST-101", "budget list failed"))
    else:
        findings.append(_mk_finding("COST-101",
            status="pass" if budgets else "must-fix",
            detail=f"{len(budgets)} budget(s) wired" if budgets else "No budget alerts wired",
            evidence_refs=["E-COST-101"]))
    for fid in ("COST-102", "COST-103"):
        findings.append(_not_verified(fid, "PAYG vs PTU usage analysis not implemented in v1 — see paygo-ptu-cost-analyzer"))
    # COST-104 orphaned check skipped
    findings.append(_not_verified("COST-104", "Orphaned resource detection not implemented in v1"))
    # COST-105 tags applied (compare bicep vs deployed)
    res = _az_json("resource", "list", "--resource-group", rg, "--subscription", sub)
    if isinstance(res, list) and res:
        with_tags = [r for r in res if r.get("tags")]
        findings.append(_mk_finding("COST-105",
            status="pass" if len(with_tags) >= 0.8 * len(res) else "should-fix",
            detail=f"{len(with_tags)}/{len(res)} resources tagged",
            evidence_refs=["E-COST-101"]))
    else:
        findings.append(_not_verified("COST-105", "resource list returned no items"))
    return findings, evidence


# ---- pillar 11: reliability -----------------------------------------------

def _check_reliability_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    spec = ctx.spec_text
    rto = ctx.spec_12.get("rto")
    rpo = ctx.spec_12.get("rpo")
    out.append(_mk_finding("REL-001",
        status="pass" if rto and rpo else "must-fix",
        detail=f"RTO={rto} RPO={rpo}" if (rto and rpo) else "SPEC sec 12 missing RTO/RPO"))
    # REL-002 multi-region if RTO < 4h
    needs_multi = False
    if rto and re.search(r"\b([0-3])\s*h\b|\b\d+\s*m\b|\b\d+\s*min", rto, re.I):
        needs_multi = True
    has_multi = bool(re.search(r"location\s*:\s*var\.\w+|secondaryLocation|geoRedundant|paired[_ -]?region", ctx.bicep_text + spec, re.I))
    if needs_multi:
        out.append(_mk_finding("REL-002",
            status="pass" if has_multi else "must-fix",
            detail="Multi-region plan present (RTO requires)" if has_multi else "RTO requires multi-region but no plan found"))
    else:
        out.append(_mk_finding("REL-002",
            status="pass" if has_multi else "should-fix",
            detail="Multi-region or unspecified RTO" if has_multi else "Single-region acceptable for declared RTO"))
    has_backup = bool(re.search(r"backup|recoveryServicesVault|Microsoft\.RecoveryServices", ctx.bicep_text + spec + ctx.docs_text, re.I))
    out.append(_mk_finding("REL-003",
        status="pass" if has_backup else "must-fix",
        detail="Backup/restore referenced" if has_backup else "No backup/restore documented"))
    has_caphost = bool(re.search(r"capacity[_ -]?host|caphost|capacityHost", spec + ctx.bicep_text, re.I))
    out.append(_mk_finding("REL-004",
        status="pass" if has_caphost else "should-fix",
        detail="Capacity host lifecycle considered" if has_caphost else "No capacity host lifecycle plan"))
    has_failures = bool(re.search(r"failure[_ -]?mode|chaos|fault[_ -]?injection", spec, re.I))
    out.append(_mk_finding("REL-005",
        status="pass" if has_failures else "should-fix",
        detail="Failure modes catalogued in SPEC" if has_failures else "No failure modes catalogued"))
    # REL-006 — graph-verified health probes on ACA/Web sites
    g = ctx.bicep_graph
    aca_apps = g.by_type("Microsoft.App/containerApps") if g else []
    web_sites = g.by_type("Microsoft.Web/sites") if g else []
    aca_with_probes = []
    for a in aca_apps:
        containers = (((a.get("properties") or {}).get("template") or {}).get("containers") or [])
        if any(c.get("probes") for c in containers):
            aca_with_probes.append(a)
    web_with_probes = [w for w in web_sites if (w.get("properties") or {}).get("siteConfig", {}).get("healthCheckPath")]
    total_compute = len(aca_apps) + len(web_sites)
    has_probes = (total_compute == 0) or (len(aca_with_probes) == len(aca_apps) and len(web_with_probes) == len(web_sites))
    if total_compute == 0:
        out.append(_mk_finding("REL-006", status="not-applicable",
            detail="No ACA or Web Sites declared in compiled ARM — no probes to configure"))
    elif has_probes:
        out.append(_mk_finding("REL-006", status="pass",
            detail=f"Health probes configured on all {total_compute} compute resource(s) "
                   f"(aca={len(aca_with_probes)}/{len(aca_apps)}, web={len(web_with_probes)}/{len(web_sites)})"))
    else:
        missing_aca = len(aca_apps) - len(aca_with_probes)
        missing_web = len(web_sites) - len(web_with_probes)
        out.append(_mk_finding("REL-006", status="should-fix",
            detail=f"Health probes missing on {missing_aca + missing_web} compute resource(s) "
                   f"(aca_missing={missing_aca}, web_missing={missing_web})"))
    # REL-007 — v0.3.0 NEW: restore drill artefact freshness (≤90d)
    drill_files = list(_glob_repo(ctx.root,
        "docs/**/restore-drill*.md", "docs/**/restore-drill*.json",
        "**/RESTORE-DRILL*.md", "evidence/**/restore-drill*.*"))
    if not drill_files:
        out.append(_mk_finding("REL-007", status="must-fix",
            detail="No restore-drill artefact found under docs/, evidence/. "
                   "Run `azqr restore-drill` (or your equivalent) and commit the report."))
    else:
        newest = max(drill_files, key=lambda p: p.stat().st_mtime)
        age_days = (datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime) / 86400
        body = _read_text(newest) or ""
        date_m = re.search(r"(\d{4}-\d{2}-\d{2})", body)
        if date_m:
            try:
                drill_dt = datetime.strptime(date_m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                age_body = (datetime.now(timezone.utc) - drill_dt).days
                age_days = min(age_days, age_body)
            except ValueError:
                pass
        if age_days <= 90:
            out.append(_mk_finding("REL-007", status="pass",
                detail=f"Restore-drill artefact `{newest.name}` is {int(age_days)} days old (≤90d)"))
        else:
            out.append(_mk_finding("REL-007", status="must-fix",
                detail=f"Restore-drill artefact `{newest.name}` is {int(age_days)} days old (>90d) — re-run drill"))
    return out


def _check_reliability_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if not tiers.get(1) or not sub or not rg:
        for fid in ("REL-101", "REL-102", "REL-103", "REL-104", "REL-105"):
            findings.append(_not_verified(fid, "Tier 1 Reader unavailable"))
        return findings, evidence
    aca = _az_json("containerapp", "list", "--resource-group", rg, "--subscription", sub)
    evidence.append(EvidenceEntry(ref="E-REL-103", pillar="reliability",
        description="Container apps inventory",
        command=f"az containerapp list -g {rg}",
        scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
        result="ok" if aca is not None else "error",
        notes=f"{len(aca) if isinstance(aca, list) else 0} container apps"))
    if isinstance(aca, list) and aca:
        bad = [a for a in aca if ((a.get("properties") or {}).get("template") or {}).get("scale", {}).get("minReplicas", 0) < 1]
        findings.append(_mk_finding("REL-103",
            status="pass" if not bad else "should-fix",
            detail=f"{len(bad)}/{len(aca)} container apps have minReplicas<1",
            evidence_refs=["E-REL-103"]))
    else:
        findings.append(_not_verified("REL-103", "no container apps in target RG"))
    rsv = _az_json("backup", "vault", "list", "--resource-group", rg, "--subscription", sub)
    if isinstance(rsv, list):
        findings.append(_mk_finding("REL-102",
            status="pass" if rsv else "should-fix",
            detail=f"{len(rsv)} backup vault(s)",
            evidence_refs=[]))
    else:
        findings.append(_not_verified("REL-102", "backup vault list failed"))
    # REL-008 — v0.3.0 NEW: at least one recoverable restore point on any RSV
    if isinstance(rsv, list) and rsv:
        rp_total = 0
        rp_sampled_vault = None
        for v in rsv:
            vname = v.get("name")
            if not vname:
                continue
            items = _az_json("backup", "item", "list",
                             "--resource-group", rg,
                             "--vault-name", vname,
                             "--subscription", sub) or []
            for it in items[:5]:
                cname = it.get("name")
                container_name = (it.get("properties") or {}).get("containerName") or ""
                if not cname or not container_name:
                    continue
                rps = _az_json("backup", "recoverypoint", "list",
                               "--resource-group", rg,
                               "--vault-name", vname,
                               "--container-name", container_name,
                               "--item-name", cname,
                               "--subscription", sub) or []
                rp_total += len(rps)
                if rps and not rp_sampled_vault:
                    rp_sampled_vault = vname
                if rp_total >= 1:
                    break
            if rp_total >= 1:
                break
        evidence.append(EvidenceEntry(ref="E-REL-008", pillar="reliability",
            description="Recovery points across RSV(s)",
            command="az backup recoverypoint list (sampled)",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok", notes=f"{rp_total} recoverable point(s), sampled vault={rp_sampled_vault}"))
        if rp_total >= 1:
            findings.append(_mk_finding("REL-008", status="pass",
                detail=f"{rp_total} recoverable point(s) found (sampled vault `{rp_sampled_vault}`)",
                evidence_refs=["E-REL-008"]))
        else:
            findings.append(_mk_finding("REL-008", status="must-fix",
                detail=f"No restore points across {len(rsv)} RSV(s) — backup is not yet recoverable",
                evidence_refs=["E-REL-008"]))
    else:
        findings.append(_not_verified("REL-008", "no RSV in target RG"))
    findings.append(_not_verified("REL-101", "Zone redundancy probe not implemented in v1"))
    findings.append(_not_verified("REL-104", "Multi-region presence probe not implemented in v1"))
    findings.append(_not_verified("REL-105", "Capacity host health probe not implemented in v1"))
    return findings, evidence


# ---- pillar 12: sre-handover ----------------------------------------------

def _check_sre_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    spec = ctx.spec_text
    docs = ctx.docs_text
    owner = ctx.spec_12.get("incident_owner")
    out.append(_mk_finding("SRE-001",
        status="pass" if owner else "must-fix",
        detail=f"Incident owner: {owner}" if owner else "SPEC sec 12 missing incident_owner"))
    runbook = bool(_glob_repo(ctx.root, "docs/**/runbook*.md", "docs/**/RUNBOOK*.md", "docs/**/operations*.md"))
    out.append(_mk_finding("SRE-002",
        status="pass" if runbook else "must-fix",
        detail="Runbook present in docs/" if runbook else "No runbook found in docs/"))
    sre_agent = bool(re.search(r"sre[_ -]?agent|sreagent|azure[_ -]?sre|sre.azure.com", spec + docs, re.I))
    out.append(_mk_finding("SRE-003",
        status="pass" if sre_agent else "should-fix",
        detail="Azure SRE Agent integration considered" if sre_agent else "No SRE Agent integration considered — see azure-sre-agent skill"))
    sev = bool(re.search(r"severity[_ -]?(matrix|levels?|sev[_ -]?[0-9])", spec + docs, re.I))
    out.append(_mk_finding("SRE-004",
        status="pass" if sev else "should-fix",
        detail="Severity matrix documented" if sev else "No severity matrix documented"))
    postmortem = bool(re.search(r"postmortem|post-mortem|after[_ -]?action", docs, re.I))
    out.append(_mk_finding("SRE-005",
        status="pass" if postmortem else "should-fix",
        detail="Postmortem template referenced" if postmortem else "No postmortem template referenced"))
    return out


def _check_sre_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if tiers.get(1) and sub and rg:
        ag = _az_json("monitor", "action-group", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-SRE-101", pillar="sre-handover",
            description="Action groups in target RG", command=f"az monitor action-group list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if ag is not None else "error",
            notes=f"{len(ag) if isinstance(ag, list) else 0} action groups"))
        if ag is None:
            findings.append(_not_verified("SRE-101", "action group list failed"))
        else:
            findings.append(_mk_finding("SRE-101",
                status="pass" if ag else "must-fix",
                detail=f"{len(ag)} action group(s)" if ag else "No action group → alerts cannot reach on-call",
                evidence_refs=["E-SRE-101"]))
        # SRE-102 SRE Agent resource presence
        sre_res = _az_json("resource", "list", "--resource-group", rg, "--subscription", sub, "--resource-type", "Microsoft.App/agents")
        findings.append(_mk_finding("SRE-102",
            status="pass" if isinstance(sre_res, list) and sre_res else "should-fix",
            detail=f"{len(sre_res)} SRE Agent resource(s)" if isinstance(sre_res, list) and sre_res else "No SRE Agent resource — see azure-sre-agent skill",
            evidence_refs=[]))
    else:
        findings.append(_not_verified("SRE-101", "Tier 1 Reader unavailable"))
        findings.append(_not_verified("SRE-102", "Tier 1 Reader unavailable"))
    sre_runbook = ctx.root / "docs" / "sre" / "runbook.md"
    sre_runbook_present = sre_runbook.is_file()
    findings.append(_mk_finding("SRE-103",
        status="pass" if sre_runbook_present else "must-fix",
        detail=("docs/sre/runbook.md present" if sre_runbook_present
                else "docs/sre/runbook.md missing — apply references/remediation-recipes/SRE-103.md")))
    # SRE-104 — v0.3.0 wired: activity log alerts on the target RG. Tier 1.
    if tiers.get(1) and sub and rg:
        ala = _az_json("monitor", "activity-log", "alert", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-SRE-104", pillar="sre-handover",
            description="Activity log alerts on target RG",
            command=f"az monitor activity-log alert list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if ala is not None else "error",
            notes=f"{len(ala) if isinstance(ala, list) else 0} alerts"))
        if ala is None:
            findings.append(_not_verified("SRE-104", "activity-log alert list failed"))
        else:
            findings.append(_mk_finding("SRE-104",
                status="pass" if ala else "should-fix",
                detail=f"{len(ala)} activity-log alert(s)" if ala else "No activity-log alerts on target RG",
                evidence_refs=["E-SRE-104"]))
    else:
        findings.append(_not_verified("SRE-104", "Tier 1 Reader unavailable"))
    # ---- v0.3.0 NEW: GOV-104/105 (Defender Secure Score + recommendations)
    if tiers.get(1) and sub:
        scores = _az_json("security", "secure-scores", "list", "--subscription", sub)
        floor = int(ctx.manifest.get("secure_score_floor", 60)) if isinstance(ctx.manifest, dict) else 60
        cur_pct: int | None = None
        if isinstance(scores, list) and scores:
            for s in scores:
                if s.get("name") == "ascScore" or "ascScore" in (s.get("id") or ""):
                    pct_f = ((s.get("properties") or {}).get("score") or {}).get("percentage")
                    if isinstance(pct_f, (int, float)):
                        cur_pct = int(round(pct_f * 100)) if pct_f <= 1 else int(round(pct_f))
                    break
            if cur_pct is None:
                pct_f = (((scores[0].get("properties") or {}).get("score") or {}).get("percentage"))
                if isinstance(pct_f, (int, float)):
                    cur_pct = int(round(pct_f * 100)) if pct_f <= 1 else int(round(pct_f))
        evidence.append(EvidenceEntry(ref="E-GOV-104", pillar="sre-handover",
            description="Defender Secure Score",
            command=f"az security secure-scores list --subscription {sub}",
            scope=f"sub={sub}", tier=1, captured_at=_utc_now(),
            result="ok" if scores is not None else "error",
            notes=f"score={cur_pct}, floor={floor}"))
        if cur_pct is None:
            findings.append(_not_verified("GOV-104", "Defender Secure Score not available (Defender for Cloud may be disabled)"))
        else:
            findings.append(_mk_finding("GOV-104",
                status="pass" if cur_pct >= floor else "should-fix",
                detail=f"Secure Score = {cur_pct}% (floor: {floor}%)",
                evidence_refs=["E-GOV-104"]))
        recs = _az_json("security", "task", "list", "--subscription", sub)
        top3 = []
        if isinstance(recs, list) and recs:
            for r in recs[:3]:
                rp = (r.get("properties") or {})
                top3.append(rp.get("recommendationDisplayName") or r.get("name") or "?")
        findings.append(_mk_finding("GOV-105",
            status="pass" if top3 else "not-applicable",
            detail=f"Top Defender recommendations: {top3}" if top3 else "No Defender recommendations surfaced",
            evidence_refs=["E-GOV-104"]))
    else:
        findings.append(_not_verified("GOV-104", "Tier 1 Reader unavailable"))
        findings.append(_not_verified("GOV-105", "Tier 1 Reader unavailable"))
    # ---- v0.3.0 NEW: GOV-201/202/203 (Azure Policy compliance)
    if tiers.get(1) and sub and rg:
        # Resource-group-scoped assignment view ("--disable-scope-strict-match" is not the right
        # path here; we list at RG scope which already includes inherited).
        assigns = _az_json("policy", "assignment", "list", "--resource-group", rg, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-GOV-201", pillar="sre-handover",
            description="Azure Policy assignments at RG scope",
            command=f"az policy assignment list -g {rg}",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if assigns is not None else "error",
            notes=f"{len(assigns) if isinstance(assigns, list) else 0} assignments"))
        if assigns is None:
            findings.append(_not_verified("GOV-201", "policy assignment list failed"))
            findings.append(_not_verified("GOV-202", "policy assignment list failed"))
            findings.append(_not_verified("GOV-203", "policy assignment list failed"))
        else:
            findings.append(_mk_finding("GOV-201",
                status="pass" if assigns else "must-fix",
                detail=f"{len(assigns)} policy assignment(s) at RG scope" if assigns else "No policy assignments at RG scope — enable a baseline initiative",
                evidence_refs=["E-GOV-201"]))
            # GOV-202: policy compliance state
            comp = _az_json("policy", "state", "summarize", "--resource-group", rg, "--subscription", sub)
            non_compliant = 0
            if isinstance(comp, dict):
                vals = (comp.get("results") or {}).get("resourceDetails") or []
                for v in vals:
                    if (v.get("complianceState") or "").lower() == "noncompliant":
                        non_compliant += int(v.get("count") or 0)
            elif isinstance(comp, list) and comp:
                for entry in comp:
                    if isinstance(entry, dict):
                        for v in (entry.get("results") or {}).get("resourceDetails", []) or []:
                            if (v.get("complianceState") or "").lower() == "noncompliant":
                                non_compliant += int(v.get("count") or 0)
            findings.append(_mk_finding("GOV-202",
                status="pass" if non_compliant == 0 else "should-fix",
                detail=f"{non_compliant} non-compliant resource(s) for assigned policies",
                evidence_refs=["E-GOV-201"]))
            # GOV-203: sane-default initiatives (ASB / Microsoft cloud security benchmark)
            asb = [a for a in assigns
                   if "cloud-security-benchmark" in (a.get("name") or "").lower()
                   or "asb" in (a.get("displayName") or "").lower()
                   or "azure security benchmark" in (a.get("displayName") or "").lower()]
            findings.append(_mk_finding("GOV-203",
                status="pass" if asb else "should-fix",
                detail=f"{len(asb)} ASB/MCSB initiative(s) assigned" if asb else "No Azure Security Benchmark / MCSB initiative assigned at RG scope",
                evidence_refs=["E-GOV-201"]))
    else:
        for fid in ("GOV-201", "GOV-202", "GOV-203"):
            findings.append(_not_verified(fid, "Tier 1 Reader unavailable"))
    return findings, evidence


# ---- pillar 13: model-lifecycle -------------------------------------------

MODEL_VERSION_RE = re.compile(r"(?:modelVersion|model_version|version)\s*:\s*['\"]?(\d{4}-\d{2}-\d{2}|\d+\.\d+(?:\.\d+)?|latest)['\"]?", re.I)


def _check_model_lifecycle_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    spec = ctx.spec_text
    g = ctx.bicep_graph
    # MDL-001 — graph-verified: every model deployment must declare an
    # explicit `model.version` that is not `latest`.
    deployments = g.by_type("Microsoft.CognitiveServices/accounts/deployments") if g else []
    if not deployments:
        out.append(_mk_finding("MDL-001", status="must-fix",
            detail="No Microsoft.CognitiveServices/accounts/deployments declared in compiled ARM — cannot pin a model that doesn't exist"))
    else:
        pinned, floating, missing = [], [], []
        for d in deployments:
            model = ((d.get("properties") or {}).get("model") or {})
            v = model.get("version")
            name = d.get("name") or "<unnamed>"
            if not v:
                missing.append(name)
            elif str(v).strip().lower() == "latest":
                floating.append(name)
            else:
                pinned.append(f"{name}@{v}")
        if floating or missing:
            problems = []
            if floating:
                problems.append(f"{len(floating)} use `latest`: {floating}")
            if missing:
                problems.append(f"{len(missing)} have no model.version: {missing}")
            out.append(_mk_finding("MDL-001", status="must-fix",
                detail="Model deployments not pinned: " + "; ".join(problems)))
        else:
            out.append(_mk_finding("MDL-001", status="pass",
                detail=f"All {len(deployments)} model deployment(s) pinned: {pinned}"))
    out.append(_mk_finding("MDL-002",
        status="pass" if re.search(r"deprecat|retir", spec, re.I) else "should-fix",
        detail="Deprecation plan mentioned in SPEC" if re.search(r"deprecat|retir", spec, re.I) else "No deprecation plan mentioned"))
    out.append(_mk_finding("MDL-003",
        status="pass" if re.search(r"canary|blue[_ -]?green|shadow", spec + ctx.docs_text, re.I) else "should-fix",
        detail="Upgrade canary/blue-green referenced" if re.search(r"canary|blue[_ -]?green|shadow", spec + ctx.docs_text, re.I) else "No upgrade canary process documented"))
    out.append(_mk_finding("MDL-004",
        status="pass" if re.search(r"quota|capacity|tpm|rpm", spec, re.I) else "must-fix",
        detail="Capacity/quota considered in SPEC" if re.search(r"quota|capacity|tpm|rpm", spec, re.I) else "No quota / capacity sizing in SPEC"))
    out.append(_mk_finding("MDL-005",
        status="pass" if re.search(r"fallback|secondary[_ -]?model", spec + ctx.src_text, re.I) else "should-fix",
        detail="Fallback model strategy referenced" if re.search(r"fallback|secondary[_ -]?model", spec + ctx.src_text, re.I) else "No fallback model strategy"))
    out.append(_mk_finding("MDL-006",
        status="pass" if re.search(r"rate[_ -]?limit|retry|backoff|429", ctx.src_text, re.I) else "should-fix",
        detail="Rate-limit handling in code" if re.search(r"rate[_ -]?limit|retry|backoff|429", ctx.src_text, re.I) else "No rate-limit / 429 handling detected"))
    out.append(_mk_finding("MDL-007",
        status="pass" if re.search(r"residency|data[_ -]?location|region[_ -]?policy", spec, re.I) else "should-fix",
        detail="Residency / region policy mentioned" if re.search(r"residency|data[_ -]?location|region[_ -]?policy", spec, re.I) else "No region/residency policy in SPEC"))
    out.append(_mk_finding("MDL-008",
        status="pass" if re.search(r"index[_ -]?refresh|reindex|knowledge.*update", spec + ctx.docs_text, re.I) else "should-fix",
        detail="Index refresh cadence declared" if re.search(r"index[_ -]?refresh|reindex|knowledge.*update", spec + ctx.docs_text, re.I) else "No knowledge index refresh cadence"))
    # MDL-009 — Foundry project-level RBAC declared
    # Look for a roleAssignment whose scope is a Foundry account/project.
    role_assigns = g.by_type("Microsoft.Authorization/roleAssignments") if g else []
    cs_accounts = g.by_type("Microsoft.CognitiveServices/accounts") if g else []
    project_scoped = [r for r in role_assigns
                      if "cognitiveservices/accounts" in (((r.get("properties") or {}).get("scope") or r.get("scope") or "")).lower()
                      or "projects" in (r.get("name") or "").lower()]
    if not cs_accounts:
        out.append(_mk_finding("MDL-009", status="not-applicable",
            detail="No Foundry / Cognitive Services accounts declared — no project-level RBAC required"))
    elif project_scoped:
        out.append(_mk_finding("MDL-009", status="pass",
            detail=f"{len(project_scoped)} role assignment(s) scoped to Foundry accounts/projects"))
    else:
        out.append(_mk_finding("MDL-009", status="should-fix",
            detail=f"No project-level role assignment found on declared Foundry accounts "
                   f"({len(cs_accounts)} account(s), {len(role_assigns)} total role assignments)"))
    # MDL-010 — knowledge index private-endpointed (if used)
    # AI Search is the v0.3.0 supported knowledge-index backend. If declared,
    # must have publicNetworkAccess=disabled AND at least one private endpoint.
    search_svcs = g.by_type("Microsoft.Search/searchServices") if g else []
    if not search_svcs:
        out.append(_mk_finding("MDL-010", status="not-applicable",
            detail="No Microsoft.Search/searchServices declared — no knowledge index to protect"))
    else:
        public_off = [s for s in search_svcs if (s.get("properties") or {}).get("publicNetworkAccess", "Enabled").lower() == "disabled"]
        if len(public_off) == len(search_svcs):
            out.append(_mk_finding("MDL-010", status="pass",
                detail=f"All {len(search_svcs)} search service(s) declare publicNetworkAccess=disabled"))
        else:
            out.append(_mk_finding("MDL-010", status="should-fix",
                detail=f"{len(search_svcs) - len(public_off)}/{len(search_svcs)} search service(s) "
                       f"have publicNetworkAccess≠Disabled — knowledge index reachable from public internet"))
    # MDL-011 — agent thread policy declared in SPEC
    has_thread_policy = bool(re.search(r"agent[_ -]?thread[_ -]?(policy|retention|lifecycle)|thread[_ -]?retention|conversation[_ -]?retention",
                                       spec + ctx.docs_text, re.I))
    out.append(_mk_finding("MDL-011",
        status="pass" if has_thread_policy else "should-fix",
        detail="Agent thread retention/policy declared" if has_thread_policy
               else "No agent thread retention/policy mentioned in SPEC or docs"))
    return out


def _check_model_lifecycle_live(ctx: RepoContext, tiers: dict[int, bool], sub: str | None, rg: str | None) -> tuple[list[Finding], list[EvidenceEntry]]:
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    if not tiers.get(1) or not sub or not rg:
        for fid in ("MDL-101", "MDL-102", "MDL-103", "MDL-104"):
            findings.append(_not_verified(fid, "Tier 1 Reader unavailable"))
        return findings, evidence
    accts = _az_json("cognitiveservices", "account", "list", "--resource-group", rg, "--subscription", sub)
    if not accts:
        for fid in ("MDL-101", "MDL-102", "MDL-103"):
            findings.append(_mk_finding(fid, status="not-applicable",
                detail="No Cognitive/Foundry accounts in target RG"))
    else:
        all_deps: list[dict] = []
        for a in accts:
            name = a.get("name")
            deps = _az_json("cognitiveservices", "account", "deployment", "list",
                            "--name", name, "--resource-group", rg, "--subscription", sub) or []
            all_deps.extend(deps)
        evidence.append(EvidenceEntry(ref="E-MDL-101", pillar="model-lifecycle",
            description="Model deployments across all Foundry accounts in RG",
            command="az cognitiveservices account deployment list (each account)",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok", notes=f"{len(all_deps)} deployments across {len(accts)} accounts"))
        floating = [d for d in all_deps if str(((d.get("properties") or {}).get("model") or {}).get("version", "")).lower() in ("", "latest")]
        findings.append(_mk_finding("MDL-101",
            status="must-fix" if floating else "pass",
            detail=f"{len(floating)}/{len(all_deps)} deployments using floating version",
            evidence_refs=["E-MDL-101"]))
        findings.append(_not_verified("MDL-102", "Deprecated-model cross-reference not implemented in v1"))
        findings.append(_mk_finding("MDL-103",
            status="pass" if all_deps else "should-fix",
            detail=f"{len(all_deps)} deployment(s) observed",
            evidence_refs=["E-MDL-101"]))
    findings.append(_not_verified("MDL-104", "Tier 2 KQL for 429 breaches not implemented in v1"))
    # ---- v0.3.0 NEW: MDL-110/111 quota pre-flight (Tier 1) + GOV-101 Defender for AI
    if tiers.get(1) and sub and rg:
        usage = _az_json("cognitiveservices", "usage", "list", "--location", "global", "--subscription", sub)
        if usage is None and accts:
            # Try per-account region.
            first_loc = (accts[0].get("location") if accts else None)
            if first_loc:
                usage = _az_json("cognitiveservices", "usage", "list", "--location", first_loc, "--subscription", sub)
        evidence.append(EvidenceEntry(ref="E-MDL-110", pillar="model-lifecycle",
            description="Cognitive Services quota usage",
            command="az cognitiveservices usage list --location <region>",
            scope=f"sub={sub} rg={rg}", tier=1, captured_at=_utc_now(),
            result="ok" if usage is not None else "error",
            notes=f"{len(usage) if isinstance(usage, list) else 0} usage entries"))
        if not isinstance(usage, list) or not usage:
            findings.append(_not_verified("MDL-110", "cognitive services usage list returned no data"))
            findings.append(_not_verified("MDL-111", "cognitive services usage list returned no data"))
        else:
            saturated = []
            for u in usage:
                cur = float(u.get("currentValue") or 0)
                lim = float(u.get("limit") or 0)
                nm = (u.get("name") or {}).get("value") or u.get("name") or "?"
                if lim and cur / lim >= 0.8:
                    saturated.append(f"{nm}={int(cur)}/{int(lim)}")
            if saturated:
                findings.append(_mk_finding("MDL-110", status="must-fix",
                    detail=f"TPM headroom exhausted (≥80%): {saturated}",
                    evidence_refs=["E-MDL-110"]))
            else:
                findings.append(_mk_finding("MDL-110", status="pass",
                    detail=f"All {len(usage)} quota entries below 80% saturation",
                    evidence_refs=["E-MDL-110"]))
            cap_avail = [u for u in usage if float(u.get("limit") or 0) - float(u.get("currentValue") or 0) > 0]
            findings.append(_mk_finding("MDL-111",
                status="pass" if cap_avail else "should-fix",
                detail=f"{len(cap_avail)}/{len(usage)} quota entries have headroom",
                evidence_refs=["E-MDL-110"]))
    else:
        findings.append(_not_verified("MDL-110", "Tier 1 Reader unavailable for quota probe"))
        findings.append(_not_verified("MDL-111", "Tier 1 Reader unavailable for quota probe"))
    # GOV-101: Defender for AI Services plan enabled
    if tiers.get(1) and sub:
        pricings = _az_json("security", "pricing", "list", "--subscription", sub)
        ai_pricing = None
        kv_pricing = None
        srv_pricing = None
        if isinstance(pricings, list):
            for p in pricings:
                nm = (p.get("name") or "").lower()
                if "ai" in nm or "cognitive" in nm:
                    ai_pricing = p
                elif "keyvaults" in nm or "key vault" in nm:
                    kv_pricing = p
                elif "servers" in nm or "containers" in nm or "appservices" in nm:
                    if not srv_pricing:
                        srv_pricing = p
        elif isinstance(pricings, dict):
            for nm, p in (pricings or {}).items():
                if "ai" in nm.lower() or "cognitive" in nm.lower():
                    ai_pricing = p
        evidence.append(EvidenceEntry(ref="E-GOV-101", pillar="model-lifecycle",
            description="Defender for Cloud pricing plans",
            command=f"az security pricing list --subscription {sub}",
            scope=f"sub={sub}", tier=1, captured_at=_utc_now(),
            result="ok" if pricings is not None else "error",
            notes="present" if ai_pricing else "missing"))
        if ai_pricing is None:
            findings.append(_not_verified("GOV-101", "No AI Services Defender plan entry returned"))
        else:
            tier = ((ai_pricing.get("properties") or {}).get("pricingTier") or ai_pricing.get("pricingTier") or "Free")
            findings.append(_mk_finding("GOV-101",
                status="pass" if str(tier).lower() == "standard" else "should-fix",
                detail=f"Defender for AI Services pricingTier = {tier}",
                evidence_refs=["E-GOV-101"]))
        # GOV-102 / GOV-103 are in other pillars but we surface here for
        # completeness — runners under other pillars will append the canonical
        # not-verified placeholder otherwise.
    else:
        findings.append(_not_verified("GOV-101", "Tier 1 Reader unavailable"))
    return findings, evidence


# ---------------------------------------------------------------------------
# Pillar runner registry
# ---------------------------------------------------------------------------


PILLAR_RUNNERS = {
    "network-posture": (_check_network_static, _check_network_live),
    "agent-governance": (_check_agt_static, _check_agt_live),
    "identity-access": (_check_identity_static, _check_identity_live),
    "secrets": (_check_secrets_static, _check_secrets_live),
    "observability": (_check_observability_static, _check_observability_live),
    "continuous-evals": (_check_evals_static, _check_evals_live),
    "responsible-ai": (_check_rai_static, _check_rai_live),
    "hitl-audit": (_check_hitl_static, _check_hitl_live),
    "supply-chain": (_check_supply_static, _check_supply_live),
    "cost": (_check_cost_static, _check_cost_live),
    "reliability": (_check_reliability_static, _check_reliability_live),
    "sre-handover": (_check_sre_static, _check_sre_live),
    "model-lifecycle": (_check_model_lifecycle_static, _check_model_lifecycle_live),
}


def _run_pillar(
    pillar: str,
    ctx: RepoContext,
    static_only: bool,
    tiers: dict[int, bool],
    sub: str | None,
    rg: str | None,
    resolved_posture: str,
    agt_profile: str,
    quick: bool,
) -> tuple[list[Finding], list[EvidenceEntry]]:
    """Dispatch to the right runner pair. Returns (findings, evidence)."""
    static_fn, live_fn = PILLAR_RUNNERS[pillar]
    # Stash resolved_posture on ctx so cross-pillar posture-aware static checks
    # (e.g. IAM-005 escalation when posture demands an auth-fronted surface)
    # can read it without a signature change.
    ctx.resolved_posture = resolved_posture
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    # Each pillar's static signature varies slightly; handle uniformly
    if pillar == "network-posture":
        findings.extend(static_fn(ctx, resolved_posture))
    elif pillar == "agent-governance":
        findings.extend(static_fn(ctx, agt_profile))
        # v4-preview deep checks: gated to fire only when profile is v4_preview.
        # Lives in _check_agt_static_v4 — never emitted by v3_7 / none / auto-as-v3_7.
        if agt_profile == "v4_preview":
            findings.extend(_check_agt_static_v4(ctx))
    else:
        findings.extend(static_fn(ctx))
    if not static_only:
        live_findings, live_evidence = live_fn(ctx, tiers, sub, rg) if pillar != "network-posture" else live_fn(ctx, tiers, resolved_posture, sub, rg)
        # v4-preview live deep checks: gated to fire only when profile is v4_preview.
        if pillar == "agent-governance" and agt_profile == "v4_preview":
            v4_live_findings, v4_live_evidence = _check_agt_live_v4(ctx, tiers, sub, rg)
            live_findings = list(live_findings) + v4_live_findings
            live_evidence = list(live_evidence) + v4_live_evidence
        # quick mode: only the first live check
        if quick and live_findings:
            kept = live_findings[:1]
            for f in live_findings[1:]:
                kept.append(_not_verified(f.id, "Skipped in --quick mode"))
            live_findings = kept
        findings.extend(live_findings)
        evidence.extend(live_evidence)
    else:
        # Static-only mode: surface every live finding for this pillar as not-verified
        # so the manifest shape stays stable and the report shows what was skipped.
        existing_ids = {f.id for f in findings}
        for fid, meta in FINDING_CATALOG.items():
            if meta["pillar"] == pillar and meta["tier"] > 0 and fid not in existing_ids:
                # v4-only live findings (AGT-V4-*) must not leak into v3.7 / none / static-mode runs
                # when the active profile is not v4_preview.
                if pillar == "agent-governance" and fid.startswith("AGT-V4-") and agt_profile != "v4_preview":
                    continue
                findings.append(_not_verified(fid, "Skipped — running in --static mode"))
    return findings, evidence


# ---------------------------------------------------------------------------
# AGT profile detection
# ---------------------------------------------------------------------------


def _detect_agt_profile(ctx: RepoContext, requested: str) -> str:
    """Auto-detect AGT profile from scoped artefacts.

    Detection scope is restricted to implementation artefacts (deps files, policy
    YAMLs, workflow YAMLs, source `.py/.ts/.tsx`) — docs/README/SPEC prose are
    EXPLICITLY EXCLUDED so that mentions of "AGT v4" in markdown do not flip
    detection. See docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md
    for the recon evidence backing the v4 signals.
    """
    if requested != "auto":
        return requested
    src = ctx.src_text
    # heuristics — capability based, version agnostic
    has_legacy_import = bool(re.search(r"foundry[-_]agt|from\s+agt\b|import\s+agt\b|@foundry/agt", src, re.I))
    has_v4_import = bool(re.search(r"agent_governance_toolkit_(?:core|runtime|sre|cli)|from\s+agent_governance_toolkit", src, re.I))
    if not has_legacy_import and not has_v4_import:
        return "none"
    # v4 signals (any one is sufficient): scan only scoped artefact files,
    # NOT docs/README/SPEC prose. See _v4_scoped_files for the exact globs.
    scoped = _v4_scoped_files(ctx.root)
    if _v4_signal_present(scoped["deps"], V4_DIST_REGEX)[0]:
        return "v4_preview"
    if _v4_signal_present(scoped["policies"], V4_POLICY_REGEX)[0]:
        return "v4_preview"
    if has_v4_import:
        return "v4_preview"
    if _v4_signal_present(scoped["src_python"], V4_DYNAMIC_REGEX)[0]:
        return "v4_preview"
    if _v4_signal_present(scoped["policies"], V4_DYNAMIC_REGEX)[0]:
        return "v4_preview"
    return "v3_7"


# ---------------------------------------------------------------------------
# Score computation + recommendation
# ---------------------------------------------------------------------------


PILLAR_WEIGHTS = {pid: 1 for pid in PILLAR_IDS}
PILLAR_WEIGHTS["network-posture"] = 2
PILLAR_WEIGHTS["agent-governance"] = 2
PILLAR_WEIGHTS["secrets"] = 2
PILLAR_WEIGHTS["observability"] = 2


def _score_pillar(findings: list[Finding], include_experimental: bool = False) -> tuple[str, int, int, int]:
    """Return (pillar_status, score_percent, max_score, verification_debt_count).

    v0.3.0 scoring contract (rubber-duck-tightened):
      pass         → +4 (full credit)
      waived       → +3 (compensating control accepted; not full credit)
      should-fix   → +1 (partial; gap is real but not blocking)
      must-fix     → +0 (blocking)
      not-verified → +0 (verification debt — was +2 inflating to 50% in v0.2.0)

    The fourth return value is `verification_debt` — the count of
    not-verified findings in this pillar. The caller surfaces this as a
    first-class exec-summary metric so the gap "we couldn't check this"
    no longer hides inside the percent.

    Findings flagged ``experimental: True`` in FINDING_CATALOG are excluded
    from scoring unless the caller passes ``include_experimental=True``
    (i.e. the user opted in via ``--include-experimental``).
    """
    if not findings:
        return "not-applicable", 0, 0, 0
    if not include_experimental:
        findings = [f for f in findings
                    if not (FINDING_CATALOG.get(f.id) or {}).get("experimental")]
    relevant = [f for f in findings if f.status != "not-applicable"]
    if not relevant:
        return "not-applicable", 0, 0, 0
    max_score = len(relevant) * 4
    earned = 0
    has_must = False
    has_should = False
    verification_debt = 0
    for f in relevant:
        if f.status == "pass":
            earned += 4
        elif f.status == "waived":
            earned += 3
        elif f.status == "should-fix":
            earned += 1
            has_should = True
        elif f.status == "fail":
            has_should = True
        elif f.status == "must-fix":
            has_must = True
        elif f.status == "not-verified":
            verification_debt += 1
            # +0 — verification debt no longer inflates score
    pct = (earned * 100) // max_score if max_score else 0
    if has_must:
        st = "red"
    elif has_should or pct < 80:
        st = "amber"
    else:
        st = "green"
    return st, pct, 100, verification_debt


def _apply_waivers(findings: list[Finding], waivers: dict[str, dict]) -> list[Finding]:
    out: list[Finding] = []
    for f in findings:
        if f.id in waivers and f.status in ("must-fix", "should-fix"):
            wf = Finding(**asdict(f))
            wf.status = "waived"
            wf.waiver_id = waivers[f.id].get("id")
            out.append(wf)
        else:
            out.append(f)
    return out


def _hard_gate_would_fail(findings_raw: list[Finding]) -> bool:
    return any(f.status == "must-fix" for f in findings_raw)


def _go_live_recommendation(raw_must: bool, waived_must: bool,
                             verification_coverage_pct: int,
                             waived_score_pct: int,
                             red_pillar_count: int) -> str:
    """Recommendation taxonomy (rubber-duck-tightened):
      - not_ready: unwaived must-fix remains
      - ready_with_waivers: must-fix exists but every one is waived
      - ready_with_unverified_risk: no unwaived must-fix, but verification
        coverage <50% (severe) or <80% (moderate, only triggers if score is
        otherwise clean)
      - ready_with_residual_risk: no unwaived must-fix, coverage OK, but
        weighted score is <80% OR one or more pillars are red. Architecture
        review still has open work.
      - ready: no unwaived must-fix AND coverage ≥80% AND score ≥80% AND no
        red pillars. The plain `ready` label is conservative on purpose —
        "READY" is the word executives will quote, so it must mean something.
    Waivers never lift the recommendation above ready_with_waivers."""
    if raw_must and waived_must:
        return "not_ready"
    if raw_must and not waived_must:
        return "ready_with_waivers"
    # No unwaived must-fix → grade the rest
    if verification_coverage_pct < 50:
        return "ready_with_unverified_risk"
    if waived_score_pct < 80 or red_pillar_count > 0:
        return "ready_with_residual_risk"
    if verification_coverage_pct < 80:
        return "ready_with_unverified_risk"
    return "ready"


def _evidence_confidence(verification_coverage_pct: int) -> str:
    """Confidence band the executive summary shows alongside score.

    HIGH ≥80% — verified evidence covers most checks
    MEDIUM 50–79% — verified evidence covers about half
    LOW <50% — most evidence is `not-verified`; treat conclusions as advisory
    """
    if verification_coverage_pct >= 80:
        return "HIGH"
    if verification_coverage_pct >= 50:
        return "MEDIUM"
    return "LOW"


def _compute_evidence_freshness(
    evidence: list["EvidenceEntry"],
    checked_at: str,
    freshness_hours: int,
    warnings: list[str] | None = None,
) -> dict:
    """Compute the `evidence_freshness` manifest block.

    Always returns the same shape:

        {
          "oldest_captured_at": ISO 8601 UTC | None,
          "newest_captured_at": ISO 8601 UTC | None,
          "span_hours":          int | None,    # newest - oldest, hours, floored
          "stale":               bool,          # (checked_at - oldest) > freshness_hours, STRICT >
          "threshold_hours":     int,           # echoed for downstream consumers
        }

    Static-mode (no evidence) and unparseable-only cases both return null
    timestamps with `stale: false`. When `warnings` is provided, this function
    appends explanatory entries for: unparseable rows, all-unparseable runs,
    clock skew (captured_at after checked_at), and unparseable `checked_at`.

    Strict `>` comparison: an evidence row exactly `freshness_hours` old is
    NOT stale. Matches the safe-check freshness convention.
    """
    block: dict = {
        "oldest_captured_at": None,
        "newest_captured_at": None,
        "span_hours": None,
        "stale": False,
        "threshold_hours": freshness_hours,
    }
    if not evidence:
        return block

    parsed: list[tuple[datetime, str]] = []
    skipped = 0
    for e in evidence:
        s = getattr(e, "captured_at", "") or ""
        try:
            t = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            parsed.append((t, s))
        except (ValueError, TypeError):
            skipped += 1

    if skipped and warnings is not None:
        warnings.append(
            f"freshness: skipped {skipped} evidence row(s) with unparseable captured_at"
        )

    if not parsed:
        if warnings is not None:
            warnings.append(
                "freshness could not be evaluated: all evidence rows had "
                "unparseable captured_at"
            )
        return block

    parsed.sort()
    oldest_dt, oldest_str = parsed[0]
    newest_dt, newest_str = parsed[-1]
    span_seconds = (newest_dt - oldest_dt).total_seconds()
    block["oldest_captured_at"] = oldest_str
    block["newest_captured_at"] = newest_str
    block["span_hours"] = int(max(0.0, span_seconds) // 3600)

    try:
        checked_dt = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        if warnings is not None:
            warnings.append(
                f"freshness: checked_at not ISO-8601 ({checked_at!r}); "
                "staleness undecidable"
            )
        return block

    age_seconds = (checked_dt - oldest_dt).total_seconds()
    if age_seconds < 0:
        if warnings is not None:
            warnings.append(
                f"freshness: future captured_at detected (clock skew) — newest "
                f"evidence is {-age_seconds / 3600:.1f}h ahead of checked_at"
            )
        return block

    age_hours = age_seconds / 3600.0
    block["stale"] = age_hours > freshness_hours
    return block


# ---------------------------------------------------------------------------
# JSON emitter
# ---------------------------------------------------------------------------


def _finding_to_dict(finding: Finding) -> dict:
    row = asdict(finding)
    for key in ("override_customer", "override_reason"):
        val = getattr(finding, key, None)
        if val is not None:
            row[key] = val
    return row


def _build_manifest(
    posture: dict,
    pillar_results_raw: dict[str, list[Finding]],
    pillar_results_waived: dict[str, list[Finding]],
    evidence: list[EvidenceEntry],
    not_verified: list[Finding],
    waivers: dict[str, dict],
    tiers: dict[int, bool],
    warnings: list[str],
    agt_profile: str,
    safe_check_ref: dict,
    quick: bool,
    static_only: bool,
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
    include_experimental: bool = False,
) -> dict:
    # Capture run-end timestamp once so the freshness math and the manifest
    # field agree exactly (prevents exact-boundary flake).
    checked_at = _utc_now()
    pillars_block = []
    raw_total_score = 0
    raw_max = 0
    waived_total = 0
    waived_max = 0
    verification_debt_by_pillar: dict[str, int] = {}
    total_verification_debt = 0

    def _filter_exp(findings: list[Finding]) -> list[Finding]:
        if include_experimental:
            return findings
        return [f for f in findings
                if not (FINDING_CATALOG.get(f.id) or {}).get("experimental")]

    for pid in PILLAR_IDS:
        raw_fs = pillar_results_raw.get(pid, [])
        w_fs = pillar_results_waived.get(pid, [])
        r_status, r_score, r_max_s, r_debt = _score_pillar(raw_fs, include_experimental=include_experimental)
        w_status, w_score, w_max_s, w_debt = _score_pillar(w_fs, include_experimental=include_experimental)
        weight = PILLAR_WEIGHTS[pid]
        raw_total_score += r_score * weight
        raw_max += 100 * weight if r_max_s else 0
        waived_total += w_score * weight
        waived_max += 100 * weight if w_max_s else 0
        verification_debt_by_pillar[pid] = w_debt
        total_verification_debt += w_debt
        pillars_block.append({
            "pillar": pid,
            "title": PILLAR_TITLES[pid],
            "status_raw": r_status,
            "status_with_waivers": w_status,
            "score_raw": r_score,
            "score_with_waivers": w_score,
            "verification_debt": w_debt,
            "findings": [_finding_to_dict(f) for f in _filter_exp(w_fs)],
        })
    raw_pct = (raw_total_score * 100) // raw_max if raw_max else 0
    waived_pct = (waived_total * 100) // waived_max if waived_max else 0
    raw_must = any(any(f.status == "must-fix" for f in raw_fs) for raw_fs in pillar_results_raw.values())
    waived_must = any(any(f.status == "must-fix" for f in w_fs) for w_fs in pillar_results_waived.values())
    # Verification coverage (v0.3.0 fix): of the verifiable findings
    # (pass + should-fix + must-fix + not-verified + waived), what fraction
    # actually has a verified status? `not-applicable` is OUT of the
    # denominator — we can't claim coverage for things that don't apply.
    # `waived` IS verified (the operator explicitly inspected it and
    # accepted the risk). Pre-v0.3.0 this math counted `waived` AND
    # excluded `not-applicable`, but treated `not-verified` as 50% credit
    # in the score itself — fixed in `_score_pillar`.
    scoreable = [f for fs in pillar_results_waived.values() for f in fs
                 if f.status in ("pass", "fail", "should-fix", "must-fix", "not-verified", "waived")]
    not_verified_count = sum(1 for f in scoreable if f.status == "not-verified")
    verified_count = len(scoreable) - not_verified_count
    coverage_pct = (verified_count * 100) // len(scoreable) if scoreable else 0
    rec = _go_live_recommendation(
        raw_must,
        waived_must,
        coverage_pct,
        waived_pct,
        sum(1 for p in pillars_block if p["status_with_waivers"] == "red"),
    )
    # Per-evidence freshness (issue #22). Append warnings in-place so they
    # land in the JSON manifest's warnings list.
    evidence_freshness = _compute_evidence_freshness(
        evidence, checked_at, freshness_hours, warnings=warnings,
    )
    return {
        "schema_version": "1.0",
        "tool": "threadlight-production-ready",
        "tool_version": VERSION,
        "checked_at": checked_at,
        "mode": "static" if static_only else ("quick" if quick else "full"),
        "agt_profile": agt_profile,
        "posture": posture,
        "score": {
            "raw_percent": raw_pct,
            "with_waivers_percent": waived_pct,
        },
        "verification_coverage": {
            "verified": verified_count,
            "total_scoreable": len(scoreable),
            "percent": coverage_pct,
        },
        "verification_debt": {
            "total": total_verification_debt,
            "by_pillar": verification_debt_by_pillar,
        },
        "go_live_recommendation": rec,
        "would_fail_hard_gate": raw_must,
        "include_experimental": include_experimental,
        "permission_tiers": {str(k): v for k, v in tiers.items()},
        "warnings": warnings,
        "safe_check_reference": safe_check_ref,
        "pillars": pillars_block,
        "evidence_register": [asdict(e) for e in evidence],
        "evidence_freshness": evidence_freshness,
        "waivers": list(waivers.values()),
        "not_verified_count": not_verified_count,
    }


# ---------------------------------------------------------------------------
# Markdown report renderer
# ---------------------------------------------------------------------------


STATUS_ICON = {
    "green": "🟢",
    "amber": "🟡",
    "red": "🔴",
    "not-applicable": "⚪",
    "pass": "✅",
    "fail": "❌",
    "should-fix": "⚠️",
    "must-fix": "❌",
    "not-verified": "❓",
    "waived": "🛡️",
}


def _render_report(manifest: dict, posture: dict, pillar_results_waived: dict[str, list[Finding]],
                   evidence: list[EvidenceEntry], waivers: dict[str, dict], warnings: list[str]) -> str:
    out: list[str] = []
    out.append(f"# Production-Readiness Report")
    out.append("")
    out.append(f"*Generated by `threadlight-production-ready` v{VERSION} at {manifest['checked_at']}*")
    out.append("")
    # 1. Executive summary
    out.append("## 1. Executive summary")
    out.append("")
    rec = manifest["go_live_recommendation"]
    rec_label = {
        "ready": "🟢 READY",
        "ready_with_waivers": "🟡 READY WITH WAIVERS",
        "ready_with_residual_risk": "🟡 READY WITH RESIDUAL RISK",
        "ready_with_unverified_risk": "🟡 READY WITH UNVERIFIED RISK",
        "not_ready": "🔴 NOT READY",
    }[rec]
    cov = manifest["verification_coverage"]
    confidence = _evidence_confidence(cov["percent"])
    out.append(f"- **Go-live recommendation:** {rec_label}")
    out.append(f"- **Raw score:** {manifest['score']['raw_percent']}%   **With waivers:** {manifest['score']['with_waivers_percent']}%")
    out.append(f"- **Verification coverage:** {cov['verified']}/{cov['total_scoreable']} checks verified ({cov['percent']}%) — the rest are `not-verified`")
    out.append(f"- **Evidence confidence:** {confidence} — `HIGH` ≥80%, `MEDIUM` 50–79%, `LOW` <50%. See Appendix for permission-tier breakdown.")
    out.append(f"- **Resolved posture:** `{posture['resolved']}` (declared: `{posture['declared'] or 'unset'}`, detected: `{posture['detected'] or 'none'}`)")
    out.append(f"- **Mode:** {manifest['mode']}   **AGT profile:** {manifest['agt_profile']}")
    out.append(f"- **Would fail a hard gate?** {'YES' if manifest['would_fail_hard_gate'] else 'no'}")
    out.append(f"- **Not-verified findings:** {manifest['not_verified_count']} (live probes that could not run — see Appendix)")
    debt = manifest.get("verification_debt") or {}
    debt_total = debt.get("total", manifest.get("not_verified_count", 0))
    if debt_total:
        top_debt = sorted(
            ((p, n) for p, n in (debt.get("by_pillar") or {}).items() if n),
            key=lambda kv: -kv[1],
        )[:3]
        top_label = ", ".join(f"{p}={n}" for p, n in top_debt) if top_debt else "—"
        out.append(
            f"- **Verification debt:** {debt_total} not-verified findings (top pillars: {top_label}). "
            f"`not-verified` no longer earns partial score credit in v0.3.0."
        )
    else:
        out.append("- **Verification debt:** 0 (all checks executed)")
    # Per-evidence freshness banner (issue #22). Only added when the run's
    # oldest evidence is older than `freshness_hours` before `checked_at`.
    ef = manifest.get("evidence_freshness") or {}
    if ef.get("stale"):
        oldest = ef.get("oldest_captured_at") or "?"
        threshold = ef.get("threshold_hours", DEFAULT_FRESHNESS_HOURS)
        delta_label = ""
        try:
            oldest_dt = datetime.strptime(oldest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            checked_dt = datetime.strptime(manifest["checked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            delta_h = int((checked_dt - oldest_dt).total_seconds() // 3600)
            delta_label = f" ({delta_h}h before report)"
        except (ValueError, TypeError, KeyError):
            pass
        out.append(
            f"- **Oldest evidence:** {oldest}{delta_label} — exceeds freshness "
            f"window ({threshold}h). Some evidence may be stale."
        )
    if posture["resolved"] != POSTURE_CITADEL:
        out.append("")
        out.append(f"> ℹ️  **Recommended enterprise posture: Citadel-spoke.** "
                   f"Current target is `{posture['resolved']}`. Citadel-specific findings were scored `not-applicable`. "
                   f"To opt in, set `target_posture: citadel-spoke` in SPEC § 12 or pass `--target citadel-spoke`.")
    out.append("")
    # Top 5 gaps
    all_gaps = [f for pid in PILLAR_IDS for f in pillar_results_waived.get(pid, []) if f.status in ("must-fix", "should-fix")]
    all_gaps.sort(key=lambda f: (0 if f.status == "must-fix" else 1, f.id))
    out.append("**Top gaps:**")
    if all_gaps:
        for f in all_gaps[:5]:
            out.append(f"- {STATUS_ICON[f.status]} `{f.id}` ({f.pillar}) — {f.title}. {f.detail}")
    else:
        out.append("- (none)")
    out.append("")
    # 2. Posture diagram
    out.append("## 2. Posture diagram")
    out.append("")
    out.append("```mermaid")
    out.append("flowchart LR")
    out.append("  user[User] --> entry[Entry surface]")
    if posture["resolved"] == POSTURE_CITADEL:
        out.append("  entry --> hub[Citadel APIM hub]")
        out.append("  hub --> spoke[Foundry account spoke]")
    elif posture["resolved"] == POSTURE_AGT:
        out.append("  entry --> agt[AGT middleware in-process]")
        out.append("  agt --> spoke[Foundry account]")
    elif posture["resolved"] == POSTURE_HYBRID:
        out.append("  entry --> hub[Citadel APIM hub]")
        out.append("  hub --> agt[AGT middleware]")
        out.append("  agt --> spoke[Foundry account spoke]")
    else:
        out.append("  entry --> gw[Standard AI Gateway]")
        out.append("  gw --> spoke[Foundry account]")
    out.append("  spoke --> models[Model deployments]")
    out.append("  spoke --> obs[App Insights + Log Analytics]")
    out.append("```")
    out.append("")
    # 3. Hard-gate preview
    out.append("## 3. Hard-gate preview")
    out.append("")
    musts = [f for pid in PILLAR_IDS for f in pillar_results_waived.get(pid, []) if f.status == "must-fix"]
    if not musts:
        out.append("✅ No must-fix findings — would pass a hard gate today.")
    else:
        out.append(f"❌ **Would fail a hard gate.** {len(musts)} must-fix finding(s):")
        out.append("")
        for f in musts:
            out.append(f"- `{f.id}` ({f.pillar}): {f.title}")
    out.append("")
    # 4. Pillar scorecard
    out.append("## 4. Pillar scorecard")
    out.append("")
    out.append("| Pillar | Status (with waivers) | Status (raw) | Raw % | With waivers % |")
    out.append("|---|---|---|---|---|")
    for p in manifest["pillars"]:
        out.append(f"| {p['title']} | {STATUS_ICON.get(p['status_with_waivers'], '?')} {p['status_with_waivers']} | {STATUS_ICON.get(p['status_raw'], '?')} {p['status_raw']} | {p['score_raw']}% | {p['score_with_waivers']}% |")
    out.append("")
    # 5. Pillar deep-dives
    out.append("## 5. Pillar deep-dives")
    out.append("")
    for pid in PILLAR_IDS:
        findings = pillar_results_waived.get(pid, [])
        if not findings:
            continue
        out.append(f"### {PILLAR_TITLES[pid]}")
        out.append("")
        out.append("| Finding | Severity | Status | Detail |")
        out.append("|---|---|---|---|")
        for f in findings:
            tier_label = TIER_TO_LABEL.get(f.tier, str(f.tier))
            detail = (f.detail or "").replace("|", "\\|")
            extra = f" (tier: {tier_label})" if f.tier > 0 else ""
            wsuffix = f" — waiver {f.waiver_id}" if f.waiver_id else ""
            override_reason = getattr(f, "override_reason", None)
            override_customer = getattr(f, "override_customer", None)
            osuffix = ""
            if override_reason:
                safe_customer = str(override_customer or "unknown").replace("|", "\\|")
                safe_reason = str(override_reason).replace("|", "\\|")
                osuffix = f" — customer override {safe_customer}: {safe_reason}"
            out.append(f"| `{f.id}` | {f.severity} | {STATUS_ICON.get(f.status, '?')} {f.status} | {detail}{extra}{wsuffix}{osuffix} |")
        out.append("")
    # 6. Uplift plan
    out.append("## 6. Uplift plan (suggested order)")
    out.append("")
    uplift_links = {
        "network-posture": "`citadel-spoke-onboarding`, `foundry-vnet-deploy`, `foundry-network-runbook`",
        "agent-governance": "`foundry-agt`",
        "identity-access": "`foundry-hosted-agents`, `azure-tenant-isolation`",
        "secrets": "`azd-patterns`",
        "observability": "`foundry-observability`",
        "continuous-evals": "`foundry-evals`",
        "responsible-ai": "`foundry-agt`",
        "hitl-audit": "`threadlight-hitl-patterns`",
        "supply-chain": "`azd-patterns`",
        "cost": "`paygo-ptu-cost-analyzer`",
        "reliability": "`foundry-vnet-deploy`, `foundry-caphost-lifecycle`",
        "sre-handover": "`azure-sre-agent` (recipe: `threadlight-pilot-handover`)",
        "model-lifecycle": "`foundry-skill-catalog`, `foundry-evals`",
    }
    step = 1
    for f in all_gaps:
        out.append(f"{step}. **{f.id}** — {f.title}. See: {uplift_links.get(f.pillar, '(see pillar reference)')}")
        step += 1
    if step == 1:
        out.append("_No remediation steps. Pilot is production-ready._")
    out.append("")
    # 7. Cost projection
    out.append("## 7. Cost projection")
    out.append("")
    out.append("_v1: high-level reminders only. For deep PAYG vs PTU analysis, run `paygo-ptu-cost-analyzer`._")
    out.append("")
    out.append("- Pricing plan declared in SPEC § 10: " + ("yes" if any(f.id == "COST-001" and f.status == "pass" for fs in pillar_results_waived.values() for f in fs) else "no"))
    out.append("- Budget alerts wired: " + ("yes" if any(f.id == "COST-101" and f.status == "pass" for fs in pillar_results_waived.values() for f in fs) else "no / not-verified"))
    out.append("")
    # 8. Eval summary
    out.append("## 8. Eval summary")
    out.append("")
    out.append("_v1: eval live probes are stubbed. Run `foundry-evals` and paste the result into SPEC § 9._")
    out.append("")
    # 9. Residual risk + rollout/rollback
    out.append("## 9. Residual risk, RACI, rollout / rollback / cutover")
    out.append("")
    out.append("### Residual risk register")
    out.append("")
    if waivers:
        out.append("| Waiver ID | Finding | Owner | Expiry | Justification |")
        out.append("|---|---|---|---|---|")
        for w in waivers.values():
            out.append(f"| `{w['id']}` | `{w['finding_id']}` | {w['owner']} | {w['expiry']} | {w['justification']} |")
    else:
        out.append("_No waivers accepted._")
    out.append("")
    out.append("### RACI (template — fill before go-live)")
    out.append("")
    out.append("| Activity | Responsible | Accountable | Consulted | Informed |")
    out.append("|---|---|---|---|---|")
    out.append("| Deploy / cutover | _TBD_ | _TBD_ | _TBD_ | _TBD_ |")
    out.append("| Incident response | _TBD_ | _TBD_ | _TBD_ | _TBD_ |")
    out.append("| Eval failures | _TBD_ | _TBD_ | _TBD_ | _TBD_ |")
    out.append("| Cost variance | _TBD_ | _TBD_ | _TBD_ | _TBD_ |")
    out.append("")
    out.append("### Rollout / rollback / cutover (template)")
    out.append("")
    out.append("1. **Rollout window:** _e.g. T0+0 → T0+2h business hours, low-traffic._")
    out.append("2. **Pre-cutover smoke:** rerun `safe-check --phase post-deploy`; rerun this skill `--quick`.")
    out.append("3. **Rollback trigger:** `must-fix` finding regression OR eval pass-rate drop > X%.")
    out.append("4. **Rollback steps:** `azd down --force --purge` on new RG OR DNS swap back to pilot RG.")
    out.append("5. **Comms:** owner notifies `#agent-prod` channel at T-1h, T0, T0+2h.")
    out.append("")
    # 10. Appendix
    out.append("## 10. Appendix")
    out.append("")
    out.append("### Evidence register")
    out.append("")
    if evidence:
        out.append("| Ref | Pillar | Tier | Collected | Command | Result | Notes |")
        out.append("|---|---|---|---|---|---|---|")
        for e in evidence:
            collected = e.captured_at or "—"
            out.append(f"| `{e.ref}` | {e.pillar} | T{e.tier} | `{collected}` | `{e.command}` | {e.result} | {e.notes} |")
    else:
        out.append("_No evidence captured (static mode or no Azure access)._")
    out.append("")
    out.append("### What was not verified")
    out.append("")
    nv = [f for fs in pillar_results_waived.values() for f in fs if f.status == "not-verified"]
    if nv:
        out.append("| Finding | Pillar | Tier | Reason |")
        out.append("|---|---|---|---|")
        for f in nv:
            out.append(f"| `{f.id}` | {f.pillar} | T{f.tier} | {f.detail} |")
    else:
        out.append("_Everything was checked._")
    out.append("")
    out.append("### Warnings during this run")
    out.append("")
    if warnings:
        for w in warnings:
            out.append(f"- {w}")
    else:
        out.append("_None._")
    out.append("")
    out.append("### Glossary")
    out.append("")
    out.append("- **AGT** — Agent Governance Toolkit. In-process middleware that enforces policy on tool calls, prompts, and outputs.")
    out.append("- **Citadel spoke** — Foundry account fronted by an APIM-based AI Hub Gateway (Citadel). See `citadel-hub-deploy` and `citadel-spoke-onboarding`.")
    out.append("- **OWASP ASI 2026** — OWASP AI/Agentic Security Initiative top-N risks reference.")
    out.append("")
    out.append(f"_End of report. Manifest: see `tests/production-readiness-manifest.json`._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# main / orchestration
# ---------------------------------------------------------------------------


def _az_available() -> bool:
    try:
        proc = subprocess.run("az --version", shell=True, capture_output=True, text=True, check=False)
        return proc.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _detect_apim_evidence(sub: str | None, rg: str | None, az_ok: bool) -> bool | None:
    """Return True if we can see an APIM-fronted Foundry connection in this RG."""
    if not (az_ok and sub and rg):
        return None
    data = _az_json("resource", "list", "--resource-group", rg, "--subscription", sub, "--query", "[?type=='Microsoft.ApiManagement/service']")
    return bool(data)


def _extract_sub_rg(manifest: dict, azd_env: dict[str, str]) -> tuple[str | None, str | None]:
    dm = manifest.get("deployment_manifest", {})
    sub = dm.get("subscription_id") or azd_env.get("AZURE_SUBSCRIPTION_ID")
    rg = dm.get("resource_group") or azd_env.get("AZURE_RESOURCE_GROUP") or azd_env.get("RESOURCE_GROUP_NAME")
    return sub, rg


def _build_evidence_ref(pillar: str, idx: int) -> str:
    return f"E-{pillar}-{idx:03d}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="production_ready",
        description="Advisory production-readiness checker for threadlight pilots.",
    )
    p.add_argument("--pillar", default="", help="comma-separated subset (default: all 13)")
    p.add_argument("--static", action="store_true", help="skip live Azure probes")
    p.add_argument("--quick", action="store_true", help="only the first live check per pillar")
    p.add_argument("--target", choices=["citadel-spoke", "agt", "standard-ai-gateway", "hybrid"],
                   default=None, help="override posture resolution")
    p.add_argument("--agt-profile", choices=["auto", "v3_7", "v4_preview", "none"], default="auto",
                   help="AGT profile (default: auto-detect from source)")
    p.add_argument("--waivers", default="tests/production-readiness-waivers.json",
                   help="waiver file path (optional)")
    p.add_argument("--accept-stale-safe-check", action="store_true",
                   help="accept stale or hash-mismatched safe-check manifest")
    p.add_argument("--freshness-hours", type=int, default=24, help="max safe-check age (default 24)")
    p.add_argument("--out", default="tests/production-readiness-manifest.json",
                   help="JSON manifest output path")
    p.add_argument("--report", default="docs/production-readiness-report.md",
                   help="markdown report output path")
    p.add_argument("--in-postdeploy", default="tests/postdeploy-manifest.json",
                   help="safe-check post-deploy manifest path")
    p.add_argument("--in-manifest", default="specs/manifest.json",
                   help="deployment manifest path")
    p.add_argument("--in-spec", default="specs/SPEC.md",
                   help="(unused in v1 - SPEC is read from manifest's repo root)")
    p.add_argument("--root", default=".", help="repo root (default: cwd)")
    p.add_argument("--quiet", action="store_true", help="suppress non-error stdout")
    p.add_argument("--include-experimental", action="store_true",
                   help="include experimental finding IDs in scoring (default: excluded)")
    p.add_argument("--diff", metavar="MANIFEST",
                   help="compare current run against a prior manifest JSON; emit diff to stdout and exit")
    p.add_argument("--gate-preview", action="store_true",
                   help="treat any must-fix as a hard-gate preview: exit 2 if would-fail-hard-gate is true")
    p.add_argument("--remediate", metavar="FINDING_ID",
                   help="print bash remediation recipe for a finding ID from references/remediation-recipes.yaml and exit")
    p.add_argument("--trend-csv", metavar="PATH", default="tests/production-readiness-trend.csv",
                   help="append a row per run (date, score, posture, debt) for trending; set to '' to disable")
    p.add_argument("--secure-score-floor", type=int, default=60,
                   help="Defender Secure Score percent floor for GOV-104 (default 60)")
    # v0.4.0 production-onboarding flags
    p.add_argument("--onboard", action="store_true",
                   help="Enter 3-phase production onboarding mode (framing wizard + apply-plan.json)")
    p.add_argument("--framing-file", default=None,
                   help="JSON file with framing answers (skips the interactive wizard)")
    p.add_argument("--apply-plan-out", default=None,
                   help="Path to write apply-plan.json (default: tests/production-readiness-apply-plan.json)")
    p.add_argument("--scaffold-cicd", action="store_true",
                   help="Phase 3: write .github/workflows/azd-deploy-prod.yml + UAMI runbook from templates")
    p.add_argument("--no-rights-probe", action="store_true",
                   help="Skip the live provisioning-rights probe (use framing answer only)")
    p.add_argument("--target-sub", default=None,
                   help="Override target subscription ID (otherwise read from framing or manifest)")
    p.add_argument("--target-rg", default=None,
                   help="Override target resource group (otherwise read from framing or manifest)")
    p.add_argument("--repo-full-name", default=None,
                   help="owner/repo string for CI/CD scaffolds (auto-detected from git remote if omitted)")
    p.add_argument("--customer-overrides", default=None,
                   help="Path to a customer-overrides.yaml file (SPEC §12). "
                        "Status-flips only. Must-fix findings cannot be overridden — "
                        "attempting to do so exits 2.")
    p.add_argument("--version", action="version", version=f"production_ready.py {VERSION}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()

    # --remediate is a side-channel: print the recipe and exit. Doesn't need
    # any other inputs.
    if args.remediate:
        return _emit_remediation(root, args.remediate)

    # --scaffold-cicd standalone: when no assessor input is available in cwd
    # (no specs/manifest.json), render templates + exit. Doesn't run the
    # assessor. If specs/manifest.json IS present, fall through to the
    # v0.3.0 main() path; the scaffold will run at the end after the
    # assessment. Combined with --onboard, scaffolding fires AFTER
    # apply-plan is written (see the --onboard block below).
    if args.scaffold_cicd and not args.onboard:
        manifest_in = root / args.in_manifest
        if not manifest_in.is_file():
            framing = (load_framing_file(args.framing_file) if args.framing_file
                       else run_framing_wizard())
            repo = args.repo_full_name or _detect_repo_full_name(os.getcwd())
            if not repo:
                _eprint("--scaffold-cicd: could not detect repo owner/repo; pass --repo-full-name.")
                return 2
            written = _scaffold_cicd(framing, repo, out_root=os.getcwd())
            for p in written:
                _eprint(f"wrote {p}")
            return 0
        # else fall through; the v0.3.0 main path injects v0.4.0 hooks at the end.

    # --onboard is the v0.4.0 3-phase production-onboarding side-channel.
    # Phase 1 (Assess) is implemented here; Phase 2 (Refine + Deploy) and
    # Phase 3 (CI/CD Handoff) run downstream in the agent driven by
    # apply-plan.json. _run_assessment_for_onboard emits the phase-2
    # decision banner to stderr before apply-plan.json is materialized.
    if args.onboard:
        framing = (load_framing_file(args.framing_file) if args.framing_file
                   else run_framing_wizard())
        manifest = _run_assessment_for_onboard(args, framing)
        recipes = load_recipe_catalog(_recipe_catalog_dir())
        plan = build_apply_plan(
            manifest=manifest,
            recipes=recipes,
            framing=framing,
            framing_path=args.framing_file,
        )
        out_path = args.apply_plan_out or str(Path(args.out) / "apply-plan.json")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        write_apply_plan(plan, out_path)
        # Phase 3: if --scaffold-cicd is also set, render templates now.
        # Otherwise hint the operator if apply-plan has deferred-to-pipeline items.
        if args.scaffold_cicd:
            repo = args.repo_full_name or _detect_repo_full_name(os.getcwd())
            if not repo:
                _eprint("--scaffold-cicd: could not detect repo owner/repo; pass --repo-full-name. Skipping scaffold.")
            else:
                written = _scaffold_cicd(framing, repo, out_root=os.getcwd())
                for p in written:
                    _eprint(f"wrote {p}")
        else:
            _hint_pipeline_scaffold_if_needed(plan, scaffold_cicd_flag=False)
        return 0

    if not args.quiet:
        print(f"threadlight-production-ready v{VERSION}")
        _print_active_context()

    # 1. Load manifest + post-deploy
    manifest_path = root / args.in_manifest
    postdeploy_path = root / args.in_postdeploy
    manifest = _load_manifest(manifest_path)
    postdeploy, pd_warns = _load_postdeploy(
        postdeploy_path, args.accept_stale_safe_check, args.freshness_hours
    )
    bind_warns = _validate_manifest_binding(manifest, postdeploy, args.accept_stale_safe_check)
    warnings = pd_warns + bind_warns

    # 2. Waivers — loaded here; binding validated AFTER posture resolution.
    waivers_path = root / args.waivers if args.waivers else None
    waivers, waiver_binding, waiver_errs = _load_waivers(waivers_path)
    warnings.extend(waiver_errs)
    customer_overrides = None
    if args.customer_overrides:
        try:
            customer_overrides = _load_customer_overrides(args.customer_overrides)
            _validate_customer_overrides(customer_overrides)
        except (FileNotFoundError, ValueError) as e:
            _eprint(f"error: customer-overrides: {e}")
            return 2

    # 3. Context — inject CLI-flag overrides into manifest so checks see them.
    # `--secure-score-floor` is read by GOV-104 via `ctx.manifest`; threading it
    # here keeps the CLI flag honest while letting an explicit manifest entry win.
    manifest.setdefault("secure_score_floor", args.secure_score_floor)
    try:
        ctx = RepoContext.from_repo(root, manifest)
    except PrerequisiteError as e:
        _eprint(f"error: {e}")
        return 2

    # 3b. Missing SPEC § 12 — soft warning, NOT exit 2 (rubber-duck #1).
    # When §12 is absent or empty, the skill still runs; posture falls back to
    # standard-ai-gateway and a warning surfaces this verification debt.
    if not any(v for v in ctx.spec_12.values()):
        warnings.append(
            "RDY-002: SPEC § 12 (Production Readiness) is missing or empty — "
            "posture, RTO/RPO, SLA, residency, incident owner could not be "
            "verified against the spec. Add § 12 from `skills/threadlight-design/"
            "references/speckit-template.md` and re-run for a full scorecard."
        )

    # 4. Pillars to run
    requested = [p.strip() for p in args.pillar.split(",") if p.strip()] if args.pillar else PILLAR_IDS
    unknown = [p for p in requested if p not in PILLAR_IDS]
    if unknown:
        _eprint(f"error: unknown pillar(s): {unknown}. Known: {PILLAR_IDS}")
        return 2
    pillars_to_run = [p for p in PILLAR_IDS if p in requested]

    # 5. Az + posture + tiers
    az_ok = _az_available()
    if not args.static and not az_ok:
        warnings.append("`az` CLI not found on PATH — live probes degraded to not-verified")
    sub, rg = _extract_sub_rg(manifest, ctx.azd_env)
    apim_evidence = None
    if not args.static:
        apim_evidence = _detect_apim_evidence(sub, rg, az_ok)
    declared, detected, resolved = _resolve_posture(args.target, ctx.spec_12, ctx.spec_11b, apim_evidence)
    posture = {"declared": declared, "detected": detected, "resolved": resolved}
    tiers = _probe_tiers(sub, rg, az_ok) if not args.static else {0: True, 1: False, 2: False, 3: False, 4: False, 5: False}

    # 5b. Validate waiver binding against THIS run (sub/rg/posture/deployment).
    # Done after posture resolution so the binding can vet target_posture.
    # Done before pillar runs so an unbound or mismatched file doesn't silently
    # apply waivers that were approved for a different MVP. Backward-compat:
    # missing binding still applies waivers but emits a loud UNBOUND warning.
    if waivers_path is not None and waivers_path.exists():
        dm_sha = _sha256_block(postdeploy.get("deployment_manifest") or {})
        apply_waivers, binding_msgs = _validate_waiver_binding(
            waiver_binding, sub, rg, dm_sha, resolved,
        )
        warnings.extend(binding_msgs)
        if not apply_waivers:
            waivers = {}

    # 6. AGT profile
    agt_profile = _detect_agt_profile(ctx, args.agt_profile)

    # 7. Run pillars
    pillar_findings_raw: dict[str, list[Finding]] = {}
    pillar_findings_waived: dict[str, list[Finding]] = {}
    evidence_all: list[EvidenceEntry] = []
    for pid in pillars_to_run:
        findings, evid = _run_pillar(
            pid, ctx,
            static_only=args.static,
            tiers=tiers,
            sub=sub,
            rg=rg,
            resolved_posture=resolved,
            agt_profile=agt_profile,
            quick=args.quick,
        )
        # mark Citadel-only findings as not-applicable if posture isn't Citadel
        if resolved != POSTURE_CITADEL:
            patched = []
            for f in findings:
                if f.id.startswith("NET-5"):
                    nf = Finding(**asdict(f))
                    nf.status = "not-applicable"
                    nf.detail = f"Not applicable: resolved posture is `{resolved}`, not citadel-spoke."
                    patched.append(nf)
                else:
                    patched.append(f)
            findings = patched
        pillar_findings_raw[pid] = findings
        pillar_findings_waived[pid] = _apply_waivers(findings, waivers)
        evidence_all.extend(evid)

    # For pillars NOT requested, fill in empty (so manifest stays shape-stable)
    for pid in PILLAR_IDS:
        pillar_findings_raw.setdefault(pid, [])
        pillar_findings_waived.setdefault(pid, [])

    # 7b. POS-001 — declared posture contradiction.
    # Fire when SE declared an enterprise posture (citadel-spoke, agt, hybrid)
    # but live evidence found nothing matching it AND tier 1 ran. Skipped when
    # tier 1 was unreachable (we'd be guessing) or pillar wasn't requested.
    if (
        "network-posture" in pillars_to_run
        and declared in (POSTURE_CITADEL, POSTURE_AGT, POSTURE_HYBRID)
        and not detected
        and tiers.get(1, False)
    ):
        pos_finding = _mk_finding(
            "POS-001",
            status="should-fix",
            detail=(
                f"SPEC § 12 declares `target_posture: {declared}` but live "
                "evidence (APIM, Foundry connection, AGT middleware) did not "
                "confirm it. Either the deployment hasn't reached the declared "
                "posture yet, the operator running this skill lacks permission "
                "to see the hub-side resources, or the declaration is stale. "
                "Architecture review should resolve this before customer "
                "sign-off — production-readiness reports that contradict the "
                "spec erode trust."
            ),
        )
        pillar_findings_raw["network-posture"].append(pos_finding)
        pillar_findings_waived["network-posture"] = _apply_waivers(
            pillar_findings_raw["network-posture"], waivers,
        )

    if customer_overrides:
        for pid in PILLAR_IDS:
            pillar_findings_waived[pid] = _apply_customer_overrides(
                pillar_findings_waived.get(pid, []), customer_overrides,
            )

    # 8. Build manifest + report
    safe_check_ref = {
        "phase": postdeploy.get("phase"),
        "checked_at": postdeploy.get("checked_at"),
        "deployment_manifest_sha256": _sha256_block(postdeploy.get("deployment_manifest") or {}),
        "source_path": str(postdeploy_path.relative_to(root) if postdeploy_path.is_relative_to(root) else postdeploy_path),
    }
    out_manifest = _build_manifest(
        posture=posture,
        pillar_results_raw=pillar_findings_raw,
        pillar_results_waived=pillar_findings_waived,
        evidence=evidence_all,
        not_verified=[f for fs in pillar_findings_waived.values() for f in fs if f.status == "not-verified"],
        waivers=waivers,
        tiers=tiers,
        warnings=warnings,
        agt_profile=agt_profile,
        safe_check_ref=safe_check_ref,
        quick=args.quick,
        static_only=args.static,
        freshness_hours=args.freshness_hours,
        include_experimental=args.include_experimental,
    )

    out_path = root / args.out
    report_path = root / args.report
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out_manifest, indent=2) + "\n", encoding="utf-8")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_md = _render_report(out_manifest, posture, pillar_findings_waived, evidence_all, waivers, warnings)
        report_path.write_text(report_md, encoding="utf-8")
    except OSError as e:
        _eprint(f"error: failed to write outputs: {e}")
        return 3

    # 8b. v0.4.0 hooks — when --framing-file is present, attach phase decision
    # to the manifest, emit the phase banner, build apply-plan, and optionally
    # scaffold CI/CD templates. All of this is a no-op when --framing-file is
    # absent, preserving v0.3.0 invocation compatibility.
    if args.framing_file:
        framing = load_framing_file(args.framing_file)
        sub = args.target_sub or framing.get("target_subscription_id")
        rg = args.target_rg or framing.get("target_resource_group")
        rights = _probe_provisioning_rights(sub, rg, skip=args.no_rights_probe)
        decision = _phase_decision(framing, rights)
        _emit_phase_banner(framing, rights, decision, sink=sys.stderr)
        out_manifest["version"] = VERSION
        out_manifest["rights_probe"] = rights
        out_manifest["phase_decision"] = decision
        try:
            out_path.write_text(json.dumps(out_manifest, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            _eprint(f"warn: failed to re-write manifest with v0.4.0 fields: {e}")
        if args.apply_plan_out or args.scaffold_cicd:
            recipes = load_recipe_catalog(_recipe_catalog_dir())
            plan = build_apply_plan(
                manifest=out_manifest, recipes=recipes,
                framing=framing, framing_path=args.framing_file,
            )
            plan_path = args.apply_plan_out or str(root / "apply-plan.json")
            Path(plan_path).parent.mkdir(parents=True, exist_ok=True)
            write_apply_plan(plan, plan_path)
            if args.scaffold_cicd:
                repo = args.repo_full_name or _detect_repo_full_name(os.getcwd())
                if not repo:
                    _eprint("--scaffold-cicd: could not detect repo owner/repo; pass --repo-full-name. Skipping scaffold.")
                else:
                    written = _scaffold_cicd(framing, repo, out_root=os.getcwd())
                    for p in written:
                        _eprint(f"wrote {p}")
            else:
                _hint_pipeline_scaffold_if_needed(plan, scaffold_cicd_flag=False)

    # 9. Summary
    if not args.quiet:
        rec_label = {"ready": "🟢 READY",
                     "ready_with_waivers": "🟡 READY WITH WAIVERS",
                     "ready_with_residual_risk": "🟡 READY (residual risk)",
                     "ready_with_unverified_risk": "🟡 READY (unverified risk)",
                     "not_ready": "🔴 NOT READY"}[out_manifest["go_live_recommendation"]]
        cov = out_manifest["verification_coverage"]
        print(
            f"\n{rec_label}  raw={out_manifest['score']['raw_percent']}%  "
            f"with_waivers={out_manifest['score']['with_waivers_percent']}%  "
            f"posture={resolved}  verified={cov['verified']}/{cov['total_scoreable']} ({cov['percent']}%)"
        )
        print(f"  -> manifest: {out_path}")
        print(f"  -> report:   {report_path}")
        if warnings:
            print(f"  ({len(warnings)} warning(s) — see appendix)")

    # 10. v0.3.0 NEW: trend CSV append (best-effort, never blocks)
    if args.trend_csv:
        try:
            _append_trend_csv(root / args.trend_csv, out_manifest, resolved)
        except OSError as e:
            _eprint(f"warn: failed to append trend csv: {e}")

    # 11. v0.3.0 NEW: --diff mode prints diff vs prior manifest to stdout
    if args.diff:
        try:
            prior = json.loads(Path(args.diff).read_text(encoding="utf-8"))
            print("\n" + _diff_manifests(prior, out_manifest))
        except (OSError, ValueError) as e:
            _eprint(f"warn: failed to load --diff manifest: {e}")

    # 12. v0.3.0 NEW: --gate-preview returns exit 2 on hard-gate-would-fail
    if args.gate_preview and out_manifest.get("would_fail_hard_gate"):
        _eprint("error: --gate-preview: at least one must-fix would block go-live")
        return 2
    return 0


# ---------------------------------------------------------------------------
# v0.3.0 NEW: industrialization helpers (diff, trend, remediation)
# ---------------------------------------------------------------------------


def _append_trend_csv(path: Path, manifest: dict, posture: str) -> None:
    """Append a single row to a trend CSV. Creates header on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("date,tool_version,posture,raw_percent,with_waivers_percent,"
              "verified,total_scoreable,verification_debt,recommendation\n")
    exists = path.exists()
    cov = manifest.get("verification_coverage") or {}
    debt = (manifest.get("verification_debt") or {}).get("total", 0)
    row = ",".join([
        manifest.get("checked_at", ""),
        manifest.get("tool_version", ""),
        posture,
        str(manifest.get("score", {}).get("raw_percent", "")),
        str(manifest.get("score", {}).get("with_waivers_percent", "")),
        str(cov.get("verified", "")),
        str(cov.get("total_scoreable", "")),
        str(debt),
        manifest.get("go_live_recommendation", ""),
    ]) + "\n"
    mode = "a" if exists else "w"
    with path.open(mode, encoding="utf-8") as f:
        if not exists:
            f.write(header)
        f.write(row)


def _diff_manifests(prior: dict, current: dict) -> str:
    """Render a short, human-readable diff between two production-readiness manifests."""
    lines: list[str] = []
    lines.append("# production-readiness diff")
    lines.append("")
    lines.append(f"prior   : {prior.get('checked_at')} v{prior.get('tool_version')} "
                 f"score raw={prior.get('score', {}).get('raw_percent')}%")
    lines.append(f"current : {current.get('checked_at')} v{current.get('tool_version')} "
                 f"score raw={current.get('score', {}).get('raw_percent')}%")
    delta = (current.get("score", {}).get("raw_percent", 0) -
             prior.get("score", {}).get("raw_percent", 0))
    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
    lines.append(f"delta   : {arrow} {delta:+d}%")
    lines.append("")
    prior_idx = {(p.get("pillar"), f.get("id")): f
                 for p in prior.get("pillars", []) for f in p.get("findings", [])}
    curr_idx = {(p.get("pillar"), f.get("id")): f
                for p in current.get("pillars", []) for f in p.get("findings", [])}
    all_keys = sorted(set(prior_idx) | set(curr_idx))
    flipped: list[str] = []
    new_must: list[str] = []
    new_pass: list[str] = []
    gone: list[str] = []
    for k in all_keys:
        p = prior_idx.get(k)
        c = curr_idx.get(k)
        if p is None and c is not None:
            if c.get("status") == "must-fix":
                new_must.append(f"+ {k[0]}/{k[1]} → must-fix")
            elif c.get("status") == "pass":
                new_pass.append(f"+ {k[0]}/{k[1]} → pass")
        elif c is None and p is not None:
            gone.append(f"- {k[0]}/{k[1]} (was {p.get('status')})")
        elif p and c and p.get("status") != c.get("status"):
            flipped.append(f"  {k[0]}/{k[1]} : {p.get('status')} → {c.get('status')}")
    if new_must:
        lines.append("## new must-fix")
        lines.extend(new_must)
        lines.append("")
    if flipped:
        lines.append("## status changes")
        lines.extend(flipped)
        lines.append("")
    if new_pass:
        lines.append("## new pass")
        lines.extend(new_pass)
        lines.append("")
    if gone:
        lines.append("## removed")
        lines.extend(gone)
    if not (new_must or flipped or new_pass or gone):
        lines.append("(no per-finding changes)")
    return "\n".join(lines)


def _emit_remediation(root: Path, finding_id: str) -> int:
    """Print remediation recipe for a finding ID. Returns 0 on hit, 2 on miss."""
    fid = finding_id.strip().upper()
    recipe_file = root / "skills" / "threadlight-production-ready" / "references" / "remediation-recipes.yaml"
    if not recipe_file.exists():
        # fall back to relative to this script
        recipe_file = Path(__file__).resolve().parent.parent / "references" / "remediation-recipes.yaml"
    if not recipe_file.exists():
        _eprint(f"error: remediation-recipes.yaml not found at {recipe_file}")
        return 2
    body = recipe_file.read_text(encoding="utf-8")
    # Very small YAML-ish parser: split blocks by `^- id:` markers.
    blocks = re.split(r"\n(?=- id:\s*)", "\n" + body)
    for block in blocks:
        m = re.search(r"- id:\s*([A-Z0-9\-]+)", block)
        if m and m.group(1).upper() == fid:
            # Strip leading "- id:" line and print the rest, plus a header
            print(f"# remediation recipe — {fid}")
            print(block.strip())
            print()
            return 0
    _eprint(f"error: no remediation recipe found for `{fid}`")
    _eprint("       add an entry to skills/threadlight-production-ready/references/remediation-recipes.yaml")
    return 2


if __name__ == "__main__":
    sys.exit(main())
