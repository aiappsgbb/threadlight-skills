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

VERSION = "0.2.0"

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
    "NET-103": {"title": "NSG flow logs enabled on spoke subnets", "pillar": "network-posture", "severity": "should-fix", "tier": 1},
    "NET-501": {"title": "Citadel APIM Access Contract present", "pillar": "network-posture", "severity": "must-fix", "tier": 5},
    "NET-502": {"title": "Foundry connection to Citadel hub reachable", "pillar": "network-posture", "severity": "must-fix", "tier": 5},
    "NET-503": {"title": "Hub-side product policy attached", "pillar": "network-posture", "severity": "should-fix", "tier": 5},
    "POS-001": {"title": "Declared posture matches detected evidence", "pillar": "network-posture", "severity": "should-fix", "tier": 1},

    # ---- agent-governance (AGT)
    "AGT-001": {"title": "AGT middleware imported in src/", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-002": {"title": "policy.yaml present in repo", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-003": {"title": "OWASP ASI 2026 verifier referenced", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-004": {"title": "AGT version pinned (not floating)", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-005": {"title": "AGT policy covers tool calls + prompt shields", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-006": {"title": "AGT telemetry sink configured", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-101": {"title": "Workload identity scoped to AGT-required RBAC", "pillar": "agent-governance", "severity": "should-fix", "tier": 1},
    "AGT-102": {"title": "AGT denials visible in App Insights last 24h", "pillar": "agent-governance", "severity": "should-fix", "tier": 2},
    # ---- agent-governance — v4-preview deep checks (gated to --agt-profile v4_preview)
    # See docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md for rationale.
    # These IDs are only emitted by _check_agt_static_v4 / _check_agt_live_v4; never by v3.7 paths.
    "AGT-V4-001": {"title": "AGT v4 distribution names declared in dependencies", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-V4-002": {"title": "AGT v4 policy uses ACS intervention_points schema", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-V4-003": {"title": "AGT v4 dynamic policy conditions (time/cost/quota) detected", "pillar": "agent-governance", "severity": "informational", "tier": 0},
    "AGT-V4-006": {"title": "AGT v4 composite GitHub Action pinned via toolkit-version", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-V4-007": {"title": "AGT v4 audit fields present in committed verifier JSON", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
    "AGT-V4-101": {"title": "AGT v4 denials carry v4-shaped policy_version in App Insights", "pillar": "agent-governance", "severity": "should-fix", "tier": 2},

    # ---- identity-access
    "IAM-001": {"title": "No client secrets in repo (managed identity only)", "pillar": "identity-access", "severity": "must-fix", "tier": 0},
    "IAM-002": {"title": "User-assigned managed identity declared in Bicep", "pillar": "identity-access", "severity": "must-fix", "tier": 0},
    "IAM-003": {"title": "RBAC scopes declared in Bicep (not subscription-wide)", "pillar": "identity-access", "severity": "must-fix", "tier": 0},
    "IAM-004": {"title": "No long-lived SAS tokens in code", "pillar": "identity-access", "severity": "should-fix", "tier": 0},
    "IAM-005": {"title": "ACA / Functions auth enabled", "pillar": "identity-access", "severity": "should-fix", "tier": 0},
    "IAM-101": {"title": "Role assignments observed in-target match Bicep", "pillar": "identity-access", "severity": "must-fix", "tier": 1},
    "IAM-102": {"title": "No Owner/Contributor on workload identity", "pillar": "identity-access", "severity": "must-fix", "tier": 1},
    "IAM-103": {"title": "Conditional access / Entra policies considered", "pillar": "identity-access", "severity": "should-fix", "tier": 1},

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
    "OBS-103": {"title": "Exceptions table populated in last 24h", "pillar": "observability", "severity": "should-fix", "tier": 2},
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
    "EVAL-103": {"title": "Eval failure alert exists in target RG", "pillar": "continuous-evals", "severity": "should-fix", "tier": 2},
    "EVAL-104": {"title": "Eval cadence schedule resource exists", "pillar": "continuous-evals", "severity": "should-fix", "tier": 1},
    "EVAL-105": {"title": "Eval drift trend reviewed in last 30d", "pillar": "continuous-evals", "severity": "should-fix", "tier": 2},

    # ---- responsible-ai
    "RAI-001": {"title": "Content filters declared on model deployments", "pillar": "responsible-ai", "severity": "must-fix", "tier": 0},
    "RAI-002": {"title": "AGT RAI policy section present", "pillar": "responsible-ai", "severity": "must-fix", "tier": 0},
    "RAI-003": {"title": "Prompt shields enabled in policy", "pillar": "responsible-ai", "severity": "must-fix", "tier": 0},
    "RAI-004": {"title": "PII redaction strategy documented", "pillar": "responsible-ai", "severity": "should-fix", "tier": 0},
    "RAI-005": {"title": "Groundedness check planned for RAG", "pillar": "responsible-ai", "severity": "should-fix", "tier": 0},
    "RAI-006": {"title": "RAI incident escalation owner named", "pillar": "responsible-ai", "severity": "should-fix", "tier": 0},
    "RAI-101": {"title": "Content filter resource present in target", "pillar": "responsible-ai", "severity": "must-fix", "tier": 1},
    "RAI-102": {"title": "AGT RAI denials observable in last 24h", "pillar": "responsible-ai", "severity": "should-fix", "tier": 2},

    # ---- hitl-audit
    "HITL-001": {"title": "SPEC sec 8 declares HITL gates if user-facing", "pillar": "hitl-audit", "severity": "should-fix", "tier": 0},
    "HITL-002": {"title": "HITL gate implementations referenced in src/", "pillar": "hitl-audit", "severity": "must-fix", "tier": 0},
    "HITL-003": {"title": "Audit trail destination configured", "pillar": "hitl-audit", "severity": "must-fix", "tier": 0},
    "HITL-004": {"title": "Escalation channel reachable (Teams/email/webhook)", "pillar": "hitl-audit", "severity": "should-fix", "tier": 0},
    "HITL-005": {"title": "HITL decision SLA documented", "pillar": "hitl-audit", "severity": "should-fix", "tier": 0},
    "HITL-101": {"title": "Audit storage account / table exists", "pillar": "hitl-audit", "severity": "must-fix", "tier": 1},
    "HITL-102": {"title": "Audit storage has immutability policy", "pillar": "hitl-audit", "severity": "should-fix", "tier": 1},
    "HITL-103": {"title": "HITL audit rows in last 7d (if expected)", "pillar": "hitl-audit", "severity": "should-fix", "tier": 2},

    # ---- supply-chain
    "SUP-001": {"title": "Container images pinned by digest", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-002": {"title": "Bicep modules pinned (no `latest`)", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-003": {"title": "Dependency manifest committed (lock file)", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-004": {"title": "SBOM generation step declared", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-005": {"title": "Vulnerability scan step declared", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-006": {"title": "ACR scoped to private network", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-007": {"title": "Provenance / attestation considered", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-101": {"title": "Deployed image digests match repo manifest", "pillar": "supply-chain", "severity": "must-fix", "tier": 1},
    "SUP-102": {"title": "ACR has public access disabled", "pillar": "supply-chain", "severity": "should-fix", "tier": 1},
    "SUP-103": {"title": "ACR has Microsoft Defender enabled", "pillar": "supply-chain", "severity": "should-fix", "tier": 1},

    # ---- cost
    "COST-001": {"title": "SPEC sec 10 declares pricing plan (PAYG vs PTU)", "pillar": "cost", "severity": "must-fix", "tier": 0},
    "COST-002": {"title": "Budget thresholds declared", "pillar": "cost", "severity": "must-fix", "tier": 0},
    "COST-003": {"title": "Cost owner documented", "pillar": "cost", "severity": "should-fix", "tier": 0},
    "COST-004": {"title": "Idle scale-down configured for ACA / Functions", "pillar": "cost", "severity": "should-fix", "tier": 0},
    "COST-005": {"title": "Tagging strategy for cost allocation", "pillar": "cost", "severity": "should-fix", "tier": 0},
    "COST-101": {"title": "Live budget alert wired on target RG", "pillar": "cost", "severity": "must-fix", "tier": 3},
    "COST-102": {"title": "Live actuals vs forecast within 20%", "pillar": "cost", "severity": "should-fix", "tier": 3},
    "COST-103": {"title": "PAYG vs PTU recommendation matches observed usage", "pillar": "cost", "severity": "should-fix", "tier": 3},
    "COST-104": {"title": "No orphaned resources in target RG", "pillar": "cost", "severity": "should-fix", "tier": 3},
    "COST-105": {"title": "Resource tags applied as per strategy", "pillar": "cost", "severity": "should-fix", "tier": 3},

    # ---- reliability
    "REL-001": {"title": "SPEC sec 12 declares RTO / RPO", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-002": {"title": "Multi-region plan documented if RTO < 4h", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-003": {"title": "Backup / restore runbook present", "pillar": "reliability", "severity": "must-fix", "tier": 0},
    "REL-004": {"title": "Capacity host lifecycle understood", "pillar": "reliability", "severity": "should-fix", "tier": 0},
    "REL-005": {"title": "Failure modes catalogued in SPEC", "pillar": "reliability", "severity": "should-fix", "tier": 0},
    "REL-006": {"title": "Health probes configured for ACA / Functions", "pillar": "reliability", "severity": "should-fix", "tier": 0},
    "REL-101": {"title": "Zone redundancy enabled where supported", "pillar": "reliability", "severity": "should-fix", "tier": 1},
    "REL-102": {"title": "Backup vault present if SPEC declares backups", "pillar": "reliability", "severity": "must-fix", "tier": 1},
    "REL-103": {"title": "ACA min-replica >= 1 in prod", "pillar": "reliability", "severity": "should-fix", "tier": 1},
    "REL-104": {"title": "Multi-region resources present if declared", "pillar": "reliability", "severity": "should-fix", "tier": 1},
    "REL-105": {"title": "Capacity host status healthy", "pillar": "reliability", "severity": "should-fix", "tier": 1},

    # ---- sre-handover
    "SRE-001": {"title": "SPEC sec 12 names incident owner / on-call", "pillar": "sre-handover", "severity": "must-fix", "tier": 0},
    "SRE-002": {"title": "Runbook present in docs/", "pillar": "sre-handover", "severity": "must-fix", "tier": 0},
    "SRE-003": {"title": "Azure SRE Agent integration considered", "pillar": "sre-handover", "severity": "should-fix", "tier": 0},
    "SRE-004": {"title": "Severity matrix documented", "pillar": "sre-handover", "severity": "should-fix", "tier": 0},
    "SRE-005": {"title": "Postmortem template referenced", "pillar": "sre-handover", "severity": "should-fix", "tier": 0},
    "SRE-101": {"title": "Action group routes to on-call rotation", "pillar": "sre-handover", "severity": "must-fix", "tier": 1},
    "SRE-102": {"title": "SRE Agent resource present if planned", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},
    "SRE-103": {"title": "Diagnostic settings cover all critical resources", "pillar": "sre-handover", "severity": "must-fix", "tier": 1},
    "SRE-104": {"title": "Activity log alerts on RG present", "pillar": "sre-handover", "severity": "should-fix", "tier": 1},

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
    "MDL-104": {"title": "Live rate-limit breaches in last 24h", "pillar": "model-lifecycle", "severity": "should-fix", "tier": 2},
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
    return [p for p in out if ".git" not in p.parts and "node_modules" not in p.parts]


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
        )


# ---- pillar 1: network-posture ---------------------------------------------

def _check_network_static(ctx: RepoContext, resolved_posture: str) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    # NET-001: infra references a network module
    has_network = bool(re.search(r"(module\s+\w+\s+'[^']*network|virtualNetworks|Microsoft\.Network)", bicep, re.I))
    out.append(_mk_finding("NET-001",
        status="pass" if has_network else "must-fix",
        detail="Found network/VNet reference in Bicep" if has_network else "No network module or virtualNetworks resource found in Bicep"))
    # NET-002: PE for Foundry account
    has_pe = bool(re.search(r"privateEndpoints?|Microsoft\.Network/privateEndpoints", bicep, re.I))
    out.append(_mk_finding("NET-002",
        status="pass" if has_pe else "must-fix",
        detail="Private endpoint(s) declared" if has_pe else "No private endpoint resources declared"))
    # NET-003: publicNetworkAccess disabled for AI services / Foundry
    pna_disabled = bool(re.search(r"publicNetworkAccess\s*:\s*'?Disabled'?", bicep, re.I))
    pna_enabled = bool(re.search(r"publicNetworkAccess\s*:\s*'?Enabled'?", bicep, re.I))
    if pna_disabled and not pna_enabled:
        st = "pass"; d = "publicNetworkAccess: Disabled on AI services"
    elif pna_enabled:
        st = "must-fix"; d = "publicNetworkAccess: Enabled — prod must be Disabled"
    else:
        st = "should-fix"; d = "publicNetworkAccess not explicitly set on AI services"
    out.append(_mk_finding("NET-003", status=st, detail=d))
    # NET-004: subnet delegation
    has_delegation = bool(re.search(r"delegations\s*:|Microsoft\.App/environments|Microsoft\.Web/serverFarms", bicep, re.I))
    out.append(_mk_finding("NET-004",
        status="pass" if has_delegation else "should-fix",
        detail="Subnet delegation/ACA env declared" if has_delegation else "No subnet delegation found for ACA/Functions"))
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
        for fid in ("NET-501", "NET-502", "NET-503"):
            findings.append(_not_verified(fid, "Tier 5 Citadel/APIM probe requires hub RG and APIM Service Reader — set TL_CITADEL_HUB_RG env to enable"))
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
    # IAM-002 user-assigned MI in Bicep
    has_uami = bool(re.search(r"userAssignedIdentities|Microsoft\.ManagedIdentity/userAssignedIdentities", bicep, re.I))
    out.append(_mk_finding("IAM-002",
        status="pass" if has_uami else "must-fix",
        detail="User-assigned managed identity declared" if has_uami else "No user-assigned managed identity in Bicep"))
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
    # IAM-005 auth enabled (ACA / Functions)
    has_auth = bool(re.search(r"authConfigs|EasyAuth|Microsoft\.Web/sites/config/authsettings", bicep, re.I))
    out.append(_mk_finding("IAM-005",
        status="pass" if has_auth else "should-fix",
        detail="Auth config declared" if has_auth else "No EasyAuth / authConfigs declared for compute"))
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
    has_kv = bool(re.search(r"Microsoft\.KeyVault/vaults|module\s+\w+\s+'[^']*keyvault", bicep, re.I))
    out.append(_mk_finding("SEC-001",
        status="pass" if has_kv else "must-fix",
        detail="Key Vault declared in infra" if has_kv else "No Key Vault declared in infra"))
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
    has_soft_delete = bool(re.search(r"enableSoftDelete\s*:\s*true|softDeleteRetention", bicep, re.I))
    has_purge = bool(re.search(r"enablePurgeProtection\s*:\s*true", bicep, re.I))
    out.append(_mk_finding("SEC-005",
        status="pass" if has_soft_delete and has_purge else "must-fix",
        detail="Soft-delete + purge protection declared" if has_soft_delete and has_purge else "Soft-delete or purge protection not declared"))
    rbac_kv = bool(re.search(r"enableRbacAuthorization\s*:\s*true", bicep, re.I))
    out.append(_mk_finding("SEC-006",
        status="pass" if rbac_kv else "should-fix",
        detail="KV uses RBAC" if rbac_kv else "KV may use legacy access policies"))
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
    findings.append(_not_verified("SEC-106", "Diagnostic settings probe not implemented in v1 — review portal"))
    return findings, evidence


# ---- pillar 5: observability ----------------------------------------------

def _check_observability_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    src = ctx.src_text
    has_ai = bool(re.search(r"Microsoft\.Insights/components|applicationInsights", bicep, re.I))
    out.append(_mk_finding("OBS-001",
        status="pass" if has_ai else "must-fix",
        detail="App Insights declared" if has_ai else "No App Insights declared in infra"))
    has_la = bool(re.search(r"Microsoft\.OperationalInsights/workspaces|logAnalytics", bicep, re.I))
    out.append(_mk_finding("OBS-002",
        status="pass" if has_la else "must-fix",
        detail="Log Analytics declared" if has_la else "No Log Analytics declared"))
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
        findings.append(_not_verified("OBS-106", "Diagnostic settings probe not implemented in v1 — review portal"))
    # OBS-102 / OBS-103 = KQL — needs LA workspace ID + Monitoring Reader, defer
    findings.append(_not_verified("OBS-102", "Tier 2 KQL trace freshness not implemented in v1"))
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
    for fid in ("EVAL-101", "EVAL-102", "EVAL-103", "EVAL-104", "EVAL-105"):
        findings.append(_not_verified(fid, "Eval live probe requires Foundry API access and SDK — not implemented in v1"))
    return findings, evidence


# ---- pillar 7: responsible-ai ---------------------------------------------

def _check_rai_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    src = ctx.src_text
    spec = ctx.spec_text
    has_cf = bool(re.search(r"raiPolicies|content[-_ ]?filter|defaultRaiPolicy", bicep + src, re.I))
    out.append(_mk_finding("RAI-001",
        status="pass" if has_cf else "must-fix",
        detail="Content filter / RAI policy declared" if has_cf else "No content filter / RAI policy declared on deployments"))
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
    # SUP-006 ACR scoped private
    acr_private = bool(re.search(r"Microsoft\.ContainerRegistry/registries.*\n[^\n]*publicNetworkAccess\s*:\s*'?Disabled", bicep, re.I | re.S))
    out.append(_mk_finding("SUP-006",
        status="pass" if acr_private else "should-fix",
        detail="ACR publicNetworkAccess=Disabled" if acr_private else "ACR not declared private in Bicep"))
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
    findings.append(_not_verified("SUP-101", "Image digest comparison probe not implemented in v1 — diff manifest manually"))
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
    has_tags = bool(re.search(r"tags\s*:\s*\{", ctx.bicep_text, re.I))
    out.append(_mk_finding("COST-005",
        status="pass" if has_tags else "should-fix",
        detail="Tag strategy applied in Bicep" if has_tags else "No tag strategy in Bicep"))
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
    has_probes = bool(re.search(r"probes?\s*:|livenessProbe|readinessProbe|healthCheck", ctx.bicep_text, re.I))
    out.append(_mk_finding("REL-006",
        status="pass" if has_probes else "should-fix",
        detail="Health probes configured" if has_probes else "No health probes configured"))
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
    findings.append(_not_verified("SRE-103", "Diagnostic settings coverage probe not implemented in v1"))
    findings.append(_not_verified("SRE-104", "Activity log alerts probe not implemented in v1"))
    return findings, evidence


# ---- pillar 13: model-lifecycle -------------------------------------------

MODEL_VERSION_RE = re.compile(r"(?:modelVersion|model_version|version)\s*:\s*['\"]?(\d{4}-\d{2}-\d{2}|\d+\.\d+(?:\.\d+)?|latest)['\"]?", re.I)


def _check_model_lifecycle_static(ctx: RepoContext) -> list[Finding]:
    out: list[Finding] = []
    bicep = ctx.bicep_text
    spec = ctx.spec_text
    matches = MODEL_VERSION_RE.findall(bicep)
    latest_only = matches and all(m.lower() == "latest" for m in matches)
    pinned = matches and any(m.lower() != "latest" for m in matches)
    out.append(_mk_finding("MDL-001",
        status="must-fix" if latest_only else ("pass" if pinned else "should-fix"),
        detail=f"Found model versions: {matches}" if matches else "No model version constraint found — review"))
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


def _score_pillar(findings: list[Finding]) -> tuple[str, int, int]:
    """Return (pillar_status, score, max_score). 100-point scale per pillar."""
    if not findings:
        return "not-applicable", 0, 0
    relevant = [f for f in findings if f.status != "not-applicable"]
    if not relevant:
        return "not-applicable", 0, 0
    max_score = len(relevant) * 4
    earned = 0
    has_must = False
    has_should = False
    for f in relevant:
        if f.status == "pass":
            earned += 4
        elif f.status == "waived":
            earned += 3
        elif f.status == "not-verified":
            earned += 2
        elif f.status == "should-fix":
            earned += 1
            has_should = True
        elif f.status == "must-fix":
            has_must = True
    pct = (earned * 100) // max_score if max_score else 0
    if has_must:
        st = "red"
    elif has_should or pct < 80:
        st = "amber"
    else:
        st = "green"
    return st, pct, 100


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
) -> dict:
    # Capture run-end timestamp once so the freshness math and the manifest
    # field agree exactly (prevents exact-boundary flake).
    checked_at = _utc_now()
    pillars_block = []
    raw_total_score = 0
    raw_max = 0
    waived_total = 0
    waived_max = 0
    for pid in PILLAR_IDS:
        raw_fs = pillar_results_raw.get(pid, [])
        w_fs = pillar_results_waived.get(pid, [])
        r_status, r_score, r_max_s = _score_pillar(raw_fs)
        w_status, w_score, w_max_s = _score_pillar(w_fs)
        weight = PILLAR_WEIGHTS[pid]
        raw_total_score += r_score * weight
        raw_max += 100 * weight if r_max_s else 0
        waived_total += w_score * weight
        waived_max += 100 * weight if w_max_s else 0
        pillars_block.append({
            "pillar": pid,
            "title": PILLAR_TITLES[pid],
            "status_raw": r_status,
            "status_with_waivers": w_status,
            "score_raw": r_score,
            "score_with_waivers": w_score,
            "findings": [asdict(f) for f in w_fs],
        })
    raw_pct = (raw_total_score * 100) // raw_max if raw_max else 0
    waived_pct = (waived_total * 100) // waived_max if waived_max else 0
    raw_must = any(any(f.status == "must-fix" for f in raw_fs) for raw_fs in pillar_results_raw.values())
    waived_must = any(any(f.status == "must-fix" for f in w_fs) for w_fs in pillar_results_waived.values())
    # Verification coverage: of the scoreable findings (pass/should-fix/must-fix/not-verified),
    # what fraction was actually verified (not 'not-verified')?
    scoreable = [f for fs in pillar_results_waived.values() for f in fs
                 if f.status in ("pass", "should-fix", "must-fix", "not-verified", "waived")]
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
        "go_live_recommendation": rec,
        "would_fail_hard_gate": raw_must,
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
            out.append(f"| `{f.id}` | {f.severity} | {STATUS_ICON.get(f.status, '?')} {f.status} | {detail}{extra}{wsuffix} |")
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
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()

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

    # 3. Context
    ctx = RepoContext.from_repo(root, manifest)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
