---
kind: repo-edit
summary: Deploy audit storage resource to target resource group
target_file: infra/audit.bicep
edit_type: insert
---

## Target file
`infra/audit.bicep` (created or extended in HITL-003). Deployment via `azd up` or `az deployment group create`.

## Edit type
`insert`

## Edit recipe
1. Ensure `infra/audit.bicep` (from HITL-003) is referenced in `infra/main.bicep`:

   ```bicep
   module auditResources 'audit.bicep' = {
     name: 'auditDeployment'
     params: {
       location: location
     }
   }
   ```

2. Update `azure.yaml` to ensure the infrastructure deployment includes the audit resources:

   ```yaml
   infra:
     path: infra/main.bicep
   ```

3. Deploy:

   ```bash
   azd up
   ```

   Or manually:

   ```bash
   az deployment group create \
     --resource-group <RG> \
     --template-file infra/main.bicep \
     --subscription <SUB>
   ```

4. Verify the resource was created:

   ```bash
   az storage account list --resource-group <RG> --subscription <SUB>
   ```

   Should list the audit storage account.

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. HITL-101 should flip from `fail` to `pass` (live check confirms storage account exists in target RG).

If still failing, check:
1. Bicep compiled successfully: `az bicep build --file infra/main.bicep`
2. Deployment reached the RG: `az resource list -g <RG> --query "[?type=='Microsoft.Storage/storageAccounts']"`
