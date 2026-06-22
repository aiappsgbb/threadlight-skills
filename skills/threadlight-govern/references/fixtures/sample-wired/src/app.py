"""Sample governed agent entry-point (FIXTURE — wired).

Demonstrates the AGT in-process middleware wired at the container boundary.
"""
from agt import apply_governance, create_governance_middleware
from agent_framework import ChatAgent


def build_agent() -> ChatAgent:
    agent = ChatAgent(name="contoso-pilot", instructions="...")
    # PROTECT: governance middleware enforces the committed policy on every
    # tool call (action-level), keyless via DefaultAzureCredential.
    return apply_governance(
        agent,
        middleware=create_governance_middleware(policy_path="policy.yaml"),
    )
