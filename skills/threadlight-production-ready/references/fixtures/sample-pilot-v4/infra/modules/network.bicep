// placeholder
resource vnet 'Microsoft.Network/virtualNetworks@2024-03-01' = {
  name: 'sample-vnet-v4'
  properties: {
    addressSpace: { addressPrefixes: [ '10.0.0.0/16' ] }
  }
}
