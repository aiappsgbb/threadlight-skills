import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import score

def _arm(name, ok=True, rounds=100, rubric=0.9, cost=1.0):
    return {"arm": name, "phases_ok": ok, "rounds": rounds,
            "rubric": rubric, "cost_usd": cost}

def test_mini_keeps_up_on_simple():
    card = score.validation_scorecard("returns-triage", [
        _arm("mini", rounds=110, rubric=0.9, cost=0.9),
        _arm("router", rounds=120, rubric=0.92, cost=8.0),
        _arm("strong", rounds=100, rubric=0.95, cost=20.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "keeps-up"

def test_mini_falls_behind_on_rubric():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=140, rubric=0.6, cost=1.0),
        _arm("router", rounds=150, rubric=0.88, cost=8.0),
        _arm("strong", rounds=130, rubric=0.9, cost=22.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "falls-behind"
    assert "rubric" in card["arms"]["mini"]["reasons"]

def test_mini_falls_behind_on_rounds():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=300, rubric=0.85, cost=1.0),
        _arm("strong", rounds=130, rubric=0.9, cost=22.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "falls-behind"
    assert "rounds" in card["arms"]["mini"]["reasons"]

def test_router_closes_gap():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("router", rounds=150, rubric=0.88, cost=8.0),
        _arm("strong", rounds=140, rubric=0.9, cost=22.0),
    ])
    assert card["router_verdict"] == "closes-the-gap"

def test_router_not_worth_it():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=140, rubric=0.62, cost=1.0),
        _arm("router", rounds=150, rubric=0.64, cost=20.0),
        _arm("strong", rounds=140, rubric=0.9, cost=22.0),
    ])
    assert card["router_verdict"] == "not-worth-it"

def test_phase_failure_is_falls_behind():
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", ok=False, rounds=120, rubric=0.9, cost=1.0),
        _arm("strong", rounds=120, rubric=0.9, cost=22.0),
    ])
    assert card["arms"]["mini"]["verdict"] == "falls-behind"
    assert "phase" in card["arms"]["mini"]["reasons"]

def test_router_mixed_when_good_rubric_but_not_cheaper():
    # rubric within 0.1 of strong (matches) but router is NOT cheaper -> mixed
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("router", rounds=150, rubric=0.88, cost=25.0),
        _arm("strong", rounds=140, rubric=0.9, cost=22.0),
    ])
    assert card["router_verdict"] == "mixed"

def test_router_verdict_none_when_strong_absent():
    # no strong arm -> router_verdict is None (contract: None if router or strong absent)
    card = score.validation_scorecard("fsi-kyc-aml", [
        _arm("mini", rounds=120, rubric=0.9, cost=1.0),
        _arm("router", rounds=130, rubric=0.9, cost=8.0),
    ])
    assert card["router_verdict"] is None
