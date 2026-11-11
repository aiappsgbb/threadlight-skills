// In-VNet operator host. With public network access off, Foundry data-plane
// calls (create the hosted-agent version, invoke, evaluate) work only from
// inside the network: https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks#run-azd-up-from-inside-the-vnet
// Cheapest honest option for a pilot or workshop: a small Linux VM with no
// public IP. The laptop drives it through Azure Run Command (control plane,
// scripts/private/vnet_run.py); a person connects through Azure Bastion
// Developer (free, browser, one VM at a time, select regions):
// https://learn.microsoft.com/azure/bastion/bastion-sku-comparison
// The VM identity deploys with azd (`azd auth login --managed-identity`): it
// gets Contributor on this resource group, Foundry User on the account, and a
// role-assignment right restricted by condition to the three roles azd and the
// postdeploy hook grant (Foundry User, AcrPull, Container Registry Repository Reader).
param location string
param token string
param tags object
param subnetId string
param vnetId string
param sshPublicKey string
param foundryAccountName string
param vmSize string = 'Standard_B2s'
param bastionDeveloper bool = true

var roleContributor = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
var roleRbacAdmin = 'f58310d9-a9f6-439a-9e8d-f62e7b41a168'
var roleFoundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleAcrRepositoryReader = 'b93aa761-3e63-49ed-ac28-beffa264f7ac'
var grantable = '{${roleFoundryUser}, ${roleAcrPull}, ${roleAcrRepositoryReader}}'
var rbacCondition = '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals ${grantable})) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals ${grantable}))'

var cloudInit = '''#cloud-config
package_update: true
packages: [python3-venv, python3-pip, git, jq, unzip, curl, ca-certificates]
runcmd:
  - curl -sL https://aka.ms/InstallAzureCLIDeb | bash
  - curl -fsSL https://aka.ms/install-azd.sh | bash
  - mkdir -p /opt/threadlight && touch /opt/threadlight/.cloud-init-done
'''

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-ops-${token}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [ { name: 'ipconfig1', properties: { subnet: { id: subnetId }, privateIPAllocationMethod: 'Dynamic' } } ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: 'vm-ops-${token}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: 'ops-${take(token, 8)}'
      #disable-next-line adminusername-should-not-be-literal
      adminUsername: 'azureuser'
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: { publicKeys: [ { path: '/home/azureuser/.ssh/authorized_keys', keyData: sshPublicKey } ] }
      }
    }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', managedDisk: { storageAccountType: 'StandardSSD_LRS' }, deleteOption: 'Delete' }
    }
    securityProfile: { securityType: 'TrustedLaunch', uefiSettings: { secureBootEnabled: true, vTpmEnabled: true } }
    networkProfile: { networkInterfaces: [ { id: nic.id, properties: { deleteOption: 'Delete' } } ] }
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource rgContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, vm.id, roleContributor)
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleContributor)
  }
}

resource rgRbacAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, vm.id, roleRbacAdmin)
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleRbacAdmin)
    conditionVersion: '2.0'
    condition: rbacCondition
  }
}

resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, vm.id, roleFoundryUser)
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-05-01' = if (bastionDeveloper) {
  name: 'bas-${token}'
  location: location
  tags: tags
  sku: { name: 'Developer' }
  properties: { virtualNetwork: { id: vnetId } }
}

output vmName string = vm.name
output principalId string = vm.identity.principalId
