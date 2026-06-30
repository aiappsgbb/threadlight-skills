"""Matrix orchestrator for threadlight-router-validation.

Dispatches the 3-arm x N-workload matrix as cost-clean waves: within a wave,
every cell targets a DISTINCT model deployment so Azure Monitor windows don't
bleed (metrics filter by ModelDeploymentName). One wave per workload.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable

Runner = Callable[[list[str]], str]
WORKFLOW = "threadlight-e2e-foundry.yml"


def _default_runner(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True,
                          text=True).stdout


def plan_waves(workloads: list[str],
               arms: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One wave per workload; each cell = arm x workload. Guarantees no two
    cells in a wave share a model deployment."""
    waves = []
    for wl in workloads:
        wave = [{**a, "workload": wl} for a in arms]
        deps = [c["model_deployment"] for c in wave]
        if len(deps) != len(set(deps)):
            raise ValueError(f"same-deployment overlap in wave for {wl}")
        waves.append(wave)
    return waves


def _latest_run_id(runner: Runner, repo: str, ref: str) -> int | None:
    out = runner(["run", "list", "--workflow", WORKFLOW, "--repo", repo,
                  "--branch", ref, "--limit", "1",
                  "--json", "databaseId,createdAt"])
    rows = json.loads(out or "[]")
    return rows[0]["databaseId"] if rows else None


def _dispatch_cell(runner: Runner, cell: dict[str, Any], repo: str,
                   ref: str) -> dict[str, Any]:
    runner(["workflow", "run", WORKFLOW, "--repo", repo, "--ref", ref,
            "-f", f"model_deployment={cell['model_deployment']}",
            "-f", f"wire_api={cell['wire_api']}",
            "-f", f"workload={cell['workload']}",
            "-f", "mode=full", "-f", "teardown=true"])
    time.sleep(0)  # dispatch returns no id; caller polls run list
    run_id = _latest_run_id(runner, repo, ref)
    return {**cell, "run_id": run_id}


def dispatch_matrix(workloads: list[str], arms: list[dict[str, Any]], *,
                    repo: str, ref: str, runner: Runner | None = None,
                    poll: bool = True,
                    poll_interval: int = 60) -> list[dict[str, Any]]:
    runner = runner or _default_runner
    cells: list[dict[str, Any]] = []
    for wave in plan_waves(workloads, arms):
        wave_cells = [_dispatch_cell(runner, c, repo, ref) for c in wave]
        if poll:
            _wait_for_wave(runner, wave_cells, repo, poll_interval)
        cells.extend(wave_cells)
    return cells


def _wait_for_wave(runner: Runner, cells: list[dict[str, Any]], repo: str,
                   interval: int) -> None:
    pending = {c["run_id"] for c in cells if c.get("run_id")}
    while pending:
        time.sleep(interval)
        done = set()
        for rid in pending:
            out = runner(["run", "view", str(rid), "--repo", repo,
                          "--json", "status"])
            if json.loads(out or "{}").get("status") == "completed":
                done.add(rid)
        pending -= done


def write_manifest(cells: list[dict[str, Any]], path) -> None:
    from pathlib import Path
    Path(path).write_text(json.dumps({"cells": cells}, indent=2),
                          encoding="utf-8")
