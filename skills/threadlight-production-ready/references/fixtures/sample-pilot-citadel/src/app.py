"""Sample Citadel-spoke pilot agent.

Governance is authored as a committed AGT policy (``policy.yaml``), linted and
verified in CI (see ``.github/workflows/governance.yml``). The agent optionally
enforces that policy at runtime via the Agent Governance Toolkit's
``agent_compliance`` evaluators; App Insights is the telemetry sink for denials.
"""
from __future__ import annotations

from opentelemetry import trace  # otel telemetry sink

try:  # optional runtime enforcement — the CI gate is the load-bearing control
    from agent_compliance import PromptDefenseEvaluator, PromptDefenseConfig
except ImportError:  # agent-governance-toolkit not installed in this environment
    PromptDefenseEvaluator = None
    PromptDefenseConfig = None

tracer = trace.get_tracer(__name__)


def build_prompt_defense():
    """Return an optional prompt-defense evaluator when the toolkit is present.

    Governance is proven at build time by ``agt verify`` in CI; this runtime
    hook is a defence-in-depth convenience, not the primary control.
    """
    if PromptDefenseEvaluator is None:
        return None
    return PromptDefenseEvaluator(PromptDefenseConfig())
