// Canonical Basic project capability host with platform-managed backing stores.
// Source of truth for `../../README.md § Private network composition`.
// This is project-wide infrastructure, not an agent-local routing toggle.

param accountName string
param projectName string
param capabilityHostName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}
resource capabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: capabilityHostName
  // ProjectCapabilityHostProperties declares no host-kind property (BCP037): the kind comes
  // from the account-level host. Basic setup keeps platform-managed stores.
  properties: {}
}
output id string = capabilityHost.id
