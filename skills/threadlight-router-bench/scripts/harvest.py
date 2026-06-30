#!/usr/bin/env python3
"""Run-metadata + log harvest for threadlight-router-bench.

Pulls three signals for a single run (the `learn` path needs only these — no
paired baseline):
  * phase parity   — per-phase worst step conclusion from `gh run view --json jobs`
  * raw logs       — `--log-failed` for failures (high precision), else `--log`
  * Phase-5 KPIs   — the govern/evals/redteam leg manifests (downloaded via gh)
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

# Ordered: first matching pattern wins. Keys are the normalized phase names.
_PHASE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("install", re.compile(r"Install threadlight_quickstart", re.I)),
    ("smoke", re.compile(r"Smoke-check", re.I)),
    ("design", re.compile(r"\[Phase 1/\d", re.I)),
    ("pattern", re.compile(r"\[Phase 2/\d", re.I)),
    ("deploy", re.compile(r"\[Phase 3/\d", re.I)),
    ("invoke", re.compile(r"\[Phase 4/\d", re.I)),
    ("legs", re.compile(r"\[Phase 5/\d", re.I)),
    ("teardown", re.compile(r"Teardown", re.I)),
]

# Worse conclusions sort higher so the per-phase worst wins.
_SEVERITY = {"success": 0, "skipped": 0, "neutral": 1, "cancelled": 2,
             "timed_out": 3, "failure": 3, None: 1}

_STEP_RE = re.compile(r"^\u25cf ")          # '● ' agent step marker
_ATTEMPT_RE = re.compile(r"Copilot CLI attempt \d+ of \d+")


def _phase_for(step_name: str) -> str | None:
    for phase, pat in _PHASE_PATTERNS:
        if pat.search(step_name):
            return phase
    return None


def parse_phase_parity(jobs_doc: dict[str, Any]) -> dict[str, str]:
    """Return {phase: worst_conclusion} across all jobs/steps."""
    worst: dict[str, str] = {}
    for job in jobs_doc.get("jobs", []):
        for step in job.get("steps", []):
            phase = _phase_for(step.get("name", ""))
            if phase is None:
                continue
            concl = step.get("conclusion") or "neutral"
            if phase not in worst or _SEVERITY.get(concl, 1) > _SEVERITY.get(worst[phase], 1):
                worst[phase] = concl
    return worst


def count_rounds(phase_log_paths: list[Path]) -> dict[str, int]:
    """Count agent steps ('● ' lines) and retry attempts across phase logs.

    Returns {'steps': int, 'attempts': int, 'total': int}. `total` == steps
    (the headline rounds-to-done effort signal); attempts is reported
    separately so a thrash-and-retry run is distinguishable.
    """
    steps = 0
    attempts = 0
    for path in phase_log_paths:
        p = Path(path)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if _STEP_RE.match(line):
                steps += 1
            if _ATTEMPT_RE.search(line):
                attempts += 1
    return {"steps": steps, "attempts": attempts, "total": steps}


def load_leg_manifests(specs_dir: Path) -> dict[str, dict[str, Any]]:
    """Load {govern,evals,redteam}-manifest.json from a dir (missing leg -> {})."""
    out: dict[str, dict[str, Any]] = {}
    for leg in ("govern", "evals", "redteam"):
        p = Path(specs_dir) / f"{leg}-manifest.json"
        out[leg] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return out


def fetch_jobs(run_id: int, repo: str = "aiappsgbb/threadlight-skills",
               runner: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """`gh run view --json jobs` for a run."""
    run = runner or _default_runner
    return json.loads(run(["run", "view", str(run_id), "--repo", repo, "--json", "jobs"]))


def fetch_view(run_id: int, repo: str = "aiappsgbb/threadlight-skills",
               runner: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """`gh run view --json ...` for run metadata + the metric window."""
    run = runner or _default_runner
    return json.loads(run([
        "run", "view", str(run_id), "--repo", repo, "--json",
        "databaseId,conclusion,status,headBranch,displayTitle,startedAt,updatedAt"]))


def fetch_logs(run_id: int, conclusion: str | None,
               repo: str = "aiappsgbb/threadlight-skills",
               runner: Callable[[list[str]], str] | None = None) -> str:
    """Fetch logs: `--log-failed` for failures (precision), else full `--log`."""
    run = runner or _default_runner
    flag = "--log-failed" if conclusion == "failure" else "--log"
    return run(["run", "view", str(run_id), "--repo", repo, flag])


def download_run(run_id: int, dest: Path,
                 repo: str = "aiappsgbb/threadlight-skills",
                 runner: Callable[[list[str]], str] | None = None) -> Path:
    """`gh run download` the run's artifact bundle into dest; return dest."""
    run = runner or _default_runner
    Path(dest).mkdir(parents=True, exist_ok=True)
    run(["run", "download", str(run_id), "--repo", repo, "--dir", str(dest)])
    return Path(dest)


def find_specs_dir(bundle_root: Path) -> Path | None:
    """Locate the returns-triage/specs dir holding the leg manifests in a bundle."""
    for p in Path(bundle_root).rglob("*-manifest.json"):
        if p.parent.name == "specs":
            return p.parent
    return None


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    # `--log-failed` exits non-zero when there are no failures; tolerate that.
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError("gh " + " ".join(args) + " failed:\n" + proc.stderr.strip())
    return proc.stdout
