from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient


def wire_continuous_eval(project_endpoint, thread, run, evaluators, app_insights_connection_string):
    client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
    return client.evaluations.create_agent_evaluation(
        thread=thread,
        run=run,
        evaluators=evaluators,
        app_insights_connection_string=app_insights_connection_string,
    )
