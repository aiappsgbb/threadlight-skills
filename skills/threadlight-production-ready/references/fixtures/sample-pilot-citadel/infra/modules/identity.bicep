param location string
param tags object

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'uami-pilot-citadel'
  location: location
  tags: tags
}

output uamiId string = uami.id
output uamiPrincipalId string = uami.properties.principalId
output uamiClientId string = uami.properties.clientId
