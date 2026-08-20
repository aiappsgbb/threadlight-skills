#!/usr/bin/env python3
"""A tiny, stdlib-only evidence gate used by production readiness lifecycle.

Implements a strict, minimal evaluator for the Task 1 tests. Designed to be
imported by tests (evaluate_evidence) and runnable as a CLI (main()).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


class EvidenceGateError(ValueError):
    """Raised on any validation / evidence problem."""


# Allowed schemas and verdicts
_ASSURANCE_SPECS = {
    "govern": ("threadlight-govern-manifest/v2", {"governed", "partial", "ungoverned"}, "governed"),
    "evals": ("threadlight-evals-manifest/v1", {"comprehensive", "partial", "offline-only", "none"}, "comprehensive"),
    "redteam": ("threadlight-redteam-manifest/v1", {"hardened", "partial", "vulnerable"}, "hardened"),
}


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception as e:  # pragma: no cover - defensive
        raise EvidenceGateError(f"unable to read {path}: {e}")
    try:
        data = json.loads(txt)
    except json.JSONDecodeError as e:
        raise EvidenceGateError(f"{path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise EvidenceGateError(f"{path} must be a JSON object")
    return data


def evaluate_evidence(root: Path | str, mode: str) -> Dict[str, Any]:
    """Evaluate evidence under `root` for the given `mode`.

    mode: one of 'live-smoke' or 'readiness-proof'.

    Returns a dict with at least: status, mode, readiness_asserted, verdicts
    or raises EvidenceGateError on failure.
    """
    root = Path(root)
    specs_dir = root / "specs"
    verdicts: Dict[str, str] = {}

    # Load assurance manifests if present and validate shape.
    for key, (expected_schema, allowed, passing) in _ASSURANCE_SPECS.items():
        path = specs_dir / f"{key}-manifest.json"
        if not path.exists():
            # absent manifest: in live-smoke we accept missing ones; in
            # readiness-proof it's an error (checked below).
            continue
        data = _load_json(path)
        schema = data.get("schema")
        if schema != expected_schema:
            raise EvidenceGateError(f"{path.name} schema expected {expected_schema!r}")
        verdict = data.get("verdict")
        if verdict not in allowed:
            raise EvidenceGateError(f"{path.name} verdict {verdict!r} not in allowed {sorted(allowed)}")
        verdicts[key] = verdict

    if mode == "live-smoke":
        # live smoke verifies structural validity only and never asserts readiness
        return {"status": "pass", "mode": mode, "readiness_asserted": False, "verdicts": verdicts}

    if mode != "readiness-proof":
        raise EvidenceGateError(f"unsupported mode: {mode!r}")

    # readiness-proof: require all assurance manifests present and passing
    missing = [k for k in _ASSURANCE_SPECS.keys() if k not in verdicts]
    if missing:
        raise EvidenceGateError(f"missing assurance manifests: {', '.join(missing)}")

    for key, (_, allowed, passing) in _ASSURANCE_SPECS.items():
        v = verdicts.get(key)
        if v != passing:
            raise EvidenceGateError(f"{key}-manifest.json expected {passing!r}, got {v!r}")

    # safe-check / post-deploy manifest
    post_path = root / "tests" / "postdeploy-manifest.json"
    if not post_path.exists():
        raise EvidenceGateError("postdeploy-manifest missing: tests/postdeploy-manifest.json")
    post = _load_json(post_path)
    if post.get("phase") != "post-deploy":
        raise EvidenceGateError("postdeploy-manifest phase must be 'post-deploy'")
    if post.get("gaps") != []:
        raise EvidenceGateError("postdeploy-manifest gaps must be empty list")

    # production readiness manifest
    pr_path = root / "tests" / "production-readiness-manifest.json"
    if not pr_path.exists():
        raise EvidenceGateError("production-readiness-manifest missing: tests/production-readiness-manifest.json")
    pr = _load_json(pr_path)
    if pr.get("would_fail_hard_gate") is not False:
        raise EvidenceGateError("production readiness would_fail_hard_gate must be false")
    if pr.get("go_live_recommendation") != "ready":
        raise EvidenceGateError("production readiness go_live_recommendation must be 'ready'")

    # kpi_scorecard validations
    score = pr.get("kpi_scorecard")
    if not isinstance(score, dict):
        raise EvidenceGateError("production readiness kpi_scorecard is missing or invalid")
    required_booleans = [
        "latency_declared",
        "cost_per_interaction_declared",
        "success_rate_declared",
        "deviation_alert_present",
        "traces_emit",
    ]
    for field in required_booleans:
        if score.get(field) is not True:
            raise EvidenceGateError(f"kpi_scorecard.{field} must be true")
    # numeric fields
    if not isinstance(score.get("eval_pass_rate"), (int, float)):
        raise EvidenceGateError("kpi_scorecard.eval_pass_rate must be measured")
    if not isinstance(score.get("cost_per_interaction_usd"), (int, float)):
        raise EvidenceGateError("kpi_scorecard.cost_per_interaction_usd must be measured")

    return {"status": "pass", "mode": mode, "readiness_asserted": True, "verdicts": verdicts}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    p = argparse.ArgumentParser(prog="evidence-gate")
    p.add_argument("--root", default=".", help="project root")
    p.add_argument("--mode", required=True, choices=["live-smoke", "readiness-proof"])
    args = p.parse_args(argv)
    try:
        out = evaluate_evidence(Path(args.root), args.mode)
    except EvidenceGateError as e:
        print(json.dumps({"status": "fail", "mode": args.mode, "error": str(e)}))
        return 2
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
