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
