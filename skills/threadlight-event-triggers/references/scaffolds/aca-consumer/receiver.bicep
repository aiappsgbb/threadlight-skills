// ACA Consumer (KEDA) receiver — Shape #4 (threadlight-event-triggers).
// A Container App with NO ingress that pulls from Service Bus and scales 0->N
// on queue depth via a KEDA azure-servicebus scaler. Dead-lettering is native
// Service Bus DLQ (maxDeliveryCount on the queue/subscription).
//
// Helper symbols (appExists, fetchLatestImage, emptyContainerImage) come from
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

@description('User-assigned managed identity resource id (KEDA needs the FULL resource id, not the client id).')
param uami string

@description('UAMI client id (surfaced to the container as AZURE_CLIENT_ID).')
param uamiClientId string

@description('ACR name — login server is {acr}.azurecr.io.')
param acr string

@description('Foundry project endpoint.')
param projectEndpoint string

@description('Hosted agent name to invoke.')
param agentName string

@description('Service Bus namespace FQDN host — e.g. mybus.servicebus.windows.net (no https://, no trailing slash).')
param serviceBusNamespace string

@description('Service Bus queue name to consume.')
param queueName string

@description('Container image — resolved by main.bicep.')
param image string

resource consumerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-${triggerName}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${uami}': {} } }
  properties: {
    environmentId: containerAppEnvId
    configuration: {
      registries: [ { server: '${acr}.azurecr.io', identity: uami } ]
      // No ingress block — this app pulls from Service Bus, not via HTTP.
    }
    template: {
      containers: [ {
        name: 'receiver'
        image: image
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: uamiClientId }
          { name: 'SERVICEBUS_NAMESPACE', value: serviceBusNamespace }
          { name: 'SERVICEBUS_QUEUE', value: queueName }
          { name: 'PROJECT_ENDPOINT', value: projectEndpoint }
          { name: 'AGENT_NAME', value: agentName }
        ]
      } ]
      scale: {
        minReplicas: 0   // scale to zero between events
        maxReplicas: 30
        rules: [ {
          name: 'sb-keda'
          custom: {
            type: 'azure-servicebus'
            // KEDA workload identity binding. `identity` MUST be the UAMI's
            // full resource id (not client id) — KEDA looks up the principal
            // at scale time. TriggerAuthentication is wired automatically by
            // the Container Apps environment when this field is populated.
            identity: uami
            metadata: {
              // namespace MUST be the FQDN host without https:// or trailing
              // slash — bare 'mybus' will silently fail to scale.
              namespace: serviceBusNamespace
              queueName: queueName
              messageCount: '5'   // scale up at >=5 unprocessed msgs per replica
            }
          }
        } ]
      }
    }
  }
  tags: { 'azd-service-name': triggerName }
}

output appName string = consumerApp.name
