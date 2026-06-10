"""Sample Citadel-spoke pilot agent.

Loads AGT in-process middleware (foundry-agt) and references OWASP ASI 2026.
Uses App Insights telemetry sink for AGT denials.
"""
from agt import policy  # foundry-agt v3.7
from opentelemetry import trace  # otel telemetry sink
from foundry_agt.shields import asi_verifier  # OWASP ASI 2026 verifier

tracer = trace.get_tracer(__name__)
policy.load("policy.yaml")
