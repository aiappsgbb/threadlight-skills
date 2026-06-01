# Scenario template — Insurance auto-claim triage

> Drop-in template for `threadlight-auto` when the operator picks the
> "auto-claim-triage" path. Mirrors the proven shape from the
> 2026-05-30 contoso-claim-triage pilot (5/5 spec demo scenarios passed live;
> details in [`aiappsgbb/agentic-loop` SKILL Validation history row #9](https://github.com/aiappsgbb/agentic-loop/blob/main/skills/agentic-loop/SKILL.md)).

## Quick invocation

```
Use threadlight-auto with the auto-claim-triage scenario:
  customer: Contoso Mutual
  tenant: fruocco
  env: dev
```

…or freeform:

```
threadlight-auto: Build an auto-claim triage agent for Contoso Mutual
  using the auto-claim-triage scenario template
```

## Defaults this template applies

| Field | Default | Override |
|---|---|---|
| Industry | P&C auto-insurance carrier | Mention "homeowners" / "commercial auto" / "renters" |
| Model | `gpt-5.4-mini` GlobalStandard cap 30 | `--model gpt-4o-mini` or `--model gpt-5.4` |
| Region | `westus3` → fallback `eastus2` → `northcentralus` | `--region <name>` |
| Tools | 3 in-process Python tools (`lookup_policy`, `check_fraud_signals`, `estimate_repair_cost`) | Add `--mcp` to swap for an MCP backend |
| Persistence | None (stateless) | Add `--cosmos` to provision Cosmos + claim-history tool |
| Eval | 8-item smoke dataset, 2 graders | Out of the box |

## What threadlight-design will seed

```markdown
# {{CUSTOMER}} — Auto-Claim Triage Agent — SPEC

## 1. Summary
A Foundry hosted agent that triages incoming auto-insurance First Notice of
Loss (FNOL) reports. Takes free-text loss description + policy number; calls
three function tools (policy lookup, fraud-signal scoring, repair-cost
estimate); emits a structured triage report for a human adjuster: priority
(P0/P1/P2/blocked), fraud_risk (Low/Medium/High), recommended_action, and
a PII-masked one-paragraph summary.

## 2. Goals & Non-Goals
**Goals**
- Adjuster-ready triage report in < 10 seconds for typical FNOL
- 3 function tools wired via MAF: lookup_policy, check_fraud_signals, estimate_repair_cost
- OTel → App Insights day 1
- Keyless: UAMI + Entra RBAC

**Non-Goals**
- No customer-facing UI for v1
- No real policy admin system integration — deterministic mock data
- No HITL approval (out of scope; can layer in via threadlight-hitl-patterns)
- No VNet injection
```

## 5 canonical demo prompts

```text
1. "FNOL: Insured says they were rear-ended at a stoplight on Highway 7 at
    2pm yesterday. Vehicle is a 2022 Toyota RAV4, rear bumper crumpled,
    taillight broken, driveable. Policy POL-1003."
2. "FNOL: Vehicle stolen from a parking lot overnight. Discovered missing
    at 6am. No witnesses. 2020 Honda Civic, policy POL-7041."
3. "FNOL: Single-vehicle collision with deer at dawn. Hood and grille
    damaged. 2019 Ford F-150, policy POL-1005."
4. "FNOL: Driver says the other driver fled, no police report yet. Front-
    end damage. 2023 Tesla Model Y, policy POL-9998." (expected: blocked,
    policy_not_found)
5. "FNOL: Insured Maria Garcia, DOB 1984-03-12, reports her 2021 Subaru
    Outback was hit while parked. Policy POL-1011. Front passenger door
    dented." (expected: PII masking — "the insured" + no DOB in summary)
```

## Mock-data seeding rules (deterministic — referenced by threadlight-demo-data-factory)

- Policy number ending in **odd digit** → in-force, returns Policy
- Policy number ending in **even digit** → not found, returns None (drives policy-not-found path)
- Policy number first digit **7** → in-force, used by fraud_rules to inflate score

## Pilot reference

Field-tested via lean-toolkit on 2026-05-30; matches threadlight's 3-tool +
multi-business-SKILL composition pattern. Wallclock ~38 min end-to-end on
first attempt; subsequent runs ~15 min after MID fixes land.
