// Model gateway: Azure API Management Standard v2 in front of the Foundry
// project endpoint (AI gateway pattern). Inbound only through a private
// endpoint; outbound through VNet integration to the Foundry private endpoint.
// Standard v2 is the lowest tier with both (Developer/Premium classic need VNet
// injection): https://learn.microsoft.com/azure/api-management/virtual-network-concepts
// Public network access can be disabled only on an existing instance with a
// private endpoint, so a second module call turns it off after the endpoint:
// https://learn.microsoft.com/azure/api-management/private-endpoint#optionally-disable-public-network-access
//
// The route is a pass-through to the project endpoint: the agent keeps its
// Responses wire format and its own Microsoft Entra token (audience
// https://ai.azure.com). The gateway validates that token, then calls Foundry
// with ITS managed identity, so model access can be governed (quotas, logging,
// routing) at the gateway. The agent's base URL ends in /openai/v1: with
// ProviderConfig(type="azure") github-copilot-sdk 1.0.1 keeps only the ORIGIN of
// any other base URL and posts to /openai/v1/responses, so a "/models" prefix
// was dropped and APIM answered 404 (2026-11-11 live run and local probe).
param location string
param token string
param tags object
param apimSubnetId string
param peSubnetId string
param apimZoneId string
param projectEndpoint string
param foundryAccountName string
param publisherEmail string
param disablePublicAccess bool = true

var roleFoundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var name = 'apim-${token}'
var sku = { name: 'StandardV2', capacity: 1 }
var properties = {
  publisherEmail: publisherEmail
  publisherName: 'Threadlight pilot'
  virtualNetworkType: 'External'
  virtualNetworkConfiguration: { subnetResourceId: apimSubnetId }
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: sku
  identity: { type: 'SystemAssigned' }
  properties: union(properties, { publicNetworkAccess: 'Enabled' })
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource apimFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, apim.id, roleFoundryUser)
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'foundry-models'
  properties: {
    displayName: 'Foundry models (pass-through)'
    path: 'models'
    protocols: [ 'https' ]
    serviceUrl: projectEndpoint
    subscriptionRequired: false
    apiType: 'http'
  }
}

resource opPost 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'post-any'
  properties: { displayName: 'POST any', method: 'POST', urlTemplate: '/*' }
}

resource opGet 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'get-any'
  properties: { displayName: 'GET any', method: 'GET', urlTemplate: '/*' }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace('''<policies>
  <inbound>
    <base />
    <validate-azure-ad-token tenant-id="__TENANT_ID__" header-name="Authorization" failed-validation-httpcode="401" failed-validation-error-message="Unauthorized">
      <audiences><audience>https://ai.azure.com</audience></audiences>
    </validate-azure-ad-token>
    <authentication-managed-identity resource="https://ai.azure.com" />
    <set-header name="x-threadlight-gateway" exists-action="override"><value>apim</value></set-header>
  </inbound>
  <backend>
    <forward-request timeout="300" buffer-response="false" />
  </backend>
  <outbound>
    <base />
    <set-header name="x-threadlight-gateway" exists-action="override"><value>apim</value></set-header>
  </outbound>
  <on-error><base /></on-error>
</policies>''', '__TENANT_ID__', tenant().tenantId)
  }
}

module pe 'private-endpoint.bicep' = {
  name: 'pe-apim-${token}'
  params: {
    name: 'pe-apim-${token}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: apim.id
    groupId: 'Gateway'
    zoneIds: [ apimZoneId ]
  }
}

module lockdown 'apim-lockdown.bicep' = if (disablePublicAccess) {
  name: 'apim-lockdown-${token}'
  params: {
    name: name
    location: location
    tags: tags
    sku: sku
    properties: properties
    privateEndpointId: pe.outputs.id
  }
  dependsOn: [ policy, opPost, opGet, apimFoundryUser ]
}

output name string = apim.name
output gatewayUrl string = '${apim.properties.gatewayUrl}/models/openai/v1'
