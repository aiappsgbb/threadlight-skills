targetScope = 'resourceGroup'

param workflowName string
param connectionName string
param operatorEmail string
param operatorTenantId string
param operatorObjectId string
param allowedCaseIds array
param location string = resourceGroup().location
param tags object = {}

resource office365 'Microsoft.Web/connections@2016-06-01' = {
  name: connectionName
  location: location
  kind: 'V1'
  tags: tags
  properties: {
    displayName: 'Governed action operator notification'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
    }
  }
}

resource notification 'Microsoft.Logic/workflows@2019-05-01' = {
  name: workflowName
  location: location
  tags: tags
  properties: {
    // The operator must complete the native mailbox consent before enabling.
    state: 'Disabled'
    accessControl: {
      triggers: {
        sasAuthenticationPolicy: { state: 'Disabled' }
        openAuthenticationPolicies: {
          policies: {
            operator: {
              type: 'AAD'
              claims: [
                { name: 'iss', value: 'https://sts.windows.net/${operatorTenantId}/' }
                { name: 'aud', value: 'https://management.azure.com/' }
                { name: 'oid', value: operatorObjectId }
              ]
            }
          }
        }
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: { '$connections': { type: 'Object', defaultValue: {} } }
      triggers: {
        Review_notification_requested: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            method: 'POST'
            schema: {
              type: 'object'
              additionalProperties: false
              required: ['operation_id', 'action_hash', 'case_id']
              properties: {
                operation_id: { type: 'string', pattern: '^[a-f0-9]{32}$', maxLength: 32 }
                action_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$', maxLength: 71 }
                case_id: { type: 'string', enum: allowedCaseIds }
              }
            }
          }
        }
      }
      actions: {
        Notify_operator: {
          type: 'ApiConnection'
          inputs: {
            host: {
              connection: { name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']' }
            }
            method: 'post'
            path: '/v2/Mail'
            body: {
              To: operatorEmail
              Subject: 'Governed returns: authenticated human review required'
              Body: '@concat(\'<p>A governed return is awaiting review.</p><p>Case: \', triggerBody()[\'case_id\'], \'</p><p>Operation: \', triggerBody()[\'operation_id\'], \'</p><p>Action hash: \', triggerBody()[\'action_hash\'], \'</p><p>Use the authenticated Task8 review client to approve or reject the exact pending action. This notification, an email reply, or a click is not an approval grant. No pending business effect is executed by this workflow.</p>\')'
            }
          }
          runtimeConfiguration: { secureData: { properties: ['inputs', 'outputs'] } }
          runAfter: {}
        }
      }
      outputs: {
        notification_only: { type: 'Bool', value: true }
      }
    }
    parameters: {
      '$connections': {
        value: {
          office365: {
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
            connectionId: office365.id
            connectionName: office365.name
          }
        }
      }
    }
  }
}

output connectionId string = office365.id
output workflowId string = notification.id
