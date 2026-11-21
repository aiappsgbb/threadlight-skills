# Eval gate policy: held-out split, noise-aware deltas, evaluator suitability

This file is read by `scripts/evals_check.py` (EVAL-106..109) and produced by
`scripts/eval_yaml.py`. A single run compared with a single threshold
(EVAL-105) is not decision-grade on a small dataset. Use this policy before any
model, prompt or tool swap, and before you accept an Agent Optimizer candidate.

## 1. Scaffold `evals/azure.eval.yaml`

```sh
python <threadlight-skills>/skills/threadlight-evals/scripts/eval_yaml.py split \
  evals/golden.jsonl --agent <agent> --out-dir evals/datasets
python <threadlight-skills>/skills/threadlight-evals/scripts/eval_yaml.py scaffold \
  --agent <agent> --rubric <rubric> --rubric-file evals/evaluators/<rubric>.json \
  --judge-model <judge-deployment> --evals-dir evals
azd ai eval create <agent>-gate
azd ai eval create <agent>-optimizer-input
```

Golden rows are `{"name", "query", "ground_truth"}`; add `"category"` to
stratify the split. The scaffold writes two evals:

| Eval | Dataset | Rubric `dataMapping` | Used by |
|---|---|---|---|
| `<agent>-gate` | held-out (~30 %) | `response: {{sample.output_items}}`, `tool_calls`, `tool_definitions`, `ground_truth: {{item.ground_truth}}` | release gate, CI |
| `<agent>-optimizer-input` | optimizer part | none | `azd ai agent optimize --eval` only |

Why two evals:

- `azd ai eval init` maps a custom rubric to `sample.output_text` only, with no
  `ground_truth`. **Tested, Nov 2026:** that judge cannot see the tool calls.
  With the mapping, the rubric pass rate on the same agent version went from 13 %
  to 47 % (#209).
- `azd ai agent optimize --eval` rejects an evaluator that has a mapping:
  `evaluator "..." has dataMapping, which optimization cannot represent`. So the
  optimizer eval stays unmapped, and it never sees the held-out rows.
- Stored mappings are immutable
  ([Learn](https://learn.microsoft.com/azure/developer/azure-developer-cli/extensions/foundry-evaluations-extension#input,-generation,-and-mapping-behavior)).
  The gate's rubric reference therefore has its own criterion name
  (`<rubric>-trajectory`).

The scaffold also raises the rubric `pass_threshold` to at least 0.7. The
generated rubric defaults to 0.5.

**Recorded, 2026-11-21:** `references/fixtures/azd-eval-create/` holds the
scaffold output, the `azd ai eval create` log for both evals, and the stored
mappings read back with `azd ai eval show`. The fixture used azd 1.37.3 and
`azure.ai.evaluations` 1.0.0-beta.2. The service kept `sample.output_items` and
`ground_truth` on the gate rubric. It stored `response: {{sample.output_text}}`
on the unmapped optimizer rubric.

## 2. Gate decision (`evals/gate-runs.json`, EVAL-109)

```json
{"eval": "<agent>-gate", "floor": 0.7, "tolerance": 0.15,
 "baseline": {"run_id": "evalrun_...", "pass_rate": 0.8},
 "runs": [{"run_id": "evalrun_...", "pass_rate": 0.75},
          {"run_id": "evalrun_...", "pass_rate": 0.85}]}
```

- **Two or more gate runs.** The decision uses their mean. **Tested, Nov 2026:**
  the same agent version, dataset and judge scored 33.3 %, 33.3 % and 20.0 %
  (±13 pp) across three runs (#210).
- **Pass** requires two things:
  - `mean >= floor`;
  - `mean >= baseline.pass_rate - tolerance`, where the baseline is the last
    accepted run.
- **Tolerance:** `tolerance` must be at least the measured spread between runs,
  otherwise the result is should-fix. `--fail-on` alone is absolute, not
  relative to a baseline
  ([quickstart](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-optimize-hosted-agent)).
- **Manifest:** `metrics.gate` in `specs/evals-manifest.json` records the runs,
  mean, baseline run ID, tolerance, floor and decision.

CI snippet. Treat **any** non-zero exit as failure. The quickstart says
`--fail-on` exits 1; **Tested, Nov 2026** (#209) it exited 2.

```sh
azd ai eval run start --eval <agent>-gate --name gate-$RUN --wait --fail-on pass-rate=0.7 \
  || { echo "eval gate failed"; exit 1; }
```

## 3. Data (EVAL-107)

- **Size:** at least 50 golden rows. They must cover every decision branch and
  every adversarial class: prompt injection, data-dump requests, and missing or
  unknown IDs.
- **Held-out split:** about 30 % of the rows are held out, and only the gate
  eval reads them. A gate on the optimizer's own rows measures overfitting, not
  quality.
- **Must-fix** when the gate has no held-out dataset or there are fewer than 50
  rows.

## 4. Evaluator suitability (EVAL-108)

- **`builtin.task_completion` penalises correct policy behaviour.**
  **Tested, Nov 2026:** on a fixed agent, 5 of 6 `task_completion` failures were
  correct behaviour (#210):
  - escalating a high-value return;
  - refusing a prompt injection;
  - refusing to dump internal policy;
  - asking for a missing order ID.
- **The overall pass rate needs every evaluator to pass,** so this capped the
  gate at about 60 % while the rubric passed 86.7 %.
- **Gate a policy-gated agent with** the trajectory-mapped rubric plus
  `builtin.task_adherence` or `builtin.intent_resolution`.
- **Should-fix** when `task_completion` gates a SPEC that has refusal or
  escalation criteria.

## 5. Optimizer candidate review checklist

Before you deploy a candidate, check these points.

- [ ] Every decision label from the SPEC is still in the instructions and output
      format. **Tested, Nov 2026:** one candidate allowed only `APPROVE`/`DENY`
      and silently dropped `ESCALATE` (#210).
- [ ] No rule restates a golden row ("domain-specific rules to remember").
      Compare the candidate with the dataset.
- [ ] Held-out gate: two runs, mean within tolerance of the baseline. Never use
      the optimizer's own score.
- [ ] Token growth of the instructions compared with the score gain. A longer
      prompt costs on every call.
- [ ] Insight-driven fixes accept up to 2 % general-quality regression
      ([Learn](https://learn.microsoft.com/azure/foundry/agents/how-to/optimize-agents-from-insights)).
      Decide whether that is acceptable for your gate.
- [ ] Tools are read-only or isolated, because optimizer runs call them for real.

## 6. Known CLI failures (azd 1.37.3, `azure.ai.evaluations` 1.0.0-beta.2, Nov 2026)

| Command | Failure | Workaround |
|---|---|---|
| `azd ai eval generate --evaluator --trace-days 1` | Repeated "Model authentication or authorization failed ... verify Foundry User role", even with the role assigned | `--trace-days 0` |
| `azd ai eval generate --dataset --from traces` | `HTTP 400 invalidPayload: Invalid seed source for data generation job` (App Insights connected, ~300 traces) | Harvest rows from App Insights with KQL, or `--from prompt` |
| `azd ai eval generate --from agent` | Service data-generation error (documented in the [quickstart](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-optimize-hosted-agent)) | `--from prompt` |
| `azd ai eval run start --fail-on` | Exit code 2, not 1 as documented | Test for any non-zero exit |
| `azd ai agent optimize --eval <mapped eval>` | `has dataMapping, which optimization cannot represent` | Use `<agent>-optimizer-input` |
| `azd ai eval generate` on a fresh `microsoft.foundry` project | HTTP 401 `AIServices/assets/write` | `threadlight-deploy` MAF `postprovision` hook (role IDs) |
