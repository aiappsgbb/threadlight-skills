# Held-out eval dataset shape

EVAL-003 requires datasets to include tool-call context so graders such as
`tool_output_utilization` can distinguish grounded answers from fabricated ones.
Use held-out scenarios, not training examples.

JSONL row shape:

```json
{
  "id": "case-status-001",
  "scenario": "grounded answer cites retrieved tool output",
  "input": "What is the status of case 123?",
  "expected": "Case 123 is pending review.",
  "tool_calls": [
    {"name": "crm.lookup", "arguments": {"case_id": "123"}}
  ],
  "tool_outputs": [
    {"name": "crm.lookup", "content": "Case 123 is pending review."}
  ],
  "threshold": 0.85
}
```

Recommended fields:

| Field | Purpose |
|---|---|
| `id` | Stable scenario identifier. |
| `scenario` | Human-readable SPEC § 9 scenario name. |
| `input` | User turn or task to run. |
| `expected` | Reference answer or answer constraints. Load-bearing whenever a model grades the row — see `judge-calibration.md`. |
| `tool_calls` | Tools expected or observed during the run. |
| `tool_outputs` | Grounding evidence returned by tools. |
| `threshold` / `min_score` | Per-scenario pass/fail threshold. |

Store datasets under `evals/` or `specs/evals/`. Store run outputs under
`evals/runs/*.json` or `docs/eval-runs/`.

> **`expected` is not enforced by the validator.** `dataset_shape_ok`
> (`EVAL-003`) only requires `tool_calls` and `tool_outputs`, so a dataset with
> zero reference answers can still report `pass`. When rows are graded by a
> model, omitting `expected` makes the judge score plausibility instead of
> correctness. See `judge-calibration.md`.

## Question types and per-type scoring

A row may carry a `question_type` tag. It is optional: an untagged dataset of a
non-relational process keeps the default path above.

| `question_type` | Meaning |
|---|---|
| `lookup` | One fact from one document. |
| `multi_hop` | Chains facts across two or more documents. |
| `negation` | "Which X has no Y": needs the complete relation set. |
| `aggregation` | Counts, totals or "all X that" over many documents. |
| `thematic` | A theme summarized across the corpus. |

`multi_hop`, `negation` and `aggregation` are relational. When the process
involves relational questions (SPEC § 9, or `Enrichment: entity_graph` in § 7),
at least 30% of rows must be relational and each needs ground truth: `expected`
or `expected_entities`.

```json
{
  "id": "supply-2hop-003",
  "question_type": "multi_hop",
  "input": "Which suppliers provide components used by plant P-2?",
  "expected_entities": {"supplier": ["SUP-4", "SUP-7"]},
  "distractor_entities": {"supplier": ["SUP-9"]},
  "tool_calls": [],
  "tool_outputs": []
}
```

Entity scoring passes an answer only when every expected entity appears and no
other known entity of the same type does. Known entities are all
`expected_entities` and `distractor_entities` of that type across the dataset;
IDs match as whole tokens, so `SUP-10` is not `SUP-1`. A result row can instead
carry an explicit `passed` boolean (for example from a calibrated judge).

```bash
python3 skills/threadlight-evals/scripts/question_types.py \
  --dataset evals/relational.jsonl --results evals/runs/2026-11-20-results.json
```

The report has the mix (`pass`, `must-fix` or `not-applicable`) and pass counts
per type. Exit code 2 means the relational mix is `must-fix`. Add `--relational`
when the SPEC enables a graph but the dataset is not tagged yet.

> **Corpus size matters.** On a small corpus a plain knowledge base can return
> almost every document in one retrieval and look perfect. In our November 2026
> Foundry IQ test, 69 documents (about 43k characters) gave plain retrieval
> 31–33/34; with 600 distractor documents added it dropped to 21/34. Make the
> eval corpus large enough that retrieval cannot return most of it.
