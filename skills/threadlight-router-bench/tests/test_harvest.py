from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import json  # noqa: E402
import harvest  # noqa: E402


def test_parse_phase_parity_from_real_green_jobs():
    doc = json.loads((FIXTURES / "gh-run-jobs.json").read_text())
    phases = harvest.parse_phase_parity(doc)
    for name in ["smoke", "design", "pattern", "deploy", "invoke", "legs"]:
        assert phases.get(name) == "success", f"{name} -> {phases.get(name)}"


def test_phase_worst_conclusion_wins():
    doc = {"jobs": [{"name": "e2e", "conclusion": "failure", "steps": [
        {"name": "[Phase 1/4] Drive design", "conclusion": "success", "number": 1},
        {"name": "[Phase 1/4 assert] artifacts", "conclusion": "failure", "number": 2},
    ]}]}
    assert harvest.parse_phase_parity(doc)["design"] == "failure"


def test_failed_phase_marks_downstream_skipped():
    # mirrors real failure 28435017341: design fails, later phases skipped
    doc = {"jobs": [{"name": "e2e", "steps": [
        {"name": "[Phase 1/4] design", "conclusion": "failure", "number": 1},
        {"name": "[Phase 2/4] pattern", "conclusion": "skipped", "number": 2},
        {"name": "[Phase 3/4] deploy", "conclusion": "skipped", "number": 3},
    ]}]}
    p = harvest.parse_phase_parity(doc)
    assert p["design"] == "failure" and p["pattern"] == "skipped"


def test_load_leg_manifests_real():
    legs = harvest.load_leg_manifests(FIXTURES / "legs")
    assert legs["evals"]["schema"] == "threadlight-evals-manifest/v1"
    assert legs["govern"]["verdict"] == "not-wired"
    assert legs["redteam"]["verdict"] == "vulnerable"


def test_load_leg_manifests_missing_dir_returns_empty():
    legs = harvest.load_leg_manifests(FIXTURES / "does-not-exist")
    assert legs == {"govern": {}, "evals": {}, "redteam": {}}


def test_fetch_logs_uses_failed_flag_for_failures():
    calls = []

    def fake_runner(args):
        calls.append(args)
        return "log-body"

    body = harvest.fetch_logs(28435017341, conclusion="failure", repo="o/r", runner=fake_runner)
    assert body == "log-body"
    assert "--log-failed" in calls[0]


def test_fetch_logs_uses_full_log_for_success():
    calls = []
    harvest.fetch_logs(1, conclusion="success", repo="o/r",
                       runner=lambda a: (calls.append(a) or "x"))
    assert "--log" in calls[0] and "--log-failed" not in calls[0]
