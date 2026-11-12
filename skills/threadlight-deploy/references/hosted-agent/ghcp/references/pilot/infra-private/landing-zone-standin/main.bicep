// =============================================================================
// TEST STAND-IN for a customer landing zone. NOT generated into pilots.
//
// What a platform team usually owns, so the private profile can be proven in
// "landing zone" mode (THREADLIGHT_EXISTING_VNET_ID / _DNS_ZONES_RG / _DNS_LINK_VNET_ID):
//   * hub VNet with the central privatelink.* DNS zones linked ONLY to the hub,
//     and an Azure DNS Private Resolver inbound endpoint;
//   * a vended spoke VNet (subnet contract of the private profile) whose custom
//     DNS is the resolver, peered both ways with the hub;
//   * optional Azure Firewall Basic in the hub with a route table on the spoke
//     workload subnets (egress allow-list by FQDN, SNI-based for HTTPS).
// https://learn.microsoft.com/azure/dns/private-resolver-architecture
// https://learn.microsoft.com/azure/firewall/choose-firewall-sku
// Recurring cost: the resolver endpoint and the firewall are hourly. Delete them
// after a proof (firewall: firewallEnabled=false; resolver: delete the resource).
// =============================================================================
param location string = resourceGroup().location
param prefix string
param tags object = {}
param hubAddressPrefix string = '10.50.0.0/16'
param spokeAddressPrefix string = '10.43.0.0/16'

@description('Deploy Azure Firewall Basic and route the spoke workload subnets through it.')
param firewallEnabled bool = false

@description('FQDNs the spoke workload subnets may reach over HTTPS (application rule, SNI). ["*"] logs everything and blocks nothing.')
param allowedFqdns array = [ '*' ]

@description('Operator subnet: network rule to these service tags on 443 (Run Command and az/azd need Azure management endpoints).')
param operatorServiceTags array = [ 'AzureCloud' ]

param logAnalyticsId string = ''

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
  'privatelink.search.windows.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.vaultcore.azure.net'
]
var dnsInPrefix = cidrSubnet(hubAddressPrefix, 28, 8)
var dnsInIp = cidrHost(dnsInPrefix, 3)

resource hub 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-hub-${prefix}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ hubAddressPrefix ] }
    subnets: [
      { name: 'AzureFirewallSubnet', properties: { addressPrefix: cidrSubnet(hubAddressPrefix, 26, 0) } }
      { name: 'AzureFirewallManagementSubnet', properties: { addressPrefix: cidrSubnet(hubAddressPrefix, 26, 1) } }
      {
        name: 'snet-dns-in'
        properties: {
          addressPrefix: dnsInPrefix
          delegations: [ { name: 'dnsResolvers', properties: { serviceName: 'Microsoft.Network/dnsResolvers' } } ]
        }
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
  name: 'link-hub-${prefix}'
  location: 'global'
  tags: tags
  properties: { virtualNetwork: { id: hub.id }, registrationEnabled: false }
}]

resource resolver 'Microsoft.Network/dnsResolvers@2022-07-01' = {
  name: 'dnspr-${prefix}'
  location: location
  tags: tags
  properties: { virtualNetwork: { id: hub.id } }
}

resource inbound 'Microsoft.Network/dnsResolvers/inboundEndpoints@2022-07-01' = {
  parent: resolver
  name: 'in'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [ {
      subnet: { id: '${hub.id}/subnets/snet-dns-in' }
      privateIpAllocationMethod: 'Static'
      privateIpAddress: dnsInIp
    } ]
  }
}

// ------------------------------------------------------------------ firewall --
resource fwPip 'Microsoft.Network/publicIPAddresses@2024-05-01' = if (firewallEnabled) {
  name: 'pip-fw-${prefix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource fwMgmtPip 'Microsoft.Network/publicIPAddresses@2024-05-01' = if (firewallEnabled) {
  name: 'pip-fwm-${prefix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource policy 'Microsoft.Network/firewallPolicies@2024-05-01' = if (firewallEnabled) {
  name: 'afwp-${prefix}'
  location: location
  tags: tags
  properties: { sku: { tier: 'Basic' }, threatIntelMode: 'Alert' }
}

resource rules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2024-05-01' = if (firewallEnabled) {
  parent: policy
  name: 'spoke-egress'
  properties: {
    priority: 200
    ruleCollections: [
      {
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        name: 'operator-azure'
        priority: 100
        action: { type: 'Allow' }
        rules: [ {
          ruleType: 'NetworkRule'
          name: 'ops-to-azure'
          ipProtocols: [ 'TCP' ]
          sourceAddresses: [ cidrSubnet(spokeAddressPrefix, 27, 129) ]
          destinationAddresses: operatorServiceTags
          destinationPorts: [ '443' ]
        } ]
      }
      {
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        name: 'workload-fqdns'
        priority: 200
        action: { type: 'Allow' }
        rules: [ {
          ruleType: 'ApplicationRule'
          name: 'allow-list'
          sourceAddresses: [ spokeAddressPrefix ]
          protocols: [ { protocolType: 'Https', port: 443 } ]
          targetFqdns: allowedFqdns
        } ]
      }
    ]
  }
}

resource firewall 'Microsoft.Network/azureFirewalls@2024-05-01' = if (firewallEnabled) {
  name: 'afw-${prefix}'
  location: location
  tags: tags
  dependsOn: [ rules ]
  properties: {
    sku: { name: 'AZFW_VNet', tier: 'Basic' }
    firewallPolicy: { id: policy.id }
    ipConfigurations: [ {
      name: 'ipconfig'
      properties: { subnet: { id: '${hub.id}/subnets/AzureFirewallSubnet' }, publicIPAddress: { id: fwPip.id } }
    } ]
    managementIpConfiguration: {
      name: 'mgmt'
      properties: { subnet: { id: '${hub.id}/subnets/AzureFirewallManagementSubnet' }, publicIPAddress: { id: fwMgmtPip.id } }
    }
  }
}

resource fwDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (firewallEnabled && !empty(logAnalyticsId)) {
  name: 'fw-logs'
  scope: firewall
  properties: {
    workspaceId: logAnalyticsId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'AZFWApplicationRule', enabled: true }
      { category: 'AZFWNetworkRule', enabled: true }
    ]
  }
}

resource routes 'Microsoft.Network/routeTables@2024-05-01' = {
  name: 'rt-spoke-${prefix}'
  location: location
  tags: tags
  properties: {
    routes: firewallEnabled ? [ {
      name: 'default-to-firewall'
      properties: {
        addressPrefix: '0.0.0.0/0'
        nextHopType: 'VirtualAppliance'
        nextHopIpAddress: firewall!.properties.ipConfigurations[0].properties.privateIPAddress
      }
    } ] : []
  }
}

// ----------------------------------------------------------- vended spoke --
resource natIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-nat-spoke-${prefix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-spoke-${prefix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIpAddresses: [ { id: natIp.id } ] }
}

resource apimNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-apim-spoke-${prefix}'
  location: location
  tags: tags
  properties: {
    securityRules: [ {
      name: 'AllowKeyVaultOutbound'
      properties: {
        priority: 100, direction: 'Outbound', access: 'Allow', protocol: 'Tcp'
        sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*'
        destinationAddressPrefix: 'AzureKeyVault', destinationPortRange: '443'
      }
    } ]
  }
}

// Workload subnets keep the NAT; with the firewall on, the 0.0.0.0/0 route wins.
var egress = { natGateway: { id: nat.id }, routeTable: { id: routes.id } }

resource spoke 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-spoke-${prefix}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ spokeAddressPrefix ] }
    dhcpOptions: { dnsServers: [ dnsInIp ] }
    subnets: [
      {
        name: 'snet-agent'
        properties: union(egress, {
          addressPrefix: cidrSubnet(spokeAddressPrefix, 24, 0)
          delegations: [ { name: 'Microsoft.App.environments', properties: { serviceName: 'Microsoft.App/environments' } } ]
        })
      }
      { name: 'snet-pe', properties: { addressPrefix: cidrSubnet(spokeAddressPrefix, 24, 1) } }
      {
        name: 'snet-aca'
        properties: union(egress, {
          addressPrefix: cidrSubnet(spokeAddressPrefix, 23, 1)
          delegations: [ { name: 'Microsoft.App.environments', properties: { serviceName: 'Microsoft.App/environments' } } ]
        })
      }
      {
        name: 'snet-apim'
        properties: {
          addressPrefix: cidrSubnet(spokeAddressPrefix, 27, 128)
          networkSecurityGroup: { id: apimNsg.id }
          delegations: [ { name: 'Microsoft.Web.serverFarms', properties: { serviceName: 'Microsoft.Web/serverFarms' } } ]
        }
      }
      { name: 'snet-ops', properties: union(egress, { addressPrefix: cidrSubnet(spokeAddressPrefix, 27, 129) }) }
    ]
  }
  dependsOn: [ inbound ]
}

resource hubToSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: hub
  name: 'to-spoke'
  properties: { remoteVirtualNetwork: { id: spoke.id }, allowVirtualNetworkAccess: true, allowForwardedTraffic: true }
}

resource spokeToHub 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: spoke
  name: 'to-hub'
  properties: { remoteVirtualNetwork: { id: hub.id }, allowVirtualNetworkAccess: true, allowForwardedTraffic: true }
}

output HUB_VNET_ID string = hub.id
output SPOKE_VNET_ID string = spoke.id
output DNS_RESOLVER_IP string = dnsInIp
output FIREWALL_PRIVATE_IP string = firewallEnabled ? firewall!.properties.ipConfigurations[0].properties.privateIPAddress : ''
