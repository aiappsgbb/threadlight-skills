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

import json
import subprocess
import sys
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
    """Import `token_evidence` from `scripts_dir` and return the module.

    Raises `ImportError` naming `threadlight-consumption-iq` explicitly
    (rather than letting a bare `ModuleNotFoundError` leak through) when
    `token_evidence.py` is not present under `scripts_dir` — the sibling
    skill from the same plugin install is genuinely missing, and that must
    fail loudly and by name rather than silently falling back to a
    duplicated/reimplemented parser.

    Only the default `scripts_dir=CONSUMPTION_SCRIPTS` resolution is cached
    at module scope (see `_shared_parser` below): calling this with an
    explicit, non-default `scripts_dir` (as a test proving the absent-sibling
    error does) never touches or poisons that cache, `sys.path`, or
    `sys.modules` beyond the one clean `is_file()` check that raises before
    any import is attempted.
    """
    global _shared_parser
    if scripts_dir == CONSUMPTION_SCRIPTS and _shared_parser is not None:
        return _shared_parser

    if not (scripts_dir / "token_evidence.py").is_file():
        raise ImportError(
            "threadlight-router-bench requires the sibling skill "
            "threadlight-consumption-iq from the same plugin install; "
            f"token_evidence.py not found under {scripts_dir}"
        )

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import token_evidence as module  # noqa: E402

    if scripts_dir == CONSUMPTION_SCRIPTS:
        _shared_parser = module
    return module


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
