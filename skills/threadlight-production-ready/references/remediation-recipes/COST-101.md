---
kind: repo-edit
summary: Deploy budget alert resource on target RG (live tier-3 check)
target_file: infra/budget.bicep
edit_type: insert
---

## Target file

`infra/budget.bicep` (or a dedicated budget module in your Bicep scaffold).

## Edit type

`insert` — scaffold and deploy the budget resource. This is a **tier-3 live check** — the assessor verifies the resource exists in Azure via `az consumption budget list`.

## Edit recipe

This recipe depends on **COST-002** being complete (budget declared in SPEC § 10 and Bicep drafted).

### Step 1: Ensure `infra/budget.bicep` exists

If not already created in COST-002, create `infra/budget.bicep`:

```bicep
param location string = resourceGroup().location
param budgetName string = 'threadlight-monthly-cap'
param budgetAmountEur int = 5000    // monthly cap from SPEC § 10
param alertThresholdPercent int = 80 // fires at 80% utilization
param ownerEmail string               // FinOps owner from SPEC § 10

resource budget 'Microsoft.Consumption/budgets@2021-10-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: budgetAmountEur
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: utcNow('yyyy-MM-01')
    }
    notifications: {
      actualThreshold: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: alertThresholdPercent
        contactEmails: [
          ownerEmail
        ]
        locale: 'en-us'
      }
    }
    forecastedNotifications: {
      forecastedThreshold: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: alertThresholdPercent
        contactEmails: [
          ownerEmail
        ]
        locale: 'en-us'
      }
    }
  }
}

output budgetId string = budget.id
```

### Step 2: Deploy via `azd up` or manual ARM deployment

Include `infra/budget.bicep` in your main ARM template, or deploy standalone:

```bash
az deployment group create \
  --resource-group <TARGET-RG> \
  --template-file infra/budget.bicep \
  --parameters \
    budgetName=threadlight-monthly-cap \
    budgetAmountEur=5000 \
    alertThresholdPercent=80 \
    ownerEmail=<FinOps-email-from-SPEC>
```

### Step 3: Verify in Azure

Check that the budget resource exists and alerts are configured:

```bash
az consumption budget list \
  --resource-group <TARGET-RG> \
  --subscription <TARGET-SUB>
```

Expected output: 1+ budget(s) with status `"notifications": {...}` configured.

## Verification

Re-run threadlight with **tier 3 enabled** (Cost Management Reader role on target subscription):

```bash
python3 scripts/production_ready.py \
  --target-rg <RG> \
  --target-sub <SUB> \
  --tiers 0,1,2,3
```

COST-101 should flip to `pass` once:
- `az consumption budget list -g <RG>` returns ≥ 1 budget.
- The budget contains active notification rules.
- The assessor records evidence: `{N} budget(s) wired`.

If still failing after Bicep deploy:
- Verify role assignment: `az role assignment list --scope /subscriptions/<SUB>`
- Confirm Cost Management Reader role is assigned.
- Re-run `azd deploy` or re-run the ARM deployment to ensure the budget resource reconciles.
