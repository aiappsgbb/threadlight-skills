---
kind: repo-edit
summary: Configure audit trail destination (Storage / Cosmos / App Insights)
target_file: infra/audit.bicep
edit_type: insert
---

## Target file
`infra/audit.bicep` (create if missing) or `infra/main.bicep` if all resources are centralized. The check looks for `Microsoft.Storage/storageAccounts`, `Microsoft.Sql/servers`, or `Microsoft.DocumentDB` in compiled Bicep.

## Edit type
`insert`

## Edit recipe
1. Create or locate `infra/audit.bicep` and declare a durable storage resource for audit trails. Choose one of:

   **Option A: Azure Storage (Table Storage)**
   ```bicep
   resource auditStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
     name: 'audit${uniqueString(resourceGroup().id)}'
     location: location
     kind: 'StorageV2'
     sku: {
       name: 'Standard_LRS'
     }
     properties: {
       accessTier: 'Hot'
       immutableStorageWithVersioning: {
         enabled: true
       }
     }
   }

   resource auditTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
     parent: auditStorage
     name: 'default/hitlAuditTrail'
   }
   ```

   **Option B: Azure Cosmos DB (document)**
   ```bicep
   resource auditCosmos 'Microsoft.DocumentDB/databaseAccounts@2023-11-15' = {
     name: 'audit-${uniqueString(resourceGroup().id)}'
     location: location
     kind: 'GlobalDocumentDB'
     properties: {
       databaseAccountOfferType: 'Standard'
     }
   }
   ```

2. Add to `src/appsettings.json`:

   ```json
   {
     "AuditTrail": {
       "Destination": "AzureStorage",
       "StorageAccountName": "<name>",
       "TableName": "hitlAuditTrail",
       "ConnectionString": "@Microsoft.KeyVault(SecretUri=https://<kv>/secrets/audit-connection/)"
     }
   }
   ```

3. Reference the audit resource in your main Bicep and pass the connection string (or managed identity) to the app config / Key Vault.

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. HITL-003 should flip from `fail` to `pass` once the Storage/Cosmos/SQL resource is found in compiled ARM.
