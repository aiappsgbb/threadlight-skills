// ACA Job (manual) receiver — Shape #2 (threadlight-event-triggers).
// A Container Apps Job with a MANUAL trigger — started on demand via the REST
// `start` API (a webhook receiver, a workflow step, or an operator). The
// per-execution payload is supplied via the TRIGGER_PAYLOAD env override on the
// `start` call.
//
// Helper symbols (jobExists, fetchLatestImage, emptyContainerImage) come from
// the azd-patterns image-aware deployment pattern — main.bicep resolves them
// and passes the result in as `image`.

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

@description('Foundry project endpoint.')
param projectEndpoint string

@description('Hosted agent name to invoke.')
param agentName string

@description('Cosmos account endpoint (idempotency table).')
param cosmosEndpoint string

@description('Container image — resolved by main.bicep.')
param image string

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-${triggerName}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${uami}': {} } }
  properties: {
    environmentId: containerAppEnvId
    configuration: {
      replicaTimeout: 1800
      triggerType: 'Manual'
      // One replica per `start`; bump for fan-out replays.
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
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
