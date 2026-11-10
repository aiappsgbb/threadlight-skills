// Business authority for the governed-write demo, separate from the governance foundation.
// Creates: a serverless Cosmos account for synthetic return cases, the business writer
// identity (the ONLY Cosmos writer), read-only access for the pilot MCP server, a
// create/read-only seeding role for the operator, the business API Container App
// (scale to zero) and the manual publisher job (runs under the foundation's publisher UAMI).
// No secrets, keys or connection strings; Cosmos local auth is disabled.
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param tags object
@description('Short lowercase prefix, e.g. gov-demo')
param prefix string
param acrName string
param environmentId string
@description('Principal ID of the pilot MCP server identity (read-only case access).')
param mcpReaderPrincipalId string
@description('Principal ID of the operator who seeds synthetic cases (create/read only).')
param operatorPrincipalId string
@description('Business API image by digest; empty skips the app.')
param businessImage string = ''
@description('returns_mcp_backend.Configuration as an object (validated by the service at startup).')
param businessConfig object = {}
@description('Publisher UAMI resource ID from the governance foundation.')
param publisherIdentityId string
param publisherClientId string
@description('Control-plane image by digest that contains publish_policy.py; empty skips the job.')
param publisherImage string = ''
param publisherEnv array = []

var cosmosName = '${prefix}-biz-${uniqueString(resourceGroup().id)}'
var writerRole = '00000000-0000-0000-0000-000000000002' // Cosmos DB Built-in Data Contributor
var readerRole = '00000000-0000-0000-0000-000000000001' // Cosmos DB Built-in Data Reader

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2025-04-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    capabilities: [{ name: 'EnableServerless' }]
    locations: [{ locationName: location, failoverPriority: 0 }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
  }
}
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-04-15' = {
  parent: cosmos
  name: 'returns'
  properties: { resource: { id: 'returns' } }
}
resource cases 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'returns-cases'
  // No TTL: cases and decision audits are permanent.
  properties: { resource: { id: 'returns-cases', partitionKey: { paths: ['/case_id'], kind: 'Hash' } } }
}

resource writer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-business'
  location: location
  tags: tags
}

var casesScope = '${cosmos.id}/dbs/returns/colls/returns-cases'
resource writerData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, writer.id, 'writer')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${writerRole}'
    principalId: writer.properties.principalId
    scope: casesScope
  }
  dependsOn: [cases]
}
resource mcpRead 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, mcpReaderPrincipalId, 'reader')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${readerRole}'
    principalId: mcpReaderPrincipalId
    scope: casesScope
  }
  dependsOn: [cases]
}
resource seedRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, 'seed-create-read')
  properties: {
    roleName: '${prefix}-case-seeder'
    type: 'CustomRole'
    assignableScopes: [cosmos.id]
    permissions: [{
      dataActions: [
        'Microsoft.DocumentDB/databaseAccounts/readMetadata'
        'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create'
        'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read'
      ]
    }]
  }
}
resource operatorSeed 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, operatorPrincipalId, 'seed')
  properties: { roleDefinitionId: seedRole.id, principalId: operatorPrincipalId, scope: casesScope }
  dependsOn: [cases]
}

var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
resource writerPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, writer.id, 'pull')
  scope: registry
  properties: { principalId: writer.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: acrPull }
}
resource publisherPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, publisherIdentityId, 'pull')
  scope: registry
  properties: {
    principalId: reference(publisherIdentityId, '2023-01-31').principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPull
  }
}

resource business 'Microsoft.App/containerApps@2025-01-01' = if (!empty(businessImage)) {
  name: '${prefix}-business'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${writer.id}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'http', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: writer.id }]
    }
    template: {
      containers: [{
        name: 'business'
        image: businessImage
        resources: { cpu: json('0.5'), memory: '1Gi' }
        command: ['sh', '-c', 'printf %s "$RETURNS_CONFIG_JSON" > /tmp/returns.json && RETURNS_CONFIG_FILE=/tmp/returns.json exec python -m uvicorn returns_mcp_backend:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers']
        env: [
          { name: 'AZURE_CLIENT_ID', value: writer.properties.clientId }
          { name: 'RETURNS_CONFIG_JSON', value: string(businessConfig) }
        ]
        probes: [{ type: 'Liveness', tcpSocket: { port: 8000 }, initialDelaySeconds: 10 }]
      }]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [writerPull, writerData]
}

resource publisher 'Microsoft.App/jobs@2025-01-01' = if (!empty(publisherImage)) {
  name: '${prefix}-publisher'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${publisherIdentityId}': {} } }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 300
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: publisherIdentityId }]
    }
    template: {
      containers: [{
        name: 'publisher'
        image: publisherImage
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        command: ['python', 'publish_policy.py', '--mode', 'sign-only']
        env: concat([{ name: 'AZURE_CLIENT_ID', value: publisherClientId }], publisherEnv)
      }]
    }
  }
  dependsOn: [publisherPull]
}

output RETURNS_COSMOS_URL string = cosmos.properties.documentEndpoint
output RETURNS_WRITER_PRINCIPAL string = writer.properties.principalId
output RETURNS_WRITER_CLIENT string = writer.properties.clientId
output RETURNS_BUSINESS_URL string = empty(businessImage) ? '' : 'https://${business!.properties.configuration.ingress.fqdn}'
