#!/usr/bin/env python3
"""Smoke test for `threadlight-auto` orchestrator decisions.

Runs the orchestrator against each fixture under `tests/fixtures/` and asserts
the `next_action.type` matches expectations.

Run locally: `python3 skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
Exit codes: 0 = all green; N = failures.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(workspace: Path) -> dict:
    out = subprocess.run(
        [sys.executable, str(ORCH), "--workspace", str(workspace), "--dry-run", "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if not out.stdout.strip():
        raise RuntimeError(f"orchestrator emitted no JSON for {workspace}; stderr={out.stderr!r}")
    return json.loads(out.stdout)


def main() -> int:
    cases = [
        ("blank",        "run",       {"preflight", "design", "deploy", "safe_check", "cost_projection", "invoke", "evals", "redteam", "govern"}, set()),
        # NOTE: all-complete fixture predates cost_projection + the discover/protect
        # legs; no cost-manifest.json → cost_projection runs, and the cascade plus
        # absent leg manifests make evals/redteam/govern run too.
        ("all-complete", "run",       {"cost_projection", "invoke", "evals", "redteam", "govern"},                   {"preflight", "design", "deploy", "safe_check"}),
        ("hard-stop",    "hard_stop", None,                                                                         None),
        ("spec-edited",  "run",       None,                                                                         None),
    ]
    failures = 0
    for fixture_name, expected_type, expected_run, expected_skip in cases:
        fixture = FIXTURES / fixture_name
        if not fixture.exists():
            print(f"❌ {fixture_name}: fixture dir missing")
            failures += 1
            continue
        try:
            report = run(fixture)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ {fixture_name}: orchestrator crashed: {exc!r}")
            failures += 1
            continue
        actual_type = report["next_action"]["type"]
        if actual_type != expected_type:
            print(f"❌ {fixture_name}: expected next_action.type={expected_type!r}, got {actual_type!r}")
            failures += 1
            continue
        if expected_run is not None:
            actual_run = set(report["next_action"].get("stages_to_run", []))
            if actual_run != expected_run:
                print(f"❌ {fixture_name}: stages_to_run mismatch; expected={sorted(expected_run)} actual={sorted(actual_run)}")
                failures += 1
                continue
        if expected_skip is not None:
            actual_skip = set(report["next_action"].get("stages_to_skip", []))
            if actual_skip != expected_skip:
                print(f"❌ {fixture_name}: stages_to_skip mismatch; expected={sorted(expected_skip)} actual={sorted(actual_skip)}")
                failures += 1
                continue
        if fixture_name == "spec-edited":
            if "design" not in set(report["next_action"].get("stages_to_run", [])):
                print(f"❌ spec-edited: expected 'design' in stages_to_run after hash mismatch; got {report['next_action'].get('stages_to_run')}")
                failures += 1
                continue
        print(f"✅ {fixture_name}: next_action.type={actual_type}")

    # --- extra: assert cost_projection is in STAGES between safe_check and invoke ---
    import importlib.util as _ilu, sys as _sys
    _s = _ilu.spec_from_file_location("_orch_check", str(ORCH))
    _m = _ilu.module_from_spec(_s)
    _sys.modules["_orch_check"] = _m
    _s.loader.exec_module(_m)
    stages = _m.STAGES
    if "cost_projection" not in stages:
        print("❌ STAGES: cost_projection not in STAGES list")
        failures += 1
    else:
        cp_idx = stages.index("cost_projection")
        sc_idx = stages.index("safe_check")
        inv_idx = stages.index("invoke")
        if not (sc_idx < cp_idx < inv_idx):
            print(f"❌ STAGES: cost_projection at index {cp_idx} not between safe_check ({sc_idx}) and invoke ({inv_idx})")
            failures += 1
        else:
            print(f"✅ STAGES order: safe_check({sc_idx}) < cost_projection({cp_idx}) < invoke({inv_idx})")

    # --- extra: assert the discover/protect legs follow invoke in STAGES ---
    for leg in ("evals", "redteam", "govern"):
        if leg not in stages:
            print(f"❌ STAGES: {leg} not in STAGES list")
            failures += 1
        elif stages.index(leg) <= stages.index("invoke"):
            print(f"❌ STAGES: {leg} at index {stages.index(leg)} not after invoke ({stages.index('invoke')})")
            failures += 1
        else:
            print(f"✅ STAGES order: invoke({stages.index('invoke')}) < {leg}({stages.index(leg)})")

    print(f"\n=== {len(cases) + 1 - failures}/{len(cases) + 1} passed ===")
    return failures


if __name__ == "__main__":
    sys.exit(main())
