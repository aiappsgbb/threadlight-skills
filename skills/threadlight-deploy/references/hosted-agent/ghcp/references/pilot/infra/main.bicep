// =============================================================================
// CANONICAL PILOT INFRA: GHCP hosted agent (Foundry) + mock MCP server (ACA)
//
// Copy to `infra/main.bicep` together with `main.parameters.json`. It is
// complete for a demo-sandbox pilot. Do not hand-add the Foundry account,
// project, model, capability hosts or role assignments: they are already here.
// Live-proven shape (westus3, gpt-5.4-mini GlobalStandard, azd 1.35 +
// azure.ai.agents extension, remote ACR builds).
//
// Every role assignment uses the role-definition GUID, because display names
// drift. "Azure AI User" is now shown as "Foundry User", and
// `az role assignment create --role "Azure AI User"` no longer resolves.
// =============================================================================
targetScope = 'resourceGroup'

@description('azd environment name')
param environmentName string

param location string = resourceGroup().location

@description('Extra tags (for example owner, purpose, disposable). Merged with azd-env-name.')
param tags object = {}

@description('Deployer principal object ID (azd sets AZURE_PRINCIPAL_ID). Gets Foundry User at project scope.')
param principalId string = ''

@allowed([ 'User', 'ServicePrincipal', 'Group' ])
param principalType string = 'User'

@description('Placeholder image for the MCP container app. `azd deploy mcp` replaces it.')
param mcpImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

param modelName string = 'gpt-5.4-mini'
param modelVersion string = '2026-03-17'

@description('GlobalStandard pay-as-you-go capacity in thousands of TPM (billed per token, not reserved). The GHCP runtime bursts ~10 calls of ~8K tokens per case; 50 or less returned 429 in live runs.')
param modelCapacity int = 300

var token = uniqueString(subscription().id, resourceGroup().id, environmentName)
var allTags = union(tags, { 'azd-env-name': environmentName })

// Role definition GUIDs (pinned)
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleAcrRepositoryReader = 'b93aa761-3e63-49ed-ac28-beffa264f7ac'
var roleFoundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d' // formerly "Azure AI User"

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: allTags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  tags: allTags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: law.id }
}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${token}'
  location: location
  tags: allTags
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: allTags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource uamiAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, uami.id, roleAcrPull)
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: allTags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: law.properties.customerId, sharedKey: law.listKeys().primarySharedKey }
    }
  }
}

resource mcp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-mcp-${token}'
  location: location
  tags: union(allTags, { 'azd-service-name': 'mcp' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${uami.id}': {} } }
  dependsOn: [ uamiAcrPull ]
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: true, targetPort: 8080, transport: 'auto' }
      registries: [ { server: acr.properties.loginServer, identity: uami.id } ]
    }
    template: {
      containers: [ {
        name: 'mcp'
        image: mcpImage
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [ { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString } ]
      } ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'ais-${token}'
  location: location
  tags: allTags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'ais-${token}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    allowProjectManagement: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: 'proj-${token}'
  location: location
  tags: allTags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: environmentName, description: 'Threadlight pilot' }
}

// The account-level capability host must exist before the project-level one.
resource accountCapabilityHost 'Microsoft.CognitiveServices/accounts/capabilityHosts@2025-04-01-preview' = {
  parent: account
  name: 'default'
  properties: { capabilityHostKind: 'Agents' }
}

// ProjectCapabilityHostProperties has no capabilityHostKind (BCP037); the kind is inherited
// from the account-level host. Basic setup: platform-managed stores, no connections.
resource projectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: 'default'
  properties: {}
  dependsOn: [ accountCapabilityHost ]
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: modelName
  sku: { name: 'GlobalStandard', capacity: modelCapacity }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
  dependsOn: [ project ]
}

// F-06: the project identity pulls the hosted-agent image from ACR.
resource projectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, project.id, roleAcrPull)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

resource projectRepositoryReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, project.id, roleAcrRepositoryReader)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrRepositoryReader)
  }
}

// The deployer needs a Foundry data-plane role at project scope before
// `azd deploy` creates the hosted agent. Subscription Owner alone returns 403.
resource deployerFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: project
  name: guid(project.id, principalId, roleFoundryUser)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

var projectEndpoint = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output FOUNDRY_PROJECT_ENDPOINT string = projectEndpoint
output AZURE_AI_PROJECT_ENDPOINT string = projectEndpoint
output AZURE_AI_PROJECT_ID string = project.id
output AZURE_AI_ACCOUNT_ID string = account.id
output AZURE_AI_ACCOUNT_NAME string = account.name
output AZURE_AI_PROJECT_NAME string = project.name
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = model.name
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output MCP_SERVER_FQDN string = mcp.properties.configuration.ingress.fqdn
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appi.properties.ConnectionString
