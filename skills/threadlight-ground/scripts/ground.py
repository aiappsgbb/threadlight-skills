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

    GRD-001  ACL enforcement       — did unentitled principals receive
                                      documents they were not allowed to?
    GRD-002  citation grounding    — does every citation trace back to the
                                      retrieved set?
    GRD-003  refusal behavior      — are unsupported queries actually
                                      refused, not answered?
    GRD-004  freshness / coverage  — does every declared knowledge source
                                      have fresh, covering evidence, and is a
                                      retrieval-quality baseline referenced?

The **`sources` argument is the authoritative inventory** — it is the
SPEC-derived `knowledge_sources[]` input contract (see
`threadlight-design/references/speckit-template.md`), NOT anything the CLI's
`--project-root` supplies. Every declared source's *enabled* controls must be
covered by evidence that carries that source's `source_id`: ACL evidence when
`permission_model == "acl"`, citation evidence when `citation_required`,
refusal evidence when `refuse_when_unsupported`. Coverage is never inferred
from another source. Missing source/control evidence yields the relevant
finding `not-verified` (never a guessed `pass`) and the manifest `status`
`partial`.

This is a **manual, live handoff** — `threadlight-auto` does not run live
ACL/citation/refusal probes against a real agent for you. Running those
probes against production or pilot data is an operator decision; this script
only assesses evidence the operator already captured and supplies.

Evidence-quality contract: missing principals, missing permission signals, an
uncovered required source, or no runs at all are reported as `not-verified` —
never guessed into a false `pass`. A *proven* leak (an unentitled principal
receiving a document outside its explicit allowlist — including a subset) is
`must-fix`, never downgraded. An **executed** must-fix finding is still
complete evidence — the manifest `status` is only `partial` when required
evidence is genuinely missing or `not-verified`, never merely because a
finding failed.

Persistence contract: the manifest persists only source metadata, principal
identifiers, document IDs, findings (whose `detail` is an allowlisted schema
of IDs/counts/status/reason-enums only — never free-form notes), metrics, a
retrieval-quality baseline *reference* (never its content), and aggregate
retrieval-count/subqueries/tokens/fan-out. It never persists retrieved
content, prompts, completions, access tokens, credentials, or customer
payloads — every write is schema-validated and scanned for forbidden
credential/content-shaped keys AND secret-shaped values before anything
touches disk, so malformed or oversharing evidence never corrupts (or even
touches) a prior valid manifest.

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
from urllib.parse import parse_qs, urlsplit

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

# Permission models that make ACL enforcement a *required* control for a
# source. The input contract (speckit-template `knowledge_sources[]`) uses the
# literal string "acl" to declare that access control must be *proven*, not
# merely intended — so that is the trigger for a required GRD-001 ACL probe.
_ACL_PERMISSION_MODELS = frozenset({"acl"})

# Every reason a finding can carry. `detail.reason` is a controlled
# vocabulary (never a free-form note), enumerated here and mirrored verbatim
# in references/ground-manifest.schema.json — the parity suite pins them.
_FINDING_REASON_ENUM = frozenset({
    # GRD-001 ACL enforcement
    "no-acl-protected-sources",
    "acl-source-uncovered",
    "ambiguous-entitlement",
    "insufficient-principals",
    "missing-entitled-or-unentitled-probe",
    "declared-principal-uncovered",
    "unauthorized-documents",
    "acl-enforced",
    # GRD-002 citation grounding
    "no-citation-required",
    "citation-source-uncovered",
    "citations-outside-retrieval",
    "citations-grounded",
    # GRD-003 refusal behavior
    "no-refusal-required",
    "refusal-source-uncovered",
    "unsupported-query-answered",
    "all-unsupported-refused",
    # GRD-004 freshness / coverage
    "no-knowledge-sources",
    "sources-uncovered",
    "freshness-unverifiable",
    "retrieval-quality-baseline-missing",
    "stale-evidence",
    "fresh-and-covered",
})

# Allowlisted finding `detail` fields. Only IDs (lists / single strings),
# counts, status enums (`by_source`), a `coverage_complete` boolean, and the
# `reason` enum may appear — never a free-form note or any content. Mirrored in
# the schema for parity.
_DETAIL_LIST_KEYS = (
    "uncovered_sources",
    "stale_sources",
    "unverifiable_freshness_sources",
    "missing_from_retrieval",
    "unsupported_queries_answered",
    "leaked_document_ids",
    "missing_principals",
)
_DETAIL_STRING_KEYS = ("worst_source",)
# `coverage_complete` is a schema-safe boolean that preserves whether the ACL
# evidence backing a finding is COMPLETE. It exists so a proven leak
# (`must-fix`) that coexists with a genuine coverage gap (an ambiguous run, too
# few principals, an unprobed declared principal) still forces the manifest
# `status` to `partial` without erasing the must-fix.
_DETAIL_BOOL_KEYS = ("coverage_complete",)
_DETAIL_EXTRA_KEYS = frozenset(
    _DETAIL_LIST_KEYS + _DETAIL_STRING_KEYS + _DETAIL_BOOL_KEYS + ("by_source",)
)
_DETAIL_KEYS = frozenset({"reason"}) | _DETAIL_EXTRA_KEYS

# Statuses that let a finding downgrade the manifest to `partial`: an
# *executed* must-fix/should-fix is still complete evidence, so partial is
# reserved for genuinely missing/unverifiable evidence.
_ACL_STATUS_ORDER = {"must-fix": 0, "should-fix": 1, "not-verified": 2, "pass": 3}

# Forbidden key names scanned for RECURSIVELY, everywhere in the final
# manifest, right before it is written. Covers both credential-shaped keys
# (mirrors threadlight-connect) and the grounding-specific "never persist"
# list: retrieved content, prompts, completions, and customer payloads.
#
# `_FORBIDDEN_KEY_WORDS` are matched as WHOLE snake/kebab-case segments (not
# substrings) so a legitimate metric field like `tokens` (a token COUNT) is
# never confused with a credential-shaped `access_token`;
# `_FORBIDDEN_KEY_SUBSTRINGS` are compound markers distinctive enough that a
# plain substring match is safe.
_FORBIDDEN_KEY_WORDS = frozenset({
    "token", "secret", "password", "credential", "credentials",
    "authorization", "content", "prompt", "completion", "completions", "payload",
})
_FORBIDDEN_KEY_SUBSTRINGS = ("api_key", "apikey", "access_key", "connection_string")

# Explicit credential VALUE patterns. Even though `detail` and payload fields
# are strict allowlists, an ID field is an inherently free string, so a hostile
# operator could try to smuggle a recognizable credential through e.g. a
# `document_id` value. These signatures are deliberately specific: opaque
# hashes, UUID-like IDs, and base64/base64url document keys are valid evidence
# identifiers and must not be rejected merely for being long or high-entropy.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),               # AWS access key id
    re.compile(r"\bASIA[0-9A-Z]{12,}\b"),               # AWS temporary key id
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),    # Slack token
    re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b"),      # GitHub token
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),             # OpenAI-style key
    re.compile(r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]+"),  # JWT
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),             # credentials embedded in a URL
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),  # Authorization value
    re.compile(
        r"(?i)(?:^|;)\s*(?:accountkey|sharedaccesskey|"
        r"sharedaccesssignature)\s*=\s*[^;\s]+"
    ),                                                   # Azure connection string
    re.compile(
        r"(?i)\b(?:pass(?:word)?|secret|api[_-]?key|access[_-]?key|"
        r"client[_-]?secret|bearer)\b\s*[:=]\s*\S+"
    ),
)

# Azure Shared Access Signature (SAS) detection — structural, never entropy.
# An Azure SAS is an http(s) URL (or a bare query string) whose query carries
# the `sig` signature, `sv` version, and another signed-token marker. Detecting
# that co-occurrence (rather than relying on generic entropy)
# means an ordinary URL or opaque document id is never mistaken for a
# credential, while a real SAS token smuggled through an ID/baseline field is.
_SAS_ADDITIONAL_MARKER_KEYS = frozenset({"se", "sp", "sr", "st", "spr"})

# Strict RFC 3339 `date-time` with a mandatory timezone — mirrors the shared
# envelope's timestamp contract so a `captured_at` that would be REJECTED by
# the manifest schema (space separator, naive, out-of-range clock) is treated
# as un-parseable here rather than silently accepted and then rejected by the
# envelope when it becomes `source_oldest_at`.
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)

# A retrieval-quality baseline reference is a repo-relative path or id ONLY —
# never content, never a URL, never an absolute path or a `..` traversal, and
# no whitespace. Mirrored verbatim as the schema `pattern` so the runtime and
# schema validators accept/reject identical strings.
_BASELINE_REF_PATTERN = (
    r"^(?!.*\.\.)(?=.*[A-Za-z0-9])[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
_BASELINE_REF_RE = re.compile(_BASELINE_REF_PATTERN)

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


class GroundEvidenceError(ValueError):
    """Raised when SPEC config, evidence, or a built manifest is the wrong
    shape (a malformed run, a credential/content-shaped key, a secret-shaped
    value, or an unsafe baseline reference). Always raised BEFORE any file
    write and before any manifest is returned to a caller — a run that fails
    here never disturbs whatever valid `ground-manifest.json` already existed
    and never emits invalid data on `--json`.
    """


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in _FORBIDDEN_KEY_SUBSTRINGS):
        return True
    words = re.split(r"[^a-z0-9]+", lowered)
    return any(word in _FORBIDDEN_KEY_WORDS for word in words)


def _looks_like_sas(value: str) -> bool:
    """True when *value* is (or embeds) an Azure Shared Access Signature.

    Detected structurally, never by entropy: an ``http(s)`` URL, leading-``?``
    query, or unprefixed query must carry ``sig`` + ``sv`` + at least one of
    ``se``/``sp``/``sr``/``st``/``spr``. Requiring all three roles keeps
    ordinary URLs, opaque ids, and repo-relative paths from being flagged.
    """
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        try:
            query = urlsplit(text).query
        except ValueError:
            return False
    elif text.startswith("?"):
        query = text[1:]
    else:
        query = text
    if not query:
        return False
    try:
        parsed = parse_qs(query, keep_blank_values=True)
    except ValueError:
        return False
    keys = {key.lower() for key in parsed}
    return (
        "sig" in keys
        and "sv" in keys
        and bool(keys & _SAS_ADDITIONAL_MARKER_KEYS)
    )


def _looks_like_secret(value: str) -> bool:
    """True when *value* carries a specific credential/secret signature.

    Structural allowlists and forbidden key names protect the manifest shape;
    this scan intentionally does not guess from entropy because opaque evidence
    IDs and baseline refs commonly use the same alphabets as credentials. It
    does, however, reject a value that is structurally an Azure SAS (``sig`` +
    ``sv`` + another SAS marker) — an ordinary URL/opaque id is untouched.
    """
    text = value.strip()
    if not text:
        return False
    if _looks_like_sas(text):
        return True
    return any(pattern.search(text) for pattern in _SECRET_VALUE_PATTERNS)


def _finding(finding_id: str, status: str, reason: str, **extras: Any) -> dict:
    """Build a finding with an allowlisted, structured `detail`. `reason` is a
    controlled enum; `extras` may only be the allowlisted detail fields
    (IDs/counts/status maps). A programming error that passes an unknown key
    or reason fails loud here rather than silently persisting free-form data.
    """
    if reason not in _FINDING_REASON_ENUM:
        raise GroundEvidenceError(f"unknown finding reason {reason!r}")
    unknown = set(extras) - _DETAIL_EXTRA_KEYS
    if unknown:
        raise GroundEvidenceError(
            "finding detail may not contain free-form key(s): "
            + ", ".join(sorted(unknown))
        )
    detail: dict[str, Any] = {"reason": reason}
    detail.update(extras)
    return {"id": finding_id, "status": status, "detail": detail}


def _parse_rfc3339(value: Any):
    """Strict RFC3339 parse. Returns None (never raises) for anything that is
    not a well-formed RFC3339 `date-time` with a mandatory timezone —
    malformed/missing `captured_at` values are simply excluded from freshness
    computations (and can therefore never make a source pass freshness).
    """
    if not isinstance(value, str) or not _RFC3339_RE.match(value):
        return None
    normalized = value[:-1] + "+00:00" if value[-1] in "Zz" else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def oldest_timestamp(runs: list) -> str | None:
    """Oldest valid `captured_at` across a flat list of evidence runs, as the
    ORIGINAL string (never reformatted). Returns None when no run carries a
    parseable RFC3339 `captured_at` — never back-filled from `generated_at`.
    Used for the envelope's global `source_oldest_at`.
    """
    parsed = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        captured_at = run.get("captured_at")
        moment = _parse_rfc3339(captured_at)
        if moment is not None:
            parsed.append((moment, captured_at))
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[0])
    return parsed[0][1]


def _as_numeric(value: Any, label: str):
    """Strict numeric coercion for telemetry fields: absent -> 0 (a run that
    simply didn't report the metric); present-but-non-numeric (a string, a
    bool, NaN/inf) or negative -> a clear GroundEvidenceError. Never silently
    coerced.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        raise GroundEvidenceError(f"{label} must be a number, got a boolean")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise GroundEvidenceError(f"{label} must be a finite number")
        if value < 0:
            raise GroundEvidenceError(f"{label} must be non-negative")
        return value
    raise GroundEvidenceError(
        f"{label} must be a number, got {type(value).__name__}"
    )


def aggregate_telemetry(runs: list) -> dict:
    """Sum only finite, non-negative numeric `subqueries`/`tokens` across
    *runs* and record `retrieval_count` (the number of runs). A boolean,
    string, negative, or non-finite value raises a `GroundEvidenceError`
    rather than being coerced/ignored.
    """
    subqueries_total: float = 0
    tokens_total: float = 0
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise GroundEvidenceError(f"telemetry run[{index}] must be an object")
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
# Strict evidence-shape helpers — raise GroundEvidenceError on any malformed
# field. Absence of an OPTIONAL classification signal (e.g. expected_entitled)
# is handled by the assessors as `not-verified`; a present-but-wrong-shape
# value always raises here, before any output.
# ---------------------------------------------------------------------------
def _require_evidence_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GroundEvidenceError(f"{label} must be a non-empty string")
    return value


def _require_evidence_id_list(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise GroundEvidenceError(f"{label} must be a list of non-empty strings")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise GroundEvidenceError(f"{label}[{index}] must be a non-empty string")
    return value


def _resolve_expected_entitled(run: dict, label: str):
    """Return the run's EXPLICIT expected-entitlement boolean, or None when no
    explicit signal is present. A present-but-non-boolean signal (e.g. ``1``,
    ``"true"``) raises — entitlement is never inferred from a naive name
    heuristic, so ambiguous expectations stay `not-verified`.
    """
    for key in ("expected_entitled", "entitled"):
        if key in run:
            value = run[key]
            if not isinstance(value, bool):
                raise GroundEvidenceError(f"{label}.{key} must be a boolean")
            return value
    return None


def _normalize_acl_run(run: Any, index: int, declared_ids: set) -> dict:
    label = f"acl_runs[{index}]"
    if not isinstance(run, dict):
        raise GroundEvidenceError(f"{label} must be an object")
    source_id = _require_evidence_string(run.get("source_id"), f"{label}.source_id")
    if source_id not in declared_ids:
        raise GroundEvidenceError(
            f"{label}.source_id {source_id!r} is not a declared knowledge source"
        )
    principal = _require_evidence_string(run.get("principal"), f"{label}.principal")
    document_ids = _require_evidence_id_list(
        run.get("document_ids"), f"{label}.document_ids"
    )
    raw_allowed = run.get("allowed_document_ids")
    allowed_ids = (
        None
        if raw_allowed is None
        else frozenset(
            _require_evidence_id_list(raw_allowed, f"{label}.allowed_document_ids")
        )
    )
    return {
        "source_id": source_id,
        "principal": principal,
        "document_ids": frozenset(document_ids),
        "allowed_ids": allowed_ids,
        "expected": _resolve_expected_entitled(run, label),
    }


def _normalize_citation_run(run: Any, index: int, declared_ids: set) -> dict:
    label = f"citation_runs[{index}]"
    if not isinstance(run, dict):
        raise GroundEvidenceError(f"{label} must be an object")
    source_id = _require_evidence_string(run.get("source_id"), f"{label}.source_id")
    if source_id not in declared_ids:
        raise GroundEvidenceError(
            f"{label}.source_id {source_id!r} is not a declared knowledge source"
        )
    citations = _require_evidence_id_list(run.get("citations"), f"{label}.citations")
    retrieved_ids = _require_evidence_id_list(
        run.get("retrieved_ids"), f"{label}.retrieved_ids"
    )
    return {
        "source_id": source_id,
        "citations": citations,
        "retrieved_ids": retrieved_ids,
    }


def _normalize_refusal_run(run: Any, index: int, declared_ids: set) -> dict:
    label = f"refusal_runs[{index}]"
    if not isinstance(run, dict):
        raise GroundEvidenceError(f"{label} must be an object")
    source_id = _require_evidence_string(run.get("source_id"), f"{label}.source_id")
    if source_id not in declared_ids:
        raise GroundEvidenceError(
            f"{label}.source_id {source_id!r} is not a declared knowledge source"
        )
    query_id = _require_evidence_string(run.get("query_id"), f"{label}.query_id")
    refused = run.get("refused")
    if not isinstance(refused, bool):
        raise GroundEvidenceError(f"{label}.refused must be a boolean")
    return {"source_id": source_id, "query_id": query_id, "refused": refused}


# ---------------------------------------------------------------------------
# SPEC knowledge-source sanitization — whitelist-only, never verbatim
# ---------------------------------------------------------------------------
def _sanitize_source(source: Any, index: int) -> dict:
    label = f"sources[{index}]"
    if not isinstance(source, dict):
        raise GroundEvidenceError(f"{label} must be an object")

    # Fail loud on any forbidden-shaped key BEFORE whitelisting — an operator
    # passing e.g. an `access_token` alongside a source config gets a clear
    # error, rather than having it silently (if safely) dropped.
    forbidden = [key for key in source if isinstance(key, str) and _is_forbidden_key(key)]
    if forbidden:
        raise GroundEvidenceError(
            f"{label} must not contain credential/content/prompt-shaped key(s): "
            + ", ".join(sorted(forbidden))
        )

    sanitized: dict[str, Any] = {}
    for key in _SOURCE_STRING_KEYS:
        value = source.get(key)
        if not isinstance(value, str) or not value:
            raise GroundEvidenceError(f"{label}.{key} must be a non-empty string")
        sanitized[key] = value

    for key in _SOURCE_BOOL_KEYS:
        value = source.get(key)
        if not isinstance(value, bool):
            raise GroundEvidenceError(f"{label}.{key} must be a boolean")
        sanitized[key] = value

    principals = source.get("acl_probe_principals")
    if principals is not None:
        if not isinstance(principals, list) or not all(
            isinstance(principal, str) and principal for principal in principals
        ):
            raise GroundEvidenceError(
                f"{label}.acl_probe_principals must be a list of non-empty strings"
            )
        sanitized["acl_probe_principals"] = list(principals)

    return sanitized


def _source_requires_acl(source: dict) -> bool:
    return source.get("permission_model") in _ACL_PERMISSION_MODELS


# ---------------------------------------------------------------------------
# GRD-001 — ACL enforcement (source-scoped, allowlist-aware)
# ---------------------------------------------------------------------------
def _acl_coverage_gap(source: dict, runs: list):
    """Return ``(reason, extras)`` for the FIRST coverage gap that makes a
    source's ACL evidence incomplete, or ``None`` when coverage is complete.

    Coverage is complete only when every run carries an explicit
    `expected_entitled`, at least two distinct principals were probed, both an
    entitled and an unentitled probe are present, and every declared
    `acl_probe_principals` appears. This is evaluated INDEPENDENTLY of whether a
    proven leak exists, so a leak and a coverage gap can be reported together.
    """
    if any(run["expected"] is None for run in runs):
        return ("ambiguous-entitlement", {})

    principals = {run["principal"] for run in runs}
    if len(principals) < 2:
        return ("insufficient-principals", {})

    entitled = [run for run in runs if run["expected"] is True]
    unentitled = [run for run in runs if run["expected"] is False]
    if not entitled or not unentitled:
        return ("missing-entitled-or-unentitled-probe", {})

    declared_principals = source.get("acl_probe_principals")
    if declared_principals:
        missing = sorted(set(declared_principals) - principals)
        if missing:
            return ("declared-principal-uncovered", {"missing_principals": missing})

    return None


def _assess_acl_group(source: dict, runs: list) -> dict:
    """Assess one source's ACL runs. Returns a small dict
    ``{"status", "reason", "extras", "coverage_complete"}``.

    **Negative evidence takes precedence.** A proven leak — an EXPLICIT
    unentitled principal (``expected_entitled == false``) receiving ANY document
    outside its explicit `allowed_document_ids` (a subset is enough; no
    allowlist means nothing is allowed) — is `must-fix` even when OTHER runs are
    ambiguous or a declared principal is unprobed. The coexisting coverage gap
    is not discarded: it is preserved in `coverage_complete` (and, when
    available, `missing_principals`) so the envelope can still be `partial`.
    With no leak, an incomplete coverage gap is `not-verified` (never a guessed
    pass), and complete-and-clean evidence is `pass`.
    """
    # Proven leak, computed from EXPLICIT unentitled runs only — an ambiguous
    # run (`expected is None`) is not proof of anything and never contributes a
    # leak, but it also cannot suppress a leak proven elsewhere in the group.
    leaked: set = set()
    for run in runs:
        if run["expected"] is False:
            allowed = run["allowed_ids"] if run["allowed_ids"] is not None else frozenset()
            leaked |= run["document_ids"] - allowed

    coverage_gap = _acl_coverage_gap(source, runs)

    if leaked:
        extras: dict[str, Any] = {"leaked_document_ids": sorted(leaked)}
        # Preserve the coexisting coverage gap so a leak + ambiguity/missing
        # principal still forces `partial` without erasing the must-fix.
        if coverage_gap is not None:
            _, gap_extras = coverage_gap
            extras.update(gap_extras)
        return {
            "status": "must-fix",
            "reason": "unauthorized-documents",
            "extras": extras,
            "coverage_complete": coverage_gap is None,
        }

    if coverage_gap is not None:
        reason, gap_extras = coverage_gap
        return {
            "status": "not-verified",
            "reason": reason,
            "extras": gap_extras,
            "coverage_complete": False,
        }

    return {
        "status": "pass",
        "reason": "acl-enforced",
        "extras": {},
        "coverage_complete": True,
    }


def assess_acl(sources: list, acl_runs: list) -> dict:
    """GRD-001 — ACL enforcement, scoped to every `permission_model == "acl"`
    source. Each such source must be covered by ACL runs carrying its
    `source_id`; an uncovered source is `not-verified`. Within a source, a
    proven allowlist leak is `must-fix`. When several ACL sources are probed the
    worst per-source result wins — a must-fix in one source is never erased by
    another source's not-verified — and every per-source status is recorded in
    `by_source`. Any coverage gap (an uncovered source, an ambiguous/insufficient
    group, an unprobed declared principal) is surfaced as `coverage_complete:
    false` so the manifest stays `partial` even when the finding is `must-fix`.
    """
    finding_id = "GRD-001"
    declared_ids = {source["id"] for source in sources}
    normalized = [
        _normalize_acl_run(run, index, declared_ids)
        for index, run in enumerate(acl_runs)
    ]
    acl_sources = [source for source in sources if _source_requires_acl(source)]
    if not acl_sources:
        return _finding(finding_id, "pass", "no-acl-protected-sources")

    groups: dict[str, list] = {}
    for run in normalized:
        groups.setdefault(run["source_id"], []).append(run)

    per_source: dict[str, dict] = {}
    uncovered: list[str] = []
    for source in acl_sources:
        source_id = source["id"]
        runs = groups.get(source_id, [])
        if not runs:
            uncovered.append(source_id)
            per_source[source_id] = {
                "status": "not-verified",
                "reason": "acl-source-uncovered",
                "extras": {},
                "coverage_complete": False,
            }
            continue
        per_source[source_id] = _assess_acl_group(source, runs)

    # A `must-fix` finding is normally COMPLETE evidence, so it would not force
    # `partial` on its own. When it coexists with any coverage gap we record
    # `coverage_complete: false` so the envelope is still `partial`.
    coverage_incomplete = any(
        not result["coverage_complete"] for result in per_source.values()
    )

    if len(per_source) == 1:
        (source_id, result), = per_source.items()
        extras = dict(result["extras"])
        if source_id in set(uncovered):
            extras["uncovered_sources"] = [source_id]
        if result["status"] == "must-fix" and coverage_incomplete:
            extras["coverage_complete"] = False
        return _finding(finding_id, result["status"], result["reason"], **extras)

    worst_id = min(per_source, key=lambda key: _ACL_STATUS_ORDER[per_source[key]["status"]])
    worst = per_source[worst_id]
    extras = dict(worst["extras"])
    extras["worst_source"] = worst_id
    extras["by_source"] = {
        source_id: result["status"] for source_id, result in per_source.items()
    }
    if uncovered:
        extras["uncovered_sources"] = sorted(uncovered)
    if worst["status"] == "must-fix" and coverage_incomplete:
        extras["coverage_complete"] = False
    return _finding(finding_id, worst["status"], worst["reason"], **extras)


# ---------------------------------------------------------------------------
# GRD-002 — citation grounding (source-scoped)
# ---------------------------------------------------------------------------
def validate_citations(citations: list, retrieved_ids: list) -> dict:
    """A citation is valid only when it is a member of the retrieved set.
    Returns `must-fix` with the sorted, de-duplicated list of citations that
    fall outside the retrieved set, or `pass` when every citation is covered.
    """
    retrieved = set(retrieved_ids or [])
    missing = sorted({citation for citation in (citations or []) if citation not in retrieved})
    if missing:
        return {"status": "must-fix", "missing_from_retrieval": missing}
    return {"status": "pass", "missing_from_retrieval": []}


def assess_citations(sources: list, citation_runs: list) -> dict:
    """GRD-002 — citation grounding, scoped to every `citation_required`
    source. Each such source must be covered by citation runs carrying its
    `source_id`; an uncovered required source is `not-verified`. Any run whose
    citations fall outside its retrieved set is `must-fix` (surfacing the
    proven failure), and any coverage gap is still recorded so the manifest
    stays `partial`.
    """
    finding_id = "GRD-002"
    declared_ids = {source["id"] for source in sources}
    normalized = [
        _normalize_citation_run(run, index, declared_ids)
        for index, run in enumerate(citation_runs)
    ]

    cite_sources = [source for source in sources if source["citation_required"]]
    cite_source_ids = {source["id"] for source in cite_sources}
    applicable = [run for run in normalized if run["source_id"] in cite_source_ids]
    covered = {run["source_id"] for run in applicable}
    uncovered = sorted(
        source["id"] for source in cite_sources if source["id"] not in covered
    )

    missing_all: list[str] = []
    for run in applicable:
        result = validate_citations(run["citations"], run["retrieved_ids"])
        missing_all.extend(result["missing_from_retrieval"])

    extras: dict[str, Any] = {}
    if uncovered:
        extras["uncovered_sources"] = uncovered
    if missing_all:
        extras["missing_from_retrieval"] = sorted(set(missing_all))
        return _finding(finding_id, "must-fix", "citations-outside-retrieval", **extras)
    if not cite_sources:
        return _finding(finding_id, "pass", "no-citation-required")
    if uncovered:
        return _finding(finding_id, "not-verified", "citation-source-uncovered", **extras)
    return _finding(finding_id, "pass", "citations-grounded")


# ---------------------------------------------------------------------------
# GRD-003 — refusal behavior (source-scoped)
# ---------------------------------------------------------------------------
def assess_refusal(sources: list, refusal_runs: list) -> dict:
    """GRD-003 — refusal behavior, scoped to every `refuse_when_unsupported`
    source. Each such source must be covered by refusal runs carrying its
    `source_id`; an uncovered required source is `not-verified`. An EXECUTED
    probe where an unsupported query was answered instead of refused is
    `must-fix`.
    """
    finding_id = "GRD-003"
    declared_ids = {source["id"] for source in sources}
    normalized = [
        _normalize_refusal_run(run, index, declared_ids)
        for index, run in enumerate(refusal_runs)
    ]

    refuse_sources = [source for source in sources if source["refuse_when_unsupported"]]
    refuse_source_ids = {source["id"] for source in refuse_sources}
    applicable = [run for run in normalized if run["source_id"] in refuse_source_ids]
    covered = {run["source_id"] for run in applicable}
    uncovered = sorted(
        source["id"] for source in refuse_sources if source["id"] not in covered
    )
    answered = sorted({run["query_id"] for run in applicable if not run["refused"]})

    extras: dict[str, Any] = {}
    if uncovered:
        extras["uncovered_sources"] = uncovered
    if answered:
        extras["unsupported_queries_answered"] = answered
        return _finding(finding_id, "must-fix", "unsupported-query-answered", **extras)
    if not refuse_sources:
        return _finding(finding_id, "pass", "no-refusal-required")
    if uncovered:
        return _finding(finding_id, "not-verified", "refusal-source-uncovered", **extras)
    return _finding(finding_id, "pass", "all-unsupported-refused")


# ---------------------------------------------------------------------------
# GRD-004 — source freshness / coverage (per-source, baseline-gated)
# ---------------------------------------------------------------------------
def assess_freshness_coverage(
    sources: list,
    all_runs: list,
    *,
    generated_at: str,
    retrieval_quality_baseline: str | None = None,
) -> dict:
    """GRD-004 — every declared source must have covering evidence carrying
    its `source_id`, that evidence must carry a fresh, valid RFC3339
    `captured_at`, and the assessment must reference a retrieval-quality
    baseline.

    Freshness is computed INDEPENDENTLY per source from only that source's own
    runs, so one source's stale run can never stale another. A covered source
    whose runs carry no valid `captured_at` cannot pass freshness — its
    timestamp is unverifiable, which is `not-verified` (documented choice: a
    missing/malformed timestamp is an evidence gap, never a silent pass). A
    missing baseline is likewise `not-verified`, never guessed. Stale-but-
    covered evidence caps at `should-fix`.
    """
    finding_id = "GRD-004"
    if not sources:
        return _finding(finding_id, "pass", "no-knowledge-sources")

    runs_by_source: dict[str, list] = {}
    for run in all_runs:
        if not isinstance(run, dict):
            continue
        source_id = run.get("source_id")
        if isinstance(source_id, str) and source_id:
            runs_by_source.setdefault(source_id, []).append(run)

    now = _parse_rfc3339(generated_at)
    uncovered: list[str] = []
    unverifiable: list[str] = []
    stale: list[str] = []
    for source in sources:
        source_id = source["id"]
        runs = runs_by_source.get(source_id, [])
        if not runs:
            uncovered.append(source_id)
            continue
        oldest = oldest_timestamp(runs)
        if oldest is None:
            unverifiable.append(source_id)
            continue
        limit = _CADENCE_GRACE_HOURS.get(source["refresh_cadence"])
        oldest_moment = _parse_rfc3339(oldest)
        if limit is not None and now is not None and oldest_moment is not None:
            age_hours = (now - oldest_moment).total_seconds() / 3600.0
            if age_hours > limit:
                stale.append(source_id)

    baseline_missing = retrieval_quality_baseline is None

    if baseline_missing or uncovered or unverifiable:
        extras: dict[str, Any] = {}
        if uncovered:
            extras["uncovered_sources"] = sorted(uncovered)
        if unverifiable:
            extras["unverifiable_freshness_sources"] = sorted(unverifiable)
        if baseline_missing:
            reason = "retrieval-quality-baseline-missing"
        elif uncovered:
            reason = "sources-uncovered"
        else:
            reason = "freshness-unverifiable"
        return _finding(finding_id, "not-verified", reason, **extras)

    if stale:
        return _finding(
            finding_id, "should-fix", "stale-evidence", stale_sources=sorted(stale)
        )

    return _finding(finding_id, "pass", "fresh-and-covered")


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
    for run in refusal_runs:
        if not isinstance(run, dict):
            continue
        query_id = run.get("query_id")
        summary.append(
            {
                "source_id": run.get("source_id"),
                "query_id": query_id if isinstance(query_id, str) and query_id else None,
                "refused": run.get("refused") if isinstance(run.get("refused"), bool) else None,
            }
        )
    return summary


# ---------------------------------------------------------------------------
# Retrieval-quality baseline reference — a repo-relative path/id, no content
# ---------------------------------------------------------------------------
def _validate_baseline_input(value: Any) -> str | None:
    """Validate the caller-supplied `retrieval_quality_baseline`. None (a
    genuinely absent baseline) is allowed and surfaces as GRD-004
    `not-verified`. A present value must be a safe repo-relative path/id — no
    absolute path, no `..` traversal, no URL, no whitespace, no content.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not _BASELINE_REF_RE.match(value):
        raise GroundEvidenceError(
            "retrieval_quality_baseline must be a repo-relative path/id "
            "(no absolute path, no '..' traversal, no URL, no whitespace)"
        )
    if _looks_like_secret(value):
        raise GroundEvidenceError(
            "retrieval_quality_baseline must not contain a secret-shaped value"
        )
    return value


# ---------------------------------------------------------------------------
# Coordinator — assess_grounding()
# ---------------------------------------------------------------------------
def _finding_forces_partial(finding: dict) -> bool:
    """A finding downgrades the manifest to `partial` when required evidence
    is genuinely missing/unverifiable: a `not-verified` status, a `must-fix`
    proven against INCOMPLETE ACL coverage (`coverage_complete: false`), an
    uncovered or freshness-unverifiable source recorded in `detail`, or a
    per-source `not-verified` hidden behind an aggregated worst-case status. An
    executed must-fix/should-fix with COMPLETE coverage never forces partial.
    """
    if finding["status"] == "not-verified":
        return True
    detail = finding.get("detail")
    if isinstance(detail, dict):
        if detail.get("coverage_complete") is False:
            return True
        for key in ("uncovered_sources", "unverifiable_freshness_sources", "missing_principals"):
            if detail.get(key):
                return True
        by_source = detail.get("by_source")
        if isinstance(by_source, dict) and "not-verified" in by_source.values():
            return True
    return False


def assess_grounding(
    *,
    sources: list,
    acl_runs: list,
    citation_runs: list,
    refusal_runs: list,
    generated_at: str,
    retrieval_quality_baseline: Any = None,
) -> dict:
    """Build the `threadlight.ground/v1` manifest from already-produced
    retrieval/evaluation evidence. Never ingests Foundry IQ itself, never
    calls an evaluator — every run here is a caller-supplied result, and
    `sources` is the authoritative SPEC-derived inventory.

    The built manifest is fully schema-validated (and scanned for forbidden
    keys / secret values) BEFORE it is returned, so `--json` can never emit
    invalid or oversharing data. `status` is `partial` exactly when a required
    piece of evidence is missing/`not-verified`; an EXECUTED
    `must-fix`/`should-fix` finding with complete coverage never downgrades
    `status` on its own.
    """
    for name, value in (
        ("sources", sources),
        ("acl_runs", acl_runs),
        ("citation_runs", citation_runs),
        ("refusal_runs", refusal_runs),
    ):
        if not isinstance(value, list):
            raise GroundEvidenceError(f"{name} must be a list")

    baseline = _validate_baseline_input(retrieval_quality_baseline)

    sanitized_sources = [
        _sanitize_source(source, index) for index, source in enumerate(sources)
    ]

    # Reject duplicate authoritative source ids BEFORE grouping or assessment:
    # grouping keys on `source_id` and the declared-id sets would silently
    # collapse a conflicting duplicate (e.g. two entries claiming the same id
    # with different `permission_model`s), so an ACL-protected source could be
    # masked by a public twin. Fail closed before any output is produced.
    seen_ids: set[str] = set()
    for source in sanitized_sources:
        source_id = source["id"]
        if source_id in seen_ids:
            raise GroundEvidenceError(f"duplicate knowledge source id {source_id!r}")
        seen_ids.add(source_id)

    acl_finding = assess_acl(sanitized_sources, acl_runs)
    citation_finding = assess_citations(sanitized_sources, citation_runs)
    refusal_finding = assess_refusal(sanitized_sources, refusal_runs)

    all_runs = [*acl_runs, *citation_runs, *refusal_runs]
    freshness_finding = assess_freshness_coverage(
        sanitized_sources,
        all_runs,
        generated_at=generated_at,
        retrieval_quality_baseline=baseline,
    )

    findings = [acl_finding, citation_finding, refusal_finding, freshness_finding]
    status = (
        "partial"
        if any(_finding_forces_partial(finding) for finding in findings)
        else "complete"
    )

    payload = {
        "sources": sanitized_sources,
        "acl_evidence": _summarize_acl_runs(acl_runs),
        "citation_evidence": _summarize_citation_runs(citation_runs),
        "refusal_evidence": _summarize_refusal_runs(refusal_runs),
        "telemetry": aggregate_telemetry(all_runs),
        "retrieval_quality_baseline": baseline,
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
    # Validate the whole manifest (schema shape + forbidden keys + secret
    # values) before returning, so no invalid/oversharing manifest ever
    # reaches `--json`, a caller, or disk.
    validate_ground_manifest(manifest)
    return manifest


# ---------------------------------------------------------------------------
# Recursive forbidden-key / secret-value defense
# ---------------------------------------------------------------------------
def _assert_no_unsafe_content(obj: Any) -> None:
    """Recursively reject any credential/content-shaped KEY and any
    secret-shaped VALUE anywhere in the manifest. `detail` is already an
    allowlisted schema, but ID fields are inherently free strings, so this is
    the defense-in-depth that makes a smuggled secret unpersistable.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                if _is_forbidden_key(key):
                    raise GroundEvidenceError(
                        "ground manifest must not contain "
                        "credential/content/prompt-shaped keys"
                    )
                if _looks_like_secret(key):
                    raise GroundEvidenceError(
                        "ground manifest must not contain secret-shaped values"
                    )
            _assert_no_unsafe_content(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_unsafe_content(item)
    elif isinstance(obj, str):
        if _looks_like_secret(obj):
            raise GroundEvidenceError(
                "ground manifest must not contain secret-shaped values"
            )


# ---------------------------------------------------------------------------
# Schema validation — hand-rolled mirror of ground-manifest.schema.json
# ---------------------------------------------------------------------------
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


def _require_string_array(value, label: str, *, min_length: int = 0) -> None:
    for index, item in enumerate(_require_array(value, label)):
        _require_string(item, f"{label}[{index}]", min_length=min_length)


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
    "retrieval_quality_baseline",
}


def _validate_detail(detail: Any, label: str) -> None:
    """Validate a finding's `detail` against the allowlisted schema: a
    required `reason` enum plus only ID lists, a `worst_source` id, a
    `coverage_complete` boolean, and a `by_source` status map. No free-form key
    or value is representable.
    """
    detail = _require_object(detail, label)
    _require_keys(detail, {"reason"}, label)
    _reject_unknown_keys(detail, _DETAIL_KEYS, label)
    if detail["reason"] not in _FINDING_REASON_ENUM:
        raise ManifestValidationError(f"{label}.reason must be a known reason code")
    for key in _DETAIL_LIST_KEYS:
        if key in detail:
            _require_string_array(detail[key], f"{label}.{key}", min_length=1)
    for key in _DETAIL_STRING_KEYS:
        if key in detail:
            _require_string(detail[key], f"{label}.{key}", min_length=1)
    for key in _DETAIL_BOOL_KEYS:
        if key in detail:
            _require_boolean(detail[key], f"{label}.{key}")
    if "by_source" in detail:
        by_source = _require_object(detail["by_source"], f"{label}.by_source")
        for source_id, source_status in by_source.items():
            _require_string(source_id, f"{label}.by_source key", min_length=1)
            if source_status not in FINDING_STATUS_ENUM:
                raise ManifestValidationError(
                    f"{label}.by_source[{source_id!r}] must be a finding status"
                )


def validate_ground_manifest(manifest: dict) -> None:
    """Hand-rolled schema check mirroring
    `references/ground-manifest.schema.json`, layered on the shared
    envelope's own validation. stdlib-only — no `jsonschema` runtime
    dependency; a test-only jsonschema parity suite pins this to the schema.
    The recursive forbidden-key / secret-value scan runs FIRST so unsafe
    content fails with a clear `GroundEvidenceError` before shape checks.
    """
    validate_envelope(manifest)
    manifest = _require_object(manifest, "ground manifest")
    _assert_no_unsafe_content(manifest)
    _require_keys(
        manifest,
        {
            "sources", "acl_evidence", "citation_evidence", "refusal_evidence",
            "telemetry", "retrieval_quality_baseline",
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
        if "detail" in finding:
            _validate_detail(finding["detail"], f"{label}.detail")

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
                source["acl_probe_principals"],
                f"{label}.acl_probe_principals",
                min_length=1,
            )

    for index, item in enumerate(_require_array(manifest["acl_evidence"], "acl_evidence")):
        label = f"acl_evidence[{index}]"
        item = _require_object(item, label)
        _require_keys(item, _ACL_EVIDENCE_KEYS, label)
        _reject_unknown_keys(item, _ACL_EVIDENCE_KEYS, label)
        _require_nullable_string(item["principal"], f"{label}.principal")
        _require_string_array(item["document_ids"], f"{label}.document_ids", min_length=1)
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
            item["missing_from_retrieval"], f"{label}.missing_from_retrieval", min_length=1
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

    baseline = manifest["retrieval_quality_baseline"]
    if baseline is not None:
        _require_string(baseline, "retrieval_quality_baseline", min_length=1)
        if not _BASELINE_REF_RE.match(baseline):
            raise ManifestValidationError(
                "retrieval_quality_baseline must be a repo-relative path/id "
                "(no absolute path, no '..' traversal, no URL, no whitespace)"
            )


def write_ground_manifest(path, manifest: dict) -> None:
    """Schema-validate + forbidden-key/secret scan, THEN atomically write. A
    prior valid manifest at *path* is untouched unless every check above
    passes — `atomic_write_json` also never leaves a partial file behind on
    failure.
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
    manual live handoff) and build the manifest. The knowledge-source
    inventory comes from the SPEC-derived `evidence["sources"]` object — NOT
    from *root*, which is only the CLI's output/project boundary. Never
    touches disk itself.
    """
    return assess_grounding(
        sources=evidence.get("sources", []),
        acl_runs=evidence.get("acl_runs", []),
        citation_runs=evidence.get("citation_runs", []),
        refusal_runs=evidence.get("refusal_runs", []),
        retrieval_quality_baseline=evidence.get("retrieval_quality_baseline"),
        generated_at=generated_at or evidence.get("generated_at") or _now_iso(),
    )


def _resolve_within_root(root: str, relative_path: str) -> str:
    """Resolve *relative_path* under *root* and reject anything that escapes
    the project root (an absolute path outside it, or a `..` traversal). Root
    is the output/project boundary — a manifest may only ever be written
    inside it.
    """
    root_path = Path(root).resolve(strict=True)
    candidate = Path(relative_path)
    if not candidate.is_absolute():
        candidate = root_path / candidate

    # Resolve the nearest existing ancestor strictly, then append any missing
    # parent segments. This follows existing parent symlinks before containment
    # is checked without requiring the destination directory to exist yet.
    parent = candidate.parent
    missing_parts: list[str] = []
    while not parent.exists() and not parent.is_symlink():
        missing_parts.append(parent.name)
        parent = parent.parent
    resolved_parent = parent.resolve(strict=True)
    for part in reversed(missing_parts):
        resolved_parent /= part

    try:
        resolved_parent.relative_to(root_path)
    except ValueError:
        raise GroundEvidenceError(
            f"output path {relative_path!r} escapes the project root"
        )
    return str(resolved_parent / candidate.name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "threadlight-ground — assess already-produced ACL/citation/refusal "
            "evidence and emit specs/ground-manifest.json. The knowledge-source "
            "inventory comes from the SPEC-derived evidence object; --project-root "
            "is only the output/project boundary."
        )
    )
    parser.add_argument("--project-root", default=".", help="pilot repo root (default cwd)")
    parser.add_argument(
        "--evidence-file",
        required=True,
        help=(
            "JSON file with keys sources, acl_runs, citation_runs, refusal_runs "
            "(all already-produced probe results), retrieval_quality_baseline, "
            "and optional generated_at"
        ),
    )
    parser.add_argument(
        "--manifest-path",
        default=DEFAULT_MANIFEST_PATH,
        help="where to write the manifest, relative to --project-root (must stay inside it)",
    )
    parser.add_argument("--emit", action="store_true", help="write the manifest to disk")
    parser.add_argument("--json", action="store_true", help="print manifest JSON to stdout")
    parser.add_argument(
        "--gate", action="store_true", help="exit 2 when any finding is must-fix"
    )
    args = parser.parse_args(argv)

    try:
        root_path = Path(args.project_root).resolve(strict=True)
        if not root_path.is_dir():
            raise NotADirectoryError(f"{root_path} is not a directory")
        root = str(root_path)
    except OSError as exc:
        print(f"error: invalid project root {args.project_root}: {exc}")
        return 1
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
    except (GroundEvidenceError, ManifestValidationError) as exc:
        print(f"error: {exc}")
        return 1

    if args.emit:
        try:
            manifest_full_path = _resolve_within_root(root, args.manifest_path)
            write_ground_manifest(manifest_full_path, manifest)
        except (GroundEvidenceError, ManifestValidationError, OSError) as exc:
            print(f"error: could not write manifest: {exc}")
            return 1

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
