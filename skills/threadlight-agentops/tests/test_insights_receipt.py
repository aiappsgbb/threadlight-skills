"""Foundry Insights receipt (#211): offline validation of the receipt block."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "insights_run.py"
FIXTURE = SKILL / "references" / "fixtures" / "insights-receipt.json"
sys.path.insert(0, str(SKILL / "scripts"))
import insights_run  # noqa: E402


def _receipt():
    return json.loads(FIXTURE.read_text())


def test_fixture_receipt_has_the_insights_run_block_and_is_valid():
    receipt = _receipt()
    for key in ("monitor", "run", "cost", "insights", "judge", "traffic_hygiene"):
        assert key in receipt
    assert receipt["run"]["id"] and receipt["monitor"]["id"] and receipt["run"]["traces_analyzed"] > 0
    assert receipt["cost"]["estimated_usd"] == insights_run.estimate_cost(
        receipt["run"]["token_usage"], receipt["cost"]["prices_usd_per_1m"])
    assert receipt["monitor"]["enabled"] is False
    must = [m for s, m in insights_run.validate(receipt) if s == "must-fix"]
    assert must == []
    result = subprocess.run([sys.executable, str(SCRIPT), "validate", "--receipt", str(FIXTURE)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


def test_cost_formula_counts_cached_tokens_at_the_cached_price():
    tokens = {"input_tokens": 1_000_000, "cached_tokens": 400_000, "output_tokens": 100_000}
    prices = {"input": 2.5, "cached": 0.25, "output": 15}
    assert insights_run.estimate_cost(tokens, prices) == pytest.approx(0.6 * 2.5 + 0.4 * 0.25 + 0.1 * 15)


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r["cost"].update(estimated_usd=99.0), "!= formula"),
    (lambda r: r["cost"].pop("price_source"), "price_source"),
    (lambda r: r["cost"].update(label="Actual"), "Estimate"),
    (lambda r: r["monitor"].update(enabled=True), "teardown"),
    (lambda r: r["insights"][0].update(proposed_fix="rewrite instructions"), "untrusted"),
    (lambda r: r["run"].pop("id"), "run.id"),
])
def test_bad_receipts_are_must_fix(mutate, needle, tmp_path):
    receipt = _receipt()
    if not receipt["insights"]:
        receipt["insights"] = [{"id": "i", "severity": "high", "category": "c"}]
    mutate(receipt)
    must = [m for s, m in insights_run.validate(receipt) if s == "must-fix"]
    assert any(needle in m for m in must), must
    path = tmp_path / "r.json"
    path.write_text(json.dumps(receipt))
    assert subprocess.run([sys.executable, str(SCRIPT), "validate", "--receipt", str(path)],
                          capture_output=True).returncode == 2


def test_scheduled_analysis_is_allowed_only_in_production():
    receipt = _receipt()
    receipt["monitor"]["enabled"] = True
    receipt["environment"] = "production"
    assert not [m for s, m in insights_run.validate(receipt) if s == "must-fix"]


def test_judge_sizing_and_shared_deployment_are_flagged():
    receipt = copy.deepcopy(_receipt())
    receipt["judge"]["tpm_capacity"] = 50_000
    receipt["run"]["traces_in_window"] = 294
    receipt["judge"]["deployment"] = receipt["agent_model_deployment"]
    should = [m for s, m in insights_run.validate(receipt) if s == "should-fix"]
    assert any("Tested Nov 2026" in m for m in should)
    assert any("dedicated judge" in m for m in should)


def test_reference_doc_covers_sdk_rbac_sizing_cost_and_teardown():
    doc = (SKILL / "references" / "foundry-insights.md").read_text()
    for needle in ("agent_insight_monitors", "begin_create_run", "Cognitive Services OpenAI User",
                   "Monitoring Reader", "Tested, Nov 2026", "800K TPM", "insights_run.py teardown",
                   "enabled=False", "untrusted", "draft-"):
        assert needle in doc, needle
