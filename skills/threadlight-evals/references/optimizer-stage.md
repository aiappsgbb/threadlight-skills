# Agent Optimizer: an optional, gated stage

Default quality loop for a Threadlight pilot (`optimizer: off`):

```
production traces -> Foundry Insights -> human fix -> azd deploy -> held-out gate (2 runs)
```

**Tested, Nov 2026** (#213). A human fix written from one Insights HIGH finding
took about a minute to redeploy (`azd deploy`). Re-gated, it raised the overall
pass rate from 20–33 % to 53–60 %, and the rubric pass rate from 47 % to 80–87 %.
The Insights lifecycle and its cost receipt are in `threadlight-agentops`
(`references/foundry-insights.md`).

## Opting in

`evals/quality-loop.json`:

```json
{"optimizer": "on", "tools_read_only": true, "human_review": "<who reviews the candidate diff>"}
```

`python <threadlight-skills>/skills/threadlight-evals/scripts/optimizer_stage.py --target .`
prints the decision and returns one of:

- exit 0 when the optimizer is off or allowed;
- exit 2 when it is blocked.

With `on`, the stage **fails closed** unless all of these are present:

1. **An estimate receipt** in `evals/optimizer-estimate.json`. It is the result of
   `project.agents.estimate_optimization_job(...)` (`azure-ai-projects` 2.11.0) for
   the job about to start. It has `cost.total` with `low`/`typical`/`ceiling` and
   a `prices_as_of` date. **Tested, Nov 2026:** USD 1.78 / 2.20 / 7.33 for 3
   candidates × 15 rows on a prompt agent.
2. **A passing held-out gate.** `evals/gate-runs.json` must have decision `pass`
   (EVAL-109) on a held-out split (EVAL-107). See `gate-policy.md`.
3. **An unmapped `<agent>-optimizer-input` eval** in `evals/azure.eval.yaml`,
   created with `scripts/eval_yaml.py`. The optimizer rejects mapped evaluators,
   and it never sees the held-out rows.
4. **`tools_read_only: true`.** Optimizer runs invoke the agent once per row, and
   every tool call executes for real
   ([quickstart warning](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-optimize-hosted-agent)).
5. **A named human reviewer** for the candidate diff. Promote only through
   `azd ai agent optimize apply --candidate <id>` followed by `azd deploy`, never
   by deploying the candidate directly. Run the review checklist in
   `gate-policy.md` § 5: decision labels preserved, no golden-row leakage, token
   growth compared with gain.

Then re-gate the deployed winner on the held-out eval, with two runs, before you
accept it.

## Documented constraints

From the [Agent Optimizer overview](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-optimizer-overview):

- Hosted-agent optimization requires the Responses protocol and is not available
  in Norway East.
- Prompt-agent optimization starts in the portal.
- The optimization model must be gpt-5, 5.1, 5.2, 5.4 or 5.5, DeepSeek-V4-Pro or
  DeepSeek-V3.2.
- The quickstart still says the subscription must be on an allow list.

## Known failures (Nov 2026)

Versions: azd 1.37.3, `azure.ai.agents` 1.2.0, `azure.ai.evaluations` 1.0.0-beta.2,
`azure-ai-projects` 2.11.0, `azure-ai-agentserver-optimization` 1.0.0b1.

| Path | Failure |
|---|---|
| CLI `azd ai agent optimize --eval <unmapped turn eval> --max-candidates 3` | `400 invalid_config: optimization_configuration.agent_optimization_space is required for agent_optimization.` |
| SDK, hosted agent, `begin_create_optimization_job` (instructions + tools) | 3 attempts, 0 scored candidates:<br>1. candidate evaluation stalled about 30 min, then `candidate_evaluation_failed`;<br>2. the baseline eval failed in 4 s with `(NotFound) Project not found`;<br>3. the baseline eval stuck `in_progress` with 0 items for 35 min. Manual `azd ai eval run`s completed in about 3 min. |
| SDK, prompt agent | Ran 12.6 min. Candidates scored 0.631 and 0.645 against a 0.662 baseline (scored on 12 rows and 15 rows respectively). The job then failed on candidate 3 with `Project not found`. Candidates overfit the dataset and dropped the `ESCALATE` label. |
| `azd ai agent optimize` | Overwrites `.agent_configs/baseline/metadata.yaml` ("Baseline saved to ..."). Commit first. |

Re-evaluate making the optimizer the default once the CLI and hosted SDK paths
complete end to end.
