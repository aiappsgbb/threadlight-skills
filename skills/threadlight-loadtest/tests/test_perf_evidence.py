"""Perf evidence: one evidence model feeds the Markdown and HTML report, labels stay distinct."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hosted_agent  # noqa: E402
import perf_evidence  # noqa: E402
import waterfall  # noqa: E402
from test_waterfall import _trace  # noqa: E402

PROMPT = "Triage RMA-2026-000451 for Jane Example"


def _evidence():
    counter = iter(range(1000))

    def invoke(prompt, session_id):
        return {"client_ms": 10000.0 + next(counter), "status": 200, "error_class": None,
                "invocation_id": f"inv{session_id[-6:]}{next(counter)}", "first_token_ms": 5000.0}

    runner = hosted_agent.PhaseRunner(invoke=invoke, prompts=[PROMPT], run_id="r1")
    phases = hosted_agent.default_phases(max_concurrency=2, steady_requests=6, cold_requests=2, ramp_steps=(1,),
                                         ramp_requests=2)
    records = runner.run(phases)
    spans = [s for r in records for s in _trace(r.invocation_id, cold_client=r.cold_phase)]
    summary = waterfall.summarize([r.as_dict() for r in records], spans)
    manifest = {"status": "complete", "findings": [{"id": "LOAD-001", "status": "pass"}],
                "budget": {"ceiling_usd": 5, "projected_usd": 1, "within_ceiling": True}}
    return perf_evidence.build_evidence(
        generated_at="2026-11-11T12:00:00+00:00", agent="returns-triage", region="eastus2",
        profile={"name": "p"}, phases=phases, runner=runner, summary=summary, manifest=manifest,
        model={"read": True, "model": "gpt-5.4", "version": "v", "sku": "GlobalStandard", "capacity": 300,
               "tpm": 300000.0, "rpm": 3000.0},
        mcp_before={"read": True, "min_replicas": 0, "max_replicas": 2, "replicas_now": 0},
        mcp_after={"read": True, "replicas_now": 1},
        prices={"usd_per_1m": {"input": 2.5, "cached_input": 0.25, "output": 15.0}, "region": "eastus2"},
        compute_prices={"vcpu_hour": 0.0994, "gib_hour": 0.0118}, cpu=1, memory_gib=2, idle_timeout_minutes=15)


def test_evidence_keeps_measured_calculated_estimated_and_documented_apart():
    ev = _evidence()
    assert ev["schema"] == "threadlight-perf-evidence/v1"
    kinds = {f["label"]: f["kind"] for f in ev["capacity"]}
    assert kinds["Model rate limit (tokens per minute)"] == "observed"
    assert kinds["Highest concurrency tested"] == "measured"
    assert kinds["Steady phase: model calls per minute / deployment RPM limit"] == "calculated"
    assert any(k == "documented" for k in kinds.values())
    assert ev["cost"]["model_per_1000"]["kind"] == "calculated"
    assert ev["cost"]["compute_per_1000"]["kind"] == "estimate"
    assert {p["phase"] for p in ev["phases"]} == {"cold", "ramp-1", "steady"}
    assert ev["waterfall"]["groups"]["cold"]["segments"]["cold_start_ms"]["n"] == 2


def test_reports_show_labels_separability_and_never_prompts(tmp_path):
    ev = _evidence()
    paths = perf_evidence.write_outputs(tmp_path, ev)
    md = (tmp_path / perf_evidence.REPORT_MD).read_text()
    page = (tmp_path / perf_evidence.REPORT_HTML).read_text()
    for text in (md, page):
        assert "not separated" in text and "Estimate" in text and "Calculated" in text and "Measured" in text
        assert "Jane Example" not in text and "RMA-2026-000451" not in text
    assert "learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions" in page
    assert "<script" not in page
    saved = json.loads((tmp_path / perf_evidence.EVIDENCE).read_text())
    assert "Jane Example" not in json.dumps(saved)
    assert len(paths) == 3


def test_render_only_rebuilds_the_report_from_saved_evidence(tmp_path):
    perf_evidence.write_outputs(tmp_path, _evidence())
    (tmp_path / perf_evidence.REPORT_MD).unlink()
    assert perf_evidence.main(["--workspace", str(tmp_path), "--render-only"]) == 0
    assert (tmp_path / perf_evidence.REPORT_MD).is_file()


def test_wait_and_join_stops_when_every_successful_request_has_spans():
    records = [hosted_agent.RequestRecord("s", False, 0, "x", "t", 1.0, 200, None, invocation_id=f"i{n}")
               for n in range(3)]
    calls = []

    def fetch(ids):
        calls.append(ids)
        seen = ids[: len(calls)]
        return [{"invocation_id": i, "name": waterfall.TURN} for i in seen]

    spans = perf_evidence.wait_and_join(fetch, records, wait_s=600, poll_s=1, sleep=lambda s: None)
    assert len(calls) == 3 and len(spans) == 3


def test_from_raw_rebuilds_evidence_and_reports_model_429s_from_azure_monitor():
    runner = hosted_agent.PhaseRunner(
        invoke=lambda p, s: {"client_ms": 9000.0, "status": 200, "error_class": None, "invocation_id": s[-8:]},
        prompts=[PROMPT], run_id="r2")
    phases = [{"name": "steady", "concurrency": 1, "requests": 2}]
    records = runner.run(phases)
    raw = {"records": [r.as_dict() for r in records], "phase_windows": runner.phase_windows,
           "spans": [s for r in records for s in _trace(r.invocation_id)],
           "inputs": {"agent": "a", "region": "eastus2", "profile": {"name": "p"}, "phases": phases, "run_id": "r2",
                      "prompts": 1, "manifest": {"status": "complete", "findings": [], "budget": {}},
                      "model": {"read": True, "tpm": 300000.0, "rpm": 3000.0}, "mcp_before": {}, "mcp_after": {},
                      "prices": {"usd_per_1m": {"input": 2.5, "output": 15.0}}, "compute_prices": {},
                      "cpu": 1, "memory_gib": 2, "idle_timeout_minutes": 15,
                      "account_id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a"}}
    metric = {"value": [{"timeseries": [
        {"metadatavalues": [{"name": {"value": "statuscode"}, "value": "429"}], "data": [{"total": 7}, {"total": 5}]},
        {"metadatavalues": [{"name": {"value": "statuscode"}, "value": "200"}], "data": [{"total": 30}]}]}]}
    seen = []

    def run(argv):
        seen.append(argv)
        return json.dumps(metric)

    ev = perf_evidence.evidence_from_raw(raw, run)
    assert ev["model_status"]["by_status"] == {"429": 12, "200": 30}
    assert "AzureOpenAIRequests" in seen[0]
    assert "12 model calls answered HTTP 429" in perf_evidence.render_markdown(ev)


def test_cost_divides_by_joined_successful_requests_and_breakdown_is_calculated():
    ev = _evidence()
    assert ev["cost"]["model_per_1000"]["formula"].endswith(
        f"/ {ev['waterfall']['successful_joined']} requests x 1,000")
    answers = {q: k for q, _, k in perf_evidence._short_answers(ev)}
    assert answers["Where does the time go (warm, mean)?"] == "calculated"


def test_records_survive_a_failed_telemetry_fetch(tmp_path):
    runner = hosted_agent.PhaseRunner(
        invoke=lambda p, s: {"client_ms": 5.0, "status": 200, "error_class": None, "invocation_id": "i1"},
        prompts=[PROMPT])
    records = runner.run([{"name": "s", "concurrency": 1, "requests": 1}])
    perf_evidence.save_raw(tmp_path, records, runner.phase_windows, spans=None, inputs={"agent": "a"})
    raw = json.loads((tmp_path / perf_evidence.RAW).read_text())
    assert raw["records"][0]["invocation_id"] == "i1" and raw["spans"] is None
    assert PROMPT not in json.dumps(raw)
