from __future__ import annotations
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import router_bench  # noqa: E402

CAND, BASE = 28437323962, 28435017341
WINDOW = {"startedAt": "2026-06-30T10:21:05Z", "updatedAt": "2026-06-30T11:05:04Z"}
AZ = json.loads((FIXTURES / "az-metrics-modelrouter.json").read_text())


def _gh_runner(args):
    rid = next((a for a in args if a in (str(CAND), str(BASE))), None)
    meta = {"databaseId": int(rid or 0), "conclusion": "success", "headBranch": "topic",
            "displayTitle": "e2e", **WINDOW}
    if "--json" in args:
        return json.dumps(meta)
    raise AssertionError(args)


def _az_runner(args):
    return json.dumps(AZ)


def test_run_bench_writes_scorecard(tmp_path):
    card = router_bench.run_bench(
        CAND, BASE, repo="o/r", outdir=tmp_path,
        resource_id="/subscriptions/x/rg/y", baseline_model="gpt-5.4-mini",
        gh_runner=_gh_runner, az_runner=_az_runner)
    assert card["schema"] == "threadlight-router-scorecard/v1"
    assert card["candidate_cost_usd"] > 0
    assert card["verdict"] in ("router-premium", "router-savings", "neutral")
    assert (tmp_path / f"scorecard-{CAND}-vs-{BASE}.json").exists()
    assert (tmp_path / f"scorecard-{CAND}-vs-{BASE}.md").exists()


def test_cli_bench_returns_zero(tmp_path):
    rc = router_bench.main(
        ["bench", str(CAND), str(BASE), "--repo", "o/r", "--out", str(tmp_path),
         "--resource", "/subscriptions/x/rg/y", "--baseline-model", "gpt-5.4-mini"],
        runner=_gh_runner, az_runner=_az_runner)
    assert rc == 0
    assert (tmp_path / f"scorecard-{CAND}-vs-{BASE}.md").exists()
