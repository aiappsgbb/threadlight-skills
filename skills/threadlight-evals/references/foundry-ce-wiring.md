# Foundry Continuous Evaluation wiring (Plan A)

Plan A wires continuous evaluation at the agent run boundary so live threads are
scored without exporting transcripts to a workstation. The important signal for
`threadlight-evals` is the Foundry SDK call:

```python
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

client = AIProjectClient(
    endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

client.evaluations.create_agent_evaluation(
    thread=thread,
    run=run,
    evaluators=[groundedness, relevance, tool_output_utilization],
    app_insights_connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"],
)
```

Use keyless auth everywhere:

- Local: `az login` feeds `DefaultAzureCredential`.
- CI: federated OIDC, no client secrets.
- Azure-hosted runtime: managed identity with the minimum Foundry + Application
  Insights permissions required to submit evaluations and write telemetry.

Results land in Application Insights. Wire an Azure Monitor metric/log alert for
pass-rate or threshold-breach signals and commit the alert definition under
`infra/` so EVAL-104 can be verified.
