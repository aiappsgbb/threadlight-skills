targetScope = 'resourceGroup'

param workflowName string
@description('An existing, consented Office 365 connection. This module never creates or authenticates a mailbox connection.')
param connectionResourceId string
param requesterEmail string
param deliveryRef string
param confirmationOrigin string
param tenantId string
param controlPrincipalId string
param location string = resourceGroup().location
param tags object = {}

resource notification 'Microsoft.Logic/workflows@2019-05-01' = {
  name: workflowName
  location: location
  tags: tags
  properties: {
    state: 'Disabled'
    accessControl: {
      triggers: {
        sasAuthenticationPolicy: { state: 'Disabled' }
        openAuthenticationPolicies: {
          policies: {
            control: {
              type: 'AAD'
              claims: [
                { name: 'iss', value: 'https://sts.windows.net/${tenantId}/' }
                { name: 'aud', value: 'https://management.azure.com/' }
                { name: 'oid', value: controlPrincipalId }
              ]
            }
          }
        }
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': { type: 'Object', defaultValue: {} }
        deliveryRef: { type: 'String' }
        confirmationOrigin: { type: 'String' }
      }
      triggers: {
        User_confirmation_requested: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            method: 'POST'
            schema: {
              type: 'object'
              additionalProperties: false
              required: ['confirmation_id', 'confirmation_url', 'delivery_ref', 'expires_at']
              properties: {
                confirmation_id: { type: 'string', pattern: '^[a-f0-9]{32}$', maxLength: 32 }
                confirmation_url: { type: 'string', maxLength: 2048 }
                delivery_ref: { type: 'string', maxLength: 128 }
                expires_at: { type: 'string', format: 'date-time', maxLength: 64 }
              }
            }
          }
        }
      }
      actions: {
        Validate_notification: {
          type: 'If'
          expression: {
            and: [
              { equals: ['@triggerBody()[\'delivery_ref\']', '@parameters(\'deliveryRef\')'] }
              { equals: ['@triggerBody()[\'confirmation_url\']', '@concat(parameters(\'confirmationOrigin\'), \'/confirmation/open/\', triggerBody()[\'confirmation_id\'])'] }
            ]
          }
          actions: {
            Notify_requester: {
              type: 'ApiConnection'
              inputs: {
                host: {
                  connection: { name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']' }
                }
                method: 'post'
                path: '/v2/Mail'
                retryPolicy: { type: 'none' }
                body: {
                  To: requesterEmail
                  Subject: 'Confirm your requested operation outside the agent'
                  Body: '@concat(\'<p>Your requested operation needs confirmation.</p><p><a href="\', triggerBody()[\'confirmation_url\'], \'">Open confirmation instructions</a></p><p>Use the separate authenticated confirmation client to inspect the exact proposal and explicitly confirm or reject it. Opening this link or replying to this email does not authorize an action.</p><p>This email is not MFA and does not replace any independently required reviewer.</p>\')'
                }
              }
              runtimeConfiguration: { secureData: { properties: ['inputs', 'outputs'] } }
              runAfter: {}
            }
            Acknowledge_notification: {
              type: 'Response'
              kind: 'Http'
              inputs: {
                statusCode: 200
                body: { notification_id: '@triggerBody()[\'confirmation_id\']' }
              }
              runAfter: { Notify_requester: ['Succeeded'] }
            }
            Notification_failed: {
              type: 'Response'
              kind: 'Http'
              inputs: { statusCode: 503, body: { error: 'notification_unavailable' } }
              runAfter: { Notify_requester: ['Failed', 'TimedOut'] }
            }
          }
          else: {
            actions: {
              Reject_notification: {
                type: 'Response'
                kind: 'Http'
                inputs: { statusCode: 400, body: { error: 'notification_binding_mismatch' } }
                runAfter: {}
              }
            }
          }
          runAfter: {}
        }
      }
      outputs: { notification_only: { type: 'Bool', value: true } }
    }
    parameters: {
      deliveryRef: { value: deliveryRef }
      confirmationOrigin: { value: confirmationOrigin }
      '$connections': {
        value: {
          office365: {
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
            connectionId: connectionResourceId
            connectionName: last(split(connectionResourceId, '/'))
          }
        }
      }
    }
  }
}

output workflowId string = notification.id
