param location string
param tags object
param uamiPrincipalId string

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: 'kv-pilot-citadel'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableSoftDelete: true
    enablePurgeProtection: true
    enableRbacAuthorization: true
    publicNetworkAccess: 'Disabled'
  }
}

// SEC-006: RBAC role assignment (Key Vault Secrets User) on the KV scope
resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, uamiPrincipalId, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output kvId string = kv.id
