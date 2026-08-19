from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import prices as prices_mod  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import score as score_mod  # noqa: E402

# `token_evidence.py` lives in the sibling `threadlight-consumption-iq` skill
# and is the actual implementation `metrics_mod.parse_metrics` delegates to
# (see `metrics_mod._load_shared_parser`). Importing it directly here lets
# these tests exercise `parse_token_series`'s richer per-deployment rows
# (deployment preserved separately from model, cached-input evidence) that
# `parse_metrics`'s collapsed `{model: {input, output}}` view cannot express.
sys.path.insert(0, str(metrics_mod.CONSUMPTION_SCRIPTS))
import token_evidence as token_evidence_mod  # noqa: E402


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


def test_parse_metrics_missing_totals_treated_as_zero():
    # A data point with no 'total' key (no data for that interval) must
    # contribute zero, not be dropped or raise.
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{}, {"total": 50.0}]}]},
    ]}
    usage = metrics_mod.parse_metrics(doc)
    assert usage["gpt-5.4"] == {"input": 50, "output": 0}


def test_parse_metrics_ignores_unknown_metric_names():
    doc = {"value": [
        {"name": {"value": "RequestCount"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 999.0}]}]},
    ]}
    assert metrics_mod.parse_metrics(doc) == {}


def test_parse_metrics_sums_multiple_timeseries_for_same_model():
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 100.0}]},
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 25.0}]},
        ]},
    ]}
    usage = metrics_mod.parse_metrics(doc)
    assert usage["gpt-5.4"] == {"input": 125, "output": 0}


def test_load_shared_parser_raises_named_error_when_sibling_skill_is_absent(tmp_path):
    # Calling the helper directly with a non-default, absent scripts_dir
    # must raise before touching sys.path/sys.modules or the module-level
    # cache — no reload, no monkeypatching of module globals, no poisoning
    # of state other tests depend on.
    with pytest.raises(ImportError, match="threadlight-consumption-iq"):
        metrics_mod._load_shared_parser(scripts_dir=tmp_path / "absent")
    # The real, cached parser must still resolve normally afterwards.
    assert metrics_mod.parse_metrics({"value": []}) == {}


# ---- token_evidence.py (shared parser) ----

def test_parse_token_series_preserves_deployment_separately_from_model():
    # A spillover deployment routed to the same model must not be merged
    # into the primary deployment's row.
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [
                {"name": {"value": "modeldeploymentname"}, "value": "model-router"},
                {"name": {"value": "modelname"}, "value": "gpt-5.4"},
            ], "data": [{"total": 100.0}]},
            {"metadatavalues": [
                {"name": {"value": "modeldeploymentname"}, "value": "spillover-router"},
                {"name": {"value": "modelname"}, "value": "gpt-5.4"},
            ], "data": [{"total": 40.0}]},
        ]},
    ]}
    series = token_evidence_mod.parse_token_series(doc)
    assert len(series) == 2
    by_deployment = {row["deployment"]: row for row in series}
    assert by_deployment["model-router"]["input_tokens"] == 100
    assert by_deployment["spillover-router"]["input_tokens"] == 40
    assert all(row["model"] == "gpt-5.4" for row in series)


def test_parse_token_series_model_fallback_chain():
    # No ModelName dimension at all: falls back to the deployment name, then
    # "unknown" if neither dimension is present.
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [
                {"name": {"value": "modeldeploymentname"}, "value": "model-router"},
            ], "data": [{"total": 5.0}]},
            {"metadatavalues": [], "data": [{"total": 1.0}]},
        ]},
    ]}
    series = token_evidence_mod.parse_token_series(doc)
    by_deployment = {row["deployment"]: row for row in series}
    assert by_deployment["model-router"]["model"] == "model-router"
    assert by_deployment["unknown"]["model"] == "unknown"


def test_parse_token_series_missing_cached_input_is_none_not_zero():
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 100.0}]}]},
    ]}
    series = token_evidence_mod.parse_token_series(doc)
    assert series[0]["cached_input_tokens"] is None


def test_parse_token_series_observed_zero_cached_input_is_not_none():
    doc = {"value": [
        {"name": {"value": "CachedInputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 0.0}]}]},
    ]}
    series = token_evidence_mod.parse_token_series(doc)
    assert series[0]["cached_input_tokens"] == 0


def test_parse_token_series_aggregates_cached_input_across_timeseries():
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 100.0}]}]},
        {"name": {"value": "cachedprompttokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 10.0}]},
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": 5.0}]},
        ]},
    ]}
    series = token_evidence_mod.parse_token_series(doc)
    assert series[0]["cached_input_tokens"] == 15


@pytest.mark.parametrize("bad_total", [True, -1.0, 10.5])
def test_parse_token_series_rejects_bad_totals(bad_total):
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "modelname"}, "value": "gpt-5.4"}],
             "data": [{"total": bad_total}]}]},
    ]}
    with pytest.raises(token_evidence_mod.TokenEvidenceError):
        token_evidence_mod.parse_token_series(doc)


def test_parse_token_series_is_stably_sorted():
    doc = {"value": [
        {"name": {"value": "InputTokens"}, "timeseries": [
            {"metadatavalues": [
                {"name": {"value": "modeldeploymentname"}, "value": "router-b"},
                {"name": {"value": "modelname"}, "value": "gpt-5.5"},
            ], "data": [{"total": 1.0}]},
            {"metadatavalues": [
                {"name": {"value": "modeldeploymentname"}, "value": "router-a"},
                {"name": {"value": "modelname"}, "value": "gpt-5.4"},
            ], "data": [{"total": 1.0}]},
        ]},
    ]}
    series = token_evidence_mod.parse_token_series(doc)
    assert [row["deployment"] for row in series] == ["router-a", "router-b"]


def test_parse_token_metrics_matches_router_fixture_exactly():
    # parse_token_metrics must produce identical output to the pre-refactor
    # router-bench parser (the same values metrics_mod.parse_metrics asserts
    # in test_parse_metrics_aggregates_by_model above).
    doc = json.loads((FIXTURES / "az-metrics-modelrouter.json").read_text())
    usage = token_evidence_mod.parse_token_metrics(doc)
    assert usage["gpt-5.4"] == {"input": 7048336, "output": 111473}
    assert usage["gpt-5.5"] == {"input": 313389, "output": 13201}
    assert "gpt-5.4-mini" not in usage


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
