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
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),  # OpenAI-style key
    re.compile(r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]+"),  # JWT
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


def build_argv(name: str, command_path: str, profile: Mapping[str, Any]) -> list[str]:
    """Build a safe, list-form argv for the selected *name* engine.

    Every value placed in the argv is validated first (non-empty string, no
    control characters) — this is the ONLY place a production adapter
    constructs a command line, and the result is always invoked with
    ``shell=False`` (see `CommandLoadAdapter.run`), so there is no shell
    metacharacter/injection surface even for adversarial profile values.
    """
    script_path = _safe_str(profile.get("script_path"), "profile.script_path")
    duration_s = profile["duration_s"]
    virtual_users = profile["virtual_users"]
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
        argv = [
            command_path, "-f", script_path, "--headless",
            "--users", str(virtual_users),
            "--spawn-rate", str(profile.get("spawn_rate_per_s", virtual_users)),
            "--run-time", f"{duration_s}s",
        ]
        if target:
            argv += ["--host", _safe_str(target, "profile.endpoint.url")]
        return argv

    raise AdapterArgumentError(f"unsupported adapter name: {name!r}")


def parse_ndjson_samples(stdout: str) -> tuple[list[dict], Optional[str]]:
    """Parse the documented newline-delimited-JSON sample contract every
    harness script (k6/locust) emits to stdout: one JSON object per line with
    at least `latency_ms`, `success`, and `tokens` (see SKILL.md). Unparsable
    or short lines are skipped; if nothing parses, a short, static error
    (never echoing the raw stdout) explains why.
    """
    samples: list[dict] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and REQUIRED_SAMPLE_KEYS <= set(record):
            samples.append(record)
    if not samples:
        return [], "no parsable NDJSON sample lines in command output"
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
            detail = scrub_text(completed.stderr or completed.stdout or "")
            error = f"{self.name} exited {completed.returncode}"
            if detail:
                error = f"{error}: {detail}"
            return {"status": "partial", "samples": samples, "error": error}

        if parse_error:
            return {"status": "partial", "samples": samples, "error": scrub_text(parse_error)}

        return {"status": "complete", "samples": samples}
