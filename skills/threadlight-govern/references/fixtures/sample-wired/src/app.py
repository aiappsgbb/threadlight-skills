"""Sample governed agent entry-point (FIXTURE — governed).

Governance is enforced through the *real* Agent Governance Toolkit, two ways:

  1. A committed ``policy.yaml`` (real agent-governance-toolkit schema) that CI
     gates with ``agt lint-policy`` + ``agt verify`` — see
     ``.github/workflows/governance.yml``. This is the always-on gate.
  2. Optionally, the real ``agent_compliance`` evaluators called in the agent's
     own request path for defence-in-depth.

No in-process "governance middleware" is imported — enforcement is proven at CI
time via the committed policy + attestation, not asserted by a runtime shim.
"""
from agent_framework import ChatAgent

try:  # optional, real: pip install "agent-governance-toolkit[core]"
    from agent_compliance import PromptDefenseEvaluator, PromptDefenseConfig
except ImportError:  # governance still enforced at CI time via `agt verify`
    PromptDefenseEvaluator = None
    PromptDefenseConfig = None


def build_agent() -> ChatAgent:
    return ChatAgent(name="contoso-pilot", instructions="...")


def guard_prompt(prompt: str) -> bool:
    """Defence-in-depth: reject a prompt that fails the prompt-defense grade.

    Optional — returns True (allow) when the evaluator is not installed, because
    the committed policy + `agt verify` attestation remain the load-bearing gate.
    """
    if PromptDefenseEvaluator is None:
        return True
    report = PromptDefenseEvaluator(PromptDefenseConfig()).evaluate(prompt)
    return not report.is_blocking(min_grade="C")
