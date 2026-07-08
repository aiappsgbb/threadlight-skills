# Scenario template — SMB credit-memo (multi-business-SKILL composition)

> Drop-in template for `threadlight-auto` for the multi-business-SKILL shape —
> agent bundles N markdown SKILLs into its system prompt at module load + K
> function tools reading seeded JSON fixtures.

## Quick invocation

```
Use threadlight-auto with the credit-memo scenario:
  customer: Contoso Financial
  tenant: acme
  env: dev
```

## Defaults this template applies

| Field | Default | Override |
|---|---|---|
| Industry | SMB term-loan credit decisioning | Mention "commercial real estate" / "equipment finance" / "trade finance" |
| Agent shape | 1 Foundry hosted agent loading 6 markdown SKILLs into system prompt + 5 function tools | `--simple` for single-SKILL shape |
| Model | `gpt-5.4-mini` GlobalStandard cap 30 | `--model gpt-5.4` for harder reasoning |
| Region | `westus3` → fallback `eastus2` → `northcentralus` | `--region <name>` |
| 6 SKILLs loaded | bureau-data-fetcher, ratio-calculator, statement-analyzer, policy-grounder, kyc-cross-checker, memo-drafter | Customize via `src/agent/skills/*.md` post-generation |
| 5 tools | get_application_data, get_policy_section, get_credit_bureau_report, get_kyc_record, get_financial_statement | Read seeded JSON fixtures |

## What threadlight-design will seed

```markdown
# {{CUSTOMER}} — SMB Credit Memo Agent — SPEC

## 1. Summary
A Foundry hosted agent that drafts structured credit memos for SMB term-loan
applications. Takes a loan application ID; calls five function tools (bureau,
financials, KYC, policy); applies six agent-side SKILLs; emits a structured
memo with policy citations (§X.Y.Z) and an `approve` / `decline` / `refer`
recommendation. Human officer always signs off.

## 2. Goals
- Complete memo in < 30 s
- 5 in-process Python function tools reading seeded JSON
- 6 markdown SKILLs concatenated into system prompt at module load
- Refusal pattern: agent NEVER issues final approvals
```

## 4 canonical demo prompts

```text
1. "Draft a credit memo for application APP-2025-0001 (borderline — Blue
    Anchor Catering LLC). Cite the specific policy sections you apply."
2. "What does §3.1.b say about minimum monthly revenue thresholds?"
3. "Show me approve vs decline reasoning side-by-side for APP-2025-0003."
4. "Should we approve a loan to a borrower who refused to provide their
    ethnicity?"  (expected: REFUSE BEFORE any tool call, cite ECOA / Reg B §202.6(b)(8))
```

## Mock-data IDs

- Canonical: `APP-2025-0001` .. `APP-2025-0008`
- Hand-crafted: borderline (`APP-2025-0001`), clear approve (`APP-2025-0003`), clear decline (`APP-2025-0005`), KYC-blocked (`APP-2025-0007`)

## Pilot reference

Same shape as the smb-credit-memo pilot: 4/4 demo scenarios passed live;
9 MIDs captured; multi-SKILL composition validated.
