#!/usr/bin/env python3
"""Smoke test for `threadlight-auto` orchestrator decisions.

Runs the orchestrator against each fixture under `tests/fixtures/` and asserts
the `next_action.type` matches expectations.

Run locally: `python3 skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
Exit codes: 0 = all green; N = failures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_orchestrator():
    """Load orchestrator.py as a module (with the sys.modules registration the
    Python 3.14 dataclass + importlib combination requires)."""
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("threadlight_auto_orchestrator", str(ORCH))
    mod = _ilu.module_from_spec(spec)
    sys.modules["threadlight_auto_orchestrator"] = mod
    spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator()


def _leg_envelope(schema: str, status: str) -> str:
    return json.dumps({
        "schema": schema,
        "tool_version": "0.1.0",
        "generated_at": "2026-08-18T10:00:00Z",
        "freshness": {"valid_for_hours": 24, "source_oldest_at": None},
        "status": status,
        "findings": [],
    })


# ---------------------------------------------------------------------------
# Manual-handoff projection (Task 7): the four live legs are advisory only.
# pytest-collected; also exercised from main() below for the standalone runner.
# ---------------------------------------------------------------------------

def test_new_live_legs_are_manual_handoffs_not_auto_stages(tmp_path):
    decision = orch.decide(tmp_path)
    handoffs = decision["manual_handoffs"]
    # Ordered exactly connect -> ground -> loadtest -> upgrade.
    assert [h["skill"] for h in handoffs] == [
        "threadlight-connect",
        "threadlight-ground",
        "threadlight-loadtest",
        "threadlight-upgrade",
    ]
    # An empty workspace has no leg manifests -> every handoff is 'ready'.
    assert all(h["status"] == "ready" for h in handoffs)
    # Each handoff names its skill for a manual, advisory chat invocation.
    for h in handoffs:
        assert h["skill"] in h["next_intent"]
        assert h["manifest"].startswith("specs/")
    # The live legs are NEVER auto-stages.
    assert not {"connect", "ground", "loadtest", "upgrade"}.intersection(decision["stages"])
    assert not {"connect", "ground", "loadtest", "upgrade"}.intersection(orch.STAGES)
    # `stages` echoes the automatic stage runner exactly.
    assert decision["stages"] == list(orch.STAGES)


def test_manual_handoff_status_reflects_validated_envelope(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "connect-manifest.json").write_text(
        _leg_envelope("threadlight-connect-manifest/v1", "complete"), encoding="utf-8")
    (specs / "ground-manifest.json").write_text(
        _leg_envelope("threadlight.ground/v1", "partial"), encoding="utf-8")
    (specs / "load-manifest.json").write_text(
        _leg_envelope("threadlight.load/v1", "aborted"), encoding="utf-8")
    # An unrecognized / malformed manifest must degrade to 'partial', never 'complete'.
    (specs / "upgrade-manifest.json").write_text("{ not valid json", encoding="utf-8")

    decision = orch.decide(tmp_path)
    by_skill = {h["skill"]: h["status"] for h in decision["manual_handoffs"]}
    assert by_skill == {
        "threadlight-connect": "complete",
        "threadlight-ground": "partial",
        "threadlight-loadtest": "aborted",
        "threadlight-upgrade": "partial",
    }


def test_invalid_envelope_never_reports_complete(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    # A dict with status 'complete' but missing required envelope keys is not a
    # valid envelope -> partial, not complete.
    (specs / "connect-manifest.json").write_text(
        json.dumps({"status": "complete"}), encoding="utf-8")
    decision = orch.decide(tmp_path)
    connect = next(h for h in decision["manual_handoffs"] if h["skill"] == "threadlight-connect")
    assert connect["status"] == "partial"


def test_live_legs_never_added_to_stage_runner():
    assert set(orch.MANUAL_HANDOFFS) == {
        "threadlight-connect", "threadlight-ground",
        "threadlight-loadtest", "threadlight-upgrade",
    }
    assert "connect" not in orch.STAGES
    assert "ground" not in orch.STAGES
    assert "loadtest" not in orch.STAGES
    assert "upgrade" not in orch.STAGES
    assert orch.STAGE_PROBES.keys() == set(orch.STAGES)


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


def _standalone_manual_handoff_checks() -> int:
    """Manual-handoff assertions for the standalone runner (no pytest fixtures)."""
    failures = 0
    with tempfile.TemporaryDirectory(prefix="threadlight-handoffs-") as tmp:
        ws = Path(tmp)
        decision = orch.decide(ws)
        order = [h["skill"] for h in decision["manual_handoffs"]]
        expected = [
            "threadlight-connect", "threadlight-ground",
            "threadlight-loadtest", "threadlight-upgrade",
        ]
        if order != expected:
            print(f"❌ manual_handoffs order: expected {expected}, got {order}")
            failures += 1
        elif {"connect", "ground", "loadtest", "upgrade"}.intersection(decision["stages"]):
            print("❌ live legs leaked into stages")
            failures += 1
        else:
            print("✅ manual handoffs ordered + excluded from stages")

        specs = ws / "specs"
        specs.mkdir()
        (specs / "connect-manifest.json").write_text(
            _leg_envelope("threadlight-connect-manifest/v1", "complete"), encoding="utf-8")
        (specs / "load-manifest.json").write_text(
            _leg_envelope("threadlight.load/v1", "aborted"), encoding="utf-8")
        by_skill = {h["skill"]: h["status"] for h in orch.decide(ws)["manual_handoffs"]}
        if by_skill["threadlight-connect"] != "complete" or by_skill["threadlight-loadtest"] != "aborted":
            print(f"❌ manual handoff status mismatch: {by_skill}")
            failures += 1
        else:
            print("✅ manual handoff status reflects envelope")
    return failures


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
        if fixture_name == "all-complete":
            for rel in (
                ".threadlight/preflight-passed.json",
                "docs/safe-check-post.md",
                "docs/invoke-results.md",
            ):
                os.utime(fixture / rel)
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

    # A fresh marker is reusable only while it remains bound to the exact
    # Foundation that passed runtime-policy validation.
    with tempfile.TemporaryDirectory(prefix="threadlight-foundation-created-") as tmp:
        workspace = Path(tmp)
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": "1.0.0", "foundation_sha256": None}), encoding="utf-8")
        foundation = workspace / "specs" / "foundation.md"
        foundation.parent.mkdir(parents=True)
        foundation.write_text("# Foundation\n", encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ foundation-created-after-preflight: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-created-after-preflight: preflight invalidated")

    with tempfile.TemporaryDirectory(prefix="threadlight-legacy-marker-") as tmp:
        workspace = Path(tmp)
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ legacy-marker-without-foundation-hash: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ legacy-marker-without-foundation-hash: preflight invalidated")

    with tempfile.TemporaryDirectory(prefix="threadlight-foundation-matching-") as tmp:
        workspace = Path(tmp)
        foundation = workspace / "specs" / "foundation.md"
        foundation.parent.mkdir(parents=True)
        foundation.write_text("# Foundation\n", encoding="utf-8")
        marker = workspace / ".threadlight" / "preflight-passed.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps({"version": "1.0.0", "foundation_sha256": _m._sha256(foundation)}),
            encoding="utf-8",
        )
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "skip":
            print(f"❌ foundation-hash-matches: expected skip, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-hash-matches: fresh preflight reused")

        foundation.write_text("# Foundation\n\nedited: true\n", encoding="utf-8")
        decision = _m._check_preflight(workspace, {})
        if decision.decision != "run":
            print(f"❌ foundation-edited-after-preflight: expected run, got {decision.decision}")
            failures += 1
        else:
            print("✅ foundation-edited-after-preflight: preflight invalidated")

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

    # --- extra: manual-handoff projection (Task 7) ---
    failures += _standalone_manual_handoff_checks()

    print(f"\n=== {len(cases) + 1 - failures}/{len(cases) + 1} passed ===")
    return failures


if __name__ == "__main__":
    sys.exit(main())
