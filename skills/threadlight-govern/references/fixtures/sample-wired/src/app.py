"""Legacy assessment-only fixture retained for negative regression tests.

Committed policy, CI checks and optional prompt grading are not runtime enforcement.
This example has no mandatory interception or effect authorization and must not
be used as a production governed-agent template.
"""
from agent_framework import ChatAgent

try:  # optional, real: pip install "agent-governance-toolkit[core]"
    from agent_compliance import PromptDefenseEvaluator, PromptDefenseConfig
except ImportError:  # Legacy optional grading, not an enforcement fallback.
    PromptDefenseEvaluator = None
    PromptDefenseConfig = None


def build_agent() -> ChatAgent:
    return ChatAgent(name="contoso-pilot", instructions="...")


def guard_prompt(prompt: str) -> bool:
    """Legacy optional prompt grading, intentionally insufficient for governance.

    Returns True when the evaluator is absent. Policy files and CI do not make
    that permissive behavior a governed runtime.
    """
    if PromptDefenseEvaluator is None:
        return True
    report = PromptDefenseEvaluator(PromptDefenseConfig()).evaluate(prompt)
    return not report.is_blocking(min_grade="C")
