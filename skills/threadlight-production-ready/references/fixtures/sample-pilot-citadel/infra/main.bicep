// sample-pilot-citadel — happy-path Citadel-spoke pilot.
// Demonstrates all 14 critical static checks PASSING in v0.3.0:
//   NET-001 (vnet), NET-002 (private endpoint), NET-003 (publicNetworkAccess=Disabled),
//   NET-004 (subnet delegation via ACA env), IAM-002 (UAMI), IAM-005 (EasyAuth on Web site),
//   SEC-001 (KV), SEC-005 (soft-delete + purge), SEC-006 (KV RBAC),
//   OBS-001 (App Insights), OBS-002 (Log Analytics),
//   REL-006 (probes), MDL-001 (model pinned), COST-005 (tags).
//
// Plus shows the v0.3.0 Citadel signal: posture=citadel-spoke + this pilot
// would resolve NET-501 when TL_CITADEL_HUB_RG is set in the environment.

targetScope = 'resourceGroup'

@description('Region for all resources')
param location string = resourceGroup().location

var commonTags = {
  env: 'pilot'
  owner: 'citadel-team@example.com'
  posture: 'citadel-spoke'
  costcenter: 'eng-platform'
}

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    tags: commonTags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    tags: commonTags
  }
}

module observability 'modules/observability.bicep' = {
  name: 'observability'
  params: {
    location: location
    tags: commonTags
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    tags: commonTags
    uamiPrincipalId: identity.outputs.uamiPrincipalId
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    location: location
    tags: commonTags
    uamiId: identity.outputs.uamiId
    subnetId: network.outputs.peSubnetId
  }
}

module compute 'modules/compute.bicep' = {
  name: 'compute'
  params: {
    location: location
    tags: commonTags
    uamiId: identity.outputs.uamiId
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    acaEnvSubnetId: network.outputs.acaEnvSubnetId
  }
}
