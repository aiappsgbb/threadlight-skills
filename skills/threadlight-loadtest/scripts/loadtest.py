#!/usr/bin/env python3
"""loadtest.py — the guarded, budget-capped load-evidence leg for
Threadlight pilots.

A **manual, cost-bearing** skill: it generates real traffic against a real
endpoint via an injected/selected `LoadAdapter` (see `adapters.py`). Because
that traffic costs real money (tokens, compute) and can hit a production
endpoint, every run is guarded by TWO gates evaluated *before* the adapter is
ever touched:

  1. **Budget** — `estimate_projected_token_cost_usd(profile)` is compared to
     the MANDATORY `budget_ceiling_usd`. If the projection exceeds the
     ceiling the run **aborts** with zero adapter calls and a `LOAD-002`
     must-fix finding.
  2. **Production safety** — a `production` `endpoint_class` requires the
     caller to pass `allow_production=True` explicitly. Otherwise the run
     **aborts** with zero adapter calls and a `LOAD-001` must-fix finding.

Only after both gates pass — and only when an adapter was actually selected
(`select_adapter` in `adapters.py`) and the profile declares a configured
endpoint — is `adapter.run(profile)` invoked. A `complete` result (nonempty
samples) can produce an ADVISORY, plan-only SPEC update snippet; this module
NEVER writes to SPEC.md itself. `specs/load-manifest.json` is the only file
this module writes, and only atomically + schema-validated (see
`skills/_shared/manifest.py`); a run that fails validation never disturbs
whatever valid manifest already exists.

No dependency is ever installed by this module — `adapters.detect_available_
commands` only probes `PATH` via `shutil.which`. No network call is ever made
by this module directly — the ONLY thing that talks to a real endpoint is the
adapter the caller supplies, and only after both guards pass.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Shared envelope (skills/_shared/manifest.py) — insert repo root on sys.path
# so `skills._shared.manifest` resolves as an implicit namespace package both
# in-repo and when this script is invoked standalone.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# Also make sibling `adapters.py` importable when this file is run directly
# (python3 scripts/loadtest.py) rather than as part of the `scripts` package.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from skills._shared.manifest import (  # noqa: E402
    ManifestValidationError,
    _validate_iso8601_timestamp,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)

from adapters import (  # noqa: E402
    CommandLoadAdapter,
    LoadAdapter,
    detect_available_commands,
    scrub_text,
    select_adapter,
)

TOOL_VERSION = "0.1.0"
LOAD_MANIFEST_SCHEMA = "threadlight.load/v1"
DEFAULT_VALID_FOR_HOURS = 24
VALID_ENDPOINT_CLASSES = ("non-production", "production")
FINDING_STATUSES = frozenset({"pass", "must-fix", "should-fix", "not-verified"})


class LoadTestValidationError(ValueError):
    """Raised for a malformed profile, budget ceiling, endpoint class, or
    sample list — a caller/programmer usage error. Always raised BEFORE any
    adapter is invoked and before any manifest is built or written."""


class LoadTestPrivacyError(ValueError):
    """Raised when a manifest about to be persisted contains a
    forbidden-shaped key or a secret-shaped string value. Raised before any
    file write — a prior valid manifest is never disturbed."""


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------
REQUIRED_PROFILE_KEYS = frozenset({
    "name", "endpoint", "duration_s", "virtual_users",
    "tokens_per_request_estimate", "price_per_1k_tokens_usd",
})
OPTIONAL_PROFILE_KEYS = frozenset({
    "request_count", "spawn_rate_per_s", "script_path", "slo", "adapter_args",
})
ALL_PROFILE_KEYS = REQUIRED_PROFILE_KEYS | OPTIONAL_PROFILE_KEYS
_ENDPOINT_KEYS = frozenset({"url", "credential_ref"})
_SLO_KEYS = frozenset({"max_p95_latency_ms", "max_error_rate"})


def _require_number(value: Any, label: str, *, minimum: Optional[float] = None,
                     strict_minimum: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LoadTestValidationError(f"{label} must be a number")
    if not math.isfinite(value):
        raise LoadTestValidationError(f"{label} must be a finite number")
    if minimum is not None:
        if strict_minimum and value <= minimum:
            raise LoadTestValidationError(f"{label} must be > {minimum}")
        if not strict_minimum and value < minimum:
            raise LoadTestValidationError(f"{label} must be >= {minimum}")


def _require_positive_int(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LoadTestValidationError(f"{label} must be an integer")
    if value <= 0:
        raise LoadTestValidationError(f"{label} must be > 0")


def _require_nullable_nonempty_str(value: Any, label: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise LoadTestValidationError(f"{label} must be null or a non-empty string")


def validate_budget_ceiling(value: Any) -> None:
    """Mandatory, strictly positive, finite. Booleans and NaN/inf rejected —
    `isinstance(True, int)` is True in Python, so bool is excluded first."""
    _require_number(value, "budget_ceiling_usd", minimum=0, strict_minimum=True)


def validate_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        raise LoadTestValidationError("profile must be an object")

    missing = REQUIRED_PROFILE_KEYS - profile.keys()
    if missing:
        raise LoadTestValidationError(
            "profile missing required key(s): " + ", ".join(sorted(missing))
        )
    unknown = profile.keys() - ALL_PROFILE_KEYS
    if unknown:
        raise LoadTestValidationError(
            "profile has unknown key(s): " + ", ".join(sorted(unknown))
        )

    if not isinstance(profile["name"], str) or not profile["name"]:
        raise LoadTestValidationError("profile.name must be a non-empty string")

    endpoint = profile["endpoint"]
    if not isinstance(endpoint, dict):
        raise LoadTestValidationError("profile.endpoint must be an object")
    unknown_endpoint = set(endpoint) - _ENDPOINT_KEYS
    if unknown_endpoint:
        raise LoadTestValidationError(
            "profile.endpoint has unknown key(s): " + ", ".join(sorted(unknown_endpoint))
        )
    _require_nullable_nonempty_str(endpoint.get("url"), "profile.endpoint.url")
    _require_nullable_nonempty_str(
        endpoint.get("credential_ref"), "profile.endpoint.credential_ref"
    )

    _require_number(profile["duration_s"], "profile.duration_s", minimum=0, strict_minimum=True)
    _require_positive_int(profile["virtual_users"], "profile.virtual_users")
    _require_number(
        profile["tokens_per_request_estimate"], "profile.tokens_per_request_estimate",
        minimum=0,
    )
    _require_number(
        profile["price_per_1k_tokens_usd"], "profile.price_per_1k_tokens_usd", minimum=0,
    )

    if profile.get("request_count") is not None:
        _require_positive_int(profile["request_count"], "profile.request_count")
    if profile.get("spawn_rate_per_s") is not None:
        _require_number(
            profile["spawn_rate_per_s"], "profile.spawn_rate_per_s", minimum=0,
            strict_minimum=True,
        )
    if "script_path" in profile:
        _require_nullable_nonempty_str(profile["script_path"], "profile.script_path")
    if profile.get("adapter_args") is not None:
        args = profile["adapter_args"]
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise LoadTestValidationError("profile.adapter_args must be a list of strings")

    slo = profile.get("slo")
    if slo is not None:
        if not isinstance(slo, dict):
            raise LoadTestValidationError("profile.slo must be an object")
        unknown_slo = set(slo) - _SLO_KEYS
        if unknown_slo:
            raise LoadTestValidationError(
                "profile.slo has unknown key(s): " + ", ".join(sorted(unknown_slo))
            )
        if slo.get("max_p95_latency_ms") is not None:
            _require_number(
                slo["max_p95_latency_ms"], "profile.slo.max_p95_latency_ms", minimum=0,
                strict_minimum=True,
            )
        if slo.get("max_error_rate") is not None:
            value = slo["max_error_rate"]
            _require_number(value, "profile.slo.max_error_rate", minimum=0)
            if value > 1:
                raise LoadTestValidationError(
                    "profile.slo.max_error_rate must be within [0, 1]"
                )


# ---------------------------------------------------------------------------
# Budget estimate — pure arithmetic, no I/O, no network.
# ---------------------------------------------------------------------------
def estimate_projected_token_cost_usd(profile: Mapping[str, Any]) -> float:
    """Project the token cost of a run from *profile* alone.

    Uses ``request_count`` when the profile supplies one. Otherwise falls
    back to ``virtual_users`` (one request per virtual user is the floor
    projection when neither an explicit count nor an observed rate is
    available) — deterministic arithmetic only, never a network probe or a
    real dry-run against the endpoint.
    """
    tokens_per_request = profile["tokens_per_request_estimate"]
    price_per_1k = profile["price_per_1k_tokens_usd"]
    request_count = profile.get("request_count")
    if request_count is None:
        request_count = profile["virtual_users"]
    return (tokens_per_request * request_count / 1000.0) * price_per_1k


# ---------------------------------------------------------------------------
# Sample validation + summarization
# ---------------------------------------------------------------------------
def _validate_sample(sample: Any, index: int) -> None:
    if not isinstance(sample, dict):
        raise LoadTestValidationError(f"samples[{index}] must be an object")
    missing = {"latency_ms", "success", "tokens"} - sample.keys()
    if missing:
        raise LoadTestValidationError(
            f"samples[{index}] missing required key(s): " + ", ".join(sorted(missing))
        )
    _require_number(sample["latency_ms"], f"samples[{index}].latency_ms", minimum=0)
    _require_number(sample["tokens"], f"samples[{index}].tokens", minimum=0)
    if not isinstance(sample["success"], bool):
        raise LoadTestValidationError(f"samples[{index}].success must be a boolean")
    if "observed_at" in sample and sample["observed_at"] is not None:
        value = sample["observed_at"]
        if not isinstance(value, str) or not value:
            raise LoadTestValidationError(
                f"samples[{index}].observed_at must be null or a non-empty string"
            )
        _validate_iso8601_timestamp(value, f"samples[{index}].observed_at")


def _nearest_rank(sorted_values: list, percentile: float) -> Optional[float]:
    """Nearest-rank percentile: rank = ceil(p * n), clamped to [1, n], 1-indexed
    into the ascending-sorted values. E.g. for [100,200,300,400,500] (n=5):
    p50 -> rank=ceil(2.5)=3 -> sorted[2]=300; p95 -> rank=ceil(4.75)=5 ->
    sorted[4]=500."""
    n = len(sorted_values)
    if n == 0:
        return None
    rank = math.ceil(percentile * n)
    rank = max(1, min(rank, n))
    return sorted_values[rank - 1]


def summarize_samples(samples: Any, *, duration_s: Optional[float] = None) -> dict:
    """Validate *samples* (finite nonnegative `latency_ms`/`tokens`, boolean
    `success`) and compute deterministic aggregates: nearest-rank p50/p95
    latency, error rate, average tokens/request, and — only when *duration_s*
    is supplied (a finite number > 0) — observed throughput (`sample_count /
    duration_s`). An empty sample list is valid input and yields an
    all-``None`` (except ``sample_count: 0``) summary rather than raising.
    """
    if not isinstance(samples, list):
        raise LoadTestValidationError("samples must be a list")
    for index, sample in enumerate(samples):
        _validate_sample(sample, index)

    n = len(samples)
    if n == 0:
        return {
            "sample_count": 0,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "error_rate": None,
            "tokens_per_request": None,
            "throughput_rps": None,
        }

    latencies = sorted(sample["latency_ms"] for sample in samples)
    error_count = sum(1 for sample in samples if not sample["success"])
    total_tokens = sum(sample["tokens"] for sample in samples)

    effective_duration = None
    if duration_s is not None:
        _require_number(duration_s, "duration_s", minimum=0, strict_minimum=True)
        effective_duration = duration_s

    return {
        "sample_count": n,
        "p50_latency_ms": _nearest_rank(latencies, 0.50),
        "p95_latency_ms": _nearest_rank(latencies, 0.95),
        "error_rate": error_count / n,
        "tokens_per_request": total_tokens / n,
        "throughput_rps": (n / effective_duration) if effective_duration else None,
    }


def _empty_diagnostics() -> dict:
    diagnostics = summarize_samples([])
    diagnostics["adapter_error"] = None
    return diagnostics


def _extract_source_oldest_at(samples: list) -> Optional[str]:
    timestamps = [
        sample["observed_at"] for sample in samples
        if isinstance(sample.get("observed_at"), str) and sample.get("observed_at")
    ]
    if not timestamps:
        return None

    def _to_utc(value: str) -> datetime:
        normalized = value[:-1] + "+00:00" if value[-1] in "Zz" else value
        return datetime.fromisoformat(normalized)

    return min(timestamps, key=_to_utc)


def _evaluate_slo(slo: Optional[Mapping[str, Any]], diagnostics: Mapping[str, Any]) -> tuple:
    if not slo:
        return "not-verified", "no SLO thresholds declared in profile"
    breaches = []
    max_p95 = slo.get("max_p95_latency_ms")
    if (
        max_p95 is not None
        and diagnostics["p95_latency_ms"] is not None
        and diagnostics["p95_latency_ms"] > max_p95
    ):
        breaches.append(f"p95_latency_ms {diagnostics['p95_latency_ms']} > {max_p95}")
    max_error_rate = slo.get("max_error_rate")
    if (
        max_error_rate is not None
        and diagnostics["error_rate"] is not None
        and diagnostics["error_rate"] > max_error_rate
    ):
        breaches.append(f"error_rate {diagnostics['error_rate']} > {max_error_rate}")
    if breaches:
        return "must-fix", "; ".join(breaches)
    return "pass", "all declared SLO thresholds met"


def _build_spec_update_plan(
    profile: Mapping[str, Any], diagnostics: Mapping[str, Any], generated_at: str,
) -> dict:
    """Advisory-only patch plan — a snippet a human applies to SPEC.md's Load
    Profile section by hand. Never written by this module."""

    def _fmt(value: Optional[float], digits: int) -> str:
        return "n/a" if value is None else f"{value:.{digits}f}"

    snippet = (
        f"### Load profile: {profile['name']}\n\n"
        f"- captured_at: {generated_at}\n"
        f"- p50_latency_ms: {_fmt(diagnostics['p50_latency_ms'], 0)}\n"
        f"- p95_latency_ms: {_fmt(diagnostics['p95_latency_ms'], 0)}\n"
        f"- throughput_rps: {_fmt(diagnostics['throughput_rps'], 2)}\n"
        f"- tokens_per_request: {_fmt(diagnostics['tokens_per_request'], 1)}\n"
        f"- error_rate: {_fmt(diagnostics['error_rate'], 4)}\n"
    )
    return {
        "action": "advisory",
        "target": "SPEC.md",
        "section": "Load Profile",
        "snippet": snippet,
    }


def _finding(finding_id: str, status: str, detail: Optional[str] = None) -> dict:
    finding: dict = {"id": finding_id, "status": status}
    if detail is not None:
        finding["detail"] = detail
    return finding


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_loadtest(
    *,
    profile: Mapping[str, Any],
    budget_ceiling_usd: float,
    endpoint_class: str,
    adapter: Optional[LoadAdapter],
    generated_at: str,
    allow_production: bool = False,
) -> dict:
    """Run (or refuse to run) a guarded, budget-capped load test.

    Validation order (every check below runs BEFORE `adapter.run()` is ever
    called):

      1. Profile shape, budget ceiling, endpoint_class, allow_production,
         generated_at — malformed input raises `LoadTestValidationError`.
      2. Budget gate: `estimate_projected_token_cost_usd(profile) >
         budget_ceiling_usd` -> ``status: "aborted"``, zero adapter calls,
         `LOAD-002` must-fix.
      3. Production gate: `endpoint_class == "production" and not
         allow_production` -> ``status: "aborted"``, zero adapter calls,
         `LOAD-001` must-fix.
      4. Adapter gate: `adapter is None` (no engine selected) -> ``status:
         "partial"``, zero adapter calls, `LOAD-002` not-verified.
      5. Endpoint gate: `profile["endpoint"]` missing a `url` and/or
         `credential_ref` -> ``status: "partial"``, zero adapter calls,
         `LOAD-002` not-verified — this module never calls an adapter blind.

    Only once all five checks pass is `adapter.run(profile)` invoked exactly
    once. A `partial` adapter result (or a `complete` claim with zero
    samples, treated defensively as untrustworthy) keeps whatever aggregate
    diagnostics the samples support but NEVER produces a `spec_update_plan`
    and always reports `LOAD-002` `not-verified`. Only a genuinely `complete`
    run with nonempty samples can produce the advisory (never auto-applied)
    `spec_update_plan`.
    """
    validate_profile(profile)
    validate_budget_ceiling(budget_ceiling_usd)
    if endpoint_class not in VALID_ENDPOINT_CLASSES:
        raise LoadTestValidationError(
            "endpoint_class must be one of " + ", ".join(VALID_ENDPOINT_CLASSES)
        )
    if not isinstance(allow_production, bool):
        raise LoadTestValidationError("allow_production must be a boolean")
    _validate_iso8601_timestamp(generated_at, "generated_at")

    projected_cost = estimate_projected_token_cost_usd(profile)
    within_ceiling = projected_cost <= budget_ceiling_usd
    production_blocked = endpoint_class == "production" and not allow_production

    # LOAD-001 (production safety) is a pure function of endpoint_class /
    # allow_production — always computable regardless of what happens next.
    load_001_status = "must-fix" if production_blocked else "pass"
    load_001_detail = (
        "production endpoint requires allow_production=true before any load "
        "is generated"
        if production_blocked else None
    )

    endpoint = profile.get("endpoint") or {}
    endpoint_configured = bool(endpoint.get("url")) and bool(endpoint.get("credential_ref"))

    def _build(status: str, load_002: tuple, load_003: tuple, *, diagnostics: dict,
               adapter_name: Optional[str], spec_update_plan: Optional[dict] = None,
               source_oldest_at: Optional[str] = None) -> dict:
        findings = [
            _finding("LOAD-001", load_001_status, load_001_detail),
            _finding("LOAD-002", load_002[0], load_002[1]),
            _finding("LOAD-003", load_003[0], load_003[1]),
        ]
        payload = {
            "profile_name": profile["name"],
            "endpoint_class": endpoint_class,
            "endpoint_configured": endpoint_configured,
            "allow_production": allow_production,
            "adapter_name": adapter_name,
            "budget": {
                "ceiling_usd": budget_ceiling_usd,
                "projected_usd": projected_cost,
                "within_ceiling": within_ceiling,
            },
            "diagnostics": diagnostics,
            "spec_update_plan": spec_update_plan,
        }
        manifest = build_envelope(
            schema=LOAD_MANIFEST_SCHEMA,
            tool_version=TOOL_VERSION,
            status=status,
            generated_at=generated_at,
            valid_for_hours=DEFAULT_VALID_FOR_HOURS,
            source_oldest_at=source_oldest_at,
            findings=findings,
            payload=payload,
        )
        validate_load_manifest(manifest)
        return manifest

    # Gate 1: budget ceiling exceeded -> abort before ANY adapter call.
    if not within_ceiling:
        return _build(
            "aborted",
            ("must-fix", (
                f"projected cost ${projected_cost:.4f} exceeds ceiling "
                f"${budget_ceiling_usd:.4f}"
            )),
            ("not-verified", "run aborted before execution; no samples collected"),
            diagnostics=_empty_diagnostics(),
            adapter_name=None,
        )

    # Gate 2: production endpoint requires explicit confirmation -> abort
    # before ANY adapter call.
    if production_blocked:
        return _build(
            "aborted",
            ("must-fix", "run aborted: production endpoint without allow_production=true"),
            ("not-verified", "run aborted before execution; no samples collected"),
            diagnostics=_empty_diagnostics(),
            adapter_name=None,
        )

    # Gate 3: no adapter selected (select_adapter found neither k6 nor
    # locust) -> nothing was installed, and nothing is called.
    if adapter is None:
        return _build(
            "partial",
            ("not-verified", (
                "no load-test engine available (k6/locust not found on PATH); "
                "nothing was installed"
            )),
            ("not-verified", "no samples collected"),
            diagnostics=_empty_diagnostics(),
            adapter_name=None,
        )

    # Gate 4: no endpoint/credentials configured -> never call an adapter blind.
    if not endpoint_configured:
        return _build(
            "partial",
            ("not-verified", "profile.endpoint is missing a url and/or credential_ref"),
            ("not-verified", "no samples collected"),
            diagnostics=_empty_diagnostics(),
            adapter_name=adapter.name,
        )

    # All guards passed: call the adapter exactly once.
    result = adapter.run(profile)
    if not isinstance(result, Mapping) or result.get("status") not in ("complete", "partial"):
        raise LoadTestValidationError(
            "adapter.run() must return a mapping with status 'complete' or 'partial'"
        )
    samples = result.get("samples")
    if samples is None:
        samples = []
    if not isinstance(samples, list):
        raise LoadTestValidationError("adapter result 'samples' must be a list")
    adapter_error = result.get("error")
    if adapter_error is not None and not isinstance(adapter_error, str):
        raise LoadTestValidationError("adapter result 'error' must be a string or absent")

    # A "complete" claim with zero samples is not trustworthy evidence and is
    # treated defensively the same as a partial run.
    if result["status"] == "complete" and samples:
        diagnostics = summarize_samples(samples, duration_s=profile["duration_s"])
        diagnostics["adapter_error"] = None
        load_003 = _evaluate_slo(profile.get("slo"), diagnostics)
        spec_update_plan = _build_spec_update_plan(profile, diagnostics, generated_at)
        source_oldest_at = _extract_source_oldest_at(samples)
        return _build(
            "complete",
            ("pass", None),
            load_003,
            diagnostics=diagnostics,
            adapter_name=adapter.name,
            spec_update_plan=spec_update_plan,
            source_oldest_at=source_oldest_at,
        )

    diagnostics = (
        summarize_samples(samples, duration_s=profile["duration_s"]) if samples
        else summarize_samples([])
    )
    diagnostics["adapter_error"] = scrub_text(adapter_error) if adapter_error else None
    if samples and profile.get("slo"):
        load_003 = _evaluate_slo(profile.get("slo"), diagnostics)
    else:
        load_003 = ("not-verified", "insufficient samples to evaluate SLO thresholds")
    source_oldest_at = _extract_source_oldest_at(samples) if samples else None
    load_002_detail = diagnostics["adapter_error"] or "adapter run did not complete"
    return _build(
        "partial",
        ("not-verified", load_002_detail),
        load_003,
        diagnostics=diagnostics,
        adapter_name=adapter.name,
        source_oldest_at=source_oldest_at,
    )


# ---------------------------------------------------------------------------
# Manifest schema validation — hand-rolled mirror of
# references/load-manifest.schema.json, layered on the shared envelope's own
# validation. stdlib-only, no `jsonschema` runtime dependency; a test-only
# jsonschema parity check pins this to the schema file.
# ---------------------------------------------------------------------------
def _require_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{label} must be an object")
    return value


def _require_keys(value: dict, required: frozenset, label: str) -> None:
    missing = required - value.keys()
    if missing:
        raise ManifestValidationError(
            f"{label} missing required key(s): " + ", ".join(sorted(missing))
        )


def _reject_unknown_keys(value: dict, allowed: frozenset, label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ManifestValidationError(
            f"{label} has unknown key(s): " + ", ".join(sorted(unknown))
        )


_DIAGNOSTICS_KEYS = frozenset({
    "sample_count", "p50_latency_ms", "p95_latency_ms", "error_rate",
    "tokens_per_request", "throughput_rps", "adapter_error",
})
_BUDGET_KEYS = frozenset({"ceiling_usd", "projected_usd", "within_ceiling"})
_SPEC_PLAN_KEYS = frozenset({"action", "target", "section", "snippet"})
_TOP_LEVEL_KEYS = frozenset({
    "schema", "tool_version", "generated_at", "freshness", "status", "findings",
    "profile_name", "endpoint_class", "endpoint_configured", "allow_production",
    "adapter_name", "budget", "diagnostics", "spec_update_plan",
})


def _require_nonnegative_or_null(value: Any, label: str, *, maximum: Optional[float] = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestValidationError(f"{label} must be null or a number")
    if value < 0:
        raise ManifestValidationError(f"{label} must be >= 0")
    if maximum is not None and value > maximum:
        raise ManifestValidationError(f"{label} must be <= {maximum}")


def validate_load_manifest(manifest: dict) -> None:
    validate_envelope(manifest)
    manifest = _require_object(manifest, "load manifest")
    _require_keys(manifest, _TOP_LEVEL_KEYS, "load manifest")
    _reject_unknown_keys(manifest, _TOP_LEVEL_KEYS, "load manifest")

    if manifest["schema"] != LOAD_MANIFEST_SCHEMA:
        raise ManifestValidationError(f"schema must be {LOAD_MANIFEST_SCHEMA!r}")

    for index, finding in enumerate(manifest["findings"]):
        label = f"findings[{index}]"
        finding = _require_object(finding, label)
        _require_keys(finding, frozenset({"id", "status"}), label)
        _reject_unknown_keys(finding, frozenset({"id", "status", "detail"}), label)
        if finding["id"] not in ("LOAD-001", "LOAD-002", "LOAD-003"):
            raise ManifestValidationError(f"{label}.id must be a known LOAD-0xx id")
        if finding["status"] not in FINDING_STATUSES:
            raise ManifestValidationError(f"{label}.status must be a known finding status")
        if "detail" in finding and finding["detail"] is not None:
            if not isinstance(finding["detail"], str):
                raise ManifestValidationError(f"{label}.detail must be a string or null")

    if not isinstance(manifest["profile_name"], str) or not manifest["profile_name"]:
        raise ManifestValidationError("profile_name must be a non-empty string")
    if manifest["endpoint_class"] not in VALID_ENDPOINT_CLASSES:
        raise ManifestValidationError("endpoint_class must be a known endpoint class")
    if not isinstance(manifest["endpoint_configured"], bool):
        raise ManifestValidationError("endpoint_configured must be a boolean")
    if not isinstance(manifest["allow_production"], bool):
        raise ManifestValidationError("allow_production must be a boolean")
    adapter_name = manifest["adapter_name"]
    if adapter_name is not None and (not isinstance(adapter_name, str) or not adapter_name):
        raise ManifestValidationError("adapter_name must be null or a non-empty string")

    budget = _require_object(manifest["budget"], "budget")
    _require_keys(budget, _BUDGET_KEYS, "budget")
    _reject_unknown_keys(budget, _BUDGET_KEYS, "budget")
    ceiling = budget["ceiling_usd"]
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)) or not math.isfinite(ceiling) or ceiling <= 0:
        raise ManifestValidationError("budget.ceiling_usd must be a positive number")
    projected = budget["projected_usd"]
    if isinstance(projected, bool) or not isinstance(projected, (int, float)) or not math.isfinite(projected) or projected < 0:
        raise ManifestValidationError("budget.projected_usd must be a nonnegative number")
    if not isinstance(budget["within_ceiling"], bool):
        raise ManifestValidationError("budget.within_ceiling must be a boolean")

    diagnostics = _require_object(manifest["diagnostics"], "diagnostics")
    _require_keys(diagnostics, _DIAGNOSTICS_KEYS, "diagnostics")
    _reject_unknown_keys(diagnostics, _DIAGNOSTICS_KEYS, "diagnostics")
    sample_count = diagnostics["sample_count"]
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        raise ManifestValidationError("diagnostics.sample_count must be a nonnegative integer")
    _require_nonnegative_or_null(diagnostics["p50_latency_ms"], "diagnostics.p50_latency_ms")
    _require_nonnegative_or_null(diagnostics["p95_latency_ms"], "diagnostics.p95_latency_ms")
    _require_nonnegative_or_null(diagnostics["error_rate"], "diagnostics.error_rate", maximum=1)
    _require_nonnegative_or_null(diagnostics["tokens_per_request"], "diagnostics.tokens_per_request")
    _require_nonnegative_or_null(diagnostics["throughput_rps"], "diagnostics.throughput_rps")
    adapter_error = diagnostics["adapter_error"]
    if adapter_error is not None:
        if not isinstance(adapter_error, str):
            raise ManifestValidationError("diagnostics.adapter_error must be null or a string")
        if len(adapter_error) > 220:
            raise ManifestValidationError("diagnostics.adapter_error must be <= 220 characters")

    spec_update_plan = manifest["spec_update_plan"]
    if spec_update_plan is not None:
        plan = _require_object(spec_update_plan, "spec_update_plan")
        _require_keys(plan, _SPEC_PLAN_KEYS, "spec_update_plan")
        _reject_unknown_keys(plan, _SPEC_PLAN_KEYS, "spec_update_plan")
        if plan["action"] != "advisory":
            raise ManifestValidationError("spec_update_plan.action must be 'advisory'")
        for key in ("target", "section", "snippet"):
            if not isinstance(plan[key], str) or not plan[key]:
                raise ManifestValidationError(f"spec_update_plan.{key} must be a non-empty string")
        if manifest["status"] != "complete":
            raise ManifestValidationError(
                "spec_update_plan must be null unless status == 'complete'"
            )


# ---------------------------------------------------------------------------
# Privacy — recursive forbidden-key / secret-value defense, run right before
# any manifest is persisted. Structural allowlists (the schema above) already
# make most of this unreachable through the normal `run_loadtest` path; this
# is defense in depth against a hand-built or future-modified manifest.
# ---------------------------------------------------------------------------
_FORBIDDEN_KEY_WORDS = frozenset({
    "token", "secret", "password", "credential", "credentials",
    "authorization", "prompt", "completion", "completions", "payload",
    "stdout", "stderr",
})
_FORBIDDEN_KEY_SUBSTRINGS = ("api_key", "apikey", "access_key", "connection_string")


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in _FORBIDDEN_KEY_SUBSTRINGS):
        return True
    words = re.split(r"[^a-z0-9]+", lowered)
    return any(word in _FORBIDDEN_KEY_WORDS for word in words)


_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),
    re.compile(r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]+"),
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),
)


def _assert_no_unsafe_content(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and _is_forbidden_key(key):
                raise LoadTestPrivacyError(f"forbidden-shaped key in manifest: {key!r}")
            _assert_no_unsafe_content(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_unsafe_content(item)
    elif isinstance(obj, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(obj):
                raise LoadTestPrivacyError("secret-shaped value found in manifest")


def write_load_manifest(path: Any, manifest: dict) -> None:
    """Schema-validate + forbidden-key/secret scan, THEN atomically write. A
    manifest that fails either check never touches disk — whatever valid
    `specs/load-manifest.json` already exists is preserved untouched."""
    validate_load_manifest(manifest)
    _assert_no_unsafe_content(manifest)
    atomic_write_json(path, manifest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_cli_adapter(timeout_s: float = 120.0):
    import shutil

    name = select_adapter(detect_available_commands())
    if name is None:
        return None
    command_path = shutil.which(name)
    if not command_path:
        return None
    return CommandLoadAdapter(name=name, command_path=command_path, timeout_s=timeout_s)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a guarded, budget-capped load test and emit "
            "specs/load-manifest.json. Manual, cost-bearing — never run in "
            "an unattended/scheduled context."
        )
    )
    parser.add_argument("--profile", required=True, help="Path to a JSON load profile")
    parser.add_argument("--budget-ceiling-usd", required=True, type=float)
    parser.add_argument("--endpoint-class", required=True, choices=VALID_ENDPOINT_CLASSES)
    parser.add_argument(
        "--allow-production", action="store_true",
        help="Required explicit confirmation to generate load against a production endpoint",
    )
    parser.add_argument("--out", default="specs/load-manifest.json")
    parser.add_argument(
        "--generated-at", default=None,
        help="ISO-8601 timestamp for the manifest; defaults to now (UTC)",
    )
    parser.add_argument(
        "--adapter-timeout-s", type=float, default=120.0,
        help="Timeout for the selected k6/locust command, in seconds",
    )
    args = parser.parse_args(argv)

    with open(args.profile, "r", encoding="utf-8") as handle:
        profile = json.load(handle)

    generated_at = args.generated_at or _now_iso()
    adapter = build_cli_adapter(timeout_s=args.adapter_timeout_s)

    manifest = run_loadtest(
        profile=profile,
        budget_ceiling_usd=args.budget_ceiling_usd,
        endpoint_class=args.endpoint_class,
        allow_production=args.allow_production,
        adapter=adapter,
        generated_at=generated_at,
    )
    write_load_manifest(args.out, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
