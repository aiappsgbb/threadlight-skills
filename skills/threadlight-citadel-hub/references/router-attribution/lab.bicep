// Router attribution lab for threadlight-citadel-hub (#220).
//
// Minimal, disposable reproduction of the hub's model path, used to prove:
//   1. the served-model token metrics (frag-set-llm-served-model.xml) reach
//      Application Insights with a `servedModel` dimension;
//   2. the hub's header-strip fragment (passed in from the pinned accelerator)
//      forwards `Foundry-Features: ModelRouterControls=V1Preview` to Foundry.
// It is NOT the hub: no access contracts, no backend pools, no private network.
// Deploy into a disposable resource group and delete it after the evidence.
//
//   az deployment group create -g <rg> -f lab.bicep \
//     -p stripHeadersFragmentXml=@<accelerator>/bicep/infra/modules/apim/policies/frag-strip-backend-headers.xml
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param publisherEmail string = 'noreply@example.com'
@description('Raw XML of the accelerator fragment frag-strip-backend-headers.xml at the pinned commit.')
param stripHeadersFragmentXml string
@allowed([ 'GlobalStandard', 'DataZoneStandard' ])
param modelSku string = 'DataZoneStandard'
param capacity int = 20

var token = uniqueString(resourceGroup().id)
var subset = [
  { name: 'gpt-6-luna', version: '2026-09-22' }
  { name: 'gpt-6.1-terra', version: '2026-10-26' }
]
var roleOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: law.id }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: 'ais-${token}'
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: { customSubDomainName: 'ais-${token}', disableLocalAuth: true, publicNetworkAccess: 'Enabled' }
}

@batchSize(1)
resource models 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = [for m in subset: {
  parent: account
  name: m.name
  sku: { name: modelSku, capacity: capacity }
  properties: { model: { format: 'OpenAI', name: m.name, version: m.version } }
}]

resource router 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: 'model-router'
  sku: { name: modelSku, capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'model-router', version: '2025-11-18' }
    routing: { mode: 'balanced', models: [for m in subset: { format: 'OpenAI', name: m.name, version: m.version }] }
  }
  dependsOn: [ models ]
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-${token}'
  location: location
  sku: { name: 'BasicV2', capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: { publisherEmail: publisherEmail, publisherName: 'threadlight lab' }
}

resource apimToFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, apim.id, roleOpenAIUser)
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleOpenAIUser)
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appi'
  properties: { loggerType: 'applicationInsights', credentials: { instrumentationKey: appi.properties.InstrumentationKey } }
}

resource fragStrip 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'strip-backend-headers'
  properties: { format: 'rawxml', value: stripHeadersFragmentXml }
}

resource fragServed 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'set-llm-served-model'
  properties: { format: 'rawxml', value: loadTextContent('../policies/frag-set-llm-served-model.xml') }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'llm'
  properties: {
    displayName: 'LLM (router lab)'
    path: 'openai/v1'
    protocols: [ 'https' ]
    serviceUrl: 'https://${account.properties.customSubDomainName}.openai.azure.com/openai/v1'
    subscriptionRequired: true
  }
}

resource opPost 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'post-any'
  properties: { displayName: 'POST any', method: 'POST', urlTemplate: '/*' }
}

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '''<policies>
  <inbound>
    <base />
    <set-variable name="requestedModel" value="@{ try { var b = context.Request.Body.As<JObject>(preserveContent: true); return (string)b["model"] ?? ""; } catch { return ""; } }" />
    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />
    <include-fragment fragment-id="strip-backend-headers" />
  </inbound>
  <backend><base /></backend>
  <outbound>
    <base />
    <include-fragment fragment-id="set-llm-served-model" />
  </outbound>
  <on-error><base /></on-error>
</policies>'''
  }
  dependsOn: [ fragStrip, fragServed ]
}

resource diag 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    sampling: { samplingType: 'fixed', percentage: 100 }
    metrics: true
  }
}

output APIM_GATEWAY string = apim.properties.gatewayUrl
output APIM_NAME string = apim.name
output APPI_APP_ID string = appi.properties.AppId
output ACCOUNT_NAME string = account.name
