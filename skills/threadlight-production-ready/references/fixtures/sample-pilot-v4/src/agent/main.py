"""Sample pilot v4 agent entrypoint — demonstrates v4 import shape.

This file exists so threadlight's `_detect_agt_profile` 'auto' mode can
detect v4 via the new module path `agent_governance_toolkit_core`.
"""
from __future__ import annotations

from agent_governance_toolkit_core import GovernanceKernel, load_policy
from agent_governance_toolkit_runtime import maf_adapter


def build_agent():
    policy = load_policy("./policies/governance.yaml")
    kernel = GovernanceKernel(policy=policy)
    return maf_adapter.wrap(kernel)


if __name__ == "__main__":
    agent = build_agent()
    print("AGT v4 governance kernel wired:", agent)
