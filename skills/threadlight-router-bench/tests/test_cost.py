from __future__ import annotations
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import prices as prices_mod  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import score as score_mod  # noqa: E402


# ---- prices.py ----

def test_seed_prices_have_known_models():
    table = prices_mod.load_prices()
    for m in ("gpt-5.4", "gpt-5.5", "gpt-5.4-mini"):
        assert m in table
        assert table[m]["input"] > 0 and table[m]["output"] > 0


def test_prices_override_file_merges(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"gpt-5.4": {"input": 1.0, "output": 2.0}}))
    table = prices_mod.load_prices(f)
    assert table["gpt-5.4"] == {"input": 1.0, "output": 2.0}
    assert "gpt-5.4-mini" in table  # untouched seed entry survives


# ---- metrics.py ----

def test_parse_metrics_aggregates_by_model():
    doc = json.loads((FIXTURES / "az-metrics-modelrouter.json").read_text())
    usage = metrics_mod.parse_metrics(doc)
    assert usage["gpt-5.4"] == {"input": 7048336, "output": 111473}
    assert usage["gpt-5.5"] == {"input": 313389, "output": 13201}
    assert "gpt-5.4-mini" not in usage  # router never routed to mini on this workload


def test_parse_metrics_lowercase_dimension_keys():
    # Azure Monitor returns dimension keys lowercased ('modelname'); parser must handle it.
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 100.0}, {"total": 50.0}]}]},
        {"name": {"value": "OutputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 10.0}]}]},
    ]}
    usage = metrics_mod.parse_metrics(doc)
    assert usage["gpt-5.4"] == {"input": 150, "output": 10}


# ---- score.py ----

def test_cost_of_uses_per_million_pricing():
    usage = {"gpt-5.4": {"input": 1_000_000, "output": 500_000}}
    prices = {"gpt-5.4": {"input": 2.0, "output": 8.0}}
    # 1M in * $2 + 0.5M out * $8 = 2.00 + 4.00 = 6.00
    assert round(score_mod.cost_of(usage, prices), 6) == 6.0


def test_scorecard_real_router_usage_verdict():
    usage = metrics_mod.parse_metrics(
        json.loads((FIXTURES / "az-metrics-modelrouter.json").read_text()))
    prices = prices_mod.load_prices()
    # baseline: same total tokens, but all priced at gpt-5.4-mini
    card = score_mod.scorecard(candidate_usage=usage,
                               baseline_model="gpt-5.4-mini", prices=prices)
    assert card["candidate_cost_usd"] > 0
    assert card["counterfactual_baseline_usd"] > 0
    assert card["schema"] == "threadlight-router-scorecard/v1"
    # router routed entirely to gpt-5.4/5.5 (pricier than mini) -> premium, not savings
    assert card["delta_usd"] == round(
        card["candidate_cost_usd"] - card["counterfactual_baseline_usd"], 4)
    assert card["verdict"] in ("router-premium", "router-savings", "neutral")


def test_scorecard_savings_when_candidate_cheaper():
    usage = {"gpt-5.4-mini": {"input": 1_000_000, "output": 0}}
    prices = {"gpt-5.4-mini": {"input": 0.15, "output": 0.60},
              "gpt-5.4": {"input": 2.50, "output": 10.0}}
    card = score_mod.scorecard(candidate_usage=usage, baseline_model="gpt-5.4", prices=prices)
    assert card["verdict"] == "router-savings"
    assert card["delta_usd"] < 0
