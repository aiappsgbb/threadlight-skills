# AGT middleware wiring snippet

Wire the in-process Agent Governance Toolkit middleware at the agent's
container boundary so the committed policy is enforced on **every tool call**.
Keyless — uses `DefaultAzureCredential`. This mirrors remediation recipe
`AGT-001`.

## Python (Microsoft Agent Framework)

```python
from agt import apply_governance, create_governance_middleware
from agent_framework import ChatAgent

def build_agent() -> ChatAgent:
    agent = ChatAgent(name="my-pilot", instructions="...")
    # PROTECT: enforce policy.yaml on every action, in-process (~8-12µs/eval).
    return apply_governance(
        agent,
        middleware=create_governance_middleware(policy_path="policy.yaml"),
    )
```

Place the `apply_governance(...)` call at the single composition point where
the agent is constructed (the container entry-point: `src/app.py`,
`src/agent/main.py`, …) so no code path can bypass it.

## TypeScript

```ts
import { applyGovernance, createGovernanceMiddleware } from "@foundry/agt";
import { ChatAgent } from "@microsoft/agent-framework";

export function buildAgent(): ChatAgent {
  const agent = new ChatAgent({ name: "my-pilot", instructions: "..." });
  return applyGovernance(agent, {
    middleware: createGovernanceMiddleware({ policyPath: "policy.yaml" }),
  });
}
```

## Verify

```bash
agt verify --strict          # → docs/agt-verifier-report.md (OWASP ASI 2026)
python3 scripts/govern_check.py --target . --emit   # refresh govern-manifest.json
```

A clean `agt verify --strict` produces the committed evidence that
`threadlight-govern` reads as `verifier_artefact_present`, and
`threadlight-production-ready` reads to flip pillars 2 + 7 to verified.
