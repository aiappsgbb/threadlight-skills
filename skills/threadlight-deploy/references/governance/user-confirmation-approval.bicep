targetScope = 'resourceGroup'

param workflowName string
param connectionName string
param connectionResourceGroup string
param requesterEmail string
param controlPlaneTenantId string
param controlPlaneObjectId string
param allowedCaseIds array
param location string = resourceGroup().location
param tags object = {}
param reviewTimeZone string = 'UTC'
@allowed(['Disabled', 'Enabled'])
param workflowState string = 'Disabled'
@description('Optional Reader permission on this workflow only for independent native-witness verification.')
param grantWorkflowReader bool = false

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
        User_confirmation_requested: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            method: 'POST'
            schema: {
              type: 'object'
              additionalProperties: false
              required: ['status', 'confirmation_id', 'operation_id', 'action_hash', 'intent_digest', 'expires_at', 'confirmation_intent', 'review_context', 'proposed_arguments']
              properties: {
                status: { type: 'string', enum: ['pending_confirmation'] }
                confirmation_id: { type: 'string', pattern: '^[a-f0-9]{32}$', maxLength: 32 }
                operation_id: { type: 'string', pattern: '^[A-Za-z0-9][A-Za-z0-9_.:@-]*$', minLength: 1, maxLength: 128 }
                action_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$', maxLength: 71 }
                intent_digest: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$', maxLength: 71 }
                expires_at: { type: 'string', format: 'date-time', maxLength: 40 }
                confirmation_intent: {
                  type: 'object'
                  additionalProperties: false
                  required: ['confirmation_id', 'context_ref', 'operation_id', 'principal', 'tenant', 'agent_id', 'action', 'action_hash', 'facts_hash', 'safe_hash', 'policy_hash', 'policy_expires_at', 'expires_at', 'requirement']
                  properties: {
                    confirmation_id: { type: 'string', pattern: '^[a-f0-9]{32}$' }
                    context_ref: { type: 'string', pattern: '^[a-f0-9]{32}$' }
                    operation_id: { type: 'string', minLength: 1, maxLength: 128 }
                    principal: { type: 'string', format: 'uuid', maxLength: 36 }
                    tenant: { type: 'string', format: 'uuid', maxLength: 36 }
                    agent_id: { type: 'string', minLength: 1, maxLength: 128 }
                    action: { type: 'string', enum: ['returns_apply_decision'] }
                    action_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    facts_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    safe_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    policy_hash: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' }
                    policy_expires_at: { type: 'string', format: 'date-time', maxLength: 40 }
                    expires_at: { type: 'string', format: 'date-time', maxLength: 40 }
                    reviewer_required: { type: 'boolean' }
                    requirement: {
                      type: 'object'
                      additionalProperties: false
                      required: ['trigger', 'provider_profile', 'max_age_seconds']
                      properties: {
                        trigger: { type: 'string', enum: ['always', 'policy'] }
                        provider_profile: { type: 'string', minLength: 1, maxLength: 128 }
                        max_age_seconds: { type: 'integer', minimum: 1, maximum: 3600 }
                      }
                    }
                  }
                }
                review_context: {
                  type: 'object'
                  maxProperties: 16
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
          expression: '@and(equals(triggerBody()[\'status\'], \'pending_confirmation\'), greater(ticks(triggerBody()[\'expires_at\']), ticks(utcNow())), equals(ticks(triggerBody()[\'expires_at\']), ticks(triggerBody()[\'confirmation_intent\'][\'expires_at\'])), lessOrEquals(ticks(triggerBody()[\'expires_at\']), ticks(triggerBody()[\'confirmation_intent\'][\'policy_expires_at\'])), equals(triggerBody()[\'confirmation_id\'], triggerBody()[\'confirmation_intent\'][\'confirmation_id\']), equals(triggerBody()[\'operation_id\'], triggerBody()[\'confirmation_intent\'][\'operation_id\']), equals(triggerBody()[\'action_hash\'], triggerBody()[\'confirmation_intent\'][\'action_hash\']), equals(triggerBody()[\'review_context\'][\'action\'], triggerBody()[\'confirmation_intent\'][\'action\']), equals(triggerBody()[\'review_context\'][\'policy\'], triggerBody()[\'confirmation_intent\'][\'policy_hash\']))'
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
                    To: requesterEmail
                    Subject: '@concat(\'Return \', triggerBody()[\'proposed_arguments\'][\'case_id\'], \' - confirm your request\')'
                    HeaderText: '@parameters(\'decision_labels\')[triggerBody()[\'proposed_arguments\'][\'decision\']]'
                    SelectionText: 'Do you confirm this requested operation?'
                    Options: 'Approve,Reject'
                    Importance: 'Normal'
                    UseOnlyHTMLMessage: false
                    HideHTMLMessage: true
                    ShowHTMLConfirmationDialog: true
                    Body: '@concat(\'Please confirm the operation you requested.\n\nCase: \', triggerBody()[\'proposed_arguments\'][\'case_id\'], \'\n\nProposed action: \', parameters(\'decision_labels\')[triggerBody()[\'proposed_arguments\'][\'decision\']], \'\n\nReason: \', replace(replace(replace(triggerBody()[\'proposed_arguments\'][\'reason\'], \'&\', \'&amp;\'), \'<\', \'&lt;\'), \'>\', \'&gt;\'), \'\n\nExpires: \', convertTimeZone(triggerBody()[\'expires_at\'], \'UTC\', parameters(\'review_timezone\'), \'yyyy-MM-dd HH:mm\'), \' (\', parameters(\'review_timezone\'), \'). Respond within 15 minutes and before this deadline.\n\nChoose Approve or Reject directly in this email. Your choice applies only to this request and does not replace any separately required reviewer.\n\nThe service verifies your response and current policy before execution. Synthetic demonstration case. No payment is executed.\')'
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
                  runError: { code: 'InvalidOrExpiredConfirmationRequest', message: 'The request expired or its bound identifiers differ.' }
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
            response: '@body(\'Send_approval_email\')'
            request: '@triggerBody()'
            observed_at: '@utcNow()'
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

resource witnessReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantWorkflowReader) {
  name: guid(approval.id, controlPlaneObjectId, 'native-confirmation-witness')
  scope: approval
  properties: {
    principalId: controlPlaneObjectId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  }
}

output workflowId string = approval.id
