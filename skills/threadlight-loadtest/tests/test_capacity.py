"""Capacity facts and cost: ARM reads, Retail Prices meters, calculated vs estimated cost."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import capacity  # noqa: E402

DEPLOYMENT = {"sku": {"name": "GlobalStandard", "capacity": 300},
              "properties": {"model": {"name": "gpt-5.4", "version": "2026-03-05"},
                             "rateLimits": [{"key": "request", "renewalPeriod": 10, "count": 500},
                                            {"key": "token", "renewalPeriod": 60, "count": 300000}]}}


def test_model_deployment_reads_rate_limits_per_minute():
    out = capacity.model_deployment(lambda argv: json.dumps(DEPLOYMENT), resource_group="rg", account="a",
                                    deployment="gpt-5.4")
    assert out == {"read": True, "sku": "GlobalStandard", "capacity": 300, "model": "gpt-5.4",
                   "version": "2026-03-05", "tpm": 300000.0, "rpm": 3000.0}


def test_unreadable_facts_are_marked_not_read():
    def fail(argv):
        raise RuntimeError("az not logged in")

    assert capacity.model_deployment(fail, resource_group="rg", account="a", deployment="d")["read"] is False
    assert capacity.container_app(fail, resource_group="rg", name="n")["replicas_now"] is None


def _prices_page(url):
    assert "Foundry+Models" in url or "Foundry%20Models" in url
    return {"Items": [
        {"skuName": "5.4 inp Gl", "meterName": "5.4 inp Gl 1M Tokens", "retailPrice": 2.5, "unitOfMeasure": "1M"},
        {"skuName": "5.4 cd inp Gl", "meterName": "5.4 cd inp Gl 1M Tokens", "retailPrice": 0.25, "unitOfMeasure": "1M"},
        {"skuName": "5.4 opt Gl", "meterName": "5.4 opt Gl 1M Tokens", "retailPrice": 15.0, "unitOfMeasure": "1M"},
        {"skuName": "5.4 pp opt Gl", "meterName": "5.4 pp opt Gl 1M Tokens", "retailPrice": 30.0, "unitOfMeasure": "1M"},
        {"skuName": "5.4 mini Opt Gl", "meterName": "x", "retailPrice": 4.5, "unitOfMeasure": "1M"},
    ], "NextPageLink": None}


def test_model_prices_pick_exact_global_meters_only():
    out = capacity.model_prices("gpt-5.4", "eastus2", get=_prices_page)
    assert out["usd_per_1m"] == {"input": 2.5, "cached_input": 0.25, "output": 15.0}


def test_cost_per_1000_is_calculated_from_measured_tokens_with_cached_input():
    prices = {"usd_per_1m": {"input": 2.5, "cached_input": 0.25, "output": 15.0}}
    tokens = {"input_tokens": 1_000_000, "cached_input_tokens": 600_000, "output_tokens": 100_000}
    out = capacity.cost_per_1000(tokens, prices, requests=100)
    # (400k x 2.5 + 600k x 0.25 + 100k x 15) / 1e6 = 2.65 USD for 100 requests
    assert out["kind"] == "calculated" and out["run_usd"] == 2.65 and out["usd"] == 26.5
    assert capacity.cost_per_1000(tokens, {"usd_per_1m": {}}, requests=100)["kind"] == "not-measured"


def test_compute_is_an_upper_bound_estimate_never_calculated():
    out = capacity.compute_estimate(sessions=10, active_minutes=15, idle_timeout_minutes=15, vcpu=1, gib=2,
                                    prices={"vcpu_hour": 0.1, "gib_hour": 0.01}, requests=200)
    assert out["kind"] == "estimate"
    assert out["run_usd"] == round(10 * 0.5 * (0.1 + 0.02), 4)
    assert "Upper bound" in out["why"]


def test_documented_facts_cite_learn():
    assert all(f["kind"] == "documented" and f["source"].startswith("https://learn.microsoft.com/")
               for f in capacity.DOCUMENTED)
