#!/usr/bin/env python3
"""question_types.py — question-type tags and per-type scoring for eval sets.

Optional companion to ``evals_check.py``. A dataset row may carry a
``question_type`` tag:

    lookup       one fact from one document
    multi_hop    chains facts across two or more documents
    negation     "which X has no Y": needs the full relation set
    aggregation  counts, sums or "all X that" over many documents
    thematic     summary of a theme across the corpus

``multi_hop``, ``negation`` and ``aggregation`` are *relational*. When a
process has relational acceptance questions (any row tagged relational, or
``--relational`` because the SPEC enables a Foundry IQ entity graph), at least
30% of the rows must be relational and every relational row must carry ground
truth (``expected`` or ``expected_entities``). An untagged dataset with no
relational requirement is ``not-applicable``: the default path is unchanged.

Per-type scoring groups run results by ``question_type``. A result row passes
from an explicit ``passed`` boolean, or, when the dataset row declares
``expected_entities`` (``{"entity_type": ["ID", ...]}``), from the entity rule:
every expected entity appears in the answer and no other known entity of the
same type does. Known entities of a type are the union of all
``expected_entities`` and optional ``distractor_entities`` of that type across
the dataset.

stdlib-only; reads files, never calls a model or a service.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

QUESTION_TYPES = ("lookup", "multi_hop", "negation", "aggregation", "thematic")
RELATIONAL_TYPES = frozenset({"multi_hop", "negation", "aggregation"})
MIN_RELATIONAL_SHARE = 0.30


class QuestionTypeError(ValueError):
    """Malformed tag, ground truth or result row."""


def load_rows(path) -> list:
    """Rows from a ``.jsonl`` file or a ``.json`` list (or ``{"rows": [...]}``)."""
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith(".jsonl"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        data = json.loads(text)
        rows = data.get("rows", data.get("results")) if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise QuestionTypeError(f"{path}: expected a list of objects")
    return rows


def _row_type(row: dict, index: int):
    if "question_type" not in row:
        return None
    value = row["question_type"]
    if value not in QUESTION_TYPES:
        raise QuestionTypeError(
            f"row[{index}].question_type must be one of {', '.join(QUESTION_TYPES)}"
        )
    return value


def _entities(row: dict, key: str, index: int) -> dict:
    value = row.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(kind, str) and kind
        and isinstance(ids, list) and all(isinstance(i, str) and i for i in ids)
        for kind, ids in value.items()
    ):
        raise QuestionTypeError(f"row[{index}].{key} must map entity types to lists of IDs")
    return value


def _has_ground_truth(row: dict, index: int) -> bool:
    # An empty list is ground truth too: "which X has no Y" -> none.
    if _entities(row, "expected_entities", index):
        return True
    expected = row.get("expected")
    return isinstance(expected, str) and bool(expected.strip())


def check_mix(rows: list, *, relational: bool = False) -> dict:
    """Assess the question-type mix of an eval dataset.

    Returns ``status`` ``pass``, ``must-fix`` or ``not-applicable`` with a
    ``reason``, per-type ``counts`` and the ``relational_share``.
    """
    _require_unique_ids(rows, "row")
    types = [_row_type(row, index) for index, row in enumerate(rows)]
    counts = {kind: types.count(kind) for kind in QUESTION_TYPES}
    counts["untagged"] = types.count(None)
    relational_rows = [index for index, kind in enumerate(types) if kind in RELATIONAL_TYPES]
    share = len(relational_rows) / len(rows) if rows else 0.0
    result = {"counts": counts, "relational_share": round(share, 4)}
    if not (relational or relational_rows):
        return {"status": "not-applicable", "reason": "no-relational-questions", **result}
    missing_truth = [
        rows[index].get("id", f"row-{index}")
        for index in relational_rows
        if not _has_ground_truth(rows[index], index)
    ]
    if missing_truth:
        return {
            "status": "must-fix", "reason": "relational-without-ground-truth",
            "rows_without_ground_truth": missing_truth, **result,
        }
    if share < MIN_RELATIONAL_SHARE:
        return {"status": "must-fix", "reason": "relational-share-below-30pct", **result}
    return {"status": "pass", "reason": "relational-mix-ok", **result}


def _require_unique_ids(rows: list, label: str) -> None:
    seen = set()
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            continue
        if row_id in seen:
            raise QuestionTypeError(f"duplicate {label} id {row_id!r}")
        seen.add(row_id)


def _entity_pattern(entity: str):
    return re.compile(r"(?<![\w-])" + re.escape(entity) + r"(?![\w-])", re.I)


def _mentioned(answer: str, known: set) -> set:
    """Known entities named in *answer*, longest first, so a name that is a
    prefix of another (``ACME`` / ``ACME Corp``) is not double-counted."""
    found = set()
    remaining = answer
    for entity in sorted(known, key=len, reverse=True):
        pattern = _entity_pattern(entity)
        if pattern.search(remaining):
            found.add(entity)
            remaining = pattern.sub(" ", remaining)
    return found


def entity_universe(rows: list) -> dict:
    universe: dict = {}
    for index, row in enumerate(rows):
        for key in ("expected_entities", "distractor_entities"):
            for kind, ids in _entities(row, key, index).items():
                universe.setdefault(kind, set()).update(ids)
    return universe


def score_answer(answer: str, expected: dict, universe: dict) -> bool:
    """Every expected entity present; no other known entity of the same type."""
    for kind, ids in expected.items():
        mentioned = _mentioned(answer, universe.get(kind, set()) | set(ids))
        if mentioned != set(ids):
            return False
    return True


def score_by_type(rows: list, results: list) -> dict:
    """Per-``question_type`` pass counts for *results* matched to *rows* by ``id``."""
    _require_unique_ids(rows, "row")
    _require_unique_ids(results, "result")
    by_id = {}
    for index, row in enumerate(rows):
        if "id" not in row:
            raise QuestionTypeError(f"row[{index}] needs an id to be scored")
        by_id[row["id"]] = (index, row)
    universe = entity_universe(rows)
    scores: dict = {}
    for position, result in enumerate(results):
        row_id = result.get("id")
        if row_id not in by_id:
            raise QuestionTypeError(f"result[{position}].id {row_id!r} is not in the dataset")
        index, row = by_id[row_id]
        kind = _row_type(row, index) or "untagged"
        if isinstance(result.get("passed"), bool):
            passed = result["passed"]
        else:
            expected = _entities(row, "expected_entities", index)
            answer = result.get("answer")
            if not expected or not isinstance(answer, str):
                raise QuestionTypeError(
                    f"result[{position}] needs a boolean 'passed', or an 'answer' "
                    "with expected_entities on its dataset row"
                )
            passed = score_answer(answer, expected, universe)
        bucket = scores.setdefault(kind, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(passed)
    for bucket in scores.values():
        bucket["rate"] = round(bucket["passed"] / bucket["total"], 4)
    return dict(sorted(scores.items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Question-type mix check and per-type scoring for an eval dataset."
    )
    parser.add_argument("--dataset", required=True, help="eval dataset (.jsonl or .json)")
    parser.add_argument("--results", help="run results: id + passed, or id + answer")
    parser.add_argument(
        "--relational", action="store_true",
        help="the process has relational questions (e.g. SPEC enrichment entity_graph)",
    )
    args = parser.parse_args(argv)
    try:
        rows = load_rows(args.dataset)
        report = {"mix": check_mix(rows, relational=args.relational)}
        if args.results:
            report["per_type"] = score_by_type(rows, load_rows(args.results))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 2 if report["mix"]["status"] == "must-fix" else 0


if __name__ == "__main__":
    sys.exit(main())
