// =============================================================================
// CANONICAL PRIVATE PILOT INFRA: the same GHCP hosted agent (Foundry) and MCP
// server as `pilot/infra/main.bicep`, with every data plane private.
//
// `generate_only.py --profile private` copies this directory to `infra/`. What
// changes from the Fast-PoC infra (same agent code, MCP server and evals):
//   * Foundry account: public network access Disabled, hosted-agent VNet
//     injection set at FIRST create (it can't be added later), private endpoint.
//     https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks
//   * ACR Premium with a private endpoint. Its public endpoint is either off, or
//     (default, for `azd` remote builds) Deny-by-default with only the region's
//     ACR Tasks service-tag prefixes allowed: ACR quick builds can't reach a
//     registry with public access disabled unless they run on a dedicated agent
//     pool, which `azd` remote build doesn't select.
//     https://learn.microsoft.com/azure/container-registry/container-registry-private-endpoints
//   * MCP server in an INTERNAL Container Apps environment on the VNet.
//   * Application Insights ingestion only through an Azure Monitor Private Link
//     Scope (query stays open for the portal and evidence scripts).
//   * Optional model gateway: API Management Standard v2, private inbound and
//     VNet-integrated outbound (`modelGateway: 'apim'`).
//   * In-VNet operator host (jumpbox, Bastion Developer) for data-plane steps.
// Adapted from foundry-samples 11-private-network-basic-vnet (commit 2c5eac99).
// Capability hosts: network injection makes the platform create the ACCOUNT
// host (`<account>@aml_aiagentservice`); this template adds only the PROJECT host
// (no BYO connections), which Learn requires for evaluations in a VNet
// (https://learn.microsoft.com/azure/foundry/concepts/evaluation-virtual-network#evaluation-run-remains-in-progress-until-it-times-out)
// and which the official 11-private-network-basic-vnet template also creates.
// Capability hosts can't be updated in place.
// Role assignments use role-definition GUIDs (display names drift).
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

@description('Optional business Cosmos endpoint read by a design-owned MCP server (governed-write kit). Empty: no data-source settings.')
param returnsCosmosUrl string = ''

@description('MCP container app minimum replicas. 0 (default) scales to zero when idle.')
@minValue(0)
@maxValue(2)
param mcpMinReplicas int = 0

param modelName string = 'gpt-5.4-mini'
param modelVersion string = '2026-03-17'

@description('GlobalStandard pay-as-you-go capacity in thousands of TPM (billed per token, not reserved).')
param modelCapacity int = 300

@description('VNet address space (RFC 1918; the Agent Service rejects public and some reserved ranges).')
param vnetAddressPrefix string = '10.42.0.0/16'

@description('Comma-separated IPv4 prefixes allowed on the ACR public endpoint for remote builds (hooks/preprovision.sh fills the regional AzureContainerRegistry service tag). Empty: ACR public network access Disabled.')
param acrBuildAllowedPrefixes string = ''

@description('Model route: none (agent calls the Foundry private endpoint), apim (API Management Standard v2 gateway) or external (the customer\'s own gateway at modelGatewayUrl).')
@allowed([ 'none', 'apim', 'external' ])
param modelGateway string = 'none'

@description('external only: the customer gateway base URL, ending in /openai/v1, that passes /openai/v1/responses through to this project endpoint.')
param modelGatewayUrl string = ''

@description('Landing zone: an existing (vended) VNet with subnets snet-agent, snet-pe, snet-aca, snet-apim, snet-ops. Empty: this template creates the VNet, NAT and private DNS zones.')
param existingVnetId string = ''

@description('Landing zone: resource group (this subscription) of the central privatelink DNS zones, linked by the platform team to the VNet whose DNS serves the spoke. Empty: zones are created here and linked to the workload VNet (Azure-provided DNS).')
param dnsZonesResourceGroup string = ''

@description('Landing zone: VNet to link the Container Apps environment zone to (the hub with the DNS resolver). Empty: only the workload VNet.')
param dnsZoneLinkVnetId string = ''

@description('Data services selected in SPEC section 11c, provisioned with private endpoints.')
param enableAiSearch bool = false
param enableStorage bool = false
param enableKeyVault bool = false

@description('AI Search region when the VNet region has no capacity (empty: same region).')
param aiSearchLocation string = ''

param apimPublisherEmail string = 'pilot-operator@example.com'

@description('Disable the gateway public network access after its private endpoint exists.')
param apimDisablePublicAccess bool = true

@description('SSH public key for the in-VNet operator host. Empty: no operator host.')
param jumpboxSshPublicKey string = ''

param jumpboxSize string = 'Standard_B2s'

@description('Create the free Azure Bastion Developer host for browser SSH (select regions only).')
param bastionDeveloper bool = true

var token = uniqueString(subscription().id, resourceGroup().id, environmentName)
var allTags = union(tags, { 'azd-env-name': environmentName })
// Network: created here, or a landing-zone VNet the platform team vended.
var landingZone = !empty(existingVnetId)
var vnetId = landingZone ? existingVnetId : resourceId('Microsoft.Network/virtualNetworks', 'vnet-${token}')
// Literal resource IDs, not module outputs: preflight validation can't resolve outputs.
var agentSubnetId = '${vnetId}/subnets/snet-agent'
var peSubnetId = '${vnetId}/subnets/snet-pe'
var acaSubnetId = '${vnetId}/subnets/snet-aca'
var apimSubnetId = '${vnetId}/subnets/snet-apim'
var opsSubnetId = '${vnetId}/subnets/snet-ops'
var zoneRg = empty(dnsZonesResourceGroup) ? resourceGroup().name : dnsZonesResourceGroup
var coreZones = [
  'privatelink.services.ai.azure.com', 'privatelink.openai.azure.com', 'privatelink.cognitiveservices.azure.com'
  'privatelink.azurecr.io', 'privatelink.azure-api.net', 'privatelink.monitor.azure.com'
  'privatelink.oms.opinsights.azure.com', 'privatelink.ods.opinsights.azure.com'
  'privatelink.agentsvc.azure-automation.net'
]
var zoneId = toObject(coreZones, z => z, z => resourceId(zoneRg, 'Microsoft.Network/privateDnsZones', z))
var acrPrefixes = empty(acrBuildAllowedPrefixes) ? [] : split(acrBuildAllowedPrefixes, ',')

// Role definition GUIDs (pinned)
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleAcrRepositoryReader = 'b93aa761-3e63-49ed-ac28-beffa264f7ac'
var roleFoundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d' // formerly "Azure AI User"

module network 'modules/network.bicep' = if (empty(existingVnetId)) {
  name: 'network-${token}'
  params: { location: location, token: token, tags: allTags, vnetAddressPrefix: vnetAddressPrefix }
}

// Landing-zone VNet without central zones: create the core zones here (network.bicep is skipped).
module lzZones 'modules/zone.bicep' = [for z in coreZones: if (landingZone && empty(dnsZonesResourceGroup)) {
  name: 'zone-${uniqueString(z)}-${token}'
  params: { zoneName: z, vnetId: vnetId, token: token, tags: allTags }
}]

// ---------------------------------------------------------------- telemetry --
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
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    publicNetworkAccessForIngestion: 'Disabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' = {
  name: 'ampls-${token}'
  location: 'global'
  tags: allTags
  properties: { accessModeSettings: { ingestionAccessMode: 'PrivateOnly', queryAccessMode: 'Open' } }
}

resource amplsAppi 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'appi'
  properties: { linkedResourceId: appi.id }
}

resource amplsLaw 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'law'
  properties: { linkedResourceId: law.id }
}

module amplsPe 'modules/private-endpoint.bicep' = {
  name: 'pe-ampls-${token}'
  params: {
    name: 'pe-ampls-${token}'
    location: location
    tags: allTags
    subnetId: peSubnetId
    targetId: ampls.id
    groupId: 'azuremonitor'
    zoneIds: [
      zoneId['privatelink.monitor.azure.com']
      zoneId['privatelink.oms.opinsights.azure.com']
      zoneId['privatelink.ods.opinsights.azure.com']
      zoneId['privatelink.agentsvc.azure-automation.net']
    ]
  }
  dependsOn: [ amplsAppi, amplsLaw, network ]
}

// ----------------------------------------------------------------- registry --
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${token}'
  location: location
  tags: allTags
}

resource acr 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: 'cr${token}'
  location: location
  tags: allTags
  sku: { name: 'Premium' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: empty(acrBuildAllowedPrefixes) ? 'Disabled' : 'Enabled'
    networkRuleBypassOptions: 'AzureServices'
    networkRuleSet: {
      defaultAction: 'Deny'
      ipRules: [for prefix in acrPrefixes: { action: 'Allow', value: trim(prefix) }]
    }
  }
}

module acrPe 'modules/private-endpoint.bicep' = {
  name: 'pe-acr-${token}'
  params: {
    name: 'pe-acr-${token}'
    location: location
    tags: allTags
    subnetId: peSubnetId
    targetId: acr.id
    groupId: 'registry'
    zoneIds: [ zoneId['privatelink.azurecr.io'] ]
  }
  dependsOn: [ network, lzZones ]
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

// ------------------------------------------------------------ MCP (internal) --
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: allTags
  properties: {
    vnetConfiguration: { internal: true, infrastructureSubnetId: acaSubnetId }
    workloadProfiles: [ { name: 'Consumption', workloadProfileType: 'Consumption' } ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: law.properties.customerId, sharedKey: law.listKeys().primarySharedKey }
    }
  }
  dependsOn: [ network, lzZones ]
}

module caeDns 'modules/aca-dns.bicep' = {
  name: 'dns-cae-${token}'
  params: {
    defaultDomain: cae.properties.defaultDomain
    staticIp: cae.properties.staticIp
    vnetId: vnetId
    extraLinkVnetId: dnsZoneLinkVnetId
    token: token
    tags: allTags
  }
}

resource mcp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-mcp-${token}'
  location: location
  tags: union(allTags, { 'azd-service-name': 'mcp' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${uami.id}': {} } }
  dependsOn: [ uamiAcrPull, acrPe ]
  properties: {
    managedEnvironmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      // In an internal environment "external" means reachable from the VNet, never from the internet.
      ingress: { external: true, targetPort: 8080, transport: 'auto' }
      registries: [ { server: acr.properties.loginServer, identity: uami.id } ]
    }
    template: {
      containers: [ {
        name: 'mcp'
        image: mcpImage
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: concat([ { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString } ],
          empty(returnsCosmosUrl) ? [] : [
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'RETURNS_COSMOS_URL', value: returnsCosmosUrl }
            { name: 'RETURNS_COSMOS_DATABASE', value: 'returns' }
            { name: 'RETURNS_COSMOS_CONTAINER', value: 'returns-cases' }
          ])
      } ]
      scale: { minReplicas: mcpMinReplicas, maxReplicas: 2 }
    }
  }
}

// ------------------------------------------------------------------ Foundry --
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'ais-${token}'
  location: location
  tags: allTags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'ais-${token}'
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', virtualNetworkRules: [], ipRules: [], bypass: 'AzureServices' }
    // Hosted-agent egress through the delegated subnet. Must be present when the
    // account is FIRST created; it can't be added or changed afterwards.
    networkInjections: [
      // A literal resource ID, not the module output: preflight validation can't resolve outputs here.
      { scenario: 'agent', subnetArmId: agentSubnetId, useMicrosoftManagedNetwork: false }
    ]
    disableLocalAuth: true
    allowProjectManagement: true
  }
  dependsOn: [ network, lzZones ]
}

module accountPe 'modules/private-endpoint.bicep' = {
  name: 'pe-ais-${token}'
  params: {
    name: 'pe-ais-${token}'
    location: location
    tags: allTags
    subnetId: peSubnetId
    targetId: account.id
    groupId: 'account'
    zoneIds: [
      zoneId['privatelink.services.ai.azure.com']
      zoneId['privatelink.openai.azure.com']
      zoneId['privatelink.cognitiveservices.azure.com']
    ]
  }
  dependsOn: [ network, lzZones ]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: 'proj-${token}'
  location: location
  tags: allTags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: environmentName, description: 'Threadlight private pilot' }
  dependsOn: [ accountPe ]
}

resource projectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: 'caphostproj'
  #disable-next-line BCP037
  properties: { capabilityHostKind: 'Agents' }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: modelName
  sku: { name: 'GlobalStandard', capacity: modelCapacity }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
  dependsOn: [ project ]
}

// The project identity pulls the hosted-agent image from the private ACR
// (supported for projects created after 2026-06-25, per the Learn limitation).
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

// With network injection, evaluation runs use the PROJECT managed identity for
// their dataset uploads: Learn says to give it Foundry User at the Foundry
// resource scope during setup, or evaluation runs fail (401 assets/write, seen
// live 2026-11-11). https://learn.microsoft.com/azure/foundry/concepts/evaluation-virtual-network
resource projectFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, project.id, roleFoundryUser)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

resource deployerFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: project
  name: guid(project.id, principalId, roleFoundryUser)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

resource deployerFoundryUserAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: account
  name: guid(account.id, principalId, roleFoundryUser)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

module dataServices 'modules/data-services.bicep' = if (enableAiSearch || enableStorage || enableKeyVault) {
  name: 'data-${token}'
  params: {
    location: location
    token: token
    tags: allTags
    peSubnetId: peSubnetId
    vnetId: vnetId
    dnsZonesResourceGroup: dnsZonesResourceGroup
    enableAiSearch: enableAiSearch
    searchLocation: empty(aiSearchLocation) ? location : aiSearchLocation
    enableStorage: enableStorage
    enableKeyVault: enableKeyVault
    readerPrincipalId: project.identity.principalId
  }
  dependsOn: [ network, lzZones ]
}

var projectEndpoint = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'

// ------------------------------------------------------------ model gateway --
module gateway 'modules/apim-gateway.bicep' = if (modelGateway == 'apim') {
  name: 'apim-${token}'
  params: {
    location: location
    token: token
    tags: allTags
    apimSubnetId: apimSubnetId
    peSubnetId: peSubnetId
    apimZoneId: zoneId['privatelink.azure-api.net']
    projectEndpoint: projectEndpoint
    foundryAccountName: account.name
    publisherEmail: apimPublisherEmail
    disablePublicAccess: apimDisablePublicAccess
  }
  dependsOn: [ accountPe, model ]
}

// ----------------------------------------------------------- operator host --
module operator 'modules/jumpbox.bicep' = if (!empty(jumpboxSshPublicKey)) {
  name: 'ops-${token}'
  params: {
    location: location
    token: token
    tags: allTags
    subnetId: opsSubnetId
    vnetId: vnetId
    sshPublicKey: jumpboxSshPublicKey
    foundryAccountName: account.name
    vmSize: jumpboxSize
    bastionDeveloper: bastionDeveloper
  }
  dependsOn: [ project ]
}

// The App Insights and container-registry project connections are created by
// hooks (postprovision.sh, acr-connection.sh), not here: in eastus2 every
// Foundry connection write returned 500 although the service saved it, and
// ARM retried the PUT until the deployment was cancelled (2026-11-10).

output AZURE_RESOURCE_GROUP string = resourceGroup().name
output FOUNDRY_PROJECT_ENDPOINT string = projectEndpoint
output AZURE_AI_PROJECT_ENDPOINT string = projectEndpoint
output AZURE_AI_PROJECT_ID string = project.id
output AZURE_AI_ACCOUNT_ID string = account.id
output AZURE_AI_ACCOUNT_NAME string = account.name
output AZURE_AI_PROJECT_NAME string = project.name
output AZURE_AI_PROJECT_PRINCIPAL_ID string = project.identity.principalId
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = model.name
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = acr.id
output MCP_SERVER_FQDN string = mcp.properties.configuration.ingress.fqdn
output MCP_CONTAINER_APP_NAME string = mcp.name
output MCP_IDENTITY_PRINCIPAL_ID string = uami.properties.principalId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appi.properties.ConnectionString
output APPLICATIONINSIGHTS_RESOURCE_ID string = appi.id
output AZURE_AI_APPINSIGHTS_CONNECTION_ID string = '${project.id}/connections/${appi.name}'
output THREADLIGHT_NETWORK_PROFILE string = 'private'
output VNET_ID string = vnetId
output NAT_PUBLIC_IP string = landingZone ? '' : network!.outputs.natPublicIp
output THREADLIGHT_LANDING_ZONE bool = landingZone
output MODEL_GATEWAY_URL string = modelGateway == 'apim' ? gateway!.outputs.gatewayUrl : (modelGateway == 'external' ? modelGatewayUrl : '')
output JUMPBOX_VM_NAME string = empty(jumpboxSshPublicKey) ? '' : operator!.outputs.vmName
output AZURE_AI_SEARCH_ENDPOINT string = enableAiSearch ? dataServices!.outputs.searchEndpoint : ''
output AZURE_STORAGE_BLOB_ENDPOINT string = enableStorage ? dataServices!.outputs.storageBlobEndpoint : ''
output AZURE_KEY_VAULT_URI string = enableKeyVault ? dataServices!.outputs.keyVaultUri : ''
