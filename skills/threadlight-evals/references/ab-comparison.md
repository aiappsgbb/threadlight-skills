# Champion–challenger A/B comparison gate

Run this gate before swapping a model, system prompt, tool schema, retrieval
index, or evaluator configuration.

## Recipe

1. Pin the current production configuration as `champion`.
2. Pin the proposed configuration as `challenger`.
3. Run the same held-out eval dataset against both configurations.
4. Compare pass rate and critical scenario scores.
5. Allow the swap only when the challenger meets all declared thresholds and is
   not materially worse than champion on protected scenarios.

Example gate semantics:

```yaml
comparison: champion_challenger
champion:
  model: gpt-4.1
  prompt: prompts/system.v7.md
challenger:
  model: gpt-4.1-mini
  prompt: prompts/system.v8.md
minimum_pass_rate: 0.85
maximum_regression: 0.05
gate: challenger_pass_rate >= minimum_pass_rate and challenger_pass_rate >= champion_pass_rate - maximum_regression
```

Store the config under `evals/ab/` or include `champion`, `challenger`, and
`baseline_vs` markers in an eval script/config. `threadlight-evals` treats this
as the F3 capability and reports it in `specs/evals-manifest.json`.
