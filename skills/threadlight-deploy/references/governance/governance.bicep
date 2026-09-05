targetScope = 'resourceGroup'

@description('Dedicated governance deployment names, network dependencies, Entra configuration. Never customer defaults.')
param config object
@allowed(['foundation', 'services'])
param phase string = 'foundation'
@description('Digest-pinned image references supplied only AFTER building generated contexts.')
param images object = {}
@description('Trusted phase-one outputs plus existing hosted instance identity and pinned signing key version.')
param bindings object = {}
param location string = resourceGroup().location

var privateRequired = config.network.posture == 'private-required'
var prefix = config.prefix
var probesEnabled = contains(config, 'probe_observability')
  ? (config.probe_observability.enabled == true && contains(['staging', 'preproduction'], config.environment))
  : false
var environmentParts = split(config.network.environment_id, '/')
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: last(environmentParts)
  scope: resourceGroup(environmentParts[2], environmentParts[4])
}
var controlName = '${prefix}-control'
var gatewayName = '${prefix}-gateway'
// external=true admits clients in the VNet when the ENVIRONMENT is internal.
// external=false would confine ingress to ACA peers and exclude injected Foundry.
var controlUrl = 'https://${controlName}.${environment.properties.defaultDomain}'
var gatewayUrl = 'https://${gatewayName}.${environment.properties.defaultDomain}/mcp'

resource controlIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-control'
  location: location
}
resource gatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-gateway'
  location: location
}
resource downstreamIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-downstream'
  location: location
}
resource publisherIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-publisher'
  location: location
}
resource fixtureIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (probesEnabled) {
  name: '${prefix}-probe-fixture'
  location: location
}

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  name: config.storage_name
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: privateRequired ? 'Disabled' : 'Enabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
      ipRules: [for ip in (privateRequired ? [] : config.network.allowed_ips): {
        action: 'Allow'
        // Storage requires individual addresses, not /32 CIDR ranges.
        value: endsWith(ip, '/32') ? split(ip, '/')[0] : ip
      }]
    }
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-01-01' = {
  parent: storage
  name: 'default'
  properties: {}
}
resource catalog 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobService
  name: 'signed-catalog'
  properties: { publicAccess: 'None' }
}
resource immutable 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2025-01-01' = {
  parent: catalog
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: 365
    allowProtectedAppendWrites: false
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2025-04-15' = {
  name: config.cosmos_name
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    enableMultipleWriteLocations: false
    locations: [{ locationName: location, failoverPriority: 0 }]
    consistencyPolicy: { defaultConsistencyLevel: 'Strong' }
    publicNetworkAccess: privateRequired ? 'Disabled' : 'Enabled'
    ipRules: [for ip in (privateRequired ? [] : config.network.allowed_ips): { ipAddressOrRange: ip }]
  }
}
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-04-15' = {
  parent: cosmos
  name: 'governance'
  properties: { resource: { id: 'governance' }, options: { throughput: 400 } }
}
// Task8 CAS stores BOTH approvals and receipts in one /scope container.
resource records 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'governance-records'
  properties: {
    resource: {
      id: 'governance-records'
      partitionKey: { paths: ['/scope'], kind: 'Hash' }
    }
  }
}
// Task9 pending and completed records never expire: an old nonce cannot redispatch.
resource idempotency 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'gateway-idempotency'
  properties: {
    resource: {
      id: 'gateway-idempotency'
      partitionKey: { paths: ['/scope'], kind: 'Hash' }
    }
  }
}
// Separate writers: agent/gateway can never write authoritative fixture effects.
resource probeContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = [for producer in ['gateway', 'native', 'fixture']: if (probesEnabled) {
  parent: database
  name: 'probe-${producer}'
  properties: {
    resource: {
      id: 'probe-${producer}'
      partitionKey: { paths: ['/scope'], kind: 'Hash' }
    }
  }
}]
resource documentRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, 'cas-items')
  properties: {
    roleName: '${prefix}-cas-items'
    type: 'CustomRole'
    assignableScopes: [cosmos.id]
    permissions: [{
      dataActions: [
        'Microsoft.DocumentDB/databaseAccounts/readMetadata'
        'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read'
        'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create'
        'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/replace'
      ]
    }]
  }
}
resource controlDocuments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, controlIdentity.id, records.name)
  properties: {
    principalId: controlIdentity.properties.principalId
    roleDefinitionId: documentRole.id
    scope: '${cosmos.id}/dbs/governance/colls/governance-records'
  }
}
resource gatewayDocuments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, gatewayIdentity.id, idempotency.name)
  properties: {
    principalId: gatewayIdentity.properties.principalId
    roleDefinitionId: documentRole.id
    scope: '${cosmos.id}/dbs/governance/colls/gateway-idempotency'
  }
}
resource gatewayProbeDocuments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = if (probesEnabled) {
  parent: cosmos
  name: guid(cosmos.id, gatewayIdentity.id, 'probe-gateway')
  properties: {
    principalId: gatewayIdentity.properties.principalId
    roleDefinitionId: documentRole.id
    scope: '${cosmos.id}/dbs/governance/colls/probe-gateway'
  }
}
resource fixtureProbeDocuments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = if (probesEnabled) {
  parent: cosmos
  name: guid(cosmos.id, 'probe-fixture')
  properties: {
    principalId: fixtureIdentity!.properties.principalId
    roleDefinitionId: documentRole.id
    scope: '${cosmos.id}/dbs/governance/colls/probe-fixture'
  }
}
resource nativeProbeDocuments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = if (probesEnabled && phase == 'services' && config.runtime == 'microsoft-agent-framework') {
  parent: cosmos
  name: guid(cosmos.id, 'probe-native')
  properties: {
    principalId: bindings.agent_principal
    roleDefinitionId: documentRole.id
    scope: '${cosmos.id}/dbs/governance/colls/probe-native'
  }
}
// The SDK's account metadata probe must work without widening item write scopes.
resource metadataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, 'metadata-only')
  properties: {
    roleName: '${prefix}-metadata-only'
    type: 'CustomRole'
    assignableScopes: [cosmos.id]
    permissions: [{ dataActions: ['Microsoft.DocumentDB/databaseAccounts/readMetadata'] }]
  }
}
resource metadataReaders 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = [for identity in ['control', 'gateway']: {
  parent: cosmos
  name: guid(cosmos.id, identity, 'metadata-only')
  properties: {
    principalId: identity == 'control' ? controlIdentity.properties.principalId : gatewayIdentity.properties.principalId
    roleDefinitionId: metadataRole.id
    scope: cosmos.id
  }
}]
resource fixtureProbeMetadata 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = if (probesEnabled) {
  parent: cosmos
  name: guid(cosmos.id, 'probe-fixture', 'metadata-only')
  properties: {
    principalId: fixtureIdentity!.properties.principalId
    roleDefinitionId: metadataRole.id
    scope: cosmos.id
  }
}
resource nativeProbeMetadata 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = if (probesEnabled && phase == 'services' && config.runtime == 'microsoft-agent-framework') {
  parent: cosmos
  name: guid(cosmos.id, 'probe-native', 'metadata-only')
  properties: {
    principalId: bindings.agent_principal
    roleDefinitionId: metadataRole.id
    scope: cosmos.id
  }
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: config.vault_name
  location: location
  properties: {
    tenantId: config.tenant_id
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: privateRequired ? 'Disabled' : 'Enabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
      ipRules: [for ip in (privateRequired ? [] : config.network.allowed_ips): { value: ip }]
    }
  }
}
resource policyKey 'Microsoft.KeyVault/vaults/keys@2024-11-01' = {
  parent: vault
  name: 'policy'
  properties: {
    kty: 'RSA'
    keySize: 3072
    keyOps: ['sign', 'verify']
    attributes: { enabled: true, exportable: false }
  }
}
resource verifyRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'governance-key-verify')
  properties: {
    roleName: '${prefix}-key-verify'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: []
      dataActions: ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/verify/action']
    }]
  }
}
resource fixtureProbeVerify 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (probesEnabled) {
  name: guid(policyKey.id, 'probe-fixture', 'verify')
  scope: policyKey
  properties: {
    principalId: fixtureIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: verifyRole.id
  }
}
resource publishRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'governance-key-sign')
  properties: {
    roleName: '${prefix}-key-sign'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: []
      dataActions: [
        'Microsoft.KeyVault/vaults/keys/read'
        'Microsoft.KeyVault/vaults/keys/verify/action'
        'Microsoft.KeyVault/vaults/keys/sign/action'
      ]
    }]
  }
}
resource verifyAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for identity in ['control', 'gateway']: {
  name: guid(policyKey.id, identity, 'verify')
  scope: policyKey
  properties: {
    principalId: identity == 'control' ? controlIdentity.properties.principalId : gatewayIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: verifyRole.id
  }
}]
resource agentVerify 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (phase == 'services' && config.runtime == 'microsoft-agent-framework') {
  name: guid(policyKey.id, bindings.agent_principal, 'verify')
  scope: policyKey
  properties: {
    principalId: bindings.agent_principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: verifyRole.id
  }
}
resource publisherSign 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(policyKey.id, publisherIdentity.id, 'sign')
  scope: policyKey
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: publishRole.id
  }
}
resource catalogRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(catalog.id, controlIdentity.id, 'read')
  scope: catalog
  properties: {
    principalId: controlIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  }
}
resource uploadRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'governance-catalog-publish')
  properties: {
    roleName: '${prefix}-catalog-publish'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: ['Microsoft.Storage/storageAccounts/blobServices/containers/read']
      dataActions: [
        'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'
        'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write'
      ]
    }]
  }
}
resource publishCatalog 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(catalog.id, publisherIdentity.id, 'upload')
  scope: catalog
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: uploadRole.id
  }
}

var privateServices = privateRequired ? [
  { name: 'blob', id: storage.id, group: 'blob', dns: config.network.blob_dns_zone_id }
  { name: 'cosmos', id: cosmos.id, group: 'Sql', dns: config.network.cosmos_dns_zone_id }
  { name: 'vault', id: vault.id, group: 'vault', dns: config.network.keyvault_dns_zone_id }
] : []
resource privateEndpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for service in privateServices: {
  name: '${prefix}-${service.name}-pe'
  location: location
  properties: {
    subnet: { id: config.network.private_endpoint_subnet_id }
    privateLinkServiceConnections: [{
      name: service.name
      properties: { privateLinkServiceId: service.id, groupIds: [service.group] }
    }]
  }
}]
resource dnsGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (service, i) in privateServices: {
  name: '${privateEndpoints[i].name}/default'
  properties: { privateDnsZoneConfigs: [{ name: service.name, properties: { privateDnsZoneId: service.dns } }] }
}]

var acrParts = split(config.acr_id, '/')
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(acrParts)
  scope: resourceGroup(acrParts[2], acrParts[4])
}
// Existing ACR must use classic RBAC. ABAC registries require a separately reviewed repository scope.
module registryPull 'registry-pull.bicep' = [for identity in ['control', 'gateway']: {
  name: '${prefix}-${identity}-pull'
  scope: resourceGroup(acrParts[2], acrParts[4])
  params: {
    registryName: registry.name
    principalId: identity == 'control' ? controlIdentity.properties.principalId : gatewayIdentity.properties.principalId
  }
}]

// Identity graph is resolved in foundation, then Entra app-role assignments and
// the signed registry are supplied to services. No resource references its own app.
// These objects are validated with the actual Task8/9 Pydantic schemas by bind.
var controlConfig = phase == 'services' ? bindings.control_config : {}
var gatewayConfig = phase == 'services' ? bindings.gateway_config : {}
var ipRestrictions = [for (ip, i) in (privateRequired ? [] : config.network.allowed_ips): {
  name: 'allowed-${i}', action: 'Allow', ipAddressRange: ip
}]
var ingress = {
  external: true
  targetPort: 8000
  transport: 'http'
  allowInsecure: false
  ipSecurityRestrictions: ipRestrictions
}
resource controlApp 'Microsoft.App/containerApps@2025-01-01' = if (phase == 'services') {
  name: controlName
  location: location
  tags: { 'azd-service-name': 'govern-control-plane' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${controlIdentity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: ingress
      registries: [{ server: registry.properties.loginServer, identity: controlIdentity.id }]
    }
    template: {
      containers: [{
        name: 'control-plane'
        image: images.control_plane
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: controlIdentity.properties.clientId }
          { name: 'TL_GOV_SERVICE', value: 'control-plane' }
          { name: 'GOV_CONFIG_JSON', value: string(controlConfig) }
        ]
        // Control-plane /health requires workload auth; TCP is liveness only.
        // Authenticated semantic readiness is a REQUIRED post-provision gate.
        probes: [{ type: 'Liveness', tcpSocket: { port: 8000 }, initialDelaySeconds: 10 }]
      }]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
  dependsOn: [controlDocuments, metadataReaders, catalogRead, verifyAssignments, registryPull]
}
resource gatewayApp 'Microsoft.App/containerApps@2025-01-01' = if (phase == 'services' && config.enable_gateway) {
  name: gatewayName
  location: location
  tags: { 'azd-service-name': 'govern-gateway' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${gatewayIdentity.id}': {}, '${downstreamIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: ingress
      registries: [{ server: registry.properties.loginServer, identity: gatewayIdentity.id }]
    }
    template: {
      containers: [{
        name: 'gateway'
        image: images.gateway
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'TL_GOV_SERVICE', value: 'gateway' }
          { name: 'GATEWAY_CONFIG_JSON', value: string(gatewayConfig) }
        ]
        probes: [{ type: 'Readiness', httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 10 }]
      }]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
  dependsOn: [gatewayDocuments, metadataReaders, verifyAssignments, registryPull, controlApp]
}

output GOV_CONTROL_PLANE_URL string = controlUrl
output GOVERNED_TOOL_GATEWAY_URL string = gatewayUrl
output TL_GOV_KEY_VERSION string = policyKey.properties.keyUriWithVersion
output TL_GOV_CONTROL_CLIENT string = controlIdentity.properties.clientId
output TL_GOV_CONTROL_PRINCIPAL string = controlIdentity.properties.principalId
output TL_GOV_GATEWAY_CLIENT string = gatewayIdentity.properties.clientId
output TL_GOV_GATEWAY_PRINCIPAL string = gatewayIdentity.properties.principalId
output TL_GOV_DOWNSTREAM_CLIENT string = downstreamIdentity.properties.clientId
output TL_GOV_DOWNSTREAM_PRINCIPAL string = downstreamIdentity.properties.principalId
output TL_GOV_PUBLISHER_CLIENT string = publisherIdentity.properties.clientId
output TL_GOV_PUBLISHER_PRINCIPAL string = publisherIdentity.properties.principalId
output TL_GOV_CONTROL_CONFIG object = controlConfig
output TL_GOV_GATEWAY_CONFIG object = gatewayConfig
output TL_GOV_BLOB_URL string = 'https://${storage.name}.blob.${az.environment().suffixes.storage}'
output TL_GOV_COSMOS_URL string = cosmos.properties.documentEndpoint
