// One private DNS zone: created here and linked to the VNet, or (landing zone)
// an existing central zone in another resource group, whose VNet links the
// platform team owns.
param zoneName string
param vnetId string
param token string
param tags object
param existingResourceGroup string = ''

resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (empty(existingResourceGroup)) {
  name: zoneName
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (empty(existingResourceGroup)) {
  parent: zone
  name: 'link-${token}'
  location: 'global'
  tags: tags
  properties: { virtualNetwork: { id: vnetId }, registrationEnabled: false }
}

resource central 'Microsoft.Network/privateDnsZones@2020-06-01' existing = if (!empty(existingResourceGroup)) {
  name: zoneName
  scope: resourceGroup(existingResourceGroup)
}

output zoneId string = empty(existingResourceGroup) ? zone.id : central.id
