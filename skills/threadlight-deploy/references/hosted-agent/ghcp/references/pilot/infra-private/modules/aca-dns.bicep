// Internal Container Apps environments get no public DNS: resolve the
// environment's default domain to its static IP inside the VNet.
// https://learn.microsoft.com/azure/container-apps/private-endpoints-with-dns
param defaultDomain string
param staticIp string
param vnetId string
param token string
param tags object

resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: defaultDomain
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-${token}'
  location: 'global'
  properties: { virtualNetwork: { id: vnetId }, registrationEnabled: false }
}

resource wildcard 'Microsoft.Network/privateDnsZones/A@2020-06-01' = {
  parent: zone
  name: '*'
  properties: { ttl: 300, aRecords: [ { ipv4Address: staticIp } ] }
}

resource apex 'Microsoft.Network/privateDnsZones/A@2020-06-01' = {
  parent: zone
  name: '@'
  properties: { ttl: 300, aRecords: [ { ipv4Address: staticIp } ] }
}
