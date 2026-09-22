package returns_evidence

import rego.v1

default pre_tool_call := {"decision": "deny", "reason": "purchase_evidence_required"}

purchase_corroborated if {
    input.snapshot.tool_call.name == "returns_apply_decision"
    input.snapshot.safe.evidence.profile == "returns-purchase-v1"
    input.snapshot.safe.evidence.claims.purchase_verified == true
    is_number(input.snapshot.safe.evidence.claims.amount)
    input.snapshot.safe.evidence.claims.amount >= 0
}

pre_tool_call := {"decision": "escalate", "reason": "supervisor_handoff"} if {
    purchase_corroborated
    input.policy_target.value.decision == "escalate_to_supervisor"
}

pre_tool_call := {"decision": "allow", "reason": "corroborated_ordinary_return"} if {
    purchase_corroborated
    input.snapshot.safe.evidence.claims.amount <= 500
    input.policy_target.value.decision in {"approve_refund", "deny_refund", "request_more_info"}
}
