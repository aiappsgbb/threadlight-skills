# Running the Microsoft AI Red Teaming Agent

This reference explains how to produce the raw scan summary that
`threadlight-redteam` ingests. The validator itself is stdlib-only and does not
call Azure; the scan is run by the Microsoft AI Red Teaming Agent in the pilot's
environment, then summarized and committed as `redteam/scan-result.json`.

## When to run

Run the scan twice in the path2production flow:

1. **Pre-deploy / staging:** after the agent, retrieval sources, tools, and
   guardrails are assembled but before customer production traffic.
2. **Post-deploy:** after the deployed endpoint and policy are live, especially
   after material changes to prompts, tools, retrieval data, content filters, or
   AGT policy.

## Keyless setup

Use Entra authentication through `DefaultAzureCredential`. In local developer
sessions this normally resolves Azure CLI credentials; in CI use OIDC / workload
identity federation. Do not commit keys or service-principal secrets.

```bash
python -m venv .venv
. .venv/bin/activate
pip install 'azure-ai-evaluation[redteam]' azure-identity
```

## Minimal scan pattern

The Azure AI Evaluation SDK exposes the AI Red Teaming Agent through `RedTeam`.
The agent is PyRIT-backed and can run multiple attack strategies against a
single-turn text target.

```python
import asyncio
import json
from azure.identity import DefaultAzureCredential
from azure.ai.evaluation.red_team import RedTeam, RiskCategory, AttackStrategy

azure_ai_project = {
    "subscription_id": "<subscription-id>",
    "resource_group_name": "<resource-group>",
    "project_name": "<project-name>",
}

async def target(messages):
    # Call the deployed agent endpoint. Return text only for the red-team scan.
    return await call_agent(messages[-1]["content"])

async def main():
    red_team_agent = RedTeam(
        azure_ai_project=azure_ai_project,
        credential=DefaultAzureCredential(),
        risk_categories=[
            RiskCategory.Violence,
            RiskCategory.HateUnfairness,
            RiskCategory.Sexual,
            RiskCategory.SelfHarm,
        ],
        num_objectives=5,
    )
    result = await red_team_agent.scan(
        target=target,
        scan_name="threadlight-preprod-safety-scan",
        attack_strategies=[
            AttackStrategy.Base64,
            AttackStrategy.UnicodeConfusable,
            AttackStrategy.Crescendo,
        ],
    )
    print(json.dumps(result, indent=2, default=str))

asyncio.run(main())
```

The Microsoft Learn article for running the AI Red Teaming Agent locally shows
`RedTeam(..., credential=DefaultAzureCredential())`, risk categories,
`num_objectives`, and attack strategies. Keep the full raw output in your CI or
secure evidence store if needed; commit only the compact summary below unless
your engagement requires detailed transcripts.

## Commit this scan summary

Normalize the run output to `redteam/scan-result.json`:

```json
{
  "tool": "ai-red-teaming-agent",
  "captured_at": "2026-06-22T16:00:00Z",
  "attack_success_rate": {
    "jailbreak": 0.0,
    "prompt_injection": 0.0,
    "indirect_attack": 0.0,
    "exfiltration": 0.0,
    "harmful_content": 0.0
  },
  "num_attacks": 120,
  "strategies": ["base64", "unicode", "crescendo"]
}
```

Then emit threadlight evidence:

```bash
python3 skills/threadlight-redteam/scripts/redteam_check.py --target . --emit --gate
```

This writes:

- `specs/redteam-manifest.json` — machine contract for pillar 7
- `docs/redteam-report.md` — human safety report for review

## Safety and data handling

- Do not commit secrets, credentials, customer confidential prompts, or detailed
  attack transcripts unless the repository is explicitly approved for that data.
- The threadlight manifest needs rates, counts, category coverage, strategy
  names, and timestamps — not prompt payloads.
- Treat retrieved documents, email, tickets, and other tool outputs as untrusted
  context for indirect prompt-injection / XPIA probes.
- Re-run after mitigation. A fixed policy without a fresh adversarial scan does
  not prove the attack no longer succeeds.
