"""Foundry evaluation rules for the pilot agent (azure-ai-projects 2.x).

Hosted agent: scheduled trace rule (hourly, capped by max_traces).
Prompt agent: continuous rule on response completion (capped by max_hourly_runs).
Results land in the Foundry Evaluations page and the agent's Monitor tab; the
App Insights connection (APPLICATIONINSIGHTS_CONNECTION_STRING) carries the traces.
The project managed identity needs Foundry User for rules to run.
"""
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    ContinuousEvaluationRuleAction,
    EvaluationRule,
    EvaluationRuleEventType,
    EvaluationRuleFilter,
    HourlyRecurrenceSchedule,
    RecurrenceTrigger,
    ScheduleProvisioningStatus,
    TraceEvaluationRuleAction,
    TracesEvalRunDataSource,
)
from azure.identity import DefaultAzureCredential

PENDING = (ScheduleProvisioningStatus.CREATING, ScheduleProvisioningStatus.UPDATING)
RULE_IDS = ("pilot-trace-rule", "pilot-continuous-rule")


def wire_rules(project_endpoint, agent_name, trace_eval_id, continuous_eval_id, hosted=True):
    client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
    if hosted:
        rule = client.evaluation_rules.create_or_update(
            id=RULE_IDS[0],
            evaluation_rule=EvaluationRule(
                display_name="Pilot trace evaluation",
                action=TraceEvaluationRuleAction(
                    eval_id=trace_eval_id,
                    source=TracesEvalRunDataSource(agent_name=agent_name, max_traces=10),
                ),
                event_type=EvaluationRuleEventType.TIMER_TRIGGER,
                schedule=RecurrenceTrigger(interval=1, schedule=HourlyRecurrenceSchedule()),
                enabled=True,
            ),
        )
        while rule.provisioning_status in PENDING:
            time.sleep(5)
            rule = client.evaluation_rules.get(id=rule.id)
        return rule
    return client.evaluation_rules.create_or_update(
        id=RULE_IDS[1],
        evaluation_rule=EvaluationRule(
            display_name="Pilot continuous evaluation",
            action=ContinuousEvaluationRuleAction(eval_id=continuous_eval_id, max_hourly_runs=20),
            event_type=EvaluationRuleEventType.RESPONSE_COMPLETED,
            filter=EvaluationRuleFilter(agent_name=agent_name),
            enabled=True,
        ),
    )


def disable_rules(project_endpoint):
    """Teardown: stop rule spend without deleting history. Applied asynchronously."""
    client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
    for rule_id in RULE_IDS:
        try:
            rule = client.evaluation_rules.get(id=rule_id)
        except Exception:  # noqa: BLE001 - rule never created
            continue
        rule.enabled = False
        client.evaluation_rules.create_or_update(id=rule_id, evaluation_rule=rule)
