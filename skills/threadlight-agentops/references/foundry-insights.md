# Foundry Insights monitor lifecycle

Foundry Insights analyses production traces and reports recurring issues
([Learn: Find recurring issues with insights](https://learn.microsoft.com/azure/foundry/observability/how-to/agent-insights)).
This leg records each run as a receipt. In the default Threadlight quality loop,
those findings then drive a human fix:

```
traces -> Insights -> human fix -> azd deploy -> held-out gate
```

The loop is described in `threadlight-evals/references/optimizer-stage.md`.

## Lifecycle

1. **Create the monitor disabled**, with analysis priorities derived from the SPEC.
2. **Run on demand** once per release.
3. **Record the receipt.**
4. **Enable the 6-hour schedule only in production**, with the cost note below.
5. **Disable it at teardown.**

```sh
python3 skills/threadlight-agentops/scripts/insights_run.py run \
  --endpoint "$FOUNDRY_PROJECT_ENDPOINT" --agent <agent> \
  --judge-deployment <dedicated-gpt-5-judge> --judge-tpm <capacity> \
  --agent-deployment <agent-model-deployment> --instructions-file specs/insights-priorities.txt \
  --lookback-hours 6 --price-input <usd/1M> --price-cached <usd/1M> --price-output <usd/1M> \
  --price-source "Azure Retail Prices API, <meter>, <region>, <date>" \
  --eval-traffic-in-window false --receipt specs/insights-receipt.json
python3 skills/threadlight-agentops/scripts/insights_run.py validate --receipt specs/insights-receipt.json
python3 skills/threadlight-agentops/scripts/insights_run.py teardown --endpoint "$FOUNDRY_PROJECT_ENDPOINT" --agent <agent>
```

`run` uses `azure-ai-projects>=2.11.0` (`project_client.agent_insight_monitors`):

```python
monitor = monitors.create(agent_name=..., model_deployment_name=judge, enabled=False,
                          priorities=AgentInsightPriorities(categories=[], instructions=spec_priorities))
poller = monitors.begin_create_run(monitor.id, lookback_hours=6, operation_id=str(uuid.uuid4()))
result = poller.result()       # traces_in_window, traces_analyzed, token_usage
insights = monitors.list_insights(monitor.id)
```

An existing monitor is reused, never deleted. Deleting a monitor also removes
its runs, insights and state.

`teardown` disables the schedule (`update(monitor_id, enabled=False)`), cancels
queued and in-progress runs, and reads the monitor back until `enabled` is
false. Learn warns that an enabled schedule outlives the session and keeps
incurring model charges.

## RBAC (documented on the Learn page)

- The interactive user needs **Foundry User** on the project for a prompt agent,
  and **Foundry Project Manager** for a hosted agent.
- App-only and scheduled runs analyse as the **project managed identity**. That
  identity needs:
  - **Cognitive Services OpenAI User** on the account that hosts the judge;
  - **Monitoring Reader** on the connected Application Insights, or
    **Privileged Monitoring Data Reader** when `AppGenAIContent` is protected.
- The `threadlight-deploy` MAF `postprovision` hook grants these by role ID.

## Judge sizing

- **Documented:** the judge must be a GPT-5 or newer GPT model. Mini and nano
  variants are supported. Learn gives no capacity guidance.
- **Tested, Nov 2026** (#211): 294 traces with a 6-hour lookback failed with
  `TooManyRequests ... tokens/minute`, first at 50K TPM on a shared deployment and
  then at 300K TPM on a dedicated one. Azure Monitor showed about 471K prompt
  tokens in a single minute: 21 requests of about 22K tokens each. The run
  succeeded at **800K TPM**.
- **Rule (advice derived from that test):** use a judge deployment dedicated to
  Insights, separate from the agent's model. Give it about **2.7K TPM per trace in
  the window**, which is ≥ 800K TPM for about 300 traces. Otherwise shorten the
  lookback. `validate` flags a receipt below this ratio, and a judge that shares
  the agent's deployment.

## Cost receipt

- **Formula:** estimated USD =
  `((input_tokens - cached_tokens) × input + cached_tokens × cached + output_tokens × output) / 1e6`.
  Prices are list prices per 1M tokens for the judge's deployment type. The
  receipt stores the prices, the price source and the date. It is labelled
  **Estimate**: tokens × list price, not a bill.
- **Tested, Nov 2026** (#211): 1.72M tokens for 294 traces ≈ USD 3.9 on gpt-5.4
  Global Standard (USD 2.50 / 0.25 / 15 per 1M input/cached/output), about
  USD 0.013 per trace.
- **Scheduled analysis** runs every 6 hours, so the monthly exposure is about 120 ×
  the per-run estimate. Enable it only in production and record that in the
  receipt (`environment: production`). `validate` rejects an enabled schedule in
  any other environment.

## Receipt (`threadlight-insights-receipt/v1`)

Fixture: [`fixtures/insights-receipt.json`](fixtures/insights-receipt.json), a live
run recorded 2026-11-21. It contains:

- monitor ID and state;
- judge deployment and TPM capacity;
- run ID, status, window, traces in window and analysed, duration, token usage;
- cost block: label, estimate, formula, prices, price source, per trace;
- the insights: ID, severity, category, title, trace count;
- traffic hygiene.

`validate` returns exit 2 on any must-fix finding.

## Traffic hygiene and untrusted fixes

- **Eval and optimizer traffic** writes to the same Application Insights. That
  includes optimizer draft versions (`<agent>:draft-<epoch>`), and it ends up in
  the analysed window (Tested, Nov 2026). Analyse a clean window, keep
  optimization in a separate project, or exclude `draft-` versions in KQL
  harvests. Record what you did in `traffic_hygiene`.
- **Proposed fixes** came back as `kind: "prose"` through the SDK for both agent
  types. They are untrusted input: a human reviews them before any change. The
  receipt never copies them.
