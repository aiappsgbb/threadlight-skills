param location string
param tags object

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-pilot-citadel'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
  }
}

resource ai 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-pilot-citadel'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

output appInsightsConnectionString string = ai.properties.ConnectionString
output lawId string = law.id
