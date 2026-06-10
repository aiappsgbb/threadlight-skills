---
kind: repo-edit
summary: Declare budget thresholds in SPEC and Bicep
target_file: docs/SPEC.md
edit_type: insert
---

## Target file

Primary: `docs/SPEC.md` (§ 10). Secondary: `infra/budget.bicep` (optional — can be referenced from SPEC instead).

## Edit type

`insert` — add budget declaration to SPEC § 10; optionally scaffold `infra/budget.bicep` for live enforcement.

## Edit recipe

### Step 1: Update SPEC § 10

In the "Budget threshold" subsection of SPEC § 10, ensure these fields are present:

```markdown
### Budget threshold

- **Monthly budget cap:** EUR <amount> (e.g., EUR 5000)
- **Alert threshold:** Fires at 80% utilization (e.g., EUR 4000)
- **Cost-allocation tags required:** Yes (to isolate this pilot's spend)
- **Owner (FinOps):** <team or email>

### Spend anomaly detection

- **Method:** Azure Cost Management anomaly alert
- **Sensitivity:** Medium (default)
- **Action:** Notify <on-call team> via <Slack | email>
```

Verify keywords present: `budget`, `threshold`, `cost`, `alert`, or currency notation (e.g., `EUR 5000`).

### Step 2: (Optional) Scaffold `infra/budget.bicep`

If using Bicep for infrastructure-as-code, add or update `infra/budget.bicep`:

```bicep
param budgetName string = 'threadlight-monthly-cap'
param budgetAmount int = 5000  // EUR
param alertThreshold int = 4000 // 80% of monthly cap
param ownerEmail string = ''   // from SPEC § 10

resource budget 'Microsoft.Consumption/budgets@2021-10-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: utcNow('yyyy-MM-01')
    }
    notifications: {
      notificationByResourceGroupKey: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: (alertThreshold * 100) / budgetAmount  // e.g., 80
        contactEmails: [
          ownerEmail
        ]
      }
    }
  }
}
```

3. Deploy the Bicep:

```bash
az deployment group create \
  --resource-group <RG> \
  --template-file infra/budget.bicep \
  --parameters ownerEmail=<FinOps-email>
```

## Verification

Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`.

COST-002 should flip to `pass` once:
- SPEC or Bicep contains budget keywords: `budget`, `cost-alert`, `threshold`, or currency notation (e.g., `EUR 1000`).
- The assessor searches SPEC text and Bicep files for these patterns (case-insensitive).

If Bicep is deployed, verify the resource exists:
```bash
az resource list -g <RG> --resource-type Microsoft.Consumption/budgets
```
