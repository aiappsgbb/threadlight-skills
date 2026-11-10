// =============================================================================
// CANONICAL PILOT INFRA: GHCP hosted agent (Foundry) + mock MCP server (ACA)
//
// Copy to `infra/main.bicep` together with `main.parameters.json`. It is
// complete for a demo-sandbox pilot. Do not hand-add the Foundry account,
// project, model or role assignments: they are already here. It creates no
// capability host (see the note above the model deployment).
// Live-proven shape (westus3, gpt-5.4-mini GlobalStandard, azd 1.35 +
// azure.ai.agents extension, remote ACR builds) while it still created
// capability hosts. They were removed on 2026-11-07 to follow Microsoft Learn;
// that change has not been re-proven live yet.
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

@description('Deployer principal object ID (azd sets AZURE_PRINCIPAL_ID). Gets Foundry User at project and account scope.')
param principalId string = ''

@allowed([ 'User', 'ServicePrincipal', 'Group' ])
param principalType string = 'User'

@description('Placeholder image for the MCP container app. `azd deploy mcp` replaces it.')
param mcpImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('MCP container app minimum replicas. 0 (default) scales to zero when idle: no always-on cost, but the first tool call after idle waits for a cold start. Set 1 (azd env set MCP_MIN_REPLICAS 1) only to keep a warm replica for a live demo, then set it back to 0.')
@minValue(0)
@maxValue(2)
param mcpMinReplicas int = 0

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
      scale: { minReplicas: mcpMinReplicas, maxReplicas: 2 }
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

// No capability hosts: the current hosted-agent backend provisions its own
// infrastructure, and Microsoft Learn says to remove capability host creation
// from provisioning scripts
// (https://learn.microsoft.com/azure/foundry/agents/how-to/migrate-hosted-agent-preview).
// Create capability hosts only for a bring-your-own-resources (standard) setup.

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

// Foundry evaluations (`python evals/run_evals.py`) invoke the judge model
// deployment on the ACCOUNT endpoint (POST /openai/evals). A project-only
// grant returned 401 "lacks ... OpenAI/evals/write" in the 2026-11-07 live run.
// Learn: "If an evaluation invokes a model deployment, assign Foundry User at
// the Foundry account scope" and "Run agent evaluations with the Azure
// Developer CLI: your identity, Foundry User, the Foundry resource"
// (https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-permissions).
resource deployerFoundryUserAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: account
  name: guid(account.id, principalId, roleFoundryUser)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

// Foundry injects APPLICATIONINSIGHTS_CONNECTION_STRING into hosted agents only
// when monitoring is configured on the project
// (https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry).
// This is the AppInsights project connection the portal "Traces > Connect"
// button creates; a project allows one connection of this category. Shape:
// https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/accounts/projects/connections
resource projectAppInsights 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: appi.name
  properties: {
    category: 'AppInsights'
    target: appi.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: { key: appi.properties.ConnectionString }
    metadata: { ApiType: 'Azure', ResourceId: appi.id }
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
// Read by scripts/record_deploy_evidence.py (telemetry evidence for the Foundry package gate).
output APPLICATIONINSIGHTS_RESOURCE_ID string = appi.id
output AZURE_AI_APPINSIGHTS_CONNECTION_ID string = projectAppInsights.id
