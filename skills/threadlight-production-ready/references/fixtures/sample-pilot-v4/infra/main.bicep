// sample-pilot-v4 infra (deliberately incomplete — only AGT pillar is meant to pass)
// missing: Key Vault, App Insights, Log Analytics, private endpoints, budget

module network 'modules/network.bicep' = { name: 'network' }

resource ai 'Microsoft.CognitiveServices/accounts@2024-06-01' = {
  name: 'sample-ai-v4'
  identity: { type: 'SystemAssigned' }
  properties: {
    publicNetworkAccess: 'Disabled'
  }
}
