import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rubric

FIX = Path(__file__).resolve().parents[1] / "references" / "fixtures"

RUBRIC = {
    "checks": [
        {"id": "retention-tension", "weight": 1,
         "all_of": ["retention", "gdpr"]},
        {"id": "beneficial-ownership", "weight": 1, "regex": r"25\s*%"},
        {"id": "tipping-off", "weight": 1, "contains": "tipping-off"},
        {"id": "structuring-ctr", "weight": 1,
         "all_of": ["10,000", "structuring"]},
        {"id": "edd-approval", "weight": 1,
         "all_of": ["enhanced due diligence", "senior-approval"]},
        {"id": "multi-jurisdiction", "weight": 1,
         "all_of": ["bsa", "amld"]},
    ]
}

def test_good_spec_scores_full():
    res = rubric.score_rubric(FIX / "kyc-spec-good.md", RUBRIC)
    assert res["score"] == 1.0
    assert all(c["passed"] for c in res["checks"])

def test_bad_spec_fails_specific_checks():
    res = rubric.score_rubric(FIX / "kyc-spec-bad.md", RUBRIC)
    failed = {c["id"] for c in res["checks"] if not c["passed"]}
    assert {"tipping-off", "retention-tension", "structuring-ctr"} <= failed
    assert res["score"] < 0.6

def test_unknown_strategy_raises():
    import pytest
    with pytest.raises(ValueError):
        rubric.score_rubric(FIX / "kyc-spec-good.md",
                            {"checks": [{"id": "x", "weight": 1, "contain": "typo"}]})

def test_score_rubric_accepts_dir(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text("tipping-off prohibited", encoding="utf-8")
    res = rubric.score_rubric(tmp_path, {"checks": [
        {"id": "t", "weight": 1, "contains": "tipping-off"}]})
    assert res["score"] == 1.0
