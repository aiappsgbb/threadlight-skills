#!/usr/bin/env python3
"""optimizer_stage.py — the Agent Optimizer as an optional, gated stage (#213).

Default quality loop: traces -> Foundry Insights -> human fix -> ``azd deploy`` ->
held-out gate. The optimizer stays **off** until the pilot opts in with
``evals/quality-loop.json``::

    {"optimizer": "on", "tools_read_only": true, "human_review": "<reviewer>"}

With ``on`` the stage fails closed (exit 2) unless all of these are present:

* ``evals/optimizer-estimate.json``: the receipt of
  ``project.agents.estimate_optimization_job`` (``cost.total`` low/typical/ceiling,
  ``prices_as_of``) for the job about to start;
* a passing held-out gate (``evals/gate-runs.json`` decision ``pass``, EVAL-109);
* the unmapped ``<agent>-optimizer-input`` eval in ``evals/azure.eval.yaml``;
* ``tools_read_only: true`` (optimizer runs call tools for real) and a named
  human reviewer for the candidate diff (``optimize apply`` + ``azd deploy``,
  never a direct deploy).

stdlib only; prints a JSON decision. Exit 0 = off or allowed, 2 = blocked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evals_check  # noqa: E402

CONFIG = "evals/quality-loop.json"
ESTIMATE = "evals/optimizer-estimate.json"


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def decide(root: str) -> dict:
    root_path = Path(root)
    config = _load(root_path / CONFIG) or {}
    mode = config.get("optimizer", "off")
    if mode not in ("off", "on"):
        return {"optimizer": mode, "allowed": False, "blocked_by": [f"{CONFIG}: optimizer must be off or on"]}
    if mode == "off":
        return {"optimizer": "off", "allowed": False, "blocked_by": [],
                "default_loop": "traces -> Insights -> human fix -> azd deploy -> held-out gate"}
    blocked = []
    estimate = _load(root_path / ESTIMATE)
    band = ((estimate or {}).get("cost") or {}).get("total") or {}
    if not all(isinstance(band.get(k), (int, float)) for k in ("low", "typical", "ceiling")):
        blocked.append(f"{ESTIMATE}: estimate_optimization_job receipt with cost.total low/typical/ceiling")
    elif not (estimate or {}).get("prices_as_of"):
        blocked.append(f"{ESTIMATE}: prices_as_of missing")
    caps = evals_check.evaluate(root, freshness_days=3650)
    gate = caps["gate_runs_recorded"].get("gate") or {}
    if gate.get("decision") != "pass" or caps["gate_split_ok"]["status"] != "pass":
        blocked.append("held-out gate result: evals/gate-runs.json decision pass on a held-out split "
                       f"(EVAL-107 {caps['gate_split_ok']['status']}, EVAL-109 {caps['gate_runs_recorded']['status']})")
    yaml_path, data = evals_check._eval_yaml(root)
    optimizer_evals = [e for e in (data or {}).get("evals") or [] if isinstance(e, dict)
                       and str(e.get("name", "")).endswith(evals_check.OPTIMIZER_EVAL_SUFFIX)]
    if not optimizer_evals or any("dataMapping" in (r or {}) for e in optimizer_evals
                                  for r in e.get("evaluators") or []):
        blocked.append("evals/azure.eval.yaml: an unmapped <agent>-optimizer-input eval (scripts/eval_yaml.py)")
    if config.get("tools_read_only") is not True:
        blocked.append(f"{CONFIG}: tools_read_only true (optimizer runs call tools for real)")
    if not config.get("human_review"):
        blocked.append(f"{CONFIG}: human_review names who reviews the candidate diff before optimize apply")
    return {"optimizer": "on", "allowed": not blocked, "blocked_by": blocked,
            "estimate_usd": band or None, "gate": gate or None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", default=".")
    args = ap.parse_args(argv)
    decision = decide(os.path.abspath(args.target))
    print(json.dumps(decision, indent=2))
    return 2 if decision["optimizer"] != "off" and not decision["allowed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
