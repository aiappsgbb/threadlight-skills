"""Sample agent entry-point (FIXTURE — bare / ungoverned).

No AGT middleware, no policy artefact, no verifier evidence. Used to assert
the validator reports must-fix gaps.
"""
from agent_framework import ChatAgent


def build_agent() -> ChatAgent:
    return ChatAgent(name="contoso-pilot", instructions="...")
