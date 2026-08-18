"""adapters.py — load-generation engine selection and safe invocation for
`threadlight-loadtest`.

Defines the `LoadAdapter` protocol every load-test engine (real or injected
fake) satisfies, plus:

  * `select_adapter` — picks among ALREADY-detected command names (k6 first,
    then locust). It never probes the filesystem itself and NEVER installs
    anything — a caller supplies the set of commands it already found on
    PATH (see `detect_available_commands`, a thin, side-effect-free
    `shutil.which` probe).
  * `CommandLoadAdapter` — the production adapter. It invokes exactly the ONE
    selected, already-installed command with a safe, list-form argv
    (`shell=False`), a mandatory timeout, and NEVER returns raw command
    stdout/stderr — only parsed samples and a scrubbed, length-capped error
    summary. Every value placed in the argv is validated (non-empty string,
    no control characters) before use.

Tests inject their own `LoadAdapter`-shaped objects (e.g. a `FakeAdapter`)
directly into `loadtest.run_loadtest(adapter=...)` — no real command is ever
required to exercise the orchestration logic.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, runtime_checkable

# Selection priority: k6 first, then locust. Adding a third engine means
# adding its name here AND a `_build_<name>_argv` branch in `build_argv` —
# `select_adapter` and `detect_available_commands` need no other change.
ADAPTER_PRIORITY: tuple[str, ...] = ("k6", "locust")

# Every request sample production adapters and test fakes are expected to
# produce/accept. See references/load-manifest.schema.json and SKILL.md for
# the documented contract consumers rely on.
REQUIRED_SAMPLE_KEYS = frozenset({"latency_ms", "success", "tokens"})
_OPTIONAL_NUMERIC_SAMPLE_KEYS = ("cold_start_latency_ms", "time_to_scale_s")

DEFAULT_TIMEOUT_S = 120.0


@runtime_checkable
class LoadAdapter(Protocol):
    """Protocol every load-test engine (fake or real) must satisfy."""

    name: str

    def run(self, profile: Mapping[str, Any]) -> Mapping[str, Any]:
        """Execute *profile* and return
        ``{"status": "complete" | "partial", "samples": [...], "error"?: str}``.

        Must never raise for an ordinary execution failure (timeout, nonzero
        exit, unparsable output) — those become ``status: "partial"`` with a
        scrubbed ``error`` string instead. Raising is reserved for a
        programmer error (e.g. an unusable *profile*).
        """
        ...  # pragma: no cover - structural protocol


class AdapterArgumentError(ValueError):
    """Raised when a profile lacks a field a command adapter needs to build a
    safe argv, or a field contains an unsafe (control-character) value."""


def detect_available_commands(
    candidates: Iterable[str] = ADAPTER_PRIORITY,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> set[str]:
    """Probe (never install) which *candidates* are already on ``PATH``.

    A thin wrapper over `shutil.which` so callers/tests can inject a fake
    `which` without touching the real filesystem/PATH.
    """
    return {name for name in candidates if which(name)}


def select_adapter(available_commands: Iterable[str]) -> Optional[str]:
    """Pick the highest-priority engine name present in *available_commands*.

    Returns ``"k6"``, ``"locust"``, or ``None`` when neither is available.
    Pure function of its input — never probes the filesystem, never installs
    anything. When it returns ``None`` the caller (``loadtest.run_loadtest``)
    must proceed with ``adapter=None``, producing a `partial` manifest with a
    `LOAD-002` `not-verified` finding rather than attempting any install.
    """
    available = set(available_commands)
    for name in ADAPTER_PRIORITY:
        if name in available:
            return name
    return None


# ---------------------------------------------------------------------------
# Credential / secret scrubbing — the ONLY path any text captured from a real
# command invocation may take before it is returned from `run()` or embedded
# in a manifest. Structural (a full URL/path) is fine; a bearer token, an
# Authorization header, an embedded basic-auth credential, or an OpenAI/JWT
# shaped key is redacted.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(?:pass(?:word)?|secret|api[_-]?key|access[_-]?key|"
        r"client[_-]?secret|token)\b\s*[:=]\s*\S+"
    ),
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),  # credentials embedded in a URL
    # OpenAI-style keys: classic ``sk-<40+ alnum>`` AND modern hyphenated
    # project/service keys (``sk-proj-...``, ``sk-svcacct-...``, ``sk-admin-...``)
    # whose bodies mix ``-``/``_`` separators. Anchored on ``\bsk-`` so opaque
    # UUID/hex IDs (which never start with ``sk-``) are left untouched.
    re.compile(r"\bsk-[A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]+)+"),  # hyphenated sk-proj-/sk-svcacct-
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),  # classic single-segment key
    re.compile(r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]+"),  # JWT
    re.compile(r"(?i)[?&]sig=[A-Za-z0-9%/+_-]{8,}"),  # Azure SAS signature
    re.compile(
        r"(?i)\b(?:AccountKey|SharedAccessKey|SharedAccessSignature)=[^;\s]+"
    ),  # storage / connection-string secret
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),  # PEM private key block
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),  # lone PEM header
    re.compile(r"\*{6,}"),  # a masked-secret marker (******) — normalize to our sentinel
)

_MAX_SCRUBBED_LENGTH = 200


def scrub_text(text: Optional[str]) -> str:
    """Redact credential-shaped substrings, collapse whitespace, and cap
    length. Idempotent — scrubbing already-scrubbed text is a no-op beyond
    whitespace collapsing/truncation, so callers may scrub defensively at
    more than one layer without doubling redaction markers unexpectedly.
    """
    if not text:
        return ""
    scrubbed = text
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED]", scrubbed)
    scrubbed = " ".join(scrubbed.split())
    if len(scrubbed) > _MAX_SCRUBBED_LENGTH:
        scrubbed = scrubbed[: _MAX_SCRUBBED_LENGTH - 1] + "…"
    return scrubbed


_SAFE_ARG_RE = re.compile(r"^[^\x00-\x1f\x7f]*$")  # no control chars / NUL / DEL


def _safe_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdapterArgumentError(f"{label} must be a non-empty string")
    if not _SAFE_ARG_RE.match(value):
        raise AdapterArgumentError(f"{label} must not contain control characters")
    return value


def _positive_number(value: Any, label: str) -> float:
    """Validate *value* is a strictly-positive, finite number (bool excluded)
    and return it as a float. Raises :class:`AdapterArgumentError` — never a
    bare ``TypeError``/``KeyError`` — so ``CommandLoadAdapter.run`` can catch it
    and degrade to a partial LOAD-002 rather than crashing the run."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterArgumentError(f"{label} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise AdapterArgumentError(f"{label} must be a positive, finite number")
    return float(value)


def _positive_int(value: Any, label: str) -> int:
    """Validate *value* is a strictly-positive integer (bool excluded)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterArgumentError(f"{label} must be a positive integer")
    if value <= 0:
        raise AdapterArgumentError(f"{label} must be a positive integer")
    return value


def _resolve_duration_seconds(profile: Mapping[str, Any]) -> Any:
    """Resolve the run duration for the argv, preferring the documented
    ``hold_seconds`` and falling back to the legacy ``duration_s`` ONLY when
    ``hold_seconds`` is absent (mirrors ``run_loadtest``'s ``effective_duration``
    precedence). The value is validated strictly positive + finite and returned
    unchanged (so an integer keeps rendering as ``"10s"``, not ``"10.0s"``). A
    missing or invalid duration is an :class:`AdapterArgumentError`, never a
    ``KeyError``.
    """
    value = profile.get("hold_seconds")
    label = "profile.hold_seconds"
    if value is None:
        value = profile.get("duration_s")
        label = "profile.duration_s"
    if value is None:
        raise AdapterArgumentError(
            "profile must supply a positive hold_seconds (or legacy duration_s) "
            "for the run duration"
        )
    _positive_number(value, label)
    return value


def build_argv(name: str, command_path: str, profile: Mapping[str, Any]) -> list[str]:
    """Build a safe, list-form argv for the selected *name* engine.

    Every value placed in the argv is validated first (non-empty control-char-free
    ``script_path``, a positive-integer ``virtual_users``, and a positive-finite
    duration resolved from ``hold_seconds`` — falling back to legacy ``duration_s``
    only when ``hold_seconds`` is absent). A missing or malformed field raises
    :class:`AdapterArgumentError` (never a ``KeyError``/``TypeError``), which
    ``CommandLoadAdapter.run`` catches and turns into a partial LOAD-002 result.
    This is the ONLY place a production adapter constructs a command line, and the
    result is always invoked with ``shell=False`` (see ``CommandLoadAdapter.run``),
    so there is no shell metacharacter/injection surface even for adversarial
    profile values.
    """
    script_path = _safe_str(profile.get("script_path"), "profile.script_path")
    virtual_users = _positive_int(profile.get("virtual_users"), "profile.virtual_users")
    duration_s = _resolve_duration_seconds(profile)
    endpoint = profile.get("endpoint") or {}
    target = endpoint.get("url")

    if name == "k6":
        argv = [
            command_path, "run", "--quiet",
            "--vus", str(virtual_users),
            "--duration", f"{duration_s}s",
        ]
        if target:
            argv += ["-e", f"TARGET_URL={_safe_str(target, 'profile.endpoint.url')}"]
        argv.append(script_path)
        return argv

    if name == "locust":
        spawn_rate = profile.get("spawn_rate_per_s")
        if spawn_rate is None:
            spawn_rate = virtual_users
        else:
            spawn_rate = _positive_number(spawn_rate, "profile.spawn_rate_per_s")
        argv = [
            command_path, "-f", script_path, "--headless",
            "--users", str(virtual_users),
            "--spawn-rate", str(spawn_rate),
            "--run-time", f"{duration_s}s",
        ]
        if target:
            argv += ["--host", _safe_str(target, "profile.endpoint.url")]
        return argv

    raise AdapterArgumentError(f"unsupported adapter name: {name!r}")


def _is_finite_nonnegative_number(value: Any) -> bool:
    """True for a real, finite, nonnegative number. Booleans are excluded (a
    JSON ``true``/``false`` is never a latency/token count)."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _normalize_sample(record: Any) -> Optional[dict]:
    """Return a clean sample dict (only the recognized keys) when *record*
    satisfies the SAME rules ``loadtest.summarize_samples`` enforces, else
    ``None`` so the caller can COUNT it as rejected rather than silently admit
    bad data.

    Strictly: ``latency_ms``/``tokens`` finite and nonnegative; ``success`` a
    real boolean (an integer ``1``/``0`` is REJECTED, never coerced to a bool);
    optional ``cold_start_latency_ms``/``time_to_scale_s`` finite nonnegative
    when present; optional ``observed_at`` a non-empty string when present.
    Unknown extra keys are dropped so nothing unexpected from a command's stdout
    can ride along into downstream aggregation.
    """
    if not isinstance(record, dict) or not REQUIRED_SAMPLE_KEYS <= set(record):
        return None
    if not _is_finite_nonnegative_number(record["latency_ms"]):
        return None
    if not _is_finite_nonnegative_number(record["tokens"]):
        return None
    if not isinstance(record["success"], bool):  # reject success=1, never coerce
        return None
    clean: dict = {
        "latency_ms": record["latency_ms"],
        "success": record["success"],
        "tokens": record["tokens"],
    }
    for key in _OPTIONAL_NUMERIC_SAMPLE_KEYS:
        if record.get(key) is not None:
            if not _is_finite_nonnegative_number(record[key]):
                return None
            clean[key] = record[key]
    observed_at = record.get("observed_at")
    if observed_at is not None:
        if not isinstance(observed_at, str) or not observed_at:
            return None
        clean["observed_at"] = observed_at
    return clean


def parse_ndjson_samples(stdout: str) -> tuple[list[dict], Optional[str]]:
    """Parse the documented newline-delimited-JSON sample contract every harness
    script (k6/locust) emits to stdout: one JSON object per line carrying at
    least ``latency_ms``, ``success``, and ``tokens`` (see SKILL.md).

    Each candidate line is validated/normalized with the SAME rules as
    ``loadtest.summarize_samples`` (via :func:`_normalize_sample`) — bad values
    are REJECTED and COUNTED, never silently coerced. The returned error is a
    short, static, count-only diagnostic (it NEVER echoes the raw stdout) and is
    non-``None`` whenever ANY non-blank line was rejected OR nothing valid parsed
    — so ``CommandLoadAdapter`` degrades such a run to ``partial`` rather than
    presenting partially-corrupt evidence as ``complete``.
    """
    samples: list[dict] = []
    considered = 0
    rejected = 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        considered += 1
        try:
            record = json.loads(line)
        except ValueError:
            rejected += 1
            continue
        normalized = _normalize_sample(record)
        if normalized is None:
            rejected += 1
            continue
        samples.append(normalized)

    if not samples:
        if considered == 0:
            return [], "no NDJSON sample lines in command output"
        return [], (
            f"no valid NDJSON sample lines in command output "
            f"({rejected} of {considered} rejected)"
        )
    if rejected:
        return samples, (
            f"{rejected} of {considered} NDJSON sample lines rejected as malformed"
        )
    return samples, None


@dataclass
class CommandLoadAdapter:
    """A `LoadAdapter` that invokes exactly ONE already-installed command
    (``name`` in ``{"k6", "locust"}``) with a safe list-form argv,
    ``shell=False``, and a mandatory timeout. Never returns raw stdout/stderr
    — only parsed samples and a scrubbed, truncated error summary.

    ``runner`` defaults to `subprocess.run` and exists purely so tests can
    inject a fake without ever spawning a real process.
    """

    name: str
    command_path: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    runner: Callable[..., Any] = subprocess.run

    def run(self, profile: Mapping[str, Any]) -> dict:
        try:
            argv = build_argv(self.name, self.command_path, profile)
        except AdapterArgumentError as exc:
            return {"status": "partial", "samples": [], "error": scrub_text(str(exc))}

        try:
            completed = self.runner(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "partial",
                "samples": [],
                "error": f"{self.name} timed out after {self.timeout_s}s",
            }
        except OSError as exc:
            return {
                "status": "partial",
                "samples": [],
                "error": scrub_text(f"{self.name} failed to start: {exc}"),
            }

        samples, parse_error = parse_ndjson_samples(completed.stdout or "")

        if completed.returncode != 0:
            # Only stderr feeds the (scrubbed) error summary — stdout is the
            # NDJSON sample channel, so its raw debug/banner lines are NEVER
            # surfaced here, even scrubbed.
            detail = scrub_text(completed.stderr or "")
            error = f"{self.name} exited {completed.returncode}"
            if detail:
                error = f"{error}: {detail}"
            return {"status": "partial", "samples": samples, "error": error}

        if parse_error:
            return {"status": "partial", "samples": samples, "error": scrub_text(parse_error)}

        return {"status": "complete", "samples": samples}
