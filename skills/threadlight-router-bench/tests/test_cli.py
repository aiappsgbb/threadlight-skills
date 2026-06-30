from __future__ import annotations
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import router_bench  # noqa: E402

RUN = 28435017341
META = {"databaseId": RUN, "conclusion": "failure", "headBranch": "topic",
        "displayTitle": "router e2e", "status": "completed",
        "startedAt": "2026-06-30T10:21:05Z", "updatedAt": "2026-06-30T11:05:04Z"}
JOBS = {"jobs": [{"name": "e2e", "conclusion": "failure", "steps": [
    {"name": "Install threadlight_quickstart [aoai]", "conclusion": "failure", "number": 1},
    {"name": "[Phase 1/4] design", "conclusion": "skipped", "number": 2},
]}]}
# two real-shaped failure lines: a dependency resolution + a rate limit
LOG = ("e2e\tInstall\t2026-06-30T10:22:00Z ERROR: ResolutionImpossible: agent-framework\n"
       "e2e\tInvoke\t2026-06-30T10:30:00Z CAPIError: exceeded rate limit, retry after 60s\n")


def _fake_runner(args):
    if "--json" in args and "jobs" in args:
        return json.dumps(JOBS)
    if "--json" in args:
        return json.dumps(META)
    if "--log-failed" in args or "--log" in args:
        return LOG
    raise AssertionError(f"unexpected gh args: {args}")


def test_run_learn_end_to_end(tmp_path):
    digest = router_bench.run_learn(RUN, repo="o/r", outdir=tmp_path,
                                    model_deployment="model-router",
                                    runner=_fake_runner)
    assert digest["run_id"] == RUN
    assert digest["conclusion"] == "failure"
    cats = {f["category"] for f in digest["findings"]}
    assert "dependency" in cats and "rate_limit" in cats
    assert digest["phase_parity"]["install"] == "failure"
    assert digest["window"]["start"] == "2026-06-30T10:21:05Z"
    # artifacts written
    assert (tmp_path / f"learnings-{RUN}.json").exists()
    assert (tmp_path / f"learnings-{RUN}.md").exists()
    written = json.loads((tmp_path / f"learnings-{RUN}.json").read_text())
    assert written["schema"] == "threadlight-router-learnings/v1"


def test_cli_learn_writes_outputs(tmp_path, capsys):
    rc = router_bench.main(["learn", str(RUN), "--repo", "o/r",
                            "--out", str(tmp_path), "--deployment", "model-router"],
                           runner=_fake_runner)
    assert rc == 0
    assert (tmp_path / f"learnings-{RUN}.md").exists()


def test_cli_unknown_command_errors():
    assert router_bench.main(["bogus"], runner=_fake_runner) == 2


def test_validate_ingest_builds_scorecard(tmp_path, monkeypatch):
    import router_bench, json
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"cells": [
        {"arm": "mini", "workload": "returns-triage", "run_id": 1},
        {"arm": "strong", "workload": "returns-triage", "run_id": 2},
    ]}), encoding="utf-8")

    # Stub the per-cell scorer so the test stays offline.
    monkeypatch.setattr(router_bench, "_score_cell", lambda cell, **k: {
        "arm": cell["arm"], "phases_ok": True, "rounds": 100,
        "rubric": 0.9, "cost_usd": 1.0})
    rc = router_bench.main(["validate", "--ingest", str(manifest),
                            "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "router-validation.md").exists()
    import json as _json
    cards = _json.loads((tmp_path / "router-validation.json").read_text(encoding="utf-8"))
    assert isinstance(cards, list) and cards
    assert cards[0]["workload"] == "returns-triage"
    assert "mini" in cards[0]["arms"]


def test_score_cell_orchestration_offline(monkeypatch, tmp_path):
    import router_bench, harvest
    monkeypatch.setattr(harvest, "fetch_jobs", lambda run_id, repo, runner=None: {})
    monkeypatch.setattr(harvest, "parse_phase_parity",
                        lambda jobs: {"phase-1": "success", "phase-2": "skipped"})
    monkeypatch.setattr(harvest, "download_run", lambda run_id, bundle, runner=None: None)
    monkeypatch.setattr(harvest, "count_rounds", lambda logs: {"total": 3})
    monkeypatch.setattr(harvest, "find_specs_dir", lambda bundle: None)
    out = router_bench._score_cell(
        {"arm": "mini", "run_id": 7, "_rubric": {"checks": []}},
        resource_id=None)
    assert out == {"arm": "mini", "phases_ok": True, "rounds": 3,
                   "rubric": 0.0, "cost_usd": 0.0}
