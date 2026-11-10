"""Pilot value page: one post-pilot view for the process owner / sponsor.

`scripts/pilot_value.py` only READS evidence other Threadlight steps already
wrote and renders `docs/pilot-value.md`, `docs/pilot-value.html` and the
summary `specs/pilot-value.json`. The contracts pinned here:

* a missing input renders as "Not measured yet", never as an invented number;
* every item carries one evidence kind (measured / calculated / estimate /
  target / not-measured) and a forecast or planning figure is never labelled
  measured — in particular a cost-manifest per-interaction forecast never
  fills the measured cost per successful interaction (KPI-003);
* the measured unit cost comes from the same verified reconciliation reader
  production-ready uses for KPI-003;
* inputs are not modified, and nothing is written outside the three outputs;
* evidence text is escaped in the HTML.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPTS = TEST_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TEST_DIR))

import pilot_value as pv  # noqa: E402
from test_cost_reconciliation import NOW, _write_bundle  # noqa: E402

GENERATED_AT = "2026-11-10T12:00:00+00:00"

SPEC = """# SpecKit: Returns Triage

## 1. Process Overview

**Name**: Returns Triage
**Domain**: Retail
**Description**: A customer-service agent hands the assistant a return id.
The assistant proposes an outcome with the policy clause cited.
**Target Persona**: Operations lead

## 9. Success Criteria

### Business KPIs (BR → KPI mapping)

| BR | KPI Name | Formula (computable from traces) | Target | Alert threshold |
|----|----------|-----------------------------------|--------|-----------------|
| BR-003 | `human_escalation_rate` | `count(escalated) / count(proposals)` | 8–20% | > 25% |
| BR-005 | `policy_citation_rate` | `count(cited) / count(answers)` | 100% | < 95% |

## 10. Trigger & Run Model
"""

KILLER = """# Killer Prompts — Returns Triage

| Rank | Prompt | BR-XXX | Expected anchors | Wow line | Surfaces |
|------|--------|--------|------------------|----------|----------|
| K1 | Triage return RMA-1 and recommend a decision. | BR-001 | RMA-1, approve_refund | "Three systems, one answer, under a minute." | Foundry playground |
| K2 | Triage return RMA-2 — should we refund it? | BR-003 | RMA-2, escalate | "A big refund goes to a supervisor every time." | Foundry playground |
"""

PACKAGE = {
    "schema": "threadlight-foundry-package/v1",
    "generated_by": "threadlight-deploy",
    "agent": {
        "kind": "foundry-hosted",
        "agent_name": "returns-triage",
        "agent_id": "agent-guid",
        "agent_version": "1",
        "fallback_active": False,
    },
    "telemetry": {"app_insights_connection_id": "conn", "trace_id": "0" * 32},
    "evaluation": {
        "eval_run_id": "evalrun_1",
        "target": {"type": "azure_ai_agent", "name": "returns-triage"},
        "builtin_evaluators": ["builtin.task_adherence", "builtin.coherence"],
        "custom_rubric_evaluators": [
            {
                "name": "spec_business_rules_rubric",
                "derived_from_acceptance_criteria": ["S-001", "S-002", "BR-001"],
                "anchors": ["1: wrong", "3: partly", "5: right"],
                "score": 4.222,
                "threshold": 4,
            }
        ],
    },
}

ROI = """# ROI (planning estimate)

> Estimate only. Assumes a fully-loaded agent hour of $45 and a 60% automation rate (both derived assumptions — tune with the customer).

- Current annual cost (user-supplied): $1,200,000
- Projected annual solution cost: $84,000

**Net annual benefit: $1,116,000 → ROI is positive.**

Net = current annual cost − projected annual solution cost.
"""


def _workspace(tmp_path: Path, files: dict[str, str | dict] | None = None) -> Path:
    root = tmp_path / "pilot"
    (root / "specs").mkdir(parents=True)
    (root / "specs" / "SPEC.md").write_text(SPEC, encoding="utf-8")
    for rel, content in (files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content)
        path.write_text(text, encoding="utf-8")
    return root


def _items(report: dict) -> dict[str, dict]:
    return {item["id"]: item for section in report["sections"] for item in section["items"]}


def test_empty_pilot_renders_not_measured_and_invents_nothing(tmp_path):
    root = _workspace(tmp_path)
    report = pv.build_report(root, generated_at=GENERATED_AT)
    items = _items(report)

    for key in ("eval", "foundry-package", "safety-check", "measured-cost",
                "estimated-cost", "business-case"):
        assert items[key]["kind"] == "not-measured", key
        assert items[key]["value"] == "Not measured yet", key
    assert report["counts"]["measured"] == 0
    assert report["counts"]["calculated"] == 0
    assert report["counts"]["estimate"] == 0

    markdown = pv.render_markdown(report)
    html = pv.render_html(report)
    assert "Not measured yet" in markdown and "Not measured yet" in html
    assert "$" not in markdown.split("## Sources")[0], "no money figure without evidence"


def test_business_kpis_are_targets_not_measurements(tmp_path):
    report = pv.build_report(_workspace(tmp_path), generated_at=GENERATED_AT)
    kpis = [item for item in _items(report).values() if item["id"].startswith("kpi-")]
    assert [k["label"] for k in kpis] == ["Human escalation rate", "Policy citation rate"]
    for kpi in kpis:
        assert kpi["kind"] == "target"
        assert kpi["measured"] is None
    assert kpis[0]["value"] == "8–20%"
    assert "Not measured yet" in pv.render_markdown(report)


def test_foundry_eval_score_is_measured_against_its_bar(tmp_path):
    root = _workspace(tmp_path, {"specs/foundry-package-manifest.json": PACKAGE})
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert items["eval"]["kind"] == "measured"
    assert items["eval"]["status"] == "good"
    assert "4.2 out of 5" in items["eval"]["value"]
    assert "bar of 4" in items["eval"]["detail"]
    assert items["foundry-package"]["kind"] == "measured"
    assert items["foundry-package"]["status"] == "good"


def test_eval_below_bar_is_flagged(tmp_path):
    package = json.loads(json.dumps(PACKAGE))
    package["evaluation"]["custom_rubric_evaluators"][0]["score"] = 3.1
    root = _workspace(tmp_path)
    (root / "specs" / "foundry-package-manifest.json").write_text(json.dumps(package))
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert items["eval"]["kind"] == "measured"
    assert items["eval"]["status"] == "attention"
    assert items["foundry-package"]["status"] != "good"


def test_killer_prompts_without_recorded_run_are_not_measured(tmp_path):
    root = _workspace(tmp_path, {"tests/killer-prompts.md": KILLER})
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert items["scenario-K1"]["kind"] == "not-measured"
    assert items["scenario-K1"]["value"] == "Not run against the live agent yet"
    assert items["scenario-K1"]["detail"] == "Three systems, one answer, under a minute."


def test_killer_prompt_outcomes_come_from_invoke_results(tmp_path):
    root = _workspace(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "killer-prompts.md").write_text(KILLER)
    (root / "docs").mkdir()
    (root / "docs" / "invoke-results.md").write_text(
        "# Invoke results\n\n| Rank | Result | Notes |\n|---|---|---|\n"
        "| K1 | PASS | cited RP-1 |\n| K2 | FAIL | no escalation |\n")
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert (items["scenario-K1"]["kind"], items["scenario-K1"]["value"]) == ("measured", "Passed")
    assert (items["scenario-K2"]["kind"], items["scenario-K2"]["value"]) == ("measured", "Failed")
    assert items["scenario-K2"]["status"] == "attention"


def test_invoke_results_without_per_scenario_result_is_not_a_pass(tmp_path):
    root = _workspace(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "killer-prompts.md").write_text(KILLER)
    (root / "docs").mkdir()
    (root / "docs" / "invoke-results.md").write_text("# Invoke results\n\nRan the demo.\n")
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert items["scenario-K1"]["kind"] == "not-measured"
    assert "not stated" in items["scenario-K1"]["value"]


def test_safety_check_pending_deploy_is_not_measured(tmp_path):
    root = _workspace(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "postdeploy-manifest.json").write_text(json.dumps(
        {"schema": "threadlight-postdeploy-manifest/v1", "phase": "pending-deploy", "gaps": []}))
    assert _items(pv.build_report(root, generated_at=GENERATED_AT))["safety-check"]["kind"] == "not-measured"

    (root / "tests" / "postdeploy-manifest.json").write_text(json.dumps(
        {"schema": "threadlight-postdeploy-manifest/v1", "phase": "post-deploy", "gaps": [],
         "checked_at": "2026-11-10T09:00:00Z"}))
    item = _items(pv.build_report(root, generated_at=GENERATED_AT))["safety-check"]
    assert (item["kind"], item["status"]) == ("measured", "good")


def test_cost_forecast_is_an_estimate_and_never_fills_measured_cost(tmp_path):
    root = _workspace(tmp_path)
    (root / "specs" / "cost-manifest.json").write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at": "2026-11-09T00:00:00Z",
        "totals": {"complete": True, "monthly_cost_current_usd": 412.5,
                   "cost_per_transaction_usd": 0.021},
    }))
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert items["estimated-cost"]["kind"] == "estimate"
    assert "$412.50" in items["estimated-cost"]["value"]
    assert items["measured-cost"]["kind"] == "not-measured"
    assert "0.021" not in items["measured-cost"]["value"]


def test_partial_cost_forecast_says_so(tmp_path):
    root = _workspace(tmp_path)
    (root / "specs" / "cost-manifest.json").write_text(json.dumps({
        "schema_version": "1.0", "generated_at": "2026-11-09T00:00:00Z",
        "totals": {"complete": False, "monthly_cost_known_usd": 90.0}}))
    item = _items(pv.build_report(root, generated_at=GENERATED_AT))["estimated-cost"]
    assert item["kind"] == "estimate"
    assert "partial" in item["value"].lower()


def test_measured_cost_per_successful_interaction_uses_verified_reconciliation(tmp_path, monkeypatch):
    monkeypatch.setattr(pv.production_ready_module(), "_cost_reconciliation_now", lambda: NOW)
    root = tmp_path / "pilot"
    root.mkdir()
    _write_bundle(root)
    (root / "specs" / "manifest.json").write_text(json.dumps(
        {"deployment_manifest": {"subscription_id": "sub-1", "resource_group": "rg-pilot"}}))
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    measured = items["measured-cost"]
    assert measured["kind"] == "measured"
    assert measured["value"] == "$0.1083 per successful interaction"
    assert "1,200 successful interactions" in measured["detail"]
    assert items["cost-target"]["kind"] == "target"
    assert items["cost-target"]["status"] == "good"
    assert items["cost-target"]["measured"] == "$0.1083 per successful interaction"
    assert items["measured-spend"]["kind"] == "measured"
    assert items["run-rate"]["kind"] == "calculated"


def test_tampered_reconciliation_is_not_relayed(tmp_path, monkeypatch):
    monkeypatch.setattr(pv.production_ready_module(), "_cost_reconciliation_now", lambda: NOW)
    root = tmp_path / "pilot"
    root.mkdir()
    _write_bundle(root)
    path = root / "specs" / "cost-reconciliation-manifest.json"
    doc = json.loads(path.read_text())
    doc["unit_economics"]["cost_per_successful_interaction_usd"] = 0.0001
    path.write_text(json.dumps(doc))
    (root / "specs" / "manifest.json").write_text(json.dumps(
        {"deployment_manifest": {"subscription_id": "sub-1", "resource_group": "rg-pilot"}}))
    items = _items(pv.build_report(root, generated_at=GENERATED_AT))
    assert items["measured-cost"]["kind"] == "not-measured"
    assert "0.0001" not in json.dumps(items)


def test_roi_is_a_planning_estimate(tmp_path):
    root = _workspace(tmp_path)
    (root / "qualification").mkdir()
    (root / "qualification" / "roi.md").write_text(ROI)
    item = _items(pv.build_report(root, generated_at=GENERATED_AT))["business-case"]
    assert item["kind"] == "estimate"
    assert "$1,116,000" in item["value"]
    assert "$1,200,000" in item["detail"] and "$84,000" in item["detail"]


def test_report_counts_and_summary_json(tmp_path):
    root = _workspace(tmp_path)
    (root / "specs" / "foundry-package-manifest.json").write_text(json.dumps(PACKAGE))
    paths = pv.write_report(root, generated_at=GENERATED_AT)
    summary = json.loads((root / "specs" / "pilot-value.json").read_text())
    assert summary["schema"] == "threadlight-pilot-value/v1"
    assert summary["generated_at"] == GENERATED_AT
    assert summary["counts"]["measured"] >= 2
    assert summary["page"] == "docs/pilot-value.html"
    assert sorted(str(p.relative_to(root)) for p in paths) == [
        "docs/pilot-value.html", "docs/pilot-value.md", "specs/pilot-value.json"]


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_reads_only_and_writes_only_its_outputs(tmp_path):
    root = _workspace(tmp_path, {"tests/killer-prompts.md": KILLER,
                                 "specs/foundry-package-manifest.json": PACKAGE})
    before = _digest_tree(root)
    pv.write_report(root, generated_at=GENERATED_AT)
    after = _digest_tree(root)
    for path, digest in before.items():
        assert after[path] == digest, f"input changed: {path}"
    assert set(after) - set(before) == {
        "docs/pilot-value.md", "docs/pilot-value.html", "specs/pilot-value.json"}


def test_output_is_deterministic(tmp_path):
    root = _workspace(tmp_path)
    pv.write_report(root, generated_at=GENERATED_AT)
    first = _digest_tree(root)
    pv.write_report(root, generated_at=GENERATED_AT)
    assert _digest_tree(root) == first


def test_html_escapes_evidence_text(tmp_path):
    root = _workspace(tmp_path)
    (root / "specs" / "SPEC.md").write_text(
        SPEC.replace("**Name**: Returns Triage", "**Name**: <script>alert(1)</script>"))
    html = pv.render_html(pv.build_report(root, generated_at=GENERATED_AT))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<script" not in html.lower(), "the page ships no script"


def test_html_is_self_contained_and_labels_every_kind(tmp_path):
    html = pv.render_html(pv.build_report(_workspace(tmp_path), generated_at=GENERATED_AT))
    assert "http://" not in html and "https://" not in html.replace(
        'xmlns="http://www.w3.org/2000/svg"', "")
    for label in ("Measured", "Calculated", "Estimate", "Target", "Not measured yet"):
        assert label in html


def test_cli_writes_report(tmp_path):
    root = _workspace(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "pilot_value.py"), "--workspace", str(root),
         "--generated-at", GENERATED_AT],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (root / "docs" / "pilot-value.html").exists()
    assert "docs/pilot-value.html" in result.stdout


def test_cli_rejects_missing_workspace(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "pilot_value.py"), "--workspace", str(tmp_path / "nope")],
        capture_output=True, text=True, check=False)
    assert result.returncode == 2


def test_description_keeps_bold_continuation_lines(tmp_path):
    root = _workspace(tmp_path)
    (root / "specs" / "SPEC.md").write_text(SPEC.replace(
        "The assistant proposes an outcome", "The assistant\n**proposes** an outcome"))
    pilot = pv.build_report(root, generated_at=GENERATED_AT)["pilot"]
    assert pilot["description"].endswith("with the policy clause cited.")
    assert "**" not in pilot["description"]


def test_kpi_labels_read_as_plain_words():
    assert pv._humanize_metric("`recommendation_acceptance_pct`") == "Recommendation acceptance (%)"
    assert pv._humanize_metric("`return_cycle_time_business_days_p50`") == "Return cycle time business days (median)"


def test_huge_integers_never_crash_the_page(tmp_path):
    root = _workspace(tmp_path)
    huge = "1" + "0" * 400
    (root / "specs" / "cost-manifest.json").write_text(
        '{"totals": {"complete": true, "monthly_cost_current_usd": ' + huge + "}}")
    item = _items(pv.build_report(root, generated_at=GENERATED_AT))["estimated-cost"]
    assert item["kind"] == "estimate"
    assert item["value"] == "Partial estimate"


def test_invoke_verdict_binds_to_its_own_scenario_only():
    assert pv._invoke_outcomes("Ran K1 and K3: K1 PASS, K3 skipped (tool timeout)") == {"K1": "pass"}
    assert pv._invoke_outcomes("| K2 | The agent did not pass the refund rule |") == {}
    assert pv._invoke_outcomes("| K4 | FAIL | no escalation |") == {"K4": "fail"}
    assert pv._invoke_outcomes("- K5: ✅ cited RP-3") == {"K5": "pass"}


def test_eval_score_needs_a_valid_delivery_record(tmp_path):
    for mutate in (
        lambda m: m.update(schema="wrong/v1"),
        lambda m: m["agent"].update(fallback_active=True),
        lambda m: m["evaluation"]["target"].update(name="other-agent"),
    ):
        package = json.loads(json.dumps(PACKAGE))
        mutate(package)
        root = _workspace(tmp_path / str(id(mutate)), {"specs/foundry-package-manifest.json": package})
        item = _items(pv.build_report(root, generated_at=GENERATED_AT))["eval"]
        assert item["kind"] == "not-measured", package


def test_eval_takes_the_weakest_rubric(tmp_path):
    package = json.loads(json.dumps(PACKAGE))
    second = dict(package["evaluation"]["custom_rubric_evaluators"][0], name="tone", score=2.0)
    package["evaluation"]["custom_rubric_evaluators"].append(second)
    root = _workspace(tmp_path, {"specs/foundry-package-manifest.json": package})
    item = _items(pv.build_report(root, generated_at=GENERATED_AT))["eval"]
    assert item["status"] == "attention"
    assert "below" in item["detail"]


def test_readiness_wording_follows_every_recommendation(tmp_path):
    root = _workspace(tmp_path)
    (root / "tests").mkdir()
    path = root / "tests" / "production-readiness-manifest.json"
    expected = {
        "ready": ("Review recommends going live", "good"),
        "ready_with_waivers": ("Review recommends going live with waivers", "attention"),
        "ready_with_residual_risk": ("Review recommends going live with residual risk", "attention"),
        "ready_with_unverified_risk": ("Review recommends going live with unverified risk", "attention"),
        "not_ready": ("Not ready yet", "attention"),
    }
    for recommendation, (value, status) in expected.items():
        path.write_text(json.dumps({"go_live_recommendation": recommendation,
                                    "checked_at": "2026-11-01T00:00:00Z"}))
        item = _items(pv.build_report(root, generated_at=GENERATED_AT))["readiness"]
        assert (item["value"], item["status"], item["as_of"]) == (value, status, "1 Nov 2026"), recommendation


def test_business_kpis_skip_the_template_exemplar_table(tmp_path):
    exemplar = (
        "### Business KPIs (BR → KPI mapping)\n\n"
        "| Industry | Example KPI (good) | Example KPI (bad) |\n|---|---|---|\n"
        "| Retail | return cycle time | tokens used |\n\n"
        "| BR | KPI Name | Formula (computable from traces) | Target | Alert threshold |\n"
    )
    root = _workspace(tmp_path)
    spec = SPEC.replace(
        "### Business KPIs (BR → KPI mapping)\n\n| BR | KPI Name | Formula (computable from traces) | Target | Alert threshold |\n",
        exemplar)
    assert spec != SPEC
    (root / "specs" / "SPEC.md").write_text(spec)
    kpis = [i for i in _items(pv.build_report(root, generated_at=GENERATED_AT)).values()
            if i["id"].startswith("kpi-")]
    assert [k["label"] for k in kpis] == ["Human escalation rate", "Policy citation rate"]


def test_window_days_tolerates_mixed_timezones():
    assert pv._window_days("2026-08-01T00:00:00", "2026-08-08T00:00:00Z") is None
