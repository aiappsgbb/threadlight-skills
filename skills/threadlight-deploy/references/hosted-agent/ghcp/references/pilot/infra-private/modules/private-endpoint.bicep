// One private endpoint with its DNS zone group: the zone group writes the A
// records into the VNet-linked private DNS zones, so the public host name
// resolves to the private IP inside the VNet.
param name string
param location string
param tags object
param subnetId string
param targetId string
param groupId string
param zoneIds array

resource pe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      { name: name, properties: { privateLinkServiceId: targetId, groupIds: [ groupId ] } }
    ]
  }
}

resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: pe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zoneId, i) in zoneIds: {
      name: 'zone-${i}'
      properties: { privateDnsZoneId: zoneId }
    }]
  }
}

output id string = pe.id
