// ACA Job (cron) receiver — Shape #1 (threadlight-event-triggers).
// A scheduled Container Apps Job that runs the receiver once per cron tick.
//
// Helper symbols (jobExists, fetchLatestImage, emptyContainerImage) come from
// the azd-patterns image-aware deployment pattern — main.bicep resolves them
// and passes the result in as `image`. See azd-patterns/SKILL.md
// § "Helper symbols for image-aware deployment".

@description('Resource name prefix (azd env name).')
param prefix string

@description('Trigger name — becomes the azd-service-name.')
param triggerName string

@description('Deployment location.')
param location string = resourceGroup().location

@description('Container Apps environment resource id.')
param containerAppEnvId string

@description('User-assigned managed identity resource id.')
param uami string

@description('UAMI client id (surfaced to the container as AZURE_CLIENT_ID).')
param uamiClientId string

@description('ACR name — login server is {acr}.azurecr.io.')
param acr string

@description('Cron schedule, e.g. "0 6 * * *" (daily 06:00 UTC).')
param triggerSource string

@description('Foundry project endpoint.')
param projectEndpoint string

@description('Hosted agent name to invoke.')
param agentName string

@description('Cosmos account endpoint (idempotency table).')
param cosmosEndpoint string

@description('Container image — resolved by main.bicep (fetchLatestImage or placeholder).')
param image string

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-${triggerName}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${uami}': {} } }
  properties: {
    environmentId: containerAppEnvId
    configuration: {
      // 1800s = 30 min. For long agent loops bump to 7200 (2h) or up to 86400
      // (24h). The right value is the p99 of one full agent invocation + headroom.
      replicaTimeout: 1800
      triggerType: 'Schedule'
      scheduleTriggerConfig: { cronExpression: triggerSource }
      registries: [ { server: '${acr}.azurecr.io', identity: uami } ]
    }
    template: {
      containers: [ {
        name: 'receiver'
        image: image
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: uamiClientId }
          { name: 'PROJECT_ENDPOINT', value: projectEndpoint }
          { name: 'AGENT_NAME', value: agentName }
          { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
        ]
      } ]
    }
  }
  tags: { 'azd-service-name': triggerName }
}

output jobName string = job.name
