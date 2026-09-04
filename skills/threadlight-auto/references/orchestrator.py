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
import re
import shutil
import subprocess
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
POSTDEPLOY_MANIFEST = "tests/postdeploy-manifest.json"
RFC3339_UTC_OR_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
_CAPABILITY_STATUSES = frozenset({"pass", "must-fix", "should-fix", "not-verified", "not-applicable"})
_REDTEAM_FINDING_IDS = frozenset({"SAFE-101", "SAFE-102", "SAFE-103", "SAFE-104", "SAFE-105", "SAFE-106"})

LEG_CONTRACTS = {
    "evals": {
        "manifest": "specs/evals-manifest.json",
        "skill": "threadlight-evals",
        "schema": "threadlight-evals-manifest/v1",
        "known_verdicts": frozenset({"comprehensive", "partial", "offline-only", "none"}),
        "required_capabilities": frozenset({
            "eval_scenarios_present",
            "eval_datasets_present",
            "dataset_shape_ok",
            "thresholds_declared",
            "schedule_present",
            "run_history_present",
            "online_eval_wired",
            "latest_eval_run_fresh",
            "alert_wired",
            "latest_pass_rate_ok",
            "ab_comparison_present",
        }),
        "require_check_id": True,
    },
    "redteam": {
        "manifest": "specs/redteam-manifest.json",
        "skill": "threadlight-redteam",
        "schema": "threadlight-redteam-manifest/v1",
        "known_verdicts": frozenset({"hardened", "partial", "vulnerable"}),
        "required_capabilities": frozenset({
            "scan_present",
            "scan_fresh",
            "jailbreak_asr_ok",
            "prompt_injection_asr_ok",
            "exfiltration_asr_ok",
            "harmful_content_asr_ok",
            "coverage_ok",
        }),
        "allow_finding_id": True,
        "forbid_extra_fields": True,
    },
    "govern": {
        "manifest": "specs/govern-manifest.json",
        "skill": "threadlight-govern",
        "schema": "threadlight-govern-manifest/v2",
        "known_verdicts": frozenset({"governed", "partial", "ungoverned"}),
        "required_capabilities": frozenset({
            "policy_artefact_present",
            "policy_schema_valid",
            "policy_versioned",
            "policy_default_deny",
            "sensitive_action_rules_present",
            "policy_tests_present",
            "ci_gate_present",
            "attestation_present",
            "attestation_fresh",
            "asi_reference_present",
        }),
    },
}

# -----------------------------------------------------------------------------
# Manual handoffs — the four executable live legs (Task 7)
# -----------------------------------------------------------------------------
#
# These legs are NEVER dispatched by the stage runner and are NEVER added to
# STAGES. They are visible, manual/advisory recommendations only: the agent (or
# operator) invokes the named skill in chat, which writes a shared-envelope
# manifest under specs/. `decide()` surfaces each one's status so the Canvas /
# CLI can show "ready to run" vs "complete/partial/aborted" without ever taking
# a live, cost-bearing action automatically. Ordered connect -> ground ->
# loadtest -> upgrade.
MANUAL_HANDOFFS = {
    "threadlight-connect": "specs/connect-manifest.json",
    "threadlight-ground": "specs/ground-manifest.json",
    "threadlight-loadtest": "specs/load-manifest.json",
    "threadlight-upgrade": "specs/upgrade-manifest.json",
}

_ENVELOPE_STATUSES = frozenset({"complete", "partial", "aborted"})
_ENVELOPE_REQUIRED_KEYS = frozenset(
    {"schema", "tool_version", "generated_at", "freshness", "status", "findings"}
)

# Reuse the canonical shared-envelope validator when this orchestrator is run
# from the threadlight-skills repo; fall back to a self-contained shape check so
# a copy dropped into a pilot workspace (without skills/_shared/) still works.
try:  # pragma: no cover - import wiring
    _REPO_ROOT = Path(__file__).resolve().parents[3]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from skills._shared.manifest import validate_envelope as _validate_envelope  # noqa: E402
except Exception:  # pragma: no cover - standalone fallback
    _validate_envelope = None

try:  # pragma: no cover - import wiring
    _EVIDENCE_GATE_DIR = _REPO_ROOT / "skills" / "threadlight-production-ready" / "scripts"
    if str(_EVIDENCE_GATE_DIR) not in sys.path:
        sys.path.insert(0, str(_EVIDENCE_GATE_DIR))
    from evidence_gate import (  # noqa: E402
        EvidenceGateError as _EvidenceGateError,
        _validate_evals_manifest as _validate_evals_manifest_contract,
        _validate_govern_manifest as _validate_govern_manifest_contract,
        _validate_redteam_manifest as _validate_redteam_manifest_contract,
    )
except Exception:  # pragma: no cover - standalone fallback
    _EvidenceGateError = ValueError
    _validate_govern_manifest_contract = None
    _validate_evals_manifest_contract = None
    _validate_redteam_manifest_contract = None

_ASSURANCE_MANIFEST_VALIDATORS = {
    "govern": _validate_govern_manifest_contract,
    "evals": _validate_evals_manifest_contract,
    "redteam": _validate_redteam_manifest_contract,
}


def _is_valid_envelope(data: Any) -> bool:
    """True when *data* is a valid shared-envelope manifest.

    Prefers the canonical validator; otherwise checks the required keys and a
    recognized status. An unrecognized / malformed manifest is NOT valid, so it
    can never be surfaced as `complete`.
    """
    if not isinstance(data, dict):
        return False
    if _validate_envelope is not None:
        try:
            _validate_envelope(data)
            return True
        except Exception:
            return False
    if not _ENVELOPE_REQUIRED_KEYS.issubset(data):
        return False
    return data.get("status") in _ENVELOPE_STATUSES


def _manifest_handoff_status(workspace: Path, manifest_rel: str) -> str:
    """Classify a manual-handoff leg strictly from its manifest envelope:

      ready    -> manifest absent (skill not run yet)
      complete -> a valid, `complete` envelope
      partial  -> a valid `partial` envelope, OR any invalid/unrecognized
                  manifest (never surfaced as complete)
      aborted  -> a valid `aborted` envelope
    """
    path = workspace / manifest_rel
    if not path.exists():
        return "ready"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "partial"
    if not _is_valid_envelope(data):
        return "partial"
    status = data.get("status")
    if status == "complete":
        return "complete"
    if status == "aborted":
        return "aborted"
    return "partial"


def _manual_handoffs(workspace: Path) -> list[dict[str, Any]]:
    """Ordered manual-handoff recommendations with per-leg status. Advisory and
    visible only — never dispatched, never a live action."""
    return [
        {
            "skill": skill,
            "manifest": manifest_rel,
            "status": _manifest_handoff_status(workspace, manifest_rel),
            "next_intent": (
                f"Ask chat to run the {skill} skill manually; it writes "
                f"{manifest_rel}. Advisory only — no live or cost-bearing action "
                f"is dispatched automatically."
            ),
        }
        for skill, manifest_rel in MANUAL_HANDOFFS.items()
    ]


@dataclass
class StageDecision:
    name: str
    decision: str  # "run" / "skip" / "hard_stop"
    reason: str
    artifacts_seen: list[str] = field(default_factory=list)
    artifacts_missing: list[str] = field(default_factory=list)
    hard_stop_signature: str | None = None


# -----------------------------------------------------------------------------
# Governed-actions lifecycle — recommendation only (Task 14)
# -----------------------------------------------------------------------------
#
# `threadlight-governed-actions` owns the consequential-action assessment and
# every high-impact change that follows from it (enforcement scaffolding,
# policy application, production rollout). None of that is auto's to take.
#
# So this module does exactly two things and nothing else:
#
#   1. It RECOMMENDS the three explicit lifecycle steps, verbatim, when the
#      workspace shows a consequential action or already carries an assessment.
#   2. It SUMMARISES an already committed manifest — a read of committed JSON,
#      never an execution or import of the producer, its probes, or its
#      scaffold — and recommends a re-run when that manifest cannot be trusted.
#
# The skill is deliberately absent from `STAGES`, `STAGE_PROBES`,
# `LEG_CONTRACTS`, and `MANUAL_HANDOFFS`: it is never dispatched, never
# scheduled, and never cascaded into. Ownership stays manual and explicit.

GOVERNED_ACTIONS_MANIFEST = "tests/governed-actions-manifest.json"

# Exact, approved wording. Auto never authors any other governed-actions
# instruction — no enforcement, no policy application, no rollout.
GOVERNED_ACTIONS_HANDOFF = {
    "execution": "manual-explicit",
    "design": (
        "run threadlight-governed-actions --phase design "
        "after threadlight-design"
    ),
    "pre_deploy": (
        "run threadlight-governed-actions --phase pre-deploy before deploy"
    ),
    "post_deploy": (
        "run threadlight-governed-actions --phase post-deploy "
        "against staging only"
    ),
    "manifest": GOVERNED_ACTIONS_MANIFEST,
}

_GOVERNED_ACTIONS_SCHEMA = "threadlight-governed-actions-manifest/v1"
_GOVERNED_ACTIONS_PHASE_KEYS = {
    "design": "design",
    "pre-deploy": "pre_deploy",
    "post-deploy": "post_deploy",
}
_GOVERNED_ACTIONS_VERDICTS = frozenset({"governed", "partial", "ungoverned"})
_GOVERNED_ACTIONS_SUMMARY_BUCKETS = (
    ("pass", "pass"),
    ("must_fix", "must-fix"),
    ("should_fix", "should-fix"),
    ("not_verified", "not-verified"),
    ("not_applicable", "not-applicable"),
)
_GOVERNED_ACTIONS_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ZERO_GOVERNED_ACTIONS_COUNTS = {key: 0 for key, _status in _GOVERNED_ACTIONS_SUMMARY_BUCKETS}

# The signals auto can read for itself, without an assessor: a declared
# consequence class in a root action registry, or an explicit SPEC-side
# declaration. Auto never infers a consequence from a name or a verb — an
# undeclared workspace simply produces no recommendation.
_CONSEQUENTIAL_REGISTRY_FILENAMES = (
    "agent.yaml",
    "agent.yml",
    "tool-registry.json",
    "tool_registry.json",
)
_CONSEQUENCE_DECLARATION_RE = re.compile(
    r"[\"']?consequence[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_.-]+)", re.IGNORECASE
)
_READ_ONLY_CONSEQUENCES = frozenset({"read", "read-only", "readonly", "none", "null"})


def detect_consequential_actions(workspace: Path) -> bool:
    """True when the workspace *declares* a non-read consequential action.

    Two explicit declarations are honoured, in this order:

      * `specs/manifest.json` with `"consequential_actions": true`;
      * a root action registry (`agent.yaml|yml`, `tool-registry.json`,
        `tool_registry.json`) declaring any `consequence` other than a
        read-only class.

    Never infers, never guesses, never raises.
    """
    spec_manifest = _parse_json_object(workspace / "specs" / "manifest.json")
    if spec_manifest is not None and spec_manifest.get("consequential_actions") is True:
        return True
    for filename in _CONSEQUENTIAL_REGISTRY_FILENAMES:
        candidate = workspace / filename
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for value in _CONSEQUENCE_DECLARATION_RE.findall(text):
            if value.strip().casefold() not in _READ_ONLY_CONSEQUENCES:
                return True
    return False


def _governed_actions_rerun_recommendation(phase: str | None) -> list[str]:
    """Approved lifecycle wording to re-run, given a trustworthy *phase*.

    An unreadable or untrustworthy phase gets the whole ordered lifecycle
    rather than a guess at which single step to repeat.
    """
    key = _GOVERNED_ACTIONS_PHASE_KEYS.get(phase or "")
    if key is None:
        return [
            GOVERNED_ACTIONS_HANDOFF["design"],
            GOVERNED_ACTIONS_HANDOFF["pre_deploy"],
            GOVERNED_ACTIONS_HANDOFF["post_deploy"],
        ]
    return [GOVERNED_ACTIONS_HANDOFF[key]]


def _parse_rfc3339(value: Any) -> datetime | None:
    """`_parse_iso` for untrusted input: a non-string is simply unusable."""
    return _parse_iso(value) if isinstance(value, str) else None


def _governed_actions_untrusted(reason: str, phase: str | None = None) -> dict[str, Any]:
    return {
        "manifest": GOVERNED_ACTIONS_MANIFEST,
        "status": "rerun-recommended",
        "trusted": False,
        "reason": reason,
        "phase": phase,
        "verdict": None,
        "counts": dict(_ZERO_GOVERNED_ACTIONS_COUNTS),
        "recommendation": _governed_actions_rerun_recommendation(phase),
    }


def summarize_governed_actions_manifest(
    path: Path,
    source_commit: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarise a committed governed-actions manifest, or recommend a re-run.

    Reads `path` as JSON and checks only what auto can check on its own:
    schema version, lifecycle phase, a clean source bound to *source_commit*,
    freshness against *now*, and summary counts that agree with the findings
    they claim to summarise. Nothing here executes or imports
    `threadlight-governed-actions`, its probes, or its scaffold — the manifest
    is untrusted input and a failed check only ever produces a recommendation
    to re-run the skill manually.

    Returns a dict; never raises.
    """
    moment = (now or datetime.now(timezone.utc))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)

    manifest = _parse_json_object(path)
    if manifest is None:
        return _governed_actions_untrusted(
            "the manifest is absent, unreadable, or not a JSON object"
        )
    if manifest.get("schema") != _GOVERNED_ACTIONS_SCHEMA:
        return _governed_actions_untrusted(
            f"the manifest schema is not {_GOVERNED_ACTIONS_SCHEMA}"
        )
    if manifest.get("evidence_contract") != "governance-ledger/v2":
        return _governed_actions_untrusted("unsupported governance evidence contract")
    evidence = manifest.get("evidence")
    conformance = manifest.get("conformance")
    if not isinstance(evidence, list) or not isinstance(conformance, dict):
        return _governed_actions_untrusted("missing governance evidence")
    probes = conformance.get("application_probes")
    if not isinstance(probes, list):
        return _governed_actions_untrusted("missing governance probes")
    by_id = {}
    for entry in evidence:
        if (not isinstance(entry, dict) or not isinstance(entry.get("evidence_id"), str)
                or not isinstance(entry.get("kind"), str)):
            return _governed_actions_untrusted("malformed governance evidence")
        if entry["evidence_id"] in by_id:
            return _governed_actions_untrusted("duplicate governance evidence")
        by_id[entry["evidence_id"]] = entry
    required_kinds = {
        "approval-anti-replay": {"approval-ledger-records", "approval-binding-digest"},
        "output-mediation": {"output-ledger-records"},
        "payload-free-audit": {"audit-ledger-records", "probe-audit-record"},
    }
    for probe in probes:
        if not isinstance(probe, dict) or not isinstance(probe.get("probe_id"), str):
            return _governed_actions_untrusted("malformed governance probe")
        if (probe.get("status") not in ("pass", "must-fix", "should-fix", "not-verified", "not-applicable")
                or "action_id" not in probe
                or not isinstance(probe["action_id"], (str, type(None)))):
            return _governed_actions_untrusted("malformed governance probe status or identity")
        kinds = required_kinds.get(probe["probe_id"])
        if not kinds or probe.get("status") != "pass":
            continue
        refs = probe.get("evidence_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            return _governed_actions_untrusted("malformed governance probe references")
        cited = [by_id[ref] for ref in refs if ref in by_id]
        if not kinds <= {entry.get("kind") for entry in cited}:
            return _governed_actions_untrusted("passing probe lacks persisted ledger evidence")
        for entry in cited:
            if entry.get("kind") in kinds and entry["kind"].endswith("-ledger-records"):
                digest = entry.get("sha256")
                if (
                    not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or entry["evidence_id"] != f"{entry['kind']}-{digest[7:]}"
                    or not isinstance(entry.get("source"), str)
                    or not entry["source"].endswith("#assessment-isolated")
                    or entry.get("live_verified") is not False
                ):
                    return _governed_actions_untrusted("invalid persisted ledger evidence")

    phase = manifest.get("phase")
    if not isinstance(phase, str) or phase not in _GOVERNED_ACTIONS_PHASE_KEYS:
        return _governed_actions_untrusted("the manifest declares an unknown phase")

    source = manifest.get("source")
    if not isinstance(source, dict):
        return _governed_actions_untrusted("the manifest declares no source", phase)
    commit = source.get("commit")
    if not isinstance(commit, str) or not _GOVERNED_ACTIONS_COMMIT_RE.match(commit):
        return _governed_actions_untrusted(
            "the manifest source commit is not a resolved commit sha", phase
        )
    if source.get("dirty") is not False:
        return _governed_actions_untrusted(
            "the assessment was captured from a dirty working tree", phase
        )
    if not isinstance(source_commit, str) or commit != source_commit:
        return _governed_actions_untrusted(
            "the manifest was captured from a different commit than the one checked out",
            phase,
        )

    captured_at = _parse_rfc3339(manifest.get("captured_at"))
    if captured_at is None:
        return _governed_actions_untrusted(
            "captured_at is not an RFC3339 timestamp with a timezone", phase
        )
    freshness = manifest.get("freshness")
    if not isinstance(freshness, dict):
        return _governed_actions_untrusted("the manifest declares no freshness", phase)
    if freshness.get("status") != "fresh":
        return _governed_actions_untrusted(
            "the assessment did not report itself fresh when it was captured", phase
        )
    expires_at = _parse_rfc3339(freshness.get("expires_at"))
    if expires_at is None:
        return _governed_actions_untrusted(
            "freshness.expires_at is not an RFC3339 timestamp with a timezone", phase
        )
    if moment < captured_at:
        return _governed_actions_untrusted("captured_at is in the future", phase)
    if moment > expires_at:
        return _governed_actions_untrusted("the assessment has expired", phase)

    findings = manifest.get("findings")
    if not isinstance(findings, list):
        return _governed_actions_untrusted("findings is not an array", phase)
    observed: dict[str, list[str]] = {status: [] for _key, status in _GOVERNED_ACTIONS_SUMMARY_BUCKETS}
    for finding in findings:
        if not isinstance(finding, dict):
            return _governed_actions_untrusted("a finding is not an object", phase)
        finding_id = finding.get("finding_id")
        status = finding.get("status")
        if (
            not isinstance(finding_id, str)
            or not isinstance(status, str)
            or status not in observed
        ):
            return _governed_actions_untrusted(
                "a finding carries an unknown id or status", phase
            )
        observed[status].append(finding_id)

    precedence = ("must-fix", "not-verified", "should-fix", "pass", "not-applicable")
    for probe_id, finding_id in (
        ("approval-anti-replay", "APR-001"), ("output-mediation", "OUT-001"),
        ("payload-free-audit", "AUD-001"),
    ):
        family = [p for p in probes if p["probe_id"] == probe_id]
        statuses = [f["status"] for f in findings if f["finding_id"] == finding_id]
        if not family and not any(status != "pass" for status in statuses):
            return _governed_actions_untrusted("governance control evidence is missing", phase)
        if any(p["status"] != "pass" for p in family) and (
            not statuses or min(precedence.index(status) for status in statuses)
            > min(precedence.index(p["status"]) for p in family)
        ):
            return _governed_actions_untrusted("governance probe outcome is missing from findings", phase)
        if probe_id == "approval-anti-replay":
            digest = lambda value: "sha256:" + hashlib.sha256(value.encode()).hexdigest()
            for action_id in {p["action_id"] for p in family}:
                sequence = [p for p in family if p["action_id"] == action_id]
                if not all(p["status"] == "pass" for p in sequence):
                    continue
                sequence_observations = [p.get("observed_sha256") for p in sequence]
                ledger_ids = {
                    ref for p in sequence for ref in p["evidence_refs"]
                    if ref in by_id and by_id[ref]["kind"] == "approval-ledger-records"
                }
                rejections = {digest("replay_rejected"), digest("binding_mismatch_rejected"),
                              digest("reused_nonce_rejected")}
                if (
                    len(sequence) != 14 or len(ledger_ids) != 14
                    or sequence_observations.count(digest("approval_accepted")) != 2
                    or sequence_observations.count(digest("expired_rejected")) != 1
                    or sum(isinstance(value, str) and value in rejections
                           for value in sequence_observations) != 11
                ):
                    return _governed_actions_untrusted("approval sequence evidence is incomplete", phase)

    summary = manifest.get("summary")
    expected_summary_keys = {"verdict"} | {key for key, _status in _GOVERNED_ACTIONS_SUMMARY_BUCKETS}
    if not isinstance(summary, dict) or set(summary) != expected_summary_keys:
        return _governed_actions_untrusted(
            "summary does not match the summary contract", phase
        )
    verdict = summary["verdict"]
    if not isinstance(verdict, str) or verdict not in _GOVERNED_ACTIONS_VERDICTS:
        return _governed_actions_untrusted("summary.verdict is not a known verdict", phase)
    counts: dict[str, int] = {}
    for key, status in _GOVERNED_ACTIONS_SUMMARY_BUCKETS:
        bucket = summary[key]
        if not isinstance(bucket, list) or any(not isinstance(v, str) for v in bucket):
            return _governed_actions_untrusted(
                f"summary.{key} is not an array of finding ids", phase
            )
        # Multiset comparison: duplicate ids are legal, so a dropped one is a
        # real disagreement rather than noise.
        if sorted(bucket) != sorted(observed[status]):
            return _governed_actions_untrusted(
                f"summary.{key} disagrees with the findings it summarises", phase
            )
        counts[key] = len(bucket)

    return {
        "manifest": GOVERNED_ACTIONS_MANIFEST,
        "status": "summarized",
        "trusted": True,
        "reason": "schema-valid, commit-bound, and still fresh",
        "phase": phase,
        "verdict": verdict,
        "counts": counts,
        "recommendation": [],
    }


def _git_head_commit(workspace: Path) -> str:
    """Resolved HEAD of *workspace*, or "" when it cannot be read."""
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - a missing/unusable git is just "unknown"
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _governed_actions_projection(workspace: Path) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """`(handoff, manifest_summary)` for the workspace.

    The handoff appears only when the workspace declares a consequential
    action or already carries an assessment — an unrelated pilot is never
    nagged. Both halves are advisory: nothing is dispatched, scheduled, or
    applied here.
    """
    manifest_path = workspace / GOVERNED_ACTIONS_MANIFEST
    manifest_present = manifest_path.is_file()
    if not manifest_present and not detect_consequential_actions(workspace):
        return None, None
    summary = (
        summarize_governed_actions_manifest(manifest_path, _git_head_commit(workspace))
        if manifest_present
        else None
    )
    return dict(GOVERNED_ACTIONS_HANDOFF), summary


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

    try:
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return StageDecision(
            "preflight",
            "run",
            f"Preflight marker is unreadable ({exc}); re-running.",
            artifacts_seen=[str(PREFLIGHT_MARKER)],
        )
    if not isinstance(marker_data, dict):
        return StageDecision(
            "preflight",
            "run",
            "Preflight marker is not a JSON object; re-running.",
            artifacts_seen=[str(PREFLIGHT_MARKER)],
        )

    foundation = workspace / "specs" / "foundation.md"
    current_foundation_hash = _sha256(foundation)
    if "foundation_sha256" not in marker_data:
        return StageDecision(
            "preflight",
            "run",
            "Legacy preflight marker is not bound to Foundation state; re-running.",
            artifacts_seen=[str(PREFLIGHT_MARKER)],
        )
    if marker_data["foundation_sha256"] != current_foundation_hash:
        return StageDecision(
            "preflight",
            "run",
            "Foundation was created, edited, or removed after preflight; re-running runtime-policy validation.",
            artifacts_seen=[str(PREFLIGHT_MARKER)],
        )

    return StageDecision(
        "preflight",
        "skip",
        f"Preflight marker is {int(age/60)} m old (< 24 h) and Foundation hash is unchanged.",
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


def _parse_env_assignment(value: str) -> str:
    if value.strip().startswith("#"):
        return ""
    candidate = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
        candidate = candidate[1:-1].strip()
    return candidate


def _deployment_manifest_binding_matches(workspace: Path, manifest_data: dict[str, Any]) -> bool:
    snapshot = manifest_data.get("deployment_manifest")
    current = _parse_json_object(workspace / "specs" / "manifest.json")
    if not isinstance(snapshot, dict) or not isinstance(current, dict):
        return False
    current_snapshot = current.get("deployment_manifest")
    if not isinstance(current_snapshot, dict):
        return False
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":")) == json.dumps(
        current_snapshot, sort_keys=True, separators=(",", ":")
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
        if azure_dir.is_symlink():
            return StageDecision(
                "deploy",
                "run",
                "infra + azure.yaml exist but .azure is symlinked — deploy evidence is not trusted.",
                artifacts_seen=["infra/main.bicep", "azure.yaml"],
            )
        if not azure_dir.is_dir():
            return StageDecision(
                "deploy",
                "run",
                "infra + azure.yaml exist but no AGENT_FQDN in azd env — `azd up` hasn't completed.",
                artifacts_seen=["infra/main.bicep", "azure.yaml"],
            )
        try:
            env_dirs = [entry for entry in azure_dir.iterdir() if entry.is_dir()]
        except OSError:
            return StageDecision(
                "deploy",
                "run",
                "infra + azure.yaml exist but no AGENT_FQDN in azd env — `azd up` hasn't completed.",
                artifacts_seen=["infra/main.bicep", "azure.yaml"],
            )
        if len(env_dirs) > 1:
            return StageDecision(
                "deploy",
                "run",
                "infra + azure.yaml exist but multiple azd envs are present — current deploy evidence is ambiguous.",
                artifacts_seen=["infra/main.bicep", "azure.yaml", ".azure/<env>/.env"],
            )
        env_files = [env_dirs[0] / ".env"] if len(env_dirs) == 1 else []
        has_fqdn = False
        for env_file in env_files:
            if env_file.parent.is_symlink() or env_file.is_symlink() or not env_file.is_file():
                if not env_file.exists() and not (env_file.parent.is_symlink() or env_file.is_symlink()):
                    continue
                return StageDecision(
                    "deploy",
                    "run",
                    "infra + azure.yaml exist but azd env evidence is symlinked — deploy evidence is not trusted.",
                    artifacts_seen=["infra/main.bicep", "azure.yaml"],
                )
            if not env_file.exists():
                continue
            try:
                text = env_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                match = re.match(r"^\s*AGENT_FQDN\s*=\s*(.*)$", line)
                if match and _parse_env_assignment(match.group(1)):
                    has_fqdn = True
                    break
            if has_fqdn:
                break
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
    manifest = workspace / POSTDEPLOY_MANIFEST
    if not manifest.exists():
        return StageDecision(
            "safe_check",
            "run",
            f"{POSTDEPLOY_MANIFEST} missing; safe-check requires manifest evidence, not docs alone.",
            artifacts_missing=[POSTDEPLOY_MANIFEST],
        )
    manifest_data = _parse_json_object(manifest)
    seen = [POSTDEPLOY_MANIFEST]
    if safe_doc.exists():
        seen.insert(0, "docs/safe-check-post.md")
    if manifest_data is None:
        return StageDecision(
            "safe_check",
            "run",
            f"{POSTDEPLOY_MANIFEST} is unreadable or malformed; safe-check evidence is not trustworthy.",
            artifacts_seen=seen,
        )
    if manifest_data.get("phase") != "post-deploy":
        return StageDecision(
            "safe_check",
            "run",
            f"{POSTDEPLOY_MANIFEST} phase must be post-deploy; re-running threadlight-safe-check.",
            artifacts_seen=seen,
        )
    if manifest_data.get("gaps") != []:
        return StageDecision(
            "safe_check",
            "run",
            f"{POSTDEPLOY_MANIFEST} reports unresolved gaps; re-running threadlight-safe-check.",
            artifacts_seen=seen,
        )
    if not _deployment_manifest_binding_matches(workspace, manifest_data):
        return StageDecision(
            "safe_check",
            "run",
            f"{POSTDEPLOY_MANIFEST} no longer matches specs/manifest.json; re-running threadlight-safe-check.",
            artifacts_seen=seen,
        )
    age = _postdeploy_age_seconds(manifest, manifest_data)
    if age is None or age < 0 or age >= FRESHNESS_SECONDS:
        reason = (
            f"{POSTDEPLOY_MANIFEST} has no fresh checked_at evidence; re-running threadlight-safe-check."
            if age is None
            else f"{POSTDEPLOY_MANIFEST} checked_at is in the future; re-running threadlight-safe-check."
            if age < 0
            else f"{POSTDEPLOY_MANIFEST} is {int(age/3600)} h old (>= 24 h); re-running threadlight-safe-check."
        )
        return StageDecision(
            "safe_check",
            "run",
            reason,
            artifacts_seen=seen,
        )
    return StageDecision(
        "safe_check",
        "skip",
        f"{POSTDEPLOY_MANIFEST} is green and {int(age/60)} m old (< 24 h).",
        artifacts_seen=seen,
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
    if not ts or not RFC3339_UTC_OR_OFFSET.fullmatch(ts):
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _capability_error(
    manifest_rel: str,
    capability_name: str,
    message: str,
) -> str:
    return f"{manifest_rel} capability {capability_name!r} {message}"


def _validate_leg_capabilities(
    manifest_rel: str,
    capabilities: dict[str, Any],
    contract: dict[str, Any],
) -> str | None:
    for capability_name in sorted(contract["required_capabilities"]):
        capability = capabilities.get(capability_name)
        if not isinstance(capability, dict):
            return _capability_error(manifest_rel, capability_name, "must be an object")
        if capability.get("status") not in _CAPABILITY_STATUSES:
            return _capability_error(manifest_rel, capability_name, "has invalid status")
        for field in ("evidence", "hint"):
            if field in capability and capability[field] is not None and not isinstance(capability[field], str):
                return _capability_error(manifest_rel, capability_name, f"has invalid {field!r}")
        if contract.get("require_check_id") and not isinstance(capability.get("check_id"), str):
            return _capability_error(manifest_rel, capability_name, "missing or invalid check_id")
        if "finding_id" in capability:
            if not contract.get("allow_finding_id") or capability["finding_id"] not in _REDTEAM_FINDING_IDS:
                return _capability_error(manifest_rel, capability_name, "has invalid finding_id")
        if contract.get("forbid_extra_fields"):
            allowed_fields = {"status", "evidence", "hint"}
            if contract.get("require_check_id"):
                allowed_fields.add("check_id")
            if contract.get("allow_finding_id"):
                allowed_fields.add("finding_id")
            extras = sorted(set(capability) - allowed_fields)
            if extras:
                return _capability_error(manifest_rel, capability_name, f"has unsupported fields {extras}")
    return None


def _postdeploy_age_seconds(path: Path, payload: dict[str, Any]) -> float | None:
    checked_at = _parse_iso(payload.get("checked_at", ""))
    if checked_at is None:
        return None
    return (datetime.now(timezone.utc) - checked_at).total_seconds()


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
            schema_version = data.get("schema_version")
            if isinstance(schema_version, str) and schema_version.startswith("1."):
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
        and manifest_generated_at > last_deploy_dt
    ):
        passed_at = state.get("cost_projection", {}).get("passed_at", "")
        return StageDecision(
            "cost_projection",
            "skip",
            (
                f"cost-projection: load_profile complete + manifest fresh (generated_at "
                f"{manifest_generated_at.isoformat()} > last_deploy_at {last_deploy_dt.isoformat()})."
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

    artifacts_seen = ["specs/SPEC.md"]
    artifacts_missing: list[str] = []
    reason = "specs/cost-manifest.json missing or stale — running threadlight-consumption-iq."
    if cost_manifest.exists():
        artifacts_seen.append("specs/cost-manifest.json")
        reason = (
            "specs/cost-manifest.json is stale or lacks a trusted 1.x schema_version/generated_at pair "
            "— running threadlight-consumption-iq."
        )
    else:
        artifacts_missing.append("specs/cost-manifest.json")

    return StageDecision(
        "cost_projection",
        "run",
        reason,
        artifacts_seen=artifacts_seen,
        artifacts_missing=artifacts_missing,
    )


def _check_leg_manifest(workspace: Path, stage: str) -> StageDecision:
    """Generic resumability probe for a Discover/Protect leg.

    The leg is re-run when its manifest is missing, malformed, stale by
    `captured_at`, or outside the known schema/verdict contract. Fresh valid
    non-passing verdicts still skip: threadlight-auto plans which executed legs
    to resume, it does not reinterpret advisory readiness outcomes.
    """
    contract = LEG_CONTRACTS[stage]
    manifest_rel = contract["manifest"]
    skill = contract["skill"]
    manifest = workspace / manifest_rel
    if not manifest.exists():
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} missing — {skill} has not produced its leg manifest yet.",
            artifacts_missing=[manifest_rel],
        )
    manifest_data = _parse_json_object(manifest)
    if manifest_data is None:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} is unreadable or malformed — re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    if manifest_data.get("schema") != contract["schema"]:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} schema mismatch (expected {contract['schema']}); re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    captured_at = _parse_iso(manifest_data.get("captured_at", ""))
    if captured_at is None:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} has no parseable captured_at; re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    verdict = manifest_data.get("verdict")
    if verdict not in contract["known_verdicts"]:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} verdict is unknown; re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    if not isinstance(manifest_data.get("tool_version"), str):
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} is missing or has invalid tool_version; re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    capabilities = manifest_data.get("capabilities")
    if not isinstance(capabilities, dict):
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} capabilities are missing or invalid; re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    validator = _ASSURANCE_MANIFEST_VALIDATORS.get(stage)
    if validator is not None:
        try:
            validator(manifest, manifest_data)
        except _EvidenceGateError as error:
            return StageDecision(
                stage,
                "run",
                f"{manifest_rel} failed assurance contract validation ({error}); re-running {skill}.",
                artifacts_seen=[manifest_rel],
            )
    required_capabilities = contract.get("required_capabilities")
    if required_capabilities is not None:
        capability_names = set(capabilities)
        missing_capabilities = sorted(required_capabilities - capability_names)
        extra_capabilities = sorted(capability_names - required_capabilities)
        if missing_capabilities or extra_capabilities:
            mismatch = []
            if missing_capabilities:
                mismatch.append(f"missing capabilities {missing_capabilities}")
            if extra_capabilities:
                mismatch.append(f"unsupported capabilities {extra_capabilities}")
            return StageDecision(
                stage,
                "run",
                f"{manifest_rel} has {' and '.join(mismatch)}; re-running {skill}.",
                artifacts_seen=[manifest_rel],
            )
        capability_error = _validate_leg_capabilities(manifest_rel, capabilities, contract)
        if capability_error is not None:
            return StageDecision(
                stage,
                "run",
                f"{capability_error}; re-running {skill}.",
                artifacts_seen=[manifest_rel],
            )
    age = (datetime.now(timezone.utc) - captured_at).total_seconds()
    if age > FRESHNESS_SECONDS:
        return StageDecision(
            stage,
            "run",
            f"{manifest_rel} captured_at is {int(age/3600)} h old (> 24 h); re-running {skill}.",
            artifacts_seen=[manifest_rel],
        )
    return StageDecision(
        stage,
        "skip",
        f"{manifest_rel} captured_at is {int(max(age, 0)/60)} m old (< 24 h) with verdict={verdict}.",
        artifacts_seen=[manifest_rel],
    )


# Discover leg — offline + online (Foundry Continuous Evaluation) + A/B evals.
# Runs threadlight-evals; emits specs/evals-manifest.json consumed by
# production-ready pillar 6 (EVAL-001..004).
def _check_evals(workspace: Path, _: dict[str, Any]) -> StageDecision:
    return _check_leg_manifest(workspace, "evals")


# Discover leg — AI Red Teaming Agent adversarial scan. Runs threadlight-redteam;
# emits docs/redteam-report.md + specs/redteam-manifest.json mapped to
# production-ready pillar 7 (SAFE-101..106).
def _check_redteam(workspace: Path, _: dict[str, Any]) -> StageDecision:
    return _check_leg_manifest(workspace, "redteam")


# Protect leg — agent-runtime governance (AGT). Runs threadlight-govern; emits
# the verifier report + specs/govern-manifest.json consumed by production-ready
# pillar 2 (AGT-001..005) and pillar 7 (RAI-002/003).
def _check_govern(workspace: Path, _: dict[str, Any]) -> StageDecision:
    return _check_leg_manifest(workspace, "govern")


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


def decide(workspace: Path, state_path: Path | None = None) -> dict[str, Any]:
    state = _read_state(state_path) if state_path else {}
    decisions: list[StageDecision] = []
    for stage in STAGES:
        probe = STAGE_PROBES[stage]
        decisions.append(probe(workspace, state))
    decisions = _cascade_invalidations(decisions)

    hard_stop = next((d for d in decisions if d.decision == "hard_stop"), None)
    governed_handoff, governed_manifest = _governed_actions_projection(workspace)

    return {
        "workspace": str(workspace),
        "state_file": str(state_path) if state_path else None,
        "stages": list(STAGES),
        "manual_handoffs": _manual_handoffs(workspace),
        # Recommendation only: `threadlight-governed-actions` is never a stage
        # and is never dispatched from here.
        "governed_actions": governed_handoff,
        "governed_actions_manifest": governed_manifest,
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
    _print_governed_actions(report)


def _print_governed_actions(report: dict[str, Any]) -> None:
    """Print the governed-actions recommendation, when there is one.

    Recommendation only — auto never runs the skill, and never prints an
    instruction beyond the approved lifecycle wording.
    """
    handoff = report.get("governed_actions")
    if not handoff:
        return
    print(f"\nGoverned actions (execution: {handoff['execution']}):")
    for key in ("design", "pre_deploy", "post_deploy"):
        print(f"  • {handoff[key]}")
    summary = report.get("governed_actions_manifest")
    if summary:
        print(f"  {summary['manifest']}: {summary['status']} — {summary['reason']}")


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
