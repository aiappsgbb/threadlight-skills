param location string
param tags object
param uamiId string
param appInsightsConnectionString string
param acaEnvSubnetId string

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'env-pilot-citadel'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: acaEnvSubnetId
      internal: true
    }
  }
}

resource aca 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'aca-pilot-citadel'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uamiId}': {} }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8080
      }
    }
    template: {
      containers: [
        {
          name: 'app'
          image: 'mcr.microsoft.com/k8se/quickstart:latest'
          env: [
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
          // REL-006: liveness + readiness probes
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8080 }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/ready', port: 8080 }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// IAM-005: EasyAuth / authConfigs surfacing via ACA
resource acaAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: aca
  name: 'current'
  properties: {
    globalValidation: { unauthenticatedClientAction: 'Return401' }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: 'placeholder-aad-client-id'
          openIdIssuer: 'https://login.microsoftonline.com/${tenant().tenantId}/v2.0'
        }
      }
    }
  }
}

output acaFqdn string = aca.properties.configuration.ingress.fqdn
