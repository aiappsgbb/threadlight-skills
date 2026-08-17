#!/usr/bin/env python3
"""ground.py — the GROUND leg: turn already-produced retrieval/evaluation
evidence into a schema-validated grounding-safety verdict.

`threadlight-ground` is a **coordinator**, not a retrieval or evaluation
engine. Provisioning and query planning stay with `foundry-iq` (Azure AI
Search Knowledge Agent); quality/relevance scoring stays with
`threadlight-evals`. This script never calls Foundry IQ, never runs an
evaluator, and never issues a retrieval query itself — it only ingests probe
*results* the caller already captured (an ACL probe run, a citation
validation run, a refusal probe run) and turns them into four findings:

    GRD-001  ACL enforcement       — did incompatible principals get
                                      identical protected results?
    GRD-002  citation grounding    — does every citation trace back to the
                                      retrieved set?
    GRD-003  refusal behavior      — are unsupported queries actually
                                      refused, not answered?
    GRD-004  freshness / coverage  — does every declared knowledge source
                                      have fresh, covering evidence?

This is a **manual, live handoff** — `threadlight-auto` does not run live
ACL/citation/refusal probes against a real agent for you. Running those
probes against production or pilot data is an operator decision; this script
only assesses evidence the operator already captured and supplies.

Evidence-quality contract: missing principals, missing permission signals, or
no runs at all are reported as `not-verified` — never guessed into a false
`pass`. A *proven* leak (incompatible entitled/unentitled principals
receiving the identical protected document set) is `must-fix`, never
downgraded. An **executed** must-fix finding is still complete evidence — the
manifest `status` is only `partial` when required evidence is genuinely
missing or `not-verified`, never merely because a finding failed.

Persistence contract: the manifest persists only source metadata, principal
identifiers, document IDs, findings, metrics, and aggregate
retrieval-count/subqueries/tokens/fan-out. It never persists retrieved
content, prompts, completions, access tokens, credentials, or customer
payloads — every write is schema-validated and scanned for forbidden
credential/content-shaped keys before anything touches disk, so malformed or
oversharing evidence never corrupts (or even touches) a prior valid manifest.

stdlib-only. No network calls, no Foundry IQ SDK, no evaluator invocation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared envelope (skills/_shared/manifest.py) — insert repo root on sys.path
# so `skills._shared.manifest` resolves as an implicit namespace package both
# in-repo and when this script is invoked standalone.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills._shared.manifest import (  # noqa: E402
    ManifestValidationError,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)

VERSION = "0.1.0"
GROUND_MANIFEST_SCHEMA = "threadlight.ground/v1"

DEFAULT_MANIFEST_PATH = "specs/ground-manifest.json"

FINDING_STATUS_ENUM = frozenset({"pass", "must-fix", "should-fix", "not-verified"})

# Statuses that make the manifest `status` "partial" — required evidence is
# genuinely missing/unverifiable. An *executed* must-fix/should-fix is still
# complete evidence, so it never appears here.
_PARTIAL_STATUSES = frozenset({"not-verified"})

# Forbidden key names scanned for RECURSIVELY, everywhere in the final
# manifest, right before it is written. Covers both credential-shaped keys
# (mirrors threadlight-connect) and the grounding-specific "never persist"
# list: retrieved content, prompts, completions, and customer payloads.
#
# `_FORBIDDEN_KEY_WORDS` are matched as WHOLE snake/kebab-case segments (not
# substrings) so a legitimate metric field like `tokens` (a token COUNT) is
# never confused with a credential-shaped `access_token`; `_FORBIDDEN_KEY_SUBSTRINGS`
# are compound markers distinctive enough that a plain substring match is safe.
_FORBIDDEN_KEY_WORDS = frozenset({
    "token", "secret", "password", "credential", "credentials",
    "authorization", "content", "prompt", "completion", "completions", "payload",
})
_FORBIDDEN_KEY_SUBSTRINGS = ("api_key", "apikey", "access_key", "connection_string")


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in _FORBIDDEN_KEY_SUBSTRINGS):
        return True
    words = re.split(r"[^a-z0-9]+", lowered)
    return any(word in _FORBIDDEN_KEY_WORDS for word in words)

_SOURCE_STRING_KEYS = ("id", "type", "permission_model", "refresh_cadence")
_SOURCE_BOOL_KEYS = ("citation_required", "refuse_when_unsupported")

# Best-effort grace windows (hours) a source's declared refresh_cadence is
# allowed before its evidence is flagged stale in GRD-004. Unknown/custom
# cadence strings are never guessed at — they skip the staleness check but
# still count for coverage.
_CADENCE_GRACE_HOURS = {
    "hourly": 4,
    "daily": 48,
    "weekly": 24 * 9,
    "monthly": 24 * 35,
}

_ACL_STATUS_ORDER = {"must-fix": 0, "not-verified": 1, "pass": 2}


class GroundValidationError(ValueError):
    """Raised when SPEC config, evidence, or a built manifest is the wrong
    shape. Always raised before any file write — a run that fails here never
    disturbs whatever valid `ground-manifest.json` already existed.
    """


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _finding(finding_id: str, status: str, detail: Any = None) -> dict:
    finding = {"id": finding_id, "status": status}
    if detail is not None:
        finding["detail"] = detail
    return finding


def _parse_rfc3339(value: Any):
    """Best-effort RFC3339 parse. Returns None (never raises) for anything
    that is not a parseable timestamp — malformed/missing `captured_at`
    values are simply excluded from freshness computations, they never abort
    the whole assessment.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def oldest_timestamp(runs: list) -> str | None:
    """Oldest `captured_at` across a flat list of evidence runs, as the
    ORIGINAL string (never reformatted). Returns None when no run carries a
    parseable `captured_at` — never back-filled from `generated_at`.
    """
    parsed = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        dt = _parse_rfc3339(run.get("captured_at"))
        if dt is not None:
            parsed.append((dt, run["captured_at"]))
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[0])
    return parsed[0][1]


def _as_numeric(value: Any, label: str):
    """Strict numeric coercion for telemetry fields: absent -> 0 (a run that
    simply didn't report the metric); present-but-non-numeric (a string, a
    bool, NaN/inf) -> a clear GroundValidationError. Never silently coerced.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        raise GroundValidationError(f"{label} must be numeric, got a boolean")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise GroundValidationError(f"{label} must be a finite number")
        return value
    raise GroundValidationError(
        f"{label} must be numeric, got {type(value).__name__}"
    )


def aggregate_telemetry(runs: list) -> dict:
    """Sum only numeric `subqueries`/`tokens` across *runs* and record
    `retrieval_count` (the number of runs). Malformed (non-numeric) values
    raise a `GroundValidationError` rather than being coerced/ignored.
    """
    subqueries_total: float = 0
    tokens_total: float = 0
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise GroundValidationError(f"telemetry run[{index}] must be an object")
        subqueries_total += _as_numeric(
            run.get("subqueries"), f"telemetry run[{index}].subqueries"
        )
        tokens_total += _as_numeric(run.get("tokens"), f"telemetry run[{index}].tokens")
    return {
        "retrieval_count": len(runs),
        "subqueries": subqueries_total,
        "tokens": tokens_total,
    }


# ---------------------------------------------------------------------------
# SPEC knowledge-source sanitization — whitelist-only, never verbatim
# ---------------------------------------------------------------------------
def _sanitize_source(source: Any, index: int) -> dict:
    label = f"sources[{index}]"
    if not isinstance(source, dict):
        raise GroundValidationError(f"{label} must be an object")

    # Fail loud on any forbidden-shaped key BEFORE whitelisting — an operator
    # passing e.g. an `access_token` alongside a source config gets a clear
    # error, rather than having it silently (if safely) dropped.
    forbidden = [key for key in source if isinstance(key, str) and _is_forbidden_key(key)]
    if forbidden:
        raise GroundValidationError(
            f"{label} must not contain credential/content/prompt-shaped key(s): "
            + ", ".join(sorted(forbidden))
        )

    sanitized: dict[str, Any] = {}
    for key in _SOURCE_STRING_KEYS:
        value = source.get(key)
        if not isinstance(value, str) or not value:
            raise GroundValidationError(f"{label}.{key} must be a non-empty string")
        sanitized[key] = value

    for key in _SOURCE_BOOL_KEYS:
        value = source.get(key)
        if not isinstance(value, bool):
            raise GroundValidationError(f"{label}.{key} must be a boolean")
        sanitized[key] = value

    principals = source.get("acl_probe_principals")
    if principals is not None:
        if not isinstance(principals, list) or not all(
            isinstance(p, str) and p for p in principals
        ):
            raise GroundValidationError(
                f"{label}.acl_probe_principals must be a list of non-empty strings"
            )
        sanitized["acl_probe_principals"] = list(principals)

    return sanitized


# ---------------------------------------------------------------------------
# GRD-001 — ACL enforcement
# ---------------------------------------------------------------------------
def _acl_permission_required(sources: list) -> bool:
    return any(
        isinstance(s, dict) and s.get("permission_model") not in (None, "public", "none")
        for s in sources
    )


def _infer_entitlement(run: dict):
    """Resolve whether *run*'s principal was EXPECTED to be entitled.

    Resolution order: (1) an explicit `expected_entitled` (or `entitled`)
    boolean on the run — this is the authoritative signal callers should
    supply from real permission checks, never inferred; (2) a best-effort
    fallback that looks at the principal's name for common demo/test
    conventions. The fallback exists only so obviously-named fixtures (e.g.
    `entitled` / `unentitled`) work without extra ceremony — it deliberately
    returns None (unknown) for anything it cannot confidently classify, so an
    ambiguous name never produces a naive false positive/negative; the caller
    should supply `expected_entitled` explicitly for real evidence.
    """
    for key in ("expected_entitled", "entitled"):
        value = run.get(key)
        if isinstance(value, bool):
            return value

    principal = str(run.get("principal") or "").strip().lower()
    if not principal:
        return None
    if any(
        marker in principal
        for marker in ("unentitled", "denied", "unauthorized", "no-access", "no_access", "guest")
    ):
        return False
    if any(marker in principal for marker in ("entitled", "authorized", "admin", "owner")):
        return True
    return None


def _assess_acl_group(runs: list) -> dict:
    classified = []
    for run in runs:
        principal = run.get("principal")
        document_ids = run.get("document_ids")
        if not isinstance(principal, str) or not principal.strip():
            return {"status": "not-verified", "detail": "an ACL run is missing its principal"}
        if not isinstance(document_ids, list):
            return {
                "status": "not-verified",
                "detail": f"ACL run for principal {principal!r} is missing document_ids",
            }
        entitled = _infer_entitlement(run)
        if entitled is None:
            return {
                "status": "not-verified",
                "detail": (
                    f"ACL run for principal {principal!r} has no determinable "
                    "entitlement/permissions (supply expected_entitled explicitly)"
                ),
            }
        classified.append((principal, entitled, frozenset(document_ids)))

    principals = {p for p, _, _ in classified}
    if len(principals) < 2:
        return {
            "status": "not-verified",
            "detail": "at least two distinct principals are required to probe ACL enforcement",
        }

    entitled_sets = [docs for _, entitled, docs in classified if entitled is True]
    unentitled_sets = [docs for _, entitled, docs in classified if entitled is False]
    if not entitled_sets or not unentitled_sets:
        return {
            "status": "not-verified",
            "detail": "both an entitled and an unentitled principal probe are required",
        }

    for entitled_docs in entitled_sets:
        for unentitled_docs in unentitled_sets:
            if entitled_docs and entitled_docs == unentitled_docs:
                return {
                    "status": "must-fix",
                    "detail": (
                        "an unentitled principal received the identical protected "
                        f"document set as an entitled principal: {sorted(entitled_docs)}"
                    ),
                }

    return {
        "status": "pass",
        "detail": "entitled and unentitled principals received distinct results",
    }


def assess_acl(sources: list, acl_runs: list) -> dict:
    """GRD-001 — ACL enforcement.

    Missing principals, missing ACL runs, or a run whose permission/entitlement
    cannot be determined all yield `not-verified` (never a false pass). A
    PROVEN leak — incompatible (entitled vs. unentitled) principals receiving
    the identical protected document set — yields `must-fix`. When multiple
    ACL-protected sources are probed (grouped by `source_id`), the worst
    per-source result wins.
    """
    finding_id = "GRD-001"
    if not _acl_permission_required(sources):
        return _finding(finding_id, "pass", "no ACL-protected sources declared")
    if not acl_runs:
        return _finding(finding_id, "not-verified", "no ACL probe runs supplied")

    groups: dict[Any, list] = {}
    for run in acl_runs:
        if not isinstance(run, dict):
            return _finding(finding_id, "not-verified", "an ACL run is not an object")
        groups.setdefault(run.get("source_id"), []).append(run)

    results = {key: _assess_acl_group(runs) for key, runs in groups.items()}

    if len(results) == 1:
        (only,) = results.values()
        return _finding(finding_id, only["status"], only["detail"])

    worst_key = min(results, key=lambda key: _ACL_STATUS_ORDER[results[key]["status"]])
    worst = results[worst_key]
    return _finding(
        finding_id,
        worst["status"],
        {
            "worst_source": worst_key,
            "reason": worst["detail"],
            "by_source": {str(key): value["status"] for key, value in results.items()},
        },
    )


# ---------------------------------------------------------------------------
# GRD-002 — citation grounding
# ---------------------------------------------------------------------------
def validate_citations(citations: list, retrieved_ids: list) -> dict:
    """A citation is valid only when it is a member of the retrieved set.
    Returns `must-fix` with the sorted, de-duplicated list of citations that
    fall outside the retrieved set, or `pass` when every citation is covered.
    """
    retrieved = set(retrieved_ids or [])
    missing = sorted({c for c in (citations or []) if c not in retrieved})
    if missing:
        return {"status": "must-fix", "missing_from_retrieval": missing}
    return {"status": "pass", "missing_from_retrieval": []}


def assess_citations(citation_runs: list) -> dict:
    """GRD-002 — citation grounding. No runs at all -> `not-verified`
    (absent execution is never a pass). Any run whose citations fall outside
    its retrieved set -> `must-fix`. Otherwise `pass`.
    """
    finding_id = "GRD-002"
    if not citation_runs:
        return _finding(finding_id, "not-verified", "no citation validation runs supplied")

    missing_all: list[str] = []
    for index, run in enumerate(citation_runs):
        if not isinstance(run, dict):
            return _finding(finding_id, "not-verified", f"citation run[{index}] is not an object")
        citations = run.get("citations")
        retrieved_ids = run.get("retrieved_ids")
        if citations is None or retrieved_ids is None:
            return _finding(
                finding_id,
                "not-verified",
                f"citation run[{index}] is missing citations/retrieved_ids",
            )
        result = validate_citations(citations, retrieved_ids)
        if result["status"] == "must-fix":
            missing_all.extend(result["missing_from_retrieval"])

    if missing_all:
        return _finding(
            finding_id,
            "must-fix",
            {"missing_from_retrieval": sorted(set(missing_all))},
        )
    return _finding(finding_id, "pass", "every citation was present in its retrieved set")


# ---------------------------------------------------------------------------
# GRD-003 — refusal behavior
# ---------------------------------------------------------------------------
def assess_refusal(refusal_runs: list) -> dict:
    """GRD-003 — refusal behavior for unsupported queries. No runs -> `not-verified`.
    An EXECUTED probe where an unsupported query was answered instead of
    refused -> `must-fix`. Otherwise `pass`.
    """
    finding_id = "GRD-003"
    if not refusal_runs:
        return _finding(finding_id, "not-verified", "no refusal probe runs supplied")

    failed_ids: list[str] = []
    for index, run in enumerate(refusal_runs):
        if not isinstance(run, dict) or not isinstance(run.get("refused"), bool):
            return _finding(
                finding_id,
                "not-verified",
                f"refusal run[{index}] is missing a boolean 'refused' result",
            )
        if not run["refused"]:
            failed_ids.append(str(run.get("query_id", index)))

    if failed_ids:
        return _finding(
            finding_id,
            "must-fix",
            {"unsupported_queries_answered": sorted(failed_ids)},
        )
    return _finding(finding_id, "pass", "every unsupported-query probe was refused")


# ---------------------------------------------------------------------------
# GRD-004 — source freshness / coverage
# ---------------------------------------------------------------------------
def assess_freshness_coverage(
    sources: list, all_runs: list, *, generated_at: str
) -> dict:
    """GRD-004 — every declared source should have evidence that (a) exists at
    all (coverage) and (b) is fresh relative to its declared `refresh_cadence`
    (freshness). No sources declared -> `pass` (nothing to ground). Sources
    declared but zero evidence at all -> `not-verified`. Partial coverage or
    stale evidence (relative to cadence) -> `should-fix`. Otherwise `pass`.
    """
    finding_id = "GRD-004"
    if not sources:
        return _finding(finding_id, "pass", "no knowledge sources declared")
    if not all_runs:
        return _finding(
            finding_id, "not-verified", "no grounding evidence supplied for any declared source"
        )

    covered_ids = {
        run.get("source_id")
        for run in all_runs
        if isinstance(run, dict) and run.get("source_id")
    }
    declared_ids = {s["id"] for s in sources}
    uncovered = sorted(declared_ids - covered_ids)

    oldest = oldest_timestamp(all_runs)
    stale_sources: list[str] = []
    if oldest is not None:
        now = _parse_rfc3339(generated_at)
        oldest_dt = _parse_rfc3339(oldest)
        if now is not None and oldest_dt is not None:
            age_hours = (now - oldest_dt).total_seconds() / 3600.0
            for source in sources:
                limit = _CADENCE_GRACE_HOURS.get(source["refresh_cadence"])
                if limit is not None and age_hours > limit:
                    stale_sources.append(source["id"])

    if uncovered or stale_sources:
        detail: dict[str, Any] = {}
        if uncovered:
            detail["uncovered_sources"] = uncovered
        if stale_sources:
            detail["stale_sources"] = sorted(stale_sources)
        return _finding(finding_id, "should-fix", detail)

    return _finding(finding_id, "pass", "all declared sources have fresh, covering evidence")


# ---------------------------------------------------------------------------
# Evidence summarization — persist ONLY safe fields, never verbatim runs
# ---------------------------------------------------------------------------
def _summarize_acl_runs(acl_runs: list) -> list:
    summary = []
    for run in acl_runs:
        if not isinstance(run, dict):
            continue
        document_ids = run.get("document_ids")
        summary.append(
            {
                "principal": run.get("principal"),
                "document_ids": sorted(document_ids) if isinstance(document_ids, list) else [],
                "source_id": run.get("source_id"),
            }
        )
    return summary


def _summarize_citation_runs(citation_runs: list) -> list:
    summary = []
    for run in citation_runs:
        if not isinstance(run, dict):
            continue
        citations = run.get("citations") or []
        retrieved_ids = run.get("retrieved_ids") or []
        result = validate_citations(citations, retrieved_ids)
        summary.append(
            {
                "source_id": run.get("source_id"),
                "citation_count": len(citations),
                "retrieved_count": len(retrieved_ids),
                "missing_from_retrieval": result["missing_from_retrieval"],
            }
        )
    return summary


def _summarize_refusal_runs(refusal_runs: list) -> list:
    summary = []
    for index, run in enumerate(refusal_runs):
        if not isinstance(run, dict):
            continue
        raw_query_id = run.get("query_id")
        query_id = str(raw_query_id) if raw_query_id not in (None, "") else str(index)
        summary.append(
            {
                "source_id": run.get("source_id"),
                "query_id": query_id,
                "refused": run.get("refused") if isinstance(run.get("refused"), bool) else None,
            }
        )
    return summary


# ---------------------------------------------------------------------------
# Coordinator — assess_grounding()
# ---------------------------------------------------------------------------
def assess_grounding(
    *,
    sources: list,
    acl_runs: list,
    citation_runs: list,
    refusal_runs: list,
    generated_at: str,
) -> dict:
    """Build the `threadlight.ground/v1` manifest from already-produced
    retrieval/evaluation evidence. Never ingests Foundry IQ itself, never
    calls an evaluator — every run here is a caller-supplied result.

    `status` is `partial` exactly when a required piece of evidence is
    missing/`not-verified`; an EXECUTED `must-fix`/`should-fix` finding is
    still complete evidence and never downgrades `status` on its own.
    """
    for name, value in (
        ("sources", sources),
        ("acl_runs", acl_runs),
        ("citation_runs", citation_runs),
        ("refusal_runs", refusal_runs),
    ):
        if not isinstance(value, list):
            raise GroundValidationError(f"{name} must be a list")

    sanitized_sources = [
        _sanitize_source(source, index) for index, source in enumerate(sources)
    ]

    acl_finding = assess_acl(sanitized_sources, acl_runs)
    citation_finding = assess_citations(citation_runs)
    refusal_finding = assess_refusal(refusal_runs)

    all_runs = [*acl_runs, *citation_runs, *refusal_runs]
    freshness_finding = assess_freshness_coverage(
        sanitized_sources, all_runs, generated_at=generated_at
    )

    findings = [acl_finding, citation_finding, refusal_finding, freshness_finding]
    status = (
        "partial"
        if any(f["status"] in _PARTIAL_STATUSES for f in findings)
        else "complete"
    )

    payload = {
        "sources": sanitized_sources,
        "acl_evidence": _summarize_acl_runs(acl_runs),
        "citation_evidence": _summarize_citation_runs(citation_runs),
        "refusal_evidence": _summarize_refusal_runs(refusal_runs),
        "telemetry": aggregate_telemetry(all_runs),
    }

    manifest = build_envelope(
        schema=GROUND_MANIFEST_SCHEMA,
        tool_version=VERSION,
        status=status,
        generated_at=generated_at,
        valid_for_hours=24,
        source_oldest_at=oldest_timestamp(all_runs),
        findings=findings,
        payload=payload,
    )
    _reject_forbidden_keys(manifest)
    return manifest


# ---------------------------------------------------------------------------
# Schema validation — hand-rolled mirror of ground-manifest.schema.json
# ---------------------------------------------------------------------------
def _contains_forbidden_keys(obj: Any) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and _is_forbidden_key(key):
                return True
            if _contains_forbidden_keys(value):
                return True
        return False
    if isinstance(obj, list):
        return any(_contains_forbidden_keys(item) for item in obj)
    return False


def _reject_forbidden_keys(manifest: dict) -> None:
    if _contains_forbidden_keys(manifest):
        raise GroundValidationError(
            "ground manifest must not contain credential/content/prompt-shaped keys"
        )


def _require_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{label} must be an object")
    return value


def _require_keys(value: dict, required: set, label: str) -> None:
    missing = required.difference(value)
    if missing:
        raise ManifestValidationError(
            f"{label} missing required key(s): " + ", ".join(sorted(missing))
        )


def _reject_unknown_keys(value: dict, allowed: set, label: str) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise ManifestValidationError(
            f"{label} has unknown key(s): " + ", ".join(sorted(unknown))
        )


def _require_string(value, label: str, *, min_length: int = 0) -> None:
    if not isinstance(value, str) or len(value) < min_length:
        suffix = "a non-empty string" if min_length else "a string"
        raise ManifestValidationError(f"{label} must be {suffix}")


def _require_nullable_string(value, label: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ManifestValidationError(f"{label} must be a string or null")


def _require_boolean(value, label: str) -> None:
    if not isinstance(value, bool):
        raise ManifestValidationError(f"{label} must be a boolean")


def _require_nullable_boolean(value, label: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise ManifestValidationError(f"{label} must be a boolean or null")


def _require_array(value, label: str) -> list:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{label} must be an array")
    return value


def _require_string_array(value, label: str) -> None:
    for index, item in enumerate(_require_array(value, label)):
        _require_string(item, f"{label}[{index}]")


def _require_number(value, label: str, *, minimum=None) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestValidationError(f"{label} must be a number")
    if not math.isfinite(value):
        raise ManifestValidationError(f"{label} must be a finite number")
    if minimum is not None and value < minimum:
        raise ManifestValidationError(f"{label} must be >= {minimum}")


_SOURCE_KEYS = {
    "id", "type", "permission_model", "refresh_cadence",
    "citation_required", "refuse_when_unsupported", "acl_probe_principals",
}
_ACL_EVIDENCE_KEYS = {"principal", "document_ids", "source_id"}
_CITATION_EVIDENCE_KEYS = {
    "source_id", "citation_count", "retrieved_count", "missing_from_retrieval",
}
_REFUSAL_EVIDENCE_KEYS = {"source_id", "query_id", "refused"}
_TELEMETRY_KEYS = {"retrieval_count", "subqueries", "tokens"}
_FINDING_KEYS = {"id", "status", "detail"}
_MANIFEST_TOP_LEVEL_KEYS = {
    "schema", "tool_version", "generated_at", "freshness", "status", "findings",
    "sources", "acl_evidence", "citation_evidence", "refusal_evidence", "telemetry",
}


def validate_ground_manifest(manifest: dict) -> None:
    """Hand-rolled schema check mirroring
    `references/ground-manifest.schema.json`, layered on the shared
    envelope's own validation. stdlib-only — no `jsonschema` runtime
    dependency; a test-only jsonschema parity suite pins this to the schema.
    """
    validate_envelope(manifest)
    manifest = _require_object(manifest, "ground manifest")
    _require_keys(
        manifest,
        {
            "sources", "acl_evidence", "citation_evidence", "refusal_evidence",
            "telemetry",
        },
        "ground manifest",
    )
    _reject_unknown_keys(manifest, _MANIFEST_TOP_LEVEL_KEYS, "ground manifest")

    if manifest["schema"] != GROUND_MANIFEST_SCHEMA:
        raise ManifestValidationError(f"schema must be {GROUND_MANIFEST_SCHEMA!r}")

    for index, finding in enumerate(_require_array(manifest["findings"], "findings")):
        label = f"findings[{index}]"
        finding = _require_object(finding, label)
        _require_keys(finding, {"id", "status"}, label)
        _reject_unknown_keys(finding, _FINDING_KEYS, label)
        _require_string(finding["id"], f"{label}.id", min_length=1)
        if finding["status"] not in FINDING_STATUS_ENUM:
            raise ManifestValidationError(
                f"{label}.status must be one of {sorted(FINDING_STATUS_ENUM)}"
            )

    for index, source in enumerate(_require_array(manifest["sources"], "sources")):
        label = f"sources[{index}]"
        source = _require_object(source, label)
        _require_keys(
            source,
            {
                "id", "type", "permission_model", "refresh_cadence",
                "citation_required", "refuse_when_unsupported",
            },
            label,
        )
        _reject_unknown_keys(source, _SOURCE_KEYS, label)
        for key in _SOURCE_STRING_KEYS:
            _require_string(source[key], f"{label}.{key}", min_length=1)
        for key in _SOURCE_BOOL_KEYS:
            _require_boolean(source[key], f"{label}.{key}")
        if "acl_probe_principals" in source:
            _require_string_array(
                source["acl_probe_principals"], f"{label}.acl_probe_principals"
            )

    for index, item in enumerate(_require_array(manifest["acl_evidence"], "acl_evidence")):
        label = f"acl_evidence[{index}]"
        item = _require_object(item, label)
        _require_keys(item, _ACL_EVIDENCE_KEYS, label)
        _reject_unknown_keys(item, _ACL_EVIDENCE_KEYS, label)
        _require_nullable_string(item["principal"], f"{label}.principal")
        _require_string_array(item["document_ids"], f"{label}.document_ids")
        _require_nullable_string(item["source_id"], f"{label}.source_id")

    for index, item in enumerate(
        _require_array(manifest["citation_evidence"], "citation_evidence")
    ):
        label = f"citation_evidence[{index}]"
        item = _require_object(item, label)
        _require_keys(item, _CITATION_EVIDENCE_KEYS, label)
        _reject_unknown_keys(item, _CITATION_EVIDENCE_KEYS, label)
        _require_nullable_string(item["source_id"], f"{label}.source_id")
        _require_number(item["citation_count"], f"{label}.citation_count", minimum=0)
        _require_number(item["retrieved_count"], f"{label}.retrieved_count", minimum=0)
        _require_string_array(
            item["missing_from_retrieval"], f"{label}.missing_from_retrieval"
        )

    for index, item in enumerate(
        _require_array(manifest["refusal_evidence"], "refusal_evidence")
    ):
        label = f"refusal_evidence[{index}]"
        item = _require_object(item, label)
        _require_keys(item, _REFUSAL_EVIDENCE_KEYS, label)
        _reject_unknown_keys(item, _REFUSAL_EVIDENCE_KEYS, label)
        _require_nullable_string(item["source_id"], f"{label}.source_id")
        _require_string(item["query_id"], f"{label}.query_id", min_length=1)
        _require_nullable_boolean(item["refused"], f"{label}.refused")

    telemetry = _require_object(manifest["telemetry"], "telemetry")
    _require_keys(telemetry, _TELEMETRY_KEYS, "telemetry")
    _reject_unknown_keys(telemetry, _TELEMETRY_KEYS, "telemetry")
    _require_number(telemetry["retrieval_count"], "telemetry.retrieval_count", minimum=0)
    _require_number(telemetry["subqueries"], "telemetry.subqueries", minimum=0)
    _require_number(telemetry["tokens"], "telemetry.tokens", minimum=0)

    _reject_forbidden_keys(manifest)


def write_ground_manifest(path, manifest: dict) -> None:
    """Schema-validate + forbidden-key-scan, THEN atomically write. A prior
    valid manifest at *path* is untouched unless every check above passes —
    `atomic_write_json` also never leaves a partial file behind on failure.
    """
    validate_ground_manifest(manifest)
    atomic_write_json(path, manifest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_ground(root: str, evidence: dict, generated_at: str | None = None) -> dict:
    """Load an evidence bundle (already-produced probe results, e.g. from a
    manual live handoff) and build the manifest. Never touches disk itself —
    callers decide whether/where to `write_ground_manifest`.
    """
    return assess_grounding(
        sources=evidence.get("sources", []),
        acl_runs=evidence.get("acl_runs", []),
        citation_runs=evidence.get("citation_runs", []),
        refusal_runs=evidence.get("refusal_runs", []),
        generated_at=generated_at or evidence.get("generated_at") or _now_iso(),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "threadlight-ground — assess already-produced ACL/citation/refusal "
            "evidence and emit specs/ground-manifest.json"
        )
    )
    parser.add_argument("--project-root", default=".", help="pilot repo root (default cwd)")
    parser.add_argument(
        "--evidence-file",
        required=True,
        help=(
            "JSON file with keys sources, acl_runs, citation_runs, refusal_runs "
            "(all already-produced probe results) and optional generated_at"
        ),
    )
    parser.add_argument(
        "--manifest-path",
        default=DEFAULT_MANIFEST_PATH,
        help="where to write the manifest, relative to --project-root",
    )
    parser.add_argument("--emit", action="store_true", help="write the manifest to disk")
    parser.add_argument("--json", action="store_true", help="print manifest JSON to stdout")
    parser.add_argument(
        "--gate", action="store_true", help="exit 2 when any finding is must-fix"
    )
    args = parser.parse_args(argv)

    root = os.path.abspath(args.project_root)
    evidence_path = (
        args.evidence_file
        if os.path.isabs(args.evidence_file)
        else os.path.join(root, args.evidence_file)
    )
    try:
        with open(evidence_path, "r", encoding="utf-8") as handle:
            evidence = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"error: could not read evidence file {evidence_path}: {exc}")
        return 1
    if not isinstance(evidence, dict):
        print(f"error: evidence file {evidence_path} must contain a JSON object")
        return 1

    try:
        manifest = run_ground(root, evidence)
    except (GroundValidationError, ManifestValidationError) as exc:
        print(f"error: {exc}")
        return 1

    if args.emit:
        manifest_full_path = os.path.join(root, args.manifest_path)
        write_ground_manifest(manifest_full_path, manifest)

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        must_fix = [f["id"] for f in manifest["findings"] if f["status"] == "must-fix"]
        not_verified = [f["id"] for f in manifest["findings"] if f["status"] == "not-verified"]
        print(f"status: {manifest['status']}")
        print(f"must-fix: {', '.join(must_fix) or 'none'}")
        print(f"not-verified: {', '.join(not_verified) or 'none'}")

    if args.gate and any(f["status"] == "must-fix" for f in manifest["findings"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
