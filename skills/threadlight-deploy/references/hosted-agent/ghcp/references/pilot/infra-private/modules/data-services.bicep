// Data services a design selects in SPEC § 11c (`ai-search`, `storage-blob`,
// `key-vault`), each with public network access Disabled, local/key auth off,
// a private endpoint and its DNS zone. The project identity gets read access;
// wiring a Foundry IQ knowledge base stays with threadlight-ground.
// Search needs Basic or higher for private endpoints (Free has none):
// https://learn.microsoft.com/azure/search/service-create-private-endpoint
// Zones: https://learn.microsoft.com/azure/private-link/private-endpoint-dns
param location string
param token string
param tags object
param peSubnetId string
param vnetId string
@description('Resource group of existing central private DNS zones (landing zone). Empty: create the zones here and link them to vnetId.')
param dnsZonesResourceGroup string = ''
param enableAiSearch bool = false
@description('AI Search region. The private endpoint stays in the VNet region; Learn allows data resources in another region (cross-region cost).')
param searchLocation string = location
param enableStorage bool = false
param enableKeyVault bool = false
param readerPrincipalId string

var roleSearchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var searchZone = 'privatelink.search.windows.net'
var blobZone = 'privatelink.blob.${environment().suffixes.storage}'
var vaultZone = 'privatelink.vaultcore.azure.net'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = if (enableAiSearch) {
  name: 'srch-${token}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'disabled'
    disableLocalAuth: true
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (enableStorage) {
  #disable-next-line BCP334
  name: 'st${take(token, 20)}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    publicNetworkAccess: 'Disabled'
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    networkAcls: { defaultAction: 'Deny', bypass: 'AzureServices' }
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = if (enableKeyVault) {
  name: 'kv-${take(token, 18)}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'AzureServices' }
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

module searchDns 'zone.bicep' = if (enableAiSearch) {
  name: 'zone-srch-${token}'
  params: { zoneName: searchZone, vnetId: vnetId, token: token, tags: tags, existingResourceGroup: dnsZonesResourceGroup }
}

module blobDns 'zone.bicep' = if (enableStorage) {
  name: 'zone-blob-${token}'
  params: { zoneName: blobZone, vnetId: vnetId, token: token, tags: tags, existingResourceGroup: dnsZonesResourceGroup }
}

module vaultDns 'zone.bicep' = if (enableKeyVault) {
  name: 'zone-kv-${token}'
  params: { zoneName: vaultZone, vnetId: vnetId, token: token, tags: tags, existingResourceGroup: dnsZonesResourceGroup }
}

module searchPe 'private-endpoint.bicep' = if (enableAiSearch) {
  name: 'pe-srch-${token}'
  params: {
    name: 'pe-srch-${token}', location: location, tags: tags, subnetId: peSubnetId
    targetId: search.id, groupId: 'searchService', zoneIds: [ searchDns!.outputs.zoneId ]
  }
}

module blobPe 'private-endpoint.bicep' = if (enableStorage) {
  name: 'pe-blob-${token}'
  params: {
    name: 'pe-blob-${token}', location: location, tags: tags, subnetId: peSubnetId
    targetId: storage.id, groupId: 'blob', zoneIds: [ blobDns!.outputs.zoneId ]
  }
}

module vaultPe 'private-endpoint.bicep' = if (enableKeyVault) {
  name: 'pe-kv-${token}'
  params: {
    name: 'pe-kv-${token}', location: location, tags: tags, subnetId: peSubnetId
    targetId: vault.id, groupId: 'vault', zoneIds: [ vaultDns!.outputs.zoneId ]
  }
}

resource searchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableAiSearch) {
  scope: search
  name: guid(search.id, readerPrincipalId, roleSearchIndexDataReader)
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
  }
}

resource blobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableStorage) {
  scope: storage
  name: guid(storage.id, readerPrincipalId, roleStorageBlobDataReader)
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
  }
}

resource vaultReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableKeyVault) {
  scope: vault
  name: guid(vault.id, readerPrincipalId, roleKeyVaultSecretsUser)
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsUser)
  }
}

output searchEndpoint string = enableAiSearch ? 'https://${search.name}.search.windows.net' : ''
output storageBlobEndpoint string = enableStorage ? storage!.properties.primaryEndpoints.blob : ''
output keyVaultUri string = enableKeyVault ? vault!.properties.vaultUri : ''
