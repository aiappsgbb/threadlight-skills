# Champion–challenger A/B comparison gate

Run this gate before swapping a model, system prompt, tool schema, retrieval
index, or evaluator configuration.

For the optional lower-cost **runtime-only** comparison after a prototype is
validated, use the canonical
[fixed-artifact checklist](../../threadlight-design/references/model-selection.md).
It separates authoring from runtime selection, defines compatibility and cost
accounting, and requires explicit scope/budget authorization. A 2x2 study of
builder artifacts is not the default experiment.

## Recipe

1. Pin the current validated configuration as `champion`.
2. Pin the proposed configuration as `challenger`. For runtime-only A/B, keep
   the same artifact, prompt, skills, tools, validators and fixtures; change only
   the runtime selection, subject to the declared matched compatibility condition.
3. Predeclare thresholds and protected scenarios; run the same held-out eval
   dataset (including negative/boundary cases and paraphrases) against both.
4. Preserve failures and guardrail violations. Compare quality, actual tool/skill
   use, structured output, PII/safety/governance, latency and cost per correct task.
5. Allow the swap only when every declared constraint passes and the challenger
   is not materially worse on protected scenarios. Otherwise keep the champion;
   never weaken controls or regenerate code to manufacture a passing comparison.

Illustrative runtime-only configuration (not deployment availability or a model
recommendation; pin actual versions and predeclare workload-specific thresholds):

```yaml
comparison: champion_challenger
champion:
  model: gpt-4.1
  prompt: prompts/system.v7.md
challenger:
  model: gpt-4.1-mini
  prompt: prompts/system.v7.md
minimum_pass_rate: 0.85
maximum_regression: 0.05
gate: challenger_pass_rate >= minimum_pass_rate and challenger_pass_rate >= champion_pass_rate - maximum_regression
```

The example's pass-rate expression is **necessary but not sufficient**: the
runner/reviewer must also verify every predeclared control and latency
constraint. For a prompt/tool/retrieval change, declare that independent change
instead; if several inputs change, do not attribute the result to the model alone.

Store the config under `evals/ab/` or include `champion`, `challenger`, and
`baseline_vs` markers in an eval script/config. `threadlight-evals` treats this
as the F3 capability and reports it in `specs/evals-manifest.json`.
Config presence is not execution evidence or promotion approval; retain the
actual paired outcomes and review decision.
