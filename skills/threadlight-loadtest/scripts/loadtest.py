#!/usr/bin/env python3
"""loadtest.py — the guarded, budget-capped load-evidence leg for
Threadlight pilots.

A **manual, cost-bearing** skill: it generates real traffic against a real
endpoint via an injected/selected `LoadAdapter` (see `adapters.py`). Because
that traffic costs real money (tokens, compute) and can hit a production
endpoint, every run is guarded by gates evaluated *before* the adapter is
ever touched:

  1. **Budget** — the run's projected token cost (see `project_token_cost`,
     preferring the profile's declared `projected_token_cost_usd` and otherwise
     a deterministic derivation from explicit request/rate + token-rate inputs)
     is compared to the MANDATORY `budget_ceiling_usd`. A KNOWN projection over
     the ceiling **aborts** the run with zero adapter calls and a `LOAD-002`
     must-fix finding. A projection that is *unavailable* (no declared value and
     no explicit inputs to derive one) yields a `partial` / `LOAD-002`
     not-verified manifest — never a fabricated request count that could
     undercount real spend.
  2. **Production safety** — a `production` `endpoint_class` requires the
     caller to pass `allow_production=True` explicitly. Otherwise the run
     **aborts** with zero adapter calls and a `LOAD-001` must-fix finding.

Only after those gates pass — and only when an adapter was actually selected
(`select_adapter` in `adapters.py`) and the profile declares a configured
endpoint — is `adapter.run(profile)` invoked. A `complete` result (nonempty
samples) can produce an ADVISORY, plan-only SPEC update snippet; this module
NEVER writes to SPEC.md itself. A `partial`/`aborted` manifest OMITS the
`spec_update_plan` key entirely. `specs/load-manifest.json` is the only file
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
DEFAULT_PROFILE_NAME = "load-profile"
VALID_ENDPOINT_CLASSES = ("non-production", "production")
FINDING_STATUSES = frozenset({"pass", "must-fix", "should-fix", "not-verified"})
REQUIRED_FINDING_IDS = ("LOAD-001", "LOAD-002", "LOAD-003")
VALID_PROJECTION_SOURCES = frozenset({"declared", "derived", "unavailable"})


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
# The approved profile shape (gap-closure plan, Task 5) carries a DIRECT
# ``projected_token_cost_usd`` plus volume fields (``peak_requests_per_second`` /
# ``hold_seconds``). Older "rich" profiles describing a live run (endpoint,
# script, virtual_users, ...) are still accepted. Structurally NOTHING is
# required: a profile that supplies neither a declared projection nor enough
# explicit inputs to DERIVE one deterministically does not raise — it yields a
# ``partial`` / ``LOAD-002: not-verified`` manifest (the budget-projection gate
# in ``run_loadtest``), because inventing a request count would undercount real
# spend. A malformed value that IS present (e.g. an endpoint that is not an
# object) is still a controlled ``LoadTestValidationError``.
RECOGNIZED_PROFILE_KEYS = frozenset({
    # Direct, declared budget projection (preferred).
    "projected_token_cost_usd",
    # Volume fields (approved shape) — also feed deterministic derivation.
    "peak_requests_per_second", "hold_seconds",
    # Explicit derivation inputs / legacy rich-shape budget fields.
    "request_count", "tokens_per_request_estimate", "price_per_1k_tokens_usd",
    "duration_s", "virtual_users",
    # Live-run / adapter descriptors.
    "name", "endpoint", "spawn_rate_per_s", "script_path", "slo", "adapter_args",
})
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
    """Structurally validate a load *profile* (a strict allowlist).

    Unknown keys are rejected, but almost every field is optional: the only
    structural rules beyond per-field types are that a declared ``endpoint`` —
    WHEN PRESENT — is a well-formed object. A profile that cannot yield a budget
    projection is NOT a validation error; it is handled at run time as a
    ``partial`` / ``not-verified`` result (never a raise, never a silent
    default). See :func:`project_token_cost`.
    """
    if not isinstance(profile, dict):
        raise LoadTestValidationError("profile must be an object")

    unknown = profile.keys() - RECOGNIZED_PROFILE_KEYS
    if unknown:
        raise LoadTestValidationError(
            "profile has unknown key(s): " + ", ".join(sorted(unknown))
        )

    if "name" in profile:
        _require_nullable_nonempty_str(profile.get("name"), "profile.name")

    # Endpoint is OPTIONAL. Absent (or null) endpoint => no live target, handled
    # as a partial/not-verified run downstream — never a raise. A present-but-
    # malformed endpoint IS a controlled error.
    if profile.get("endpoint") is not None:
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

    # Direct, declared projection. Nonnegative + finite; zero is allowed ONLY
    # because it is explicit here (never a silent default).
    if profile.get("projected_token_cost_usd") is not None:
        _require_number(
            profile["projected_token_cost_usd"], "profile.projected_token_cost_usd",
            minimum=0,
        )

    if profile.get("peak_requests_per_second") is not None:
        _require_number(
            profile["peak_requests_per_second"], "profile.peak_requests_per_second",
            minimum=0, strict_minimum=True,
        )
    if profile.get("hold_seconds") is not None:
        _require_number(
            profile["hold_seconds"], "profile.hold_seconds", minimum=0, strict_minimum=True,
        )
    if profile.get("duration_s") is not None:
        _require_number(profile["duration_s"], "profile.duration_s", minimum=0, strict_minimum=True)
    if profile.get("virtual_users") is not None:
        _require_positive_int(profile["virtual_users"], "profile.virtual_users")
    if profile.get("tokens_per_request_estimate") is not None:
        _require_number(
            profile["tokens_per_request_estimate"], "profile.tokens_per_request_estimate",
            minimum=0,
        )
    if profile.get("price_per_1k_tokens_usd") is not None:
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
def project_token_cost(profile: Mapping[str, Any]) -> tuple:
    """Project the token cost of a run and report its provenance.

    Returns ``(projected_usd, source)`` where *source* is one of:

      * ``"declared"`` — the profile carried an explicit
        ``projected_token_cost_usd``, used verbatim (zero is allowed only
        because it is explicit and already validated);
      * ``"derived"`` — no declared projection, but the profile supplied enough
        EXPLICIT inputs to compute one deterministically: an explicit
        ``request_count`` (or ``hold_seconds`` × ``peak_requests_per_second``)
        together with ``tokens_per_request_estimate`` and
        ``price_per_1k_tokens_usd``;
      * ``"unavailable"`` — none of the above. ``projected_usd`` is ``None`` and
        the caller must NOT run the adapter; it emits a ``partial`` /
        ``not-verified`` manifest instead of inventing a request count that
        would undercount.

    Pure arithmetic — never a network probe or a real dry-run. Notably it never
    falls back to ``virtual_users`` (one-request-per-VU would undercount a real
    sustained run).
    """
    declared = profile.get("projected_token_cost_usd")
    if declared is not None:
        return float(declared), "declared"

    request_count = profile.get("request_count")
    if request_count is None:
        rate = profile.get("peak_requests_per_second")
        duration = profile.get("hold_seconds")
        if duration is None:
            duration = profile.get("duration_s")
        if rate is not None and duration is not None:
            request_count = rate * duration

    tokens_per_request = profile.get("tokens_per_request_estimate")
    price_per_1k = profile.get("price_per_1k_tokens_usd")
    if (
        request_count is not None
        and tokens_per_request is not None
        and price_per_1k is not None
    ):
        return (tokens_per_request * request_count / 1000.0) * price_per_1k, "derived"

    return None, "unavailable"


def estimate_projected_token_cost_usd(profile: Mapping[str, Any]) -> Optional[float]:
    """Convenience wrapper returning only the numeric projection (or ``None``
    when it is ``"unavailable"``). See :func:`project_token_cost` for the
    provenance-bearing form the orchestrator relies on."""
    return project_token_cost(profile)[0]


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
    for optional_metric in ("cold_start_latency_ms", "time_to_scale_s"):
        if sample.get(optional_metric) is not None:
            _require_number(
                sample[optional_metric], f"samples[{index}].{optional_metric}", minimum=0,
            )
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
    """Validate *samples* (finite nonnegative ``latency_ms``/``tokens``, boolean
    ``success``, optional nonnegative ``cold_start_latency_ms``/
    ``time_to_scale_s``) and compute deterministic aggregates: nearest-rank
    p50/p95/p99 latency, error rate, average tokens/request, observed throughput
    (``sample_count / duration_s`` — only when *duration_s* is a finite number
    > 0), and the worst observed cold-start latency / time-to-scale WHEN the
    samples carry them. An empty sample list is valid input and yields an
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
            "p99_latency_ms": None,
            "error_rate": None,
            "tokens_per_request": None,
            "throughput_rps": None,
            "cold_start_latency_ms": None,
            "time_to_scale_s": None,
        }

    latencies = sorted(sample["latency_ms"] for sample in samples)
    error_count = sum(1 for sample in samples if not sample["success"])
    total_tokens = sum(sample["tokens"] for sample in samples)
    cold_starts = [
        sample["cold_start_latency_ms"] for sample in samples
        if sample.get("cold_start_latency_ms") is not None
    ]
    scale_times = [
        sample["time_to_scale_s"] for sample in samples
        if sample.get("time_to_scale_s") is not None
    ]

    effective_duration = None
    if duration_s is not None:
        _require_number(duration_s, "duration_s", minimum=0, strict_minimum=True)
        effective_duration = duration_s

    return {
        "sample_count": n,
        "p50_latency_ms": _nearest_rank(latencies, 0.50),
        "p95_latency_ms": _nearest_rank(latencies, 0.95),
        "p99_latency_ms": _nearest_rank(latencies, 0.99),
        "error_rate": error_count / n,
        "tokens_per_request": total_tokens / n,
        "throughput_rps": (n / effective_duration) if effective_duration else None,
        "cold_start_latency_ms": max(cold_starts) if cold_starts else None,
        "time_to_scale_s": max(scale_times) if scale_times else None,
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
    diagnostics: Mapping[str, Any], generated_at: str,
) -> dict:
    """Advisory, plan-only patch a human pastes into SPEC.md's load-profile
    section by hand — emitted for a COMPLETE run only, never written by this
    module and never overwriting declared values.

    The snippet is a small, safe YAML block nested under
    ``load_profile: performance:`` carrying ``captured_at`` plus every observed
    metric. Only validated numeric aggregates and the (ISO-8601, whitespace-
    free) timestamp are interpolated — no caller-controlled free text — so the
    block cannot smuggle YAML structure or secrets.
    """

    def _num(value: Optional[float], digits: int) -> Optional[str]:
        return None if value is None else f"{value:.{digits}f}"

    lines = [
        "load_profile:",
        "  performance:",
        f'    captured_at: "{generated_at}"',
    ]
    ordered = (
        ("p50_latency_ms", _num(diagnostics.get("p50_latency_ms"), 0)),
        ("p95_latency_ms", _num(diagnostics.get("p95_latency_ms"), 0)),
        ("p99_latency_ms", _num(diagnostics.get("p99_latency_ms"), 0)),
        ("error_rate", _num(diagnostics.get("error_rate"), 4)),
        ("tokens_per_request", _num(diagnostics.get("tokens_per_request"), 1)),
        ("observed_throughput_rps", _num(diagnostics.get("throughput_rps"), 2)),
        ("cold_start_latency_ms", _num(diagnostics.get("cold_start_latency_ms"), 0)),
        ("time_to_scale_s", _num(diagnostics.get("time_to_scale_s"), 2)),
    )
    for key, rendered in ordered:
        if rendered is not None:
            lines.append(f"    {key}: {rendered}")
    snippet = "\n".join(lines) + "\n"
    return {
        "action": "advisory",
        "target": "SPEC.md",
        "section": "load_profile/performance",
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

    Every gate below is evaluated BEFORE ``adapter.run()`` is ever called. The
    order matters — the first matching gate wins:

      0. Structural input validation (profile shape, budget ceiling,
         endpoint_class, allow_production, generated_at). Malformed input raises
         ``LoadTestValidationError``. A *missing* endpoint or projection is NOT
         malformed — it flows to a partial gate below.
      1. Budget gate (declared/derived projection only): a KNOWN
         ``project_token_cost`` strictly greater than ``budget_ceiling_usd`` ->
         ``status: "aborted"``, ``LOAD-002`` must-fix, zero adapter calls.
      2. Production gate: ``endpoint_class == "production" and not
         allow_production`` -> ``status: "aborted"``, ``LOAD-001`` must-fix,
         zero adapter calls.
      3. Projection gate: the projection is ``"unavailable"`` (neither declared
         nor deterministically derivable) -> ``status: "partial"``, ``LOAD-002``
         not-verified, zero adapter calls. We never invent a request count to
         fabricate a projection that could undercount real spend.
      4. Adapter gate: ``adapter is None`` -> ``status: "partial"``,
         ``LOAD-002`` not-verified, zero adapter calls.
      5. Endpoint gate: no ``url`` and/or ``credential_ref`` configured ->
         ``status: "partial"``, ``LOAD-002`` not-verified, zero adapter calls —
         this module never calls an adapter blind.

    Only once all gates pass is ``adapter.run(profile)`` invoked exactly once. A
    ``partial`` adapter result (or a ``complete`` claim with zero samples,
    treated defensively as untrustworthy) keeps whatever aggregate diagnostics
    the samples support but OMITS ``spec_update_plan`` entirely and reports
    ``LOAD-002`` not-verified. Only a genuinely ``complete`` run with nonempty
    samples produces the advisory (never auto-applied) ``spec_update_plan``.

    ``LOAD-001`` is a pure function of ``endpoint_class`` / ``allow_production``
    and is therefore always reported accurately, even when a different gate is
    what aborts/limits the run (e.g. a budget abort on a production endpoint
    still shows ``LOAD-001`` must-fix).
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

    projected_cost, projection_source = project_token_cost(profile)
    within_ceiling: Optional[bool] = (
        None if projected_cost is None else projected_cost <= budget_ceiling_usd
    )
    production_blocked = endpoint_class == "production" and not allow_production

    # LOAD-001 (production safety) is a pure function of endpoint_class /
    # allow_production — always computable regardless of what happens next.
    load_001_status = "must-fix" if production_blocked else "pass"
    load_001_detail = (
        "production endpoint requires allow_production=true before any load "
        "is generated"
        if production_blocked else None
    )

    profile_name = profile.get("name") or DEFAULT_PROFILE_NAME
    endpoint = profile.get("endpoint") or {}
    endpoint_configured = bool(endpoint.get("url")) and bool(endpoint.get("credential_ref"))
    effective_duration = profile.get("hold_seconds")
    if effective_duration is None:
        effective_duration = profile.get("duration_s")

    def _build(status: str, load_002: tuple, load_003: tuple, *, diagnostics: dict,
               adapter_name: Optional[str], spec_update_plan: Optional[dict] = None,
               source_oldest_at: Optional[str] = None) -> dict:
        findings = [
            _finding("LOAD-001", load_001_status, load_001_detail),
            _finding("LOAD-002", load_002[0], load_002[1]),
            _finding("LOAD-003", load_003[0], load_003[1]),
        ]
        payload = {
            "profile_name": profile_name,
            "endpoint_class": endpoint_class,
            "endpoint_configured": endpoint_configured,
            "allow_production": allow_production,
            "adapter_name": adapter_name,
            "budget": {
                "ceiling_usd": budget_ceiling_usd,
                "projected_usd": projected_cost,
                "within_ceiling": within_ceiling,
                "projection_source": projection_source,
            },
            "diagnostics": diagnostics,
        }
        # spec_update_plan is present ONLY for a complete run; partial/aborted
        # manifests omit the key entirely (never a null placeholder).
        if spec_update_plan is not None:
            payload["spec_update_plan"] = spec_update_plan
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

    # Gate 1: KNOWN projection strictly over the ceiling -> abort before ANY
    # adapter call. (When the projection is unavailable, within_ceiling is None
    # and this gate is skipped in favour of the partial gate below — we never
    # abort on a cost we could not compute.)
    if within_ceiling is False:
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

    # Gate 3: no budget projection is available (neither a declared
    # projected_token_cost_usd nor enough explicit inputs to derive one). We
    # refuse to run rather than invent a request count that could undercount.
    if projected_cost is None:
        return _build(
            "partial",
            ("not-verified", (
                "budget projection unavailable: profile declares no "
                "projected_token_cost_usd and lacks explicit request_count / "
                "rate+duration and token-rate inputs to derive one"
            )),
            ("not-verified", "no samples collected"),
            diagnostics=_empty_diagnostics(),
            adapter_name=None,
        )

    # Gate 4: no adapter selected (select_adapter found neither k6 nor
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

    # Gate 5: no endpoint/credentials configured -> never call an adapter blind.
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
    #
    # Malformed sample DATA from an adapter (structurally a list, but carrying
    # values that fail summarize_samples' own validation — e.g. a non-numeric
    # latency, a negative token count, or success coerced from an int) must
    # NEVER crash the run. Such input degrades to a ``partial`` / ``LOAD-002``
    # not-verified manifest with safe, zeroed aggregate diagnostics and no
    # spec-update plan — exactly like an adapter that returned no samples.
    try:
        if result["status"] == "complete" and samples:
            diagnostics = summarize_samples(samples, duration_s=effective_duration)
            diagnostics["adapter_error"] = None
            load_003 = _evaluate_slo(profile.get("slo"), diagnostics)
            spec_update_plan = _build_spec_update_plan(diagnostics, generated_at)
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
            summarize_samples(samples, duration_s=effective_duration) if samples
            else summarize_samples([])
        )
        source_oldest_at = _extract_source_oldest_at(samples) if samples else None
    except LoadTestValidationError as exc:
        # Injected/parsed samples were structurally a list but semantically
        # invalid. Do not propagate — emit a safe partial with zeroed
        # diagnostics and a scrubbed diagnostic string, and no spec plan.
        safe_detail = scrub_text(f"adapter samples rejected: {exc}")
        diagnostics = _empty_diagnostics()
        diagnostics["adapter_error"] = safe_detail
        return _build(
            "partial",
            ("not-verified", safe_detail),
            ("not-verified", "insufficient samples to evaluate SLO thresholds"),
            diagnostics=diagnostics,
            adapter_name=adapter.name,
            source_oldest_at=None,
        )

    diagnostics["adapter_error"] = scrub_text(adapter_error) if adapter_error else None
    if samples and profile.get("slo"):
        load_003 = _evaluate_slo(profile.get("slo"), diagnostics)
    else:
        load_003 = ("not-verified", "insufficient samples to evaluate SLO thresholds")
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
    "sample_count", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
    "error_rate", "tokens_per_request", "throughput_rps",
    "cold_start_latency_ms", "time_to_scale_s", "adapter_error",
})
_BUDGET_KEYS = frozenset({
    "ceiling_usd", "projected_usd", "within_ceiling", "projection_source",
})
_SPEC_PLAN_KEYS = frozenset({"action", "target", "section", "snippet"})
# ``spec_update_plan`` is intentionally excluded from the REQUIRED set: it is
# present only for a complete run and MUST be omitted (not null) otherwise.
_REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema", "tool_version", "generated_at", "freshness", "status", "findings",
    "profile_name", "endpoint_class", "endpoint_configured", "allow_production",
    "adapter_name", "budget", "diagnostics",
})
_TOP_LEVEL_KEYS = _REQUIRED_TOP_LEVEL_KEYS | frozenset({"spec_update_plan"})


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
    _require_keys(manifest, _REQUIRED_TOP_LEVEL_KEYS, "load manifest")
    _reject_unknown_keys(manifest, _TOP_LEVEL_KEYS, "load manifest")

    if manifest["schema"] != LOAD_MANIFEST_SCHEMA:
        raise ManifestValidationError(f"schema must be {LOAD_MANIFEST_SCHEMA!r}")

    for index, finding in enumerate(manifest["findings"]):
        label = f"findings[{index}]"
        finding = _require_object(finding, label)
        _require_keys(finding, frozenset({"id", "status"}), label)
        _reject_unknown_keys(finding, frozenset({"id", "status", "detail"}), label)
        if finding["id"] not in REQUIRED_FINDING_IDS:
            raise ManifestValidationError(f"{label}.id must be a known LOAD-0xx id")
        if finding["status"] not in FINDING_STATUSES:
            raise ManifestValidationError(f"{label}.status must be a known finding status")
        if "detail" in finding and finding["detail"] is not None:
            if not isinstance(finding["detail"], str):
                raise ManifestValidationError(f"{label}.detail must be a string or null")

    # Exactly one finding per required id — no duplicates, no missing, no
    # unknown ids, no extras. (Cross-skill consumers key on these ids.)
    finding_ids = [finding["id"] for finding in manifest["findings"]]
    if sorted(finding_ids) != sorted(REQUIRED_FINDING_IDS):
        raise ManifestValidationError(
            "findings must contain exactly one each of "
            + ", ".join(REQUIRED_FINDING_IDS)
        )

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
    projection_source = budget["projection_source"]
    if projection_source not in VALID_PROJECTION_SOURCES:
        raise ManifestValidationError(
            "budget.projection_source must be one of "
            + ", ".join(sorted(VALID_PROJECTION_SOURCES))
        )
    projected = budget["projected_usd"]
    within = budget["within_ceiling"]
    if projection_source == "unavailable":
        # An unavailable projection cannot claim a number or a verdict.
        if projected is not None:
            raise ManifestValidationError(
                "budget.projected_usd must be null when projection_source is 'unavailable'"
            )
        if within is not None:
            raise ManifestValidationError(
                "budget.within_ceiling must be null when projection_source is 'unavailable'"
            )
    else:
        if isinstance(projected, bool) or not isinstance(projected, (int, float)) or not math.isfinite(projected) or projected < 0:
            raise ManifestValidationError("budget.projected_usd must be a nonnegative number")
        if not isinstance(within, bool):
            raise ManifestValidationError("budget.within_ceiling must be a boolean")

    diagnostics = _require_object(manifest["diagnostics"], "diagnostics")
    _require_keys(diagnostics, _DIAGNOSTICS_KEYS, "diagnostics")
    _reject_unknown_keys(diagnostics, _DIAGNOSTICS_KEYS, "diagnostics")
    sample_count = diagnostics["sample_count"]
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        raise ManifestValidationError("diagnostics.sample_count must be a nonnegative integer")
    _require_nonnegative_or_null(diagnostics["p50_latency_ms"], "diagnostics.p50_latency_ms")
    _require_nonnegative_or_null(diagnostics["p95_latency_ms"], "diagnostics.p95_latency_ms")
    _require_nonnegative_or_null(diagnostics["p99_latency_ms"], "diagnostics.p99_latency_ms")
    _require_nonnegative_or_null(diagnostics["error_rate"], "diagnostics.error_rate", maximum=1)
    _require_nonnegative_or_null(diagnostics["tokens_per_request"], "diagnostics.tokens_per_request")
    _require_nonnegative_or_null(diagnostics["throughput_rps"], "diagnostics.throughput_rps")
    _require_nonnegative_or_null(
        diagnostics["cold_start_latency_ms"], "diagnostics.cold_start_latency_ms"
    )
    _require_nonnegative_or_null(diagnostics["time_to_scale_s"], "diagnostics.time_to_scale_s")
    adapter_error = diagnostics["adapter_error"]
    if adapter_error is not None:
        if not isinstance(adapter_error, str):
            raise ManifestValidationError("diagnostics.adapter_error must be null or a string")
        if len(adapter_error) > 220:
            raise ManifestValidationError("diagnostics.adapter_error must be <= 220 characters")

    # spec_update_plan: OMITTED entirely for partial/aborted; an object is
    # allowed only for a complete run. A null placeholder is never valid.
    has_plan = "spec_update_plan" in manifest
    if manifest["status"] != "complete":
        if has_plan:
            raise ManifestValidationError(
                "spec_update_plan must be omitted unless status == 'complete'"
            )
    elif has_plan:
        plan = _require_object(manifest["spec_update_plan"], "spec_update_plan")
        _require_keys(plan, _SPEC_PLAN_KEYS, "spec_update_plan")
        _reject_unknown_keys(plan, _SPEC_PLAN_KEYS, "spec_update_plan")
        if plan["action"] != "advisory":
            raise ManifestValidationError("spec_update_plan.action must be 'advisory'")
        for key in ("target", "section", "snippet"):
            if not isinstance(plan[key], str) or not plan[key]:
                raise ManifestValidationError(f"spec_update_plan.{key} must be a non-empty string")


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
    # OpenAI keys: modern hyphenated project/service keys (``sk-proj-...``,
    # ``sk-svcacct-...``) AND the classic single-segment ``sk-<40+ alnum>``.
    re.compile(r"\bsk-[A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]+)+"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]+"),
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),
    # A masked-secret marker (******) is itself evidence a secret was present;
    # persisting it (or a partially masked value) is rejected outright.
    re.compile(r"\*{6,}"),
    # Azure SAS token signature segment.
    re.compile(r"(?i)[?&]sig=[A-Za-z0-9%/+_-]{8,}"),
    # Storage / connection-string secret components.
    re.compile(r"(?i)\b(?:AccountKey|SharedAccessKey|SharedAccessSignature)=[^;\s]+"),
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

    try:
        with open(args.profile, "r", encoding="utf-8") as handle:
            profile = json.load(handle)
    except (OSError, ValueError) as exc:
        print(
            scrub_text(f"error: could not read profile {args.profile!r}: {exc}"),
            file=sys.stderr,
        )
        return 2

    generated_at = args.generated_at or _now_iso()
    adapter = build_cli_adapter(timeout_s=args.adapter_timeout_s)

    # run_loadtest degrades adapter-execution and sample-parsing failures to a
    # ``partial`` manifest (it does not raise for them), so those still write a
    # manifest below. A raise here means a controlled input/contract error
    # (bad profile shape, privacy violation); surface it scrubbed, non-zero.
    try:
        manifest = run_loadtest(
            profile=profile,
            budget_ceiling_usd=args.budget_ceiling_usd,
            endpoint_class=args.endpoint_class,
            allow_production=args.allow_production,
            adapter=adapter,
            generated_at=generated_at,
        )
    except (LoadTestValidationError, LoadTestPrivacyError) as exc:
        print(scrub_text(f"error: load test aborted: {exc}"), file=sys.stderr)
        return 2

    try:
        write_load_manifest(args.out, manifest)
    except (ManifestValidationError, LoadTestPrivacyError, OSError) as exc:
        print(
            scrub_text(f"error: could not write manifest to {args.out!r}: {exc}"),
            file=sys.stderr,
        )
        return 2

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
