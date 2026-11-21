# Foundry evaluation rules wiring (Plan A)

Plan A scores production traffic with Foundry **evaluation rules**
([continuous and scheduled evaluation](https://learn.microsoft.com/azure/foundry/observability/how-to/continuous-scheduled-evaluation)).
`threadlight-evals` looks for `evaluation_rules.create_or_update(...)` with a rule
action (`ContinuousEvaluationRuleAction`, `TraceEvaluationRuleAction`,
`DatasetEvaluationRuleAction`) or a rule action declared in `azure.eval.yaml`.

## Which rule

- **Hosted agent:** scheduled trace rule (hourly, `max_traces`), or a scheduled
  dataset rule that sends fixed queries. **Tested, Nov 2026:** an hourly trace rule
  on a hosted agent provisioned `SUCCEEDED` and scored 7 traces on its first run.
- **Prompt agent:** continuous rule on `RESPONSE_COMPLETED`, capped by
  `max_hourly_runs` (default 100). **Tested, Nov 2026:** one run per completed
  response, stopped at the cap of 20.
- **External agent:** scheduled trace rule with `agent_id` set to the agent's
  `otel_agent_id`. Continuous rules run on Foundry agent response completion and
  do not apply to an external agent (documented).

## Keyless snippet (azure-ai-projects 2.x)

```python
import os, time
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    EvaluationRule, EvaluationRuleEventType, HourlyRecurrenceSchedule,
    RecurrenceTrigger, ScheduleProvisioningStatus, TraceEvaluationRuleAction,
    TracesEvalRunDataSource,
)

project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                          credential=DefaultAzureCredential())
rule = project.evaluation_rules.create_or_update(
    id="pilot-trace-rule",
    evaluation_rule=EvaluationRule(
        display_name="Pilot trace evaluation",
        action=TraceEvaluationRuleAction(
            eval_id=trace_eval.id,  # openai_client.evals.create(... "scenario": "traces")
            source=TracesEvalRunDataSource(agent_name=agent_name, max_traces=10),
        ),
        event_type=EvaluationRuleEventType.TIMER_TRIGGER,
        schedule=RecurrenceTrigger(interval=1, schedule=HourlyRecurrenceSchedule()),
        enabled=True,
    ),
)
while rule.provisioning_status in (ScheduleProvisioningStatus.CREATING,
                                   ScheduleProvisioningStatus.UPDATING):
    time.sleep(5)
    rule = project.evaluation_rules.get(id=rule.id)
```

Continuous rule for a prompt agent:
`ContinuousEvaluationRuleAction(eval_id=..., max_hourly_runs=20)`,
`event_type=EvaluationRuleEventType.RESPONSE_COMPLETED`,
`filter=EvaluationRuleFilter(agent_name=agent_name)`.

## Identity

- Local: `az login` feeds `DefaultAzureCredential`. CI: federated OIDC, no secrets.
- The **project managed identity** needs **Foundry User** for rules to run, and
  **Reader** on the connected Application Insights for trace rules
  ([evaluation permissions](https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-permissions)).
  The `threadlight-deploy` MAF template's `postprovision` hook grants Foundry User and
  Monitoring Reader by role ID; Monitoring Reader includes `*/read`, the resource read
  that Reader provides.

## Cost control and teardown

- Cap spend with `max_traces` (trace rules), `max_hourly_runs` (continuous rules)
  and the recurrence interval; every run calls the judge model.
- **Teardown disables rules:** `rule.enabled = False` then `create_or_update`
  again. It applies asynchronously (still `True` on an immediate read, `False`
  about 30 s later): read it back before reporting it disabled.

Results appear on the Foundry Evaluations page and the agent's Monitor tab. Wire an
Azure Monitor alert for pass-rate or threshold breach and commit it under `infra/`
so EVAL-104 can be verified.

## Legacy

`client.evaluations.create_agent_evaluation(thread, run, ...)` is the classic-portal
API ([classic docs](https://learn.microsoft.com/azure/foundry-classic/how-to/continuous-evaluation-agents))
and is absent from `azure-ai-projects` 2.x. `evals_check.py` reports it as `legacy`;
`--accept-legacy-online-eval` lets it satisfy EVAL-101 with the label kept.
