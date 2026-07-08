import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import harvest

FIX = Path(__file__).resolve().parents[1] / "references" / "fixtures"

def test_count_rounds_counts_steps_and_attempts():
    res = harvest.count_rounds([FIX / "phase-design-sample.log"])
    assert res["steps"] == 4          # four '● ' lines
    assert res["attempts"] == 1       # one 'attempt N of 3' header
    assert res["total"] == 4

def test_count_rounds_missing_file_is_zero():
    res = harvest.count_rounds([FIX / "does-not-exist.log"])
    assert res == {"steps": 0, "attempts": 0, "total": 0}
