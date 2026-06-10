param location string
param tags object
param uamiId string
param subnetId string

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'foundry-pilot-citadel'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uamiId}': {} }
  }
  properties: {
    publicNetworkAccess: 'Disabled'
    customSubDomainName: 'foundry-pilot-citadel'
    disableLocalAuth: true
  }
}

// MDL-001: pinned model deployment (no "latest")
resource gpt 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: 'gpt-4o-2024-08-06'
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

resource foundryPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-foundry'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: 'pe-foundry-conn'
        properties: {
          privateLinkServiceId: foundry.id
          groupIds: [ 'account' ]
        }
      }
    ]
  }
}

output foundryId string = foundry.id
