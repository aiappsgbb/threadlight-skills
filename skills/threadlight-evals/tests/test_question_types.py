"""Tests for question_types.py — question-type tags and per-type scoring (#217)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import question_types as qt  # noqa: E402


def row(id, question_type=None, **extra):
    data = {"id": id, "input": f"question {id}", "tool_calls": [], "tool_outputs": []}
    if question_type is not None:
        data["question_type"] = question_type
    data.update(extra)
    return data


def relational_set(relational=3, lookup=7):
    rows = [row(f"r{i}", "multi_hop", expected_entities={"supplier": [f"SUP-{i}"]})
            for i in range(relational)]
    rows += [row(f"l{i}", "lookup", expected=f"answer {i}") for i in range(lookup)]
    return rows


def test_untagged_dataset_without_relational_requirement_is_not_applicable():
    result = qt.check_mix([row("a"), row("b")])
    assert result["status"] == "not-applicable"
    assert result["counts"]["untagged"] == 2


def test_lookup_only_dataset_keeps_default_path():
    assert qt.check_mix(relational_set(relational=0, lookup=5))["status"] == "not-applicable"


def test_thirty_percent_relational_with_ground_truth_passes():
    result = qt.check_mix(relational_set(relational=3, lookup=7))
    assert result["status"] == "pass"
    assert result["relational_share"] == 0.3
    assert result["counts"]["multi_hop"] == 3


def test_relational_share_below_thirty_percent_is_must_fix():
    result = qt.check_mix(relational_set(relational=2, lookup=8))
    assert result["status"] == "must-fix"
    assert result["reason"] == "relational-share-below-30pct"


def test_relational_flag_requires_relational_rows():
    result = qt.check_mix(relational_set(relational=0, lookup=5), relational=True)
    assert result["status"] == "must-fix"


def test_relational_row_without_ground_truth_is_must_fix():
    rows = relational_set(relational=3, lookup=2)
    rows.append(row("neg", "negation"))
    result = qt.check_mix(rows)
    assert result["status"] == "must-fix"
    assert result["reason"] == "relational-without-ground-truth"
    assert result["rows_without_ground_truth"] == ["neg"]


@pytest.mark.parametrize("bad", ["multihop", "", 3, None])
def test_unknown_question_type_raises(bad):
    with pytest.raises(qt.QuestionTypeError):
        qt.check_mix([{"id": "x", "question_type": bad}])


def test_malformed_expected_entities_raises():
    with pytest.raises(qt.QuestionTypeError):
        qt.check_mix([row("x", "multi_hop", expected_entities={"supplier": "SUP-1"})])


def test_entity_rule_requires_all_expected_and_no_wrong_entity_of_same_type():
    universe = {"supplier": {"SUP-1", "SUP-2", "SUP-3"}, "component": {"CMP-101"}}
    expected = {"supplier": ["SUP-1", "SUP-2"]}
    assert qt.score_answer("SUP-1 and SUP-2 supply CMP-101.", expected, universe)
    assert not qt.score_answer("Only SUP-1.", expected, universe)
    assert not qt.score_answer("SUP-1, SUP-2 and SUP-3.", expected, universe)
    # Entity IDs match whole tokens only: SUP-10 is not SUP-1.
    assert not qt.score_answer("SUP-10 and SUP-2.", expected, universe)


def test_score_by_type_groups_results_and_uses_entity_rule():
    rows = [
        row("m1", "multi_hop", expected_entities={"supplier": ["SUP-1"]}),
        row("m2", "multi_hop", expected_entities={"supplier": ["SUP-2"]}),
        row("n1", "negation", expected_entities={"supplier": ["SUP-3"]},
            distractor_entities={"supplier": ["SUP-9"]}),
        row("l1", "lookup", expected="x"),
    ]
    results = [
        {"id": "m1", "answer": "SUP-1"},
        {"id": "m2", "answer": "SUP-1 and SUP-2"},   # wrong extra supplier
        {"id": "n1", "answer": "SUP-3 but not SUP-9"},  # distractor named
        {"id": "l1", "passed": True},
    ]
    scores = qt.score_by_type(rows, results)
    assert scores["multi_hop"] == {"total": 2, "passed": 1, "rate": 0.5}
    assert scores["negation"] == {"total": 1, "passed": 0, "rate": 0.0}
    assert scores["lookup"] == {"total": 1, "passed": 1, "rate": 1.0}


def test_score_by_type_rejects_unknown_ids_and_unscorable_results():
    rows = [row("l1", "lookup", expected="x")]
    with pytest.raises(qt.QuestionTypeError):
        qt.score_by_type(rows, [{"id": "nope", "passed": True}])
    with pytest.raises(qt.QuestionTypeError):
        qt.score_by_type(rows, [{"id": "l1", "answer": "x"}])


def test_cli_reports_mix_and_per_type_and_gates_on_must_fix(tmp_path, capsys):
    dataset = tmp_path / "evals" / "relational.jsonl"
    dataset.parent.mkdir()
    dataset.write_text("\n".join(json.dumps(r) for r in relational_set(3, 7)), encoding="utf-8")
    results = tmp_path / "results.json"
    results.write_text(json.dumps([{"id": "r0", "answer": "SUP-0"}, {"id": "l0", "passed": False}]),
                       encoding="utf-8")
    assert qt.main(["--dataset", str(dataset), "--results", str(results)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mix"]["status"] == "pass"
    assert report["per_type"]["multi_hop"]["passed"] == 1
    assert report["per_type"]["lookup"]["passed"] == 0

    thin = tmp_path / "thin.jsonl"
    thin.write_text("\n".join(json.dumps(r) for r in relational_set(1, 9)), encoding="utf-8")
    assert qt.main(["--dataset", str(thin)]) == 2


def test_cli_untagged_dataset_exits_zero(tmp_path, capsys):
    dataset = tmp_path / "plain.jsonl"
    dataset.write_text(json.dumps(row("a")), encoding="utf-8")
    assert qt.main(["--dataset", str(dataset)]) == 0
    assert json.loads(capsys.readouterr().out)["mix"]["status"] == "not-applicable"
