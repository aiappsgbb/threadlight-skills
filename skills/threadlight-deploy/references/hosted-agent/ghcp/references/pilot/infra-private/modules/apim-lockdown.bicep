// Second PUT of the same API Management instance with public network access
// disabled. Allowed only once the instance exists and has a private endpoint.
param name string
param location string
param tags object
param sku object
param properties object
#disable-next-line no-unused-params
param privateEndpointId string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: sku
  identity: { type: 'SystemAssigned' }
  properties: union(properties, { publicNetworkAccess: 'Disabled' })
}
