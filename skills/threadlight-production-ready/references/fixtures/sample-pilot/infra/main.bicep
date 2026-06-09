// sample-pilot infra (deliberately incomplete)
// missing: Key Vault, App Insights, Log Analytics, AGT policy, private endpoints, budget

module network 'modules/network.bicep' = { name: 'network' }

resource ai 'Microsoft.CognitiveServices/accounts@2024-06-01' = {
  name: 'sample-ai'
  identity: { type: 'SystemAssigned' }
  properties: {
    publicNetworkAccess: 'Disabled'
  }
}
