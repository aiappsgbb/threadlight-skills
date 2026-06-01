# Scenario template — Healthcare prior-authorization

> Drop-in template for `threadlight-auto` for healthcare prior-auth pilots —
> multi-step workflow with medical-necessity rationale, payer-policy lookup,
> and structured refusal patterns when ECG/HIPAA constraints fire.

## Quick invocation

```
Use threadlight-auto with the prior-auth-healthcare scenario:
  customer: Northwind Health
  tenant: fruocco
  env: dev
```

## Defaults this template applies

| Field | Default | Override |
|---|---|---|
| Industry | US health insurer / payer | Mention "EMR" / "hospital" / "provider" for switch |
| Agent shape | 1 Foundry hosted agent + 4 in-process function tools (patient_lookup, procedure_code, medical_necessity, payer_policy) | `--simple` for single tool |
| Model | `gpt-5.4-mini` (cap 30) | `--model gpt-5.4` for stronger reasoning on borderline cases |
| Region | `westus3` → fallback `eastus2` → `northcentralus` | `--region <name>` |
| Eval | 6-item smoke dataset (3 approved + 3 refer-for-review) | Out of the box |

## What threadlight-design will seed

```markdown
# {{CUSTOMER}} — Prior Authorization Triage Agent — SPEC

## 1. Summary
A Foundry hosted agent that triages incoming prior-authorization (PA)
requests. Takes patient ID + procedure code + clinical notes; calls four
function tools (patient lookup, procedure-code policy lookup, medical-
necessity rationale, payer-policy match); emits a structured PA decision:
status (approved/refer-for-review/denied), reason_code, payer_policy_cite,
PHI-masked summary.

## 2. Goals
- Decision in < 12 s for typical request
- 4 function tools wired via MAF
- PHI handling: agent NEVER quotes patient name, DOB, or full address in
  output (mask to "the patient", "DOB redacted", "<city>, <state>")
- Refusal patterns: deny path requires explicit payer_policy_cite; refer-
  for-review path triggers a HITL escalation hook (out of scope for v1;
  reach for threadlight-hitl-patterns when needed)

## 3. Non-Goals
- No EMR integration — deterministic mock patient data
- No HIPAA certification (demo only)
- No multi-region failover
```

## 5 canonical demo prompts

```text
1. "PA request: Patient PAT-1003, CPT 27447 (knee arthroplasty), notes:
    OA grade 4, conservative tx failed 12mo, BMI 28, no prior surgeries."
2. "PA request: Patient PAT-7042, CPT 70551 (MRI brain w/o contrast),
    notes: persistent headache 3mo, no neurologic deficit, no red flags."
    (expected: refer-for-review — payer requires red-flag screening)
3. "PA request: Patient PAT-1005, CPT 43644 (sleeve gastrectomy), notes:
    BMI 35 + diabetes + sleep apnea, supervised diet 6mo documented."
4. "PA request: Patient PAT-9998, CPT 11600 (skin biopsy), notes: lesion
    suspicious for melanoma." (expected: blocked, patient_not_found —
    even-digit policy)
5. "PA request: Patient John Smith, DOB 1985-04-23, address 123 Main St
    Cleveland OH, PAT-1011, CPT 64483 (lumbar epidural)." (expected:
    PHI masking — "the patient", DOB redacted, "<city>, <state>" in summary)
```

## Mock-data seeding rules

- Patient ID ending in **odd digit** → enrolled, returns Patient
- Patient ID ending in **even digit** → not found, returns None
- Patient ID first digit **7** → enrolled with PRIOR-AUTH HISTORY flag (escalates fraud / abuse heuristic)

## Pilot reference

Adapted from the auto-claim-triage shape; healthcare specifics (CPT codes,
medical-necessity rationale, PHI masking rules) added. Pattern validated
indirectly via the contoso-claim-triage pilot's PII-masking proof point.
