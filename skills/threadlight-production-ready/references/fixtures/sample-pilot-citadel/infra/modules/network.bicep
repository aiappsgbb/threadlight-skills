@description('Region for all resources')
param location string
param tags object

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-pilot-citadel'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [ '10.20.0.0/16' ]
    }
    subnets: [
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: '10.20.0.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: '10.20.1.0/23'
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

resource pe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-pilot-citadel'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: vnet.properties.subnets[0].id
    }
    privateLinkServiceConnections: [
      {
        name: 'pe-conn'
        properties: {
          privateLinkServiceId: resourceId('Microsoft.Storage/storageAccounts', 'stpilotcitadel')
          groupIds: [ 'blob' ]
        }
      }
    ]
  }
}

output peSubnetId string = vnet.properties.subnets[0].id
output acaEnvSubnetId string = vnet.properties.subnets[1].id
output vnetId string = vnet.id
