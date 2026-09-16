targetScope = 'resourceGroup'

param workflowName string
param connectionName string
param connectionResourceGroup string
param operatorEmail string
param controlPlaneTenantId string
param controlPlaneObjectId string
param allowedCaseIds array
param location string = resourceGroup().location
param tags object = {}
param reviewTimeZone string = 'UTC'
@allowed(['Disabled', 'Enabled'])
param workflowState string = 'Disabled'

resource office365 'Microsoft.Web/connections@2016-06-01' existing = {
  name: connectionName
  scope: resourceGroup(connectionResourceGroup)
}

resource approval 'Microsoft.Logic/workflows@2019-05-01' = {
  name: workflowName
  location: location
  tags: tags
  properties: {
    state: workflowState
    accessControl: {
      triggers: {
        sasAuthenticationPolicy: { state: 'Disabled' }
        openAuthenticationPolicies: {
          policies: {
            control_plane: {
              type: 'AAD'
              claims: [
                { name: 'iss', value: 'https://sts.windows.net/${controlPlaneTenantId}/' }
                { name: 'aud', value: 'https://management.azure.com/' }
                { name: 'oid', value: controlPlaneObjectId }
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
        review_timezone: { type: 'String', defaultValue: reviewTimeZone }
        decision_labels: {
          type: 'Object'
          defaultValue: {
            approve_refund: 'Approve refund decision'
            deny_refund: 'Decline refund decision'
            escalate_to_supervisor: 'Supervisor handoff'
            request_more_info: 'Request additional information'
          }
        }
      }
      triggers: {
        Review_notification_requested: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            method: 'POST'
            schema: {
              type: 'object'
              additionalProperties: false
              required: ['operation_id', 'action_hash', 'case_id', 'proposed_arguments', 'expires_at', 'approval_intent', 'review_context']
              properties: {
                operation_id: { type: 'string', pattern: '^[a-f0-9]{32}$', maxLength: 32 }
                action_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$', maxLength: 71 }
                case_id: { type: 'string', enum: allowedCaseIds }
                expires_at: { type: 'string', format: 'date-time', maxLength: 40 }
                approval_intent: {
                  type: 'object'
                  additionalProperties: false
                  required: ['principal', 'agent_id', 'tenant', 'allowed_roles', 'action_hash', 'policy_hash', 'session_id', 'context_identity', 'nonce', 'expires_at', 'policy_expires_at']
                  properties: {
                    principal: { type: 'string', format: 'uuid', maxLength: 36 }
                    agent_id: { type: 'string', minLength: 1, maxLength: 128 }
                    tenant: { type: 'string', format: 'uuid', maxLength: 36 }
                    allowed_roles: { type: 'array', minItems: 1, maxItems: 16, uniqueItems: true, items: { type: 'string', minLength: 1, maxLength: 128 } }
                    action_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    policy_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    session_id: { type: 'string', minLength: 1, maxLength: 128 }
                    context_identity: { type: 'string', minLength: 1, maxLength: 128 }
                    nonce: { type: 'string', pattern: '^[a-f0-9]{32}$' }
                    expires_at: { type: 'string', format: 'date-time', maxLength: 40 }
                    policy_expires_at: { type: 'string', format: 'date-time', maxLength: 40 }
                  }
                }
                review_context: {
                  type: 'object'
                  additionalProperties: false
                  required: ['tenant', 'subject', 'client', 'action', 'scope', 'policy', 'deployment']
                  properties: {
                    tenant: { type: 'string', format: 'uuid', maxLength: 36 }
                    subject: { type: 'string', format: 'uuid', maxLength: 36 }
                    client: { type: 'string', format: 'uuid', maxLength: 36 }
                    action: { type: 'string', enum: ['returns_apply_decision'] }
                    scope: { type: 'string', minLength: 1, maxLength: 128 }
                    policy: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    deployment: { type: 'object', maxProperties: 7 }
                  }
                }
                proposed_arguments: {
                  type: 'object'
                  additionalProperties: false
                  required: ['case_id', 'expected_etag', 'decision', 'reason']
                  properties: {
                    case_id: { type: 'string', enum: allowedCaseIds }
                    expected_etag: { type: 'string', minLength: 1, maxLength: 128 }
                    decision: { type: 'string', enum: ['approve_refund', 'deny_refund', 'escalate_to_supervisor', 'request_more_info'] }
                    reason: { type: 'string', minLength: 1, maxLength: 512 }
                  }
                }
              }
            }
          }
        }
      }
      actions: {
        Require_fresh_pending: {
          type: 'If'
          expression: '@and(greater(ticks(triggerBody()[\'expires_at\']), ticks(utcNow())), equals(ticks(triggerBody()[\'expires_at\']), ticks(triggerBody()[\'approval_intent\'][\'expires_at\'])), lessOrEquals(ticks(triggerBody()[\'expires_at\']), ticks(triggerBody()[\'approval_intent\'][\'policy_expires_at\'])), equals(triggerBody()[\'action_hash\'], triggerBody()[\'approval_intent\'][\'action_hash\']), equals(triggerBody()[\'case_id\'], triggerBody()[\'proposed_arguments\'][\'case_id\']))'
          actions: {
            Send_approval_email: {
              type: 'ApiConnectionWebhook'
              inputs: {
                host: {
                  connection: { name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']' }
                }
                path: '/approvalmail/$subscriptions'
                body: {
                  NotificationUrl: '@listCallbackUrl()'
                  Message: {
                    To: operatorEmail
                    Subject: '@concat(\'Return \', triggerBody()[\'case_id\'], \' - decision requested\')'
                    HeaderText: '@parameters(\'decision_labels\')[triggerBody()[\'proposed_arguments\'][\'decision\']]'
                    SelectionText: 'Do you authorize this request?'
                    Options: 'Approve,Reject'
                    Importance: 'Normal'
                    UseOnlyHTMLMessage: false
                    HideHTMLMessage: true
                    ShowHTMLConfirmationDialog: true
                    Body: '@concat(\'Case: \', triggerBody()[\'case_id\'], \'\n\nProposed action: \', parameters(\'decision_labels\')[triggerBody()[\'proposed_arguments\'][\'decision\']], \'\n\nReason: \', replace(replace(replace(triggerBody()[\'proposed_arguments\'][\'reason\'], \'&\', \'&amp;\'), \'<\', \'&lt;\'), \'>\', \'&gt;\'), \'\n\nExpires: \', convertTimeZone(triggerBody()[\'expires_at\'], \'UTC\', parameters(\'review_timezone\'), \'yyyy-MM-dd HH:mm\'), \' (\', parameters(\'review_timezone\'), \'). This email waits for a response for up to 15 minutes.\n\nChoose Approve to authorize this request, or Reject to decline it.\n\nSynthetic demonstration case. No payment is executed.\')'
                  }
                }
              }
              limit: { timeout: 'PT15M' }
              runtimeConfiguration: { secureData: { properties: ['inputs', 'outputs'] } }
              runAfter: {}
            }
          }
          else: {
            actions: {
              Reject_expired_request: {
                type: 'Terminate'
                inputs: {
                  runStatus: 'Failed'
                  runError: { code: 'InvalidOrExpiredApprovalRequest', message: 'The request expired or its case identifiers differ.' }
                }
                runAfter: {}
              }
            }
          }
          runAfter: {}
        }
      }
      outputs: {
        outlook_decision: {
          type: 'Object'
          value: {
            operation_id: '@triggerBody()[\'operation_id\']'
            action_hash: '@triggerBody()[\'action_hash\']'
            case_id: '@triggerBody()[\'case_id\']'
            response: '@body(\'Send_approval_email\')'
            request: '@triggerBody()'
            observed_at: '@utcNow()'
            within_intent_lifetime: '@lessOrEquals(ticks(utcNow()), ticks(triggerBody()[\'expires_at\']))'
            control_plane_grant_created: false
            business_effect_executed: false
          }
        }
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

output workflowId string = approval.id
