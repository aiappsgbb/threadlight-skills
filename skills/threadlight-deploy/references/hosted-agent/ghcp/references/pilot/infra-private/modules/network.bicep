// Private pilot network: one VNet, dedicated subnets, explicit egress (NAT
// gateway) and the private DNS zones every private endpoint registers in.
// Adapted from the official network-secured basic agent template
// (microsoft-foundry/foundry-samples infrastructure-setup-bicep/11-private-network-basic-vnet,
// commit 2c5eac99). Subnet rules: https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks#limitations
//
// Subnets (default 10.42.0.0/16, RFC 1918 as the Agent Service requires):
//   snet-agent  /24  delegated Microsoft.App/environments, one Foundry account only (hosted-agent injection)
//   snet-pe     /24  private endpoints
//   snet-aca    /23  delegated Microsoft.App/environments (internal Container Apps environment, MCP server)
//   snet-apim   /27  delegated Microsoft.Web/serverFarms + NSG (API Management v2 outbound integration)
//   snet-ops    /27  in-VNet operator host (jumpbox)
// New VNets default to private subnets (no default outbound access), so the
// subnets that need Microsoft Entra, package or base-image egress use the NAT
// gateway: https://learn.microsoft.com/azure/virtual-network/ip-services/default-outbound-access
param location string
param token string
param tags object
param vnetAddressPrefix string = '10.42.0.0/16'

var zoneNames = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.azurecr.io'
  'privatelink.azure-api.net'
  'privatelink.monitor.azure.com'
  'privatelink.oms.opinsights.azure.com'
  'privatelink.ods.opinsights.azure.com'
  'privatelink.agentsvc.azure-automation.net'
]

resource natIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-nat-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIpAddresses: [ { id: natIp.id } ], idleTimeoutInMinutes: 10 }
}

// API Management v2 outbound integration requires an NSG on its subnet with
// outbound access to Azure Key Vault: https://learn.microsoft.com/azure/api-management/integrate-vnet-outbound
resource apimNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-apim-${token}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowKeyVaultOutbound'
        properties: {
          priority: 100, direction: 'Outbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*'
          destinationAddressPrefix: 'AzureKeyVault', destinationPortRange: '443'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-${token}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ vnetAddressPrefix ] }
    subnets: [
      {
        name: 'snet-agent'
        properties: {
          addressPrefix: cidrSubnet(vnetAddressPrefix, 24, 0)
          natGateway: { id: nat.id }
          delegations: [ { name: 'Microsoft.App.environments', properties: { serviceName: 'Microsoft.App/environments' } } ]
        }
      }
      {
        name: 'snet-pe'
        properties: { addressPrefix: cidrSubnet(vnetAddressPrefix, 24, 1) }
      }
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: cidrSubnet(vnetAddressPrefix, 23, 1)
          natGateway: { id: nat.id }
          delegations: [ { name: 'Microsoft.App.environments', properties: { serviceName: 'Microsoft.App/environments' } } ]
        }
      }
      {
        name: 'snet-apim'
        properties: {
          addressPrefix: cidrSubnet(vnetAddressPrefix, 27, 128)
          networkSecurityGroup: { id: apimNsg.id }
          delegations: [ { name: 'Microsoft.Web.serverFarms', properties: { serviceName: 'Microsoft.Web/serverFarms' } } ]
        }
      }
      {
        name: 'snet-ops'
        properties: { addressPrefix: cidrSubnet(vnetAddressPrefix, 27, 129), natGateway: { id: nat.id } }
      }
    ]
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zone in zoneNames: {
  name: zone
  location: 'global'
  tags: tags
}]

resource zoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zone, i) in zoneNames: {
  parent: zones[i]
  name: 'link-${token}'
  location: 'global'
  tags: tags
  properties: { virtualNetwork: { id: vnet.id }, registrationEnabled: false }
}]

output vnetId string = vnet.id
output vnetName string = vnet.name
output agentSubnetId string = '${vnet.id}/subnets/snet-agent'
output peSubnetId string = '${vnet.id}/subnets/snet-pe'
output acaSubnetId string = '${vnet.id}/subnets/snet-aca'
output apimSubnetId string = '${vnet.id}/subnets/snet-apim'
output opsSubnetId string = '${vnet.id}/subnets/snet-ops'
output natPublicIp string = natIp.properties.ipAddress
// Zone IDs by name; zoneLinks are part of the module, so a caller that depends on
// this module only sees zones that already resolve inside the VNet.
output zoneIds object = toObject(zoneNames, name => name, name => resourceId('Microsoft.Network/privateDnsZones', name))
