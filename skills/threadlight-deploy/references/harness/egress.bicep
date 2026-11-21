// Foundry Copilot harness network egress (preview): an RAI policy with a Network
// (egressPolicy) control, attached to the prompt agent as rai_config.
// https://learn.microsoft.com/azure/foundry/agents/how-to/use-tools-skills-copilot-harness#control-network-egress
// API 2026-05-15-preview is the documented one; Bicep has no type for it yet (BCP081 warning).
//
// Start in Audit (would-deny requests are logged, not blocked). Enforced returns
// 403 to the sandbox for every host that is not allowed. Tested Nov 2026: with
// Enforced and only customer hosts allowed, the agent emitted ZERO agent spans,
// so the App Insights / Live Metrics / SDK settings / Agent 365 hosts are always
// allowed here. Rule edits reach a NEW conversation in about a minute.

@description('Foundry account name (the policy is an account child resource).')
param accountName string

@description('Audit or Enforced.')
@allowed([ 'Audit', 'Enforced' ])
param mode string = 'Audit'

@description('Hosts the harness sandbox may reach directly, in addition to telemetry.')
param allowHosts array = []

@description('Telemetry hosts that must stay reachable or tracing stops.')
param telemetryHosts array = [
  '*.in.applicationinsights.azure.com'
  '*.livediagnostics.monitor.azure.com'
  'settings.sdk.monitor.azure.com'
  'agent365.svc.cloud.microsoft'
]

param policyName string = 'harness-egress'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

var hosts = union(telemetryHosts, allowHosts)

resource egress 'Microsoft.CognitiveServices/accounts/raiPolicies@2026-05-15-preview' = {
  parent: account
  name: policyName
  properties: {
    mode: 'Blocking'
    basePolicyName: 'Microsoft.DefaultV2'
    contentFilters: []
    egressPolicy: {
      mode: mode
      defaultAction: 'Deny'
      rules: [for (host, i) in hosts: {
        name: 'allow-${i}'
        ruleType: 'Fqdn'
        match: { host: host }
        action: { actionType: 'Allow' }
      }]
    }
  }
}

output id string = egress.id
output hosts array = hosts
