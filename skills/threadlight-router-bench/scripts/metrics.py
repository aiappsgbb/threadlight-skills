#!/usr/bin/env python3
"""Azure Monitor token-metric harvest for the bench cost axis.

The pure payload parsing itself (`parse_token_metrics` / `parse_token_series`)
lives in `threadlight-consumption-iq/scripts/token_evidence.py` and is loaded
here as a sibling-skill import (see `_load_shared_parser` below) rather than
duplicated, so router-bench and consumption-iq can never silently disagree
about the same Azure Monitor payload. This module keeps only what is
genuinely router-bench-specific: the `az monitor metrics list` fetch/CLI
orchestration and the `parse_metrics` delegation router-bench's own callers
already depend on.

Caveat (design constraint): Cognitive Services token metrics carry NO run-id
dimension, so benches must be serialized on a shared deployment and bounded by
the run's start/end window to attribute usage correctly.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

# `threadlight-router-bench` and `threadlight-consumption-iq` are always
# installed together from the same plugin and never independently
# versioned — this repository is the deployment unit, so a
# repository-relative import of the sibling skill's scripts directory is a
# real dependency, not an assumption about the caller's environment.
CONSUMPTION_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "threadlight-consumption-iq" / "scripts"
)

_shared_parser: Any = None


def _load_shared_parser(scripts_dir: Path = CONSUMPTION_SCRIPTS) -> Any:
    """Load `token_evidence.py` from `scripts_dir` and return the module.

    Loaded in isolation via `importlib.util.spec_from_file_location` +
    `module_from_spec`/`exec_module` — never `sys.path` — so this never
    depends on (or perturbs) the interpreter's global import machinery.
    The freshly executed module is also never registered under the
    `token_evidence` name (or any other name) in `sys.modules`: doing so
    would risk a real `import token_evidence` elsewhere in the process
    silently picking up whichever `scripts_dir` happened to load last (a
    filename-collision hazard), or a nondefault call here silently
    overwriting/reusing an already-cached entry instead of loading its own
    module fresh — exactly the failure this replaces (the previous
    implementation used `sys.path.insert` + `import token_evidence`, so a
    nondefault load after the default one was already cached would return
    the *same* cached module rather than the nondefault directory's own
    file).

    Raises `ImportError` naming `threadlight-consumption-iq` explicitly
    (rather than letting a bare `ModuleNotFoundError`/`FileNotFoundError`
    leak through) when `token_evidence.py` is not present under
    `scripts_dir` — the sibling skill from the same plugin install is
    genuinely missing, and that must fail loudly and by name rather than
    silently falling back to a duplicated/reimplemented parser. This check
    runs before any import machinery touches `scripts_dir` at all.

    Only the default `scripts_dir=CONSUMPTION_SCRIPTS` resolution is cached
    at module scope (see `_shared_parser` below): calling this with an
    explicit, non-default `scripts_dir` (as a test proving both the
    absent-sibling error and isolated-module loading does) never touches or
    poisons that cache, `sys.path`, or `sys.modules`.
    """
    global _shared_parser
    is_default = scripts_dir == CONSUMPTION_SCRIPTS
    if is_default and _shared_parser is not None:
        return _shared_parser

    module_path = scripts_dir / "token_evidence.py"
    if not module_path.is_file():
        raise ImportError(
            "threadlight-router-bench requires the sibling skill "
            "threadlight-consumption-iq from the same plugin install; "
            f"token_evidence.py not found under {scripts_dir}"
        )

    # A name derived from the module's own resolved path (never the bare
    # `"token_evidence"`) is used only as the spec's label; it is not
    # inserted into `sys.modules`, so it cannot collide with — or be
    # shadowed by — any other module of the same base filename loaded
    # elsewhere in the process.
    spec = importlib.util.spec_from_file_location(
        f"_shared_token_evidence[{module_path}]", module_path
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"could not load token_evidence.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if is_default:
        _shared_parser = module
    return module


# Bound once at import time from the default sibling module so callers can
# catch it as `metrics.TokenEvidenceError` without also importing
# `token_evidence` themselves — a stable re-export, not a duplicated
# definition (there is exactly one `TokenEvidenceError` class; this name is
# an alias for it, not a lookalike).
TokenEvidenceError = _load_shared_parser().TokenEvidenceError


def parse_metrics(doc: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Aggregate az-monitor metrics JSON into {model: {input, output}} token
    totals. Delegates to the shared `token_evidence.parse_token_metrics` —
    see that module for the parsing contract (case-insensitive dimension
    matching, fail-closed totals, model fallback chain)."""
    return _load_shared_parser().parse_token_metrics(doc)


def fetch_metrics(resource_id: str, start_iso: str, end_iso: str,
                  deployment: str = "model-router", interval: str = "PT1H",
                  runner: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """`az monitor metrics list` for InputTokens+OutputTokens, split by model."""
    run = runner or _default_runner
    out = run([
        "monitor", "metrics", "list", "--resource", resource_id,
        "--metrics", "InputTokens", "OutputTokens",
        "--start-time", start_iso, "--end-time", end_iso,
        "--interval", interval, "--aggregation", "Total",
        "--filter", f"ModelDeploymentName eq '{deployment}' and ModelName eq '*'",
        "-o", "json",
    ])
    return json.loads(out)


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["az", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("az " + " ".join(args) + " failed:\n" + proc.stderr.strip())
    return proc.stdout
